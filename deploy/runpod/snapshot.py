"""Stream a SQLite online backup and its immutable uploads to one Global Volume object."""

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

CHUNK = 1024 * 1024
ID = re.compile(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{32}")
MEDIA = {".png", ".jpeg", ".jpg", ".webp", ".mp4", ".webm", ".wav", ".mp3"}


def uuid_name(value):
    return str(UUID(value)) == value


def media_path(job, name, extensions=MEDIA):
    if not uuid_name(job) or not isinstance(name, str):
        raise ValueError("Invalid job or media name")
    file = Path(name)
    if name != file.name or file.suffix not in extensions or not uuid_name(file.stem):
        raise ValueError("Invalid stored media name")
    return f"{job}/{name}"


def inputs(db):
    for job, raw in db.execute("SELECT job_id,input_files_json FROM jobs"):
        for item in json.loads(raw):
            yield "inputs/" + media_path(job, item["name"]), item["size"]


class DigestWriter:
    def __init__(self, stream):
        self.stream = stream
        self.digest = hashlib.sha256()

    def write(self, data):
        count = self.stream.write(data)
        if count != len(data):
            raise OSError("Short backup write")
        self.digest.update(data)
        return count


def add(archive, name, path, size):
    if not path.is_file() or path.stat().st_size != size:
        raise ValueError(f"Missing or changed snapshot member: {name}")
    info = tarfile.TarInfo(name)
    info.size = size
    with path.open("rb") as stream:
        archive.addfile(info, stream)


def backup(data, backups):
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex
    tar = backups / f"{stamp}.tar"
    marker_path = backups / f"{stamp}.sha256"
    if tar.exists() or marker_path.exists():
        raise FileExistsError("Snapshot ID already in use")
    dbfile = data / "studio.db"
    if not dbfile.is_file():
        raise ValueError("No local studio.db to back up")
    with tempfile.TemporaryDirectory(dir=data.parent) as tmp:
        copy = Path(tmp) / "studio.db"
        with (
            closing(sqlite3.connect(dbfile.as_uri() + "?mode=ro", uri=True)) as src,
            closing(sqlite3.connect(copy)) as db,
        ):
            src.backup(db)
            with tar.open("wb") as stream:
                writer = DigestWriter(stream)
                with tarfile.open(fileobj=writer, mode="w|") as archive:
                    add(archive, "studio.db", copy, copy.stat().st_size)
                    for name, size in inputs(db):
                        path = data / name
                        if path.resolve() != path:
                            raise ValueError("Unexpected link in input tree")
                        add(archive, name, path, size)
        with tar.open("rb") as saved:
            if hashlib.file_digest(saved, "sha256").digest() != writer.digest.digest():
                raise OSError("Backup read-back mismatch; no completion marker written")
        # An interrupted write has no valid completion marker. No remote rename or lock.
        with marker_path.open("w", encoding="ascii") as marker:
            marker.write(writer.digest.hexdigest() + "\n")
    return stamp


def restore(data, backups, results, stamp):
    if not ID.fullmatch(stamp):
        raise ValueError("Use the snapshot ID (no path or extension)")
    if data.exists():
        raise ValueError(
            "Restore requires a new, absent DATA_DIR; existing data is preserved"
        )
    digest = (backups / f"{stamp}.sha256").read_text(encoding="ascii").strip()
    if not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("Incomplete snapshot marker")
    data.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data.parent) as tmp:
        stage = Path(tmp) / "data"
        stage.mkdir()
        reserve = 256 * CHUNK
        with (backups / f"{stamp}.tar").open("rb") as stream:
            # Validate the very bytes being extracted, including tar padding.
            class Reader:
                def __init__(self):
                    self.digest = hashlib.sha256()

                def read(self, size):
                    block = stream.read(size)
                    self.digest.update(block)
                    return block

            reader = Reader()
            with tarfile.open(fileobj=reader, mode="r|") as archive:
                seen = {}
                for item in archive:
                    if not item.isreg() or item.size < 0:
                        raise ValueError("Unexpected archive entry")
                    if item.name != "studio.db":
                        parts = item.name.split("/")
                        if (
                            len(parts) != 3
                            or parts[0] != "inputs"
                            or media_path(*parts[1:]) != "/".join(parts[1:])
                        ):
                            raise ValueError("Invalid input archive path")
                    if item.size + reserve > shutil.disk_usage(stage).free:
                        raise ValueError("Insufficient container disk for restore")
                    target = stage / item.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with target.open("xb") as out:
                        shutil.copyfileobj(archive.extractfile(item), out, CHUNK)
                    seen[item.name] = item.size
            while reader.read(CHUNK):
                pass
            if reader.digest.hexdigest() != digest:
                raise ValueError("Snapshot SHA-256 mismatch")
        if "studio.db" not in seen:
            raise ValueError("Snapshot database missing")
        with closing(sqlite3.connect(stage / "studio.db")) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            expected = dict(inputs(db))
            if expected != {
                name: size for name, size in seen.items() if name != "studio.db"
            }:
                raise ValueError("Snapshot input inventory mismatch")
            db.execute("DELETE FROM worker_state")
            db.execute(
                "UPDATE jobs SET status='interrupted', stage='恢复快照', error_message='请手动重新生成', finished_at=? WHERE status IN ('queued','running')",
                (datetime.now(UTC).isoformat(),),
            )
            missing = []
            for job, name in db.execute(
                "SELECT job_id, output_path FROM jobs WHERE status='completed'"
            ):
                path = results / media_path(job, name, {".mp4", ".webm"})
                if path.resolve() != path or not path.is_file():
                    missing.append((job,))
            db.executemany(
                "UPDATE jobs SET status='interrupted',stage='成片缺失',error_message='快照对应的持久卷成片已移除',output_path=NULL WHERE job_id=?",
                missing,
            )
            db.commit()
        stage.rename(data)  # local filesystem only, under the operation lock
    return len(missing)


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("action", choices=("backup", "restore"))
    cli.add_argument("data", type=Path)
    cli.add_argument("backups", type=Path)
    cli.add_argument("--results", type=Path)
    cli.add_argument("--id")
    args = cli.parse_args()
    if args.action == "backup":
        print("Snapshot ID:", backup(args.data, args.backups))
    else:
        if not args.id or not args.results:
            cli.error("restore requires --id and --results")
        print(
            "Restored. Missing completed outputs:",
            restore(args.data, args.backups, args.results, args.id),
        )
