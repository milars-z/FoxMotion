import logging

import uvicorn

from .config import Settings

if __name__ == "__main__":
    cfg = Settings()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    uvicorn.run(
        "backend.api.app:from_environment",
        factory=True,
        host=cfg.studio_host,
        port=cfg.studio_port,
        access_log=False,
    )
