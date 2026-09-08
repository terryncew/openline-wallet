"""Run tests, preserve public evidence, and independently check the result."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

from coordinator import Coordinator, transaction
from test_coordinator import Fixture, CoordinatorTests
from verify import verify

HERE = Path(__file__).resolve().parent
WALLET = HERE.parents[1]
AIRLOCK_SHA = "fb02207f3ac561368beeabf9ff168076bf828824"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    import airlock
    airlock_repo = Path(airlock.__file__).resolve().parents[2]
    sha = subprocess.check_output(["git", "-C", str(airlock_repo), "rev-parse", "HEAD"], text=True).strip()
    if sha != AIRLOCK_SHA:
        raise SystemExit("AIRLOCK_SOURCE_PIN_MISMATCH: install the pinned checkout editable")
    if subprocess.check_output(["git", "-C", str(airlock_repo), "status", "--porcelain"], text=True).strip():
        raise SystemExit("AIRLOCK_SOURCE_DIRTY")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CoordinatorTests)
    names = [t.id().split(".")[-1] for t in suite]
    with (args.output / "tests.txt").open("w") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped:
        raise SystemExit("COORDINATOR_TESTS_FAILED_OR_SKIPPED")
    with tempfile.TemporaryDirectory(prefix="coordinator-proof-") as root:
        fixture = Fixture(root)
        final = fixture.crash_reconcile()
        with transaction(fixture.c.path) as db:
            events = [json.loads(row[0]) for row in db.execute("SELECT body FROM events ORDER BY seq")]
        balances, count = fixture.balances()
        public = {"agreement": fixture.terms, "events": events, "final": final,
                  "balances": balances, "transfers": count, "initial_total": 20000}
    (args.output / "evidence.json").write_text(json.dumps(public, indent=2, sort_keys=True)+"\n")
    code = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob("*.py")}
    files = {name: hashlib.sha256((args.output / name).read_bytes()).hexdigest()
             for name in ("tests.txt", "evidence.json")}
    report = {"schema": "coordinator-001.result.v1", "verdict": "CONTROLLED_SETTLEMENT_RECONCILIATION_PASSED",
              "python": platform.python_version(), "airlock_commit": sha,
              "wallet_base": "b30e36463edd4b7ce3e56879a743745241070849",
              "tests_passed": result.testsRun, "tests": names, "source_sha256": code,
              "files_sha256": files, "simulated_money": True, "external_parties": False,
              "negative_control": "fresh retry key permits two transfers for one logical obligation",
              "claim_boundary": "Controlled separate signer processes; trusted coordinator, evaluator and provider; no OS isolation, live models, external operators or real payments."}
    (args.output / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    verify(args.output, HERE)
    print(json.dumps({"verdict": report["verdict"], "tests_passed": result.testsRun,
                      "transfers": count, "output": str(args.output)}))


if __name__ == "__main__":
    main()
