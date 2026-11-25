"""Run one neutral FL2VA graph through local ComfyUI and validate both media streams."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import threading
import time
import uuid

import aiohttp
from PIL import Image, ImageDraw

BASE = "http://127.0.0.1:8188"


def write(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def graph(workflow, input_dir, tag, seed):
    prompt = json.loads(workflow.read_text())
    folder = input_dir / "foxmotion-phase2"
    folder.mkdir(exist_ok=True)
    for i, node in enumerate(("148", "147")):
        image = Image.new("RGB", (320, 192), (97, 155, 201))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 120, 320, 192), fill=(134, 181, 122))
        x = 73 + i * 40
        draw.ellipse((x, 73, x + 42, 115), fill=(234, 176, 42))
        filename = f"calibration-v1-{i}.png"
        if not (folder / filename).exists():
            image.save(folder / filename)
        prompt[node]["inputs"]["image"] = f"{folder.name}/{filename}"
    for suffix, value in ((152,320), (153,192), (133,0.9)):
        prompt[f"140:{suffix}"]["inputs"]["value"] = value
    prompt["140:129"]["inputs"]["noise_seed"] = seed
    # Paired workloads repeat the same A/B across configurations while the hot
    # job forces both a fresh text encode and fresh diffusion.
    prompt["140:131"]["inputs"]["prompt"] = ("A yellow ball moves slowly across a green field under a blue sky."
                                              if seed % 2 else "A golden ball moves slowly across a green field under a blue sky.")
    prompt["92"]["inputs"]["filename_prefix"] = "foxmotion-phase2/" + tag
    return prompt


async def generate(args, pid, stamp, directory):
    runtime = args.runtime
    job_id = uuid.uuid4().hex
    prompt = graph(args.workflow, runtime / "comfy/input", job_id, args.seed)
    state = dict(pid=pid, start_ticks=stamp, process_ready=False, model_ready=False, inference_ready=False)
    def save():
        write(directory / "state.json", state)
    save()
    async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=30)) as client:
        async with client.get(BASE + "/queue") as resp:
            resp.raise_for_status()
            queue = await resp.json()
            if queue["queue_running"] or queue["queue_pending"]:
                raise RuntimeError("Comfy queue busy")
        state["process_ready"] = True
        save()
        async with client.ws_connect(BASE + "/ws?clientId=" + job_id, heartbeat=30) as ws:
            start = time.time()
            async with client.post(BASE + "/prompt", json=dict(prompt=prompt, client_id=job_id)) as resp:
                reply = await resp.json()
                if resp.status != 200:
                    raise RuntimeError(str(reply)[:2500])
                prompt_id = reply["prompt_id"]
            state.update(prompt_id=prompt_id, submitted=start)
            save()
            with (directory / "events.jsonl").open("x", buffering=1) as log:
                async with asyncio.timeout(3600):
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        event = json.loads(msg.data)
                        kind, data = event["type"], event["data"]
                        if data.get("prompt_id") != prompt_id:
                            continue
                        info = {k:data[k] for k in ("node","nodes","value","max","node_id","exception_type","exception_message") if k in data}
                        print(json.dumps(dict(t=time.time(), kind=kind, **info)), file=log)
                        if kind == "progress" and data.get("node") == "140:125" and not state["model_ready"]:
                            state["model_ready"] = True
                            save()
                        if kind in ("execution_error","execution_interrupted"):
                            raise RuntimeError(f"{kind}: {info}")
                        if kind == "execution_success" or (kind == "executing" and data.get("node") is None):
                            break
            end = time.time()
            for _ in range(20):
                async with client.get(BASE + "/history/" + prompt_id) as resp:
                    history = (await resp.json()).get(prompt_id)
                if history:
                    break
                await asyncio.sleep(.25)
            if not history or history["status"]["status_str"] != "success":
                raise RuntimeError("Missing successful prompt history")
            refs = [ref for group in history["outputs"]["92"].values() if isinstance(group,list)
                    for ref in group if isinstance(ref, dict) and ref.get("type") == "output"]
            root = (runtime / "comfy/output/foxmotion-phase2").resolve()
            video = (root.parent / refs[0]["subfolder"] / refs[0]["filename"]).resolve()
            if video.parent != root or not video.name.startswith(job_id):
                raise ValueError("Unexpected output")
            media = json.loads(subprocess.check_output(["ffprobe","-v","error","-count_frames","-show_entries",
                 "stream=codec_type,codec_name,width,height,nb_read_frames,duration,r_frame_rate","-of","json",str(video)]))
            streams = {s["codec_type"]:s for s in media["streams"]}
            v = streams["video"]
            if (v["width"], v["height"], int(v["nb_read_frames"])) != (320,192,22) or int(streams["audio"]["nb_read_frames"]) < 1:
                raise ValueError(media)
            subprocess.run(["ffmpeg","-v","error","-xerror","-i",str(video),"-map","0:v","-map","0:a","-f","null","-"], check=True)
            state.update(model_ready=True, inference_ready=True, ended=end, seconds=end-start, media=media, output=str(video))
            save()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--runtime", type=Path, default=Path("/opt/video-runtime"))
    p.add_argument("--workflow", type=Path, default=Path("/workspace/config/workflows/image.api.json"))
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--measure", action="store_true")
    args = p.parse_args()
    args.directory.mkdir(parents=True, exist_ok=False)
    ctl = args.runtime / "studio/bin/supervisorctl"
    pid = int(subprocess.check_output([ctl, "-c", args.runtime/"supervisord.conf", "pid", "comfy"]))
    stamp = (Path("/proc") / str(pid) / "stat").read_text().split()[21]
    done = threading.Event()
    with ThreadPoolExecutor(1) as pool:
        sample = None
        if args.measure:
            from telemetry import collect
            sample = pool.submit(collect, args.directory, pid, done)
            time.sleep(1.5)
            if sample.done():
                sample.result()
        try:
            asyncio.run(generate(args, pid, stamp, args.directory))
        except Exception as exc:
            state_path = args.directory / "state.json"
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            state.update(inference_ready=False, failure=type(exc).__name__, failed_at=time.time())
            write(state_path, state)
            raise
        finally:
            if sample:
                time.sleep(20)
                done.set()
                sample.result()


if __name__ == "__main__":
    main()
