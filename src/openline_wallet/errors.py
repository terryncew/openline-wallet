"""Stable fail-closed errors for OpenLine Wallet."""

from __future__ import annotations


class WalletError(ValueError):
    """A user-facing protocol or state failure with a stable reason code."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        message = code if detail is None else f"{code}: {detail}"
        super().__init__(message)
