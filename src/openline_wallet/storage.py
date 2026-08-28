"""Small atomic local store. The wallet is user-owned state, not a service."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any

from .canonical import pretty_json, strict_json_load
from .errors import WalletError


STATE_FILE = "wallet.json"
ROOT_KEY_FILE = "root.key"
EPOCH_KEY_FILE = "epoch.key"


def atomic_write_json(path: str | Path, value: Any, *, mode: int = 0o600) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(pretty_json(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        if os.name != "nt":
            os.chmod(target, mode)
        try:
            directory = os.open(target.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def load_state(wallet_dir: str | Path) -> dict[str, Any]:
    path = Path(wallet_dir) / STATE_FILE
    value = strict_json_load(path)
    if not isinstance(value, dict):
        raise WalletError("WALLET_STATE_INVALID")
    return value


def save_state(wallet_dir: str | Path, state: dict[str, Any]) -> None:
    atomic_write_json(Path(wallet_dir) / STATE_FILE, state)
