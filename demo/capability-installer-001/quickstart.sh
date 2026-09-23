#!/bin/bash
# capinstall quickstart: completes the full flow locally. Copyable.
set -e
export CAPINSTALL_HOME="${CAPINSTALL_HOME:-$HOME/.capinstall-quickstart}"
rm -rf "$CAPINSTALL_HOME"

capinstall init
PKG="$CAPINSTALL_HOME/packages/example"

capinstall inspect "$PKG" | head -20
capinstall accept "$PKG"
capinstall import "$PKG"

PH=$(python3 -c "import json,os; print(list(json.load(open(os.environ['CAPINSTALL_HOME']+'/decisions.json')))[0])")
capinstall invoke "$PH" w01_mixed_eval_majority
capinstall settle "$PH" --demo
capinstall revoke "$PH"

echo "--- invoke after revoke (expect refusal) ---"
capinstall invoke "$PH" w02_all_ok || echo "(refused as expected)"
echo "quickstart complete. state at $CAPINSTALL_HOME"
