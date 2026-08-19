#!/usr/bin/env bash
# Install bd-tui as an isolated CLI. Usage:
#   curl -fsSL https://raw.githubusercontent.com/gevou/bd-tui/main/install.sh | bash
set -euo pipefail

REF="${BD_TUI_REF:-main}"
SPEC="git+https://github.com/gevou/bd-tui.git@${REF}"

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
echo "bd-tui installed. Make sure the 'bd' (beads) CLI is on your PATH, then run:"
echo "    bd-tui"
