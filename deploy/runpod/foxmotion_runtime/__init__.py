"""Local diagnostics and a guarded native loader for the pinned RunPod ComfyUI."""
import asyncio
import logging
import time
from contextlib import suppress
from pathlib import Path

from aiohttp import web
import comfy.model_management as mm
import nodes
import folder_paths
from server import PromptServer


def origin(patcher):
    factory = getattr(patcher, "cached_patcher_init", None)
    if factory and factory[1]:
        path = factory[1][0]
        if isinstance(path, (list, tuple)):
            path = path[0] if path else None
        if isinstance(path, (str, Path)):
            return Path(path).resolve()
    return None


def inventory():
    result = []
    for wrapper in tuple(mm.current_loaded_models):
        model = wrapper.model
        if model is None or model.load_device.type != "cuda":
            continue
        size = model.loaded_size()
        if size > 0:
            filename = origin(model)
            result.append(dict(name=filename.name if filename else type(model.model).__name__,
                               kind=type(model.model).__name__, bytes=size,
                               device=str(model.load_device), patched=bool(model.patches)))
    return result


class FoxMotionUNETLoader(nodes.UNETLoader):
    def load_unet(self, unet_name, weight_dtype):
        # Classic has already pruned inactive graph outputs here. Only intervene
        # for an H3 base which remains on the device after native pruning.
        incoming = Path(folder_paths.get_full_path_or_raise("diffusion_models", unet_name)).resolve()
        resident = list(mm.current_loaded_models)
        stale = []
        devices = set()
        for wrapper in resident:
            model = wrapper.model
            if model is None or type(model.model).__name__ != "MiniMaxH3":
                continue
            filename = origin(model)
            if filename and filename != incoming and model.loaded_size():
                stale.append(wrapper)
                devices.add(wrapper.device)
        if stale:
            logging.info("FoxMotion switching H3 base; releasing %d stale base(s)", len(stale))
            keep = [w for w in resident if w not in stale]
            for device in devices:
                mm.free_memory(1e30, device, keep_loaded=keep)
        return super().load_unet(unet_name, weight_dtype)


NODE_CLASS_MAPPINGS = {"FoxMotionUNETLoader": FoxMotionUNETLoader}
server = PromptServer.instance
gate = asyncio.Lock()
releasing = False
last_activity = time.monotonic()


async def observe_queue(app):
    async def poll():
        global last_activity
        was_busy = False
        while True:
            busy = bool(server.prompt_queue.get_tasks_remaining())
            if busy or was_busy:
                last_activity = time.monotonic()
            was_busy = busy
            await asyncio.sleep(1)

    observer = asyncio.create_task(poll())
    yield
    observer.cancel()
    with suppress(asyncio.CancelledError):
        await observer


server.app.cleanup_ctx.append(observe_queue)


def pending():
    global releasing
    queue = server.prompt_queue
    if releasing and not queue.get_flags(reset=False) and not any(m["bytes"] for m in inventory()):
        releasing = False
    return releasing


@web.middleware
async def gate_release(request, handler):
    global releasing, last_activity
    route = request.path.removeprefix("/api")
    if request.method != "POST" or route not in ("/prompt", "/free"):
        return await handler(request)
    async with gate:
        if pending():
            return web.json_response({"error": "显存释放中"}, status=409)
        if route == "/free":
            if server.prompt_queue.get_tasks_remaining():
                return web.json_response({"error": "Comfy 队列有任务"}, status=409)
            result = await handler(request)
            # Only a joint unload/reset requests a completely empty inventory.
            data = await request.json()
            releasing = result.status == 200 and bool(data.get("unload_models") and data.get("free_memory"))
            return result
        result = await handler(request)
        if result.status == 200:
            last_activity = time.monotonic()
        return result


server.app.middlewares.append(gate_release)


@server.routes.get("/foxmotion/runtime")
async def inspect_runtime(request):
    return web.json_response(dict(models=inventory(), busy=bool(server.prompt_queue.get_tasks_remaining()),
                                  idle_seconds=max(0, time.monotonic()-last_activity), releasing=pending()))
