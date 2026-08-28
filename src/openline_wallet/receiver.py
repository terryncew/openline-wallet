"""Reference receiver boundary for OpenLine Wallet bundles.

The wallet verifies evidence. This receiver decides whether an exact action may
produce an effect. Keeping the classes separate is the product invariant, not
an aesthetic preference.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
import secrets
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .clock import as_utc, isoformat, parse_time, utc_now
from .crypto import normalize_public_key, principal_id as principal_id_for_key
from .crypto import public_key_hex, record_hash, sign_record, verify_record
from .errors import WalletError
from .wallet import (
    DECISION_AUTHORITY,
    GATE_RECEIPT_SCHEMA,
    Timeline,
    WALLET_POLICY_AUTHORITY,
    verify_bundle,
)


PRESENTATION_SCHEMA = "openline.wallet.holder_presentation.v1"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


@dataclass
class _AdmittedBundle:
    bundle: dict[str, Any]
    timeline: Timeline


def create_presentation(
    *,
    bundle: Mapping[str, Any],
    mandate_id: str,
    subject_id: str,
    subject_key: Ed25519PrivateKey,
    action: str,
    receiver_challenge: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Bind one subject key to one exact receiver challenge and action."""
    current = as_utc(now or utc_now(), "presentation time")
    principal = bundle.get("principal") if isinstance(bundle, Mapping) else None
    head = bundle.get("head") if isinstance(bundle, Mapping) else None
    if not isinstance(principal, Mapping) or not isinstance(head, Mapping):
        raise WalletError("PRESENTATION_BUNDLE_INVALID")
    return sign_record(
        {
            "schema": PRESENTATION_SCHEMA,
            "principal_id": principal.get("principal_id"),
            "mandate_id": mandate_id,
            "subject_id": subject_id,
            "subject_public_key": public_key_hex(subject_key),
            "action": action,
            "receiver_challenge": receiver_challenge,
            "bundle_head_hash": head.get("event_hash"),
            "issued_at": isoformat(current),
        },
        subject_key,
    )


