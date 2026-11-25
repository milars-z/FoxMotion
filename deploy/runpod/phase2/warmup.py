"""One opt-in synthetic inference per supervised Comfy process. Status is read-only."""
import argparse
import fcntl
import json
import os
import sqlite3
from pathlib import Path
import subprocess
import sys
import urllib.request


def identity(runtime):
    command = [runtime/"studio/bin/supervisorctl", "-c", runtime/"supervisord.conf", "pid", "comfy"]
    pid = int(subprocess.check_output(command, stderr=subprocess.DEVNULL))
    ticks = (Path("/proc") / str(pid) / "stat").read_text().split()[21]
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://127.0.0.1:8188/queue", timeout=3) as r:
        q = json.load(r)
    busy = bool(q["queue_running"] or q["queue_pending"])
    database = Path(os.environ.get("DATA_DIR", runtime / "data")) / "studio.db"
    if database.exists():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=3) as db:
            busy = busy or bool(db.execute(
                "select exists(select 1 from jobs where status in ('queued','running'))"
            ).fetchone()[0])
    return f"{pid}-{ticks}", busy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "run"])
    parser.add_argument("runtime", type=Path)
    parser.add_argument("workflow", type=Path)
    parser.add_argument("--measure", action="store_true", help="Record hardware telemetry during this probe")
    args = parser.parse_args()
    try:
        key, busy = identity(args.runtime)
    except (ValueError, OSError, subprocess.SubprocessError):
        print(json.dumps(dict(process_ready=False, model_ready=False, inference_ready=False)))
        return 1
    root = args.runtime / "warmup"
    directory = root / key
    if args.action == "status":
        state = dict(process_ready=True, model_ready=False, inference_ready=False)
        if (directory / "state.json").is_file():
            state.update(json.loads((directory / "state.json").read_text()))
        state["process_ready"] = True
        state["queue_busy"] = busy
        if (directory / "failed").exists():
            state["warmup_failed"] = True
        print(json.dumps(state))
        return 0
    root.mkdir(mode=0o700, exist_ok=True)
    with (root / "warmup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if directory.exists():
            state = directory / "state.json"
            return 0 if state.exists() and json.loads(state.read_text()).get("inference_ready") else 1
        if busy:
            print("Warmup deferred while queue is busy")
            return 1
        command = [sys.executable, Path(__file__).with_name("probe.py"), directory,
                   "--runtime", args.runtime, "--workflow", args.workflow, "--seed", "42"]
        if args.measure:
            command.append("--measure")
        result = subprocess.run(command)
        if result.returncode and directory.exists():
            (directory / "failed").touch()
        return result.returncode


if __name__ == "__main__":
    sys.exit(main())
