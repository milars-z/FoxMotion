import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from conftest import request_data

from backend.inference.base import Canceled, InferenceError, LostExecution, Run
from backend.inference.comfyui import ComfyUIBackend
from backend.inference.workflow import WorkflowAdapter
from backend.models.jobs import Request
from backend.services.store import Store


@pytest.fixture
def fixture(cfg):
    """Fabricated test-only schema. None of these node names represent H3."""
    cfg.inference_backend = "comfyui"
    (cfg.workflow_dir / "bindings").mkdir(parents=True)
    for kind in ("input", "output", "model"):
        cfg.comfy_dir(kind).mkdir(parents=True)
    folder = cfg.comfy_dir("model") / "diffusion_models"
    folder.mkdir()
    weight = "fixture_ref2va_cpu.safetensors"
    (folder / weight).write_bytes(b"fixture")
    fields = ["prompt", "seed", "duration", "width", "height", "steps", "output_prefix"]
    inputs = {k: "default" for k in fields}
    inputs.update({k: "input" for k in ("ref_video", "ref_image", "ref_audio")})
    inputs["weight"] = weight
    graph = {"fixture": {"class_type": "LocalTestDouble", "inputs": inputs}}
    target = lambda k: {"node": "fixture", "input": k}
    variant = {
        "workflow": "fixture.api.json",
        "fields": {k: [target(k)] for k in fields},
        "slots": {k: [target(k)] for k in ("ref_video", "ref_image", "ref_audio")},
        "models": [{**target("weight"), "folder": "diffusion_models", "base": True}],
        "output_node": "fixture",
        "output_key": "fixture_outputs",
    }
    variant["fields"]["duration"][0]["values"] = {"3": 72}
    spec = {
        "family": "Ref2VA",
        "durations": [3],
        "resolutions": ["640x360"],
        "steps": [8],
        "preset": "fixture",
        "variants": [variant],
    }
    (cfg.workflow_dir / "bindings" / "reference.json").write_text(json.dumps(spec))
    (cfg.workflow_dir / "fixture.api.json").write_text(json.dumps(graph))
    objects = {
        "LocalTestDouble": {
            "input": {
                "required": {
                    k: ([[weight]] if k == "weight" else ["STRING"]) for k in inputs
                }
            }
        }
    }
    return cfg, objects, spec, graph


