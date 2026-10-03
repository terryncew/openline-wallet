from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ac_run", HERE / "run.py")
run = importlib.util.module_from_spec(spec); spec.loader.exec_module(run)
BUNDLE = json.loads((HERE / "export" / "bundle.json").read_text())

class AuthorityContinuityTests(unittest.TestCase):
    def test_signature_verification(self):
        r = run.Receiver(BUNDLE["public_keys"]["owner"], run.key("receiver"), BUNDLE["mandate"]["mandate_id"])
        self.assertTrue(r.verify_owner(BUNDLE["grants"][0]))

    def test_worker_binding(self):
        row = next(x for x in BUNDLE["run_log"] if x["request"].get("control") == "C4")
        self.assertEqual((row["decision"], row["reason_codes"]), ("REFUSED", ["worker_mismatch"]))
        self.assertNotEqual(row["request"]["worker_pubkey"], row["request"]["grant"]["subject_pubkey"])

    def test_job_binding(self):
        row = next(x for x in BUNDLE["run_log"] if x["request"].get("control") == "C5")
        self.assertEqual((row["decision"], row["reason_codes"]), ("REFUSED", ["mandate_mismatch"]))
        self.assertNotEqual(row["request"]["grant"]["mandate_id"], BUNDLE["mandate"]["mandate_id"])

    def test_revocation_ordering_and_replay(self):
        rev = next(x for x in BUNDLE["run_log"] if x["kind"] == "revocation")
        c1 = next(x for x in BUNDLE["run_log"] if x["request"].get("control") == "C1")
        c2 = next(x for x in BUNDLE["run_log"] if x["request"].get("control") == "C2")
        self.assertLess(rev["seq"], c1["seq"])
        self.assertEqual(c1["reason_codes"], ["grant_revoked"])
        self.assertEqual(c2["reason_codes"], ["replayed_authorization"])

    def test_successor_grant(self):
        gb = BUNDLE["grants"][1]
        self.assertEqual(gb["subject_pubkey"], BUNDLE["worker_public_keys"]["worker-b"])
        self.assertEqual(BUNDLE["ledger"][0]["grant_id"], gb["grant_id"])

    def test_append_only_history(self):
        lengths = [x["receipt_log_length"] for x in BUNDLE["run_log"]]
        self.assertEqual(lengths, sorted(lengths))
        self.assertEqual([x["receipt_id"] for x in BUNDLE["receipts"]], ["R-001", "R-002", "R-003", "R-004", "R-005", "R-006"])

    def test_receipt_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "export"; shutil.copytree(HERE / "export", target)
            bundle = json.loads((target / "bundle.json").read_text()); bundle["receipts"][2]["action"] = "phase-2"
            (target / "bundle.json").write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
            manifest = json.loads((target / "manifest.json").read_text()); manifest["bundle.json"] = run.file_hash(target / "bundle.json")
            (target / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            result = subprocess.run([sys.executable, str(HERE / "verify.py"), "--bundle", str(target), "--profile", str(run.PROFILE)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("receipt signature", result.stdout)

    def test_isolated_export_only_verification(self):
        result = subprocess.run([sys.executable, "-I", str(HERE / "verify.py"), "--bundle", str(HERE / "export"), "--profile", str(run.PROFILE)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == "__main__": unittest.main()
