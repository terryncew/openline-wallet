"""Reference matcher: keyword overlap ranking. Local, deterministic.

Filters out revoked listings and anything over the need's max_price,
then scores by keyword overlap. Scores and reasons are the matcher's
stated ranking only — they carry no claim about listing quality.
"""

from __future__ import annotations

import re

from .interfaces import Matcher

_WORD = re.compile(r"\w+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _listing_words(listing: dict) -> set[str]:
    return _words(
        " ".join(
            str(listing.get(f, "") or "")
            for f in ("capability", "service", "terms")
        )
    )


class KeywordMatcher(Matcher):
    def rank(
        self, need: dict, candidates: list[dict]
    ) -> list[tuple[dict, float, list[str]]]:
        """Return (listing, score, reasons) sorted best-first.

        Filters: revoked listings and price > need max_price are dropped.
        Score: count of need keywords present in the listing's
        capability+service+terms word set (case-insensitive).
        Tie-break: lower price first, then listing_id.
        """
        if not isinstance(need, dict):
            raise TypeError("need must be a dict")
        keywords = [str(k).lower() for k in (need.get("keywords") or [])]
        max_price = need.get("max_price")

        ranked: list[tuple[dict, float, list[str]]] = []
        for listing in candidates:
            if listing.get("standing") == "revoked":
                continue
            price = listing.get("price", float("inf"))
            if max_price is not None and price > max_price:
                continue
            words = _listing_words(listing)
            matched = [kw for kw in keywords if kw in words]
            score = float(len(matched))
            reasons = [f"keyword {kw!r} matched" for kw in matched]
            if max_price is not None:
                reasons.append(f"price {price} within budget {max_price}")
            ranked.append((listing, score, reasons))

        ranked.sort(
            key=lambda item: (
                -item[1],
                item[0].get("price", float("inf")),
                item[0].get("listing_id", ""),
            )
        )
        return ranked
