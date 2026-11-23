import asyncio
import shutil
from argparse import ArgumentParser
from sys import version

from .config import Settings
from .inference.base import build_backend
from .services.catalog import catalog
from .services.store import Store


async def main(host_only=False):
    cfg = Settings()
    if cfg.studio_port != 7860 or cfg.comfyui_url != "http://127.0.0.1:8188":
        raise SystemExit("生产配置需要 Studio 7860、ComfyUI 127.0.0.1:8188")
    store = Store(cfg.data_dir)
    free = shutil.disk_usage(store.root).free
    print(
        "Python",
        version.split()[0],
        "Data",
        store.root,
        "Free GiB",
        round(free / 2**30, 1),
    )
    if free < 1024**3:
        raise SystemExit("可用空间低于 1 GiB，请检查持久卷")
    if host_only:
        return
    engine = build_backend(cfg)
    caps = await engine.capabilities()
    for c in caps:
        print(c.mode, c.family, "READY" if c.ready else c.reason)
    routes = (await catalog(cfg, engine))["routes"]
    for route in routes:
        print(
            route["mode"],
            route["model_profile_id"],
            route["lora_id"] or "base",
            route["status"],
            route["reason"],
        )
    if not any(c.ready for c in caps) and not any(
        route["capability"].ready for route in routes
    ):
        raise SystemExit(
            "没有就绪模式。检查 FFmpeg 或启动本机 ComfyUI 并配置 bindings。"
        )


if __name__ == "__main__":
    parser = ArgumentParser()
    parser.add_argument("--host-only", action="store_true")
    asyncio.run(main(parser.parse_args().host_only))
