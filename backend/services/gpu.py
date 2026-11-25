"""Studio coordination for the loopback Comfy queue and native /free flags."""
import asyncio
from pathlib import Path

import httpx
from filelock import FileLock, Timeout


def claim_lock(store):
    return FileLock(str(store.root / "gpu-claim.lock"), timeout=0)


def active_jobs(store):
    with store.connection() as db:
        return bool(db.execute("SELECT EXISTS(SELECT 1 FROM jobs WHERE status IN ('queued','running'))").fetchone()[0])


def cgroup_status():
    root = Path("/sys/fs/cgroup")
    try:
        limit = (root / "memory.max").read_text().strip()
        return dict(used=int((root / "memory.current").read_text()), limit=None if limit == "max" else int(limit),
                    events={k:int(v) for k,v in (line.split() for line in (root/"memory.events").read_text().splitlines())})
    except (OSError, ValueError):
        return None


async def status(cfg, store):
    async with httpx.AsyncClient(base_url=cfg.comfyui_url, trust_env=False, timeout=5) as client:
        inventory, stats = await asyncio.gather(client.get("/foxmotion/runtime"), client.get("/system_stats"))
        inventory.raise_for_status()
        stats.raise_for_status()
        data = inventory.json()
        gpu = stats.json()["devices"][0]
        return dict(**data, queued_or_running=active_jobs(store), device=gpu["name"],
                    total=gpu["vram_total"], used=gpu["vram_total"]-gpu["vram_free"], cgroup=cgroup_status())


async def release_if_idle(cfg, store):
    ttl = cfg.gpu_idle_release_seconds
    if not ttl:
        return
    current = await status(cfg, store)
    if (current["busy"] or current["queued_or_running"] or current["releasing"]
            or current["idle_seconds"] < ttl):
        return
    if any(model["bytes"] > 0 for model in current["models"]):
        await release(cfg, store)


class Busy(RuntimeError):
    pass


async def release(cfg, store):
    try:
        with claim_lock(store):
            if active_jobs(store):
                raise Busy("Studio 有排队或执行中的任务")
            async with httpx.AsyncClient(base_url=cfg.comfyui_url, trust_env=False, timeout=5) as client:
                state = (await client.get("/foxmotion/runtime")).raise_for_status().json()
                if state["busy"] or state["releasing"]:
                    raise Busy("Comfy 正在执行任务或释放显存")
                response = await client.post("/free", json={"unload_models":True,"free_memory":True})
                if response.status_code == 409:
                    raise Busy("Comfy 正忙")
                response.raise_for_status()
                for _ in range(120):
                    state = (await client.get("/foxmotion/runtime")).raise_for_status().json()
                    if not state["releasing"] and not any(m["bytes"] for m in state["models"]):
                        return
                    await asyncio.sleep(.25)
                raise RuntimeError("Comfy 尚未确认释放完毕")
    except Timeout as exc:
        raise Busy("GPU 调度正在进行") from exc
