import asyncio
import json
import logging
import time
import httpx
from backend.services import gpu, privacy
from contextlib import suppress

from filelock import FileLock, Timeout

from backend.config import Settings
from backend.inference.base import (
    Canceled,
    InferenceError,
    LostExecution,
    Run,
    build_backend,
)
from backend.models.jobs import Request
from backend.services.files import publish_result
from backend.services.store import Store

log = logging.getLogger("h3.worker")


class Worker:
    def __init__(self, settings):
        self.cfg = settings
        self.store = Store(settings.data_dir)
        self.backend = build_backend(settings)

    async def execute(self, job):
        try:
            private_path = None
            if job.get("privacy_mode"):
                private_path = privacy.task(self.cfg, job["job_id"])
                payload = privacy.read_json(private_path / "request.json")
                job = {**job, "prompt": payload["prompt"], "input_files_json": json.dumps(payload["inputs"])}
            run = Run(self.store, job, private_path)
        except (OSError, ValueError, KeyError):
            self.store.finish(
                job["job_id"],
                "failed",
                "执行失败",
                "无法创建输出目录，请检查磁盘和权限",
            )
            return
        if not private_path:
            log.info("job=%s event=start backend=%s", run.id, job["backend"])
        try:
            if self.cfg.inference_backend != job["backend"]:
                raise InferenceError("后端已切换，请使用当前配置重新生成任务")
            req = Request(
                mode=job["mode"],
                privacy_mode=bool(private_path),
                prompt=job["prompt"],
                parameters=json.loads(job["parameters_json"]),
                roles=[f["role"] for f in json.loads(job["input_files_json"])],
            )
            await self.backend.validate(req)
            run.check()
            path = await self.backend.generate(run)
            if (
                not private_path and self.cfg.result_dir
                and self.cfg.result_dir.resolve() != run.outputs.parent
            ):
                run.check()
                run.stage("保存成片至持久卷")
                published = await asyncio.to_thread(
                    publish_result, path, self.cfg.result_dir, run.id
                )
                self.store.finish(run.id, "completed", "生成完成", output=path.name)
                row = self.store.get(run.id)
                with suppress(OSError):
                    if not row or row["status"] != "completed":
                        published.unlink(missing_ok=True)
                    path.unlink(missing_ok=True)
            else:
                self.store.finish(run.id, "completed", "生成完成", output=path.name)
        except Canceled:
            self.store.finish(run.id, "canceled", "已取消")
        except (LostExecution, asyncio.CancelledError) as err:
            self.store.finish(
                run.id, "interrupted", "执行中断", "保密执行中断" if private_path else str(err) or "Worker 已停止"
            )
            if isinstance(err, asyncio.CancelledError):
                raise
        except (InferenceError, ValueError, RuntimeError) as err:
            self.store.finish(run.id, "failed", "执行失败", "保密任务执行失败" if private_path else str(err))
        except Exception as err:  # noqa: BLE001 - isolate backend failures to this job.
            if not private_path:
                log.error("job=%s error_type=%s", run.id, type(err).__name__)
            self.store.finish(
                run.id, "failed", "执行失败", "本地执行异常，请检查依赖、配置和磁盘空间"
            )
        finally:
            if private_path:
                await privacy.maintain(self.cfg, self.store, self.backend)
            else:
                final = self.store.get(run.id)
                log.info("job=%s status=%s", run.id, final["status"] if final else "deleted")

    async def pulse(self):
        while True:
            self.store.heartbeat(self.cfg.inference_backend)
            await asyncio.sleep(2)

    async def janitor(self):
        next_orphans = 0.0
        while True:
            try:
                await asyncio.to_thread(privacy.reap_expired, self.cfg, self.store)
                if time.monotonic() >= next_orphans:
                    await asyncio.to_thread(privacy.abandoned, self.cfg, self.store)
                    next_orphans = time.monotonic() + 60
            except (OSError, RuntimeError):
                log.warning("Private cleanup pending; will retry")
            await asyncio.sleep(10)

    async def serve(self):
        with FileLock(str(self.store.root / "worker.lock"), timeout=0):
            self.store.recover()
            next_cleanup = 0.0
            janitor = asyncio.create_task(self.janitor())
            beat = asyncio.create_task(self.pulse())
            next_idle_check = time.monotonic() + self.cfg.gpu_idle_release_seconds
            try:
                while True:
                    if time.monotonic() >= next_cleanup:
                        if not await privacy.maintain(self.cfg, self.store, self.backend):
                            await asyncio.sleep(2)
                            continue
                        next_cleanup = time.monotonic() + 10
                    try:
                        with gpu.claim_lock(self.store):
                            job = self.store.claim()
                    except Timeout:
                        await asyncio.sleep(self.cfg.worker_poll_seconds)
                        continue
                    if job:
                        await self.execute(job)
                        if job.get("privacy_mode"):
                            next_cleanup = 0.0
                        next_idle_check = time.monotonic() + self.cfg.gpu_idle_release_seconds
                    else:
                        ttl = self.cfg.gpu_idle_release_seconds
                        if ttl and self.cfg.inference_backend == "comfyui" and time.monotonic() >= next_idle_check:
                            try:
                                await gpu.release_if_idle(self.cfg, self.store)
                            except (httpx.HTTPError, RuntimeError) as exc:
                                log.info("idle GPU release deferred: %s", type(exc).__name__)
                            next_idle_check = time.monotonic() + min(ttl, 5)
                        await asyncio.sleep(self.cfg.worker_poll_seconds)
            finally:
                janitor.cancel()
                with suppress(asyncio.CancelledError):
                    await janitor
                beat.cancel()
                with suppress(asyncio.CancelledError):
                    await beat
                with self.store.connection(True) as db:
                    db.execute("DELETE FROM worker_state")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(Worker(Settings()).serve())
    except Timeout:
        raise SystemExit("该数据目录已有 Worker")
