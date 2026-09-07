"""Receiver-local effect closure. An admission is never an execution lease.

The reference uses one in-process serialization domain. A provider adapter must
place its actual irreversible operation inside ``effect_frontier``; an upstream
check cannot establish closure for work already handed to another service.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from datetime import datetime
from pathlib import Path
import re
import secrets
from threading import RLock
from typing import Any, Callable, Mapping

from .canonical import canonical_json, strict_json_load
from .clock import as_utc, isoformat, parse_time, utc_now
from .crypto import record_hash, sha256_hex, sign_record, verify_record
from .errors import WalletError
from .receiver import ReferenceGate
from .storage import atomic_write_json
from .wallet import DECISION_AUTHORITY, GATE_RECEIPT_SCHEMA, WALLET_POLICY_AUTHORITY

EFFECT_RECEIPT_SCHEMA = "openline.wallet.effect_receipt.v1"
CLOSURE_SCHEMA = "openline.wallet.effect_closure.v1"
EFFECT_SCHEMA = "openline.platform_exit_live.effect.v1"
_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def _acquire_writer(path: Path) -> int:
    """One live writer for this local ledger, including across processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, ImportError) as exc:
        os.close(fd)
        raise WalletError("RECEIVER_EFFECT_OWNER_BUSY") from exc


