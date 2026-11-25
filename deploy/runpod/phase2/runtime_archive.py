"""Offline runtime layers for a matching Pod image and pinned source checkouts."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

LAYERS = ("studio", "comfy-venv", "node", "gpu-constraints.txt")
FRONT = "frontend-dist"


def output(*cmd):
    return subprocess.check_output(cmd, text=True).strip()


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def packages(python):
    code = ("import importlib.metadata as m,json; print(json.dumps(dict(sorted("
            "(d.metadata['Name'],d.version) for d in m.distributions() if d.metadata['Name']))))")
    return json.loads(output(python, "-I", "-c", code))


def image_versions():
    code = ("import torch,torchvision,torchaudio,json,sysconfig; print(json.dumps([torch.__version__,"
            "torchvision.__version__,torchaudio.__version__,torch.version.cuda,sysconfig.get_path('purelib')]))")
    return dict(python=platform.python_version(), base_executable=os.path.realpath(sys.executable),
                distro=platform.freedesktop_os_release()["VERSION_ID"], machine=platform.machine(),
                libc=platform.libc_ver(), gpu_stack=json.loads(output(sys.executable, "-c", code)))


def manifest(args):
    rt = args.runtime
    return dict(schema=1, runtime=str(rt), image=image_versions(),
                project=output("git", "-C", args.project, "rev-parse", "HEAD"),
                comfy=output("git", "-C", args.comfy, "rev-parse", "HEAD"),
                studio_packages=packages(rt/"studio/bin/python"), comfy_packages=packages(rt/"comfy-venv/bin/python"),
                node=output(rt/"node/bin/node","--version"), ffmpeg=output("ffmpeg","-version").splitlines()[0],
                gpu=output("nvidia-smi","--query-gpu=name,driver_version,memory.total,compute_cap","--format=csv,noheader"),
                external=["matching base image GPU stack", "both source checkouts", "persistent model volume and workflows",
                          "local deploy/runpod/.env", "separate Studio history snapshot"])


def backup(args, root):
    identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    folder = root / identifier
    folder.mkdir(parents=True, exist_ok=False)
    info = manifest(args)
    # Build a portable gzip archive from the dependency layers only.
    def select(member):
        if "__pycache__" in Path(member.name).parts or member.name.endswith(".pyc"):
            return None
        return member
    archive = folder / "runtime.tar.gz"
    with tarfile.open(archive, "w:gz", compresslevel=1, dereference=False) as tar:
        for name in LAYERS:
            tar.add(args.runtime / name, name, filter=select)
        tar.add(args.project / "frontend/dist", FRONT, filter=select)
    info["archive_sha256"] = digest(archive)
    metadata = folder / "manifest.json"
    metadata.write_text(json.dumps(info, indent=2) + "\n")
    (folder / "COMPLETE.sha256").write_text(digest(metadata) + "  manifest.json\n")
    print(identifier, flush=True)


def load_manifest(folder, args):
    metadata = folder / "manifest.json"
    if (folder / "COMPLETE.sha256").read_text().strip() != digest(metadata) + "  manifest.json":
        raise ValueError("Manifest SHA-256 mismatch")
    info = json.loads(metadata.read_text())
    if info["schema"] != 1 or info["runtime"] != str(args.runtime):
        raise ValueError("Runtime must use its original absolute prefix (venv entry points)")
    if info["image"] != json.loads(json.dumps(image_versions())):
        raise ValueError("Base image Python / GPU libraries differ")
    for name in ("project","comfy"):
        if output("git","-C",getattr(args,name),"rev-parse","HEAD") != info[name]:
            raise ValueError(f"{name} checkout differs")
    return info


def extract(folder, stage, info):
    archive = folder / "runtime.tar.gz"
    # Copy once from FUSE, verify local bytes, then extract only that verified copy.
    with tempfile.TemporaryDirectory(dir=stage.parent, prefix=".foxmotion-archive-") as tmp:
        local = Path(tmp) / "runtime.tar.gz"
        shutil.copyfile(archive, local)
        if digest(local) != info["archive_sha256"]:
            raise ValueError("Runtime archive SHA-256 mismatch")
        with tarfile.open(local, "r:gz") as tar:
            def checked(member, dest):
                parts = Path(member.name).parts
                if not parts or parts[0] not in (*LAYERS, FRONT) or ".." in parts:
                    raise ValueError(f"Unexpected layer path {member.name!r}")
                if member.issym() and os.path.isabs(member.linkname):
                    if not (len(parts)==3 and parts[0] in ("studio","comfy-venv") and parts[1]=="bin"
                            and re.fullmatch(r"python(?:3(?:\.12)?)?", parts[2])
                            and os.path.realpath(member.linkname) == info["image"]["base_executable"]):
                        raise ValueError(f"Unexpected external symlink {member.name!r}")
                    # Install these approved absolute symlinks after safe extraction.
                    links.append((member.name, member.linkname))
                    return None
                return tarfile.data_filter(member, dest)
            links = []
            tar.extractall(stage, filter=checked)
            for name, target in links:
                (stage / name).symlink_to(target)


def validate(stage, info):
    if not (stage/FRONT/"index.html").is_file():
        raise ValueError("Frontend build missing")
    for name, key in (("studio","studio_packages"),("comfy-venv","comfy_packages")):
        if packages(stage/name/"bin/python") != info[key]:
            raise ValueError(f"{name} package inventory differs")
    if output(stage/"node/bin/node","--version") != info["node"]:
        raise ValueError("Node differs")
    output(stage/"studio/bin/python","-I","-c","import fastapi,uvicorn,supervisor")
    output(stage/"comfy-venv/bin/python","-I","-c","import torch,torchaudio,torchvision,aiohttp,av,safetensors")


def tree_hash(root):
    return {str(p.relative_to(root)): digest(p) for p in root.rglob("*") if p.is_file()}


def restore(args, folder):
    info = load_manifest(folder, args)
    runtime = args.runtime
    if args.action == "restore" and runtime.exists():
        raise FileExistsError("Restore needs a new runtime path at the recorded prefix")
    with tempfile.TemporaryDirectory(dir=runtime.parent, prefix=".foxmotion-stage-") as work:
        stage = Path(work) / "runtime"
        stage.mkdir()
        extract(folder, stage, info)
        validate(stage, info)
        if args.action == "verify":
            trial_project = Path(work) / "trial-frontend"
            trial_project.mkdir()
            trial_runtime = Path(work) / "trial-runtime"
            activate(stage, trial_runtime, trial_project / "dist")
            validate(trial_runtime, info)
            if tree_hash(trial_project / "dist") != tree_hash(trial_runtime / FRONT):
                raise ValueError("Restored frontend differs")
            print("Isolated restore, activation and imports passed")
            return
        activate(stage, runtime, args.project / "frontend/dist")
        print("Runtime activated at", runtime)


def activate(stage, runtime, dist):
    if runtime.exists() or runtime.is_symlink():
        raise FileExistsError("Runtime already exists")
    if dist.exists():
        if tree_hash(dist) != tree_hash(stage / FRONT):
            raise FileExistsError("Existing frontend/dist differs")
    else:
        # Stage on the destination filesystem. A retry accepts an identical dist.
        with tempfile.TemporaryDirectory(dir=dist.parent) as tmp:
            candidate = Path(tmp)/"dist"
            shutil.copytree(stage/FRONT, candidate)
            candidate.rename(dist)
    for sub in ("logs","tmp","cache","comfy/input","comfy/output","comfy/temp","comfy/user"):
        (stage / sub).mkdir(parents=True, exist_ok=True)
    # Preserve the live DATA_DIR absence for the separate history restore flow.
    stage.rename(runtime)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["backup","verify","restore"])
    p.add_argument("id", nargs="?")
    for name in ("volume","runtime","project","comfy"):
        p.add_argument("--"+name, type=Path, required=True)
    args = p.parse_args()
    os.umask(0o077)
    root = args.volume / "runtime_backup/phase2-runtime"
    if args.action == "backup":
        if args.id:
            p.error("backup creates a new ID")
        backup(args, root)
        return
    if not args.id or not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}", args.id):
        p.error("Expected a backup ID")
    with (args.runtime.parent/".foxmotion-runtime-restore.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        restore(args, root / args.id)


if __name__ == "__main__":
    main()
