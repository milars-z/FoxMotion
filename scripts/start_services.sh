#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode=$(.venv/bin/python -c 'from backend.config import Settings; print(Settings().inference_backend)')
if [[ "$mode" == comfyui ]]; then sudo systemctl start h3-comfyui; fi
sudo systemctl start h3-api h3-worker
systemctl --no-pager status h3-api h3-worker
