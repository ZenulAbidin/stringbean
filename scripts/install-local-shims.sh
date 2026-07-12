#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHIM_DIR="${HOME}/.local/bin"
ALT_SHIM_DIR="${HOME}/bin"
REPO_ROOT="$ROOT_DIR"
OLD_SHIM_NAME="agent""-relay"

mkdir -p "$SHIM_DIR"
if [[ ! -w "$SHIM_DIR" ]]; then
  mkdir -p "$ALT_SHIM_DIR"
  if [[ -w "$ALT_SHIM_DIR" ]]; then
    echo "Using fallback shim directory: $ALT_SHIM_DIR"
    SHIM_DIR="$ALT_SHIM_DIR"
  else
    echo "Error: cannot write to $HOME/.local/bin or $ALT_SHIM_DIR."
    echo "Add repo scripts to PATH instead:"
    echo "  export PATH=\"$HOME/Documents/stringbean/scripts:\$PATH\""
    exit 1
  fi
fi

cat > "$SHIM_DIR/stringbean" <<EOF
#!/usr/bin/env bash
exec "$REPO_ROOT/scripts/stringbean" "\$@"
EOF

rm -f "$SHIM_DIR/$OLD_SHIM_NAME"

cat > "$SHIM_DIR/sbx" <<EOF
#!/usr/bin/env bash
exec "$REPO_ROOT/scripts/sbx" "\$@"
EOF

chmod +x "$SHIM_DIR/stringbean" "$SHIM_DIR/sbx"

printf 'Installed shims to %s:\n' "$SHIM_DIR"
printf '  %s\n' "$SHIM_DIR/stringbean" "$SHIM_DIR/sbx"
