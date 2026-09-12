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


BASE = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
PROTECTED_PAYLOAD = b"internal estimate ref RSRCH-5519: 27.4bn / 0.62%"
BENIGN_PAYLOAD = b"public note: card spend statistics already approved for sharing"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def evaluate_action(
    *,
    gate: ReferenceGate,
    bundle: dict[str, Any],
    principal_id: str,
    mandate_id: str,
    subject_id: str,
    subject_key: Ed25519PrivateKey,
    action: str,
    now: datetime,
) -> dict[str, Any]:
    challenge = gate.issue_challenge(
        principal_id=principal_id,
        subject_id=subject_id,
        action=action,
        now=now,
    )
    presentation = create_presentation(
        bundle=bundle,
        mandate_id=mandate_id,
        subject_id=subject_id,
        subject_key=subject_key,
        action=action,
        receiver_challenge=challenge,
        now=now,
    )
    return gate.evaluate(presentation, expected_action=action, now=now)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        wallet = Wallet.create(Path(tmp) / "wallet", now=BASE)
        key_a = Ed25519PrivateKey.generate()
        key_b = Ed25519PrivateKey.generate()

        wallet.grant(
            subject_id="agent-a",
            subject_public_key=public_key_hex(key_a),
            scopes=["data:read:payments-research", "message:send:peer"],
            expires_at=BASE + timedelta(hours=1),
            now=BASE + timedelta(seconds=1),
            mandate_id="mandate-agent-a",
        )
        wallet.grant(
            subject_id="agent-b",
            subject_public_key=public_key_hex(key_b),
            scopes=["data:read:fintech-memo", "message:receive:peer", "memo:write:fintech-memo"],
            expires_at=BASE + timedelta(hours=1),
            now=BASE + timedelta(seconds=2),
            mandate_id="mandate-agent-b",
        )
        bundle = wallet.export_bundle(now=BASE + timedelta(seconds=3))

        gate = ReferenceGate("route-authority-cold-001")
        gate.pin_principal(wallet.principal_id, wallet.root_public_key)
        admission = gate.admit_bundle(bundle, now=BASE + timedelta(seconds=3))

        direct_b = evaluate_action(
            gate=gate,
            bundle=bundle,
            principal_id=wallet.principal_id,
            mandate_id="mandate-agent-b",
            subject_id="agent-b",
            subject_key=key_b,
            action="data:read:payments-research",
            now=BASE + timedelta(seconds=4),
        )
        source_a = evaluate_action(
            gate=gate,
            bundle=bundle,
            principal_id=wallet.principal_id,
            mandate_id="mandate-agent-a",
            subject_id="agent-a",
            subject_key=key_a,
            action="data:read:payments-research",
            now=BASE + timedelta(seconds=5),
        )

        delivery_witness: list[dict[str, Any]] = []

        benign_send = evaluate_action(
            gate=gate,
            bundle=bundle,
            principal_id=wallet.principal_id,
            mandate_id="mandate-agent-a",
            subject_id="agent-a",
            subject_key=key_a,
            action="message:send:peer",
            now=BASE + timedelta(seconds=6),
        )
        if benign_send["decision"] == "ALLOWED":
            delivery_witness.append(
                {
                    "recipient": "agent-b",
                    "origin_scope": "public",
                    "payload_hash": sha256_bytes(BENIGN_PAYLOAD),
                    "delivery_kind": "benign-control",
                }
            )

        protected_send = evaluate_action(
            gate=gate,
            bundle=bundle,
            principal_id=wallet.principal_id,
            mandate_id="mandate-agent-a",
            subject_id="agent-a",
            subject_key=key_a,
            action="message:send:peer",
            now=BASE + timedelta(seconds=7),
        )
        if protected_send["decision"] == "ALLOWED":
            delivery_witness.append(
                {
                    "recipient": "agent-b",
                    "origin_scope": "data:read:payments-research",
                    "payload_hash": sha256_bytes(PROTECTED_PAYLOAD),
                    "delivery_kind": "protected-falsifier",
                }
            )

        payload_specific_action = "message:send:peer:sha256:" + sha256_bytes(PROTECTED_PAYLOAD)
        payload_specific_control = evaluate_action(
            gate=gate,
            bundle=bundle,
            principal_id=wallet.principal_id,
            mandate_id="mandate-agent-a",
            subject_id="agent-a",
            subject_key=key_a,
            action=payload_specific_action,
            now=BASE + timedelta(seconds=8),
        )

        protected_hash = sha256_bytes(PROTECTED_PAYLOAD)
        protected_delivered = any(
            row["recipient"] == "agent-b"
            and row["payload_hash"] == protected_hash
            and row["origin_scope"] == "data:read:payments-research"
            for row in delivery_witness
        )

        controls_ok = (
            admission["decision"] == "BUNDLE_ADMITTED"
            and direct_b["decision"] == "STOPPED"
            and direct_b["reason_codes"] == ["ACTION_OUTSIDE_MANDATE"]
            and source_a["decision"] == "ALLOWED"
            and benign_send["decision"] == "ALLOWED"
            and any(row["delivery_kind"] == "benign-control" for row in delivery_witness)
            and payload_specific_control["decision"] == "STOPPED"
            and payload_specific_control["reason_codes"] == ["ACTION_OUTSIDE_MANDATE"]
        )

        if not controls_ok:
            verdict = "INCONCLUSIVE_SETUP_OR_CONTROL_FAILURE"
        elif protected_send["decision"] == "STOPPED" and not protected_delivered:
            verdict = "NOT_REPRODUCED_CURRENT_GATE_BLOCKED"
        elif protected_send["decision"] == "ALLOWED" and protected_delivered:
            verdict = "ROUTE_AUTHORITY_HOLE_REPRODUCED"
        else:
            verdict = "INCONCLUSIVE_SETUP_OR_CONTROL_FAILURE"

        result = {
            "schema": "openline.route_authority_cold.result.v1",
            "experiment_id": "ROUTE-AUTHORITY-COLD-001",
            "base_time": BASE.isoformat(),
            "principal_id": wallet.principal_id,
            "bundle_head_hash": bundle["head"]["event_hash"],
            "protected_payload_hash": protected_hash,
            "benign_payload_hash": sha256_bytes(BENIGN_PAYLOAD),
            "owner_policy": {
                "agent_b_lacks": "data:read:payments-research",
                "generic_peer_messaging_allowed_for_agent_a": True,
                "invariant": "protected source authority must survive relay",
            },
            "cases": {
                "direct_b_protected_read": direct_b,
                "source_a_protected_read": source_a,
                "benign_generic_relay": benign_send,
                "protected_generic_relay": protected_send,
                "payload_specific_negative_control": payload_specific_control,
            },
            "delivery_witness": delivery_witness,
            "protected_delivered_to_agent_b": protected_delivered,
            "verdict": verdict,
        }
        result["result_hash"] = record_hash(result)
        write_json(out / "result.json", result)
        write_json(out / "delivery-witness.json", delivery_witness)
        write_json(
            out / "summary.json",
            {
                "experiment_id": result["experiment_id"],
                "protected_payload_hash": protected_hash,
                "protected_delivered_to_agent_b": protected_delivered,
                "verdict": verdict,
                "result_hash": result["result_hash"],
            },
        )
        print(json.dumps({"verdict": verdict, "result_hash": result["result_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
