"""Tests for FileRegistry. Plain unittest, stdlib only."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from exchange.registry import FileRegistry, listing_id_for  # noqa: E402


def make_listing(**over):
    listing = {
        "seller_id": "seller-a",
        "seller_name": "seller-a",
        "capability": "digest-report",
        "service": "text_digest",
        "artifact": "text_digest@1.0",
        "price": 50,
        "currency": "SIM_USD (simulated)",
        "terms": "results only; implementation retained by seller",
        "acceptance": {"type": "exact_match"},
    }
    listing.update(over)
    return listing


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = FileRegistry(Path(self.tmp.name) / "registry.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_returns_deterministic_id(self):
        lid = self.reg.register(make_listing())
        self.assertEqual(
            lid, listing_id_for("seller-a", "digest-report", "text_digest@1.0")
        )
        self.assertTrue(lid.startswith("listing-"))

    def test_register_is_idempotent(self):
        lid1 = self.reg.register(make_listing())
        lid2 = self.reg.register(make_listing())
        self.assertEqual(lid1, lid2)
        self.assertEqual(len(self.reg.all()), 1)

    def test_get(self):
        lid = self.reg.register(make_listing())
        rec = self.reg.get(lid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["listing_id"], lid)
        self.assertEqual(rec["standing"], "active")
        self.assertIsNone(rec["revoked_reason"])
        self.assertIn("created_at", rec)
        self.assertIsNone(self.reg.get("listing-deadbeefdeadbeef"))

    def test_search_keywords(self):
        self.reg.register(make_listing())
        self.reg.register(
            make_listing(
                seller_id="seller-b",
                seller_name="seller-b",
                capability="image-caption",
                service="image_caption",
                artifact="image_caption@2.0",
                terms="captions for photographs",
            )
        )
        hits = self.reg.search({"keywords": ["digest"]})
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["capability"], "digest-report")
        # no keyword filter -> both
        self.assertEqual(len(self.reg.search({})), 2)

    def test_search_max_price(self):
        self.reg.register(make_listing(price=50))
        self.reg.register(
            make_listing(
                seller_id="seller-c",
                capability="digest-pro",
                artifact="text_digest@2.0",
                price=200,
            )
        )
        hits = self.reg.search({"max_price": 50})
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["price"], 50)

    def test_search_service(self):
        self.reg.register(make_listing())
        self.reg.register(
            make_listing(
                seller_id="seller-d",
                capability="image-caption",
                service="image_caption",
                artifact="image_caption@1.0",
            )
        )
        hits = self.reg.search({"service": "text_digest"})
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["service"], "text_digest")

    def test_revoked_excluded_from_search(self):
        lid = self.reg.register(make_listing())
        self.reg.revoke(lid, "no longer offered")
        rec = self.reg.get(lid)
        self.assertEqual(rec["standing"], "revoked")
        self.assertEqual(rec["revoked_reason"], "no longer offered")
        self.assertEqual(self.reg.search({}), [])
        # ...but still visible via get/all (receipts survive)
        self.assertIsNotNone(self.reg.get(lid))
        self.assertEqual(len(self.reg.all()), 1)

    def test_revoke_idempotent(self):
        lid = self.reg.register(make_listing())
        self.reg.revoke(lid, "first reason")
        self.reg.revoke(lid, "second reason")  # silent no-op
        rec = self.reg.get(lid)
        self.assertEqual(rec["standing"], "revoked")
        self.assertEqual(rec["revoked_reason"], "first reason")

    def test_revoke_unknown_raises(self):
        with self.assertRaises(KeyError):
            self.reg.revoke("listing-0000000000000000", "x")

    def test_invalid_listing_refused(self):
        bad = make_listing()
        del bad["terms"]
        with self.assertRaises(KeyError):
            self.reg.register(bad)
        with self.assertRaises(ValueError):
            self.reg.register(make_listing(price=0))
        with self.assertRaises(ValueError):
            self.reg.register(make_listing(price=-5))
        with self.assertRaises(ValueError):
            self.reg.register(make_listing(price="fifty"))
        with self.assertRaises(ValueError):
            self.reg.register(make_listing(acceptance="exact_match"))
        with self.assertRaises(ValueError):
            self.reg.register(make_listing(capability=""))

    def test_registry_survives_recreate(self):
        lid = self.reg.register(make_listing())
        fresh = FileRegistry(Path(self.tmp.name) / "registry.json")
        rec = fresh.get(lid)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["seller_id"], "seller-a")

    def test_revoke_sticky_across_reregister(self):
        lid = self.reg.register(make_listing())
        self.reg.revoke(lid, "gone")
        self.reg.register(make_listing(price=999))
        rec = self.reg.get(lid)
        self.assertEqual(rec["standing"], "revoked")
        self.assertEqual(self.reg.search({}), [])


if __name__ == "__main__":
    unittest.main()
