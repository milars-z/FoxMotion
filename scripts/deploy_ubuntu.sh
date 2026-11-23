#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ $EUID != 0 ]] || { echo "Run as a non-root Studio service owner."; exit 1; }
root=$(pwd -P)
export PATH="$root/.private/node/bin:$PATH"
py="$root/.venv/bin/python"
[[ -x "$py" ]] || { echo "Run bash scripts/install_ubuntu.sh first."; exit 1; }
owner=$(id -un)
comfy=$("$py" -c 'from backend.config import Settings; print(Settings().comfyui_home.resolve())')
data=$("$py" -c 'from backend.config import Settings; print(Settings().data_dir.resolve())')
mode=$("$py" -c 'from backend.config import Settings; print(Settings().inference_backend)')
input=$("$py" -c 'from backend.config import Settings; print(Settings().comfy_dir("input"))')
output=$("$py" -c 'from backend.config import Settings; print(Settings().comfy_dir("output"))')
for path in "$root" "$comfy" "$input" "$output" "$data"; do
    [[ "$path" =~ ^/[a-zA-Z0-9_./-]+$ ]] || { echo "Use service paths containing letters, digits, slash, dot, dash or underscore."; exit 1; }
done
bash scripts/check_environment.sh --host-only
npm --prefix frontend run build
stage=$(mktemp -d)
trap 'rm -f "$stage"/h3-{api,worker,comfyui}.service; rmdir "$stage"' EXIT
unit() {
  local name="$1" dir="$2" command="$3"
  cat >"$stage/h3-$name.service" <<UNIT
[Unit]
Description=H3 Video Studio $name
After=network.target
RequiresMountsFor=$data $dir
[Service]
Type=simple
User=$owner
WorkingDirectory=$dir
ExecStart=$command
Restart=on-failure
RestartSec=3
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
IPAddressDeny=any
IPAddressAllow=localhost
Environment=PYTHONUNBUFFERED=1
Environment=HF_HUB_OFFLINE=1
Environment=HF_HUB_DISABLE_TELEMETRY=1
Environment=DO_NOT_TRACK=1
[Install]
WantedBy=multi-user.target
UNIT
}
unit api "$root" "$py -m backend"
unit worker "$root" "$py -m backend.worker"
if [[ "$mode" == comfyui ]]; then
  [[ -x "$comfy/.venv/bin/python" && -f "$comfy/main.py" ]] || { echo "Install ComfyUI and its separate .venv at COMFYUI_HOME first."; exit 1; }
  "$comfy/.venv/bin/python" "$comfy/main.py" --help | grep -- '--disable-api-nodes' >/dev/null || { echo "Use a ComfyUI release with --disable-api-nodes."; exit 1; }
  unit comfyui "$comfy" "$comfy/.venv/bin/python $comfy/main.py --listen 127.0.0.1 --port 8188 --disable-api-nodes --disable-auto-launch --input-directory $input --output-directory $output"
  sudo install -m 0644 "$stage/h3-comfyui.service" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable h3-comfyui
  sudo systemctl restart h3-comfyui
  for ((i=0; i<90; i++)); do
    "$py" -c 'import httpx; httpx.get("http://127.0.0.1:8188/queue",trust_env=False,timeout=2).raise_for_status()' 2>/dev/null && break
    sleep 2
  done
fi
"$py" -m backend.preflight
sudo install -m 0644 "$stage/h3-api.service" "$stage/h3-worker.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable h3-api h3-worker
sudo systemctl restart h3-api h3-worker
for ((i=0; i<30; i++)); do
  if "$py" -c 'import httpx; s=httpx.get("http://127.0.0.1:7860/api/system",trust_env=False,timeout=2).json(); assert s["worker"]["online"] and s["worker"]["backend"]==s["backend"]' 2>/dev/null; then
    echo "Studio and Worker ready on 127.0.0.1:7860"; exit 0
  fi
  sleep 1
done
echo "Startup check failed. Inspect journalctl -u h3-api -u h3-worker"; exit 1
