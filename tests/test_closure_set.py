from __future__ import annotations

from datetime import datetime, timedelta, timezone
import copy
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.closure_set import (
    attest_closure, create_closure_request, create_membership,
    evaluate_closure_set, sign_set_report, verify_closure_request,
    verify_membership, verify_set_report,
)
from openline_wallet.crypto import public_key_hex, record_hash, sign_record
from openline_wallet.effect_closure import EffectClosure, EffectGate
from openline_wallet.errors import WalletError
from openline_wallet.receiver import create_presentation
from openline_wallet.wallet import Wallet

BASE = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def at(seconds):
    return BASE + timedelta(seconds=seconds)


def resign(record, key):
    body = {k: copy.deepcopy(v) for k, v in record.items() if k not in {"payload_hash", "signature"}}
    return sign_record(body, key)


class ClosureSetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wallet = Wallet.create(self.root / "wallet", now=BASE)
        self.subject = Ed25519PrivateKey.generate()
        self.wallet.grant(subject_id="agent-a", subject_public_key=public_key_hex(self.subject),
                          scopes=["deploy:staging"], expires_at=at(3600), now=BASE,
                          mandate_id="grant-a")
        self.grant = self.wallet.export_bundle(now=at(1))
        self.gates = []
        self.effects = []
        for i in range(3):
            gate = EffectGate(f"receiver-{i}")
            gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
            gate.admit_bundle(self.grant, now=at(1))
            effects = EffectClosure(gate, self.root / f"r{i}" / "effects.json",
                                    self.root / f"r{i}" / "receipts")
            self.gates.append(gate)
            self.effects.append(effects)
            self.addCleanup(effects.shutdown)
        self.manifest = create_membership(self.wallet.root_key, "grant-a", [
            {"gate_id": g.gate_id, "gate_public_key": g.public_key} for g in self.gates], now=at(2))
        self.wallet.revoke("grant-a", now=at(3))
        self.revoked = self.wallet.export_bundle(now=at(4))
        self.request = create_closure_request(self.wallet.root_key, self.manifest,
                                              self.revoked, now=at(4), nonce="request-one")

    def witnesses(self):
        return [attest_closure(g, e, self.manifest, self.request, self.revoked, now=at(5))
                for g, e in zip(self.gates, self.effects)]

    def test_all_members_are_required_and_original_receipts_verify(self):
        witnesses = self.witnesses()
        partial = evaluate_closure_set(self.manifest, self.request, self.revoked, witnesses[:2], now=at(6))
        self.assertEqual(partial["status"], "CLOSURE_INCOMPLETE")
        self.assertEqual(partial["missing"], ["receiver-2"])
        self.assertEqual(partial["verified_receivers"], 2)
        result = evaluate_closure_set(self.manifest, self.request, self.revoked, witnesses, now=at(6))
        self.assertEqual(result["status"], "EFFECT_CLOSED")
        self.assertEqual(result["verified_receivers"], 3)
        self.assertTrue(all(w["local_closure"]["status"] == "EFFECT_CLOSED" for w in witnesses))
        auditor = Ed25519PrivateKey.generate()
        report = sign_set_report(result, auditor)
        self.assertEqual(verify_set_report(report, public_key_hex(auditor), self.manifest,
                                           self.request, self.revoked, witnesses, now=at(6)), result)

    def test_prepared_action_is_fenced_by_real_receiver(self):
        gate, effects = self.gates[0], self.effects[0]
        challenge = gate.issue_challenge(principal_id=self.wallet.principal_id,
                                         subject_id="agent-a", action="deploy:staging", now=at(2))
        presentation = create_presentation(bundle=self.grant, mandate_id="grant-a",
            subject_id="agent-a", subject_key=self.subject, action="deploy:staging",
            receiver_challenge=challenge, now=at(2))
        ticket = effects.prepare(presentation, action="deploy:staging", release="held", now=at(2))["ticket"]
        witness = attest_closure(gate, effects, self.manifest, self.request, self.revoked, now=at(5))
        result = effects.finish(ticket, action="deploy:staging", release="held", now=at(6))
        self.assertEqual(result["reason_codes"], ["MANDATE_REVOKED"])
        self.assertFalse(result["effect_applied"])
        self.assertEqual(witness["local_closure"]["pending_fenced"], 1)
        self.assertFalse((self.root / "r0" / "effects.json").exists())

    def test_wrong_member_and_duplicate_keys_are_rejected(self):
        key = Ed25519PrivateKey.generate()
        members = [{"gate_id": "a", "gate_public_key": public_key_hex(key)},
                   {"gate_id": "b", "gate_public_key": public_key_hex(key)}]
        with self.assertRaisesRegex(WalletError, "CLOSURE_KEY_DUPLICATE"):
            create_membership(self.wallet.root_key, "grant-a", members, now=at(2))
        forged = copy.deepcopy(self.manifest)
        forged["members"].pop()
        with self.assertRaises(WalletError):
            verify_membership(forged, now=at(4))

    def test_tampered_membership_and_request_cannot_change_the_world(self):
        manifest = resign({**self.manifest, "scope": "ANY_SYSTEM"}, self.wallet.root_key)
        with self.assertRaisesRegex(WalletError, "CLOSURE_MEMBERSHIP_INVALID"):
            verify_membership(manifest, now=at(4))
        other_key = Ed25519PrivateKey.generate()
        wrong = resign(self.request, other_key)
        with self.assertRaisesRegex(WalletError, "CLOSURE_REQUEST_SIGNATURE_INVALID"):
            verify_closure_request(self.manifest, wrong, self.revoked, now=at(5))
        with self.assertRaisesRegex(WalletError, "CLOSURE_REQUEST_BINDING_MISMATCH"):
            verify_closure_request(self.manifest, self.request, self.grant, now=at(5))

    def test_canonical_membership_and_request_types_are_enforced(self):
        with self.assertRaisesRegex(WalletError, "CLOSURE_MEMBERS_INVALID"):
            create_membership(self.wallet.root_key, "grant-a", "not-a-member-list", now=at(2))
        noncanonical = resign({**self.manifest, "root_public_key": self.wallet.root_public_key.upper()},
                              self.wallet.root_key)
        with self.assertRaisesRegex(WalletError, "CLOSURE_MEMBERSHIP_INVALID"):
            verify_membership(noncanonical, now=at(4))
        malformed = resign({**self.request, "head_sequence": True}, self.wallet.root_key)
        with self.assertRaisesRegex(WalletError, "CLOSURE_REQUEST_INVALID"):
            verify_closure_request(self.manifest, malformed, self.revoked, now=at(5))

    def test_auditor_report_cannot_be_accepted_before_its_timestamp(self):
        witnesses = self.witnesses()
        auditor = Ed25519PrivateKey.generate()
        result = evaluate_closure_set(self.manifest, self.request, self.revoked, witnesses, now=at(6))
        report = sign_set_report(result, auditor)
        with self.assertRaisesRegex(WalletError, "CLOSURE_SET_FROM_FUTURE"):
            verify_set_report(report, public_key_hex(auditor), self.manifest,
                              self.request, self.revoked, witnesses, now=at(5))

    def test_stale_request_and_nonce_replay_fail(self):
        witnesses = self.witnesses()
        second = create_closure_request(self.wallet.root_key, self.manifest, self.revoked,
                                        now=at(7), nonce="request-two")
        result = evaluate_closure_set(self.manifest, second, self.revoked, witnesses, now=at(8))
        self.assertEqual(result["status"], "CLOSURE_INCOMPLETE")
        self.assertEqual(len(result["errors"]), 3)
        with self.assertRaisesRegex(WalletError, "CLOSURE_REQUEST_EXPIRED"):
            evaluate_closure_set(self.manifest, self.request, self.revoked, witnesses, now=at(125))

    def test_one_poisoned_member_cannot_be_counted_away(self):
        witnesses = self.witnesses()
        attacks = [
            {"gate_id": "other"}, {"scope": "ANY_SYSTEM"}, {"status": "EFFECT_CLOSED", "active_frontiers": 1},
            {"mandate_id": "different"}, {"head_hash": "0" * 64}, {"pending_fenced": -1},
        ]
        for changes in attacks:
            with self.subTest(changes=changes):
                altered = copy.deepcopy(witnesses)
                local = {k: v for k, v in altered[1]["local_closure"].items() if k not in {"payload_hash", "signature"}}
                local.update(changes)
                altered[1]["local_closure"] = sign_record(local, self.gates[1].gate_key)
                altered[1] = resign(altered[1], self.gates[1].gate_key)
                result = evaluate_closure_set(self.manifest, self.request, self.revoked, altered, now=at(6))
                self.assertEqual(result["status"], "CLOSURE_INCOMPLETE")
                self.assertEqual(result["verified_receivers"], 2)
        duplicated = witnesses + [copy.deepcopy(witnesses[0])]
        result = evaluate_closure_set(self.manifest, self.request, self.revoked, duplicated, now=at(6))
        self.assertEqual(result["status"], "CLOSURE_INCOMPLETE")
        self.assertTrue(result["errors"])

    def test_unrecognized_key_and_extra_member_are_not_quorum_votes(self):
        witnesses = self.witnesses()
        fake = copy.deepcopy(witnesses[0])
        fake = resign(fake, Ed25519PrivateKey.generate())
        result = evaluate_closure_set(self.manifest, self.request, self.revoked,
                                      [fake, witnesses[1], witnesses[2]], now=at(6))
        self.assertEqual(result["verified_receivers"], 2)
        self.assertEqual(result["status"], "CLOSURE_INCOMPLETE")
        extra = resign({**witnesses[0], "gate_id": "receiver-99"}, self.gates[0].gate_key)
        result = evaluate_closure_set(self.manifest, self.request, self.revoked,
                                      witnesses + [extra], now=at(6))
        self.assertEqual(result["status"], "CLOSURE_INCOMPLETE")
        self.assertEqual(result["verified_receivers"], 3)

    def test_auditor_cannot_promote_missing_evidence(self):
        witnesses = self.witnesses()
        auditor = Ed25519PrivateKey.generate()
        result = evaluate_closure_set(self.manifest, self.request, self.revoked, witnesses[:2], now=at(6))
        report = sign_set_report({**result, "status": "EFFECT_CLOSED"}, auditor)
        with self.assertRaisesRegex(WalletError, "CLOSURE_SET_BINDING_MISMATCH"):
            verify_set_report(report, public_key_hex(auditor), self.manifest, self.request,
                              self.revoked, witnesses[:2], now=at(6))
        good = sign_set_report(result, auditor)
        tampered = copy.deepcopy(good)
        tampered["verified_receivers"] = 3
        with self.assertRaisesRegex(WalletError, "CLOSURE_SET_SIGNATURE_INVALID"):
            verify_set_report(tampered, public_key_hex(auditor), self.manifest, self.request,
                              self.revoked, witnesses[:2], now=at(6))

    def test_closure_clock_is_sampled_after_inflight_drain(self):
        from threading import Event, Thread
        from unittest.mock import patch
        from openline_wallet import effect_closure as effect_module
        from openline_wallet import closure_set as set_module

        gate, effects = self.gates[0], self.effects[0]
        challenge = gate.issue_challenge(principal_id=self.wallet.principal_id,
                                         subject_id="agent-a", action="deploy:staging", now=at(2))
        presentation = create_presentation(bundle=self.grant, mandate_id="grant-a",
            subject_id="agent-a", subject_key=self.subject, action="deploy:staging",
            receiver_challenge=challenge, now=at(2))
        ticket = effects.prepare(presentation, action="deploy:staging", release="drain", now=at(2))["ticket"]
        entered, release, sampled = Event(), Event(), Event()
        clock = {"now": at(5)}
        original = effect_module.atomic_write_json
        results = {}
        def held_write(path, value, **kwargs):
            if Path(path) == effects.ledger_path:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test writer timed out")
            return original(path, value, **kwargs)
        def fake_clock():
            sampled.set()
            return clock["now"]
        def finish():
            results["effect"] = effects.finish(ticket, action="deploy:staging", release="drain", now=at(5))
        def close():
            results["closure"] = attest_closure(gate, effects, self.manifest,
                                                self.request, self.revoked)
        with patch.object(effect_module, "atomic_write_json", side_effect=held_write), \
             patch.object(set_module, "utc_now", side_effect=fake_clock):
            writer = Thread(target=finish)
            writer.start()
            self.assertTrue(entered.wait(3))
            closer = Thread(target=close)
            closer.start()
            self.assertFalse(sampled.wait(0.05), "closure clock was sampled upstream of the frontier")
            clock["now"] = at(8)
            release.set()
            writer.join(5)
            closer.join(5)
        self.assertFalse(writer.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertTrue(results["effect"]["effect_applied"])
        self.assertEqual(results["closure"]["local_closure"]["closed_at"], "2026-09-06T12:00:08Z")

    def test_receiver_uncertainty_blocks_witness(self):
        gate, effects = self.gates[0], self.effects[0]
        challenge = gate.issue_challenge(principal_id=self.wallet.principal_id,
                                         subject_id="agent-a", action="deploy:staging", now=at(2))
        presentation = create_presentation(bundle=self.grant, mandate_id="grant-a",
            subject_id="agent-a", subject_key=self.subject, action="deploy:staging",
            receiver_challenge=challenge, now=at(2))
        ticket = effects.prepare(presentation, action="deploy:staging", release="uncertain", now=at(2))["ticket"]
        from unittest.mock import patch
        from openline_wallet import effect_closure as module
        original = module.atomic_write_json
        def fail_ledger(path, value, **kwargs):
            if Path(path) == effects.ledger_path:
                raise OSError("injected storage failure")
            return original(path, value, **kwargs)
        with patch.object(module, "atomic_write_json", side_effect=fail_ledger):
            with self.assertRaises(OSError):
                effects.finish(ticket, action="deploy:staging", release="uncertain", now=at(2))
        with self.assertRaisesRegex(WalletError, "EFFECT_OUTCOME_UNRESOLVED"):
            attest_closure(gate, effects, self.manifest, self.request, self.revoked, now=at(5))
        self.assertTrue(any(effects.intent_dir.iterdir()))
        witnesses = [attest_closure(g, e, self.manifest, self.request, self.revoked, now=at(5))
                     for g, e in zip(self.gates[1:], self.effects[1:])]
        self.assertEqual(evaluate_closure_set(self.manifest, self.request, self.revoked,
                                              witnesses, now=at(6))["status"], "CLOSURE_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
