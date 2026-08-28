from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.crypto import public_key_hex, sign_record
from openline_wallet.errors import WalletError
from openline_wallet.receiver import ReferenceGate, create_presentation
from openline_wallet.wallet import Wallet


BASE = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


class ReceiverGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.wallet = Wallet.create(Path(self.temp.name) / "wallet", now=BASE)
        self.agent_key = Ed25519PrivateKey.generate()
        self.wallet.grant(
            subject_id="agent-a",
            subject_public_key=public_key_hex(self.agent_key),
            scopes=["deploy:staging"],
            expires_at=BASE + timedelta(hours=1),
            now=BASE,
            mandate_id="mandate-a",
        )
        self.bundle = self.wallet.export_bundle(now=BASE + timedelta(seconds=1))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def gate(self) -> ReferenceGate:
        gate = ReferenceGate("receiver-test")
        gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        return gate

    def presentation(self, gate: ReferenceGate, *, action: str = "deploy:staging", now=BASE + timedelta(seconds=2)):
        challenge = gate.issue_challenge(
            principal_id=self.wallet.principal_id,
            subject_id="agent-a",
            action=action,
            now=now,
        )
        return create_presentation(
            bundle=self.bundle,
            mandate_id="mandate-a",
            subject_id="agent-a",
            subject_key=self.agent_key,
            action=action,
            receiver_challenge=challenge,
            now=now,
        )

    def test_unpinned_root_is_rejected(self) -> None:
        with self.assertRaisesRegex(WalletError, "ROOT_NOT_PINNED"):
            ReferenceGate("untrusted").admit_bundle(self.bundle, now=BASE + timedelta(seconds=1))

    def test_exact_action_and_holder_proof_pass(self) -> None:
        gate = self.gate()
        gate.admit_bundle(self.bundle, now=BASE + timedelta(seconds=1))
        receipt = gate.evaluate(
            self.presentation(gate),
            expected_action="deploy:staging",
            now=BASE + timedelta(seconds=2),
        )
        self.assertEqual(receipt["decision"], "ALLOWED")
        self.assertEqual(receipt["decision_authority"], "RECEIVER_GATE")

    def test_presentation_is_one_use(self) -> None:
        gate = self.gate()
        gate.admit_bundle(self.bundle, now=BASE + timedelta(seconds=1))
        presentation = self.presentation(gate)
        first = gate.evaluate(presentation, expected_action="deploy:staging", now=BASE + timedelta(seconds=2))
        second = gate.evaluate(presentation, expected_action="deploy:staging", now=BASE + timedelta(seconds=3))
        self.assertEqual(first["decision"], "ALLOWED")
        self.assertEqual(second["reason_codes"], ["PRESENTATION_REPLAYED"])

    def test_signed_malformed_time_stops_instead_of_raising(self) -> None:
        gate = self.gate()
        gate.admit_bundle(self.bundle, now=BASE + timedelta(seconds=1))
        presentation = self.presentation(gate)
        body = dict(presentation)
        body.pop("payload_hash")
        body.pop("signature")
        body["issued_at"] = "yesterday-ish"
        malformed = sign_record(body, self.agent_key)
        receipt = gate.evaluate(
            malformed,
            expected_action="deploy:staging",
            now=BASE + timedelta(seconds=2),
        )
        self.assertEqual(receipt["decision"], "STOPPED")
        self.assertEqual(receipt["reason_codes"], ["PRESENTATION_TIME_INVALID"])

    def test_action_outside_mandate_is_stopped(self) -> None:
        gate = self.gate()
        gate.admit_bundle(self.bundle, now=BASE + timedelta(seconds=1))
        presentation = self.presentation(gate, action="deploy:production")
        receipt = gate.evaluate(
            presentation,
            expected_action="deploy:production",
            now=BASE + timedelta(seconds=2),
        )
        self.assertEqual(receipt["decision"], "STOPPED")
        self.assertEqual(receipt["reason_codes"], ["ACTION_OUTSIDE_MANDATE"])

    def test_current_history_stops_revoked_old_mandate(self) -> None:
        gate = self.gate()
        gate.admit_bundle(self.bundle, now=BASE + timedelta(seconds=1))
        self.wallet.revoke("agent-a", now=BASE + timedelta(seconds=2))
        current = self.wallet.export_bundle(now=BASE + timedelta(seconds=3))
        gate.admit_bundle(current, now=BASE + timedelta(seconds=3))
        challenge = gate.issue_challenge(
            principal_id=self.wallet.principal_id,
            subject_id="agent-a",
            action="deploy:staging",
            now=BASE + timedelta(seconds=4),
        )
        presentation = create_presentation(
            bundle=current,
            mandate_id="mandate-a",
            subject_id="agent-a",
            subject_key=self.agent_key,
            action="deploy:staging",
            receiver_challenge=challenge,
            now=BASE + timedelta(seconds=4),
        )
        receipt = gate.evaluate(presentation, expected_action="deploy:staging", now=BASE + timedelta(seconds=4))
        self.assertEqual(receipt["reason_codes"], ["MANDATE_REVOKED"])
        with self.assertRaisesRegex(WalletError, "BUNDLE_HEAD_STALE"):
            gate.admit_bundle(self.bundle, now=BASE + timedelta(seconds=5))

    def test_receipt_cannot_name_a_different_gate_key(self) -> None:
        gate = self.gate()
        gate.admit_bundle(self.bundle, now=BASE + timedelta(seconds=1))
        receipt = gate.evaluate(
            self.presentation(gate),
            expected_action="deploy:staging",
            now=BASE + timedelta(seconds=2),
        )
        body = dict(receipt)
        body.pop("payload_hash")
        body.pop("signature")
        body["gate_public_key"] = public_key_hex(Ed25519PrivateKey.generate())
        forged_label = sign_record(body, gate.gate_key)
        with self.assertRaisesRegex(WalletError, "GATE_RECEIPT_SIGNATURE_INVALID"):
            self.wallet.add_receipt(forged_label)

    def test_competing_same_sequence_heads_quarantine(self) -> None:
        identity = Wallet.create(Path(self.temp.name) / "identity", now=BASE)
        seed = identity.export_bundle(now=BASE)
        branch_a = Wallet.import_bundle(
            Path(self.temp.name) / "branch-a",
            seed,
            root_key=identity.root_key,
            epoch_key=identity.epoch_key,
            now=BASE,
        )
        branch_b = Wallet.import_bundle(
            Path(self.temp.name) / "branch-b",
            seed,
            root_key=identity.root_key,
            epoch_key=identity.epoch_key,
            now=BASE,
        )
        key_a = Ed25519PrivateKey.generate()
        key_b = Ed25519PrivateKey.generate()
        branch_a.grant(
            subject_id="agent-a",
            subject_public_key=public_key_hex(key_a),
            scopes=["read:a"],
            expires_at=BASE + timedelta(hours=1),
            now=BASE + timedelta(seconds=1),
            mandate_id="branch-a-mandate",
        )
        branch_b.grant(
            subject_id="agent-b",
            subject_public_key=public_key_hex(key_b),
            scopes=["read:b"],
            expires_at=BASE + timedelta(hours=1),
            now=BASE + timedelta(seconds=1),
            mandate_id="branch-b-mandate",
        )
        gate = ReferenceGate("fork-test")
        gate.pin_principal(identity.principal_id, identity.root_public_key)
        gate.admit_bundle(branch_a.export_bundle(now=BASE + timedelta(seconds=2)), now=BASE + timedelta(seconds=2))
        with self.assertRaisesRegex(WalletError, "BUNDLE_FORK_QUARANTINED"):
            gate.admit_bundle(branch_b.export_bundle(now=BASE + timedelta(seconds=2)), now=BASE + timedelta(seconds=2))


if __name__ == "__main__":
    unittest.main()
