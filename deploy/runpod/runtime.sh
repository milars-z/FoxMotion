#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
volume
case ${1:-} in
    backup|verify|restore) ;;
    *) die "Usage: runtime.sh backup | {verify|restore} BACKUP_ID" ;;
esac
if [[ $1 == backup ]]; then lock_runtime; fi
exec "$BASE_PYTHON" "$here/phase2/runtime_archive.py" "$@" --volume "$RUNPOD_VOLUME" \
    --runtime "$RUNTIME_DIR" --project "$project" --comfy "$COMFYUI_HOME"
