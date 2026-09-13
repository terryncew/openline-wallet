"""Read-only late settlement for an acknowledged GitHub merge.

This module exists for one narrow case:

1. GitHub's merge endpoint returned ``merged=true`` and a concrete merge SHA.
2. The receiver durably recorded that acknowledgement before settlement.
3. The immediate read path could not yet reconcile GitHub's eventual state.
4. No merge retry is permitted.

A later read-only reconciliation may promote that exact acknowledged attempt to
``CONFIRMED`` only when GitHub eventually exposes the same merge SHA and the
merge commit still binds the reviewed head. Transport-ambiguous attempts that
never received an acknowledged merge SHA remain unattributed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import github_effect as ge
from .clock import as_utc, isoformat, utc_now
from .crypto import record_hash, sign_record, verify_record
from .errors import WalletError
from .wallet import DECISION_AUTHORITY, WALLET_POLICY_AUTHORITY


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise WalletError(code)


def _acknowledged_sha(row: dict[str, Any]) -> str | None:
    response = row.get("response")
    if not isinstance(response, dict):
        return None
    if set(response) != {"merged", "sha"} or response.get("merged") is not True:
        return None
    try:
        return ge._sha(response.get("sha"), "GITHUB_ACKNOWLEDGED_SHA_INVALID")
    except WalletError:
        return None


def settle_acknowledged_merges(
    receiver: ge.GitHubMergeReceiver,
    *,
    now: datetime | None = None,
    wait_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    """Confirm only already-acknowledged GitHub merges using read-only evidence.

    This function never calls ``merge``. It may only read the PR and resulting
    commit. A durable API acknowledgement is required before attribution.

    ``wait_seconds`` is bounded to keep settlement finite. If GitHub still does
    not expose the exact acknowledged merge within the window, the attempt
    remains ``UNCERTAIN``.
    """
    _require(isinstance(receiver, ge.GitHubMergeReceiver), "GITHUB_RECEIVER_REQUIRED")
    _require(
        type(wait_seconds) in (int, float) and 0 <= wait_seconds <= 120,
        "GITHUB_SETTLEMENT_WAIT_INVALID",
    )

    results: list[dict[str, Any]] = []
    with receiver._lock, receiver.gate._effect_lock:
        receiver._assert_open()

        for row in receiver._records():
            if row["status"] not in {"IN_FLIGHT", "UNCERTAIN"}:
                continue

            expected_sha = _acknowledged_sha(row)
            if expected_sha is None:
                # No successful mutation acknowledgement means we cannot
                # attribute an observed merge to this receiver attempt.
                results.append(
                    {
                        "attempt_id": row["id"],
                        "status": "UNCERTAIN",
                        "reason": "GITHUB_MERGE_NOT_ACKNOWLEDGED",
                    }
                )
                continue

            admission = row.get("admission")
            frontier = row.get("frontier")
            _require(
                isinstance(admission, dict)
                and verify_record(
                    admission, expected_public_key=receiver.gate.public_key
                )[0],
                "GITHUB_ADMISSION_RECEIPT_INVALID",
            )
            _require(
                isinstance(frontier, dict)
                and verify_record(
                    frontier, expected_public_key=receiver.gate.public_key
                )[0]
                and frontier.get("decision") == "ALLOWED",
                "GITHUB_FRONTIER_RECEIPT_INVALID",
            )

            try:
                after, commit = ge._settled_merge(
                    receiver.client,
                    receiver.target,
                    expected_sha=expected_sha,
                    wait_seconds=float(wait_seconds),
                )
            except Exception as exc:
                results.append(
                    {
                        "attempt_id": row["id"],
                        "status": "UNCERTAIN",
                        "reason": (
                            exc.code
                            if isinstance(exc, WalletError)
                            else "GITHUB_READ_FAILED"
                        ),
                    }
                )
                continue

            confirmed_at = isoformat(as_utc(now or utc_now()))
            effect = sign_record(
                {
                    "schema": ge.EFFECT_SCHEMA,
                    "gate_id": receiver.gate.gate_id,
                    "gate_public_key": receiver.gate.public_key,
                    "principal_id": admission["principal_id"],
                    "mandate_id": admission["mandate_id"],
                    "subject_id": admission["subject_id"],
                    "action": receiver.target.action,
                    "target": receiver.target.to_record(),
                    "admission_receipt_hash": record_hash(admission),
                    "frontier_receipt_hash": record_hash(frontier),
                    "attempt_id": row["id"],
                    "merge_commit_sha": expected_sha,
                    "merge_commit": commit,
                    "before": row.get("before"),
                    "after": after,
                    "status": "MERGE_CONFIRMED",
                    "scope": "GITHUB_PR_MERGE_ONLY",
                    "confirmed_at": confirmed_at,
                    "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
                    "decision_authority": DECISION_AUTHORITY,
                },
                receiver.gate.gate_key,
            )
            result = {
                "decision": "ALLOWED",
                "reason_codes": [],
                "effect_applied": True,
                "receipt": frontier,
                "admission_receipt": admission,
                "effect_receipt": effect,
            }
            row.update(
                status="CONFIRMED",
                after=after,
                merge_commit=commit,
                effect=effect,
                result=result,
                completed_at=confirmed_at,
            )
            receiver._store(row)
            receiver._completed[row["id"]] = result

            results.append(
                {
                    "attempt_id": row["id"],
                    "status": "CONFIRMED",
                    "effect_receipt": effect,
                    "result": result,
                }
            )

    return results
