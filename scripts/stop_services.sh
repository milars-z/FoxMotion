#!/usr/bin/env bash
set -euo pipefail
sudo systemctl stop h3-worker h3-api
if systemctl cat h3-comfyui.service >/dev/null 2>&1; then sudo systemctl stop h3-comfyui; fi
