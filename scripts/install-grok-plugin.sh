#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PLUGIN_DIR="$ROOT_DIR/plugins/grok-stringbean"

grok plugin validate "$PLUGIN_DIR" >/dev/null

INSTALLED_NAME="$(
  grok plugin list \
    | awk -v path="$PLUGIN_DIR" '
      index($0, path) {
        print $2
        exit
      }
    '
)"

if [[ -n "$INSTALLED_NAME" ]]; then
  grok plugin uninstall "$INSTALLED_NAME" --confirm >/dev/null
fi

grok plugin install --trust "$PLUGIN_DIR"

printf 'Installed Grok Build plugin: grok-stringbean\n'
printf 'Restart Grok Build or open a new task, then invoke: /sbx <task>\n'
printf 'If Grok shows plugin-qualified skill names, choose: grok-stringbean:sbx\n'
