#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
volume
lock_runtime
"$BASE_PYTHON" - "$here/workflows" "$WORKFLOW_DIR" <<'PY'
import hashlib
from pathlib import Path
import sys
source, target = map(Path, sys.argv[1:])
files = sorted(source.glob("*.api.json")) + sorted((source / "bindings").glob("*.json"))
for path in files:
    dest = target / path.relative_to(source)
    if dest.exists() and dest.read_bytes() != path.read_bytes():
        raise SystemExit(f"Existing workflow differs: {dest}")
for path in files:
    dest = target / path.relative_to(source)
    dest.parent.mkdir(exist_ok=True, parents=True)
    if not dest.exists():
        with dest.open("xb") as stream:
            stream.write(path.read_bytes())
    if dest.read_bytes() != path.read_bytes():
        raise SystemExit(f"Workflow readback failed: {dest}")
    print(hashlib.sha256(dest.read_bytes()).hexdigest(), dest.name)
PY
