"""UTC timestamp and bounded-duration helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from .errors import WalletError


_DURATION = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[smhd])$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime, label: str = "timestamp") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise WalletError("TIMESTAMP_TIMEZONE_REQUIRED", label)
    return value.astimezone(timezone.utc)


def isoformat(value: datetime) -> str:
    return as_utc(value).isoformat().replace("+00:00", "Z")


def parse_time(value: object, label: str = "timestamp") -> datetime:
    if not isinstance(value, str) or not value:
        raise WalletError("TIMESTAMP_INVALID", label)
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise WalletError("TIMESTAMP_INVALID", label) from exc
    if parsed.tzinfo is None:
        raise WalletError("TIMESTAMP_TIMEZONE_REQUIRED", label)
    return parsed.astimezone(timezone.utc)


def parse_duration(value: str) -> timedelta:
    if not isinstance(value, str):
        raise WalletError("DURATION_INVALID")
    match = _DURATION.fullmatch(value.strip().lower())
    if match is None:
        raise WalletError("DURATION_INVALID", "use 30s, 10m, 2h, or 7d")
    count = int(match.group("count"))
    unit = match.group("unit")
    seconds = count * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    if seconds > 366 * 86400:
        raise WalletError("DURATION_TOO_LONG")
    return timedelta(seconds=seconds)
