"""Read-only late settlement for an acknowledged GitHub merge.

This module exists for one narrow case:

1. GitHub's merge endpoint returned ``merged=true`` and a concrete merge SHA.
2. The receiver durably recorded that acknowledgement before settlement.
3. The immediate read path could not yet reconcile GitHub's eventual state.
4. No merge retry is permitted.

A later read-only reconciliation may promote that exact acknowledged attempt to
``CONFIRMED`` only when GitHub exposes the exact acknowledged commit and that
commit still binds the reviewed head. The PR read is used to re-check target
identity; late attribution does not wait for the PR object's
``merge_commit_sha`` field to catch up. Transport-ambiguous attempts that never
received an acknowledged merge SHA remain unattributed.
"""
from __future__ import annotations

from datetime import datetime
from time import monotonic, sleep
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


def _settled_acknowledged_merge(
    client: ge.GitHubClient,
    target: ge.MergeTarget,
    *,
    expected_sha: str,
    wait_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Settle a previously acknowledged merge without depending on PR SHA lag.

    The successful merge response is already durable before this function is
    called. Re-read the PR only to bind the observation to the same repository,
    PR number, base ref, and reviewed head. A null ``merge_commit_sha`` is not
    treated as failure because GitHub may expose the acknowledged commit before
    that PR field converges.

    If the PR does expose a non-null merge SHA, it must equal the acknowledged
    SHA. The exact acknowledged commit is then fetched directly and its parents
    are verified. Only read failures may be retried, and the retry window is
    bounded. No mutation method is reachable from this helper.
    """
    expected_sha = ge._sha(
        expected_sha, "GITHUB_ACKNOWLEDGED_SHA_INVALID"
    )
    after = ge._snapshot(client.pr(target), target)
    _require(
        after["head_sha"] == target.head_sha,
        "GITHUB_MERGE_NOT_RECONCILED",
    )
    observed = after["merge_commit_sha"]
    _require(
        observed is None or observed == expected_sha,
        "GITHUB_MERGE_NOT_RECONCILED",
    )

    deadline = monotonic() + wait_seconds
    while True:
        try:
            commit = ge._merge_parents(client, target, expected_sha)
            return after, commit
        except WalletError as exc:
            # Only provider read uncertainty is retryable. A wrong commit,
            # wrong parent, wrong identity, or contradictory SHA is terminal.
            if exc.code not in {
                "GITHUB_HTTP_ERROR",
                "GITHUB_TRANSPORT_UNCERTAIN",
            }:
                raise
            if monotonic() >= deadline:
                raise WalletError(
                    "GITHUB_MERGE_NOT_RECONCILED"
                ) from exc
            sleep(min(0.2, max(0.0, deadline - monotonic())))


def settle_acknowledged_merges(
    receiver: ge.GitHubMergeReceiver,
    *,
    now: datetime | None = None,
    wait_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    """Confirm only already-acknowledged GitHub merges using read-only evidence.

    This function never calls ``merge``. It may only read the PR and the exact
    acknowledged commit. A durable API acknowledgement is required before
    attribution.

    ``wait_seconds`` is bounded to keep settlement finite. If GitHub still does
    not expose the exact acknowledged commit within the window, the attempt
    remains ``UNCERTAIN``.
    """
    _require(
        isinstance(receiver, ge.GitHubMergeReceiver),
        "GITHUB_RECEIVER_REQUIRED",
    )
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
                after, commit = _settled_acknowledged_merge(
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
