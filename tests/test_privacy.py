import asyncio
import io
import json
import sqlite3
import tarfile
import wave
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from filelock import FileLock

from conftest import request_data, submit
from backend.inference import private_comfy
from backend.services import privacy
from backend.worker import Worker
from deploy.runpod import snapshot


def enqueue(client, prompt, roles=(), files=()):
    payload = request_data("reference" if roles else "text", prompt, list(roles))
    response = submit(client, {**payload, "privacy_mode":True}, files)
    assert response.status_code == 201, response.text
    private = response.json()
    assert private["privacy_mode"] and private["prompt"] == "" and not private["input_files"]
    return private["job_id"]


def wav():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", ""))
        audio.writeframes(b"\0\0" * 800)
    return buffer.getvalue()


def verify_snapshot(cfg, tmp_path, key, marker, ordinary):
    backups = tmp_path / "archives"
    backups.mkdir(exist_ok=True)
    archive = backups / (snapshot.backup(cfg.data_dir, backups) + ".tar")
    data = archive.read_bytes()
    assert marker.encode() not in data and key.encode() not in data
    with tarfile.open(archive) as tar:
        assert tar.getnames() == ["studio.db"]
        restored = tmp_path / "inspect.db"
        restored.write_bytes(tar.extractfile("studio.db").read())
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT job_id FROM jobs").fetchall() == [(ordinary,)]


def test_private_multimedia_result_snapshot_confirmed_delete(cfg, client, png, video, tmp_path, caplog):
    cfg.result_dir = tmp_path / "global-volume"
    cfg.result_dir.mkdir()
    ordinary = submit(client).json()["job_id"]
    # Claim and finish the ordinary fixture before processing the private one.
    worker = Worker(cfg)
    asyncio.run(worker.execute(worker.store.claim()))
    sentinel = "SYNTHETIC-private-caption-73c4"
    private = enqueue(client, sentinel, ("ref_image","ref_video","ref_audio"),
                      [("private-filename.png",png,"image/png"),("private-movie.mp4",video,"video/mp4"),
                       ("private-voice.wav",wav(),"audio/wav")])
    base = privacy.task(cfg,private)
    assert len(list((base/"inputs").iterdir())) == 3
    assert all("private-" not in p.name for p in (base/"inputs").iterdir())
    assert sentinel in (base/"request.json").read_text()
    assert not (cfg.data_dir/"inputs"/private).exists()
    verify_snapshot(cfg,tmp_path,private,sentinel,ordinary)
    asyncio.run(worker.execute(worker.store.claim()))
    assert sorted(x.name for x in base.iterdir()) == ["result"]
    assert (cfg.result_dir/ordinary).is_dir() and not (cfg.result_dir/private).exists()
    detail = client.get("/api/jobs/"+private).json()
    assert detail["status"]=="completed" and detail["prompt"]==""
    for tail in ("","?download=true"):
        response = client.get("/api/jobs/"+private+"/result"+tail,headers={"Range":"bytes=0-63"})
        assert response.status_code == 206 and len(response.content)==64
        assert "no-store" in response.headers["cache-control"]
    assert client.post("/api/jobs/"+private+"/regenerate").status_code==409
    assert client.delete("/api/jobs/"+private).status_code==204
    assert client.get("/api/jobs/"+private).status_code==404 and not base.exists()
    verify_snapshot(cfg,tmp_path,private,sentinel,ordinary)
    assert sentinel not in caplog.text and "private-filename.png" not in caplog.text


@pytest.mark.parametrize("end",["cancel","failed","interrupted","cancel_running"])
def test_cleanup_and_expiry(cfg,client,end):
    key=enqueue(client,"[mock:fail]" if end=="failed" else "synthetic fixture")
    worker=Worker(cfg)
    if end=="cancel":
        assert client.post("/api/jobs/"+key+"/cancel").json()["status"]=="canceled"
    else:
        job=worker.store.claim()
        assert client.delete("/api/jobs/"+key).status_code==409
        if end=="failed":
            asyncio.run(worker.execute(job))
        elif end=="cancel_running":
            client.post("/api/jobs/"+key+"/cancel")
            asyncio.run(worker.execute(job))
        else:
            worker.store.recover()
    assert asyncio.run(privacy.maintain(cfg,worker.store,worker.backend))
    assert not privacy.task(cfg,key).exists()
    assert client.get("/api/jobs/"+key).json()["status"] == {
        "cancel":"canceled","cancel_running":"canceled","interrupted":"interrupted","failed":"failed"}[end]
    with worker.store.connection(True) as db:
        old=(datetime.now(UTC)-timedelta(seconds=cfg.private_retention_seconds+1)).isoformat()
        db.execute("UPDATE jobs SET finished_at=? WHERE job_id=?",(old,key))
    assert asyncio.run(privacy.maintain(cfg,worker.store,worker.backend))
    assert client.get("/api/jobs/"+key).status_code==404


