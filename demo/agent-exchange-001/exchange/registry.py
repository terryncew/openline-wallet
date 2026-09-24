"""JSON-file registry of seller listings. Local, deterministic, no network.

The whole registry is a single JSON file at `path`, written atomically
(temp file + os.replace). listing_id is derived deterministically from
(seller_id, capability, artifact), so re-registering the same offering
returns the same id.

Revocation is sticky: re-registering an already-revoked listing does not
clear its revoked standing, and revoking an already-revoked listing is a
silent no-op.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .interfaces import Registry

_REQUIRED_FIELDS = (
    "seller_id",
    "seller_name",
    "capability",
    "service",
    "artifact",
    "price",
    "currency",
    "terms",
    "acceptance",
)


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _sha16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def listing_id_for(seller_id: str, capability: str, artifact: str) -> str:
    """Deterministic listing id from the seller's offering identity."""
    return "listing-" + _sha16(
        _canonical(
            {"seller_id": seller_id, "capability": capability, "artifact": artifact}
        )
    )


class FileRegistry(Registry):
    def __init__(self, path: Path):
        self.path = Path(path)

    # -- storage -------------------------------------------------------
    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError(f"registry file {self.path} is corrupt: not an object")
        return data

    def _save(self, data: dict) -> None:
        parent = self.path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(data, indent=2, sort_keys=True))
                fh.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- Registry ------------------------------------------------------
    def register(self, listing: dict) -> str:
        """Validate and store a listing; return its deterministic listing_id.

        Raises KeyError if a required field is missing, ValueError if a
        field is invalid (e.g. price <= 0). Re-registering the same
        (seller_id, capability, artifact) overwrites fields but preserves
        created_at and never clears a revoked standing.
        """
        if not isinstance(listing, dict):
            raise TypeError("listing must be a dict")
        for field in _REQUIRED_FIELDS:
            if field not in listing:
                raise KeyError(f"listing missing required field: {field!r}")
        price = listing["price"]
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise ValueError("price must be a number")
        if price <= 0:
            raise ValueError("price must be > 0")
        if not isinstance(listing["acceptance"], dict):
            raise ValueError("acceptance must be a dict")
        for field in ("seller_id", "seller_name", "capability", "service",
                      "artifact", "currency", "terms"):
            if not isinstance(listing[field], str) or not listing[field]:
                raise ValueError(f"{field} must be a non-empty string")

        lid = listing_id_for(
            listing["seller_id"], listing["capability"], listing["artifact"]
        )
        record = {
            "listing_id": lid,
            "seller_id": listing["seller_id"],
            "seller_name": listing["seller_name"],
            "capability": listing["capability"],
            "service": listing["service"],
            "artifact": listing["artifact"],
            "price": price,
            "currency": listing["currency"],
            "terms": listing["terms"],
            "required_permissions": list(listing.get("required_permissions", [])),
            "acceptance": listing["acceptance"],
            "evidence": list(listing.get("evidence", [])),
            "standing": "active",
            "revoked_reason": None,
            "created_at": _utcnow(),
        }
        # Optional free-text description (searchable, not required).
        if listing.get("description"):
            record["description"] = listing["description"]
        # Exchange-kernel linkage fields: the commission offer id and the
        # local seller identity name, so a selected listing maps back to the
        # exact offer and the exact signing key. Preserved verbatim.
        for extra in ("offer_id", "identity"):
            if extra in listing:
                record[extra] = listing[extra]

        data = self._load()
        existing = data.get(lid)
        if existing is not None:
            record["created_at"] = existing.get("created_at", record["created_at"])
            if existing.get("standing") == "revoked":
                record["standing"] = "revoked"
                record["revoked_reason"] = existing.get("revoked_reason")
        data[lid] = record
        self._save(data)
        return lid

    @staticmethod
    def _matches_query(record: dict, query: dict) -> bool:
        if record.get("standing") == "revoked":
            return False
        max_price = query.get("max_price")
        if max_price is not None and record.get("price", float("inf")) > max_price:
            return False
        service = query.get("service")
        if service is not None and record.get("service") != service:
            return False
        keywords = query.get("keywords") or []
        if keywords:
            haystack = " ".join(
                str(record.get(f, "") or "")
                for f in ("capability", "service", "terms", "seller_name", "description")
            ).lower()
            if not any(str(kw).lower() in haystack for kw in keywords):
                return False
        return True

    def search(self, query: dict) -> list[dict]:
        """Find active listings matching keywords / max_price / service.

        Revoked listings are always excluded.
        """
        if not isinstance(query, dict):
            raise TypeError("query must be a dict")
        data = self._load()
        return [
            rec
            for rec in data.values()
            if self._matches_query(rec, query)
        ]

    def get(self, listing_id: str) -> dict | None:
        return self._load().get(listing_id)

    def revoke(self, listing_id: str, reason: str) -> None:
        """Mark listing revoked. Re-revoking is a silent no-op.

        Raises KeyError if the listing_id is unknown.
        """
        data = self._load()
        record = data.get(listing_id)
        if record is None:
            raise KeyError(f"unknown listing_id: {listing_id!r}")
        if record.get("standing") == "revoked":
            return  # idempotent: already revoked, leave it alone
        record["standing"] = "revoked"
        record["revoked_reason"] = reason
        self._save(data)

    def all(self) -> list[dict]:
        """Every listing record, including revoked ones."""
        return list(self._load().values())
