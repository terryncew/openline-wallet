"""Reappraise frozen receipts against their exact original source bindings.

The current CI has changed, so its bytes cannot satisfy an earlier source hash.
This isolates the predecessor workflow and checks every listed source hash before
running the original unmodified verifiers. No experiment is rerun or receipt edited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
BASE = "ec385ce2c0cdc253d5e634d1963f254203205e30"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run() -> dict:
    frozen = ROOT / "proofs/wallet-closure-set-001"
    result = json.loads((frozen / "result.json").read_text())
    source = result["source_sha256"]
    predecessor = ROOT / "proofs/provider-effect-001/baseline-ci.yml"
    ci = ".github/workflows/ci.yml"
    if sha(predecessor) != source[ci]:
        raise RuntimeError("PINNED_PREDECESSOR_CI_MISMATCH")
    # Every non-workflow source must still be byte-identical to the frozen proof.
    for name, digest in source.items():
        if name != ci and sha(ROOT / name) != digest:
            raise RuntimeError("FROZEN_SOURCE_MISMATCH: " + name)
    with tempfile.TemporaryDirectory() as temp:
        shadow = Path(temp)
        shutil.copytree(ROOT / "src/openline_wallet", shadow / "src/openline_wallet",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in source:
            target = shadow / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(predecessor if name == ci else ROOT / name, target)
        for name, digest in source.items():
            if sha(shadow / name) != digest:
                raise RuntimeError("FROZEN_SHADOW_MISMATCH: " + name)
        env = {**os.environ, "PYTHONPATH": str(shadow / "src"),
               "ARTIFACT_TOOL_DISABLE_WARMUP": "1"}
        checks = {}
        for label, script, directory, extra in [
            ("WALLET-EFFECT-CLOSURE-001", ROOT / "proofs/wallet-effect-closure-001/verify.py",
             ROOT / "proofs/wallet-effect-closure-001", []),
            ("WALLET-CLOSURE-SET-001", frozen / "verify.py", frozen,
             ["--source-root", str(shadow)]),
        ]:
            response = subprocess.run([sys.executable, str(script), str(directory), *extra],
                                      cwd=shadow, env=env, capture_output=True, text=True, timeout=120)
            if response.returncode:
                raise RuntimeError(label + ": " + (response.stdout + response.stderr)[-1000:])
            checks[label] = json.loads(response.stdout)
        return {"base_commit": BASE, "predecessor_ci_sha256": sha(predecessor),
                "source_files_verified": len(source), "results": checks,
                "status": "FROZEN_HISTORICAL_EVIDENCE_VERIFIED"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