def test_completed_expiry_and_orphan_leases(cfg,client):
    key=enqueue(client,"synthetic result")
    worker=Worker(cfg)
    asyncio.run(worker.execute(worker.store.claim()))
    assert list((privacy.task(cfg,key)/"result").glob("*.mp4"))
    base=privacy.root(cfg)
    orphan=privacy.local_dir(base/"jobs"/str(uuid4()))
    abandoned=privacy.local_dir(base/"spool"/str(uuid4()))
    active=privacy.local_dir(base/"spool"/str(uuid4()))
    with FileLock(str(active/"upload.lock")):
        privacy.abandoned(cfg,worker.store)
        assert active.exists() and not abandoned.exists() and not orphan.exists()
    with worker.store.connection(True) as db:
        db.execute("UPDATE jobs SET finished_at=? WHERE job_id=?",("2000-01-01T00:00:00+00:00",key))
    assert client.get("/api/jobs/"+key+"/result").status_code == 410
    privacy.reap_expired(cfg,worker.store)
    assert not privacy.task(cfg,key).exists() and not worker.store.get(key)


def test_private_cache_cleanup_recovers_without_touching_other_jobs(tmp_path):
    key,scrub,other=(str(uuid4()) for _ in range(3))
    path=tmp_path/"comfy.json"
    graph=private_comfy.maintenance_graph({"weights":{"class_type":"VAELoader","inputs":{"vae_name":"public"}},
                                           "sensitive":{"class_type":"LoadImage","inputs":{"image":"secret.png"}}})
    privacy.write_json(path,dict(generation=key,scrub=scrub,graph=graph))
    history={other:{"status":{"completed":True}},key:{"status":{"completed":True}}}
    requests=[]
    busy=True
    fail_delete=True
    def handle(req):
        nonlocal fail_delete
        p=req.url.path
        if p=="/queue": return httpx.Response(200,json={"queue_running":[[0,key]] if busy else [],"queue_pending":[]})
        if p.startswith("/history/"):
            pid=p.rsplit("/",1)[1]
            return httpx.Response(200,json={pid:history[pid]} if pid in history else {})
        body=json.loads(req.content)
        requests.append((p,body))
        if p=="/prompt":
            assert "sensitive" not in body["prompt"]
            assert set(body["prompt"])=={"weights","private_zero","private_done"}
            history[scrub]={"status":{"completed":True}}
            return httpx.Response(200,json={"prompt_id":scrub})
        assert p=="/history" and body in ({"delete":[key]},{"delete":[scrub]})
        if fail_delete:
            fail_delete=False
            return httpx.Response(503)
        history.pop(body["delete"][0],None)
        return httpx.Response(200)
    async def run():
        nonlocal busy
        async with httpx.AsyncClient(base_url="http://test",transport=httpx.MockTransport(handle)) as client:
            assert not await private_comfy.clean(client,path) and not requests
            busy=False
            assert not await private_comfy.clean(client,path) and path.exists()
            assert await private_comfy.clean(client,path) and not path.exists()
    asyncio.run(run())
    assert list(history)==[other] and sum(p=="/prompt" for p,_ in requests)==1


def test_private_rejects_global_or_link(cfg,tmp_path):
    with pytest.raises(RuntimeError):
        privacy.local_dir("/workspace/forbidden-private")
    link=tmp_path/"link"
    link.symlink_to(tmp_path/"test-target")
    with pytest.raises(RuntimeError):
        privacy.local_dir(link/"payload")

