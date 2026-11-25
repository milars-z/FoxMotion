import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from backend.inference.workflow import bind_references
from backend.models.jobs import Capability, Options, Request, Slot, validate_request
from backend.services import gpu
from backend.services.store import Store


def test_reference_order_and_video_frames():
    uploads = {"ref_image": ["b.png", "a.png"], "ref_video": ["b.mp4", "a.mp4"], "ref_audio": ["b.wav", "a.wav"]}
    graph = {"ref": {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": {}}}
    bind_references(graph, "ref", uploads)
    pins = graph["ref"]["inputs"]
    assert graph[pins["ref_images.ref_image_0"][0]]["inputs"]["image"] == "b.png"
    assert graph[pins["ref_audios.ref_audio_1"][0]]["inputs"]["audio"] == "a.wav"
    split = graph[pins["ref_videos.ref_video_0"][0]]
    assert graph[split["inputs"]["video"][0]]["inputs"]["file"] == "b.mp4"
    assert len(pins) == 6


def test_reference_tag_must_match_role_count():
    cap = Capability(mode="reference", family="Ref2VA", ready=True, options=Options(
        durations=[1], resolutions=["320x192"], steps=[4], presets=["test"],
        slots={"ref_audio":Slot(max=3)}, reference_tags=True))
    req = Request(mode="reference", prompt="Use <Audio 2>", roles=["ref_audio"],
                  parameters=dict(duration=1, resolution="320x192",steps=4,preset="test",seed=1))
    with pytest.raises(ValueError, match="索引"):
        validate_request(req,cap)
    req.prompt="Use <Audio 1>"
    validate_request(req,cap)


def test_release_does_not_contact_comfy_when_worker_claims(tmp_path):
    store = Store(tmp_path)
    cfg = SimpleNamespace(comfyui_url="http://127.0.0.1:8188")
    with gpu.claim_lock(store), patch.object(gpu.httpx, "AsyncClient") as http:
        with pytest.raises(gpu.Busy):
            asyncio.run(gpu.release(cfg,store))
        http.assert_not_called()


def test_release_refuses_even_before_active_job_enters_comfy(tmp_path):
    store = Store(tmp_path)
    with patch.object(gpu,"active_jobs",return_value=True), patch.object(gpu.httpx,"AsyncClient") as http:
        with pytest.raises(gpu.Busy):
            asyncio.run(gpu.release(SimpleNamespace(),store))
        http.assert_not_called()


@pytest.mark.parametrize(("ttl", "seconds", "busy", "expected"), [
    (0, 500, False, False), (30, 29, False, False), (30, 30, False, True),
    (30, 60, True, False),
])
def test_idle_release_threshold(ttl, seconds, busy, expected):
    state = dict(busy=busy, queued_or_running=False, releasing=False,
                 idle_seconds=seconds, models=[{"bytes": 128}])
    with patch.object(gpu, "status", return_value=state) as status, patch.object(gpu, "release") as release:
        asyncio.run(gpu.release_if_idle(SimpleNamespace(gpu_idle_release_seconds=ttl), None))
        assert release.await_count == int(expected)
        assert status.await_count == int(ttl != 0)


def test_manual_release_waits_for_native_ack(tmp_path):
    store = Store(tmp_path)
    cfg = SimpleNamespace(comfyui_url="http://127.0.0.1:8188")
    calls = []
    snapshots = iter([
        {"busy": False, "releasing": False, "models": [{"bytes": 1024}]},
        {"busy": False, "releasing": True, "models": [{"bytes": 1024}]},
        {"busy": False, "releasing": False, "models": []},
    ])

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert json.loads(request.content) == {"unload_models": True, "free_memory": True}
            return httpx.Response(200)
        return httpx.Response(200, json=next(snapshots))

    client = httpx.AsyncClient(base_url=cfg.comfyui_url, transport=httpx.MockTransport(handle))
    with patch.object(gpu.httpx, "AsyncClient", return_value=client):
        asyncio.run(gpu.release(cfg, store))
    assert calls == [
        ("GET", "/foxmotion/runtime"),
        ("POST", "/free"),
        ("GET", "/foxmotion/runtime"),
        ("GET", "/foxmotion/runtime"),
    ]
