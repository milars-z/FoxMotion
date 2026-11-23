#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.private/node/bin:$PATH"
py=.venv/bin/python
[[ -x "$py" ]] || { echo "Missing Studio venv. Run bash scripts/install_ubuntu.sh"; exit 1; }
"$py" -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required"'
for cmd in ffmpeg ffprobe node npm findmnt systemctl; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd"; exit 1; }
done
ffmpeg -hide_banner -encoders 2>/dev/null | grep 'libx264 ' >/dev/null || { echo "FFmpeg libx264 encoder missing"; exit 1; }
"$py" -m backend.preflight --host-only
mode=$("$py" -c 'from backend.config import Settings; print(Settings().inference_backend)')
if command -v nvidia-smi >/dev/null; then
    nvidia-smi --query-gpu=name,memory.total,memory.free,driver_version --format=csv || [[ "$mode" == mock ]]
    nvidia-smi | head -n 12 || true
elif [[ "$mode" == comfyui ]]; then
    echo "NVIDIA driver / nvidia-smi missing. Install a compatible driver on the host."; exit 1
else
    echo "Mock mode uses CPU; nvidia-smi unavailable."
fi
data=$("$py" -c 'from backend.config import Settings; print(Settings().data_dir.resolve())')
findmnt -T "$data" -o TARGET,SOURCE,FSTYPE
df -h "$data"
free -h
if [[ "$mode" == comfyui ]]; then
    comfy=$("$py" -c 'from backend.config import Settings; print(Settings().comfyui_home.resolve())')
    [[ -f "$comfy/main.py" && -x "$comfy/.venv/bin/python" ]] || { echo "Missing ComfyUI main.py or separate .venv under $comfy"; exit 1; }
    git -C "$comfy" rev-parse HEAD || echo "ComfyUI commit unavailable; H3 profile verification requires a Git checkout."
    "$comfy/.venv/bin/python" -c 'import torch; assert torch.cuda.is_available(), "CUDA unavailable in ComfyUI venv"; print(torch.cuda.get_device_name(0), torch.version.cuda)'
fi
if [[ "${1:-}" != --host-only ]]; then "$py" -m backend.preflight; fi
