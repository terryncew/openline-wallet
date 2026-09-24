"""Exchange kernel interface contracts (frozen for this build).

Local, deterministic, simulated. Currency is SIM_USD everywhere.
These ABCs define the seams; LocalMailboxTransport / FileRegistry /
KeywordMatcher are the reference implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Transport(ABC):
    """Carries talk between agents. Talk is not authority."""

    @abstractmethod
    def send(self, sender: str, recipient: str, kind: str, body: dict) -> str:
        """Append one message; return msg_id."""

    @abstractmethod
    def receive(self, recipient: str) -> list[dict]:
        """Return and clear all pending messages for recipient."""

    @abstractmethod
    def peek(self, recipient: str) -> list[dict]:
        """Return pending messages without clearing."""


class Registry(ABC):
    """Persistent record of what sellers offer."""

    @abstractmethod
    def register(self, listing: dict) -> str:
        """Validate and store a listing; return its deterministic listing_id."""

    @abstractmethod
    def search(self, query: dict) -> list[dict]:
        """Find listings matching query. Revoked listings never match."""

    @abstractmethod
    def get(self, listing_id: str) -> dict | None:
        """Return the listing record, or None if unknown."""

    @abstractmethod
    def revoke(self, listing_id: str, reason: str) -> None:
        """Mark listing revoked. Idempotent: re-revoking is a silent no-op."""

    @abstractmethod
    def all(self) -> list[dict]:
        """Return every listing record, including revoked ones."""


class Matcher(ABC):
    """Ranks candidate listings against a need. A score is the matcher's
    stated ranking only, never a claim about listing quality."""

    @abstractmethod
    def rank(
        self, need: dict, candidates: list[dict]
    ) -> list[tuple[dict, float, list[str]]]:
        """Return (listing, score, reasons) sorted best-first. No scores are
        claims of quality; they are the matcher's stated ranking only."""


class Receiver(ABC):
    """Independently decides whether delivered work meets acceptance."""

    @abstractmethod
    def check(self, job_id: str) -> dict:
        """Return {"verdict": "accepted|rejected", "checks": [...], ...}."""


class Settlement(ABC):
    """Moves simulated funds exactly once per accepted job."""

    @abstractmethod
    def settle(self, job_id: str) -> dict:
        """Return {"settlement_id": ..., "amount": ..., "status": ...}.
        Status is "settled" or "already" (idempotent)."""
