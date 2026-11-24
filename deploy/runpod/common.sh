#!/usr/bin/env bash
# Shared by the RunPod entry points. All runtime locks live on the container disk.
set -Eeuo pipefail
umask 077
trap 'echo "RunPod: ${BASH_SOURCE[0]} line $LINENO failed" >&2' ERR
die() { echo "FoxMotion: $*" >&2; exit 1; }
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
project=$(cd "$here/../.." && pwd -P)
local_fs() {
    local probe fs
    probe=$(realpath -m -- "$1")
    while [[ ! -e "$probe" ]]; do probe=$(dirname -- "$probe"); done
    fs=$(findmnt -T "$probe" -n -o FSTYPE)
    case "$fs" in overlay|ext[234]|xfs|btrfs|zfs|tmpfs) ;; *) die "$1 is on $fs; use a local Linux filesystem";; esac
}
[[ $(uname -s) == Linux ]] || die "Run this entry point on the Ubuntu Pod"
local_fs "$project"
if [[ -f "$here/.env" ]]; then
    local_fs "$here/.env"
    set -a
    source "$here/.env"
    set +a
fi
: "${INFERENCE_BACKEND:=mock}" "${RUNPOD_VOLUME:=/workspace}" "${RUNTIME_DIR:=/opt/video-runtime}"
: "${BASE_PYTHON:=python3}" "${COMFYUI_HOME:=/opt/ComfyUI}" "${MIN_FREE_GIB:=8}"
: "${DATA_DIR:=$RUNTIME_DIR/data}" "${RESULT_DIR:=$RUNPOD_VOLUME/outputs}"
: "${BACKUP_DIR:=$RUNPOD_VOLUME/backup}" "${COMFYUI_MODEL_DIR:=$RUNPOD_VOLUME/models}"
: "${WORKFLOW_DIR:=$RUNPOD_VOLUME/config/workflows}"
export COMFYUI_INPUT_DIR="$RUNTIME_DIR/comfy/input" COMFYUI_OUTPUT_DIR="$RUNTIME_DIR/comfy/output"
export H3_VERIFICATION_DIR="$DATA_DIR/compatibility"
export STUDIO_HOST=127.0.0.1 STUDIO_PORT=7860 COMFYUI_URL=http://127.0.0.1:8188
export INFERENCE_BACKEND DATA_DIR RESULT_DIR COMFYUI_MODEL_DIR COMFYUI_HOME WORKFLOW_DIR
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 DO_NOT_TRACK=1 HF_HUB_DISABLE_TELEMETRY=1
export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 VITE_TELEMETRY_DISABLED=1
export XDG_CACHE_HOME="$RUNTIME_DIR/cache" HF_HOME="$RUNTIME_DIR/cache/hf"
export TMPDIR="$RUNTIME_DIR/tmp" PYTHONUNBUFFERED=1
export PATH="$RUNTIME_DIR/node/bin:$PATH"
studio="$RUNTIME_DIR/studio/bin/python"
comfy="$RUNTIME_DIR/comfy-venv/bin/python"
control="$RUNTIME_DIR/studio/bin/supervisorctl"
conf="$RUNTIME_DIR/supervisord.conf"
[[ $INFERENCE_BACKEND == mock || $INFERENCE_BACKEND == comfyui ]] || die "INFERENCE_BACKEND must be mock or comfyui"
[[ $MIN_FREE_GIB =~ ^[1-9][0-9]*$ ]] || die "MIN_FREE_GIB must be a positive integer"
for path in "$project" "$RUNTIME_DIR" "$RUNPOD_VOLUME" "$COMFYUI_HOME" "$DATA_DIR" "$RESULT_DIR" "$BACKUP_DIR" "$WORKFLOW_DIR" "$COMFYUI_MODEL_DIR"; do
    [[ "$path" =~ ^/[A-Za-z0-9_./-]+$ && "$path" == "$(realpath -m -- "$path")" ]] || die "Use canonical absolute paths without spaces or symlinks: $path"
done
# Reject nested roots to keep backup, deletion and restore isolated.
"$BASE_PYTHON" "$here/check.py" paths "$RUNPOD_VOLUME" "$RUNTIME_DIR" "$project" "$COMFYUI_HOME" "$DATA_DIR" "$RESULT_DIR" "$BACKUP_DIR" "$WORKFLOW_DIR" "$COMFYUI_MODEL_DIR"
local_fs "$RUNTIME_DIR"
for path in studio comfy-venv node cache tmp logs comfy; do local_fs "$RUNTIME_DIR/$path"; done
local_fs "$COMFYUI_HOME"
local_fs "$DATA_DIR"
local_fs "$project/frontend"
local_fs "$project/frontend/node_modules"
volume() {
    local mount
    mount=$(findmnt -T "$RUNPOD_VOLUME" -n -o TARGET,FSTYPE) || die "Missing Global Volume at $RUNPOD_VOLUME"
    read -r target fs <<< "$mount"
    [[ $target == "$RUNPOD_VOLUME" && $fs == fuse.geesefs ]] || die "Expected $RUNPOD_VOLUME fuse.geesefs, found $mount"
    for path in "$RESULT_DIR" "$BACKUP_DIR" "$WORKFLOW_DIR" "$COMFYUI_MODEL_DIR"; do
        local probe="$path"
        while [[ ! -e "$probe" ]]; do probe=$(dirname "$probe"); done
        [[ $(findmnt -T "$probe" -n -o TARGET) == "$RUNPOD_VOLUME" ]] || die "Different mount below Global Volume: $path"
    done
}
space() {
    "$BASE_PYTHON" "$here/check.py" space "$MIN_FREE_GIB" "$project" "$RUNTIME_DIR" "$DATA_DIR" "$COMFYUI_HOME"
}
lock_runtime() {
    mkdir -p -- "$RUNTIME_DIR" "$TMPDIR"
    exec 9>"$RUNTIME_DIR/operation.lock"
    flock -n 9 || die "Another deployment operation is in progress"
}
ctl() { "$control" -c "$conf" "$@"; }
active() { [[ -S "$RUNTIME_DIR/supervisor.sock" ]] && ctl pid >/dev/null 2>&1; }
