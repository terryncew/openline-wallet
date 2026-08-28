"""Ed25519 signing helpers for portable wallet evidence."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import canonical_json
from .errors import WalletError


_HEX_32 = re.compile(r"^[0-9a-f]{64}$")
_HEX_64 = re.compile(r"^[0-9a-f]{128}$")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def public_key_hex(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    public = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    return public.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()


def private_key_hex(key: Ed25519PrivateKey) -> str:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()


def principal_id(public_hex: str) -> str:
    normalized = normalize_public_key(public_hex)
    return "openline:principal:" + sha256_hex(bytes.fromhex(normalized))


def normalize_public_key(value: object) -> str:
    if not isinstance(value, str):
        raise WalletError("PUBLIC_KEY_INVALID")
    normalized = value.strip().lower().removeprefix("ed25519:")
    if _HEX_32.fullmatch(normalized) is None:
        raise WalletError("PUBLIC_KEY_INVALID")
    return normalized


def sign_record(body: Mapping[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    if "payload_hash" in body or "signature" in body:
        raise WalletError("SIGNED_BODY_RESERVED_FIELD")
    copied = dict(body)
    canonical = canonical_json(copied)
    return {
        **copied,
        "payload_hash": sha256_hex(canonical),
        "signature": {
            "algorithm": "Ed25519",
            "public_key": public_key_hex(key),
            "value": key.sign(canonical).hex(),
        },
    }


def verify_record(
    record: Mapping[str, Any],
    *,
    expected_public_key: str | None = None,
) -> tuple[bool, str | None]:
    try:
        if not isinstance(record, Mapping):
            return False, "SIGNED_RECORD_INVALID"
        body = dict(record)
        signature = body.pop("signature")
        payload_hash = body.pop("payload_hash")
        if not isinstance(signature, Mapping) or signature.get("algorithm") != "Ed25519":
            return False, "SIGNATURE_ALGORITHM_UNSUPPORTED"
        public = normalize_public_key(signature.get("public_key"))
        if expected_public_key is not None and public != normalize_public_key(expected_public_key):
            return False, "SIGNER_MISMATCH"
        value = str(signature.get("value", "")).lower()
        if _HEX_64.fullmatch(value) is None:
            return False, "SIGNATURE_ENCODING_INVALID"
        canonical = canonical_json(body)
        if payload_hash != sha256_hex(canonical):
            return False, "PAYLOAD_HASH_MISMATCH"
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public)).verify(
            bytes.fromhex(value), canonical
        )
        return True, None
    except InvalidSignature:
        return False, "SIGNATURE_INVALID"
    except WalletError as exc:
        return False, exc.code
    except (KeyError, TypeError, ValueError):
        return False, "SIGNED_RECORD_MALFORMED"


def record_hash(record: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json(dict(record)))


def save_private_key(path: str | Path, key: Ed25519PrivateKey) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise WalletError("PRIVATE_KEY_EXISTS", str(target)) from exc
    with os.fdopen(descriptor, "w", encoding="ascii") as handle:
        handle.write(private_key_hex(key) + "\n")


def load_private_key(path: str | Path) -> Ed25519PrivateKey:
    target = Path(path)
    try:
        mode = target.stat().st_mode & 0o777
        if os.name != "nt" and mode & 0o077:
            raise WalletError("PRIVATE_KEY_PERMISSIONS_UNSAFE", oct(mode))
        raw = target.read_text(encoding="ascii").strip().lower()
    except WalletError:
        raise
    except OSError as exc:
        raise WalletError("PRIVATE_KEY_READ_FAILED", str(exc)) from exc
    if _HEX_32.fullmatch(raw) is None:
        raise WalletError("PRIVATE_KEY_INVALID")
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(raw))


def read_public_key(value: str | Path) -> str:
    candidate = Path(value)
    if candidate.exists():
        try:
            return normalize_public_key(candidate.read_text(encoding="ascii"))
        except OSError as exc:
            raise WalletError("PUBLIC_KEY_READ_FAILED", str(exc)) from exc
    return normalize_public_key(str(value))
