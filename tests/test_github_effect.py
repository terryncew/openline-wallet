"""PROVIDER-EFFECT-001: exact GitHub mutation and conservative closure."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch
import copy
import tempfile
import sqlite3
from contextlib import closing
import json
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.crypto import public_key_hex, record_hash, verify_record
from openline_wallet.effect_closure import EffectGate
from openline_wallet.errors import WalletError
from openline_wallet.github_effect import (
    GitHubClient, GitHubHTTPError, GitHubMergeReceiver, MergeTarget, merge_action,
)
from openline_wallet.receiver import create_presentation
from openline_wallet.wallet import Wallet

BASE = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
BASE_SHA = "b" * 40
MERGE_SHA = "c" * 40
TARGET = MergeTarget("example/provider-effect-sandbox", 12345, 7, HEAD, "olp-test-base", BASE_SHA)


class FakeGitHub:
    def __init__(self):
        self.target = TARGET
        self.state = "open"
        self.merged = False
        self.head = HEAD
        self.base = BASE_SHA
        self.merge_sha = None
        self.calls = []
        self.entered = Event()
        self.release = Event()
        self.hold = False
        self.fail_after_effect = False
        self.fail_before_effect = False
        self.mutation_count = 0
        self.base_at_merge = None
        self.bad_commit_parents = False

    def pr(self, target):
        self.calls.append("GET")
        return {"number": 7, "state": self.state, "merged": self.merged,
                "head": {"sha": self.head}, "base": {"ref": "olp-test-base", "sha": self.base,
                "repo": {"full_name": "example/provider-effect-sandbox", "id": 12345}},
                "merge_commit_sha": self.merge_sha}

    def commit(self, target, sha):
        self.calls.append("GET_COMMIT")
        return {"sha": sha, "parents": [{"sha": self.base},
                {"sha": "d" * 40 if self.bad_commit_parents else self.head}]}

    def merge(self, target):
        self.calls.append("PUT")
        self.entered.set()
        if self.hold and not self.release.wait(5):
            raise TimeoutError("test hold exceeded")
        if self.fail_before_effect:
            raise GitHubHTTPError(409)
        if self.merged or self.head != target.head_sha:
            raise GitHubHTTPError(409)
        if self.base_at_merge is not None:
            self.base = self.base_at_merge
        self.merged = True
        self.state = "closed"
        self.merge_sha = MERGE_SHA
        self.mutation_count += 1
        if self.fail_after_effect:
            raise WalletError("GITHUB_TRANSPORT_UNCERTAIN")
        return {"merged": True, "sha": MERGE_SHA, "message": "Pull Request successfully merged"}


class GitHubEffectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wallet = Wallet.create(self.root / "wallet", now=BASE)
        self.subject = Ed25519PrivateKey.generate()
        self.wallet.grant(subject_id="agent-a", subject_public_key=public_key_hex(self.subject),
            scopes=[TARGET.action], expires_at=BASE + timedelta(hours=1), now=BASE, mandate_id="grant-a")
        self.bundle = self.wallet.export_bundle(now=BASE + timedelta(seconds=1))
        self.gate = EffectGate("github-test")
        self.gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        self.gate.admit_bundle(self.bundle, now=self.at(1))
        self.client = FakeGitHub()
        self.receiver = GitHubMergeReceiver(self.gate, self.client, TARGET, self.root / "journal.sqlite")
        self.addCleanup(self.receiver.shutdown)

    def other_gate(self):
        gate = EffectGate("other")
        gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        return gate

    def at(self, seconds):
        return BASE + timedelta(seconds=seconds)

    def presentation(self, *, action=None, key=None, now=None):
        now = now or self.at(2)
        action = action or TARGET.action
        key = key or self.subject
        challenge = self.gate.issue_challenge(principal_id=self.wallet.principal_id,
            subject_id="agent-a", action=TARGET.action, now=now)
        return create_presentation(bundle=self.bundle, mandate_id="grant-a", subject_id="agent-a",
            subject_key=key, action=action, receiver_challenge=challenge, now=now)

    def prepared(self):
        return self.receiver.prepare(self.presentation(), now=self.at(2))["ticket"]

    def revoke(self, at=3):
        self.wallet.revoke("grant-a", now=self.at(at))
        return self.wallet.export_bundle(now=self.at(at + 1))

    def test_scope_and_target_validation(self):
        self.assertEqual(TARGET.action, merge_action(TARGET.repository, 7, HEAD, repository_id=12345, base_ref="olp-test-base"))
        self.assertNotEqual(TARGET.action, merge_action(TARGET.repository, 8, HEAD, repository_id=12345, base_ref="olp-test-base"))
        self.assertNotEqual(TARGET.action, merge_action(TARGET.repository, 7, HEAD, repository_id=999, base_ref="olp-test-base"))
        self.assertNotEqual(TARGET.action, merge_action(TARGET.repository, 7, HEAD, repository_id=12345, base_ref="olp-test-other"))
        self.assertEqual(TARGET.action, MergeTarget(TARGET.repository, 12345, 7, HEAD, "olp-test-base", "d"*40).action)
        self.assertNotEqual(TARGET.action, merge_action(TARGET.repository, 7, "d" * 40, repository_id=12345, base_ref="olp-test-base"))
        for repository in ["other", "owner/repo/extra", "owner/../repo", "owner/repo?x=1"]:
            with self.assertRaises(WalletError):
                merge_action(repository, 7, HEAD, repository_id=12345, base_ref="olp-test-base")
        with self.assertRaises(WalletError):
            merge_action(TARGET.repository, True, HEAD, repository_id=12345, base_ref="olp-test-base")
        with self.assertRaises(WalletError):
            MergeTarget(TARGET.repository, 0, 7, HEAD, "olp-test-base", BASE_SHA)

    def test_hold_revoke_close_release_stops_without_network(self):
        ticket = self.prepared()
        certificate = self.receiver.close(self.revoke(), "grant-a", now=self.at(4))
        result = self.receiver.finish(ticket, now=self.at(5))
        self.assertEqual(result["reason_codes"], ["MANDATE_REVOKED"])
        self.assertEqual(result["decision"], "STOPPED")
        self.assertFalse(result["effect_applied"])
        self.assertEqual(self.client.mutation_count, 0)
        self.assertEqual(certificate["status"], "EFFECT_CLOSED")
        self.assertEqual(certificate["pending_fenced"], 1)
        self.assertTrue(verify_record(certificate, expected_public_key=self.gate.public_key)[0])
        self.assertEqual(self.receiver.finish(ticket, now=self.at(5)), result)

    def test_normal_merge_reconciles_exact_sha_and_preserves_history(self):
        ticket = self.prepared()
        result = self.receiver.finish(ticket, now=self.at(3))
        self.assertEqual(result["decision"], "ALLOWED")
        self.assertEqual(result["effect_receipt"]["merge_commit_sha"], MERGE_SHA)
        self.assertTrue(verify_record(result["effect_receipt"], expected_public_key=self.gate.public_key)[0])
        self.assertEqual(self.client.mutation_count, 1)
        self.assertEqual(self.receiver.finish(ticket, now=self.at(4)), result)
        self.wallet.add_receipt(result["receipt"])
        certificate = self.receiver.close(self.revoke(at=5), "grant-a", now=self.at(6))
        self.assertEqual(certificate["active_frontiers"], 0)
        self.assertEqual(certificate["confirmed_effect_hashes"], [record_hash(result["effect_receipt"])])
        self.assertEqual(self.client.mutation_count, 1)

    def test_actual_merge_drains_before_closure(self):
        ticket = self.prepared()
        self.client.hold = True
        results, errors = {}, []
        def finish():
            try:
                results["effect"] = self.receiver.finish(ticket, now=self.at(3))
            except Exception as exc:
                errors.append(exc)
        worker = Thread(target=finish)
        worker.start()
        self.assertTrue(self.client.entered.wait(2))
        revoked = self.revoke(at=4)
        closed = Event()
        def close():
            try:
                results["closure"] = self.receiver.close(revoked, "grant-a", now=self.at(5))
            except Exception as exc:
                errors.append(exc)
            finally:
                closed.set()
        closer = Thread(target=close)
        closer.start()
        self.assertFalse(closed.wait(.05))
        self.client.release.set()
        worker.join(3)
        closer.join(3)
        self.assertFalse(worker.is_alive() or closer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results["effect"]["effect_receipt"]["merge_commit_sha"], MERGE_SHA)
        self.assertEqual(results["closure"]["status"], "EFFECT_CLOSED")
        self.assertEqual(self.client.mutation_count, 1)
        self.assertLessEqual(results["effect"]["effect_receipt"]["confirmed_at"], results["closure"]["closed_at"])

    def test_changed_head_or_base_never_calls_merge(self):
        for field, value in [("head", "d" * 40), ("base", "e" * 40), ("state", "closed")]:
            with self.subTest(field=field):
                setattr(self.client, field, value)
                result = self.receiver.finish(self.prepared(), now=self.at(3))
                self.assertEqual(result["decision"], "STOPPED")
                self.assertEqual(result["reason_codes"], ["GITHUB_PR_STATE_CHANGED"])
                setattr(self.client, field, {"head": HEAD, "base": BASE_SHA, "state": "open"}[field])
        self.assertEqual(self.client.mutation_count, 0)
        self.assertNotIn("PUT", self.client.calls)

    def test_wrong_repository_identity_never_calls_merge(self):
        original = self.client.pr
        def wrong(target):
            value = original(target)
            value["base"]["repo"]["id"] = 999
            return value
        self.client.pr = wrong
        result = self.receiver.finish(self.prepared(), now=self.at(3))
        self.assertEqual(result["reason_codes"], ["GITHUB_PR_IDENTITY_MISMATCH"])
        self.assertEqual(self.client.mutation_count, 0)

    def test_transport_failure_is_durable_uncertainty(self):
        ticket = self.prepared()
        self.client.fail_after_effect = True
        with self.assertRaisesRegex(WalletError, "GITHUB_TRANSPORT_UNCERTAIN"):
            self.receiver.finish(ticket, now=self.at(3))
        revoked = self.revoke()
        with self.assertRaisesRegex(WalletError, "GITHUB_EFFECT_OUTCOME_UNRESOLVED"):
            self.receiver.close(revoked, "grant-a", now=self.at(4))
        with self.assertRaisesRegex(WalletError, "GITHUB_EFFECT_OUTCOME_UNRESOLVED"):
            self.receiver.prepare(self.presentation(now=self.at(4)), now=self.at(4))
        self.assertEqual(self.client.mutation_count, 1)
        self.assertEqual(self.receiver.reconcile(now=self.at(5))[0]["status"], "OBSERVED_MERGED_UNATTRIBUTED")
        # A confirmed terminal merged PR can close the exact receiver capability,
        # but the missing API acknowledgement is never invented as our effect receipt.
        certificate = self.receiver.close(revoked, "grant-a", now=self.at(5))
        self.assertEqual(certificate["unattributed_merge_observations"].__len__(), 1)
        self.assertEqual(certificate["confirmed_effect_hashes"], [])
        with self.assertRaises(WalletError):
            self.receiver.finish(ticket, now=self.at(6))
        self.assertEqual(self.client.mutation_count, 1)

    def test_negative_reconciliation_never_clears_ambiguity(self):
        ticket = self.prepared()
        self.client.fail_before_effect = True
        with self.assertRaises(GitHubHTTPError):
            self.receiver.finish(ticket, now=self.at(3))
        self.client.fail_before_effect = False
        self.assertEqual(self.receiver.reconcile()[0]["status"], "UNCERTAIN")
        with self.assertRaisesRegex(WalletError, "GITHUB_EFFECT_OUTCOME_UNRESOLVED"):
            self.receiver.close(self.revoke(), "grant-a", now=self.at(4))
        self.assertEqual(self.client.mutation_count, 0)

    def test_restart_requires_fresh_standing_and_never_retries(self):
        ticket = self.prepared()
        self.client.fail_after_effect = True
        with self.assertRaises(WalletError):
            self.receiver.finish(ticket, now=self.at(3))
        revoked = self.revoke()
        self.receiver.shutdown()
        gate = EffectGate("github-test", gate_key=self.gate.gate_key)
        gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        reopened = GitHubMergeReceiver(gate, self.client, TARGET, self.root / "journal.sqlite")
        self.addCleanup(reopened.shutdown)
        with self.assertRaisesRegex(WalletError, "GITHUB_RECOVERY_REQUIRES_REVIEW"):
            reopened.prepare({}, now=self.at(4))
        self.assertEqual(reopened.reconcile()[0]["status"], "OBSERVED_MERGED_UNATTRIBUTED")
        certificate = reopened.close(revoked, "grant-a", now=self.at(5))
        self.assertEqual(certificate["status"], "EFFECT_CLOSED")
        self.assertEqual(self.client.mutation_count, 1)

    def test_writer_and_identity_are_exclusive(self):
        with self.assertRaisesRegex(WalletError, "RECEIVER_EFFECT_OWNER_BUSY"):
            GitHubMergeReceiver(self.gate, self.client, TARGET, self.root / "journal.sqlite")
        self.receiver.shutdown()
        with self.assertRaisesRegex(WalletError, "GITHUB_JOURNAL_BINDING_MISMATCH"):
            GitHubMergeReceiver(self.other_gate(), self.client, TARGET, self.root / "journal.sqlite")
        with self.assertRaisesRegex(WalletError, "GITHUB_JOURNAL_BINDING_MISMATCH"):
            GitHubMergeReceiver(self.gate, self.client,
                MergeTarget(TARGET.repository, TARGET.repository_id, 8, HEAD, "olp-test-base", BASE_SHA),
                self.root / "journal.sqlite")

    def test_wrong_subject_signature_and_scope(self):
        bad = self.receiver.prepare(self.presentation(key=Ed25519PrivateKey.generate()), now=self.at(2))
        self.assertEqual(bad["decision"], "STOPPED")
        self.assertFalse(bad["effect_applied"])
        bad = self.receiver.prepare(self.presentation(action="github:merge:wrong"), now=self.at(2))
        self.assertEqual(bad["decision"], "STOPPED")
        self.assertEqual(self.client.mutation_count, 0)


    def test_base_drift_is_explicitly_reported(self):
        self.client.base_at_merge = "d" * 40
        result = self.receiver.finish(self.prepared(), now=self.at(3))
        self.assertTrue(result["effect_receipt"]["merge_commit"]["base_drift"])
        self.assertEqual(result["effect_receipt"]["merge_commit"]["parents"][0], "d" * 40)
        self.assertEqual(result["effect_receipt"]["action"], TARGET.action)
        self.assertEqual(self.client.mutation_count, 1)
        self.assertEqual(self.receiver.close(self.revoke(), "grant-a", now=self.at(4))["status"], "EFFECT_CLOSED")

    def test_second_ticket_cannot_repeat_a_consumed_target(self):
        first, second = self.prepared(), self.prepared()
        self.receiver.finish(first, now=self.at(3))
        stopped = self.receiver.finish(second, now=self.at(4))
        self.assertEqual(stopped["reason_codes"], ["GITHUB_TARGET_ALREADY_CONSUMED"])
        with self.assertRaisesRegex(WalletError, "GITHUB_TARGET_ALREADY_CONSUMED"):
            self.receiver.prepare(self.presentation(), now=self.at(4))
        self.assertEqual(self.client.mutation_count, 1)
        closure = self.receiver.close(self.revoke(at=5), "grant-a", now=self.at(6))
        self.assertEqual(len(closure["confirmed_effect_hashes"]), 1)

    def test_recovered_prepared_ticket_is_durably_fenced(self):
        self.prepared()
        revoked = self.revoke()
        self.receiver.shutdown()
        gate = EffectGate("github-test", gate_key=self.gate.gate_key)
        gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        gate.admit_bundle(revoked, now=self.at(4))
        reopened = GitHubMergeReceiver(gate, self.client, TARGET, self.root / "journal.sqlite")
        self.addCleanup(reopened.shutdown)
        with self.assertRaisesRegex(WalletError, "GITHUB_RECOVERY_REQUIRES_REVIEW"):
            reopened.prepare({}, now=self.at(4))
        self.assertEqual(reopened.reconcile(now=self.at(5))[0]["decision"], "STOPPED")
        self.assertEqual(reopened.close(revoked, "grant-a", now=self.at(6))["status"], "EFFECT_CLOSED")
        self.assertEqual(self.client.mutation_count, 0)

    def test_tampered_durable_intent_is_not_trusted(self):
        self.prepared()
        self.receiver.shutdown()
        with closing(sqlite3.connect(self.root / "journal.sqlite")) as db:
            rowid, raw = db.execute("SELECT id, data FROM attempts").fetchone()
            record = json.loads(raw)
            record["status"] = "CONFIRMED"
            db.execute("UPDATE attempts SET data=? WHERE id=?", (json.dumps(record), rowid))
            db.commit()
        gate = EffectGate("github-test", gate_key=self.gate.gate_key)
        gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        with self.assertRaisesRegex(WalletError, "GITHUB_JOURNAL_INVALID"):
            GitHubMergeReceiver(gate, self.client, TARGET, self.root / "journal.sqlite")
        self.assertEqual(self.client.mutation_count, 0)

    def test_closure_remains_sealed_after_restart(self):
        self.prepared()
        revoked = self.revoke()
        certificate = self.receiver.close(revoked, "grant-a", now=self.at(4))
        self.receiver.shutdown()
        gate = EffectGate("github-test", gate_key=self.gate.gate_key)
        gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        gate.admit_bundle(revoked, now=self.at(4))
        reopened = GitHubMergeReceiver(gate, self.client, TARGET, self.root / "journal.sqlite")
        self.addCleanup(reopened.shutdown)
        self.assertEqual(reopened.close(revoked, "grant-a", now=self.at(5)), certificate)
        with self.assertRaises(WalletError):
            reopened.prepare({}, now=self.at(5))
        self.assertEqual(self.client.mutation_count, 0)

    def test_ambiguous_attempt_cannot_be_swept_by_other_ticket(self):
        first, second = self.prepared(), self.prepared()
        self.client.fail_before_effect = True
        with self.assertRaises(GitHubHTTPError):
            self.receiver.finish(first, now=self.at(3))
        self.client.fail_before_effect = False
        stopped = self.receiver.finish(second, now=self.at(4))
        self.assertEqual(stopped["reason_codes"], ["GITHUB_PRIOR_ATTEMPT_UNRESOLVED"])
        revoked = self.revoke(at=5)
        with self.assertRaisesRegex(WalletError, "GITHUB_EFFECT_OUTCOME_UNRESOLVED"):
            self.receiver.close(revoked, "grant-a", now=self.at(6))
        self.assertEqual(self.receiver.reconcile()[0]["status"], "UNCERTAIN")
        self.assertEqual(self.client.mutation_count, 0)
        self.assertEqual(self.client.calls.count("PUT"), 1)

    def test_wrong_merge_commit_parent_fails_closed(self):
        self.client.bad_commit_parents = True
        with self.assertRaisesRegex(WalletError, "GITHUB_MERGE_COMMIT_INVALID"):
            self.receiver.finish(self.prepared(), now=self.at(3))
        self.assertEqual(self.client.mutation_count, 1)
        self.assertEqual(self.receiver.reconcile()[0]["status"], "UNCERTAIN")
        with self.assertRaisesRegex(WalletError, "GITHUB_EFFECT_OUTCOME_UNRESOLVED"):
            self.receiver.close(self.revoke(), "grant-a", now=self.at(4))

    def test_http_client_is_fixed_host_and_rejects_redirects(self):
        client = GitHubClient("disposable-test-token")
        with self.assertRaisesRegex(WalletError, "GITHUB_API_ROOT_INVALID"):
            GitHubClient("token", api_root="https://evil.example")
        with self.assertRaisesRegex(WalletError, "GITHUB_PATH_INVALID"):
            client.request("GET", "/repos/a/b/pulls/1/../../secrets")
        with self.assertRaisesRegex(WalletError, "GITHUB_TIMEOUT_INVALID"):
            GitHubClient("token", timeout=0)
        self.assertEqual(client.timeout, 20.0)


if __name__ == "__main__":
    unittest.main()
