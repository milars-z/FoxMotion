"""Benchmark launcher for the pinned ComfyUI. Production starts main.py directly."""
import functools
import json
import os
from pathlib import Path
import runpy
import sys
import threading
import time

trace = Path(sys.argv.pop(1))
entry = Path(sys.argv[1]).resolve()
sys.argv = sys.argv[1:]
sys.path.insert(0, str(entry.parent))


def install():
    import nodes
    import torch
    import comfy.model_management as mm
    import comfy.sd as sd
    import comfy.samplers as samplers
    import comfy.utils as utils
    from comfy_execution.utils import get_executing_context

    def emit(**event):
        with trace.open("a") as out:
            print(json.dumps(dict(t=time.time(), pid=os.getpid(), **event)), file=out)

    def resident():
        result = {}
        for wrapped in list(mm.current_loaded_models):
            patcher = wrapped.model
            if patcher is not None:
                key = type(patcher.model).__name__
                result[key] = dict(loaded=patcher.loaded_size(), size=patcher.model_size(),
                                   device=str(patcher.load_device), dynamic=patcher.is_dynamic())
        return result

    def wrap(owner, name, label, sync=False):
        method = getattr(owner, name)

        @functools.wraps(method)
        def measured(*args, **kwargs):
            context = get_executing_context()
            if context is None:
                return method(*args, **kwargs)
            if sync:
                torch.cuda.synchronize()
            start = time.time()
            try:
                result = method(*args, **kwargs)
                if sync:
                    torch.cuda.synchronize()
                return result
            finally:
                end = time.time()
                emit(stage=label, start=start, end=end, node=context.node_id, prompt=context.prompt_id,
                     models=resident() if label in {"placement", "release", "diffusion", "decode"} else None)
        setattr(owner, name, measured)

    for owner, method in [(nodes.UNETLoader,"load_unet"), (nodes.CLIPLoader,"load_clip"),
                          (nodes.VAELoader,"load_vae"), (nodes.LoraLoaderModelOnly,"load_lora_model_only")]:
        wrap(owner, method, "loader")
    wrap(sd.CLIP, "tokenize", "tokenize", True)
    wrap(sd.CLIP, "encode_from_tokens_scheduled", "text", True)
    wrap(sd.VAE, "encode", "keyframe_vae", True)
    wrap(sd.VAE, "decode", "decode", True)
    wrap(samplers.KSAMPLER, "sample", "diffusion", True)
    wrap(mm, "load_models_gpu", "placement")
    wrap(mm, "free_memory", "release")
    wrap(utils, "load_torch_file", "weight_open")

    def idle_sample():
        while True:
            try:
                emit(stage="inventory", models=resident())
            except Exception as exc:
                emit(stage="inventory_unavailable", error=type(exc).__name__)
            time.sleep(5)
    threading.Thread(target=idle_sample, daemon=True).start()


original_run = threading.Thread.run


def start_thread(thread):
    if getattr(getattr(thread, "_target", None), "__name__", "") == "prompt_worker":
        threading.Thread.run = original_run
        install()
    return original_run(thread)


threading.Thread.run = start_thread
runpy.run_path(str(entry), run_name="__main__")
