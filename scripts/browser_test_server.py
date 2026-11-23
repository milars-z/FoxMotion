"""Isolated production-static server used by Playwright; directory is never shared with Studio."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.chdir(ROOT)
    with TemporaryDirectory(prefix="h3-browser-") as data:
        env = {
            **os.environ,
            "INFERENCE_BACKEND": "mock",
            "DATA_DIR": data,
            "STUDIO_HOST": "127.0.0.1",
            "STUDIO_PORT": "17860",
            "MOCK_STAGE_SECONDS": "0.7",
        }
        worker = subprocess.Popen([sys.executable, "-m", "backend.worker"], env=env)
        try:
            subprocess.run([sys.executable, "-m", "backend"], env=env, check=True)
        finally:
            worker.terminate()
            worker.wait(timeout=10)


if __name__ == "__main__":
    main()
