"""Fixed-membership closure evidence, not a source of execution authority.

A closed set is a conjunction of independently signed receiver-local closures.
The caller supplies the exact membership and evidence; absent or invalid members
cannot be counted away. No remote effect is performed by this module.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence
import re
import secrets

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .canonical import canonical_json
from .clock import as_utc, isoformat, parse_time, utc_now
from .crypto import normalize_public_key, principal_id, public_key_hex, record_hash, sign_record, verify_record
from .effect_closure import CLOSURE_SCHEMA, EffectClosure, EffectGate
from .errors import WalletError
from .wallet import verify_bundle

MEMBERSHIP_SCHEMA = "openline.wallet.closure_membership.v1"
REQUEST_SCHEMA = "openline.wallet.closure_request.v1"
WITNESS_SCHEMA = "openline.wallet.closure_witness.v1"
SET_SCHEMA = "openline.wallet.closure_set.v1"
SCOPE = "RECEIVER_LOCAL_STAGING_LEDGER"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
MAX_MEMBERS = 32
MAX_LIFETIME = 600


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise WalletError(code)


def _id(value: object) -> str:
    _require(isinstance(value, str) and _ID.fullmatch(value) is not None, "CLOSURE_ID_INVALID")
    return value


def _shape(value: object, fields: set[str], code: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping) and set(value) == fields, code)
    return value


def _signed(record: Mapping[str, Any], key: str, code: str) -> None:
    valid, _reason = verify_record(record, expected_public_key=key)
    _require(valid, code)


def _window(record: Mapping[str, Any], now: datetime, max_seconds: int = MAX_LIFETIME) -> None:
    start = parse_time(record.get("issued_at"))
    end = parse_time(record.get("expires_at"))
    _require(0 < (end - start).total_seconds() <= max_seconds, "CLOSURE_LIFETIME_INVALID")
    _require(start <= now < end, "CLOSURE_REQUEST_EXPIRED")


def _members(rows: object) -> list[dict[str, str]]:
    _require(isinstance(rows, list) and 1 <= len(rows) <= MAX_MEMBERS, "CLOSURE_MEMBERS_INVALID")
    result = []
    for row in rows:
        _shape(row, {"gate_id", "gate_public_key"}, "CLOSURE_MEMBER_SHAPE_INVALID")
        result.append({"gate_id": _id(row["gate_id"]),
                       "gate_public_key": normalize_public_key(row["gate_public_key"])})
    _require(result == sorted(result, key=lambda r: r["gate_id"]), "CLOSURE_MEMBERS_UNSORTED")
    _require(len({r["gate_id"] for r in result}) == len(result), "CLOSURE_MEMBER_DUPLICATE")
    _require(len({r["gate_public_key"] for r in result}) == len(result), "CLOSURE_KEY_DUPLICATE")
    return result


def create_membership(root_key: Ed25519PrivateKey, mandate_id: str,
                      members: Sequence[Mapping[str, str]], *,
                      now: datetime | None = None, ttl_seconds: int = 600) -> dict[str, Any]:
    current = as_utc(now or utc_now())
    _require(type(ttl_seconds) is int and 0 < ttl_seconds <= MAX_LIFETIME, "CLOSURE_LIFETIME_INVALID")
    root = public_key_hex(root_key)
    _require(isinstance(members, (list, tuple)) and 1 <= len(members) <= MAX_MEMBERS,
             "CLOSURE_MEMBERS_INVALID")
    _require(all(isinstance(row, Mapping) for row in members), "CLOSURE_MEMBER_SHAPE_INVALID")
    rows = sorted((dict(row) for row in members), key=lambda r: str(r.get("gate_id", "")))
    rows = _members(rows)
    return sign_record({"schema": MEMBERSHIP_SCHEMA, "principal_id": principal_id(root),
                        "root_public_key": root, "mandate_id": _id(mandate_id),
                        "members": rows, "scope": SCOPE, "issued_at": isoformat(current),
                        "expires_at": isoformat(current + timedelta(seconds=ttl_seconds)),
                        "wallet_policy_authority": "NONE"}, root_key)


def verify_membership(manifest: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = as_utc(now or utc_now())
    m = _shape(manifest, {"schema", "principal_id", "root_public_key", "mandate_id", "members",
                          "scope", "issued_at", "expires_at", "wallet_policy_authority",
                          "payload_hash", "signature"}, "CLOSURE_MEMBERSHIP_INVALID")
    _require(m["schema"] == MEMBERSHIP_SCHEMA and m["scope"] == SCOPE and
             m["wallet_policy_authority"] == "NONE", "CLOSURE_MEMBERSHIP_INVALID")
    root = normalize_public_key(m["root_public_key"])
    _signed(m, root, "CLOSURE_MEMBERSHIP_SIGNATURE_INVALID")
    _require(m["principal_id"] == principal_id(root), "CLOSURE_PRINCIPAL_MISMATCH")
    _id(m["mandate_id"])
    _require(m["root_public_key"] == root and m["members"] == _members(m["members"]),
             "CLOSURE_MEMBERSHIP_INVALID")
    _window(m, current)
    return dict(m)


def _revocation(bundle: Mapping[str, Any], mandate_id: str) -> tuple[dict[str, Any], int]:
    for event in bundle["events"]:
        if event["event_type"] == "MANDATE_REVOKED" and event["data"]["mandate_id"] == mandate_id:
            return event, event["sequence"]
    raise WalletError("MANDATE_NOT_REVOKED")


def create_closure_request(root_key: Ed25519PrivateKey, manifest: Mapping[str, Any],
                           bundle: Mapping[str, Any], *, now: datetime | None = None,
                           ttl_seconds: int = 120, nonce: str | None = None) -> dict[str, Any]:
    current = as_utc(now or utc_now())
    m = verify_membership(manifest, now=current)
    _require(public_key_hex(root_key) == m["root_public_key"], "CLOSURE_ROOT_KEY_MISMATCH")
    _require(type(ttl_seconds) is int and 0 < ttl_seconds <= 120, "CLOSURE_LIFETIME_INVALID")
    verified, timeline = verify_bundle(bundle, now=current)
    _require(verified["principal"]["principal_id"] == m["principal_id"], "CLOSURE_PRINCIPAL_MISMATCH")
    mandate = timeline.mandates.get(m["mandate_id"])
    _require(mandate is not None and mandate["status"] == "REVOKED", "MANDATE_NOT_REVOKED")
    event, sequence = _revocation(verified, m["mandate_id"])
    end = min(current + timedelta(seconds=ttl_seconds), parse_time(m["expires_at"]),
              parse_time(verified["expires_at"]))
    _require(end > current, "CLOSURE_REQUEST_EXPIRED")
    return sign_record({"schema": REQUEST_SCHEMA, "manifest_hash": record_hash(m),
                        "principal_id": m["principal_id"], "mandate_id": m["mandate_id"],
                        "bundle_hash": record_hash(verified), "head_sequence": timeline.head_sequence,
                        "head_hash": timeline.head_hash, "revocation_sequence": sequence,
                        "revocation_hash": record_hash(event), "nonce": _id(nonce or ("request_" + secrets.token_hex(16))),
                        "issued_at": isoformat(current), "expires_at": isoformat(end),
                        "wallet_policy_authority": "NONE"}, root_key)


def verify_closure_request(manifest: Mapping[str, Any], request: Mapping[str, Any],
                           bundle: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    current = as_utc(now or utc_now())
    m = verify_membership(manifest, now=current)
    r = _shape(request, {"schema", "manifest_hash", "principal_id", "mandate_id", "bundle_hash",
                         "head_sequence", "head_hash", "revocation_sequence", "revocation_hash", "nonce",
                         "issued_at", "expires_at", "wallet_policy_authority", "payload_hash", "signature"},
               "CLOSURE_REQUEST_INVALID")
    _require(r["schema"] == REQUEST_SCHEMA and r["wallet_policy_authority"] == "NONE",
             "CLOSURE_REQUEST_INVALID")
    _signed(r, m["root_public_key"], "CLOSURE_REQUEST_SIGNATURE_INVALID")
    _window(r, current, 120)
    _id(r["nonce"])
    _require(type(r["head_sequence"]) is int and r["head_sequence"] > 0 and
             type(r["revocation_sequence"]) is int and r["revocation_sequence"] > 0,
             "CLOSURE_REQUEST_INVALID")
    _require(r["manifest_hash"] == record_hash(m) and r["principal_id"] == m["principal_id"]
             and r["mandate_id"] == m["mandate_id"], "CLOSURE_REQUEST_BINDING_MISMATCH")
    _require(parse_time(r["expires_at"]) <= parse_time(m["expires_at"]), "CLOSURE_REQUEST_BINDING_MISMATCH")
    verified, timeline = verify_bundle(bundle, now=current, require_fresh=False)
    _require(verified["principal"]["principal_id"] == m["principal_id"], "CLOSURE_PRINCIPAL_MISMATCH")
    _require(parse_time(r["issued_at"]) >= parse_time(verified["issued_at"]), "CLOSURE_REQUEST_BEFORE_BUNDLE")
    _require(r["bundle_hash"] == record_hash(verified) and r["head_sequence"] == timeline.head_sequence
             and r["head_hash"] == timeline.head_hash, "CLOSURE_REQUEST_BINDING_MISMATCH")
    _require(parse_time(r["expires_at"]) <= parse_time(verified["expires_at"]), "CLOSURE_REQUEST_BINDING_MISMATCH")
    mandate = timeline.mandates.get(m["mandate_id"])
    _require(mandate is not None and mandate["status"] == "REVOKED", "MANDATE_NOT_REVOKED")
    event, sequence = _revocation(verified, m["mandate_id"])
    _require(r["revocation_sequence"] == sequence and r["revocation_hash"] == record_hash(event),
             "CLOSURE_REVOCATION_MISMATCH")
    _require(parse_time(r["issued_at"]) >= parse_time(event["issued_at"]), "CLOSURE_REQUEST_BEFORE_REVOCATION")
    return dict(r)


def _check_local(closure: Mapping[str, Any], member: Mapping[str, str],
                 request: Mapping[str, Any]) -> None:
    c = _shape(closure, {"schema", "gate_id", "gate_public_key", "principal_id", "mandate_id",
                         "status", "scope", "head_sequence", "head_hash", "closed_at", "pending_fenced",
                         "active_frontiers", "wallet_policy_authority", "decision_authority", "payload_hash", "signature"},
               "CLOSURE_LOCAL_INVALID")
    _require(c["schema"] == CLOSURE_SCHEMA and c["status"] == "EFFECT_CLOSED" and c["scope"] == SCOPE
             and c["wallet_policy_authority"] == "NONE" and c["decision_authority"] == "RECEIVER_GATE",
             "CLOSURE_LOCAL_INVALID")
    _signed(c, member["gate_public_key"], "CLOSURE_LOCAL_SIGNATURE_INVALID")
    _require(c["gate_id"] == member["gate_id"] and c["gate_public_key"] == member["gate_public_key"],
             "CLOSURE_MEMBER_MISMATCH")
    _require(type(c["head_sequence"]) is int, "CLOSURE_LOCAL_INVALID")
    _require(c["principal_id"] == request["principal_id"] and c["mandate_id"] == request["mandate_id"]
             and c["head_sequence"] == request["head_sequence"] and c["head_hash"] == request["head_hash"],
             "CLOSURE_LOCAL_BINDING_MISMATCH")
    _require(type(c["active_frontiers"]) is int and c["active_frontiers"] == 0
             and type(c["pending_fenced"]) is int and c["pending_fenced"] >= 0, "CLOSURE_LOCAL_INVALID")
    closed = parse_time(c["closed_at"])
    _require(parse_time(request["issued_at"]) <= closed < parse_time(request["expires_at"]),
             "CLOSURE_LOCAL_TIME_INVALID")


def attest_closure(gate: EffectGate, effects: EffectClosure, manifest: Mapping[str, Any],
                   request: Mapping[str, Any], bundle: Mapping[str, Any], *,
                   now: datetime | None = None) -> dict[str, Any]:
    """Receiver-owned fresh witness. The relay cannot call this without a live Gate.

    The lock covers the local closure and witness signature. A pending final
    effect must drain first; a missing/uncertain receiver cannot manufacture one.
    """
    _require(effects.gate is gate, "CLOSURE_EFFECT_OWNER_MISMATCH")
    # The clock sample belongs inside the frontier lock. Sampling before a
    # drain could backdate a closure ahead of the effect it waited for.
    with gate._effect_lock:
        current = as_utc(now or utc_now())
        m = verify_membership(manifest, now=current)
        r = verify_closure_request(m, request, bundle, now=current)
        member = next((x for x in m["members"] if x["gate_id"] == gate.gate_id), None)
        _require(member is not None and member["gate_public_key"] == gate.public_key, "CLOSURE_MEMBER_MISMATCH")
        closure = effects.close(bundle, r["mandate_id"], now=current)
        _check_local(closure, member, r)
        return sign_record({"schema": WITNESS_SCHEMA, "manifest_hash": record_hash(m),
                            "request_hash": record_hash(r), "gate_id": gate.gate_id,
                            "gate_public_key": gate.public_key, "principal_id": r["principal_id"],
                            "mandate_id": r["mandate_id"], "local_closure": closure,
                            "observed_at": isoformat(current), "wallet_policy_authority": "NONE"}, gate.gate_key)


def _check_witness(witness: Mapping[str, Any], member: Mapping[str, str],
                   manifest: Mapping[str, Any], request: Mapping[str, Any]) -> None:
    w = _shape(witness, {"schema", "manifest_hash", "request_hash", "gate_id", "gate_public_key",
                         "principal_id", "mandate_id", "local_closure", "observed_at", "wallet_policy_authority",
                         "payload_hash", "signature"}, "CLOSURE_WITNESS_INVALID")
    _require(w["schema"] == WITNESS_SCHEMA and w["wallet_policy_authority"] == "NONE",
             "CLOSURE_WITNESS_INVALID")
    _signed(w, member["gate_public_key"], "CLOSURE_WITNESS_SIGNATURE_INVALID")
    _require(w["gate_id"] == member["gate_id"] and w["gate_public_key"] == member["gate_public_key"]
             and w["principal_id"] == request["principal_id"] and w["mandate_id"] == request["mandate_id"]
             and w["manifest_hash"] == record_hash(manifest) and w["request_hash"] == record_hash(request),
             "CLOSURE_WITNESS_BINDING_MISMATCH")
    observed = parse_time(w["observed_at"])
    _require(parse_time(request["issued_at"]) <= observed < parse_time(request["expires_at"]),
             "CLOSURE_WITNESS_TIME_INVALID")
    _check_local(w["local_closure"], member, request)
    _require(parse_time(w["local_closure"]["closed_at"]) <= observed, "CLOSURE_WITNESS_TIME_INVALID")


def evaluate_closure_set(manifest: Mapping[str, Any], request: Mapping[str, Any],
                         bundle: Mapping[str, Any], witnesses: Sequence[Mapping[str, Any]], *,
                         now: datetime | None = None) -> dict[str, Any]:
    """Return an all-members result, with no authority to perform effects.

    Invalid evidence is retained as an error entry. Extra or duplicate witnesses
    never count toward membership; a poisoned member cannot be silently replaced.
    """
    current = as_utc(now or utc_now())
    m = verify_membership(manifest, now=current)
    r = verify_closure_request(m, request, bundle, now=current)
    _require(isinstance(witnesses, (list, tuple)) and len(witnesses) <= MAX_MEMBERS * 2,
             "CLOSURE_WITNESSES_INVALID")
    members = {x["gate_id"]: x for x in m["members"]}
    accepted: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for witness in witnesses:
        name = witness.get("gate_id") if isinstance(witness, Mapping) else None
        if not isinstance(name, str) or name not in members:
            errors.append({"gate_id": str(name), "reason": "CLOSURE_MEMBER_UNKNOWN"})
            continue
        if name in seen:
            accepted.pop(name, None)
            errors.append({"gate_id": name, "reason": "CLOSURE_WITNESS_DUPLICATE"})
            continue
        seen.add(name)
        try:
            _check_witness(witness, members[name], m, r)
            accepted[name] = dict(witness)
        except (WalletError, TypeError, ValueError, KeyError) as exc:
            errors.append({"gate_id": name, "reason": exc.code if isinstance(exc, WalletError) else "CLOSURE_WITNESS_INVALID"})
    missing = sorted(set(members) - set(accepted))
    complete = len(accepted) == len(members) and not errors
    return {"schema": SET_SCHEMA, "status": "EFFECT_CLOSED" if complete else "CLOSURE_INCOMPLETE",
            "scope": "FIXED_RECEIVER_SET", "manifest_hash": record_hash(m), "request_hash": record_hash(r),
            "principal_id": r["principal_id"], "mandate_id": r["mandate_id"],
            "head_sequence": r["head_sequence"], "head_hash": r["head_hash"],
            "required_receivers": len(members), "verified_receivers": len(accepted),
            "missing": missing, "errors": errors,
            "witness_hashes": [{"gate_id": name, "witness_hash": record_hash(accepted[name])}
                               for name in sorted(accepted)],
            "evaluated_at": isoformat(current), "wallet_policy_authority": "NONE",
            "effect_authority": "NONE"}


def sign_set_report(result: Mapping[str, Any], witness_key: Ed25519PrivateKey) -> dict[str, Any]:
    """Optional audit signature; it does not replace independent receiver proofs."""
    _shape(result, {"schema", "status", "scope", "manifest_hash", "request_hash", "principal_id", "mandate_id",
                    "head_sequence", "head_hash", "required_receivers", "verified_receivers", "missing", "errors",
                    "witness_hashes", "evaluated_at", "wallet_policy_authority", "effect_authority"},
           "CLOSURE_SET_INVALID")
    return sign_record(dict(result), witness_key)


def verify_set_report(report: Mapping[str, Any], expected_auditor_key: str,
                      manifest: Mapping[str, Any], request: Mapping[str, Any], bundle: Mapping[str, Any],
                      witnesses: Sequence[Mapping[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    """Recompute every field; an auditor cannot promote missing receipts to CLOSED."""
    _signed(report, expected_auditor_key, "CLOSURE_SET_SIGNATURE_INVALID")
    current = parse_time(report.get("evaluated_at"))
    actual = as_utc(now or utc_now())
    _require(current <= actual, "CLOSURE_SET_FROM_FUTURE")
    _require(actual < parse_time(request.get("expires_at")), "CLOSURE_REQUEST_EXPIRED")
    expected = evaluate_closure_set(manifest, request, bundle, witnesses, now=current)
    body = dict(report)
    body.pop("payload_hash", None)
    body.pop("signature", None)
    _require(body == expected, "CLOSURE_SET_BINDING_MISMATCH")
    return expected
