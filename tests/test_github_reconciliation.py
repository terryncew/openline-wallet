"""Real GitHub run 34163238256: terminal PR visible before merge SHA.

Every scenario uses the existing receiver, authentic Wallet grants and
signatures, and a fake provider. No test changes the frozen receipts.
"""
import unittest
from unittest.mock import patch
from openline_wallet.errors import WalletError
from openline_wallet.github_effect import _settled_merge
import test_github_effect as fixture
TARGET, MERGE_SHA = fixture.TARGET, fixture.MERGE_SHA

class DelayedMergeReadTests(unittest.TestCase):
    setUp = fixture.GitHubEffectTests.setUp
    at = fixture.GitHubEffectTests.at
    presentation = fixture.GitHubEffectTests.presentation
    prepared = fixture.GitHubEffectTests.prepared
    revoke = fixture.GitHubEffectTests.revoke
    def test_successful_merge_waits_for_terminal_sha(self):
        ticket = self.prepared()
        original = self.client.pr
        reads = [0]
        def delayed(target):
            value = original(target)
            if value["merged"] and reads[0] < 3:
                reads[0] += 1
                value["merge_commit_sha"] = None
            return value
        self.client.pr = delayed
        result = self.receiver.finish(ticket, now=self.at(3))
        self.assertEqual(result["effect_receipt"]["merge_commit_sha"], MERGE_SHA)
        self.assertEqual(reads[0], 3)
        self.assertEqual(self.client.mutation_count, 1)
        closure = self.receiver.close(self.revoke(), "grant-a", now=self.at(4))
        self.assertEqual(closure["status"], "EFFECT_CLOSED")
        self.assertEqual(self.client.mutation_count, 1)

    def test_permanent_null_keeps_durable_uncertainty(self):
        ticket = self.prepared()
        original = self.client.pr
        def missing(target):
            value = original(target)
            if value["merged"]:
                value["merge_commit_sha"] = None
            return value
        self.client.pr = missing
        with patch("openline_wallet.github_effect.monotonic", side_effect=[0, 0, 0, 0]):
            with self.assertRaisesRegex(WalletError, "GITHUB_MERGE_NOT_RECONCILED"):
                _settled_merge(self.client, TARGET, expected_sha=MERGE_SHA, wait_seconds=0)
        # The actual receiver must preserve a dispatched, uncertain attempt.
        with patch("openline_wallet.github_effect._settled_merge",
                   side_effect=WalletError("GITHUB_MERGE_NOT_RECONCILED")):
            with self.assertRaisesRegex(WalletError, "GITHUB_MERGE_NOT_RECONCILED"):
                self.receiver.finish(ticket, now=self.at(3))
        self.assertEqual(self.client.mutation_count, 1)
        with self.assertRaisesRegex(WalletError, "GITHUB_EFFECT_OUTCOME_UNRESOLVED"):
            self.receiver.close(self.revoke(), "grant-a", now=self.at(4))

    def test_late_read_only_recovery_never_reissues_merge(self):
        ticket = self.prepared()
        original = self.client.pr
        self.client.pr = lambda target: dict(original(target), merge_commit_sha=None)
        with patch("openline_wallet.github_effect._settled_merge",
                   side_effect=WalletError("GITHUB_MERGE_NOT_RECONCILED")):
            with self.assertRaises(WalletError):
                self.receiver.finish(ticket, now=self.at(3))
        self.assertEqual(self.client.mutation_count, 1)
        self.client.pr = original
        result = self.receiver.reconcile(now=self.at(5))
        self.assertEqual(result[0]["status"], "OBSERVED_MERGED_UNATTRIBUTED")
        self.assertEqual(self.client.mutation_count, 1)
        certificate = self.receiver.close(self.revoke(), "grant-a", now=self.at(6))
        self.assertEqual(certificate["status"], "EFFECT_CLOSED")
        self.assertEqual(certificate["confirmed_effect_hashes"], [])
        self.assertEqual(len(certificate["unattributed_merge_observations"]), 1)

    def test_wrong_merge_sha_cannot_be_attributed(self):
        ticket = self.prepared()
        original = self.client.pr
        def wrong(target):
            value = original(target)
            if value["merged"]:
                value["merge_commit_sha"] = "d" * 40
            return value
        self.client.pr = wrong
        with self.assertRaisesRegex(WalletError, "GITHUB_MERGE_NOT_RECONCILED"):
            self.receiver.finish(ticket, now=self.at(3))
        self.assertEqual(self.client.mutation_count, 1)
        self.assertEqual(self.receiver.reconcile(now=self.at(4))[0]["status"], "UNCERTAIN")
        with self.assertRaises(WalletError):
            self.receiver.close(self.revoke(), "grant-a", now=self.at(5))

    def test_wrong_parents_remain_uncertain(self):
        ticket = self.prepared()
        self.client.bad_commit_parents = True
        with self.assertRaisesRegex(WalletError, "GITHUB_MERGE_COMMIT_INVALID"):
            self.receiver.finish(ticket, now=self.at(3))
        self.assertEqual(self.client.mutation_count, 1)
        self.assertEqual(self.receiver.reconcile(now=self.at(4))[0]["status"], "UNCERTAIN")
        with self.assertRaises(WalletError):
            self.receiver.close(self.revoke(), "grant-a", now=self.at(5))

    def test_recorded_live_null_sha_then_terminal_commit(self):
        import json
        import zipfile
        from pathlib import Path
        from openline_wallet.github_effect import MergeTarget
        archive = Path(__file__).resolve().parents[1] / "proofs/provider-effect-live-001/attempt-7-original.zip"
        with zipfile.ZipFile(archive) as z:
            record = json.loads(z.read("provider/target.json"))
            response = json.loads(z.read("provider/merge_response.json"))
            observed = json.loads(z.read("provider/after_b.json"))
        target = MergeTarget(**{k:record[k] for k in
            ("repository","repository_id","number","head_sha","base_ref","base_sha")})
        merge_sha = response["sha"]
        assert observed["merged"] is True and observed["merge_commit_sha"] is None
        parents = [target.base_sha,target.head_sha]
        class RecordedReads:
            def __init__(self):
                self.reads = 0
                self.mutations = 0
            def pr(self,t):
                self.reads += 1
                sha = None if self.reads == 1 else merge_sha
                return {"number":t.number,"state":"closed","merged":True,
                    "head":{"sha":t.head_sha},
                    "base":{"ref":t.base_ref,"sha":t.base_sha,
                            "repo":{"full_name":t.repository,"id":t.repository_id}},
                    "merge_commit_sha":sha}
            def commit(self,t,sha):
                return {"sha":sha,"parents":[{"sha":p} for p in parents]}
            def merge(self,t):
                self.mutations += 1
                raise AssertionError("No mutation is permitted")
        client=RecordedReads()
        after,commit=_settled_merge(client,target,expected_sha=merge_sha)
        self.assertEqual(after["merge_commit_sha"],merge_sha)
        self.assertEqual(commit["parents"],parents)
        self.assertEqual(client.reads,2)
        self.assertEqual(client.mutations,0)

    def test_unmerged_or_closed_negative_state_is_not_success(self):
        for state, merged in [("closed",False),("open",False)]:
            with self.subTest(state=state):
                self.client.state=state
                self.client.merged=merged
                with self.assertRaisesRegex(WalletError, "GITHUB_MERGE_NOT_RECONCILED"):
                    _settled_merge(self.client,TARGET,expected_sha=MERGE_SHA,wait_seconds=0)
                self.assertEqual(self.client.mutation_count,0)
