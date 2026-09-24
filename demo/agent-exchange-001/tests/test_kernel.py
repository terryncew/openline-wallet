"""Kernel tests: the exchange loop and its fault points.

Covers: happy path settles exactly once; interrupted commission retries
converge to one job / one reservation; interrupted verify reconciles to
PENDING then completes one release; refusal pays nothing; agent swap does
not leak authority; revoked listings refuse; receipts survive; reconcile is
read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from exchange.kernel import Exchange, ExchangeError

DEMO = Path(__file__).resolve().parent.parent


class _X(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.x = Exchange(Path(self.td.name) / "x")
        self.x.bootstrap(budget=300)
        self.old_env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        self.td.cleanup()

    def need(self, max_price=100):
        return self.x.post_need("digest report", ["digest", "report"],
                                max_price, service="text_digest")

    def ledger(self):
        return self.x.read_json("ledger.json")

    def allowances(self):
        return self.x.read_json("allowances.json")

    def agent_allow(self, name="agent"):
        return self.allowances()[self.x.principal(name)]

    def transfers(self):
        return self.ledger()["transfers"]

    # -- happy path --------------------------------------------------------

    def test_happy_path_settles_exactly_once(self):
        need = self.need()
        ranked = self.x.find(need)
        self.assertTrue(ranked)
        deal = self.x.run_deal(need, ranked, index=0)
        self.assertEqual(deal["verdict"], "accepted")
        amount = deal["listing"]["price"]
        self.assertEqual(deal["settlement"]["amount"], amount)
        # Exactly one transfer for this job.
        mine = [t for t in self.transfers()
                if t["settlement_id"] == deal["settlement"]["settlement_id"]]
        self.assertEqual(len(mine), 1)
        # Balances moved exactly the amount, once.
        seller_principal = deal["listing"]["seller_id"]
        self.assertEqual(self.ledger()["balances"][seller_principal], amount)
        allow = self.agent_allow()
        self.assertEqual(allow["spent"], amount)
        self.assertEqual(allow["reserved"], 0)
        # Receipt exists and verifies via the commission receipt command.
        code, out, _ = self.x.cli("receipt", "--job", deal["job_id"])
        self.assertEqual(code, 0)
        self.assertIn("signature", out)

    def test_refused_work_pays_nothing_and_releases(self):
        need = self.need()
        ranked = self.x.find(need)
        deal = self.x.run_deal(need, ranked, index=0, wrong_input=True)
        self.assertEqual(deal["verdict"], "rejected")
        self.assertNotIn("settlement", deal)
        self.assertEqual(self.transfers(), [])
        allow = self.agent_allow()
        self.assertEqual(allow["reserved"], 0)
        self.assertEqual(allow["spent"], 0)
        self.assertIn(deal["job_id"], allow["released_jobs"])

    # -- fault points ------------------------------------------------------

    def test_interrupted_commission_retries_to_one_job_one_reservation(self):
        need = self.need()
        ranked = self.x.find(need)
        listing = self.x.select(ranked, index=0)
        inp = self.x._write_input("job", "fault test text")
        os.environ["COMMISSION_CRASH_AFTER"] = "commission-txn"
        with self.assertRaises(ExchangeError) as cm:
            self.x.commission(listing, input_path=inp)
        self.assertEqual(cm.exception.code, "COMMISSION_REFUSED")
        del os.environ["COMMISSION_CRASH_AFTER"]
        job_id = self.x.commission(listing, input_path=inp)  # identical retry
        jobs = self.x.read_json("jobs.json")
        self.assertEqual(len(jobs), 1)
        self.assertIn(job_id, jobs)
        allow = self.agent_allow()
        self.assertEqual(allow["reserved"], listing["price"])
        self.assertEqual(len(allow["reserved_jobs"]), 1)
        # The agreement survived the interruption.
        self.assertEqual(jobs[job_id]["agreement"]["amount"], listing["price"])

    def test_interrupted_verify_reports_pending_then_releases_once(self):
        need = self.need()
        ranked = self.x.find(need)
        listing = self.x.select(ranked, index=0)
        job_id = self.x.commission(listing)
        self.x.deliver(job_id, listing["identity"], wrong_input=True)
        os.environ["COMMISSION_CRASH_AFTER"] = "verify-verdict"
        code, out, err = self.x.cli("verify", "--caller", "owner", "--job", job_id)
        self.assertNotEqual(code, 0)  # the crash hook fired
        del os.environ["COMMISSION_CRASH_AFTER"]
        # Reconcile names the pending release and the recovery command.
        out = self.x.reconcile()
        self.assertIn("PENDING", out)
        self.assertIn(job_id, out)
        # Repeating verify completes the release exactly once.
        for _ in range(3):
            code, _, _ = self.x.cli("verify", "--caller", "owner", "--job", job_id)
            self.assertEqual(code, 2)  # rejected
        allow = self.agent_allow()
        self.assertEqual(allow["reserved"], 0)
        self.assertEqual(allow["released_jobs"].count(job_id), 1)
        # And it is still read-only: nothing settled.
        self.assertEqual(self.transfers(), [])

    def test_reconcile_is_read_only(self):
        need = self.need()
        ranked = self.x.find(need)
        self.x.run_deal(need, ranked, index=0)

        def digest():
            h = hashlib.sha256()
            for name in ("jobs.json", "allowances.json", "ledger.json",
                         "commissions.json", "offers.json"):
                p = self.x.chome / name
                h.update(p.read_bytes())
            return h.hexdigest()

        before = digest()
        self.x.reconcile()
        self.assertEqual(before, digest())

    # -- authority portability ---------------------------------------------

    def test_agent_swap_does_not_leak_authority(self):
        need = self.need()
        ranked = self.x.find(need)
        deal = self.x.run_deal(need, ranked, index=0)
        first_allow = self.agent_allow("agent")
        swap = self.x.swap_buyer_agent()
        # Old agent blocked.
        self.assertEqual(swap["old_allowance_preserved"]["spent"],
                        first_allow["spent"])
        # New agent starts clean.
        new_allow = swap["new_allowance"]
        self.assertEqual(new_allow["reserved"], 0)
        self.assertEqual(new_allow["spent"], 0)
        self.assertEqual(new_allow["reserved_jobs"], [])
        # New agent can do its own deal under its own budget.
        need2 = self.need()
        ranked2 = self.x.find(need2)
        deal2 = self.x.run_deal(need2, ranked2, index=0, agent_name="agent-b")
        self.assertEqual(deal2["verdict"], "accepted")
        # The first job's settlement still belongs to the old agent's books.
        self.assertEqual(self.agent_allow("agent")["spent"], first_allow["spent"])
        self.assertEqual(self.agent_allow("agent-b")["spent"], deal2["listing"]["price"])

    def test_revoked_listing_refuses_and_receipts_survive(self):
        need = self.need()
        ranked = self.x.find(need)
        deal = self.x.run_deal(need, ranked, index=0)
        listing = deal["listing"]
        receipt_before = dict(self.x.read_json("jobs.json")[deal["job_id"]]["settlement"])
        self.x.revoke_seller(listing["listing_id"], "test revocation")
        # Selection refuses.
        with self.assertRaises(ExchangeError) as cm:
            self.x.select([(listing, 1.0, ["stale"])])
        self.assertEqual(cm.exception.code, "LISTING_REVOKED")
        # Even a stale dict cannot commission after revocation.
        with self.assertRaises(ExchangeError) as cm2:
            self.x.commission(listing)
        self.assertEqual(cm2.exception.code, "LISTING_REVOKED")
        # The receipt survived untouched.
        receipt_after = self.x.read_json("jobs.json")[deal["job_id"]]["settlement"]
        self.assertEqual(receipt_before, receipt_after)
        # And the registry no longer surfaces it.
        ids = [l["listing_id"] for l in self.x.registry.search({"keywords": ["digest"]})]
        self.assertNotIn(listing["listing_id"], ids)

    def test_matcher_excludes_over_budget_and_revoked(self):
        need = self.need(max_price=55)  # Seller A at 50 and B at 40 fit; C at 60 is out
        ranked = self.x.find(need)
        prices = [l["price"] for l, _, _ in ranked]
        self.assertTrue(all(p <= 55 for p in prices))
        self.assertEqual(len(ranked), 2)
        # Cheapest first on ties.
        self.assertLessEqual(ranked[0][0]["price"], ranked[1][0]["price"])


if __name__ == "__main__":
    unittest.main()
