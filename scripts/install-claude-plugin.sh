#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MARKETPLACE_ROOT="$ROOT_DIR"
MARKETPLACE_NAME="stringbean-local"
PLUGIN_NAME="claude-stringbean"

claude plugin validate "$ROOT_DIR/plugins/claude-stringbean" >/dev/null
claude plugin marketplace add "$MARKETPLACE_ROOT" >/dev/null

if claude plugin list | grep -q "claude-stringbean@stringbean-local"; then
  claude plugin uninstall "$PLUGIN_NAME@$MARKETPLACE_NAME" --scope user -y >/dev/null
fi

claude plugin install "$PLUGIN_NAME@$MARKETPLACE_NAME" --scope user

printf 'Installed Claude Code plugin: %s@%s\n' "$PLUGIN_NAME" "$MARKETPLACE_NAME"
printf 'Restart Claude Code or open a new task, then invoke: /sbx <task>\n'
printf 'If Claude shows plugin-qualified skill names, choose: claude-stringbean:sbx\n'
