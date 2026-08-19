#!/usr/bin/env bash
# Install bd-tui as an isolated CLI (via uv, falling back to pipx).
#
# Usage:
#   ./install.sh [--bin-dir DIR] [--ref GIT_REF]
#   curl -fsSL https://raw.githubusercontent.com/gevou/bd-tui/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --bin-dir ~/bin
#   curl -fsSL .../install.sh | BD_TUI_BIN_DIR=~/bin bash
#
# Options:
#   --bin-dir DIR   Directory to place the `bd-tui` executable in
#                   (default: uv/pipx's default, usually ~/.local/bin).
#                   Also settable via BD_TUI_BIN_DIR.
#   --ref REF       Git ref (branch/tag/sha) to install (default: main,
#                   or BD_TUI_REF).
set -euo pipefail

REF="${BD_TUI_REF:-main}"
BIN_DIR="${BD_TUI_BIN_DIR:-}"

usage() { awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bin-dir) BIN_DIR="${2:?--bin-dir needs a directory}"; shift 2 ;;
        --ref)     REF="${2:?--ref needs a value}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

SPEC="git+https://github.com/gevou/bd-tui.git@${REF}"

# Point both backends' executable dir at the chosen folder, if any.
if [[ -n "$BIN_DIR" ]]; then
    mkdir -p "$BIN_DIR"
    export UV_TOOL_BIN_DIR="$BIN_DIR"   # honored by `uv tool install`
    export PIPX_BIN_DIR="$BIN_DIR"      # honored by `pipx install`
    echo "Installing bd-tui into: $BIN_DIR"
fi

if command -v uv >/dev/null 2>&1; then
    echo "Installing bd-tui with uv…"
    uv tool install --force "$SPEC"
elif command -v pipx >/dev/null 2>&1; then
    echo "Installing bd-tui with pipx…"
    pipx install --force "$SPEC"
else
    cat >&2 <<'EOF'
bd-tui needs either uv or pipx to install into an isolated environment.

  uv:   curl -LsSf https://astral.sh/uv/install.sh | sh
  pipx: python3 -m pip install --user pipx && python3 -m pipx ensurepath

Then re-run this installer.
EOF
    exit 1
fi

echo
echo "bd-tui installed. Ensure the 'bd' (beads) CLI is on your PATH."
if [[ -n "$BIN_DIR" ]]; then
    echo "Make sure '$BIN_DIR' is on your PATH, then run: bd-tui"
else
    echo "Run: bd-tui"
fi
