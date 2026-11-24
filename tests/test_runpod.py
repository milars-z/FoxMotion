import asyncio
import hashlib
import tarfile
from uuid import uuid4

import pytest
from conftest import request_data, submit
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.services.store import Store
from backend.worker import Worker
from deploy.runpod import check, snapshot


def test_publish_restore_and_delete(cfg, png, tmp_path):
    cfg.result_dir = tmp_path / "volume" / "outputs"
    cfg.result_dir.mkdir(parents=True)
    backups = tmp_path / "backup"
    backups.mkdir()
    with TestClient(create_app(cfg), base_url="http://localhost") as client:
        job = submit(
            client,
            request_data("image", roles=["first_frame"]),
            [("first.png", png, "image/png")],
        ).json()
        key = job["job_id"]
        worker = Worker(cfg)
        asyncio.run(worker.execute(worker.store.claim()))
        row = worker.store.get(key)
        assert row["status"] == "completed"
        final = cfg.result_dir / key / row["output_path"]
        assert final.stat().st_size > 100
        assert not list((cfg.data_dir / "outputs" / key).iterdir())
        url = f"/api/jobs/{key}/result"
        assert client.get(url, headers={"Range": "bytes=0-31"}).status_code == 206
        queued = submit(client).json()["job_id"]
        stamp = snapshot.backup(cfg.data_dir, backups)
        restored = tmp_path / "restored"
        assert snapshot.restore(restored, backups, cfg.result_dir, stamp) == 0
        assert Store(restored).get(queued)["status"] == "interrupted"
        old = cfg.model_copy(update={"data_dir": restored})
        with TestClient(create_app(old), base_url="http://localhost") as reopened:
            assert reopened.get(url).content == final.read_bytes()
            assert reopened.post(f"/api/jobs/{key}/regenerate").status_code == 201
        with pytest.raises(ValueError, match="absent"):
            snapshot.restore(restored, backups, cfg.result_dir, stamp)
        assert client.delete(f"/api/jobs/{key}").status_code == 204
        assert not final.exists()
        another = tmp_path / "after-delete"
        assert snapshot.restore(another, backups, cfg.result_dir, stamp) == 1
        assert Store(another).get(key)["status"] == "interrupted"


def test_volume_write_failure(cfg, client, tmp_path):
    # Simulate a configured volume becoming unavailable after job submission.
    cfg.result_dir = tmp_path / "missing" / "outputs"
    job = submit(client).json()
    worker = Worker(cfg)
    asyncio.run(worker.execute(worker.store.claim()))
    assert worker.store.get(job["job_id"])["status"] == "failed"
    assert client.get(f"/api/jobs/{job['job_id']}/result").status_code == 409


def test_cancel_during_publication(cfg, tmp_path, monkeypatch):
    from backend import worker as worker_module

    cfg.result_dir = tmp_path / "outputs"
    cfg.result_dir.mkdir()
    with TestClient(create_app(cfg), base_url="http://localhost") as http:
        task = submit(http).json()["job_id"]
        publish = worker_module.publish_result

        def cancel_after_copy(source, root, job_id):
            file = publish(source, root, job_id)
            assert http.post(f"/api/jobs/{task}/cancel").is_success
            return file

        monkeypatch.setattr(worker_module, "publish_result", cancel_after_copy)
        worker = Worker(cfg)
        asyncio.run(worker.execute(worker.store.claim()))
        assert worker.store.get(task)["status"] == "canceled"
        assert list((cfg.result_dir / task).iterdir()) == []


def test_retry_deletion_after_volume_returns(cfg, tmp_path):
    cfg.result_dir = tmp_path / "outputs"
    cfg.result_dir.mkdir()
    with TestClient(create_app(cfg), base_url="http://localhost") as http:
        task = submit(http).json()["job_id"]
        worker = Worker(cfg)
        asyncio.run(worker.execute(worker.store.claim()))
        detached = tmp_path / "detached-volume"
        cfg.result_dir.rename(detached)
        assert http.delete(f"/api/jobs/{task}").status_code == 204
        with worker.store.connection() as db:
            assert db.execute("SELECT job_id FROM trash").fetchone()[0] == task
        detached.rename(cfg.result_dir)
    with TestClient(create_app(cfg), base_url="http://localhost") as reopened:
        assert reopened.get(f"/api/jobs/{task}").status_code == 404
        assert not (cfg.result_dir / task).exists()