class ReferenceGate:
    """A minimal receiver-owned exact-action Gate.

    It pins principal roots, admits only monotonic fresh bundle heads, issues
    one-use challenges, and signs every allow/stop decision. The class performs
    no side effect itself; a receiver may act only after an ``ALLOWED`` receipt.
    """

    def __init__(
        self,
        gate_id: str,
        *,
        gate_key: Ed25519PrivateKey | None = None,
        max_bundle_age_seconds: int = 600,
        challenge_ttl_seconds: int = 60,
    ) -> None:
        if max_bundle_age_seconds <= 0 or max_bundle_age_seconds > 600:
            raise WalletError("GATE_FRESHNESS_POLICY_INVALID")
        if challenge_ttl_seconds <= 0 or challenge_ttl_seconds > 300:
            raise WalletError("CHALLENGE_TTL_INVALID")
        if not isinstance(gate_id, str) or _ID.fullmatch(gate_id) is None:
            raise WalletError("GATE_ID_INVALID")
        self.gate_id = gate_id
        self.gate_key = gate_key or Ed25519PrivateKey.generate()
        self.max_bundle_age_seconds = max_bundle_age_seconds
        self.challenge_ttl_seconds = challenge_ttl_seconds
        self._trusted_roots: dict[str, str] = {}
        self._admitted: dict[str, _AdmittedBundle] = {}
        self._quarantined: set[str] = set()
        self._challenges: dict[str, dict[str, Any]] = {}

    @property
    def public_key(self) -> str:
        return public_key_hex(self.gate_key)

    def pin_principal(self, principal_id: str, root_public_key: str) -> None:
        root = normalize_public_key(root_public_key)
        if principal_id_for_key(root) != principal_id:
            raise WalletError("PINNED_PRINCIPAL_ROOT_MISMATCH")
        existing = self._trusted_roots.get(principal_id)
        if existing is not None and existing != root:
            raise WalletError("PINNED_ROOT_CONFLICT")
        self._trusted_roots[principal_id] = root

    def admit_bundle(
        self,
        bundle: Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = as_utc(now or utc_now(), "bundle admission time")
        verified, timeline = verify_bundle(bundle, now=current)
        principal = verified["principal"]["principal_id"]
        root = verified["principal"]["root_public_key"]
        if self._trusted_roots.get(principal) != root:
            raise WalletError("ROOT_NOT_PINNED")
        issued = parse_time(verified["issued_at"], "bundle issued_at")
        age = int((current - issued).total_seconds())
        if age < -5 or age > self.max_bundle_age_seconds:
            raise WalletError("BUNDLE_OUTSIDE_GATE_FRESHNESS")
        if principal in self._quarantined:
            raise WalletError("PRINCIPAL_FORK_QUARANTINED")

        previous = self._admitted.get(principal)
        if previous is not None:
            old_sequence = previous.timeline.head_sequence
            old_hash = previous.timeline.head_hash
            new_sequence = timeline.head_sequence
            new_hash = timeline.head_hash
            if new_sequence < old_sequence:
                raise WalletError("BUNDLE_HEAD_STALE")
            if new_sequence == old_sequence and new_hash != old_hash:
                self._quarantined.add(principal)
                raise WalletError("BUNDLE_FORK_QUARANTINED")
            if new_sequence > old_sequence and old_sequence > 0:
                ancestor_hash = record_hash(verified["events"][old_sequence - 1])
                if ancestor_hash != old_hash:
                    self._quarantined.add(principal)
                    raise WalletError("BUNDLE_FORK_QUARANTINED")

        self._admitted[principal] = _AdmittedBundle(verified, timeline)
        return {
            "decision": "BUNDLE_ADMITTED",
            "principal_id": principal,
            "head_sequence": timeline.head_sequence,
            "head_hash": timeline.head_hash,
            "decision_authority": DECISION_AUTHORITY,
        }

    def issue_challenge(
        self,
        *,
        principal_id: str,
        subject_id: str,
        action: str,
        now: datetime | None = None,
    ) -> str:
        current = as_utc(now or utc_now(), "challenge issue time")
        token = "challenge_" + secrets.token_hex(16)
        self._challenges[token] = {
            "principal_id": principal_id,
            "subject_id": subject_id,
            "action": action,
            "issued_at": current,
            "expires_at": current + timedelta(seconds=self.challenge_ttl_seconds),
            "used": False,
        }
        return token

    def _receipt(
        self,
        *,
        decision: str,
        reasons: list[str],
        principal_id: str,
        mandate_id: str | None,
        subject_id: str | None,
        action: str,
        presentation_hash: str | None,
        now: datetime,
    ) -> dict[str, Any]:
        return sign_record(
            {
                "schema": GATE_RECEIPT_SCHEMA,
                "gate_id": self.gate_id,
                "gate_public_key": self.public_key,
                "principal_id": principal_id,
                "mandate_id": mandate_id,
                "subject_id": subject_id,
                "action": action,
                "decision": decision,
                "reason_codes": reasons,
                "presentation_hash": presentation_hash,
                "decided_at": isoformat(now),
                "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
                "decision_authority": DECISION_AUTHORITY,
            },
            self.gate_key,
        )

    def evaluate(
        self,
        presentation: Mapping[str, Any],
        *,
        expected_action: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = as_utc(now or utc_now(), "gate evaluation time")
        principal = str(presentation.get("principal_id", "")) if isinstance(presentation, Mapping) else ""
        mandate_id = str(presentation.get("mandate_id", "")) if isinstance(presentation, Mapping) else ""
        subject_id = str(presentation.get("subject_id", "")) if isinstance(presentation, Mapping) else ""
        presentation_hash: str | None = None

        def stop(reason: str) -> dict[str, Any]:
            return self._receipt(
                decision="STOPPED",
                reasons=[reason],
                principal_id=principal,
                mandate_id=mandate_id or None,
                subject_id=subject_id or None,
                action=expected_action,
                presentation_hash=presentation_hash,
                now=current,
            )

        expected_shape = {
            "schema",
            "principal_id",
            "mandate_id",
            "subject_id",
            "subject_public_key",
            "action",
            "receiver_challenge",
            "bundle_head_hash",
            "issued_at",
            "payload_hash",
            "signature",
        }
        if not isinstance(presentation, Mapping) or set(presentation) != expected_shape:
            return stop("PRESENTATION_SHAPE_INVALID")
        if presentation.get("schema") != PRESENTATION_SCHEMA:
            return stop("PRESENTATION_SCHEMA_INVALID")
        subject_public = str(presentation.get("subject_public_key", ""))
        valid, reason = verify_record(presentation, expected_public_key=subject_public)
        if valid is not True:
            return stop(reason or "PRESENTATION_SIGNATURE_INVALID")
        presentation_hash = record_hash(presentation)
        challenge_token = str(presentation.get("receiver_challenge", ""))
        challenge = self._challenges.get(challenge_token)
        if challenge is None:
            return stop("CHALLENGE_UNKNOWN")
        if challenge["used"]:
            return stop("PRESENTATION_REPLAYED")
        challenge["used"] = True
        if challenge["expires_at"] <= current:
            return stop("CHALLENGE_EXPIRED")
        if (
            challenge["principal_id"] != principal
            or challenge["subject_id"] != subject_id
            or challenge["action"] != expected_action
        ):
            return stop("CHALLENGE_BINDING_MISMATCH")
        try:
            issued = parse_time(presentation.get("issued_at"), "presentation issued_at")
        except WalletError:
            return stop("PRESENTATION_TIME_INVALID")
        if abs((current - issued).total_seconds()) > self.challenge_ttl_seconds:
            return stop("PRESENTATION_STALE")
        if presentation.get("action") != expected_action:
            return stop("ACTION_BINDING_MISMATCH")
        if principal in self._quarantined:
            return stop("PRINCIPAL_FORK_QUARANTINED")
        admitted = self._admitted.get(principal)
        if admitted is None:
            return stop("BUNDLE_NOT_ADMITTED")
        if parse_time(admitted.bundle["expires_at"]) <= current:
            return stop("ADMITTED_BUNDLE_EXPIRED")
        if presentation.get("bundle_head_hash") != admitted.timeline.head_hash:
            return stop("PRESENTATION_HEAD_STALE")
        mandate = admitted.timeline.mandates.get(mandate_id)
        if mandate is None:
            return stop("MANDATE_UNKNOWN")
        if mandate.get("status") != "ACTIVE":
            return stop(f"MANDATE_{mandate.get('status', 'INACTIVE')}")
        if parse_time(mandate["expires_at"]) <= current:
            return stop("MANDATE_EXPIRED")
        if mandate["subject_id"] != subject_id or mandate["subject_public_key"] != subject_public:
            return stop("SUBJECT_BINDING_MISMATCH")
        if expected_action not in mandate["scopes"]:
            return stop("ACTION_OUTSIDE_MANDATE")
        return self._receipt(
            decision="ALLOWED",
            reasons=[],
            principal_id=principal,
            mandate_id=mandate_id,
            subject_id=subject_id,
            action=expected_action,
            presentation_hash=presentation_hash,
            now=current,
        )
