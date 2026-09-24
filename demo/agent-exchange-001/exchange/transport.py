"""File-backed JSONL mailboxes. Local, deterministic, no network.

One mailbox file per recipient: <maildir>/<recipient>.jsonl, append-only
by convention (receive truncates it). A per-recipient sequence counter at
<maildir>/.seq-<recipient> feeds the deterministic msg_id.

Thread-safe enough for a single-host demo: lines are appended with a
single open(..., "a") call per message, and the sequence file is written
via temp-file + os.replace. Concurrent writers from multiple processes
are not supported.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .interfaces import Transport

_KINDS = {
    "NEED_POSTED",
    "OFFER_LIST",
    "MATCH_REQUEST",
    "MATCH_RESPONSE",
    "AGREEMENT_PROPOSED",
    "AGREEMENT_FROZEN",
    "RESULT_DELIVERED",
    "VERDICT",
    "SETTLED",
    "RECEIPT",
    "REVOKED",
}


def _canonical(obj) -> str:
    """Deterministic JSON rendering for hashing."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalMailboxTransport(Transport):
    def __init__(self, maildir: Path):
        self.maildir = Path(maildir)
        self.maildir.mkdir(parents=True, exist_ok=True)

    # -- internal ------------------------------------------------------
    def _mailbox(self, recipient: str) -> Path:
        return self.maildir / f"{recipient}.jsonl"

    def _seq_path(self, recipient: str) -> Path:
        return self.maildir / f".seq-{recipient}"

    def _next_seq(self, recipient: str) -> int:
        """Return the next per-recipient sequence number, persisting it.

        The counter survives across transport instances sharing a maildir,
        so msg_ids stay deterministic and collision-free.
        """
        seq_path = self._seq_path(recipient)
        try:
            current = int(seq_path.read_text(encoding="utf-8").strip() or "0")
        except (FileNotFoundError, ValueError):
            current = 0
        nxt = current + 1
        # Write via temp file + exclusive create, then atomically replace.
        tmp = seq_path.with_name(f".seq-{recipient}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(nxt))
        os.replace(tmp, seq_path)
        return nxt

    @staticmethod
    def _msg_id(sender: str, recipient: str, kind: str, body: dict, seq: int) -> str:
        return "msg-" + _sha16(
            _canonical(
                {
                    "sender": sender,
                    "recipient": recipient,
                    "kind": kind,
                    "body": body,
                    "seq": seq,
                }
            )
        )

    # -- Transport ------------------------------------------------------
    def send(self, sender: str, recipient: str, kind: str, body: dict) -> str:
        """Append one envelope; return its deterministic msg_id."""
        if kind not in _KINDS:
            raise ValueError(f"unknown message kind: {kind!r}")
        if not isinstance(body, dict):
            raise TypeError("body must be a dict")
        seq = self._next_seq(recipient)
        envelope = {
            "msg_id": self._msg_id(sender, recipient, kind, body, seq),
            "sender": sender,
            "recipient": recipient,
            "kind": kind,
            "body": body,
            "at": _utcnow(),
        }
        with self._mailbox(recipient).open("a", encoding="utf-8") as fh:
            fh.write(_canonical(envelope) + "\n")
        return envelope["msg_id"]

    def _read_all(self, recipient: str) -> list[dict]:
        path = self._mailbox(recipient)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def receive(self, recipient: str) -> list[dict]:
        """Return and clear all pending messages for recipient."""
        messages = self._read_all(recipient)
        self._mailbox(recipient).open("w", encoding="utf-8").close()
        return messages

    def peek(self, recipient: str) -> list[dict]:
        """Return pending messages without clearing."""
        return self._read_all(recipient)
