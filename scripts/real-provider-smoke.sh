#!/usr/bin/env bash

set -euo pipefail

TASK="${1:-Real smoke test: inspect the stringbean repository and report whether the orchestrator can start. Do not modify files.}"
RUN_ID="${STRINGBEAN_SMOKE_RUN_ID:-real-provider-local-smoke}"

if command -v sbx >/dev/null 2>&1; then
  exec sbx "$TASK" --run-id "$RUN_ID" --quiet
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/sbx" "$TASK" --run-id "$RUN_ID" --quiet
