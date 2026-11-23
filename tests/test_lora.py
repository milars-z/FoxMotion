"""Neutral prompts and synthetic loader I/O. These fixtures never certify H3 hardware."""

import asyncio
import hashlib
import json
import struct
from uuid import uuid4

import httpx
import pytest
from conftest import request_data, submit
from fastapi.testclient import TestClient

from backend import record_smoke
from backend.api.app import create_app
from backend.inference import evidence
from backend.inference.base import InferenceError, Run
from backend.inference.comfyui import ComfyUIBackend
from backend.inference.workflow import WorkflowAdapter
from backend.models.jobs import Request
from backend.models.registry import (
    DEFAULT_PROFILE,
    H3,
    HM,
    LORAS,
    PROFILES,
    manifest_path,
)
from backend.services.store import Store
from backend.worker import Worker
from scripts.inspect_lora import inspect


def candidate(lid=HM, strength=0.8, preset="Mock Standard"):
    data = request_data(preset=preset)
    data["parameters"].update(
        model_profile_id=H3,
        lora_id=lid,
        lora_strength=strength,
        sampler="euler",
        scheduler="beta",
        steps=18,
        shift=12,
        turbo_enabled=False,
    )
    return data


def test_mock_snapshot_and_old_history(cfg, client):
    cat = client.get("/api/capabilities").json()["catalog"]
    assert cat["profiles"] == client.get("/api/models").json()
    assert cat["loras"] == client.get("/api/loras").json()
    assert str(cfg.comfyui_home) not in json.dumps(cat)
    assert all(
        r["status"] != "ready" and r["capability"]["ready"] for r in cat["routes"]
    )
    first = submit(client, candidate()).json()
    second = submit(client, candidate(strength=0)).json()
    worker = Worker(cfg)
    for result in (first, second):
        job = worker.store.claim()
        assert job["job_id"] == result["job_id"]
        saved = json.loads(job["parameters_json"])
        assert saved == result["parameters"]
        assert (
            saved["lora_id"] == HM
            and saved["steps"] == 18
            and saved["lora_strength"] == result["parameters"]["lora_strength"]
        )
        asyncio.run(worker.execute(job))
        with TestClient(create_app(cfg), base_url="http://localhost") as fresh:
            row = fresh.get("/api/jobs/" + job["job_id"]).json()
            assert (
                row["parameters"] == saved
                and row["status"] == "completed"
                and row["backend"] == "mock"
            )
    old = submit(client).json()["job_id"]
    raw = json.dumps(request_data()["parameters"])
    with worker.store.connection(True) as db:
        db.execute("UPDATE jobs SET parameters_json=? WHERE job_id=?", (raw, old))
    assert (
        client.get("/api/jobs/" + old).json()["parameters"]["model_profile_id"]
        == DEFAULT_PROFILE
    )
    assert client.post("/api/jobs/" + old + "/regenerate").status_code == 201
    assert worker.store.get(old)["parameters_json"] == raw


@pytest.mark.parametrize(
    "patch",
    [
        {"lora_strength": -0.1},
        {"lora_strength": 1.51},
        {"lora_id": "other"},
        {"lora_id": "../private.safetensors", "lora_strength": 0},
        {"model_profile_id": "../file"},
        {"model_profile_id": DEFAULT_PROFILE},
        {"scheduler": "bogus"},
        {"turbo_enabled": True},
        {"shift": 999},
    ],
)
def test_reject_invalid_parameters(client, patch):
    data = candidate()
    data["parameters"].update(patch)
    assert submit(client, data).status_code == 422
    assert not client.get("/api/jobs").json()


def test_comfy_missing_workflow(cfg):
    cfg.inference_backend = "comfyui"
    with TestClient(create_app(cfg), base_url="http://localhost") as http:
        result = submit(http, candidate())
        assert result.status_code == 503 and "WORKFLOW_NOT_CONFIGURED" in result.text
        assert not http.get("/api/jobs").json()


def test_read_only_inspector(tmp_path):
    path = tmp_path / "small.safetensors"
    header = json.dumps(
        {
            "__metadata__": {"source": "fixture"},
            "layer.lora_down.weight": {
                "dtype": "F32",
                "shape": [1, 2],
                "data_offsets": [0, 8],
            },
        }
    ).encode()
    contents = struct.pack("<Q", len(header)) + header + bytes(8)
    path.write_bytes(contents)
    report = inspect(path, True)
    assert report["sha256"] == hashlib.sha256(contents).hexdigest()
    assert report["typical_keys"][0]["shape"] == [1, 2] and report["metadata"] == {
        "source": "fixture"
    }
    assert report["compatibility"] == "unverified" and "sha256" not in inspect(path)
    assert path.read_bytes() == contents


