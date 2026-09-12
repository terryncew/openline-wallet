from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_wallet.crypto import public_key_hex, record_hash
from openline_wallet.receiver import ReferenceGate, create_presentation
from openline_wallet.wallet import Wallet


BASE = datetime(2026, 9, 12, 19, 30, tzinfo=timezone.utc)
PROTECTED_TRANSFORM = b"payroll summary: engineering band median exceeds support band median"
BENIGN = b"public benefits enrollment reminder"


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        wallet = Wallet.create(Path(tmp) / "wallet", now=BASE)
        keys = {name: Ed25519PrivateKey.generate() for name in ("source", "blocked", "allowed", "clean")}
        policies = {
            "source": ["records:read:payroll", "message:send:peer"],
            "blocked": ["message:receive:peer"],
            "allowed": ["records:read:payroll", "message:receive:peer"],
            "clean": ["message:send:peer"],
        }
        for index, name in enumerate(("source", "blocked", "allowed", "clean"), start=1):
            wallet.grant(
                subject_id=f"agent-{name}",
                subject_public_key=public_key_hex(keys[name]),
                scopes=policies[name],
                expires_at=BASE + timedelta(hours=1),
                now=BASE + timedelta(seconds=index),
                mandate_id=f"mandate-{name}",
            )

        bundle = wallet.export_bundle(now=BASE + timedelta(seconds=5))
        gate = ReferenceGate("route-authority-gate-001")
        gate.pin_principal(wallet.principal_id, wallet.root_public_key)
        gate.protect_action(
            "records:read:payroll",
            required_recipient_scopes=["records:read:payroll"],
        )
        admission = gate.admit_bundle(bundle, now=BASE + timedelta(seconds=5))

        counter = 6

        def evaluate(name: str, action: str) -> dict[str, Any]:
            nonlocal counter
            now = BASE + timedelta(seconds=counter)
            counter += 1
            challenge = gate.issue_challenge(
                principal_id=wallet.principal_id,
                subject_id=f"agent-{name}",
                action=action,
                now=now,
            )
            presentation = create_presentation(
                bundle=bundle,
                mandate_id=f"mandate-{name}",
                subject_id=f"agent-{name}",
                subject_key=keys[name],
                action=action,
                receiver_challenge=challenge,
                now=now,
            )
            return gate.evaluate(presentation, expected_action=action, now=now)

        delivery_witness: list[dict[str, Any]] = []

        blocked_direct = evaluate("blocked", "records:read:payroll")

        clean_send = evaluate("clean", "message:send:peer")
        clean_route = gate.evaluate_route(
            clean_send,
            expected_action="message:send:peer",
            recipient_subject_id="agent-blocked",
            now=BASE + timedelta(seconds=counter),
        )
        counter += 1
        if clean_send["decision"] == "ALLOWED" and clean_route["decision"] == "ALLOWED":
            delivery_witness.append(
                {
                    "kind": "benign-clean-sender",
                    "recipient": "agent-blocked",
                    "payload_hash": sha256(BENIGN),
                }
            )

        source_read = evaluate("source", "records:read:payroll")
        carried_after_read = list(gate.carried_scopes(wallet.principal_id, "agent-source"))

        protected_send_blocked = evaluate("source", "message:send:peer")
        blocked_route = gate.evaluate_route(
            protected_send_blocked,
            expected_action="message:send:peer",
            recipient_subject_id="agent-blocked",
            now=BASE + timedelta(seconds=counter),
        )
        counter += 1
        if protected_send_blocked["decision"] == "ALLOWED" and blocked_route["decision"] == "ALLOWED":
            delivery_witness.append(
                {
                    "kind": "protected-transform-to-unauthorized",
                    "recipient": "agent-blocked",
                    "payload_hash": sha256(PROTECTED_TRANSFORM),
                }
            )

        protected_send_allowed = evaluate("source", "message:send:peer")
        allowed_route = gate.evaluate_route(
            protected_send_allowed,
            expected_action="message:send:peer",
            recipient_subject_id="agent-allowed",
            now=BASE + timedelta(seconds=counter),
        )
        counter += 1
        if protected_send_allowed["decision"] == "ALLOWED" and allowed_route["decision"] == "ALLOWED":
            delivery_witness.append(
                {
                    "kind": "protected-transform-to-authorized",
                    "recipient": "agent-allowed",
                    "payload_hash": sha256(PROTECTED_TRANSFORM),
                }
            )

        controls_ok = (
            admission["decision"] == "BUNDLE_ADMITTED"
            and blocked_direct["decision"] == "STOPPED"
            and blocked_direct["reason_codes"] == ["ACTION_OUTSIDE_MANDATE"]
            and clean_send["decision"] == "ALLOWED"
            and clean_route["decision"] == "ALLOWED"
            and source_read["decision"] == "ALLOWED"
            and carried_after_read == ["records:read:payroll"]
            and protected_send_blocked["decision"] == "ALLOWED"
            and protected_send_allowed["decision"] == "ALLOWED"
            and allowed_route["decision"] == "ALLOWED"
            and any(row["kind"] == "benign-clean-sender" for row in delivery_witness)
            and any(row["kind"] == "protected-transform-to-authorized" for row in delivery_witness)
        )
        unauthorized_delivered = any(
            row["kind"] == "protected-transform-to-unauthorized"
            for row in delivery_witness
        )

        if not controls_ok:
            verdict = "INCONCLUSIVE_SETUP_OR_CONTROL_FAILURE"
        elif blocked_route["decision"] == "STOPPED" and blocked_route["reason_codes"] == [
            "RECIPIENT_LACKS_SOURCE_AUTHORITY"
        ] and not unauthorized_delivered:
            verdict = "ROUTE_AUTHORITY_REPAIR_PASS"
        else:
            verdict = "ROUTE_AUTHORITY_REPAIR_FAILED"

        result = {
            "schema": "openline.route_authority_gate.result.v1",
            "experiment_id": "ROUTE-AUTHORITY-GATE-001",
            "base_time": BASE.isoformat(),
            "principal_id": wallet.principal_id,
            "bundle_head_hash": bundle["head"]["event_hash"],
            "protected_transform_hash": sha256(PROTECTED_TRANSFORM),
            "benign_hash": sha256(BENIGN),
            "owner_policy": {
                "protected_source_action": "records:read:payroll",
                "required_recipient_scopes": ["records:read:payroll"],
                "generic_peer_messaging_remains_separately_authorized": True,
                "declassification_supported": False,
            },
            "cases": {
                "blocked_direct_read": blocked_direct,
                "clean_sender_action": clean_send,
                "clean_sender_route": clean_route,
                "source_protected_read": source_read,
                "source_carried_scopes_after_read": carried_after_read,
                "source_generic_send_to_blocked": protected_send_blocked,
                "source_route_to_blocked": blocked_route,
                "source_generic_send_to_allowed": protected_send_allowed,
                "source_route_to_allowed": allowed_route,
            },
            "delivery_witness": delivery_witness,
            "unauthorized_protected_delivery": unauthorized_delivered,
            "verdict": verdict,
        }
        result["result_hash"] = record_hash(result)
        write_json(out / "result.json", result)
        write_json(out / "delivery-witness.json", delivery_witness)
        write_json(
            out / "summary.json",
            {
                "experiment_id": result["experiment_id"],
                "unauthorized_protected_delivery": unauthorized_delivered,
                "verdict": verdict,
                "result_hash": result["result_hash"],
            },
        )
        print(json.dumps({"verdict": verdict, "result_hash": result["result_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
