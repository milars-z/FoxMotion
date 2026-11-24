#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
volume
space
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || die "Expected Ubuntu 24.04"
for tool in git ffmpeg ffprobe curl findmnt flock tar sha256sum; do
    command -v "$tool" >/dev/null || die "Missing $tool in the Pod image"
done
ffmpeg -hide_banner -encoders 2>/dev/null | grep 'libx264 ' >/dev/null || die "FFmpeg requires libx264"
"$BASE_PYTHON" -c 'import sys, venv, ensurepip; print(sys.version); assert sys.version_info[:2] == (3,12), "Select Python 3.12 with venv support"'
"$BASE_PYTHON" "$here/check.py" resources
if command -v nvidia-smi >/dev/null; then
    if ! nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version,compute_cap --format=csv; then
        [[ $INFERENCE_BACKEND == mock ]] || die "Cannot query the allocated GPU"
    fi
elif [[ $INFERENCE_BACKEND == comfyui ]]; then die "GPU unavailable; check Pod allocation"; fi
if [[ $INFERENCE_BACKEND == comfyui ]]; then
    "$BASE_PYTHON" "$here/check.py" gpu
    "$BASE_PYTHON" "$here/check.py" inventory "$COMFYUI_MODEL_DIR" "$WORKFLOW_DIR"
fi
echo "Global Volume $RUNPOD_VOLUME, results $RESULT_DIR, local state $DATA_DIR"
