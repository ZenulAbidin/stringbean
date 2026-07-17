#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPT_DIR="${CODEX_HOME:-$HOME/.codex}/prompts"

"$ROOT_DIR/scripts/install-codex-plugin.sh"

mkdir -p "$PROMPT_DIR"
install -m 644 "$ROOT_DIR/codex-prompts/sbx.md" "$PROMPT_DIR/sbx.md"

printf 'Installed Codex prompt: %s\n' "$PROMPT_DIR/sbx.md"
printf 'Restart Codex or open a new task, then run: /prompts:sbx <task>\n'
