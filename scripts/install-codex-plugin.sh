#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MARKETPLACE_ROOT="$ROOT_DIR"
MARKETPLACE_NAME="stringbean-local"
PLUGIN_NAME="stringbean"

codex plugin marketplace add "$MARKETPLACE_ROOT" >/dev/null
codex plugin add "$PLUGIN_NAME@$MARKETPLACE_NAME"

printf 'Installed Codex plugin: %s@%s\n' "$PLUGIN_NAME" "$MARKETPLACE_NAME"
printf 'Restart Codex or open a new task, then invoke: $sbx <task>\n'
printf 'If Codex shows plugin-qualified skill names, choose: stringbean:sbx\n'
