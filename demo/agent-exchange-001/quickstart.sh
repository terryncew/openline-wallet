#!/usr/bin/env bash
# OpenLine Exchange Preview — quickstart.
#
# Runs the full kernel loop end to end, then renders the developer-preview UI.
# Everything is local and simulated: controlled agents, SIM_USD (simulated).
# This is NOT a functioning market.
#
# Usage: bash quickstart.sh [--home PATH]
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$DEMO_DIR/../.." && pwd)"
HOME_DIR="${1:-$DEMO_DIR/.exchange-home}"

# The venv used for repo work carries the crypto dependency. Fall back to
# system python3 if it is importable there.
if "$HOME/workspace/.venvs/bac/bin/python" -c "import cryptography" 2>/dev/null; then
  PY="$HOME/workspace/.venvs/bac/bin/python"
else
  PY="python3"
fi

export PYTHONPATH="$DEMO_DIR:$REPO_ROOT/src"

echo "== OpenLine Exchange Preview =="
echo "== LOCAL DEVELOPER PREVIEW: controlled agents, SIM_USD simulated funds =="
echo

"$PY" "$DEMO_DIR/run_demo.py" --home "$HOME_DIR"

echo
echo "== rendering the developer-preview UI =="
"$PY" "$DEMO_DIR/ui/dashboard.py" "$HOME_DIR/snapshots/demo.json" \
  -o "$DEMO_DIR/ui/index.html"
echo "UI: $DEMO_DIR/ui/index.html"
echo
echo "Done. Nothing here is a market: no real counterparties, no real money,"
echo "no demand, no reputation, no liquidity. See README.md."
