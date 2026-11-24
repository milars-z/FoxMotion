#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
volume
lock_runtime
[[ $# == 1 ]] || die "Usage: restore.sh SNAPSHOT_ID (see *.sha256 in $BACKUP_DIR)"
[[ ! -S $RUNTIME_DIR/supervisor.sock ]] || die "Stop services before restoring"
"$BASE_PYTHON" "$here/snapshot.py" restore "$DATA_DIR" "$BACKUP_DIR" --results "$RESULT_DIR" --id "$1"
