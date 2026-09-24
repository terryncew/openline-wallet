import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from openline_wallet.github_effect import MergeTarget

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "github_ack_closure_live_002",
    ROOT / "scripts" / "github_ack_closure_live_002.py",
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
        return {
            "sha": sha,
            "parents": [{"sha": target.base_sha}, {"sha": target.head_sha}],
        }


def target():
    return MergeTarget(
        mod.SANDBOX,
        1360489534,
        13,
        HEAD,
        "olp-test-123-base",
        BASE,
    )


class AcknowledgedClosureTests(unittest.TestCase):
    def test_forced_post_ack_interruption_recovers_after_restart_without_retry(self):
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
            self.assertTrue(evidence["interruption"]["provider_acknowledged"])
            self.assertEqual(evidence["interruption"]["journal_status"], "UNCERTAIN")
            self.assertFalse(evidence["interruption"]["effect_receipt_present"])
            self.assertTrue(evidence["recovery"]["receiver_restarted"])
            self.assertEqual(evidence["recovery"]["effect_status"], "MERGE_CONFIRMED")
            self.assertEqual(evidence["recovery"]["closure_status"], "EFFECT_CLOSED")
            self.assertEqual(evidence["recovery"]["active_frontiers"], 0)
            self.assertEqual(evidence["recovery"]["unattributed_merge_observations"], [])
            self.assertFalse(any(p.suffix == ".key" for p in (root / "public").rglob("*")))

    def test_wallet_pin_is_exact_merged_repair(self):
        self.assertEqual(
            mod.WALLET_PIN,
            "323c11f55c9c6bcc6d618ce4c0f3f19cbbc627bd",
        )

    def test_confirmation_is_exact(self):
        self.assertEqual(mod.CONFIRM, "RUN GITHUB ACK CLOSURE")


if __name__ == "__main__":
    unittest.main()
