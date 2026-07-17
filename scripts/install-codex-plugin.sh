#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MARKETPLACE_ROOT="$ROOT_DIR"
MARKETPLACE_NAME="stringbean-local"
PLUGIN_NAME="stringbean"
MCP_PYTHON="/usr/bin/python3"

if [[ ! -x "$MCP_PYTHON" ]]; then
  echo "Error: the trusted Codex plugin interpreter is unavailable: $MCP_PYTHON" >&2
  exit 1
fi

if ! PYTHONDONTWRITEBYTECODE=1 "$MCP_PYTHON" - "$ROOT_DIR/plugins/stringbean/runtime/src" <<'PY' >/dev/null 2>&1
import importlib
from pathlib import Path
import sys

if sys.version_info[:2] < (3, 10) or sys.version_info.releaselevel != "final":
    raise SystemExit(1)
for module_name in ("pydantic", "typer", "rich", "yaml"):
    importlib.import_module(module_name)
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from agent_relay import cli, config  # noqa: F401,E402
PY
then
  echo "Error: /usr/bin/python3 must be a final Python 3.10+ with pydantic, typer, rich, and PyYAML." >&2
  echo "Install those dependencies first; pre-approved Stringbean runs never download code automatically." >&2
  exit 1
fi

codex plugin marketplace add "$MARKETPLACE_ROOT" >/dev/null
codex plugin add "$PLUGIN_NAME@$MARKETPLACE_NAME"

printf 'Installed Codex plugin: %s@%s\n' "$PLUGIN_NAME" "$MARKETPLACE_NAME"
printf 'Restart Codex or open a new task, then invoke: $sbx <task>\n'
printf 'If Codex shows plugin-qualified skill names, choose: stringbean:sbx\n'
