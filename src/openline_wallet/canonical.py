"""Strict canonical JSON shared by wallet and receiver.

The profile intentionally matches the integer-only OpenLine receipt profile.
Floats and duplicate object keys fail closed instead of being serialized two
different ways by two implementations.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from .errors import WalletError


MAX_SAFE_INTEGER = (1 << 53) - 1
CANONICALIZATION = "olp-canonical-json-int-v1"


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WalletError("DUPLICATE_JSON_KEY", key)
        result[key] = value
    return result


def strict_json_loads(text: str) -> Any:
    def reject_constant(value: str) -> None:
        raise WalletError("NONFINITE_JSON_NUMBER", value)

    try:
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=reject_constant,
        )
    except WalletError:
        raise
    except (TypeError, ValueError) as exc:
        raise WalletError("JSON_INVALID", str(exc)) from exc


def strict_json_load(path: str | Path) -> Any:
    try:
        return strict_json_loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise WalletError("FILE_READ_FAILED", str(exc)) from exc


def _validate(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        if abs(value) > MAX_SAFE_INTEGER:
            raise WalletError("INTEGER_OUT_OF_RANGE", path)
        return
    if isinstance(value, float):
        raise WalletError("FLOAT_FORBIDDEN", path)
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate(item, f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise WalletError("OBJECT_KEY_INVALID", path)
            _validate(item, f"{path}.{key}")
        return
    raise WalletError("CANONICAL_VALUE_UNSUPPORTED", f"{path}: {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    _validate(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def pretty_json(value: Any) -> str:
    _validate(value)
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