@pytest.fixture
def exported(cfg, monkeypatch):
    cfg.inference_backend = "comfyui"
    root = cfg.comfy_dir("model")
    for p in ("diffusion_models", "text_encoders", "vae", "loras"):
        (root / p).mkdir(parents=True)
    for kind in ("input", "output"):
        cfg.comfy_dir(kind).mkdir()
    files = [
        ("diffusion_models", PROFILES[H3].checkpoint),
        ("text_encoders", "fixture_encoder.safetensors"),
        ("vae", "fixture_v.safetensors"),
        ("vae", "fixture_a.safetensors"),
        ("loras", LORAS[HM].filename),
    ]
    for folder, name in files:
        (root / folder / name).write_bytes(b"synthetic fixture")
    fields = {
        "prompt": "Scene",
        "seed": 42,
        "width": 640,
        "height": 360,
        "duration": 3,
        "steps": 18,
        "output_prefix": "test",
        "sampler": "E",
        "scheduler": "B",
        "shift": 12,
        "turbo_enabled": False,
    }

    def target(node, field):
        return {"node": node, "input": field}

    obj = {
        "TestSources": {
            "input": {
                "required": {f"m{i}": [[name]] for i, (_, name) in enumerate(files[:4])}
            },
            "output": ["TestWeight"],
        },
        "TestAdapter": {
            "input": {
                "required": {
                    "w": [[files[-1][1]]],
                    "base": ["TestWeight"],
                    "strength": ["FLOAT", {"min": 0, "max": 1.5}],
                }
            },
            "output": ["TestWeight"],
        },
        "TestSave": {
            "input": {
                "required": {
                    **{k: ["STRING"] for k in fields},
                    "model": ["TestWeight"],
                    "sampler": [["E"]],
                    "scheduler": [["B"]],
                    "shift": ["FLOAT"],
                    "turbo_enabled": ["BOOLEAN"],
                }
            },
            "output": [],
        },
    }
    identity = ("a" * 40, "test-gpu", "test-driver")
    monkeypatch.setattr(evidence, "runtime_identity", lambda cfg: identity)

    def write(lid, *, attest=True, broken=None):
        cfg.workflow_dir.mkdir(exist_ok=True)
        manifest = manifest_path(cfg, "text", H3, lid)
        manifest.parent.mkdir(exist_ok=True)
        graph = {
            "s": {
                "class_type": "TestSources",
                "inputs": {f"m{i}": name for i, (_, name) in enumerate(files[:4])},
            },
            "o": {"class_type": "TestSave", "inputs": {**fields, "model": ["s", 0]}},
        }
        bindings = {k: [target("o", k)] for k in fields}
        bindings["sampler"][0]["values"] = {"euler": "E"}
        bindings["scheduler"][0]["values"] = {"beta": "B"}
        models = [
            {**target("s", f"m{i}"), "folder": folder, "base": i == 0}
            for i, (folder, name) in enumerate(files[:4])
        ]
        name = ("lora" if lid else "base") + ".json"
        variant = {
            "workflow": name,
            "slots": {},
            "fields": bindings,
            "models": models,
            "output_node": "o",
            "output_key": "media",
        }
        if lid:
            graph["l"] = {
                "class_type": "TestAdapter",
                "inputs": {"base": ["s", 0], "w": files[-1][1], "strength": 0.8},
            }
            graph["o"]["inputs"]["model"] = ["l", 0]
            bindings["lora_strength"] = [target("l", "strength")]
            models.append({**target("l", "w"), "folder": "loras"})
            variant["loader"] = {
                "model_input": "base",
                "model_output": 0,
                "model_type": "TestWeight",
            }
        if broken == "loader":
            variant.pop("loader", None)
        if broken == "detached":
            graph["o"]["inputs"]["model"] = ["s", 0]
        if broken == "enum":
            bindings["scheduler"][0]["values"] = {"beta": "BAD"}
        (cfg.workflow_dir / name).write_text(json.dumps(graph))
        spec = {
            "family": "FL2VA",
            "durations": [3],
            "resolutions": ["640x360"],
            "steps": [18],
            "preset": "Test",
            "samplers": ["euler"],
            "schedulers": ["beta"],
            "shifts": [12],
            "confirmed_loras": [files[-1][1]] if lid else [],
            "variants": [variant],
        }
        manifest.write_text(json.dumps(spec))
        if attest:
            folder = cfg.data_dir / "compatibility"
            folder.mkdir(parents=True, exist_ok=True)
            digest = evidence.sha256(cfg.workflow_dir / name)
            rec = evidence.SmokeRecord(
                bindings_sha256=evidence.sha256(manifest),
                workflow_sha256=digest,
                checkpoint_sha256=evidence.sha256(root / files[0][0] / files[0][1]),
                lora_sha256=evidence.sha256(root / files[-1][0] / files[-1][1])
                if lid
                else None,
                comfy_commit=identity[0],
                gpu=identity[1],
                driver=identity[2],
                prompt_id="synthetic",
                baseline_prompt_id="other",
                result="passed",
                loaded=True,
                generated=True,
                unmatched_keys=0,
                effect_observed=True,
            )
            (folder / f"{manifest.stem}.{digest}.json").write_text(
                rec.model_dump_json()
            )

    return cfg, obj, write