@pytest.mark.parametrize("succeed",[True,False])
def test_comfy_private_worker_with_fake_transport(cfg,client,png,video,tmp_path,monkeypatch,succeed):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from backend.inference.comfyui import ComfyUIBackend
    from backend.inference.mock import MockBackend
    import backend.inference.comfyui as comfy

    cfg.result_dir = tmp_path / "global-volume"
    cfg.result_dir.mkdir()
    cfg.inference_backend = "comfyui"
    caption = "SYNTHETIC-H3-PRIVATE-REF-5e3c"
    key = enqueue(client, caption, ("ref_image","ref_video","ref_audio"),
                  [("original-name.png",png,"image/png"),("motion.mp4",video,"video/mp4"),("tone.wav",wav(),"audio/wav")])
    caps = asyncio.run(MockBackend(cfg).capabilities())
    variant = SimpleNamespace(fields={"prompt":[SimpleNamespace(node="h3",input="prompt")]},
                              reference_node="h3", output_node="save", output_key="images", loader=None, workflow="ref")
    class Adapter:
        def __init__(self,*args):
            self.cap = next(c for c in caps if c.mode=="reference")
            self.graphs = [(None,variant,None)]
            self.revocation_paths = {}
        def render(self, req, names, prefix):
            assert len(names)==3
            for files in names.values():
                assert all((cfg.comfy_dir("input")/name).is_file() for name in files)
            return {"h3":{"class_type":"MiniMaxH3ReferenceToVideo","inputs":{"prompt":req.prompt}},
                    "save":{"class_type":"SaveVideo","inputs":{"filename_prefix":prefix}}}, variant
    monkeypatch.setattr(comfy,"WorkflowAdapter",Adapter)
    history={}
    posts=[]
    def handle(req):
        p=req.url.path
        if p=="/system_stats": return httpx.Response(200,json={"system":{"argv":["--cache-classic"]}})
        if p=="/queue": return httpx.Response(200,json={"queue_running":[],"queue_pending":[]})
        if p=="/object_info": return httpx.Response(200,json={})
        if p.startswith("/history/"):
            pid=p.rsplit("/",1)[1]
            return httpx.Response(200,json={pid:history[pid]} if pid in history else {})
        body=json.loads(req.content)
        posts.append(p)
        if p=="/history":
            history.pop(body["delete"][0],None)
        elif p=="/prompt":
            assert caption not in req.content.decode() and key not in req.content.decode()
            graph=body["prompt"]
            pid=body["prompt_id"]
            if "private_done" in graph:
                assert set(graph)=={"private_done","private_zero"}
                history[pid]={"status":{"completed":True}}
            else:
                folder=graph["private_caption"]["inputs"]["folder"]
                assert (cfg.comfy_dir("input")/folder/"caption.txt").read_text()==caption
                assert body["extra_data"]["preview_method"]=="none"
                from pathlib import Path
                sub=Path(graph["save"]["inputs"]["filename_prefix"]).parent
                dest=cfg.comfy_dir("output")/sub/"generated.mp4"
                dest.write_bytes(video)
                history[pid]={"status":{"completed":succeed,"status_str":"success" if succeed else "error"},
                              "outputs":{"save":{"images":[{"type":"output","filename":dest.name,"subfolder":str(sub)}]}}}
            return httpx.Response(200,json={"prompt_id":pid})
        else:
            raise AssertionError(p)
        return httpx.Response(200,json={})
    worker=Worker(cfg)
    worker.backend=ComfyUIBackend(cfg,transport=httpx.MockTransport(handle))
    worker.backend.validate=AsyncMock()
    asyncio.run(worker.execute(worker.store.claim()))
    assert worker.store.get(key)["status"] == ("completed" if succeed else "failed")
    assert posts==["/prompt","/prompt","/history","/history"] and not history
    for kind in ("input","output"):
        assert not list((cfg.comfy_dir(kind)/"foxmotion-private").iterdir())
    assert not list(cfg.result_dir.iterdir())
    assert client.get("/api/jobs/"+key+"/result").status_code==(200 if succeed else 409)
    assert client.delete("/api/jobs/"+key).status_code==204

def test_runtime_backup_excludes_private_trees(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from deploy.runpod.phase2 import runtime_archive

    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    for name in ("node", "studio", "comfy-venv"):
        (runtime / name).mkdir(parents=True)
        (runtime / name / "allowlisted-version").write_text("fixture")
    (runtime / "gpu-constraints.txt").write_text("fixture")
    (project / "frontend/dist").mkdir(parents=True)
    (project / "frontend/dist/index.html").write_text("fixture")
    marker = b"SYNTHETIC_RUNTIME_PRIVATE_MARKER"
    for name in ("data-private/jobs/test", "data-private/spool/test",
                 "comfy/input/foxmotion-private/test", "comfy/output/foxmotion-private/test"):
        folder = runtime / name
        folder.mkdir(parents=True)
        (folder / "private-fixture").write_bytes(marker)
    monkeypatch.setattr(runtime_archive, "manifest", lambda args: {})
    archives = tmp_path / "archives"
    runtime_archive.backup(SimpleNamespace(runtime=runtime, project=project), archives)
    archive, = archives.glob("*/runtime.tar.gz")
    with tarfile.open(archive, "r:gz") as bundle:
        assert bundle.getnames()
        for member in bundle:
            assert "private" not in member.name
            if member.isfile():
                assert marker not in bundle.extractfile(member).read()


def test_disk_spill_belongs_to_upload_lease(cfg,client,monkeypatch):
    from backend.services import privacy as service
    dirs = []
    real_spool = service.tempfile.SpooledTemporaryFile
    def spool(*args, **kwargs):
        out = real_spool(*args, **kwargs)
        dirs.append((kwargs["dir"], out))
        return out
    monkeypatch.setattr(service.tempfile, "SpooledTemporaryFile", spool)
    payload = {**request_data("image", "synthetic", ["first_frame"]), "privacy_mode": True}
    response = submit(client, payload, [("invalid.png", b"x" * 1600000, "image/png")])
    assert response.status_code == 422 and len(dirs) == 1
    directory, out = dirs[0]
    assert out._rolled and out.closed and not directory.exists()
    assert directory.parent == cfg.private_root / "spool"