def test_snapshot_integrity_before_promotion(cfg, client, tmp_path):
    submit(client)
    stamp = snapshot.backup(cfg.data_dir, tmp_path)
    marker = tmp_path / f"{stamp}.sha256"
    marker.write_text("0" * 64)
    dest = tmp_path / "restored"
    with pytest.raises(ValueError, match="SHA-256"):
        snapshot.restore(dest, tmp_path, tmp_path / "outputs", stamp)
    assert not dest.exists()
    marker.unlink()
    with pytest.raises(FileNotFoundError):
        snapshot.restore(dest, tmp_path, tmp_path / "outputs", stamp)
    assert not dest.exists()


def test_archive_rejects_unexpected_members(tmp_path):
    stamp = "20260101T010101Z-" + uuid4().hex
    archive = tmp_path / f"{stamp}.tar"
    with tarfile.open(archive, mode="w") as tar:
        tar.addfile(tarfile.TarInfo("../outside"))
    (tmp_path / f"{stamp}.sha256").write_text(
        hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    with pytest.raises(ValueError, match="archive path"):
        snapshot.restore(tmp_path / "dest", tmp_path, tmp_path, stamp)
    assert not (tmp_path / "outside").exists()


def test_approved_wheel_plan():
    item = {
        "metadata": {"name": "example"},
        "download_info": {
            "url": "https://wheels.example/example.whl",
            "archive_info": {"hashes": {"sha256": "a" * 64}},
        },
    }
    assert "--hash=sha256:" in check.check_plan({"install": [item]})
    for name in ("torch", "torchvision", "Triton", "nvidia_cudnn_cu12"):
        with pytest.raises(ValueError, match="GPU stack"):
            check.check_plan({"install": [{**item, "metadata": {"name": name}}]})
    item["download_info"]["url"] = "https://wheels.example/unreviewed.tar.gz"
    with pytest.raises(ValueError, match="wheel"):
        check.check_plan({"install": [item]})


def test_storage_isolation(tmp_path):
    volume, runtime, project, comfy = [
        tmp_path / n for n in ("volume", "runtime", "project", "comfy")
    ]
    data = runtime / "data"
    dirs = [volume / n for n in ("outputs", "backup", "config/workflows", "models")]
    check.paths(volume, runtime, project, comfy, data, *dirs)
    for bad in (volume / "data", project, runtime, runtime / "studio/data"):
        with pytest.raises(ValueError):
            check.paths(volume, runtime, project, comfy, bad, *dirs)
    with pytest.raises(ValueError):
        check.paths(
            volume, runtime, project, comfy, data, *dirs[:-1], dirs[0] / "models"
        )


def test_cgroup_ancestor_limits(tmp_path, monkeypatch, capsys):
    root = tmp_path / "cgroup"
    leaf = root / "pod/tasks"
    leaf.mkdir(parents=True)
    (tmp_path / "mountinfo").write_text(f"1 0 0:1 / {root} rw - cgroup2 cgroup rw")
    (tmp_path / "cgroupfile").write_text("0::/pod/tasks")
    proc = tmp_path / "proc"
    proc.mkdir()
    (tmp_path / "mountinfo").rename(proc / "mountinfo")
    (tmp_path / "cgroupfile").rename(proc / "cgroup")
    (root / "cpu.max").write_text("900000 100000")
    (root / "memory.max").write_text(str(50 * 10**9))
    (leaf / "memory.max").write_text("max")
    monkeypatch.setattr(
        check.os, "sched_getaffinity", lambda _: range(64), raising=False
    )
    check.resources(proc)
    out = capsys.readouterr().out
    assert "quota vCPU: 9.0" in out and "RAM ceiling GiB: 46.57" in out
