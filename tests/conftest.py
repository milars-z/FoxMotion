import io
import json
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api.app import create_app
from backend.config import Settings


@pytest.fixture
def cfg(tmp_path):
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "studio",
        workflow_dir=tmp_path / "workflows",
        comfyui_home=tmp_path / "ComfyUI",
        inference_backend="mock",
        mock_stage_seconds=0,
    )


@pytest.fixture
def client(cfg):
    with TestClient(create_app(cfg), base_url="http://127.0.0.1:7860") as http:
        yield http


def request_data(mode="text", prompt="测试镜头", roles=None, preset="Mock Standard"):
    return {
        "mode": mode,
        "prompt": prompt,
        "roles": roles or [],
        "parameters": {
            "duration": 3,
            "resolution": "640x360",
            "seed": -1,
            "steps": 8,
            "preset": preset,
        },
    }


def submit(client, data=None, files=()):
    parts = [
        ("payload", (None, json.dumps(data or request_data()), "application/json"))
    ]
    parts.extend(("files", f) for f in files)
    return client.post("/api/jobs", files=parts)


@pytest.fixture
def png():
    buf = io.BytesIO()
    Image.new("RGB", (20, 12), "green").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=green:s=64x64:d=0.3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()
