"""Independent local receiver checks; never execute Website effects.

These direct ReferenceGate evaluations supplement the eight-transaction story.
They do not bypass Website B policy to claim B accepted advertising refusal.
"""
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.clock import isoformat, utc_now
from openline_wallet.crypto import public_key_hex, record_hash, verify_record
from openline_wallet.errors import WalletError
from openline_wallet.receiver import ReferenceGate, create_presentation
from openline_wallet.wallet import Wallet


class IndependentReceiverReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = utc_now()
        self.wallet = Wallet.create(Path(self.temp.name) / "owner", now=self.now)
        self.akey = Ed25519PrivateKey.generate()
        self.bkey = Ed25519PrivateKey.generate()
        self.gates = [ReferenceGate("review-site-a"), ReferenceGate("review-site-b")]
        self.actions = {gate.gate_id: "review:" + gate.gate_id for gate in self.gates}
        self.wallet.grant(
            subject_id="agent-a", subject_public_key=public_key_hex(self.akey),
            scopes=list(self.actions.values()), expires_at=self.now + timedelta(hours=1),
            mandate_id="review-mandate-a", now=self.now,
        )
        self.bundle = self.wallet.export_bundle(now=self.now)
        self.records = []
        for gate in self.gates:
            gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
            gate.admit_bundle(self.bundle, now=self.now)
        self.addCleanup(self.persist)

    def persist(self):
        directory = os.environ.get("OWNER_PORTABILITY_REVIEW_EVIDENCE")
        if directory:
            path = Path(directory)
            path.mkdir(parents=True, exist_ok=True)
            output = path / (self._testMethodName + ".json")
            if output.exists():
                raise AssertionError("review evidence must not overwrite a prior run")
            output.write_text(json.dumps({
                "test": self._testMethodName,
                "review_time": isoformat(self.now),
                "kind": "supplemental-direct-gate-evaluation",
                "website_transactions": 0,
                "protected_effects": 0,
                "owner_root": self.wallet.root_public_key,
                "agent_a_public_key": public_key_hex(self.akey),
                "agent_b_public_key": public_key_hex(self.bkey),
                "receiver_pins": {g.gate_id: g.public_key for g in self.gates},
                "initial_bundle": self.bundle,
                "final_bundle": self.wallet.export_bundle(now=self.now),
                "checks": self.records,
                "limits": "Synthetic same-process receiver instances; no independently operated website, OS key isolation, or protected effects.",
            }, indent=2) + "\n")

    def presentation(self, gate, *, key=None, subject="agent-a", mandate="review-mandate-a", bundle=None):
        bundle = bundle or self.bundle
        action = self.actions[gate.gate_id]
        challenge = gate.issue_challenge(
            principal_id=self.wallet.principal_id, subject_id=subject,
            action=action, now=self.now,
        )
        return create_presentation(
            bundle=bundle, mandate_id=mandate, subject_id=subject,
            subject_key=key or self.akey, action=action,
            receiver_challenge=challenge, now=self.now,
        )

    def evaluate(self, gate, presentation, expected_reason=None):
        receipt = gate.evaluate(presentation, expected_action=self.actions[gate.gate_id], now=self.now)
        self.assertTrue(verify_record(receipt, expected_public_key=gate.public_key)[0])
        self.assertEqual(receipt["presentation_hash"], record_hash(presentation))
        self.assertEqual(receipt["decision"], "STOPPED" if expected_reason else "ALLOWED")
        self.assertEqual(receipt["reason_codes"], [expected_reason] if expected_reason else [])
        self.records.append({"receiver": gate.gate_id, "presentation": presentation, "receipt": receipt})
        return receipt

    def switch_to_b(self):
        self.wallet.revoke("agent-a", reason="REVIEW_OWNER_SWITCH", now=self.now)
        self.wallet.grant(
            subject_id="agent-b", subject_public_key=public_key_hex(self.bkey),
            scopes=list(self.actions.values()), expires_at=self.now + timedelta(hours=1),
            mandate_id="review-mandate-b", now=self.now,
        )
        current = self.wallet.export_bundle(now=self.now)
        for gate in self.gates:
            gate.admit_bundle(current, now=self.now)
        return current

    def test_each_receiver_rejects_wrong_holder(self):
        impostor = Ed25519PrivateKey.generate()
        for gate in self.gates:
            with self.subTest(receiver=gate.gate_id):
                self.evaluate(gate, self.presentation(gate, key=impostor), "SUBJECT_BINDING_MISMATCH")

    def test_each_receiver_enforces_one_use_challenges(self):
        for gate in self.gates:
            with self.subTest(receiver=gate.gate_id):
                presentation = self.presentation(gate)
                self.evaluate(gate, presentation)
                self.evaluate(gate, presentation, "PRESENTATION_REPLAYED")

    def test_challenges_are_receiver_specific(self):
        a, b = self.gates
        pa, pb = self.presentation(a), self.presentation(b)
        self.evaluate(b, pa, "CHALLENGE_UNKNOWN")
        self.evaluate(a, pb, "CHALLENGE_UNKNOWN")
        # A failed cross-receiver attempt did not consume the issuing receiver's challenge.
        self.evaluate(a, pa)
        self.evaluate(b, pb)

    def test_each_receiver_rejects_revoked_a(self):
        current = self.switch_to_b()
        for gate in self.gates:
            with self.subTest(receiver=gate.gate_id):
                self.evaluate(gate, self.presentation(gate, bundle=current), "MANDATE_REVOKED")

    def test_each_receiver_rejects_stale_authority_after_observation(self):
        self.switch_to_b()
        for gate in self.gates:
            with self.subTest(receiver=gate.gate_id):
                with self.assertRaises(WalletError) as rejected:
                    gate.admit_bundle(self.bundle, now=self.now)
                self.assertEqual(rejected.exception.code, "BUNDLE_HEAD_STALE")
                self.records.append({"receiver": gate.gate_id, "operation": "admit-old-bundle", "reason": rejected.exception.code})

    def test_each_receiver_rejects_forged_authority(self):
        forged = deepcopy(self.bundle)
        forged["events"][0]["data"]["scopes"] = ["everything"]
        for gate in self.gates:
            with self.subTest(receiver=gate.gate_id):
                with self.assertRaises(WalletError) as rejected:
                    gate.admit_bundle(forged, now=self.now)
                self.assertEqual(rejected.exception.code, "BUNDLE_SIGNATURE_INVALID")
                self.records.append({"receiver": gate.gate_id, "operation": "admit-forged-bundle", "reason": rejected.exception.code, "forged_bundle": forged})

    def test_b_uses_own_key_and_a_cannot_sign_b_mandate(self):
        current = self.switch_to_b()
        self.assertEqual(len({self.wallet.root_public_key, public_key_hex(self.akey), public_key_hex(self.bkey)}), 3)
        self.assertNotEqual(self.gates[0].public_key, self.gates[1].public_key)
        for gate in self.gates:
            with self.subTest(receiver=gate.gate_id):
                self.evaluate(gate, self.presentation(gate, key=self.bkey, subject="agent-b", mandate="review-mandate-b", bundle=current))
                self.evaluate(gate, self.presentation(gate, key=self.akey, subject="agent-b", mandate="review-mandate-b", bundle=current), "SUBJECT_BINDING_MISMATCH")


if __name__ == "__main__":
    unittest.main()
