import logging

import uvicorn

from .config import Settings

if __name__ == "__main__":
    cfg = Settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("python_multipart").setLevel(logging.ERROR)
    uvicorn.run(
        "backend.api.app:from_environment",
        factory=True,
        host=cfg.studio_host,
        port=cfg.studio_port,
        access_log=False,
    )
