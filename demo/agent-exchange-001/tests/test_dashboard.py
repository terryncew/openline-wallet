"""Seller view must report worked jobs by display name, not principal id.

Regression: render_seller_view joined jobs on j["seller"] (the wallet
principal id, e.g. "openline:principal:97f53...") == name (the display
name, e.g. "Seller B"), which never matches, so the committed UI showed
"No jobs worked." / "No settlements received." for a seller with
settled jobs on record. Jobs carry both fields; the join accepts either.
"""

import unittest

from ui.dashboard import render_seller_view

PRINCIPAL_ID = "openline:principal:97f53deadbeef"


def _snapshot():
    return {
        "agents": [{"name": "Seller B", "role": "seller"}],
        "listings": [
            {"listing_id": "listing-1", "seller_name": "Seller B",
             "capability": "digest-report", "price": 40,
             "standing": "active", "terms": "test terms"},
        ],
        "jobs": [
            {"job_id": "job-1", "seller": PRINCIPAL_ID,
             "seller_name": "Seller B", "amount": 40,
             "status": "settled", "verdict": "accepted"},
        ],
        "receipts": [
            {"job_id": "job-1", "settlement_id": "settle:abc",
             "amount": 40, "hash": "abcdef0123456789"},
        ],
    }


class SellerViewTruthTest(unittest.TestCase):
    def test_worked_jobs_rendered_for_seller_name(self):
        html = render_seller_view(_snapshot())
        self.assertIn("job-1", html)
        self.assertNotIn("No jobs worked.", html)

    def test_settlements_rendered_for_worked_jobs(self):
        html = render_seller_view(_snapshot())
        self.assertNotIn("No settlements received.", html)

    def test_unworked_seller_still_shows_empty(self):
        snap = _snapshot()
        snap["jobs"] = []
        snap["receipts"] = []
        html = render_seller_view(snap)
        self.assertIn("No jobs worked.", html)


if __name__ == "__main__":
    unittest.main()
