#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
volume
lock_runtime
[[ -d $BACKUP_DIR ]] || die "Run bootstrap.sh first"
"$BASE_PYTHON" "$here/snapshot.py" backup "$DATA_DIR" "$BACKUP_DIR"
