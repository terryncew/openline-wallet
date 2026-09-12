from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.crypto import public_key_hex, verify_record
from openline_wallet.receiver import ROUTE_RECEIPT_SCHEMA, ReferenceGate, create_presentation
from openline_wallet.wallet import Wallet


BASE = datetime(2026, 9, 12, 19, 0, tzinfo=timezone.utc)


class RouteAuthorityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.wallet = Wallet.create(Path(self.temp.name) / "wallet", now=BASE)
        self.keys = {name: Ed25519PrivateKey.generate() for name in ("source", "blocked", "allowed", "clean")}
        scopes = {
            "source": ["records:read:payroll", "message:send:peer"],
            "blocked": ["message:receive:peer"],
            "allowed": ["records:read:payroll", "message:receive:peer"],
            "clean": ["message:send:peer"],
        }
        for i, name in enumerate(("source", "blocked", "allowed", "clean"), start=1):
            self.wallet.grant(
                subject_id=f"agent-{name}",
                subject_public_key=public_key_hex(self.keys[name]),
                scopes=scopes[name],
                expires_at=BASE + timedelta(hours=1),
                now=BASE + timedelta(seconds=i),
                mandate_id=f"mandate-{name}",
            )
        self.bundle = self.wallet.export_bundle(now=BASE + timedelta(seconds=5))
        self.gate = ReferenceGate("route-authority-unit")
        self.gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        self.gate.protect_action(
            "records:read:payroll",
            required_recipient_scopes=["records:read:payroll"],
        )
        self.gate.admit_bundle(self.bundle, now=BASE + timedelta(seconds=5))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def action(self, name: str, action: str, offset: int):
        challenge = self.gate.issue_challenge(
            principal_id=self.wallet.principal_id,
            subject_id=f"agent-{name}",
            action=action,
            now=BASE + timedelta(seconds=offset),
        )
        presentation = create_presentation(
            bundle=self.bundle,
            mandate_id=f"mandate-{name}",
            subject_id=f"agent-{name}",
            subject_key=self.keys[name],
            action=action,
            receiver_challenge=challenge,
            now=BASE + timedelta(seconds=offset),
        )
        return self.gate.evaluate(
            presentation,
            expected_action=action,
            now=BASE + timedelta(seconds=offset),
        )

    def test_protected_read_creates_monotonic_carried_scope(self) -> None:
        self.assertEqual(self.gate.carried_scopes(self.wallet.principal_id, "agent-source"), ())
        receipt = self.action("source", "records:read:payroll", 6)
        self.assertEqual(receipt["decision"], "ALLOWED")
        self.assertEqual(
            self.gate.carried_scopes(self.wallet.principal_id, "agent-source"),
            ("records:read:payroll",),
        )

    def test_unexposed_sender_can_reach_recipient_without_source_scope(self) -> None:
        send = self.action("clean", "message:send:peer", 6)
        route = self.gate.evaluate_route(
            send,
            expected_action="message:send:peer",
            recipient_subject_id="agent-blocked",
            now=BASE + timedelta(seconds=7),
        )
        self.assertEqual(route["decision"], "ALLOWED")
        self.assertEqual(route["carried_scopes"], [])

    def test_exposed_sender_cannot_route_to_recipient_without_source_scope(self) -> None:
        self.action("source", "records:read:payroll", 6)
        send = self.action("source", "message:send:peer", 7)
        route = self.gate.evaluate_route(
            send,
            expected_action="message:send:peer",
            recipient_subject_id="agent-blocked",
            now=BASE + timedelta(seconds=8),
        )
        self.assertEqual(route["decision"], "STOPPED")
        self.assertEqual(route["reason_codes"], ["RECIPIENT_LACKS_SOURCE_AUTHORITY"])
        self.assertEqual(route["carried_scopes"], ["records:read:payroll"])

    def test_exposed_sender_can_route_to_authorized_recipient(self) -> None:
        self.action("source", "records:read:payroll", 6)
        send = self.action("source", "message:send:peer", 7)
        route = self.gate.evaluate_route(
            send,
            expected_action="message:send:peer",
            recipient_subject_id="agent-allowed",
            now=BASE + timedelta(seconds=8),
        )
        self.assertEqual(route["decision"], "ALLOWED")
        self.assertEqual(route["recipient_subject_id"], "agent-allowed")
        ok, reason = verify_record(route, expected_public_key=self.gate.public_key)
        self.assertTrue(ok, reason)
        self.assertEqual(route["schema"], ROUTE_RECEIPT_SCHEMA)

    def test_route_receipt_cannot_be_reused_for_a_second_recipient(self) -> None:
        send = self.action("clean", "message:send:peer", 6)
        first = self.gate.evaluate_route(
            send,
            expected_action="message:send:peer",
            recipient_subject_id="agent-blocked",
            now=BASE + timedelta(seconds=7),
        )
        second = self.gate.evaluate_route(
            send,
            expected_action="message:send:peer",
            recipient_subject_id="agent-allowed",
            now=BASE + timedelta(seconds=8),
        )
        self.assertEqual(first["decision"], "ALLOWED")
        self.assertEqual(second["decision"], "STOPPED")
        self.assertEqual(second["reason_codes"], ["ROUTE_ACTION_RECEIPT_REPLAYED"])

    def test_conflicting_receiver_policy_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "PROTECTED_ACTION_CONFLICT"):
            self.gate.protect_action(
                "records:read:payroll",
                required_recipient_scopes=["records:read:executive"],
            )


if __name__ == "__main__":
    unittest.main()
