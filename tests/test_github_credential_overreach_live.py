import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from openline_wallet.github_effect import MergeTarget

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "github_credential_overreach_live",
    ROOT / "scripts" / "github_credential_overreach_live.py",
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

BASE = "a" * 40
HEAD = "b" * 40
MERGE = "c" * 40


class FakeClient:
    def __init__(self):
        self.merged = False
        self.merge_calls = 0

    def pr(self, target):
        return {
            "number": target.number,
            "state": "closed" if self.merged else "open",
            "merged": self.merged,
            "merge_commit_sha": MERGE if self.merged else None,
            "head": {"sha": target.head_sha},
            "base": {
                "ref": target.base_ref,
                "sha": target.base_sha,
                "repo": {
                    "full_name": target.repository,
                    "id": target.repository_id,
                },
            },
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
        }

    def merge(self, target):
        self.merge_calls += 1
        self.merged = True
        return {"merged": True, "sha": MERGE}

    def commit(self, target, sha):
        self.assert_sha = sha
        return {
            "sha": sha,
            "parents": [{"sha": target.base_sha}, {"sha": target.head_sha}],
        }


def target():
    return MergeTarget(
        mod.SANDBOX,
        1360489534,
        7,
        HEAD,
        "olp-test-123-base",
        BASE,
    )


class BoundaryTests(unittest.TestCase):
    def test_valid_wrong_scope_stops_then_exact_successor_merges_once(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            evidence = mod.run_boundary(
                client,
                target(),
                root / "private",
                root / "public",
                transport="fixture",
            )
            self.assertEqual(evidence["verdict"], mod.PASS)
            self.assertEqual(client.merge_calls, 1)
            self.assertEqual(
                evidence["control"]["reason_codes"],
                ["ACTION_OUTSIDE_MANDATE"],
            )
            self.assertEqual(evidence["control"]["merge_requests_after_worker_a"], 0)
            self.assertFalse(evidence["control"]["provider_merged_after_worker_a"])
            self.assertTrue(evidence["worker_a_revoked"])
            self.assertTrue(evidence["worker_b_revoked"])
            self.assertEqual(evidence["closure"]["status"], "EFFECT_CLOSED")
            self.assertEqual(evidence["closure"]["active_frontiers"], 0)
            self.assertEqual(evidence["successor"]["effect_status"], "MERGE_CONFIRMED")
            self.assertFalse(any(p.suffix == ".key" for p in (root / "public").rglob("*")))

    def test_tampered_control_receipt_fails_verification(self):
        client = FakeClient()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "public"
            evidence = mod.run_boundary(
                client, target(), root / "private", out, transport="fixture"
            )
            plan = {
                "repository": mod.SANDBOX,
                "repository_id": 1360489534,
                "number": 7,
                "base_ref": "olp-test-123-base",
                "base_sha": BASE,
                "head_ref": "olp-test-123-head",
                "head_sha": HEAD,
            }
            mod.write_result(out, evidence, plan, mode="controlled")
            receipt = json.loads((out / "worker-a-stop-receipt.json").read_text())
            receipt["reason_codes"] = ["NOT_THE_FROZEN_REASON"]
            (out / "worker-a-stop-receipt.json").write_text(
                json.dumps(receipt, indent=2, sort_keys=True) + "\n"
            )
            with self.assertRaises(mod.ExperimentError):
                mod.verify(out)

    def test_wallet_pin_is_frozen(self):
        self.assertEqual(
            mod.WALLET_PIN,
            "ebe6b2882095aba64611f5802ad5fdcf8e65961a",
        )

    def test_confirmation_string_is_exact(self):
        self.assertEqual(mod.CONFIRM, "RUN GITHUB CREDENTIAL OVERREACH")


if __name__ == "__main__":
    unittest.main()
