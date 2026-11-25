#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
exec "$BASE_PYTHON" "$here/phase2/warmup.py" status "$RUNTIME_DIR" "$WORKFLOW_DIR/image.api.json"