def _release_writer(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class EffectGate(ReferenceGate):
    """ReferenceGate with a shared lock for authority admission and effects.

    All receiver mutations, including direct calls to inherited public methods,
    participate in the same serialization domain. This is process-local only.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._effect_lock = RLock()

    def pin_principal(self, *args: Any, **kwargs: Any) -> None:
        with self._effect_lock:
            return super().pin_principal(*args, **kwargs)

    def admit_bundle(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with self._effect_lock:
            return super().admit_bundle(*args, **kwargs)

    def issue_challenge(self, *args: Any, **kwargs: Any) -> str:
        with self._effect_lock:
            return super().issue_challenge(*args, **kwargs)

    def evaluate(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        with self._effect_lock:
            return super().evaluate(*args, **kwargs)

    def _standing(self, admission: Mapping[str, Any], now: datetime) -> str | None:
        """Revalidate the original exact grant against current receiver state."""
        expected = {"schema", "gate_id", "gate_public_key", "principal_id",
                    "mandate_id", "subject_id", "action", "decision", "reason_codes",
                    "presentation_hash", "decided_at", "wallet_policy_authority",
                    "decision_authority", "payload_hash", "signature"}
        if not isinstance(admission, Mapping) or set(admission) != expected or admission.get("schema") != GATE_RECEIPT_SCHEMA:
            return "ADMISSION_RECEIPT_INVALID"
        valid, _reason = verify_record(admission, expected_public_key=self.public_key)
        if (not valid or admission.get("gate_id") != self.gate_id
                or admission.get("gate_public_key") != self.public_key
                or admission.get("decision") != "ALLOWED"
                or admission.get("wallet_policy_authority") != WALLET_POLICY_AUTHORITY
                or admission.get("decision_authority") != DECISION_AUTHORITY):
            return "ADMISSION_RECEIPT_INVALID"
        principal = admission.get("principal_id")
        try:
            parse_time(admission.get("decided_at"), "admission time")
        except WalletError:
            return "ADMISSION_RECEIPT_INVALID"
        if principal in self._quarantined:
            return "PRINCIPAL_FORK_QUARANTINED"
        admitted = self._admitted.get(principal)
        if admitted is None:
            return "BUNDLE_NOT_ADMITTED"
        if self._trusted_roots.get(principal) != admitted.bundle["principal"]["root_public_key"]:
            return "ROOT_NOT_PINNED"
        if parse_time(admitted.bundle["expires_at"]) <= now:
            return "ADMITTED_BUNDLE_EXPIRED"
        mandate = admitted.timeline.mandates.get(admission.get("mandate_id"))
        if mandate is None:
            return "MANDATE_UNKNOWN"
        if mandate.get("status") != "ACTIVE":
            return f"MANDATE_{mandate.get('status', 'INACTIVE')}"
        if parse_time(mandate["expires_at"]) <= now:
            return "MANDATE_EXPIRED"
        if mandate["subject_id"] != admission.get("subject_id"):
            return "SUBJECT_BINDING_MISMATCH"
        if admission.get("action") not in mandate["scopes"]:
            return "ACTION_OUTSIDE_MANDATE"
        return None

    def effect_frontier(
        self,
        admission: Mapping[str, Any],
        effect: Callable[[dict[str, Any]], Any],
        *,
        now: datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Check current standing and perform the effect in one critical section.

        The callback must contain the real final effect, not just an enqueue or
        authorization check. A callback exception is indeterminate to the caller.
        """
        with self._effect_lock:
            current = as_utc(now or utc_now(), "effect frontier time")
            reason = self._standing(admission, current)
            receipt = self._receipt(
                decision="STOPPED" if reason else "ALLOWED",
                reasons=[reason] if reason else [],
                principal_id=str(admission.get("principal_id", "")),
                mandate_id=admission.get("mandate_id"),
                subject_id=admission.get("subject_id"),
                action=str(admission.get("action", "")),
                presentation_hash=admission.get("presentation_hash"),
                now=current,
            )
            if reason:
                return receipt, False
            effect(receipt)
            return receipt, True

    def close_revocation(
        self, bundle: Mapping[str, Any], mandate_id: str, *, now: datetime | None = None,
        pending_fenced: int = 0,
    ) -> dict[str, Any]:
        """Admit an authentic revocation and attest the local serialized frontier."""
        with self._effect_lock:
            owner = getattr(self, "_effect_owner", None)
            if owner is None or owner._closed or owner._uncertain or any(owner.intent_dir.iterdir()):
                raise WalletError("EFFECT_CLOSURE_UNAVAILABLE")
            current = as_utc(now or utc_now(), "effect closure time")
            admission = self.admit_bundle(bundle, now=current)
            principal = admission["principal_id"]
            mandate = self._admitted[principal].timeline.mandates.get(mandate_id)
            if mandate is None or mandate.get("status") != "REVOKED":
                raise WalletError("MANDATE_NOT_REVOKED")
            return sign_record({
                "schema": CLOSURE_SCHEMA,
                "gate_id": self.gate_id,
                "gate_public_key": self.public_key,
                "principal_id": principal,
                "mandate_id": mandate_id,
                "status": "EFFECT_CLOSED",
                "scope": "RECEIVER_LOCAL_STAGING_LEDGER",
                "head_sequence": admission["head_sequence"],
                "head_hash": admission["head_hash"],
                "closed_at": isoformat(current),
                "pending_fenced": pending_fenced,
                "active_frontiers": 0,
                "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
                "decision_authority": DECISION_AUTHORITY,
            }, self.gate_key)


@dataclass(frozen=True)
class _Ticket:
    admission: dict[str, Any]
    release: str
    action: str
    payload_hash: str


class EffectClosure:
    """One-use preparation, final frontier checks, and separate effect evidence."""

    def __init__(self, gate: EffectGate, ledger_path: Path, receipts_dir: Path) -> None:
        self.gate = gate
        self.ledger_path = Path(ledger_path).resolve()
        self.receipts_dir = Path(receipts_dir).resolve()
        self.evidence_dir = self.receipts_dir.parent / "effect-closure-evidence"
        self.intent_dir = self.evidence_dir / "intents"
        self._closed = False
        with gate._effect_lock:
            if getattr(gate, "_effect_owner", None) is not None:
                raise WalletError("RECEIVER_EFFECT_OWNER_BUSY")
            self._writer_fd = _acquire_writer(self.ledger_path.with_name(self.ledger_path.name + ".effect.lock"))
            try:
                self.receipts_dir.mkdir(parents=True, exist_ok=True)
                self.evidence_dir.mkdir(parents=True, exist_ok=True)
                self.intent_dir.mkdir(parents=True, exist_ok=True)
                self._pending: dict[str, _Ticket] = {}
                self._completed: dict[str, dict[str, Any]] = {}
                self._uncertain = any(self.intent_dir.iterdir())
                gate._effect_owner = self
            except Exception:
                _release_writer(self._writer_fd)
                raise

    def _assert_ready(self) -> None:
        if self._closed:
            raise WalletError("RECEIVER_EFFECT_OWNER_CLOSED")
        if self._uncertain or any(self.intent_dir.iterdir()):
            raise WalletError("EFFECT_OUTCOME_UNRESOLVED")

    def shutdown(self) -> None:
        """Release the writer. Never clear unresolved intents automatically."""
        with self.gate._effect_lock:
            if self._closed:
                return
            self._closed = True
            self.gate._effect_owner = None
            _release_writer(self._writer_fd)

    def _persist(self, record: Mapping[str, Any], directory: Path) -> str:
        digest = record_hash(record)
        atomic_write_json(directory / f"{digest}.json", dict(record), mode=0o644)
        return digest

    def prepare(self, presentation: Mapping[str, Any], *, action: str, release: str,
                now: datetime | None = None) -> dict[str, Any]:
        with self.gate._effect_lock:
            self._assert_ready()
            if action != "deploy:staging":
                raise WalletError("RECEIVER_EFFECT_UNSUPPORTED", action)
            if not isinstance(release, str) or _RELEASE.fullmatch(release) is None:
                raise WalletError("RELEASE_ID_INVALID")
            admission = self.gate.evaluate(presentation, expected_action=action, now=now)
            digest = self._persist(admission, self.evidence_dir)
            if admission["decision"] != "ALLOWED":
                self._persist(admission, self.receipts_dir)
                return {"decision": "STOPPED", "reason_codes": admission["reason_codes"],
                        "effect_applied": False, "release": release, "gate_id": self.gate.gate_id,
                        "receipt_hash": digest, "receipt": admission}
            token = "effect_" + secrets.token_hex(24)
            payload_hash = sha256_hex(canonical_json({"action": action, "release": release}))
            self._pending[token] = _Ticket(admission, release, action, payload_hash)
            return {"decision": "PREPARED", "ticket": token, "admission_receipt_hash": digest,
                    "effect_applied": False, "release": release, "gate_id": self.gate.gate_id}

    def finish(self, ticket: str, *, action: str, release: str,
               now: datetime | None = None) -> dict[str, Any]:
        with self.gate._effect_lock:
            if self._closed:
                raise WalletError("RECEIVER_EFFECT_OWNER_CLOSED")
            if self._uncertain:
                raise WalletError("EFFECT_OUTCOME_UNRESOLVED")
            if any(self.intent_dir.iterdir()):
                raise WalletError("EFFECT_OUTCOME_UNRESOLVED")
            if ticket in self._completed:
                result = self._completed[ticket]
                if result["release"] != release or result["receipt"]["action"] != action:
                    raise WalletError("EFFECT_BINDING_MISMATCH")
                return dict(result)
            pending = self._pending.get(ticket)
            if pending is None:
                raise WalletError("EFFECT_TICKET_UNKNOWN")
            payload_hash = sha256_hex(canonical_json({"action": action, "release": release}))
            if payload_hash != pending.payload_hash:
                raise WalletError("EFFECT_BINDING_MISMATCH")
            admission = pending.admission
            admission_hash = record_hash(admission)
            effect_id = sha256_hex(canonical_json({"admission": admission_hash, "ticket": ticket}))
            def apply(frontier_receipt: dict[str, Any]) -> None:
                value = strict_json_load(self.ledger_path) if self.ledger_path.exists() else []
                if not isinstance(value, list):
                    raise WalletError("RECEIVER_LEDGER_INVALID")
                frontier_hash = record_hash(frontier_receipt)
                if any(isinstance(item, dict) and item.get("receipt_hash") == frontier_hash for item in value):
                    raise WalletError("EFFECT_ALREADY_APPLIED")
                value.append({
                    "schema": EFFECT_SCHEMA, "action": action, "release": release,
                    "principal_id": admission["principal_id"], "subject_id": admission["subject_id"],
                    "mandate_id": admission["mandate_id"], "gate_id": admission["gate_id"],
                    "receipt_hash": frontier_hash, "decided_at": frontier_receipt["decided_at"],
                })
                atomic_write_json(self.ledger_path, value, mode=0o644)
            intent_path = self.intent_dir / f"{effect_id}.json"
            # Durable intent precedes the callback. A crash or lost acknowledgement
            # leaves the next process sealed until the local ledger is reconciled.
            atomic_write_json(intent_path, {
                "schema": "openline.wallet.effect_intent.v1", "effect_id": effect_id,
                "admission_receipt_hash": admission_hash, "action": action,
                "release": release, "principal_id": admission["principal_id"],
                "mandate_id": admission["mandate_id"], "status": "UNRESOLVED",
            }, mode=0o600)
            try:
                frontier_receipt, applied = self.gate.effect_frontier(admission, apply, now=now)
                frontier_hash = self._persist(frontier_receipt, self.receipts_dir)
                current = as_utc(now or utc_now(), "completion time")
                evidence = sign_record({
                    "schema": EFFECT_RECEIPT_SCHEMA, "gate_id": self.gate.gate_id,
                    "gate_public_key": self.gate.public_key, "principal_id": admission["principal_id"],
                    "mandate_id": admission["mandate_id"], "subject_id": admission["subject_id"],
                    "action": action, "release": release, "effect_id": effect_id,
                    "admission_receipt_hash": admission_hash, "frontier_receipt_hash": frontier_hash,
                    "decision": frontier_receipt["decision"], "reason_codes": frontier_receipt["reason_codes"],
                    "effect_applied": applied, "completed_at": isoformat(current),
                    "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
                    "decision_authority": DECISION_AUTHORITY,
                }, self.gate.gate_key)
                evidence_hash = self._persist(evidence, self.evidence_dir)
                result = {"decision": frontier_receipt["decision"],
                          "reason_codes": list(frontier_receipt["reason_codes"]),
                          "effect_applied": applied, "release": release, "gate_id": self.gate.gate_id,
                          "receipt_hash": frontier_hash, "receipt": frontier_receipt,
                          "admission_receipt_hash": admission_hash, "admission_receipt": admission,
                          "frontier_receipt_hash": frontier_hash, "frontier_receipt": frontier_receipt,
                          "effect_receipt_hash": evidence_hash, "effect_receipt": evidence}
                self._completed[ticket] = result
                del self._pending[ticket]
                intent_path.unlink()
                directory = os.open(self.intent_dir, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                return dict(result)
            except Exception:
                # The effect may have crossed its frontier before the failure.
                # Keep the durable intent and refuse further effects or closure.
                self._uncertain = True
                raise

    def close(self, bundle: Mapping[str, Any], mandate_id: str,
              *, now: datetime | None = None) -> dict[str, Any]:
        with self.gate._effect_lock:
            self._assert_ready()
            current = as_utc(now or utc_now(), "effect closure time")
            # Admission and fencing occur under the same lock as the final effect.
            admission = self.gate.admit_bundle(bundle, now=current)
            principal = admission["principal_id"]
            mandate = self.gate._admitted[principal].timeline.mandates.get(mandate_id)
            if mandate is None or mandate.get("status") != "REVOKED":
                raise WalletError("MANDATE_NOT_REVOKED")
            pending_fenced = 0
            for pending in self._pending.values():
                original = pending.admission
                if original["principal_id"] == principal and original["mandate_id"] == mandate_id:
                    if self.gate._standing(original, current) is None:
                        raise WalletError("EFFECT_CLOSURE_INCOMPLETE")
                    pending_fenced += 1
            certificate = self.gate.close_revocation(
                bundle, mandate_id, now=current, pending_fenced=pending_fenced,
            )
            self._persist(certificate, self.evidence_dir)
            return certificate
