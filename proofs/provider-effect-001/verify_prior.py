"""Reappraise frozen receipts using their recorded, byte-exact source packet.

The old experiment's base commit is not a source-tree identity claim. Its
recorded source hashes bind the staged implementation used for the experiment.
Keep Git provenance separate from cryptographic evidence consistency.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BASE = "ec385ce2c0cdc253d5e634d1963f254203205e30"
ARCHIVE = ROOT / "proofs/provider-effect-001/recorded-source-snapshot.zip"
ARCHIVE_SHA256 = "b2ae6df5e837e8890df0bfa395291142b74ce4b3326272d629bdb15d02d22f8c"
RESULT_SHA256 = "7550c3fddd52d2e9bc4fa4f078cc68d437600e3ed22938df6a3c7a4df97a9449"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


def run():
    frozen = ROOT / "proofs/wallet-closure-set-001"
    require(sha((frozen / "result.json").read_bytes()) == RESULT_SHA256,
            "FROZEN_RESULT_CHANGED")
    result = json.loads((frozen / "result.json").read_text())
    source = result["source_sha256"]
    require(result["base_commit"] == "a8633858a04d33c0e2930214d7c93105f67a3398",
            "FROZEN_BASE_MISMATCH")
    require(sha(ARCHIVE.read_bytes()) == ARCHIVE_SHA256, "SOURCE_ARCHIVE_CHANGED")
    with zipfile.ZipFile(ARCHIVE) as archive, tempfile.TemporaryDirectory() as temp:
        shadow = Path(temp)
        names = set(archive.namelist())
        require(all((not Path(n).is_absolute() and ".." not in Path(n).parts
                    and (n.endswith(".py") or n == ".github/workflows/ci.yml"))
                    for n in names), "SOURCE_ARCHIVE_PATH")
        require(set(source) <= names, "SOURCE_ARCHIVE_INCOMPLETE")
        for name in names:
            require(not name.startswith("/") and ".." not in Path(name).parts,
                    "SOURCE_ARCHIVE_PATH")
            target = shadow / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
        for name, digest in source.items():
            require(sha((shadow / name).read_bytes()) == digest,
                    "FROZEN_SOURCE_MISMATCH: " + name)
        env = {**os.environ, "PYTHONPATH": str(shadow / "src"), "PYTHONNOUSERSITE": "1"}
        checks = {}
        for label, script, directory, extra in [
            ("WALLET-EFFECT-CLOSURE-001", ROOT / "proofs/wallet-effect-closure-001/verify.py",
             ROOT / "proofs/wallet-effect-closure-001", []),
            ("WALLET-CLOSURE-SET-001", shadow / "proofs/wallet-closure-set-001/verify.py",
             frozen, ["--source-root", str(shadow)]),
        ]:
            response = subprocess.run(
                [sys.executable, "-P", str(script), str(directory), *extra],
                cwd=shadow, env=env, capture_output=True, text=True, timeout=120)
            require(response.returncode == 0,
                    label + ": " + (response.stdout + response.stderr)[-2000:])
            checks[label] = json.loads(response.stdout)
        return {
            "base_commit": BASE,
            "status": "FROZEN_EVIDENCE_CONSISTENT",
            "source_files_verified": len(source),
            "source_archive_sha256": ARCHIVE_SHA256,
            "source_provenance": "RECORDED_SOURCE_SNAPSHOT",
            "git_tree_source_identity": "UNRESOLVED",
            "claim_scope": "SELF_ATTESTED_HISTORICAL_EVIDENCE_ONLY",
            "results": checks,
        }


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
