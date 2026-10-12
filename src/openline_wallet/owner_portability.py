"""OWNER-PORTABILITY-001: offline website policy adapter, not a new Gate.

Owner consent is an experiment-specific signed document. Existing Wallet and
ReferenceGate remain the only mandate/holder authorization machinery.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import pretty_json, strict_json_load
from .clock import utc_now, isoformat, parse_time
from .crypto import public_key_hex, record_hash, sign_record, verify_record
from .errors import WalletError
from .receiver import ReferenceGate, create_presentation
from .wallet import Wallet, verify_bundle

PREFERENCES = {
    "advertising_data_sharing": False,
    "financial_obligations_require_explicit_approval": True,
    "automatic_changed_terms_acceptance": False,
}


def require(condition, reason):
    if not condition:
        raise WalletError(reason)


def signed(record, key):
    require(verify_record(record, expected_public_key=key)[0], "SIGNATURE_INVALID")


def action_for(terms, operation="reserve-demo-slot"):
    # Digest binds all signed terms, including the site, version and obligations.
    return "reserve:" + record_hash({"terms": terms, "operation": operation})


class OwnerDecisions:
    """Owner-controlled append-only consent history, separate from mandates."""
    def __init__(self, wallet, now):
        self.wallet = wallet
        self.records = []
        self.append("PREFERENCES", {"constraints": PREFERENCES}, now)

    def append(self, kind, data, now, key=None):
        record = sign_record({
            "schema": "owner-portability.owner-decision.v1",
            "principal_id": self.wallet.principal_id,
            "sequence": len(self.records) + 1,
            "previous": record_hash(self.records[-1]) if self.records else None,
            "kind": kind, "data": deepcopy(data), "issued_at": isoformat(now),
        }, key or self.wallet.root_key)
        self.records.append(record)
        return record

    def approve(self, terms, now, *, financial=False):
        return self.append("APPROVE", {
            "site": terms["site"], "terms_hash": record_hash(terms),
            "version": terms["version"], "action": action_for(terms),
            "financial_approval": financial,
            "expires_at": isoformat(now + timedelta(minutes=5)),
        }, now)

    def withdraw(self, approval, now):
        return self.append("WITHDRAW", {"approval_hash": record_hash(approval)}, now)


def check_owner_log(records, principal, root, now):
    require(isinstance(records, list) and bool(records), "OWNER_LOG_MISSING")
    previous = None
    for index, item in enumerate(records, 1):
        signed(item, root)
        require(set(item) == {"schema", "principal_id", "sequence", "previous", "kind", "data", "issued_at", "payload_hash", "signature"}, "OWNER_RECORD_SHAPE")
        require(item["schema"] == "owner-portability.owner-decision.v1", "OWNER_SCHEMA")
        require(item["principal_id"] == principal and item["sequence"] == index and item["previous"] == previous, "OWNER_CHAIN_INVALID")
        require(parse_time(item["issued_at"]) <= now, "OWNER_DECISION_FUTURE")
        if index > 1:
            require(parse_time(records[index-2]["issued_at"]) <= parse_time(item["issued_at"]), "OWNER_TIME_ORDER")
        require(item["kind"] in {"PREFERENCES", "APPROVE", "WITHDRAW", "REFUSE"}, "OWNER_KIND")
        previous = record_hash(item)
    require(records[0]["kind"] == "PREFERENCES" and records[0]["data"] == {"constraints": PREFERENCES}, "PREFERENCES_INVALID")
    require(all(r["kind"] != "PREFERENCES" for r in records[1:]), "PREFERENCES_REPLACED")
    return previous


def policy_reason(terms, terms_key, records, principal, root, consent_hash, action, now, *, recognizes=True, declines=False):
    if not recognizes:
        return "WALLET_NOT_RECOGNIZED"
    if declines:
        return "ARRANGEMENT_DECLINED"
    try:
        signed(terms, terms_key)
        require(set(terms) == {"schema", "site", "version", "advertising_data_sharing", "new_financial_obligation", "payload_hash", "signature"}, "TERMS_SHAPE")
        require(terms["schema"] == "owner-portability.terms.v1", "TERMS_SCHEMA")
        require(type(terms["advertising_data_sharing"]) is bool and type(terms["new_financial_obligation"]) is bool, "TERMS_FLAGS")
        check_owner_log(records, principal, root, now)
        require(action == action_for(terms), "ACTION_TERMS_MISMATCH")
        require(not terms["advertising_data_sharing"], "ADVERTISING_SHARING_REFUSED")
        approvals = {record_hash(r): r for r in records if r["kind"] == "APPROVE"}
        approval = approvals.get(consent_hash)
        require(approval is not None, "CONSENT_MISSING")
        require(not any(r["kind"] == "WITHDRAW" and r["data"].get("approval_hash") == consent_hash for r in records), "CONSENT_WITHDRAWN")
        data = approval["data"]
        require(set(data) == {"site", "terms_hash", "version", "action", "financial_approval", "expires_at"}, "CONSENT_SHAPE")
        require(data["site"] == terms["site"] and data["terms_hash"] == record_hash(terms) and data["version"] == terms["version"] and data["action"] == action, "CONSENT_BINDING_MISMATCH")
        require(parse_time(data["expires_at"]) > now, "CONSENT_EXPIRED")
        require(type(data["financial_approval"]) is bool, "CONSENT_FINANCIAL_INVALID")
        require(not terms["new_financial_obligation"] or data["financial_approval"], "FINANCIAL_APPROVAL_REQUIRED")
    except (WalletError, KeyError, TypeError, ValueError) as exc:
        return getattr(exc, "code", "POLICY_MALFORMED")
    return None


class Website:
    """Receiver-owned terms checks followed by the unchanged ReferenceGate."""
    def __init__(self, site, principal, root, *, advertising=False, recognizes=True, declines=False):
        self.site, self.principal, self.root = site, principal, root
        self.recognizes, self.declines = recognizes, declines
        self.gate = ReferenceGate(site)
        if recognizes:
            self.gate.pin_principal(principal, root)
        self.terms = self.make_terms("v1", advertising=advertising)
        self.owner_history = []
        self.effects = []

    def make_terms(self, version, *, advertising=False, financial=False):
        return sign_record({"schema": "owner-portability.terms.v1", "site": self.site,
            "version": version, "advertising_data_sharing": advertising,
            "new_financial_obligation": financial}, self.gate.gate_key)

    def transact(self, bundle, records, consent_hash, presentation, now):
        action = action_for(self.terms)
        gate_receipt = None
        reason = policy_reason(self.terms, self.gate.public_key, records, self.principal,
            self.root, consent_hash, action, now, recognizes=self.recognizes, declines=self.declines)
        # Observe withdrawals even on refused requests. Never roll back a
        # previously observed owner decision chain merely because policy refused.
        if self.recognizes and not self.declines:
            try:
                check_owner_log(records, self.principal, self.root, now)
                if records[:len(self.owner_history)] != self.owner_history:
                    reason = "OWNER_HEAD_STALE_OR_FORKED"
                else:
                    self.owner_history = deepcopy(records)
            except (WalletError, KeyError, TypeError, ValueError):
                pass  # policy_reason already reports the invalid owner evidence
        # Retain verified revocations even when the proposed terms/consent
        # are refused. A refusal must not make a receiver forget authority.
        if self.recognizes and not self.declines:
            try:
                self.gate.admit_bundle(bundle, now=now)
            except WalletError as exc:
                if reason is None:
                    reason = exc.code
        if reason is None:
            gate_receipt = self.gate.evaluate(presentation, expected_action=action, now=now)
            if gate_receipt["decision"] != "ALLOWED":
                reason = gate_receipt["reason_codes"][0]
        effect = None
        if reason is None:
            effect = sign_record({"schema": "owner-portability.simulated-effect.v1",
                "site": self.site, "action": action, "index": len(self.effects) + 1,
                "gate_receipt_hash": record_hash(gate_receipt),
                "consent_hash": consent_hash, "description": "synthetic slot reserved; no external effect"}, self.gate.gate_key)
            self.effects.append(effect)
        return sign_record({"schema": "owner-portability.transaction.v1", "site": self.site,
            "decided_at": isoformat(now), "decision": "REFUSED" if reason else "PERMITTED",
            "reason": reason, "terms": deepcopy(self.terms), "owner_log": deepcopy(records),
            "consent_hash": consent_hash, "bundle": deepcopy(bundle),
            "presentation": deepcopy(presentation), "gate_receipt": gate_receipt,
            "effect": effect}, self.gate.gate_key)


def present(site, bundle, subject, key, mandate, now, *, action=None):
    action = action or action_for(site.terms)
    challenge = site.gate.issue_challenge(principal_id=bundle["principal"]["principal_id"],
        subject_id=subject, action=action, now=now)
    return create_presentation(bundle=bundle, mandate_id=mandate, subject_id=subject,
        subject_key=key, action=action, receiver_challenge=challenge, now=now)


def verify_packet(packet, anchors):
    """Read-only independent verification using public pins; no keys or services.

    Signed receiver refusal is evidence of that receiver's decision. Successful
    effects additionally require public mandate, consent and holder checks.
    No claim that signed statements prove an external event or log completeness.
    """
    signed(packet, anchors["owner_root"])
    require(packet["pins"] == anchors, "PIN_MISMATCH")
    principal = anchors["principal"]
    transactions = packet["transactions"]
    heads, owner_heads, used, counts = {}, {}, set(), {}
    gate_receipts = []
    effects = []
    for tx in transactions:
        site = tx["site"]
        pin = anchors["sites"][site]
        key = pin["public_key"]
        signed(tx, key)
        now = parse_time(tx["decided_at"])
        reason = policy_reason(tx["terms"], key, tx["owner_log"], principal,
            anchors["owner_root"], tx["consent_hash"], action_for(tx["terms"]), now,
            recognizes=pin["recognizes"], declines=pin["declines"])
        require(tx["terms"]["site"] == site, "SITE_MISMATCH")
        if reason is not None:
            require(tx["decision"] == "REFUSED" and tx["reason"] == reason, "POLICY_DECISION_MISMATCH")
            require(tx["gate_receipt"] is None and tx["effect"] is None, "REFUSAL_EFFECT")
            continue
        require(tx["owner_log"][:len(owner_heads.get(site, []))] == owner_heads.get(site, []), "OWNER_HEAD_STALE_OR_FORKED")
        owner_heads[site] = tx["owner_log"]
        bundle, timeline = verify_bundle(tx["bundle"], now=now)
        require(bundle["principal"] == {"principal_id": principal, "root_public_key": anchors["owner_root"]}, "BUNDLE_PRINCIPAL")
        old = heads.get(site, [])
        require(bundle["events"][:len(old)] == old, "BUNDLE_HEAD_STALE")
        heads[site] = bundle["events"]
        receipt = tx["gate_receipt"]
        require(receipt is not None, "GATE_RECEIPT_MISSING")
        signed(receipt, key)
        require(receipt["gate_id"] == site and receipt["gate_public_key"] == key, "GATE_PIN_MISMATCH")
        require(receipt["action"] == action_for(tx["terms"]), "GATE_ACTION_MISMATCH")
        gate_receipts.append(receipt)
        if tx["decision"] == "REFUSED":
            require(receipt["decision"] == "STOPPED" and tx["reason"] == receipt["reason_codes"][0] and tx["effect"] is None, "GATE_REFUSAL_MISMATCH")
            if tx["reason"] == "MANDATE_REVOKED":
                p = tx["presentation"]
                signed(p, p["subject_public_key"])
                require(receipt["presentation_hash"] == record_hash(p), "REFUSAL_PRESENTATION_BINDING")
                require(timeline.mandates[p["mandate_id"]]["status"] == "REVOKED", "REVOCATION_MISSING")
            continue
        require(tx["decision"] == "PERMITTED" and tx["reason"] is None and receipt["decision"] == "ALLOWED" and receipt["reason_codes"] == [], "GATE_NOT_ALLOWED")
        p = tx["presentation"]
        signed(p, p["subject_public_key"])
        require(p["principal_id"] == principal and p["action"] == receipt["action"] and p["bundle_head_hash"] == timeline.head_hash, "PRESENTATION_BINDING")
        require(receipt["presentation_hash"] == record_hash(p) and receipt["subject_id"] == p["subject_id"] and receipt["mandate_id"] == p["mandate_id"], "RECEIPT_PRESENTATION_BINDING")
        challenge = (site, p["receiver_challenge"])
        require(challenge not in used, "CHALLENGE_REPLAY")
        used.add(challenge)
        require(abs((now - parse_time(p["issued_at"])).total_seconds()) <= 60, "PRESENTATION_STALE")
        mandate = timeline.mandates[p["mandate_id"]]
        require(mandate["status"] == "ACTIVE" and parse_time(mandate["expires_at"]) > now, "MANDATE_INACTIVE")
        require(mandate["subject_id"] == p["subject_id"] and mandate["subject_public_key"] == p["subject_public_key"] and p["action"] in mandate["scopes"], "MANDATE_BINDING")
        effect = tx["effect"]
        signed(effect, key)
        counts[site] = counts.get(site, 0) + 1
        require(set(effect) == {"schema", "site", "action", "index", "gate_receipt_hash", "consent_hash", "description", "payload_hash", "signature"}, "EFFECT_SHAPE")
        require(effect["schema"] == "owner-portability.simulated-effect.v1" and effect["description"] == "synthetic slot reserved; no external effect", "EFFECT_SCHEMA")
        require(receipt["decided_at"] == tx["decided_at"], "RECEIPT_TIME_BINDING")
        require(effect == {**effect, "site": site, "action": p["action"], "index": counts[site], "gate_receipt_hash": record_hash(receipt), "consent_hash": tx["consent_hash"]}, "EFFECT_BINDING")
        effects.append(effect)
    final, _ = verify_bundle(packet["final_bundle"], require_fresh=False)
    require(final["principal"] == {"principal_id": principal, "root_public_key": anchors["owner_root"]}, "FINAL_PRINCIPAL")
    initial, _ = verify_bundle(packet["initial_bundle"], require_fresh=False)
    require(final["events"][:len(initial["events"])] == initial["events"], "HISTORY_LOST")
    require(all(r in final["receipts"] for r in gate_receipts), "RECEIPT_LOST")
    check_owner_log(packet["owner_log"], principal, anchors["owner_root"], parse_time(packet["completed_at"]))
    require(all(tx["owner_log"] == packet["owner_log"][:len(tx["owner_log"])] for tx in transactions), "OWNER_HISTORY_LOST")
    require(all(tx["bundle"]["events"] == final["events"][:len(tx["bundle"]["events"])] for tx in transactions), "WALLET_HISTORY_LOST")
    expected = ["PERMITTED", "REFUSED", "REFUSED", "REFUSED", "REFUSED", "PERMITTED", "REFUSED", "PERMITTED"]
    require([t["decision"] for t in transactions] == expected, "STORY_OUTCOME")
    require(transactions[1]["reason"] == "ADVERTISING_SHARING_REFUSED" and transactions[4]["reason"] == "MANDATE_REVOKED" and transactions[6]["reason"] == "CONSENT_BINDING_MISMATCH", "STORY_REFUSALS")
    require(transactions[0]["bundle"] == transactions[1]["bundle"], "SHARED_AUTHORITY_MISSING")
    require(transactions[0]["presentation"]["subject_id"] == "agent-a" and transactions[5]["presentation"]["subject_id"] == "agent-b", "AGENT_SWITCH_MISSING")
    # B's policy refusal precedes an action Gate decision; explicitly verify that
    # B can independently admit the SAME authority, rather than infer from refusal.
    require(initial["principal"] == {"principal_id": principal, "root_public_key": anchors["owner_root"]}, "INITIAL_PRINCIPAL")
    require(len(packet["admissions"]) == 2, "ADMISSIONS_MISSING")
    for admission, site in zip(packet["admissions"], ("site-a", "site-b")):
        signed(admission, anchors["sites"][site]["public_key"])
        require(admission["site"] == site and admission["bundle_hash"] == record_hash(initial), "ADMISSION_BINDING")
        receiver = ReferenceGate(site)
        receiver.pin_principal(principal, anchors["owner_root"])
        result = receiver.admit_bundle(initial, now=parse_time(admission["admitted_at"]))
        require(result == admission["result"], "ADMISSION_RESULT")
    require(any(r["kind"] == "REFUSE" and r["data"] == {
        "site": "site-b", "terms_hash": record_hash(transactions[1]["terms"]),
        "action": action_for(transactions[1]["terms"]), "reason": "ADVERTISING_SHARING_REFUSED",
        "receiver_transaction_hash": record_hash(transactions[1])}
        for r in packet["owner_log"]), "OWNER_REFUSAL_MISSING")
    require(packet["baseline"] == {"a_local_token_at_a": "PERMITTED", "a_local_token_at_b": "REFUSED", "b_separate_registration": "PERMITTED"}, "BASELINE_MISMATCH")
    return {"verdict": "OWNER_PORTABILITY_LOCAL_PASS", "transactions": len(transactions),
        "permitted": len(effects), "refused": len(transactions)-len(effects),
        "wallet_events": len(final["events"]), "gate_receipts": len(gate_receipts),
        "owner_decisions": len(packet["owner_log"]), "independent_receivers_verified": 2}


def run(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    now = utc_now()
    with tempfile.TemporaryDirectory() as local:
        wallet = Wallet.create(Path(local)/"owner", now=now)
        owner = OwnerDecisions(wallet, now)
        akey, bkey = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        sites = [Website("site-a", wallet.principal_id, wallet.root_public_key),
            Website("site-b", wallet.principal_id, wallet.root_public_key, advertising=True),
            Website("unknown-site", wallet.principal_id, wallet.root_public_key, recognizes=False),
            Website("declining-site", wallet.principal_id, wallet.root_public_key, declines=True)]
        a, b, unknown, decline = sites
        consent = owner.approve(a.terms, now)
        wallet.grant(subject_id="agent-a", subject_public_key=public_key_hex(akey),
            scopes=[action_for(a.terms), action_for(b.terms)], expires_at=now+timedelta(hours=1), now=now, mandate_id="mandate-a")
        initial = wallet.export_bundle(now=now)
        admissions = []
        for site in (a, b):
            admission = site.gate.admit_bundle(initial, now=now)
            admissions.append(sign_record({"schema": "owner-portability.admission.v1",
                "site": site.site, "bundle_hash": record_hash(initial),
                "admitted_at": isoformat(now), "result": admission}, site.gate.gate_key))
        txs = []
        def attempt(site, bundle, subject, key, mandate, approval):
            tx = site.transact(bundle, owner.records, record_hash(approval) if approval else None,
                present(site, bundle, subject, key, mandate, now), now)
            txs.append(tx)
            if tx["gate_receipt"]:
                wallet.add_receipt(tx["gate_receipt"])
            return tx
        attempt(a, initial, "agent-a", akey, "mandate-a", consent)
        refusal = attempt(b, initial, "agent-a", akey, "mandate-a", None)
        owner.append("REFUSE", {"site": b.site, "terms_hash": record_hash(b.terms),
            "action": action_for(b.terms), "reason": "ADVERTISING_SHARING_REFUSED",
            "receiver_transaction_hash": record_hash(refusal)}, now)
        attempt(unknown, initial, "agent-a", akey, "mandate-a", None)
        attempt(decline, initial, "agent-a", akey, "mandate-a", None)
        wallet.revoke("agent-a", reason="OWNER_AGENT_SWITCH", now=now)
        wallet.grant(subject_id="agent-b", subject_public_key=public_key_hex(bkey),
            scopes=[action_for(a.terms)], expires_at=now+timedelta(hours=1), now=now, mandate_id="mandate-b")
        switched = wallet.export_bundle(now=now)
        attempt(a, switched, "agent-a", akey, "mandate-a", consent)
        attempt(a, switched, "agent-b", bkey, "mandate-b", consent)
        a.terms = a.make_terms("v2")
        attempt(a, switched, "agent-b", bkey, "mandate-b", consent)
        new_consent = owner.approve(a.terms, now)
        wallet.revoke("agent-b", reason="EXACT_TERMS_REAPPROVAL", now=now)
        wallet.grant(subject_id="agent-b", subject_public_key=public_key_hex(bkey),
            scopes=[action_for(a.terms)], expires_at=now+timedelta(hours=1), now=now, mandate_id="mandate-b-v2")
        attempt(a, wallet.export_bundle(now=now), "agent-b", bkey, "mandate-b-v2", new_consent)
        # A deliberately minimal site-local token registry, not an OAuth model.
        registries = {"a": {"token-a": "owner"}, "b": {"token-b": "owner"}}
        def baseline(site, token):
            return "PERMITTED" if token in registries[site] else "REFUSED"
        pins = {"principal": wallet.principal_id, "owner_root": wallet.root_public_key,
            "sites": {s.site: {"public_key": s.gate.public_key, "recognizes": s.recognizes,
                "declines": s.declines} for s in sites}}
        packet = sign_record({"schema": "owner-portability.evidence.v1", "pins": pins,
            "initial_bundle": initial, "admissions": admissions,
            "final_bundle": wallet.export_bundle(now=now),
            "owner_log": owner.records, "transactions": txs, "completed_at": isoformat(now),
            "baseline": {"a_local_token_at_a": baseline("a", "token-a"),
                "a_local_token_at_b": baseline("b", "token-a"),
                "b_separate_registration": baseline("b", "token-b")}}, wallet.root_key)
        (output/"trust-anchors.json").write_text(pretty_json(pins))
        (output/"evidence.json").write_text(pretty_json(packet))
        # Evidence is written before checking PASS; failures remain reviewable.
        try:
            result = verify_packet(packet, pins)
        except Exception as exc:
            (output/"result.json").write_text(pretty_json({"verdict": "FAIL", "error": str(exc)}))
            raise
        (output/"result.json").write_text(pretty_json(result))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("run")
    demo.add_argument("output", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("evidence", type=Path)
    verify.add_argument("--trust-anchors", required=True, type=Path)
    args = parser.parse_args()
    result = run(args.output) if args.command == "run" else verify_packet(
        strict_json_load(args.evidence), strict_json_load(args.trust_anchors))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