@pytest.mark.parametrize(
    "scenario",
    [
        "lora",
        "zero",
        "none",
        "loader",
        "detached",
        "enum",
        "no_receipt",
        "changed_weights",
        "loader_error",
    ],
)
def test_prompt_boundary(exported, scenario, video):
    cfg, objects, write = exported
    lid = None if scenario == "none" else HM
    strength = 0 if scenario in ("zero", "none") else 0.8
    write(HM if strength else None, attest=scenario != "no_receipt", broken=scenario)
    if scenario == "changed_weights":
        (cfg.comfy_dir("model") / "loras" / LORAS[HM].filename).write_bytes(b"changed")
    req = Request.model_validate(candidate(lid, strength, "Test"))
    store = Store(cfg.data_dir)
    key = str(uuid4())
    with store.connection(True) as db:
        store.insert(db, key, req, [], "comfyui")
    run = Run(store, store.claim())
    posted = []

    def http(request):
        path = request.url.path
        if path == "/object_info":
            return httpx.Response(200, json=objects)
        if path == "/queue":
            return httpx.Response(200, json={"queue_pending": [], "queue_running": []})
        if path == "/prompt":
            graph = json.loads(request.content)["prompt"]
            posted.append(graph)
            out = graph["o"]["inputs"]
            assert (
                out["sampler"] == "E"
                and out["scheduler"] == "B"
                and out["shift"] == 12
                and out["turbo_enabled"] is False
            )
            assert graph["s"]["inputs"]["m0"] == PROFILES[H3].checkpoint
            assert ("l" in graph) == bool(strength)
            if strength:
                assert graph["l"]["inputs"]["strength"] == strength
            return httpx.Response(200, json={"prompt_id": "fixture"})
        if path == "/history/fixture":
            if scenario == "loader_error":
                return httpx.Response(
                    200,
                    json={
                        "fixture": {
                            "status": {
                                "status_str": "error",
                                "messages": [
                                    [
                                        "execution_error",
                                        {
                                            "node_id": "l",
                                            "exception_message": "size mismatch",
                                        },
                                    ]
                                ],
                            }
                        }
                    },
                )
            sub = f"h3studio/{key}"
            dest = cfg.comfy_dir("output") / sub
            dest.mkdir(parents=True)
            (dest / "v.mp4").write_bytes(video)
            return httpx.Response(
                200,
                json={
                    "fixture": {
                        "status": {"completed": True},
                        "outputs": {
                            "o": {
                                "media": [
                                    {
                                        "type": "output",
                                        "filename": "v.mp4",
                                        "subfolder": sub,
                                    }
                                ]
                            }
                        },
                    }
                },
            )
        pytest.fail(path)

    async def unavailable(*args, **kwargs):
        raise OSError()

    engine = ComfyUIBackend(cfg, httpx.MockTransport(http), unavailable)
    if scenario in ("lora", "zero", "none"):
        assert asyncio.run(engine.generate(run)).is_file() and len(posted) == 1
    elif scenario == "loader_error":
        with pytest.raises(InferenceError, match="LORA_KEYS_INCOMPATIBLE"):
            asyncio.run(engine.generate(run))
        assert not asyncio.run(engine.selection_cap("text", H3, HM)).ready
    else:
        with pytest.raises(InferenceError):
            asyncio.run(engine.generate(run))
        assert not posted


def test_smoke_receipt_requires_real_history_and_review(exported, monkeypatch, video):
    cfg, objects, write = exported
    write(None, attest=False)
    write(HM, attest=False)
    base_id, lora_id = str(uuid4()), str(uuid4())
    history = {}
    for pid, name in ((base_id, "base"), (lora_id, "lora")):
        graph = json.loads((cfg.workflow_dir / f"{name}.json").read_text())
        dest = cfg.comfy_dir("output") / f"{name}.mp4"
        dest.write_bytes(video)
        history[pid] = {
            "prompt": [0, pid, graph],
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "o": {
                    "media": [
                        {"filename": dest.name, "subfolder": "", "type": "output"}
                    ]
                }
            },
        }
    real_client = httpx.Client

    def local_history(**kwargs):
        return real_client(
            **kwargs,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=history)
            ),
        )

    monkeypatch.setattr(record_smoke.httpx, "Client", local_history)
    monkeypatch.setattr(
        record_smoke,
        "runtime_identity",
        lambda cfg: ("a" * 40, "test-gpu", "test-driver"),
    )
    with pytest.raises(InferenceError):
        record_smoke.record(cfg, "text", HM, lora_id, base_id, 0, False)
    output = record_smoke.record(cfg, "text", HM, lora_id, base_id, 0, True)
    assert json.loads(output.read_text())["result"] == "passed"
    assert WorkflowAdapter(cfg, "text", objects, H3, HM).cap.ready
    record_smoke.record(cfg, "text", HM, lora_id, None, 3, False)
    with pytest.raises(InferenceError, match="LORA_KEYS_INCOMPATIBLE"):
        WorkflowAdapter(cfg, "text", objects, H3, HM)
