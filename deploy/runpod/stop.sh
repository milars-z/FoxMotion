#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
lock_runtime
if active; then
    ctl shutdown
    for ((i=0; i<100; i++)); do
        [[ -S $RUNTIME_DIR/supervisor.sock ]] || break
        sleep 1
    done
    [[ ! -S $RUNTIME_DIR/supervisor.sock ]] || die "Supervisor shutdown pending; inspect $RUNTIME_DIR/logs"
elif [[ -S $RUNTIME_DIR/supervisor.sock ]]; then
    die "Supervisor socket exists but control failed; inspect $RUNTIME_DIR/logs"
fi
echo "FoxMotion services stopped. Run backup.sh before releasing the Pod."