@pytest.mark.parametrize(
    "case",
    [
        "editor",
        "wrong_family",
        "missing_weight",
        "unknown_class",
        "bad_field",
        "wrong_base",
        "wrong_enum",
        "cloud",
    ],
)
def test_unready_config(fixture, case):
    cfg, objects, spec, graph = fixture
    if case == "editor":
        graph = {"nodes": [], "links": []}
    if case == "wrong_family":
        spec["family"] = "FL2VA"
    if case == "missing_weight":
        next((cfg.comfy_dir("model") / "diffusion_models").iterdir()).unlink()
    if case == "unknown_class":
        objects.clear()
    if case == "bad_field":
        spec["variants"][0]["fields"]["seed"][0]["input"] = "absent"
    if case == "wrong_base":
        other = "fixture_fl2va_cpu.safetensors"
        (cfg.comfy_dir("model") / "diffusion_models" / other).write_text("test")
        graph["fixture"]["inputs"]["weight"] = other
        objects["LocalTestDouble"]["input"]["required"]["weight"] = [[other]]
    if case == "wrong_enum":
        objects["LocalTestDouble"]["input"]["required"]["weight"] = [["missing"]]
    if case == "cloud":
        objects["LocalTestDouble"]["api_node"] = True
    (cfg.workflow_dir / "bindings" / "reference.json").write_text(json.dumps(spec))
    (cfg.workflow_dir / "fixture.api.json").write_text(json.dumps(graph))
    with pytest.raises(InferenceError):
        WorkflowAdapter(cfg, "reference", objects)


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "ws_offline",
        "bad_output",
        "remote_error",
        "http_error",
        "cancel_pending",
        "cancel_running",
    ],
)
def test_local_protocol(fixture, png, video, scenario):
    cfg, objects, _, _ = fixture
    store = Store(cfg.data_dir)
    key = str(uuid4())
    root = cfg.data_dir / "inputs" / key
    root.mkdir()
    names = [f"{uuid4()}.mp4", f"{uuid4()}.png", f"{uuid4()}.wav"]
    roles = ["ref_video", "ref_image", "ref_audio"]
    req = Request.model_validate(
        request_data("reference", roles=roles, preset="fixture")
    )
    req.parameters.seed = 42
    metas = [
        {"role": role, "name": name, "mime": mime, "size": len(buf)}
        for role, name, mime, buf in zip(
            roles,
            names,
            ["video/mp4", "image/png", "audio/wav"],
            [video, png, b"audio"],
        )
    ]
    for f, data in zip(metas, [video, png, b"audio"]):
        (root / f["name"]).write_bytes(data)
    with store.connection(True) as db:
        store.insert(db, key, req, metas, "comfyui")
    run = Run(store, store.claim())
    calls = []
    events = []
    history_count = 0
    deleted = False
    submitted = False
    sub = "h3studio/" + key

    class WS:
        async def recv(self):
            events.append("recv")
            return json.dumps({"type": "executing", "data": {"prompt_id": "test-id"}})

        async def close(self):
            events.append("close")

    async def ws(url, **kwargs):
        assert url.startswith("ws://127.0.0.1:8188/ws?clientId=")
        events.append("open")
        if scenario == "ws_offline":
            raise OSError()
        return WS()

    def respond(request):
        nonlocal history_count, deleted, submitted
        route = request.url.path
        calls.append((request.method, route))

        def ok(data):
            return httpx.Response(200, json=data)

        if route == "/object_info":
            return ok(objects)
        if route == "/upload/image":
            assert (
                names[1].encode() in request.content
                and names[0].encode() not in request.content
            )
            return ok({"type": "input", "subfolder": sub, "name": names[1]})
        if route == "/queue" and request.method == "POST":
            assert json.loads(request.content) == {"delete": ["test-id"]}
            deleted = True
            return ok({})
        if route == "/api/jobs/test-id/cancel":
            deleted = True
            return ok({"cancelled": True})
        if route == "/queue":
            active = [[0, "test-id"]] if submitted and not deleted else []
            return ok(
                {
                    "queue_pending": [] if scenario == "cancel_running" else active,
                    "queue_running": active if scenario == "cancel_running" else [],
                }
            )
        if route == "/prompt":
            submitted = True
            inputs = json.loads(request.content)["prompt"]["fixture"]["inputs"]
            assert (
                inputs["prompt"] == req.prompt
                and inputs["duration"] == 72
                and inputs["seed"] == 42
            )
            assert (
                inputs["height"] == 360
                and inputs["width"] == 640
                and inputs["steps"] == 8
            )
            for role, name in zip(roles, names):
                assert inputs[role] == sub + "/" + name
            assert (cfg.comfy_dir("input") / inputs["ref_video"]).read_bytes() == video
            assert (
                cfg.comfy_dir("input") / inputs["ref_audio"]
            ).read_bytes() == b"audio"
            assert inputs["output_prefix"].startswith(sub + "/")
            dest = cfg.comfy_dir("output") / sub
            dest.mkdir(parents=True)
            (dest / "result.mp4").write_bytes(video)
            if scenario == "http_error":
                return httpx.Response(503)
            if scenario.startswith("cancel_"):
                with store.connection(True) as db:
                    db.execute("UPDATE jobs SET cancel_requested=1")
            return ok({"prompt_id": "test-id"})
        if route == "/history/test-id":
            history_count += 1
            if history_count < 2 or scenario.startswith("cancel_"):
                return ok({})
            if scenario == "remote_error":
                return ok(
                    {"test-id": {"status": {"status_str": "error", "completed": False}}}
                )
            media = {
                "filename": "result.mp4",
                "subfolder": "other" if scenario == "bad_output" else sub,
                "type": "output",
            }
            return ok(
                {
                    "test-id": {
                        "status": {"completed": True},
                        "outputs": {"fixture": {"fixture_outputs": [media]}},
                    }
                }
            )
        pytest.fail("Unexpected route " + route)

    backend = ComfyUIBackend(cfg, httpx.MockTransport(respond), ws)
    caps = asyncio.run(backend.capabilities())
    assert [c.ready for c in caps] == [False, False, True]
    if scenario in ("success", "ws_offline"):
        path = asyncio.run(backend.generate(run))
        assert path.read_bytes() == video and path.parent == run.outputs
        assert store.get(key)["comfy_prompt_id"] == "test-id"
    else:
        error = {
            "bad_output": InferenceError,
            "remote_error": InferenceError,
            "http_error": LostExecution,
            "cancel_pending": Canceled,
            "cancel_running": Canceled,
        }[scenario]
        with pytest.raises(error):
            asyncio.run(backend.generate(run))
    assert ("GET", "/queue") in calls and calls.count(("POST", "/upload/image")) == 1
    assert "open" in events
    if scenario == "success":
        assert "recv" in events
    if scenario.startswith("cancel_"):
        assert deleted
