"""Offline negative cases: no protected effect on every rejected transaction."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.clock import utc_now
from openline_wallet.crypto import public_key_hex, record_hash, sign_record
from openline_wallet.errors import WalletError
from openline_wallet.owner_portability import (
    OwnerDecisions, Website, action_for, present, run, verify_packet,
)
from openline_wallet.canonical import strict_json_load
from openline_wallet.wallet import Wallet


class OwnerPortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = utc_now()
        self.wallet = Wallet.create(Path(self.temp.name)/"wallet", now=self.now)
        self.agent = Ed25519PrivateKey.generate()
        self.owner = OwnerDecisions(self.wallet, self.now)
        self.site = Website("test-site", self.wallet.principal_id, self.wallet.root_public_key)
        self.approval = self.owner.approve(self.site.terms, self.now)
        self.grant()
        self.bundle = self.wallet.export_bundle(now=self.now)

    def grant(self, mandate="test-mandate", key=None, subject="agent-a"):
        self.wallet.grant(subject_id=subject, subject_public_key=public_key_hex(key or self.agent),
            scopes=[action_for(self.site.terms)], expires_at=self.now+timedelta(hours=1),
            now=self.now, mandate_id=mandate)

    def request(self, *, bundle=None, records=None, approval=None, key=None,
                presentation=None, now=None, subject="agent-a", mandate="test-mandate"):
        bundle = bundle if bundle is not None else self.bundle
        now = now or self.now
        return self.site.transact(bundle, records if records is not None else self.owner.records,
            record_hash(approval if approval is not None else self.approval),
            presentation if presentation is not None else present(self.site, bundle, subject,
                key or self.agent, mandate, now), now)

    def refused(self, tx, reason, before=0):
        self.assertEqual(tx["decision"], "REFUSED")
        self.assertEqual(tx["reason"], reason)
        self.assertIsNone(tx["effect"])
        self.assertEqual(len(self.site.effects), before)
        evidence = os.environ.get("OWNER_PORTABILITY_TEST_EVIDENCE")
        if evidence:
            from openline_wallet.canonical import pretty_json
            directory = Path(evidence)
            directory.mkdir(parents=True, exist_ok=True)
            # Preserve receiver-signed refusals, including attack inputs, public
            # pins and effect count. No subject or owner private keys.
            existing = list(directory.glob(self._testMethodName + "-*.json"))
            (directory / f"{self._testMethodName}-{len(existing)+1}.json").write_text(pretty_json({
                "test": self._testMethodName, "receiver_key": self.site.gate.public_key,
                "owner_root": self.wallet.root_public_key,
                "effect_count_before": before, "effect_count_after": len(self.site.effects),
                "transaction": tx}))

    def test_matching_explicit_consent_permits(self):
        self.assertEqual(self.request()["decision"], "PERMITTED")
        self.assertEqual(len(self.site.effects), 1)

    def test_preferences_are_not_consent(self):
        self.refused(self.request(records=self.owner.records[:1]), "CONSENT_MISSING")

    def test_unsigned_preferences_fail(self):
        records = deepcopy(self.owner.records)
        del records[0]["signature"]
        self.refused(self.request(records=records), "SIGNATURE_INVALID")

    def test_forged_wallet_authority(self):
        bundle = deepcopy(self.bundle)
        bundle["events"][0]["data"]["scopes"] = ["everything"]
        self.refused(self.request(bundle=bundle), "BUNDLE_SIGNATURE_INVALID")

    def test_agent_self_approval(self):
        records = deepcopy(self.owner.records)
        body = {k: v for k, v in records[-1].items() if k not in {"signature", "payload_hash"}}
        records[-1] = sign_record(body, self.agent)
        self.refused(self.request(records=records, approval=records[-1]), "SIGNATURE_INVALID")

    def test_wrong_holder_key(self):
        self.refused(self.request(key=Ed25519PrivateKey.generate()), "SUBJECT_BINDING_MISMATCH")

    def test_replayed_challenge(self):
        p = present(self.site, self.bundle, "agent-a", self.agent, "test-mandate", self.now)
        self.assertEqual(self.request(presentation=p)["decision"], "PERMITTED")
        self.refused(self.request(presentation=p), "PRESENTATION_REPLAYED", before=1)

    def test_tampered_terms(self):
        self.site.terms["version"] = "forged-v2"
        self.refused(self.request(), "SIGNATURE_INVALID")

    def test_changed_terms_require_new_approval_and_mandate(self):
        self.site.terms = self.site.make_terms("v2")
        self.refused(self.request(), "CONSENT_BINDING_MISMATCH")
        consent = self.owner.approve(self.site.terms, self.now)
        self.refused(self.request(approval=consent), "ACTION_OUTSIDE_MANDATE")
        self.wallet.revoke("agent-a", now=self.now)
        self.grant("v2-mandate")
        self.bundle = self.wallet.export_bundle(now=self.now)
        self.assertEqual(self.request(approval=consent, mandate="v2-mandate")["decision"], "PERMITTED")

    def test_withdrawn_consent(self):
        self.owner.withdraw(self.approval, self.now)
        self.refused(self.request(), "CONSENT_WITHDRAWN")

    def test_withdrawal_cannot_be_rolled_back(self):
        old = deepcopy(self.owner.records)
        self.owner.withdraw(self.approval, self.now)
        self.refused(self.request(), "CONSENT_WITHDRAWN")
        self.refused(self.request(records=old), "OWNER_HEAD_STALE_OR_FORKED")

    def test_expired_consent(self):
        self.refused(self.request(now=self.now+timedelta(minutes=6)), "CONSENT_EXPIRED")

    def test_advertising_requirement_refused_even_if_owner_approves(self):
        self.site.terms = self.site.make_terms("ads", advertising=True)
        approval = self.owner.approve(self.site.terms, self.now)
        self.refused(self.request(approval=approval), "ADVERTISING_SHARING_REFUSED")

    def test_financial_obligation_needs_explicit_approval(self):
        self.site.terms = self.site.make_terms("financial", financial=True)
        approval = self.owner.approve(self.site.terms, self.now)
        self.refused(self.request(approval=approval), "FINANCIAL_APPROVAL_REQUIRED")
        explicit = self.owner.approve(self.site.terms, self.now, financial=True)
        self.wallet.revoke("agent-a", now=self.now)
        self.grant("financial-mandate")
        self.bundle = self.wallet.export_bundle(now=self.now)
        self.assertEqual(self.request(approval=explicit, mandate="financial-mandate")["decision"], "PERMITTED")

    def test_cross_site_approval(self):
        other = Website("other", self.wallet.principal_id, self.wallet.root_public_key)
        approval = self.owner.approve(other.terms, self.now)
        self.refused(self.request(approval=approval), "CONSENT_BINDING_MISMATCH")

    def test_cross_action_approval(self):
        data = deepcopy(self.approval["data"])
        data["action"] = "transfer:funds"
        approval = self.owner.append("APPROVE", data, self.now)
        self.refused(self.request(approval=approval), "CONSENT_BINDING_MISMATCH")

    def test_receiver_declines(self):
        self.site.declines = True
        self.refused(self.request(), "ARRANGEMENT_DECLINED")

    def test_unknown_wallet(self):
        self.site = Website("unrecognized", self.wallet.principal_id,
            self.wallet.root_public_key, recognizes=False)
        self.refused(self.request(), "WALLET_NOT_RECOGNIZED")

    def test_revoked_a_and_independent_b_key(self):
        self.wallet.revoke("agent-a", now=self.now)
        b = Ed25519PrivateKey.generate()
        self.grant("mandate-b", key=b, subject="agent-b")
        self.bundle = self.wallet.export_bundle(now=self.now)
        self.refused(self.request(), "MANDATE_REVOKED")
        # Only B's key and public documents are inputs to the B request.
        self.assertEqual(self.request(subject="agent-b", key=b, mandate="mandate-b")["decision"], "PERMITTED")

    def test_stale_bundle_after_observed_revocation(self):
        old = self.bundle
        self.wallet.revoke("agent-a", now=self.now)
        self.bundle = self.wallet.export_bundle(now=self.now)
        self.refused(self.request(), "MANDATE_REVOKED")
        self.refused(self.request(bundle=old), "BUNDLE_HEAD_STALE")

    def test_revocation_observed_on_policy_refusal_is_retained(self):
        old = self.bundle
        self.wallet.revoke("agent-a", now=self.now)
        self.bundle = self.wallet.export_bundle(now=self.now)
        self.refused(self.request(records=self.owner.records[:1]), "CONSENT_MISSING")
        self.refused(self.request(bundle=old), "BUNDLE_HEAD_STALE")

    def test_stale_presentation(self):
        p = present(self.site, self.bundle, "agent-a", self.agent, "test-mandate", self.now)
        self.refused(self.request(presentation=p, now=self.now+timedelta(seconds=61)), "CHALLENGE_EXPIRED")

    def test_owner_log_fork(self):
        self.assertEqual(self.request()["decision"], "PERMITTED")
        records = deepcopy(self.owner.records)
        data = deepcopy(records[-1]["data"])
        data["financial_approval"] = True
        self.owner.records.pop()
        self.owner.append("APPROVE", data, self.now)
        self.refused(self.request(), "OWNER_HEAD_STALE_OR_FORKED", before=1)

    def test_public_verifier_and_tampering(self):
        output = Path(self.temp.name)/"proof"
        result = run(output)
        self.assertEqual(result["permitted"], 3)
        packet = strict_json_load(output/"evidence.json")
        pins = strict_json_load(output/"trust-anchors.json")
        self.assertEqual(verify_packet(packet, pins), result)
        for field in ("decision", "effect", "owner_log", "terms"):
            forged = deepcopy(packet)
            forged["transactions"][0][field] = None
            with self.assertRaises(WalletError):
                verify_packet(forged, pins)
        wrong_pins = deepcopy(pins)
        wrong_pins["owner_root"] = public_key_hex(self.agent)
        with self.assertRaises(WalletError):
            verify_packet(packet, wrong_pins)
        forged = deepcopy(packet)
        forged["transactions"].pop()
        with self.assertRaises(WalletError):
            verify_packet(forged, pins)

    def test_verifier_rejects_resealed_semantic_forgeries(self):
        from openline_wallet import owner_portability as experiment
        keys = {}
        original = experiment.sign_record
        def capture(body, key):
            keys[public_key_hex(key)] = key
            return original(body, key)
        output = Path(self.temp.name)/"resealed"
        with patch.object(experiment, "sign_record", side_effect=capture):
            run(output)
        packet = strict_json_load(output/"evidence.json")
        pins = strict_json_load(output/"trust-anchors.json")
        def reseal(record):
            key = keys[record["signature"]["public_key"]]
            return original({k: v for k, v in record.items() if k not in {"signature", "payload_hash"}}, key)
        for attack in ("effect-binding", "revoked-mandate", "lost-history", "lost-refusal", "admission-binding", "duplicate-effect"):
            forged = deepcopy(packet)
            if attack == "effect-binding":
                tx = forged["transactions"][0]
                tx["effect"]["consent_hash"] = "forged"
                tx["effect"] = reseal(tx["effect"])
                forged["transactions"][0] = reseal(tx)
            elif attack == "revoked-mandate":
                tx = forged["transactions"][4]
                tx["decision"], tx["reason"] = "PERMITTED", None
                tx["gate_receipt"]["decision"] = "ALLOWED"
                tx["gate_receipt"]["reason_codes"] = []
                tx["gate_receipt"] = reseal(tx["gate_receipt"])
                forged["transactions"][4] = reseal(tx)
            elif attack == "lost-history":
                forged["final_bundle"]["receipts"] = []
                forged["final_bundle"] = reseal(forged["final_bundle"])
            elif attack == "lost-refusal":
                forged["transactions"].pop(1)
            elif attack == "admission-binding":
                forged["admissions"][1]["bundle_hash"] = "forged"
                forged["admissions"][1] = reseal(forged["admissions"][1])
            else:
                forged["transactions"].insert(1, deepcopy(forged["transactions"][0]))
            with self.subTest(attack=attack), self.assertRaises((WalletError, KeyError, TypeError)):
                verify_packet(reseal(forged), pins)



if __name__ == "__main__":
    unittest.main()
