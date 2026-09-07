"""WALLET-EFFECT-CLOSURE-001: held work must not outlive revoked authority."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import sys
from urllib.request import Request, urlopen
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.canonical import strict_json_load
from openline_wallet.clock import utc_now
from openline_wallet.storage import atomic_write_json
from openline_wallet.crypto import public_key_hex, record_hash, sign_record, verify_record
from openline_wallet.effect_closure import EffectClosure, EffectGate, EFFECT_RECEIPT_SCHEMA
from openline_wallet.errors import WalletError
from openline_wallet.gate_http import ReceiverRuntime, build_http_server
from openline_wallet.receiver import ReferenceGate, create_presentation
from openline_wallet.wallet import Wallet

BASE = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
ACTION = "deploy:staging"


class EffectClosureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.wallet = Wallet.create(self.root / "wallet", now=BASE)
        self.subject_key = Ed25519PrivateKey.generate()
        self.wallet.grant(subject_id="agent-a", subject_public_key=public_key_hex(self.subject_key),
                          scopes=[ACTION, "read:logs"], expires_at=BASE+timedelta(hours=1),
                          now=BASE, mandate_id="grant-a")
        self.bundle = self.wallet.export_bundle(now=BASE+timedelta(seconds=1))
        self.gate = EffectGate("effect-test")
        self.gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        self.gate.admit_bundle(self.bundle, now=BASE+timedelta(seconds=1))
        self.effects = EffectClosure(self.gate, self.root/"effects.json", self.root/"receipts")

    def at(self, seconds):
        return BASE+timedelta(seconds=seconds)

    def presentation(self, *, bundle=None, mandate_id="grant-a", subject_id="agent-a", key=None, now=None):
        now = now or self.at(2)
        bundle = bundle or self.bundle
        token = self.gate.issue_challenge(principal_id=self.wallet.principal_id,
                                          subject_id=subject_id, action=ACTION, now=now)
        return create_presentation(bundle=bundle, mandate_id=mandate_id, subject_id=subject_id,
                                   subject_key=key or self.subject_key, action=ACTION,
                                   receiver_challenge=token, now=now)

    def prepare(self, release="held", now=None, **kwargs):
        now = now or self.at(2)
        return self.effects.prepare(self.presentation(now=now, **kwargs), action=ACTION,
                                    release=release, now=now)

    def revoke(self, at=3):
        self.wallet.revoke("grant-a", now=self.at(at))
        return self.wallet.export_bundle(now=self.at(at+1))

    def ledger(self):
        return strict_json_load(self.root/"effects.json") if (self.root/"effects.json").exists() else []

    def test_hold_revoke_close_release_blocks_effect(self):
        prepared = self.prepare()
        self.assertEqual(prepared["decision"], "PREPARED")
        self.assertFalse(prepared["effect_applied"])
        closure = self.effects.close(self.revoke(), "grant-a", now=self.at(4))
        self.assertEqual(closure["status"], "EFFECT_CLOSED")
        self.assertEqual(closure["pending_fenced"], 1)
        self.assertEqual(closure["active_frontiers"], 0)
        self.assertTrue(verify_record(closure, expected_public_key=self.gate.public_key)[0])
        result = self.effects.finish(prepared["ticket"], action=ACTION, release="held", now=self.at(5))
        self.assertEqual(result["decision"], "STOPPED")
        self.assertEqual(result["reason_codes"], ["MANDATE_REVOKED"])
        self.assertFalse(result["effect_applied"])
        self.assertEqual(self.ledger(), [])
        self.assertEqual(result["receipt_hash"], record_hash(result["receipt"]))
        self.assertEqual(result["receipt"]["decision"], "STOPPED")
        self.assertEqual(result["admission_receipt"]["decision"], "ALLOWED")
        self.assertTrue(verify_record(result["effect_receipt"], expected_public_key=self.gate.public_key)[0])
        self.assertEqual(result["effect_receipt"]["schema"], EFFECT_RECEIPT_SCHEMA)
        self.wallet.add_receipt(result["receipt"])
        self.assertEqual(self.wallet.summary(now=self.at(5))["receipt_count"], 1)

    def test_normal_effect_and_history_survive_revocation(self):
        prepared = self.prepare("before")
        result = self.effects.finish(prepared["ticket"], action=ACTION, release="before", now=self.at(3))
        self.assertEqual(result["decision"], "ALLOWED")
        self.assertTrue(result["effect_applied"])
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.ledger()[0]["receipt_hash"], result["receipt_hash"])
        self.wallet.add_receipt(result["receipt"])
        closure = self.effects.close(self.revoke(at=4), "grant-a", now=self.at(5))
        self.assertEqual(closure["status"], "EFFECT_CLOSED")
        self.assertEqual(len(self.ledger()), 1)
        self.assertEqual(self.wallet.summary(now=self.at(6))["receipt_count"], 1)

    def test_ticket_replay_is_idempotent_and_binding_is_exact(self):
        prepared = self.prepare()
        ticket = prepared["ticket"]
        with self.assertRaisesRegex(WalletError, "EFFECT_BINDING_MISMATCH"):
            self.effects.finish(ticket, action=ACTION, release="different", now=self.at(3))
        first = self.effects.finish(ticket, action=ACTION, release="held", now=self.at(3))
        second = self.effects.finish(ticket, action=ACTION, release="held", now=self.at(4))
        self.assertEqual(first, second)
        self.assertEqual(len(self.ledger()), 1)
        with self.assertRaisesRegex(WalletError, "EFFECT_TICKET_UNKNOWN"):
            self.effects.finish("effect_unknown", action=ACTION, release="held", now=self.at(4))

    def test_forged_admission_cannot_cross_frontier(self):
        prepared = self.prepare()
        original = self.effects._pending[prepared["ticket"]].admission
        forged = dict(original)
        forged.pop("payload_hash")
        forged.pop("signature")
        forged["action"] = "deploy:production"
        forged = sign_record(forged, Ed25519PrivateKey.generate())
        calls = []
        receipt, applied = self.gate.effect_frontier(forged, lambda receipt: calls.append(receipt), now=self.at(3))
        self.assertEqual(receipt["reason_codes"], ["ADMISSION_RECEIPT_INVALID"])
        self.assertFalse(applied)
        self.assertEqual(calls, [])

    def test_narrowing_fences_predecessor_but_successor_can_act(self):
        prepared = self.prepare()
        self.wallet.narrow("grant-a", scopes=["read:logs"], now=self.at(3), mandate_id="grant-b")
        current = self.wallet.export_bundle(now=self.at(4))
        self.gate.admit_bundle(current, now=self.at(4))
        result = self.effects.finish(prepared["ticket"], action=ACTION, release="held", now=self.at(5))
        self.assertEqual(result["reason_codes"], ["MANDATE_SUPERSEDED"])
        self.assertEqual(self.ledger(), [])

    def test_regrant_does_not_resurrect_old_ticket(self):
        prepared = self.prepare()
        self.wallet.revoke("grant-a", now=self.at(3))
        self.wallet.grant(subject_id="agent-a", subject_public_key=public_key_hex(self.subject_key),
                          scopes=[ACTION], expires_at=self.at(3600), now=self.at(4), mandate_id="grant-b")
        current = self.wallet.export_bundle(now=self.at(5))
        self.gate.admit_bundle(current, now=self.at(5))
        old = self.effects.finish(prepared["ticket"], action=ACTION, release="held", now=self.at(6))
        self.assertEqual(old["reason_codes"], ["MANDATE_REVOKED"])
        fresh = self.prepare("successor", now=self.at(7), bundle=current, mandate_id="grant-b")
        good = self.effects.finish(fresh["ticket"], action=ACTION, release="successor", now=self.at(8))
        self.assertTrue(good["effect_applied"])
        self.assertEqual([e["release"] for e in self.ledger()], ["successor"])

    def test_expiry_and_stale_bundle_fail_closed(self):
        prepared = self.prepare()
        result = self.effects.finish(prepared["ticket"], action=ACTION, release="held", now=self.at(602))
        self.assertEqual(result["reason_codes"], ["ADMITTED_BUNDLE_EXPIRED"])
        self.assertEqual(self.ledger(), [])
        self.wallet.export_bundle(now=self.at(3600))
        current = self.wallet.export_bundle(now=self.at(3600))
        self.gate.admit_bundle(current, now=self.at(3600))
        fresh = self.gate._receipt(decision="ALLOWED", reasons=[], principal_id=self.wallet.principal_id,
                                   mandate_id="grant-a", subject_id="agent-a", action=ACTION,
                                   presentation_hash="test", now=self.at(3600))
        receipt, applied = self.gate.effect_frontier(fresh, lambda _: self.fail("effect"), now=self.at(3600))
        self.assertEqual(receipt["reason_codes"], ["MANDATE_EXPIRED"])
        self.assertFalse(applied)

    def test_closure_requires_authentic_revocation_and_rejects_rollback(self):
        with self.assertRaisesRegex(WalletError, "MANDATE_NOT_REVOKED"):
            self.effects.close(self.bundle, "grant-a", now=self.at(2))
        revoked = self.revoke()
        forged = dict(revoked)
        forged["head"] = {"sequence": 999, "event_hash": "0"*64}
        with self.assertRaises(WalletError):
            self.effects.close(forged, "grant-a", now=self.at(4))
        self.effects.close(revoked, "grant-a", now=self.at(4))
        with self.assertRaisesRegex(WalletError, "BUNDLE_HEAD_STALE"):
            self.gate.admit_bundle(self.bundle, now=self.at(5))

    def test_callback_failure_seals_restarts_and_blocks_closure(self):
        prepared = self.prepare()
        original = self.effects._persist
        def broken(record, directory):
            if record.get("schema") == EFFECT_RECEIPT_SCHEMA:
                raise OSError("simulated evidence failure after effect")
            return original(record, directory)
        with patch.object(self.effects, "_persist", side_effect=broken):
            with self.assertRaises(OSError):
                self.effects.finish(prepared["ticket"], action=ACTION, release="held", now=self.at(3))
        self.assertEqual(len(self.ledger()), 1)
        with self.assertRaisesRegex(WalletError, "EFFECT_OUTCOME_UNRESOLVED"):
            self.effects.close(self.revoke(), "grant-a", now=self.at(4))
        self.effects.shutdown()
        restarted_gate = EffectGate("effect-test")
        restarted_gate.pin_principal(self.wallet.principal_id, self.wallet.root_public_key)
        restarted = EffectClosure(restarted_gate, self.root/"effects.json", self.root/"receipts")
        with self.assertRaisesRegex(WalletError, "EFFECT_OUTCOME_UNRESOLVED"):
            restarted.close(self.wallet.export_bundle(now=self.at(5)), "grant-a", now=self.at(5))
        self.assertTrue(list(restarted.intent_dir.iterdir()))
        restarted.shutdown()

    def test_corrupt_ledger_seals_instead_of_issuing_success(self):
        prepared = self.prepare()
        (self.root/"effects.json").write_text('{"broken": true}')
        with self.assertRaisesRegex(WalletError, "RECEIVER_LEDGER_INVALID"):
            self.effects.finish(prepared["ticket"], action=ACTION, release="held", now=self.at(3))
        self.assertTrue(self.effects._uncertain)
        self.assertTrue(list(self.effects.intent_dir.iterdir()))

    def test_closure_waits_for_effect_already_at_frontier(self):
        first = self.prepare("first")
        second = self.prepare("second", now=self.at(2))
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        results = {}
        original_write = atomic_write_json
        def held_write(path, value, **kwargs):
            if Path(path) == self.root/"effects.json":
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test hold")
            return original_write(path, value, **kwargs)
        def run_effect():
            try:
                results["first"] = self.effects.finish(first["ticket"], action=ACTION,
                                                       release="first", now=self.at(3))
            except Exception as exc:
                results["error"] = exc
        def run_close(bundle):
            try:
                results["closure"] = self.effects.close(bundle, "grant-a", now=self.at(5))
            except Exception as exc:
                results["error"] = exc
            finally:
                closed.set()
        with patch("openline_wallet.effect_closure.atomic_write_json", side_effect=held_write):
            worker = threading.Thread(target=run_effect)
            worker.start()
            self.assertTrue(entered.wait(5))
            revoked = self.revoke(at=4)
            closer = threading.Thread(target=run_close, args=(revoked,))
            closer.start()
            self.assertFalse(closed.wait(0.05))
            release.set()
            worker.join(5)
            closer.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertNotIn("error", results)
        self.assertTrue(results["first"]["effect_applied"])
        self.assertEqual(results["closure"]["status"], "EFFECT_CLOSED")
        self.assertEqual(results["closure"]["pending_fenced"], 1)
        result = self.effects.finish(second["ticket"], action=ACTION, release="second", now=self.at(6))
        self.assertEqual(result["reason_codes"], ["MANDATE_REVOKED"])
        self.assertEqual([e["release"] for e in self.ledger()], ["first"])

    def test_exclusive_writer_and_shutdown(self):
        with self.assertRaisesRegex(WalletError, "RECEIVER_EFFECT_OWNER_BUSY"):
            EffectClosure(self.gate, self.root/"effects.json", self.root/"receipts")
        other = EffectGate("other")
        with self.assertRaisesRegex(WalletError, "RECEIVER_EFFECT_OWNER_BUSY"):
            EffectClosure(other, self.root/"effects.json", self.root/"other-receipts")
        self.effects.shutdown()
        replacement = EffectClosure(other, self.root/"effects.json", self.root/"receipts")
        replacement.shutdown()
        with self.assertRaisesRegex(WalletError, "RECEIVER_EFFECT_OWNER_CLOSED"):
            self.effects.prepare({}, action=ACTION, release="held", now=self.at(3))

    def test_exact_original_receiver_allows_stale_admitted_work(self):
        """Expected negative control, loaded from the pinned, byte-exact v0.1 source."""
        source = Path(__file__).resolve().parents[1]/"proofs"/"wallet-effect-closure-001"/"baseline_gate_http.py"
        spec = importlib.util.spec_from_file_location("openline_wallet._effect_closure_baseline", source)
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        old = module.ReceiverRuntime.create(
            gate_id="old-receiver", principal_id=self.wallet.principal_id,
            root_public_key=self.wallet.root_public_key,
            ledger_path=self.root/"old"/"effects.json", receipts_dir=self.root/"old"/"receipts")
        old.gate.admit_bundle(self.bundle, now=self.at(1))
        challenge = old.gate.issue_challenge(principal_id=self.wallet.principal_id,
            subject_id="agent-a", action=ACTION, now=self.at(2))
        presentation = create_presentation(bundle=self.bundle, mandate_id="grant-a",
            subject_id="agent-a", subject_key=self.subject_key, action=ACTION,
            receiver_challenge=challenge, now=self.at(2))
        entered, release = threading.Event(), threading.Event()
        result = {}
        def held_write(path, value, **kwargs):
            if Path(path) == old.ledger_path:
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("test hold")
            return atomic_write_json(path, value, **kwargs)
        def run():
            result.update(old.execute({"presentation": presentation, "action": ACTION, "release": "old-held"}))
        with patch("openline_wallet.receiver.utc_now", return_value=self.at(2)), \
             patch.object(module, "atomic_write_json", side_effect=held_write):
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(entered.wait(5))
            revoked = self.revoke(at=3)
            old.gate.admit_bundle(revoked, now=self.at(4))
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result["effect_applied"])
        self.assertEqual(result["decision"], "ALLOWED")
        self.assertEqual(strict_json_load(old.ledger_path)[0]["release"], "old-held")
        self.assertEqual(old.gate._admitted[self.wallet.principal_id].timeline.mandates["grant-a"]["status"], "REVOKED")

    def test_real_http_hold_close_release(self):
        """The actual ThreadingHTTPServer must fence a request already in /execute."""
        now = utc_now()
        wallet = Wallet.create(self.root/"http-wallet", now=now)
        key = Ed25519PrivateKey.generate()
        wallet.grant(subject_id="http-agent", subject_public_key=public_key_hex(key),
            scopes=[ACTION], expires_at=now+timedelta(hours=1), now=now, mandate_id="http-grant")
        bundle = wallet.export_bundle()
        runtime = ReceiverRuntime.create(gate_id="http-test", principal_id=wallet.principal_id,
            root_public_key=wallet.root_public_key, ledger_path=self.root/"http"/"effects.json",
            receipts_dir=self.root/"http"/"receipts")
        entered, release = threading.Event(), threading.Event()
        def hold(_ticket):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test hold")
        runtime.before_effect = hold
        server = build_http_server(runtime=runtime, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}"
        def post(path, body):
            request = Request(url+path, data=json.dumps(body).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request, timeout=5) as response:
                return json.loads(response.read())
        try:
            post("/admit", {"bundle": bundle})
            token = post("/challenge", {"principal_id": wallet.principal_id,
                "subject_id": "http-agent", "action": ACTION})["challenge"]
            presentation = create_presentation(bundle=bundle, mandate_id="http-grant",
                subject_id="http-agent", subject_key=key, action=ACTION, receiver_challenge=token)
            result = {}
            def run():
                result.update(post("/execute", {"presentation": presentation,
                    "action": ACTION, "release": "held-http"}))
            worker = threading.Thread(target=run)
            worker.start()
            self.assertTrue(entered.wait(5))
            wallet.revoke("http-grant")
            revoked = wallet.export_bundle()
            closure = post("/close", {"bundle": revoked, "mandate_id": "http-grant"})
            self.assertEqual(closure["status"], "EFFECT_CLOSED")
            release.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(result["reason_codes"], ["MANDATE_REVOKED"])
            self.assertFalse(result["effect_applied"])
            self.assertFalse((self.root/"http"/"effects.json").exists())
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == "__main__":
    unittest.main()
