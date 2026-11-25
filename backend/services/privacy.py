"""Container-local storage for confidential jobs. General backups never visit these roots."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from filelock import FileLock, Timeout
from starlette.exceptions import HTTPException
from starlette.formparsers import MultiPartException, MultiPartParser

from backend.services.files import inside, job_dir


def local_dir(path):
    path = Path(path).absolute()
    if path.resolve() != path or path.is_relative_to("/workspace"):
        raise RuntimeError("保密目录必须位于容器本地盘")
    ancestor = path
    while not ancestor.exists():
        ancestor = ancestor.parent
    if sys.platform == "linux":
        fs = subprocess.check_output(["findmnt", "-n", "-o", "FSTYPE", "-T", str(ancestor)], text=True).strip()
        if fs not in {"overlay", "ext2", "ext3", "ext4", "xfs", "tmpfs", "btrfs", "zfs"}:
            raise RuntimeError("保密目录位于未经确认的文件系统")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def root(cfg):
    path = cfg.private_root
    for shared in (cfg.result_dir, cfg.workflow_dir, cfg.comfyui_model_dir, cfg.data_dir):
        if shared and (path.is_relative_to(shared.resolve()) or shared.resolve().is_relative_to(path)):
            raise RuntimeError("保密目录与普通存储重叠")
    return local_dir(path)


def task(cfg, key):
    base = root(cfg) / "jobs"
    if base.is_symlink():
        raise RuntimeError("保密任务根目录不可为链接")
    return job_dir(base, key)


def staging(cfg, kind, key):
    base = cfg.comfy_dir(kind) / "foxmotion-private"
    if base.is_symlink():
        raise RuntimeError("保密工作目录不可为链接")
    return job_dir(base, key)


def write_json(path, data):
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as out:
        json.dump(data, out)
        out.flush()
        os.fsync(out.fileno())
    temp.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def remove_tree(path):
    if path.exists():
        shutil.rmtree(path)


def guard(cfg):
    return FileLock(str(root(cfg) / "lifecycle.lock"), timeout=60)


class LocalParser(MultiPartParser):
    def on_headers_finished(self):
        super().on_headers_finished()
        part = self._current_part.file
        if part is not None:
            # Starlette's default spool is still empty here. Bind disk spill to this request.
            original = part.file
            spool = tempfile.SpooledTemporaryFile(max_size=self.spool_max_size, dir=self.directory)
            part.file = spool
            self._files_to_close_on_error[-1] = spool
            original.close()


@asynccontextmanager
async def upload_form(request, cfg):
    with guard(cfg):
        directory = local_dir(root(cfg) / "spool" / str(uuid4()))
        lease = FileLock(str(directory / "upload.lock"))
        lease.acquire()
    try:
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "multipart/form-data":
            # Preserve ordinary form-encoded, file-free API clients. Only multipart can spool media.
            async with request.form() as form:
                yield form, directory
            return
        parser = LocalParser(request.headers, request.stream(), max_files=20, max_fields=1, max_part_size=65536)
        parser.directory = directory
        try:
            form = await parser.parse()
        except MultiPartException as exc:
            raise HTTPException(400, "上传格式无效") from exc
        try:
            yield form, directory
        finally:
            await form.close()
    finally:
        with guard(cfg):
            lease.release()
            remove_tree(directory)


def remove_work(cfg, key, keep_result=False):
    path = task(cfg, key)
    if (path / "comfy.json").exists():
        raise RuntimeError("正等待 ComfyUI 完成保密数据清理")
    stage = path / "stage.json"
    if stage.exists():
        staging_key = read_json(stage)
        for kind in ("input", "output"):
            staging_path = staging(cfg, kind, staging_key)
            if staging_path.exists():
                local_dir(staging_path)
                remove_tree(staging_path)
        stage.unlink()
    if keep_result:
        remove_tree(path / "inputs")
        for name in ("request.json", "request.tmp", "stage.tmp", "comfy.tmp"):
            (path / name).unlink(missing_ok=True)
    else:
        remove_tree(path)


def purge(cfg, store, key):
    with guard(cfg), store.connection(True) as db:
        row = db.execute("SELECT status,privacy_mode FROM jobs WHERE job_id=?", (key,)).fetchone()
        if not row or not row["privacy_mode"] or row["status"] in ("running", "queued"):
            raise RuntimeError("保密任务仍在执行或已清理")
        remove_work(cfg, key)
        db.execute("PRAGMA secure_delete=ON")
        db.execute("DELETE FROM jobs WHERE job_id=? AND privacy_mode=1", (key,))


def abandoned(cfg, store):
    with guard(cfg):
        base = root(cfg)
        for folder in (base / "spool").glob("*"):
            if not folder.is_dir() or folder.is_symlink():
                continue
            try:
                with FileLock(str(folder/"upload.lock"), timeout=0):
                    pass
            except Timeout:
                continue
            remove_tree(folder)
        for folder in (base / "jobs").glob("*"):
            try:
                key = str(UUID(folder.name))
            except ValueError:
                continue
            if key == folder.name and not store.get(key):
                remove_work(cfg, key)


def expired(cfg, finished_at):
    return (datetime.now(UTC) - datetime.fromisoformat(finished_at)).total_seconds() >= cfg.private_retention_seconds


def reap_expired(cfg, store):
    # Run independently of generation, including during a long ordinary job.
    cutoff = (datetime.now(UTC) - timedelta(seconds=cfg.private_retention_seconds)).isoformat()
    with store.connection() as db:
        keys = [row[0] for row in db.execute(
            "SELECT job_id FROM jobs WHERE privacy_mode=1 AND finished_at IS NOT NULL AND finished_at<=?", (cutoff,)
        )]
    for key in keys:
        try:
            with guard(cfg):
                remove_tree(task(cfg, key) / "result")
            purge(cfg, store, key)
        except (RuntimeError, OSError):
            # The Comfy journal, if present, remains available for the Worker.
            continue


async def maintain(cfg, store, backend):
    ready = True
    with store.connection() as db:
        rows = db.execute("SELECT job_id,status,finished_at FROM jobs WHERE privacy_mode=1 AND status NOT IN ('queued','running')").fetchall()
    for row in rows:
        key = row["job_id"]
        path = task(cfg, key)
        try:
            # Originals can go as soon as Studio execution ends. Comfy reads only its copies.
            with guard(cfg):
                remove_tree(path / "inputs")
                (path / "request.json").unlink(missing_ok=True)
            if (path/"comfy.json").is_file():
                from backend.inference.comfyui import ComfyUIBackend
                cleaner = backend if isinstance(backend, ComfyUIBackend) else ComfyUIBackend(cfg)
                if not await cleaner.clean_private(path/"comfy.json"):
                    ready = False
                    continue
            if expired(cfg, row["finished_at"]):
                purge(cfg, store, key)
            else:
                with guard(cfg):
                    remove_work(cfg, key, keep_result=row["status"] == "completed")
        except (OSError, RuntimeError, ValueError):
            # A concurrent manual purge is already a successful cleanup.
            if store.get(key):
                ready = False
    return ready
