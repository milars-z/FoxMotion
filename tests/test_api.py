import asyncio
import io
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import request_data, submit
from fastapi.testclient import TestClient
from filelock import FileLock, Timeout
from pydantic import ValidationError

from backend.api.app import create_app
from backend.config import Settings
from backend.worker import Worker


def test_health_capabilities_and_private_access(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert [x["ready"] for x in client.get("/api/capabilities").json()["modes"]] == [
        True
    ] * 3
    assert client.get("/api/system").json()["backend"] == "mock"
    assert (
        client.post(
            "/api/jobs", headers={"Origin": "https://other.invalid"}
        ).status_code
        == 403
    )
    assert client.get("/api/jobs", headers={"Host": "other.invalid"}).status_code == 400
    assert (
        client.post(
            "/api/jobs", headers={"Content-Length": str(193 * 2**20)}
        ).status_code
        == 413
    )


def test_fifo_persistence_range_and_cleanup(client, cfg):
    ids = [submit(client).json()["job_id"] for _ in range(3)]
    worker = Worker(cfg)
    assert [j["job_id"] for j in client.get("/api/jobs").json()] == ids[::-1]
    running = worker.store.claim()
    assert running["job_id"] == ids[0]
    assert client.delete("/api/jobs/" + ids[0]).status_code == 409
    assert client.post("/api/jobs/" + ids[1] + "/cancel").json()["status"] == "canceled"
    asyncio.run(worker.execute(running))
    j = client.get("/api/jobs/" + ids[0]).json()
    assert j["status"] == "completed" and j["parameters"]["seed"] >= 0
    url = "/api/jobs/" + ids[0] + "/result"
    res = client.get(url, headers={"Range": "bytes=0-31"})
    assert res.status_code == 206 and len(res.content) == 32 and b"ftyp" in res.content
    assert (
        "attachment"
        in client.get(url + "?download=true").headers["content-disposition"]
    )
    with TestClient(create_app(cfg), base_url="http://localhost") as reopened:
        assert reopened.get("/api/jobs/" + ids[0]).json() == j
    assert client.delete("/api/jobs/" + ids[0]).status_code == 204
    assert not (cfg.data_dir / "outputs" / ids[0]).exists()
    assert worker.store.claim()["job_id"] == ids[2]
    worker.store.recover()
    assert client.get("/api/jobs/" + ids[2]).json()["status"] == "interrupted"


def test_regeneration_independent_files(client, cfg, png, video):
    files = [
        ("a.png", png, "image/png"),
        ("b.png", png, "image/png"),
        ("c.mp4", video, "video/mp4"),
    ]
    r = submit(
        client,
        request_data("reference", roles=["ref_image", "ref_image", "ref_video"]),
        files,
    )
    assert r.status_code == 201, r.text
    j = r.json()
    new = client.post("/api/jobs/" + j["job_id"] + "/regenerate").json()
    assert j["parameters"] == new["parameters"]
    assert len(new["input_files"]) == 3
    client.post("/api/jobs/" + j["job_id"] + "/cancel")
    client.delete("/api/jobs/" + j["job_id"])
    for f in new["input_files"]:
        assert (cfg.data_dir / "inputs" / new["job_id"] / f["name"]).is_file()


@pytest.mark.parametrize(
    "name,content,mime",
    [
        ("bad.png", b"bad", "image/png"),
        ("../x.png", b"bad", "image/png"),
        ("x.exe", b"x", "image/png"),
        ("x.png", b"x", "video/mp4"),
    ],
)
def test_reject_invalid_upload_and_cleanup(client, cfg, name, content, mime):
    result = submit(
        client, request_data("image", roles=["first_frame"]), [(name, content, mime)]
    )
    assert result.status_code == 422
    assert not list((cfg.data_dir / "inputs").iterdir())


def test_reject_invalid_video_and_parameters(client):
    assert (
        submit(
            client,
            request_data("reference", roles=["ref_video"]),
            [("bad.mp4", b"bad", "video/mp4")],
        ).status_code
        == 422
    )
    assert submit(client, request_data("reference")).status_code == 422
    assert submit(client, request_data("image")).status_code == 422
    req = request_data()
    req["parameters"]["duration"] = 999
    assert submit(client, req).status_code == 422
    assert submit(client, request_data(prompt=" ")).status_code == 422


def test_running_cancel_and_failure(client, cfg):
    worker = Worker(cfg.model_copy(update={"mock_stage_seconds": 0.12}))
    failure = submit(client, request_data(prompt="[mock:fail]")).json()
    asyncio.run(worker.execute(worker.store.claim()))
    assert (
        "Mock" in client.get("/api/jobs/" + failure["job_id"]).json()["error_message"]
    )
    j = submit(client).json()
    row = worker.store.claim()
    with ThreadPoolExecutor() as pool:
        result = pool.submit(asyncio.run, worker.execute(row))
        time.sleep(0.07)
        assert client.post("/api/jobs/" + j["job_id"] + "/cancel").json()[
            "cancel_requested"
        ]
        result.result(timeout=8)
    assert worker.store.get(j["job_id"])["status"] == "canceled"
    assert client.get("/api/jobs/" + j["job_id"] + "/result").status_code == 409


def test_worker_lock_and_recovery(client, cfg):
    worker = Worker(cfg)
    j = submit(client).json()
    worker.store.claim()
    with FileLock(str(worker.store.root / "worker.lock")):
        with pytest.raises(Timeout):
            asyncio.run(worker.serve())
        assert worker.store.get(j["job_id"])["status"] == "running"
    worker.store.recover()
    assert worker.store.get(j["job_id"])["status"] == "interrupted"


def test_audio_reference_browser_mime(client):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 8000, 80, "NONE", "not compressed"))
        wav.writeframes(bytes(160))
    result = submit(
        client,
        request_data("reference", roles=["ref_audio"]),
        [("reference.wav", buffer.getvalue(), "audio/x-wav")],
    )
    assert result.status_code == 201
    assert result.json()["input_files"][0]["mime"] == "audio/wav"


def test_oversize_file_rolls_back(cfg):
    cfg.max_file_mb = 1
    with TestClient(create_app(cfg), base_url="http://localhost") as http:
        result = submit(
            http,
            request_data("image", roles=["first_frame"]),
            [("large.png", bytes(1024**2 + 1), "image/png")],
        )
        assert result.status_code == 422
        assert not http.get("/api/jobs").json()
        assert not list((cfg.data_dir / "inputs").iterdir())


def test_missing_workflow_and_remote_url(cfg):
    with pytest.raises(ValidationError):
        Settings(comfyui_url="http://example.org:8188")
    cfg.inference_backend = "comfyui"
    with TestClient(create_app(cfg), base_url="http://localhost") as client:
        assert all(
            not c["ready"] and "NOT_CONFIGURED" in c["reason"]
            for c in client.get("/api/capabilities").json()["modes"]
        )
        assert submit(client).status_code == 503
        assert not client.get("/api/jobs").json()
