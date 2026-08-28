"""Principal-owned authority continuity and portable evidence bundles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import secrets
from typing import Any, Mapping, Sequence

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import canonical_json
from .clock import as_utc, isoformat, parse_time, utc_now
from .crypto import (
    load_private_key,
    normalize_public_key,
    principal_id,
    public_key_hex,
    record_hash,
    save_private_key,
    sign_record,
    verify_record,
)
from .errors import WalletError
from .storage import EPOCH_KEY_FILE, ROOT_KEY_FILE, load_state, save_state


STATE_SCHEMA = "openline.wallet.local_state.v1"
EPOCH_SCHEMA = "openline.wallet.epoch_certificate.v1"
EVENT_SCHEMA = "openline.wallet.timeline_event.v1"
BUNDLE_SCHEMA = "openline.wallet.receiver_bundle.v1"
GATE_RECEIPT_SCHEMA = "openline.gate.action_receipt.v1"

WALLET_POLICY_AUTHORITY = "NONE"
DECISION_AUTHORITY = "RECEIVER_GATE"
DEFAULT_BUNDLE_TTL_SECONDS = 600
MAX_BUNDLE_TTL_SECONDS = 600

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_EVENT_TYPES = frozenset({"MANDATE_ISSUED", "MANDATE_NARROWED", "MANDATE_REVOKED"})


@dataclass(frozen=True)
class Timeline:
    mandates: dict[str, dict[str, Any]]
    active_by_subject: dict[str, str]
    head_sequence: int
    head_hash: str | None

    def current(self, now: datetime) -> list[dict[str, Any]]:
        current: list[dict[str, Any]] = []
        for mandate in self.mandates.values():
            item = dict(mandate)
            if item["status"] == "ACTIVE" and parse_time(item["expires_at"]) <= now:
                item["status"] = "EXPIRED"
            if item["status"] == "ACTIVE":
                current.append(item)
        return sorted(current, key=lambda row: (row["subject_id"], row["mandate_id"]))


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise WalletError(f"{label.upper()}_INVALID")
    return value


def _scope(value: object) -> str:
    if not isinstance(value, str) or _SCOPE.fullmatch(value) is None:
        raise WalletError("SCOPE_INVALID", str(value))
    return value


def _scopes(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes)) or not values:
        raise WalletError("SCOPES_REQUIRED")
    normalized = sorted({_scope(value) for value in values})
    if len(normalized) != len(values):
        raise WalletError("SCOPE_DUPLICATE")
    if len(normalized) > 32:
        raise WalletError("TOO_MANY_SCOPES")
    return normalized


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _copy(value: Any) -> Any:
    return json.loads(canonical_json(value).decode("ascii"))


def _expect_signature(record: Mapping[str, Any], public_key: str, label: str) -> None:
    valid, reason = verify_record(record, expected_public_key=public_key)
    if valid is not True:
        raise WalletError(f"{label}_INVALID", reason)


def _issue_epoch_certificate(
    root_key: Ed25519PrivateKey,
    epoch_key: Ed25519PrivateKey,
    *,
    principal: str,
    now: datetime,
) -> dict[str, Any]:
    return sign_record(
        {
            "schema": EPOCH_SCHEMA,
            "principal_id": principal,
            "epoch_id": _new_id("epoch"),
            "sequence": 1,
            "epoch_public_key": public_key_hex(epoch_key),
            "issued_at": isoformat(now),
            "expires_at": isoformat(now + timedelta(days=366)),
            "authority": "ISSUE_AND_NARROW_MANDATES",
        },
        root_key,
    )


def _verify_epoch_certificate(
    certificate: Mapping[str, Any],
    *,
    principal: str,
    root_public_key: str,
) -> dict[str, Any]:
    expected = {
        "schema",
        "principal_id",
        "epoch_id",
        "sequence",
        "epoch_public_key",
        "issued_at",
        "expires_at",
        "authority",
        "payload_hash",
        "signature",
    }
    if not isinstance(certificate, Mapping) or set(certificate) != expected:
        raise WalletError("EPOCH_CERTIFICATE_SHAPE_INVALID")
    if certificate.get("schema") != EPOCH_SCHEMA:
        raise WalletError("EPOCH_CERTIFICATE_SCHEMA_INVALID")
    _expect_signature(certificate, root_public_key, "EPOCH_CERTIFICATE_SIGNATURE")
    if certificate.get("principal_id") != principal:
        raise WalletError("EPOCH_PRINCIPAL_MISMATCH")
    if certificate.get("sequence") != 1:
        raise WalletError("EPOCH_SEQUENCE_INVALID")
    if certificate.get("authority") != "ISSUE_AND_NARROW_MANDATES":
        raise WalletError("EPOCH_AUTHORITY_INVALID")
    _identifier(certificate.get("epoch_id"), "epoch_id")
    try:
        normalize_public_key(certificate.get("epoch_public_key"))
    except WalletError as exc:
        raise WalletError("EPOCH_PUBLIC_KEY_INVALID") from exc
    issued = parse_time(certificate.get("issued_at"), "epoch issued_at")
    expires = parse_time(certificate.get("expires_at"), "epoch expires_at")
    if expires <= issued:
        raise WalletError("EPOCH_LIFETIME_INVALID")
    return dict(certificate)


def _mandate_data(data: Mapping[str, Any], *, predecessor_required: bool) -> dict[str, Any]:
    expected = {
        "mandate_id",
        "subject_id",
        "subject_public_key",
        "scopes",
        "expires_at",
        "predecessor_mandate_id",
    }
    if not isinstance(data, Mapping) or set(data) != expected:
        raise WalletError("MANDATE_DATA_SHAPE_INVALID")
    mandate_id = _identifier(data.get("mandate_id"), "mandate_id")
    subject_id = _identifier(data.get("subject_id"), "subject_id")
    try:
        subject_public = normalize_public_key(data.get("subject_public_key"))
    except WalletError as exc:
        raise WalletError("SUBJECT_PUBLIC_KEY_INVALID") from exc
    raw_scopes = data.get("scopes")
    if not isinstance(raw_scopes, list):
        raise WalletError("SCOPES_REQUIRED")
    scopes = _scopes(raw_scopes)
    expires = parse_time(data.get("expires_at"), "mandate expires_at")
    predecessor = data.get("predecessor_mandate_id")
    if predecessor_required:
        predecessor = _identifier(predecessor, "predecessor_mandate_id")
    elif predecessor is not None:
        raise WalletError("PREDECESSOR_UNEXPECTED")
    return {
        "mandate_id": mandate_id,
        "subject_id": subject_id,
        "subject_public_key": subject_public,
        "scopes": scopes,
        "expires_at": isoformat(expires),
        "predecessor_mandate_id": predecessor,
    }


def replay_events(
    events: Sequence[Mapping[str, Any]],
    *,
    principal: str,
    root_public_key: str,
    epoch_certificate: Mapping[str, Any],
) -> Timeline:
    epoch = _verify_epoch_certificate(
        epoch_certificate,
        principal=principal,
        root_public_key=root_public_key,
    )
    epoch_public = str(epoch["epoch_public_key"])
    epoch_issued = parse_time(epoch["issued_at"])
    epoch_expires = parse_time(epoch["expires_at"])
    mandates: dict[str, dict[str, Any]] = {}
    active_by_subject: dict[str, str] = {}
    previous_hash: str | None = None
    previous_time: datetime | None = None

    for index, raw_event in enumerate(events, start=1):
        if not isinstance(raw_event, Mapping):
            raise WalletError("EVENT_INVALID")
        event = dict(raw_event)
        expected = {
            "schema",
            "principal_id",
            "sequence",
            "previous_event_hash",
            "event_type",
            "issued_at",
            "data",
            "payload_hash",
            "signature",
        }
        if set(event) != expected or event.get("schema") != EVENT_SCHEMA:
            raise WalletError("EVENT_SHAPE_INVALID")
        if event.get("principal_id") != principal:
            raise WalletError("EVENT_PRINCIPAL_MISMATCH")
        if event.get("sequence") != index:
            raise WalletError("EVENT_SEQUENCE_INVALID")
        if event.get("previous_event_hash") != previous_hash:
            raise WalletError("EVENT_CHAIN_BROKEN")
        event_type = event.get("event_type")
        if event_type not in _EVENT_TYPES:
            raise WalletError("EVENT_TYPE_INVALID")
        expected_signer = root_public_key if event_type == "MANDATE_REVOKED" else epoch_public
        _expect_signature(event, expected_signer, "EVENT_SIGNATURE")
        issued = parse_time(event.get("issued_at"), "event issued_at")
        if previous_time is not None and issued < previous_time:
            raise WalletError("EVENT_TIME_REVERSED")
        if event_type != "MANDATE_REVOKED" and not (epoch_issued <= issued < epoch_expires):
            raise WalletError("EVENT_OUTSIDE_EPOCH_LIFETIME")

        data = event.get("data")
        if event_type in {"MANDATE_ISSUED", "MANDATE_NARROWED"}:
            mandate = _mandate_data(data, predecessor_required=event_type == "MANDATE_NARROWED")
            if parse_time(mandate["expires_at"]) <= issued:
                raise WalletError("MANDATE_EXPIRES_BEFORE_ISSUE")
            if parse_time(mandate["expires_at"]) > epoch_expires:
                raise WalletError("MANDATE_OUTLIVES_EPOCH")
            if mandate["mandate_id"] in mandates:
                raise WalletError("MANDATE_ID_DUPLICATE")

            if event_type == "MANDATE_ISSUED":
                active_id = active_by_subject.get(mandate["subject_id"])
                if active_id is not None:
                    active = mandates[active_id]
                    if active["status"] == "ACTIVE" and parse_time(active["expires_at"]) > issued:
                        raise WalletError("SUBJECT_ALREADY_HAS_ACTIVE_MANDATE")
            else:
                predecessor_id = str(mandate["predecessor_mandate_id"])
                predecessor = mandates.get(predecessor_id)
                if predecessor is None or predecessor["status"] != "ACTIVE":
                    raise WalletError("PREDECESSOR_NOT_ACTIVE")
                if parse_time(predecessor["expires_at"]) <= issued:
                    raise WalletError("PREDECESSOR_EXPIRED")
                if mandate["subject_id"] != predecessor["subject_id"]:
                    raise WalletError("NARROW_SUBJECT_CHANGED")
                if mandate["subject_public_key"] != predecessor["subject_public_key"]:
                    raise WalletError("NARROW_SUBJECT_KEY_CHANGED")
                if not set(mandate["scopes"]).issubset(predecessor["scopes"]):
                    raise WalletError("NARROW_SCOPE_EXPANDED")
                if parse_time(mandate["expires_at"]) > parse_time(predecessor["expires_at"]):
                    raise WalletError("NARROW_EXPIRY_EXPANDED")
                if (
                    mandate["scopes"] == predecessor["scopes"]
                    and mandate["expires_at"] == predecessor["expires_at"]
                ):
                    raise WalletError("NARROW_NO_REDUCTION")
                predecessor["status"] = "SUPERSEDED"
                predecessor["successor_mandate_id"] = mandate["mandate_id"]

            mandate["status"] = "ACTIVE"
            mandate["event_hash"] = record_hash(event)
            mandate["event_payload_hash"] = event["payload_hash"]
            mandate["issued_at"] = isoformat(issued)
            mandates[mandate["mandate_id"]] = mandate
            active_by_subject[mandate["subject_id"]] = mandate["mandate_id"]
        else:
            if not isinstance(data, Mapping) or set(data) != {"mandate_id", "reason"}:
                raise WalletError("REVOCATION_DATA_SHAPE_INVALID")
            mandate_id = _identifier(data.get("mandate_id"), "mandate_id")
            reason = _identifier(data.get("reason"), "revocation_reason")
            mandate = mandates.get(mandate_id)
            if mandate is None or mandate["status"] != "ACTIVE":
                raise WalletError("MANDATE_NOT_ACTIVE")
            mandate["status"] = "REVOKED"
            mandate["revoked_at"] = isoformat(issued)
            mandate["revocation_reason"] = reason
            active_by_subject.pop(mandate["subject_id"], None)

        previous_hash = record_hash(event)
        previous_time = issued

    return Timeline(
        mandates=mandates,
        active_by_subject=active_by_subject,
        head_sequence=len(events),
        head_hash=previous_hash,
    )


def _verify_gate_receipt(receipt: Mapping[str, Any], *, principal: str) -> None:
    expected = {
        "schema",
        "gate_id",
        "gate_public_key",
        "principal_id",
        "mandate_id",
        "subject_id",
        "action",
        "decision",
        "reason_codes",
        "presentation_hash",
        "decided_at",
        "wallet_policy_authority",
        "decision_authority",
        "payload_hash",
        "signature",
    }
    if (
        not isinstance(receipt, Mapping)
        or set(receipt) != expected
        or receipt.get("schema") != GATE_RECEIPT_SCHEMA
    ):
        raise WalletError("GATE_RECEIPT_SCHEMA_INVALID")
    gate_public = normalize_public_key(receipt.get("gate_public_key"))
    valid, reason = verify_record(receipt, expected_public_key=gate_public)
    if valid is not True:
        raise WalletError("GATE_RECEIPT_SIGNATURE_INVALID", reason)
    _identifier(receipt.get("gate_id"), "gate_id")
    if receipt.get("principal_id") != principal:
        raise WalletError("GATE_RECEIPT_PRINCIPAL_MISMATCH")
    if receipt.get("decision") not in {"ALLOWED", "STOPPED"}:
        raise WalletError("GATE_RECEIPT_DECISION_INVALID")
    reasons = receipt.get("reason_codes")
    if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
        raise WalletError("GATE_RECEIPT_REASONS_INVALID")
    parse_time(receipt.get("decided_at"), "gate receipt decided_at")
    if receipt.get("wallet_policy_authority") != WALLET_POLICY_AUTHORITY:
        raise WalletError("GATE_RECEIPT_WALLET_AUTHORITY_INVALID")
    if receipt.get("decision_authority") != DECISION_AUTHORITY:
        raise WalletError("GATE_RECEIPT_AUTHORITY_INVALID")


def verify_bundle(
    bundle: Mapping[str, Any],
    *,
    now: datetime | None = None,
    require_fresh: bool = True,
) -> tuple[dict[str, Any], Timeline]:
    current = as_utc(now or utc_now(), "bundle verification time")
    expected = {
        "schema",
        "principal",
        "epoch_certificate",
        "events",
        "receipts",
        "head",
        "issued_at",
        "expires_at",
        "canonicalization",
        "wallet_policy_authority",
        "decision_authority",
        "payload_hash",
        "signature",
    }
    if not isinstance(bundle, Mapping) or set(bundle) != expected:
        raise WalletError("BUNDLE_SHAPE_INVALID")
    if bundle.get("schema") != BUNDLE_SCHEMA:
        raise WalletError("BUNDLE_SCHEMA_INVALID")
    principal_block = bundle.get("principal")
    if not isinstance(principal_block, Mapping) or set(principal_block) != {
        "principal_id",
        "root_public_key",
    }:
        raise WalletError("PRINCIPAL_BLOCK_INVALID")
    root_public = str(principal_block.get("root_public_key", "")).lower()
    expected_principal = principal_id(root_public)
    if principal_block.get("principal_id") != expected_principal:
        raise WalletError("PRINCIPAL_ID_MISMATCH")
    _expect_signature(bundle, root_public, "BUNDLE_SIGNATURE")
    if bundle.get("canonicalization") != "olp-canonical-json-int-v1":
        raise WalletError("CANONICALIZATION_INVALID")
    if bundle.get("wallet_policy_authority") != WALLET_POLICY_AUTHORITY:
        raise WalletError("WALLET_AUTHORITY_INVALID")
    if bundle.get("decision_authority") != DECISION_AUTHORITY:
        raise WalletError("DECISION_AUTHORITY_INVALID")
    issued = parse_time(bundle.get("issued_at"), "bundle issued_at")
    expires = parse_time(bundle.get("expires_at"), "bundle expires_at")
    ttl = int((expires - issued).total_seconds())
    if ttl <= 0 or ttl > MAX_BUNDLE_TTL_SECONDS:
        raise WalletError("BUNDLE_TTL_INVALID")
    if issued > current + timedelta(seconds=5):
        raise WalletError("BUNDLE_FROM_FUTURE")
    if require_fresh and expires <= current:
        raise WalletError("BUNDLE_EXPIRED")

    events = bundle.get("events")
    receipts = bundle.get("receipts")
    if not isinstance(events, list) or not isinstance(receipts, list):
        raise WalletError("BUNDLE_HISTORY_INVALID")
    timeline = replay_events(
        events,
        principal=expected_principal,
        root_public_key=root_public,
        epoch_certificate=bundle.get("epoch_certificate"),
    )
    head = bundle.get("head")
    if not isinstance(head, Mapping) or set(head) != {"event_hash", "sequence"}:
        raise WalletError("BUNDLE_HEAD_INVALID")
    if head.get("sequence") != timeline.head_sequence or head.get("event_hash") != timeline.head_hash:
        raise WalletError("BUNDLE_HEAD_MISMATCH")
    seen_receipts: set[str] = set()
    for receipt in receipts:
        _verify_gate_receipt(receipt, principal=expected_principal)
        receipt_hash = record_hash(receipt)
        if receipt_hash in seen_receipts:
            raise WalletError("GATE_RECEIPT_DUPLICATE")
        seen_receipts.add(receipt_hash)
    return dict(bundle), timeline


class Wallet:
    """Local principal state. It produces evidence and never authorizes effects."""

    def __init__(
        self,
        wallet_dir: str | Path,
        state: dict[str, Any],
        root_key: Ed25519PrivateKey,
        epoch_key: Ed25519PrivateKey,
    ) -> None:
        self.wallet_dir = Path(wallet_dir)
        self.state = state
        self.root_key = root_key
        self.epoch_key = epoch_key
        self._validate_local()

    @classmethod
    def create(
        cls,
        wallet_dir: str | Path,
        *,
        label: str = "My OpenLine Wallet",
        root_key: Ed25519PrivateKey | None = None,
        epoch_key: Ed25519PrivateKey | None = None,
        now: datetime | None = None,
    ) -> "Wallet":
        target = Path(wallet_dir)
        if target.exists() and any(target.iterdir()):
            raise WalletError("WALLET_DIRECTORY_NOT_EMPTY", str(target))
        target.mkdir(parents=True, exist_ok=True)
        current = as_utc(now or utc_now(), "wallet creation time")
        root = root_key or Ed25519PrivateKey.generate()
        epoch = epoch_key or Ed25519PrivateKey.generate()
        root_public = public_key_hex(root)
        principal = principal_id(root_public)
        state = {
            "schema": STATE_SCHEMA,
            "version": 1,
            "label": str(label)[:128],
            "created_at": isoformat(current),
            "principal_id": principal,
            "root_public_key": root_public,
            "epoch_certificate": _issue_epoch_certificate(
                root,
                epoch,
                principal=principal,
                now=current,
            ),
            "events": [],
            "receipts": [],
            "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
            "decision_authority": DECISION_AUTHORITY,
        }
        save_private_key(target / ROOT_KEY_FILE, root)
        try:
            save_private_key(target / EPOCH_KEY_FILE, epoch)
            save_state(target, state)
        except Exception:
            (target / ROOT_KEY_FILE).unlink(missing_ok=True)
            (target / EPOCH_KEY_FILE).unlink(missing_ok=True)
            raise
        return cls(target, state, root, epoch)

    @classmethod
    def open(cls, wallet_dir: str | Path) -> "Wallet":
        target = Path(wallet_dir)
        return cls(
            target,
            load_state(target),
            load_private_key(target / ROOT_KEY_FILE),
            load_private_key(target / EPOCH_KEY_FILE),
        )

    @classmethod
    def import_bundle(
        cls,
        wallet_dir: str | Path,
        bundle: Mapping[str, Any],
        *,
        root_key: Ed25519PrivateKey,
        epoch_key: Ed25519PrivateKey,
        label: str = "Imported OpenLine Wallet",
        now: datetime | None = None,
    ) -> "Wallet":
        verified, _timeline = verify_bundle(bundle, now=now, require_fresh=False)
        principal_block = verified["principal"]
        if public_key_hex(root_key) != principal_block["root_public_key"]:
            raise WalletError("IMPORTED_ROOT_KEY_MISMATCH")
        if public_key_hex(epoch_key) != verified["epoch_certificate"]["epoch_public_key"]:
            raise WalletError("IMPORTED_EPOCH_KEY_MISMATCH")
        target = Path(wallet_dir)
        if target.exists() and any(target.iterdir()):
            raise WalletError("WALLET_DIRECTORY_NOT_EMPTY", str(target))
        target.mkdir(parents=True, exist_ok=True)
        state = {
            "schema": STATE_SCHEMA,
            "version": 1,
            "label": str(label)[:128],
            "created_at": verified["issued_at"],
            "principal_id": principal_block["principal_id"],
            "root_public_key": principal_block["root_public_key"],
            "epoch_certificate": _copy(verified["epoch_certificate"]),
            "events": _copy(verified["events"]),
            "receipts": _copy(verified["receipts"]),
            "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
            "decision_authority": DECISION_AUTHORITY,
        }
        save_private_key(target / ROOT_KEY_FILE, root_key)
        try:
            save_private_key(target / EPOCH_KEY_FILE, epoch_key)
            save_state(target, state)
        except Exception:
            (target / ROOT_KEY_FILE).unlink(missing_ok=True)
            (target / EPOCH_KEY_FILE).unlink(missing_ok=True)
            raise
        return cls(target, state, root_key, epoch_key)

    @property
    def principal_id(self) -> str:
        return str(self.state["principal_id"])

    @property
    def root_public_key(self) -> str:
        return str(self.state["root_public_key"])

    def _validate_local(self) -> Timeline:
        expected = {
            "schema",
            "version",
            "label",
            "created_at",
            "principal_id",
            "root_public_key",
            "epoch_certificate",
            "events",
            "receipts",
            "wallet_policy_authority",
            "decision_authority",
        }
        if set(self.state) != expected or self.state.get("schema") != STATE_SCHEMA:
            raise WalletError("WALLET_STATE_SHAPE_INVALID")
        if self.state.get("version") != 1:
            raise WalletError("WALLET_VERSION_UNSUPPORTED")
        if public_key_hex(self.root_key) != self.state.get("root_public_key"):
            raise WalletError("LOCAL_ROOT_KEY_MISMATCH")
        if self.state.get("principal_id") != principal_id(self.root_public_key):
            raise WalletError("LOCAL_PRINCIPAL_ID_MISMATCH")
        certificate = self.state.get("epoch_certificate")
        epoch = _verify_epoch_certificate(
            certificate,
            principal=self.principal_id,
            root_public_key=self.root_public_key,
        )
        if public_key_hex(self.epoch_key) != epoch["epoch_public_key"]:
            raise WalletError("LOCAL_EPOCH_KEY_MISMATCH")
        if self.state.get("wallet_policy_authority") != WALLET_POLICY_AUTHORITY:
            raise WalletError("WALLET_AUTHORITY_INVALID")
        if self.state.get("decision_authority") != DECISION_AUTHORITY:
            raise WalletError("DECISION_AUTHORITY_INVALID")
        timeline = replay_events(
            self.state.get("events", []),
            principal=self.principal_id,
            root_public_key=self.root_public_key,
            epoch_certificate=certificate,
        )
        seen_receipts: set[str] = set()
        for receipt in self.state.get("receipts", []):
            _verify_gate_receipt(receipt, principal=self.principal_id)
            receipt_hash = record_hash(receipt)
            if receipt_hash in seen_receipts:
                raise WalletError("GATE_RECEIPT_DUPLICATE")
            seen_receipts.add(receipt_hash)
        return timeline

    def timeline(self) -> Timeline:
        return self._validate_local()

    def _append_event(
        self,
        event_type: str,
        data: Mapping[str, Any],
        *,
        signer: Ed25519PrivateKey,
        now: datetime,
    ) -> dict[str, Any]:
        timeline = self._validate_local()
        event = sign_record(
            {
                "schema": EVENT_SCHEMA,
                "principal_id": self.principal_id,
                "sequence": timeline.head_sequence + 1,
                "previous_event_hash": timeline.head_hash,
                "event_type": event_type,
                "issued_at": isoformat(now),
                "data": _copy(dict(data)),
            },
            signer,
        )
        candidate = _copy(self.state)
        candidate["events"].append(event)
        replay_events(
            candidate["events"],
            principal=self.principal_id,
            root_public_key=self.root_public_key,
            epoch_certificate=candidate["epoch_certificate"],
        )
        self.state = candidate
        save_state(self.wallet_dir, self.state)
        return event

    def _resolve_current(self, target: str, *, now: datetime) -> dict[str, Any]:
        timeline = self.timeline()
        if target in timeline.mandates:
            mandate = timeline.mandates[target]
        else:
            mandate_id = timeline.active_by_subject.get(target)
            mandate = timeline.mandates.get(mandate_id) if mandate_id else None
        if mandate is None or mandate.get("status") != "ACTIVE":
            raise WalletError("CURRENT_MANDATE_NOT_FOUND", target)
        if parse_time(mandate["expires_at"]) <= now:
            raise WalletError("CURRENT_MANDATE_EXPIRED", target)
        return mandate

    def grant(
        self,
        *,
        subject_id: str,
        subject_public_key: str,
        scopes: Sequence[str],
        expires_at: datetime,
        now: datetime | None = None,
        mandate_id: str | None = None,
    ) -> dict[str, Any]:
        current = as_utc(now or utc_now(), "grant time")
        subject = _identifier(subject_id, "subject_id")
        normalized_scopes = _scopes(scopes)
        expiration = as_utc(expires_at, "mandate expiry")
        if expiration <= current:
            raise WalletError("MANDATE_EXPIRY_INVALID")
        timeline = self.timeline()
        active_id = timeline.active_by_subject.get(subject)
        if active_id is not None:
            active = timeline.mandates[active_id]
            if parse_time(active["expires_at"]) > current and active["status"] == "ACTIVE":
                raise WalletError("SUBJECT_ALREADY_HAS_ACTIVE_MANDATE", active_id)
        return self._append_event(
            "MANDATE_ISSUED",
            {
                "mandate_id": mandate_id or _new_id("mandate"),
                "subject_id": subject,
                "subject_public_key": str(subject_public_key).lower(),
                "scopes": normalized_scopes,
                "expires_at": isoformat(expiration),
                "predecessor_mandate_id": None,
            },
            signer=self.epoch_key,
            now=current,
        )

    def narrow(
        self,
        target: str,
        *,
        scopes: Sequence[str],
        expires_at: datetime | None = None,
        now: datetime | None = None,
        mandate_id: str | None = None,
    ) -> dict[str, Any]:
        current = as_utc(now or utc_now(), "narrow time")
        predecessor = self._resolve_current(target, now=current)
        expiration = as_utc(expires_at, "narrow expiry") if expires_at else parse_time(
            predecessor["expires_at"]
        )
        return self._append_event(
            "MANDATE_NARROWED",
            {
                "mandate_id": mandate_id or _new_id("mandate"),
                "subject_id": predecessor["subject_id"],
                "subject_public_key": predecessor["subject_public_key"],
                "scopes": _scopes(scopes),
                "expires_at": isoformat(expiration),
                "predecessor_mandate_id": predecessor["mandate_id"],
            },
            signer=self.epoch_key,
            now=current,
        )

    def revoke(
        self,
        target: str,
        *,
        reason: str = "USER_REVOKED",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = as_utc(now or utc_now(), "revocation time")
        mandate = self._resolve_current(target, now=current)
        return self._append_event(
            "MANDATE_REVOKED",
            {
                "mandate_id": mandate["mandate_id"],
                "reason": _identifier(reason.upper().replace(" ", "_"), "revocation_reason"),
            },
            signer=self.root_key,
            now=current,
        )

    def add_receipt(self, receipt: Mapping[str, Any]) -> None:
        _verify_gate_receipt(receipt, principal=self.principal_id)
        candidate_hash = record_hash(receipt)
        if any(record_hash(existing) == candidate_hash for existing in self.state["receipts"]):
            raise WalletError("GATE_RECEIPT_DUPLICATE")
        candidate = _copy(self.state)
        candidate["receipts"].append(_copy(receipt))
        self.state = candidate
        save_state(self.wallet_dir, self.state)

    def export_bundle(
        self,
        *,
        now: datetime | None = None,
        ttl_seconds: int = DEFAULT_BUNDLE_TTL_SECONDS,
    ) -> dict[str, Any]:
        current = as_utc(now or utc_now(), "export time")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise WalletError("BUNDLE_TTL_INVALID")
        if ttl_seconds <= 0 or ttl_seconds > MAX_BUNDLE_TTL_SECONDS:
            raise WalletError("BUNDLE_TTL_INVALID", f"maximum is {MAX_BUNDLE_TTL_SECONDS}s")
        timeline = self._validate_local()
        bundle = sign_record(
            {
                "schema": BUNDLE_SCHEMA,
                "principal": {
                    "principal_id": self.principal_id,
                    "root_public_key": self.root_public_key,
                },
                "epoch_certificate": _copy(self.state["epoch_certificate"]),
                "events": _copy(self.state["events"]),
                "receipts": _copy(self.state["receipts"]),
                "head": {
                    "sequence": timeline.head_sequence,
                    "event_hash": timeline.head_hash,
                },
                "issued_at": isoformat(current),
                "expires_at": isoformat(current + timedelta(seconds=ttl_seconds)),
                "canonicalization": "olp-canonical-json-int-v1",
                "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
                "decision_authority": DECISION_AUTHORITY,
            },
            self.root_key,
        )
        verify_bundle(bundle, now=current)
        return bundle

    def summary(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = as_utc(now or utc_now(), "summary time")
        timeline = self.timeline()
        mandates: list[dict[str, Any]] = []
        for mandate in sorted(timeline.mandates.values(), key=lambda item: item["issued_at"]):
            item = _copy(mandate)
            if item["status"] == "ACTIVE" and parse_time(item["expires_at"]) <= current:
                item["status"] = "EXPIRED"
            mandates.append(item)
        return {
            "schema": "openline.wallet.summary.v1",
            "label": self.state["label"],
            "principal_id": self.principal_id,
            "root_public_key": self.root_public_key,
            "head_sequence": timeline.head_sequence,
            "head_hash": timeline.head_hash,
            "mandates": mandates,
            "receipt_count": len(self.state["receipts"]),
            "wallet_policy_authority": WALLET_POLICY_AUTHORITY,
            "decision_authority": DECISION_AUTHORITY,
        }
