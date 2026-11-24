#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
[[ $# == 0 || ( $# == 1 && $1 == --comfy-only && $INFERENCE_BACKEND == comfyui ) ]] || die "Usage: start.sh [--comfy-only (comfyui mode)]"
bash "$here/check_env.sh"
lock_runtime
[[ -x $control && -f $project/frontend/dist/index.html && -d $RESULT_DIR ]] || die "Run bootstrap.sh first"
if [[ ! -e $DATA_DIR/studio.db && -n $(find "$BACKUP_DIR" -maxdepth 1 -name '*.sha256' -print -quit) && ${1:-} != --comfy-only ]]; then
    die "Found backups with no local database. Run restore.sh with a snapshot ID before starting."
fi
cd "$RUNTIME_DIR"
export PYTHONPATH="$project"
if [[ $INFERENCE_BACKEND == comfyui ]]; then
    [[ -x $comfy && ${COMFYUI_REV:-} =~ ^[a-f0-9]{40}$ && $(git -C "$COMFYUI_HOME" rev-parse HEAD) == "$COMFYUI_REV" ]] || die "Run ComfyUI bootstrap for this revision"
    "$comfy" "$here/check.py" gpu
    flags=$("$comfy" "$COMFYUI_HOME/main.py" --help)
    for flag in --disable-api-nodes --disable-all-custom-nodes --extra-model-paths-config --temp-directory; do
        [[ $flags == *"$flag"* ]] || die "ComfyUI does not support required flag $flag"
    done
fi
# The daemon captures its environment at startup. Reject reuse with changed paths/mode.
signature=$(printf '%s\n' "$project" "$RUNTIME_DIR" "$DATA_DIR" "$RESULT_DIR" "$WORKFLOW_DIR" "$COMFYUI_HOME" "$COMFYUI_MODEL_DIR" "$INFERENCE_BACKEND" "${COMFYUI_REV:-}" | sha256sum)
if active; then
    [[ $(cat "$RUNTIME_DIR/started-config") == "$signature" ]] || die "Configuration changed; run stop.sh then start.sh"
else
    cat > "$RUNTIME_DIR/extra-models.yaml" <<EOF
foxmotion:
  base_path: "$COMFYUI_MODEL_DIR"
  is_default: true
  diffusion_models: diffusion_models
  text_encoders: text_encoders
  vae: vae
  embeddings: embeddings
  loras: loras
EOF
    cat > "$conf" <<EOF
[unix_http_server]
file=$RUNTIME_DIR/supervisor.sock
chmod=0600
[supervisord]
pidfile=$RUNTIME_DIR/supervisord.pid
logfile=$RUNTIME_DIR/logs/supervisor.log
logfile_maxbytes=5MB
logfile_backups=2
childlogdir=$RUNTIME_DIR/logs
umask=077
[rpcinterface:supervisor]
supervisor.rpcinterface_factory=supervisor.rpcinterface:make_main_rpcinterface
[supervisorctl]
serverurl=unix://$RUNTIME_DIR/supervisor.sock
EOF
    program() {
        cat >> "$conf" <<EOF
[program:$1]
command=$2
directory=$3
autostart=false
autorestart=unexpected
startsecs=1
stopsignal=TERM
stopasgroup=true
killasgroup=true
stopwaitsecs=30
redirect_stderr=true
stdout_logfile=$RUNTIME_DIR/logs/$1.log
stdout_logfile_maxbytes=5MB
stdout_logfile_backups=2
EOF
    }
    program api "$studio -m backend" "$RUNTIME_DIR"
    program worker "$studio -m backend.worker" "$RUNTIME_DIR"
    if [[ $INFERENCE_BACKEND == comfyui ]]; then
        program comfy "$comfy $COMFYUI_HOME/main.py --listen 127.0.0.1 --port 8188 --disable-api-nodes --disable-all-custom-nodes --disable-auto-launch --base-directory $RUNTIME_DIR/comfy --extra-model-paths-config $RUNTIME_DIR/extra-models.yaml --input-directory $COMFYUI_INPUT_DIR --output-directory $COMFYUI_OUTPUT_DIR --temp-directory $RUNTIME_DIR/comfy/temp" "$COMFYUI_HOME"
    fi
    "$RUNTIME_DIR/studio/bin/supervisord" -c "$conf" 9>&-
    echo "$signature" > "$RUNTIME_DIR/started-config"
fi
ensure_started() {
    if ! ctl status "$1" | grep ' RUNNING ' >/dev/null; then
        if [[ -n ${2:-} ]]; then
            "$studio" -c 'import socket,sys; s=socket.socket(); s.bind(("127.0.0.1",int(sys.argv[1]))); s.close()' "$2" || die "Loopback port $2 is occupied"
        fi
        ctl start "$1" || die "Failed to start $1. See $RUNTIME_DIR/logs/$1.log"
    fi
}
if [[ $INFERENCE_BACKEND == comfyui ]]; then
    ensure_started comfy 8188
    for ((i=0; i<120; i++)); do
        curl -fsS --noproxy '*' --max-time 2 http://127.0.0.1:8188/queue >/dev/null 2>&1 && break
        sleep 1
    done
    curl -fsS --noproxy '*' --max-time 3 http://127.0.0.1:8188/queue >/dev/null || die "ComfyUI did not become ready; inspect comfy.log"
    if [[ ${1:-} == --comfy-only ]]; then echo "ComfyUI available at 127.0.0.1:8188 for manual validation"; exit 0; fi
fi
"$studio" -m backend.preflight
ensure_started api 7860
ensure_started worker
for ((i=0; i<30; i++)); do
    if "$studio" -c 'import httpx,os; r=httpx.get("http://127.0.0.1:7860/api/system",trust_env=False,timeout=2).json(); assert r["worker"]["online"] and r["backend"]==r["worker"]["backend"]==os.environ["INFERENCE_BACKEND"]' 2>/dev/null; then
        ctl status
        echo "Studio ready at 127.0.0.1:7860"
        exit 0
    fi
    sleep 1
done
die "API/worker health check failed; inspect $RUNTIME_DIR/logs"
