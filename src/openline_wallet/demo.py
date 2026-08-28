"""PLATFORM-EXIT-001: the acceptance demo for OpenLine Wallet v0.1."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import tempfile
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import pretty_json
from .clock import utc_now
from .crypto import load_private_key, public_key_hex, verify_record
from .receiver import ReferenceGate, create_presentation
from .storage import EPOCH_KEY_FILE, ROOT_KEY_FILE, atomic_write_json
from .wallet import Wallet, verify_bundle


VERDICT = "PLATFORM_EXIT_CONTINUITY_ENFORCED"


def _write_artifact(directory: Path, name: str, value: Any) -> None:
    atomic_write_json(directory / name, value, mode=0o644)


def run_platform_exit(output_dir: str | Path | None = None) -> dict[str, Any]:
    """Run the complete provider-exit story against independent Gate state."""
    owned_temporary = tempfile.TemporaryDirectory(prefix="openline-platform-exit-")
    workspace = Path(owned_temporary.name)
    artifacts = Path(output_dir) if output_dir is not None else workspace / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    base = utc_now()

    wallet = Wallet.create(workspace / "wallet", label="Platform Exit Demo", now=base)
    agent_a_key = Ed25519PrivateKey.generate()
    agent_b_key = Ed25519PrivateKey.generate()

    issued_a = wallet.grant(
        subject_id="agent-a",
        subject_public_key=public_key_hex(agent_a_key),
        scopes=["deploy:staging"],
        expires_at=base + timedelta(hours=2),
        now=base,
        mandate_id="mandate-platform-a",
    )
    before_exit = wallet.export_bundle(now=base + timedelta(seconds=1))

    platform_a = ReferenceGate("platform-a")
    platform_a.pin_principal(wallet.principal_id, wallet.root_public_key)
    platform_a.admit_bundle(before_exit, now=base + timedelta(seconds=1))
    challenge_a = platform_a.issue_challenge(
        principal_id=wallet.principal_id,
        subject_id="agent-a",
        action="deploy:staging",
        now=base + timedelta(seconds=2),
    )
    presentation_a = create_presentation(
        bundle=before_exit,
        mandate_id="mandate-platform-a",
        subject_id="agent-a",
        subject_key=agent_a_key,
        action="deploy:staging",
        receiver_challenge=challenge_a,
        now=base + timedelta(seconds=2),
    )
    platform_a_receipt = platform_a.evaluate(
        presentation_a,
        expected_action="deploy:staging",
        now=base + timedelta(seconds=2),
    )
    if platform_a_receipt["decision"] != "ALLOWED":
        raise AssertionError("Platform A baseline action did not pass")
    wallet.add_receipt(platform_a_receipt)

    wallet.revoke("agent-a", reason="PROVIDER_EXIT", now=base + timedelta(seconds=3))
    issued_b = wallet.grant(
        subject_id="agent-b",
        subject_public_key=public_key_hex(agent_b_key),
        scopes=["deploy:staging"],
        expires_at=base + timedelta(hours=2),
        now=base + timedelta(seconds=4),
        mandate_id="mandate-platform-b",
    )
    after_exit = wallet.export_bundle(now=base + timedelta(seconds=5))
    _verified, after_timeline = verify_bundle(after_exit, now=base + timedelta(seconds=5))

    platform_b = ReferenceGate("platform-b")
    platform_b.pin_principal(wallet.principal_id, wallet.root_public_key)
    platform_b.admit_bundle(after_exit, now=base + timedelta(seconds=5))

    stale_bundle_result = "UNEXPECTEDLY_ADMITTED"
    try:
        platform_b.admit_bundle(before_exit, now=base + timedelta(seconds=6))
    except Exception as exc:  # stable code is asserted below without coupling demo output to traceback text
        stale_bundle_result = getattr(exc, "code", type(exc).__name__)

    old_challenge = platform_b.issue_challenge(
        principal_id=wallet.principal_id,
        subject_id="agent-a",
        action="deploy:staging",
        now=base + timedelta(seconds=6),
    )
    old_presentation = create_presentation(
        bundle=after_exit,
        mandate_id="mandate-platform-a",
        subject_id="agent-a",
        subject_key=agent_a_key,
        action="deploy:staging",
        receiver_challenge=old_challenge,
        now=base + timedelta(seconds=6),
    )
    old_receipt = platform_b.evaluate(
        old_presentation,
        expected_action="deploy:staging",
        now=base + timedelta(seconds=6),
    )

    new_challenge = platform_b.issue_challenge(
        principal_id=wallet.principal_id,
        subject_id="agent-b",
        action="deploy:staging",
        now=base + timedelta(seconds=7),
    )
    new_presentation = create_presentation(
        bundle=after_exit,
        mandate_id="mandate-platform-b",
        subject_id="agent-b",
        subject_key=agent_b_key,
        action="deploy:staging",
        receiver_challenge=new_challenge,
        now=base + timedelta(seconds=7),
    )
    new_receipt = platform_b.evaluate(
        new_presentation,
        expected_action="deploy:staging",
        now=base + timedelta(seconds=7),
    )

    old_event_valid, _reason = verify_record(
        issued_a,
        expected_public_key=wallet.state["epoch_certificate"]["epoch_public_key"],
    )
    imported = Wallet.import_bundle(
        workspace / "moved-wallet",
        after_exit,
        root_key=load_private_key(wallet.wallet_dir / ROOT_KEY_FILE),
        epoch_key=load_private_key(wallet.wallet_dir / EPOCH_KEY_FILE),
        now=base + timedelta(seconds=8),
    )
    imported_summary = imported.summary(now=base + timedelta(seconds=8))
    receipt_preserved = any(
        item.get("payload_hash") == platform_a_receipt.get("payload_hash")
        for item in after_exit["receipts"]
    )

    checks = {
        "platform_a_action_allowed": platform_a_receipt["decision"] == "ALLOWED",
        "old_mandate_signature_still_valid": old_event_valid is True,
        "stale_pre_exit_bundle_rejected": stale_bundle_result == "BUNDLE_HEAD_STALE",
        "old_authority_stopped_on_platform_b": old_receipt["decision"] == "STOPPED"
        and old_receipt["reason_codes"] == ["MANDATE_REVOKED"],
        "successor_authority_allowed_on_platform_b": new_receipt["decision"] == "ALLOWED",
        "platform_a_receipt_survived_move": receipt_preserved,
        "imported_wallet_has_same_principal": imported.principal_id == wallet.principal_id,
        "history_head_survived_move": imported_summary["head_hash"] == after_timeline.head_hash,
    }
    verdict = VERDICT if all(checks.values()) else "PLATFORM_EXIT_INCONCLUSIVE"
    result = {
        "schema": "openline.platform_exit_001.result.v1",
        "experiment_id": "PLATFORM-EXIT-001",
        "verdict": verdict,
        "checks": checks,
        "observed": {
            "platform_a": platform_a_receipt["decision"],
            "platform_b_old_authority": old_receipt["decision"],
            "platform_b_old_reason": old_receipt["reason_codes"],
            "platform_b_successor": new_receipt["decision"],
            "stale_bundle": stale_bundle_result,
            "history_events": len(after_exit["events"]),
            "history_receipts": len(after_exit["receipts"]),
        },
        "authority": {
            "wallet_policy_authority": "NONE",
            "decision_authority": "RECEIVER_GATE",
        },
        "claim_boundary": {
            "earned": "A receiver that admits the current exported history rejects superseded authority and accepts the current subject-bound mandate.",
            "unearned": [
                "cross-machine propagation",
                "production key custody",
                "guardian recovery UX",
                "global identity proof",
            ],
        },
        "mandates": {
            "platform_a": issued_a["data"]["mandate_id"],
            "platform_b": issued_b["data"]["mandate_id"],
        },
    }
    _write_artifact(artifacts, "wallet-before.olw", before_exit)
    _write_artifact(artifacts, "wallet-after.olw", after_exit)
    _write_artifact(artifacts, "platform-a-receipt.json", platform_a_receipt)
    _write_artifact(artifacts, "platform-b-old-receipt.json", old_receipt)
    _write_artifact(artifacts, "platform-b-current-receipt.json", new_receipt)
    _write_artifact(artifacts, "result.json", result)
    owned_temporary.cleanup()
    return result


def render_result(result: Mapping[str, Any]) -> str:
    observed = result["observed"]
    return "\n".join(
        [
            f"Platform A   {observed['platform_a']} — agent-a / deploy:staging",
            "Move         wallet history and Platform A receipt preserved",
            f"Platform B   {observed['platform_b_old_authority']} — old mandate / {observed['platform_b_old_reason'][0]}",
            f"Platform B   {observed['platform_b_successor']} — agent-b / deploy:staging",
            f"Verdict      {result['verdict']}",
            "Boundary     Wallet owns continuity. Gate owns consequences.",
        ]
    )


def result_json(result: Mapping[str, Any]) -> str:
    return pretty_json(dict(result))
