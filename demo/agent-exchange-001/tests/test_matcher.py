"""Tests for KeywordMatcher. Plain unittest, stdlib only."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchange.matcher import KeywordMatcher  # noqa: E402


def listing(lid, capability, service, terms, price, standing="active"):
    return {
        "listing_id": lid,
        "capability": capability,
        "service": service,
        "terms": terms,
        "price": price,
        "standing": standing,
    }


class MatcherTest(unittest.TestCase):
    def setUp(self):
        self.m = KeywordMatcher()

    def test_ranking_order(self):
        need = {"keywords": ["digest", "report"], "max_price": 100}
        a = listing("listing-aa", "digest-report", "text_digest",
                    "daily digest report", 50)
        b = listing("listing-bb", "digest-summary", "text_digest",
                    "weekly digest", 30)
        ranked = self.m.rank(need, [a, b])
        self.assertEqual([r[0]["listing_id"] for r in ranked],
                         ["listing-aa", "listing-bb"])
        self.assertEqual(ranked[0][1], 2.0)
        self.assertEqual(ranked[1][1], 1.0)

    def test_tie_break_lower_price_first(self):
        need = {"keywords": ["digest"], "max_price": 100}
        cheap = listing("listing-zz", "digest", "text_digest", "digest", 10)
        pricey = listing("listing-aa", "digest", "text_digest", "digest", 90)
        ranked = self.m.rank(need, [pricey, cheap])
        self.assertEqual([r[0]["listing_id"] for r in ranked],
                         ["listing-zz", "listing-aa"])

    def test_tie_break_listing_id(self):
        need = {"keywords": ["digest"], "max_price": 100}
        a = listing("listing-bb", "digest", "text_digest", "digest", 10)
        b = listing("listing-aa", "digest", "text_digest", "digest", 10)
        ranked = self.m.rank(need, [a, b])
        self.assertEqual([r[0]["listing_id"] for r in ranked],
                         ["listing-aa", "listing-bb"])

    def test_budget_filter(self):
        need = {"keywords": ["digest"], "max_price": 50}
        over = listing("listing-aa", "digest", "text_digest", "digest", 51)
        at = listing("listing-bb", "digest", "text_digest", "digest", 50)
        ranked = self.m.rank(need, [over, at])
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][0]["listing_id"], "listing-bb")

    def test_revoked_filter(self):
        need = {"keywords": ["digest"], "max_price": 100}
        dead = listing("listing-aa", "digest-report", "text_digest",
                       "digest report", 10, standing="revoked")
        live = listing("listing-bb", "digest", "text_digest", "digest", 90)
        ranked = self.m.rank(need, [dead, live])
        self.assertEqual([r[0]["listing_id"] for r in ranked], ["listing-bb"])

    def test_reasons_present(self):
        need = {"keywords": ["digest"], "max_price": 50}
        cand = listing("listing-aa", "digest-report", "text_digest",
                       "daily digest report", 50)
        ranked = self.m.rank(need, [cand])
        self.assertEqual(len(ranked), 1)
        reasons = ranked[0][2]
        self.assertIn("keyword 'digest' matched", reasons)
        self.assertIn("price 50 within budget 50", reasons)
        # no quality claims in reasons
        for r in reasons:
            self.assertNotIn("quality", r.lower())
            self.assertNotIn("best", r.lower())

    def test_case_insensitive(self):
        need = {"keywords": ["DIGEST"], "max_price": 100}
        cand = listing("listing-aa", "Digest-Report", "text_digest",
                       "Daily DIGEST", 10)
        ranked = self.m.rank(need, [cand])
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0][1], 1.0)

    def test_empty_candidates(self):
        self.assertEqual(self.m.rank({"keywords": ["x"]}, []), [])

    def test_no_max_price_accepts_any(self):
        need = {"keywords": ["digest"]}
        cand = listing("listing-aa", "digest", "text_digest", "digest", 10**9)
        ranked = self.m.rank(need, [cand])
        self.assertEqual(len(ranked), 1)


if __name__ == "__main__":
    unittest.main()
