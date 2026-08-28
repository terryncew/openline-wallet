from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.crypto import load_private_key, private_key_hex, public_key_hex, verify_record
from openline_wallet.errors import WalletError
from openline_wallet.storage import EPOCH_KEY_FILE, ROOT_KEY_FILE
from openline_wallet.wallet import Wallet, verify_bundle


BASE = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


class WalletTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "wallet"
        self.wallet = Wallet.create(self.path, now=BASE)
        self.subject_key = Ed25519PrivateKey.generate()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def grant(self, *, scopes: list[str] | None = None, mandate_id: str = "mandate-a") -> dict:
        return self.wallet.grant(
            subject_id="agent-a",
            subject_public_key=public_key_hex(self.subject_key),
            scopes=scopes or ["deploy:staging"],
            expires_at=BASE + timedelta(hours=2),
            now=BASE,
            mandate_id=mandate_id,
        )

    def test_identity_has_separate_root_and_epoch_keys(self) -> None:
        root = load_private_key(self.path / ROOT_KEY_FILE)
        epoch = load_private_key(self.path / EPOCH_KEY_FILE)
        self.assertNotEqual(public_key_hex(root), public_key_hex(epoch))
        self.assertEqual(self.wallet.root_public_key, public_key_hex(root))
        if os.name != "nt":
            self.assertEqual((self.path / ROOT_KEY_FILE).stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.path / EPOCH_KEY_FILE).stat().st_mode & 0o777, 0o600)

    def test_export_verifies_and_contains_no_private_key(self) -> None:
        self.grant()
        bundle = self.wallet.export_bundle(now=BASE + timedelta(seconds=1))
        _verified, timeline = verify_bundle(bundle, now=BASE + timedelta(seconds=1))
        serialized = json.dumps(bundle)
        self.assertEqual(timeline.head_sequence, 1)
        self.assertNotIn(private_key_hex(self.wallet.root_key), serialized)
        self.assertNotIn(private_key_hex(self.wallet.epoch_key), serialized)
        self.assertEqual(bundle["wallet_policy_authority"], "NONE")
        self.assertEqual(bundle["decision_authority"], "RECEIVER_GATE")

    def test_tampered_scope_fails_bundle_verification(self) -> None:
        self.grant()
        bundle = self.wallet.export_bundle(now=BASE + timedelta(seconds=1))
        bundle["events"][0]["data"]["scopes"] = ["deploy:production"]
        with self.assertRaises(WalletError):
            verify_bundle(bundle, now=BASE + timedelta(seconds=1))

    def test_narrow_requires_a_strict_subset(self) -> None:
        self.grant(scopes=["deploy:staging", "read:logs"])
        event = self.wallet.narrow(
            "agent-a",
            scopes=["deploy:staging"],
            now=BASE + timedelta(seconds=1),
            mandate_id="mandate-a-narrow",
        )
        self.assertEqual(event["event_type"], "MANDATE_NARROWED")
        timeline = self.wallet.timeline()
        self.assertEqual(timeline.mandates["mandate-a"]["status"], "SUPERSEDED")
        self.assertEqual(timeline.mandates["mandate-a-narrow"]["status"], "ACTIVE")

    def test_narrow_cannot_expand_scope(self) -> None:
        self.grant()
        with self.assertRaisesRegex(WalletError, "NARROW_SCOPE_EXPANDED"):
            self.wallet.narrow(
                "agent-a",
                scopes=["deploy:staging", "deploy:production"],
                now=BASE + timedelta(seconds=1),
            )

    def test_revocation_preserves_original_signature(self) -> None:
        issued = self.grant()
        self.wallet.revoke("agent-a", now=BASE + timedelta(seconds=1))
        valid, reason = verify_record(
            issued,
            expected_public_key=self.wallet.state["epoch_certificate"]["epoch_public_key"],
        )
        self.assertTrue(valid, reason)
        self.assertEqual(self.wallet.timeline().mandates["mandate-a"]["status"], "REVOKED")

    def test_bundle_freshness_is_capped_at_ten_minutes(self) -> None:
        with self.assertRaisesRegex(WalletError, "BUNDLE_TTL_INVALID"):
            self.wallet.export_bundle(now=BASE, ttl_seconds=601)
        bundle = self.wallet.export_bundle(now=BASE, ttl_seconds=60)
        with self.assertRaisesRegex(WalletError, "BUNDLE_EXPIRED"):
            verify_bundle(bundle, now=BASE + timedelta(seconds=61))

    def test_naive_api_timestamp_fails_closed(self) -> None:
        with self.assertRaisesRegex(WalletError, "TIMESTAMP_TIMEZONE_REQUIRED"):
            self.wallet.grant(
                subject_id="agent-a",
                subject_public_key=public_key_hex(self.subject_key),
                scopes=["deploy:staging"],
                expires_at=datetime(2026, 8, 28, 14, 0),
                now=BASE,
            )

    def test_import_restores_history_only_with_matching_keys(self) -> None:
        self.grant()
        bundle = self.wallet.export_bundle(now=BASE + timedelta(seconds=1))
        imported = Wallet.import_bundle(
            Path(self.temp.name) / "imported",
            bundle,
            root_key=self.wallet.root_key,
            epoch_key=self.wallet.epoch_key,
            now=BASE + timedelta(seconds=2),
        )
        self.assertEqual(imported.principal_id, self.wallet.principal_id)
        self.assertEqual(imported.timeline().head_hash, self.wallet.timeline().head_hash)

    def test_import_rejects_wrong_root_key(self) -> None:
        bundle = self.wallet.export_bundle(now=BASE)
        with self.assertRaisesRegex(WalletError, "IMPORTED_ROOT_KEY_MISMATCH"):
            Wallet.import_bundle(
                Path(self.temp.name) / "bad-import",
                bundle,
                root_key=Ed25519PrivateKey.generate(),
                epoch_key=self.wallet.epoch_key,
                now=BASE,
            )


if __name__ == "__main__":
    unittest.main()
