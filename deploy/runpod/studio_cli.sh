#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
volume
[[ -x $studio ]] || die "Run bootstrap.sh first"
case "${1:-}" in preflight|record_smoke) module=$1; shift;; *) die "Usage: studio_cli.sh {preflight|record_smoke} [args...]";; esac
cd "$RUNTIME_DIR"
export PYTHONPATH="$project"
exec "$studio" -m "backend.$module" "$@"
