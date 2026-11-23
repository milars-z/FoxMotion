import asyncio
import json
import shutil
from contextlib import suppress
from uuid import uuid4

from backend.models.jobs import FAMILY, MODES, Capability, Options
from backend.models.registry import DEFAULT_PROFILE

from .base import InferenceBackend, InferenceError


class MockBackend(InferenceBackend):
    def __init__(self, settings):
        self.settings = settings

    async def capabilities(self):
        ready = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
        slots = {
            "text": {},
            "image": {"first_frame": {"min": 1, "max": 1}, "last_frame": {"max": 1}},
            "reference": {
                "ref_image": {"max": 4},
                "ref_video": {"max": 1},
                "ref_audio": {"max": 1},
            },
        }
        return [
            Capability(
                mode=m,
                family=FAMILY[m],
                ready=ready,
                reason="" if ready else "缺少 FFmpeg 或 ffprobe",
                options=Options(
                    durations=[3, 5],
                    resolutions=["640x360", "360x640"],
                    steps=[8, 16, 24],
                    presets=["Mock Standard"],
                    slots=slots[m],
                ),
            )
            for m in MODES
        ]

    async def selection_cap(self, mode, profile_id=DEFAULT_PROFILE, lora_id=None):
        cap = await super().selection_cap(mode, profile_id, lora_id)
        if profile_id != DEFAULT_PROFILE and cap.options:
            cap.options.steps = [8, 16, 18, 24]
            cap.options.samplers = ["euler"]
            cap.options.schedulers = ["simple", "beta", "normal"]
            cap.options.shifts = [8, 12]
            cap.options.turbo_options = [False]
        return cap

    async def generate(self, run):
        params = json.loads(run.job["parameters_json"])
        for stage in ("校验测试输入", "准备测试画面", "渲染测试片段"):
            run.check()
            run.stage(stage)
            await asyncio.sleep(self.settings.mock_stage_seconds)
        if run.job["prompt"] == "[mock:fail]":
            raise InferenceError("已触发 Mock 失败测试")
        output = run.outputs / f"{uuid4()}.mp4"
        # A moving calibration chart is intentionally distinct from real inference.
        source = f"testsrc2=size={params['resolution']}:rate=24:duration={params['duration']}"
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            source,
            "-an",
            "-threads",
            "2",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-metadata",
            "title=H3 Studio - MOCK calibration clip",
            "-movflags",
            "+faststart",
            str(output),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while proc.returncode is None:
                run.check()
                with suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(proc.wait(), 0.15)
            if proc.returncode:
                raise InferenceError("FFmpeg 编码失败，请检查 libx264 编码器和磁盘空间")
            return output
        finally:
            if proc.returncode is None:
                with suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
