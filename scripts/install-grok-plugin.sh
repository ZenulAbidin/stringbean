#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$ROOT_DIR/plugins/grok-stringbean"

grok plugin validate "$PLUGIN_DIR" >/dev/null
grok plugin install --trust "$PLUGIN_DIR"

printf 'Installed Grok Build plugin: grok-stringbean\n'
printf 'Restart Grok Build or open a new task, then invoke: /sbx <task>\n'
printf 'If Grok shows plugin-qualified skill names, choose: grok-stringbean:sbx\n'
