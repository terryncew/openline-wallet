"""Focused controls for the commission-work preview.

Run in this repo's unittest style (CI: python -m unittest discover):

  happy path          -> agreement freezes, one settlement, receipt verifies
  rejected work       -> no payment, reservation released
  altered agreement   -> verify refuses (amount / payee / input hash)
  tampered result     -> submit refuses (RECORD_TAMPERED)
  replay settle       -> ALREADY_SETTLED, no duplicate payment
  insufficient funds  -> INSUFFICIENT_ALLOWANCE at commission time
  double reservation  -> ALREADY_RESERVED; parallel jobs cannot double-commit
  agent boundaries    -> budget raise, acceptance rewrite, payee change,
                         price override, self-authorization all refused;
                         seller cannot verify its own work
  revocation          -> new commissions blocked (MANDATE_REVOKED);
                         an already-earned obligation remains payable
  idempotency         -> resubmit refused; rework reuses the recorded output
  reconciliation      -> read-only; re-derives the next step, changes nothing
  funded authority    -> commitments cannot exceed the owner's funded balance,
                         not just the allowance (INSUFFICIENT_OWNER_FUNDS)
  monetary validity   -> budgets and prices must be positive integers,
                         validated before signing or state change
                         (INVALID_AMOUNT); zero-price work is not supported
  concurrent verify   -> two verifications of one submission release once;
                         verification is idempotent
  invariant review    -> re-grant preserves live commitments and refuses
                         reduction under them; settle refuses an unfunded
                         commitment; a crash between the verdict and the
                         release reconciles to exactly one release
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent.parent / "demo" / "commission-work-001"
# COMMISSION_MODULE lets the regression suite run against an older copy of
# the preview (e.g. the frozen 536d1cc code) to demonstrate that each
# regression fails pre-fix and passes post-fix.
_module_path = os.environ.get("COMMISSION_MODULE", str(DEMO_DIR / "commission.py"))
_spec = importlib.util.spec_from_file_location("commission_work", _module_path)
commission = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(commission)


class _T(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home = Path(self.td.name) / "home"
        self.work = Path(self.td.name) / "work"
        self.work.mkdir()

    def tearDown(self):
        self.td.cleanup()

    def cli(self, *argv):
        argv = [argv[0], "--home", str(self.home), *argv[1:]]
        out, err, code = io.StringIO(), io.StringIO(), None
        with redirect_stdout(out), redirect_stderr(err):
            code = commission.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def setup_basic(self, budget=200, price=50):
        code, _, _ = self.cli("init")
        self.assertEqual(code, 0)
        code, _, _ = self.cli("delegate", "--caller", "owner", "--budget", str(budget))
        self.assertEqual(code, 0)
        code, _, _ = self.cli("offer", "--caller", "seller", "--price", str(price))
        self.assertEqual(code, 0)

    def input(self, name, nonce, text="hello world test input"):
        p = self.work / name
        p.write_text(json.dumps({"nonce": nonce}) + "\n" + text + "\n")
        return str(p)

    def commission_job(self, nonce, text="hello world test input"):
        before = set(json.loads((self.home / "jobs.json").read_text())) if (self.home / "jobs.json").exists() else set()
        inp = self.input("in-%s.txt" % nonce, nonce, text)
        code, out, err = self.cli("commission", "--caller", "agent",
                                  "--offer", "offer-text-digest-v1", "--input", inp)
        self.assertEqual(code, 0, err)
        after = set(json.loads((self.home / "jobs.json").read_text()))
        new = after - before
        self.assertEqual(len(new), 1)
        return new.pop()

    def settle(self, job):
        code, _, err = self.cli("work", "--caller", "seller", "--job", job)
        self.assertEqual(code, 0, err)
        code, _, err = self.cli("submit", "--caller", "seller", "--job", job)
        self.assertEqual(code, 0, err)
        code, _, err = self.cli("verify", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)
        code, _, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)
        return job

    def allowances(self):
        return json.loads((self.home / "allowances.json").read_text())

    def agent_allowance(self):
        return list(self.allowances().values())[0]


class HappyPath(_T):
    def test_successful_work_settles_exactly_once(self):
        self.setup_basic()
        job = self.settle(self.commission_job("happy-1"))
        ledger = json.loads((self.home / "ledger.json").read_text())
        owner, seller = [json.loads((self.home / "keys" / ("%s.pub.json" % n)).read_text())["principal"]
                         for n in ("owner", "seller")]
        self.assertEqual(ledger["balances"][owner], 9950)
        self.assertEqual(ledger["balances"][seller], 50)
        self.assertEqual(len(ledger["transfers"]), 1)
        # replay cannot produce a second payment
        code, _, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 2)
        self.assertIn("ALREADY_SETTLED", err)
        ledger2 = json.loads((self.home / "ledger.json").read_text())
        self.assertEqual(ledger2, ledger)
        # receipt verifies
        code, out, err = self.cli("receipt", "--job", job)
        self.assertEqual(code, 0, err)
        self.assertIn("signature verifies against the owner's key", out)


class RejectedWork(_T):
    def test_rejected_work_pays_nothing_and_releases_reservation(self):
        self.setup_basic()
        job = self.commission_job("reject-1")
        code, _, err = self.cli("work", "--caller", "seller", "--job", job, "--wrong-input")
        self.assertEqual(code, 0, err)
        code, _, err = self.cli("submit", "--caller", "seller", "--job", job)
        self.assertEqual(code, 0, err)
        code, out, err = self.cli("verify", "--caller", "owner", "--job", job)
        self.assertEqual(code, 2)  # rejected
        self.assertIn("REJECTED", out)
        code, _, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 2)
        self.assertIn("SETTLEMENT_REFUSED", err)
        ledger = json.loads((self.home / "ledger.json").read_text())
        owner, seller = [json.loads((self.home / "keys" / ("%s.pub.json" % n)).read_text())["principal"]
                         for n in ("owner", "seller")]
        self.assertEqual(ledger["balances"][owner], 10000)
        self.assertEqual(ledger["balances"][seller], 0)
        self.assertEqual(ledger["transfers"], [])
        allow = self.agent_allowance()
        self.assertEqual(allow["reserved"], 0)
        self.assertEqual(allow["spent"], 0)


class Tampering(_T):
    def test_altered_agreement_amount_refused(self):
        self.setup_basic()
        job = self.commission_job("tamp-1")
        self.cli("work", "--caller", "seller", "--job", job)
        self.cli("submit", "--caller", "seller", "--job", job)
        code, out, _ = self.cli("verify", "--caller", "owner", "--job", job,
                                "--tamper-agreement", "amount")
        self.assertEqual(code, 2)
        self.assertIn("agreement_integrity: FAIL", out)

    def test_altered_payee_refused(self):
        self.setup_basic()
        job = self.commission_job("tamp-2")
        self.cli("work", "--caller", "seller", "--job", job)
        self.cli("submit", "--caller", "seller", "--job", job)
        code, out, _ = self.cli("verify", "--caller", "owner", "--job", job, "--tamper-payee")
        self.assertEqual(code, 2)
        self.assertIn("payee_matches_offer: FAIL", out)

    def test_altered_input_hash_refused(self):
        self.setup_basic()
        job = self.commission_job("tamp-3")
        self.cli("work", "--caller", "seller", "--job", job)
        self.cli("submit", "--caller", "seller", "--job", job)
        code, out, _ = self.cli("verify", "--caller", "owner", "--job", job, "--tamper-input-hash")
        self.assertEqual(code, 2)
        self.assertIn("agreement_integrity: FAIL", out)

    def test_altered_signed_result_refused_at_submit(self):
        self.setup_basic()
        job = self.commission_job("tamp-4")
        self.cli("work", "--caller", "seller", "--job", job)
        code, _, err = self.cli("submit", "--caller", "seller", "--job", job, "--tamper-result")
        self.assertEqual(code, 2)
        self.assertIn("RECORD_TAMPERED", err)

    def test_nonce_reuse_refused(self):
        self.setup_basic()
        self.commission_job("nonce-dup")
        inp = self.input("in-dup2.txt", "nonce-dup", "different text")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp)
        self.assertEqual(code, 2)
        self.assertIn("NONCE_REUSED", err)


class Funds(_T):
    def test_insufficient_allowance_refused(self):
        self.setup_basic(budget=40, price=50)
        inp = self.input("in-poor.txt", "poor-1")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp)
        self.assertEqual(code, 2)
        self.assertIn("INSUFFICIENT_ALLOWANCE", err)

    def test_parallel_jobs_cannot_double_commit(self):
        self.setup_basic(budget=200, price=50)
        for n in ("par-1", "par-2", "par-3", "par-4"):
            self.commission_job(n)
        allow = self.agent_allowance()
        self.assertEqual(allow["reserved"], 200)
        inp = self.input("in-par5.txt", "par-5")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp)
        self.assertEqual(code, 2)
        self.assertIn("ALREADY_RESERVED", err)
        # the four reservations are still intact
        self.assertEqual(self.agent_allowance()["reserved"], 200)


class AgentBoundaries(_T):
    def test_agent_cannot_raise_budget(self):
        self.setup_basic()
        inp = self.input("in-b1.txt", "b1")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp,
                                "--raise-budget", "1000")
        self.assertEqual(code, 2)
        self.assertIn("BUDGET_INCREASE_REFUSED", err)

    def test_agent_cannot_rewrite_acceptance(self):
        self.setup_basic()
        inp = self.input("in-b2.txt", "b2")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp,
                                "--rewrite-acceptance")
        self.assertEqual(code, 2)
        self.assertIn("ACCEPTANCE_REWRITE_REFUSED", err)

    def test_agent_cannot_change_payee(self):
        self.setup_basic()
        inp = self.input("in-b3.txt", "b3")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp,
                                "--payee", "attacker")
        self.assertEqual(code, 2)
        self.assertIn("PAYEE_CHANGE_REFUSED", err)

    def test_agent_cannot_override_price(self):
        self.setup_basic()
        inp = self.input("in-b4.txt", "b4")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp,
                                "--price-override", "1")
        self.assertEqual(code, 2)
        self.assertIn("PRICE_OVERRIDE_REFUSED", err)

    def test_agent_cannot_authorize_itself(self):
        self.setup_basic()
        inp = self.input("in-b5.txt", "b5")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp,
                                "--self-authorize")
        self.assertEqual(code, 2)
        self.assertIn("SELF_AUTHORIZATION_REFUSED", err)

    def test_seller_cannot_verify_own_work(self):
        self.setup_basic()
        job = self.commission_job("b6")
        self.cli("work", "--caller", "seller", "--job", job)
        self.cli("submit", "--caller", "seller", "--job", job)
        code, _, err = self.cli("verify", "--caller", "seller", "--job", job)
        self.assertEqual(code, 2)
        self.assertIn("CALLER_NOT_AUTHORIZED", err)

    def test_seller_cannot_revoke_agent(self):
        self.setup_basic()
        code, _, err = self.cli("revoke", "--caller", "seller")
        self.assertEqual(code, 2)
        self.assertIn("CALLER_NOT_AUTHORIZED", err)


class Revocation(_T):
    def test_revoked_agent_blocked_but_earned_obligation_payable(self):
        self.setup_basic()
        job = self.commission_job("rev-1")
        self.cli("work", "--caller", "seller", "--job", job)
        self.cli("submit", "--caller", "seller", "--job", job)
        self.cli("verify", "--caller", "owner", "--job", job)
        code, _, _ = self.cli("revoke", "--caller", "owner")
        self.assertEqual(code, 0)
        # new work is blocked
        inp = self.input("in-rev2.txt", "rev-2")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp)
        self.assertEqual(code, 2)
        self.assertIn("MANDATE_REVOKED", err)
        # the already-earned obligation is not erased
        code, out, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)
        self.assertIn("already earned", out)
        ledger = json.loads((self.home / "ledger.json").read_text())
        seller = json.loads((self.home / "keys" / "seller.pub.json").read_text())["principal"]
        self.assertEqual(ledger["balances"][seller], 50)


class Idempotency(_T):
    def test_resubmit_refused_and_rework_reuses_output(self):
        self.setup_basic()
        job = self.commission_job("idem-1")
        code, out, err = self.cli("work", "--caller", "seller", "--job", job)
        self.assertEqual(code, 0, err)
        code, out, err = self.cli("work", "--caller", "seller", "--job", job)
        self.assertEqual(code, 0, err)
        self.assertIn("no duplicate dispatch", out)
        code, _, err = self.cli("submit", "--caller", "seller", "--job", job)
        self.assertEqual(code, 0, err)
        code, _, err = self.cli("submit", "--caller", "seller", "--job", job)
        self.assertEqual(code, 2)
        self.assertIn("WORK_ALREADY_SUBMITTED", err)
        # the recorded submission still verifies and settles exactly once
        code, _, err = self.cli("verify", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)
        code, _, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)


class Reconciliation(_T):
    def test_reconcile_is_readonly_and_derives_next_steps(self):
        self.setup_basic()
        j1 = self.commission_job("rec-1")
        j2 = self.commission_job("rec-2")
        self.cli("work", "--caller", "seller", "--job", j1)   # worked, not submitted
        self.cli("work", "--caller", "seller", "--job", j2)
        self.cli("submit", "--caller", "seller", "--job", j2)
        self.cli("verify", "--caller", "owner", "--job", j2)  # verified, not settled
        before = (self.home / "jobs.json").read_text()
        code, out, err = self.cli("reconcile")
        self.assertEqual(code, 0, err)
        self.assertIn(j1, out)
        self.assertIn(j2, out)
        self.assertIn("no payment made", out)
        # nothing changed: no side effects
        self.assertEqual((self.home / "jobs.json").read_text(), before)
        # and the derived steps complete exactly once afterwards
        self.cli("submit", "--caller", "seller", "--job", j1)
        self.cli("verify", "--caller", "owner", "--job", j1)
        self.cli("settle", "--caller", "owner", "--job", j1)
        self.cli("settle", "--caller", "owner", "--job", j2)
        code, _, err = self.cli("settle", "--caller", "owner", "--job", j1)
        self.assertIn("ALREADY_SETTLED", err)
        ledger = json.loads((self.home / "ledger.json").read_text())
        self.assertEqual(len(ledger["transfers"]), 2)


class RegressionFixes(_T):
    """The three failures reproduced at 536d1cc. Each test fails against the
    frozen pre-fix code (COMMISSION_MODULE pointing at the 536d1cc copy)
    and passes after the fix."""

    def test_spent_plus_reserved_accounting(self):
        # Budget 50, price 50: one settlement spends the whole budget. A
        # second sequential commission must be refused — spent money is gone.
        self.setup_basic(budget=50, price=50)
        self.settle(self.commission_job("sp-1"))
        allow = self.agent_allowance()
        self.assertEqual((allow["granted"], allow["reserved"], allow["spent"]), (50, 0, 50))
        inp = self.input("in-sp2.txt", "sp-2")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp)
        self.assertEqual(code, 2, "second 50-unit commission on a spent 50 budget must be refused")
        self.assertIn("INSUFFICIENT_ALLOWANCE", err)
        # nothing moved: still exactly one settlement
        ledger = json.loads((self.home / "ledger.json").read_text())
        self.assertEqual(len(ledger["transfers"]), 1)
        self.assertEqual(self.agent_allowance()["spent"], 50)

    def _crash_settle(self, job):
        """Leave the exact state the old code left behind: the transfer is
        written to the ledger, but the process dies before the allowance
        and job records are updated."""
        jobs = json.loads((self.home / "jobs.json").read_text())
        job_rec = jobs[job]
        settle_id = "settle:" + job_rec["agreement_hash"]
        ledger = json.loads((self.home / "ledger.json").read_text())
        owner = json.loads((self.home / "keys" / "owner.pub.json").read_text())["principal"]
        seller = json.loads((self.home / "keys" / "seller.pub.json").read_text())["principal"]
        ledger["balances"][owner] -= 50
        ledger["balances"][seller] += 50
        ledger["transfers"].append({
            "settlement_id": settle_id,
            "job_id": job,
            "agreement_hash": job_rec["agreement_hash"],
            "amount": 50,
            "currency": "SIM_USD",
            "from": owner,
            "to": seller,
            "at": "2026-09-23T00:00:00+00:00",
        })
        (self.home / "ledger.json").write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n")

    def test_crash_mid_settle_never_pays_twice(self):
        self.setup_basic()
        job = self.commission_job("cr-1")
        self.cli("work", "--caller", "seller", "--job", job)
        self.cli("submit", "--caller", "seller", "--job", job)
        self.cli("verify", "--caller", "owner", "--job", job)
        self._crash_settle(job)
        # reconciliation must report the committed state truthfully: it may
        # direct idempotent completion, never a fresh settlement.
        code, out, err = self.cli("reconcile")
        self.assertEqual(code, 0, err)
        self.assertIn("COMMITTED", out)
        self.assertIn("never a second transfer", out)
        # the retry completes the records and appends no second transfer
        code, out, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)
        ledger = json.loads((self.home / "ledger.json").read_text())
        self.assertEqual(len(ledger["transfers"]), 1,
                         "retry after a mid-settle crash must not produce a second transfer")
        owner = json.loads((self.home / "keys" / "owner.pub.json").read_text())["principal"]
        seller = json.loads((self.home / "keys" / "seller.pub.json").read_text())["principal"]
        self.assertEqual(ledger["balances"][owner], 9950)
        self.assertEqual(ledger["balances"][seller], 50)
        allow = self.agent_allowance()
        self.assertEqual((allow["reserved"], allow["spent"]), (0, 50))
        # and a further replay is still refused
        code, _, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 2)
        self.assertIn("ALREADY_SETTLED", err)
        self.assertEqual(len(json.loads((self.home / "ledger.json").read_text())["transfers"]), 1)

    def test_substituted_payee_after_verify_refused(self):
        self.setup_basic()
        job = self.commission_job("py-1")
        self.cli("work", "--caller", "seller", "--job", job)
        self.cli("submit", "--caller", "seller", "--job", job)
        code, _, err = self.cli("verify", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)
        # Attack: rewrite the payee in the mutable jobs.json copy after a
        # successful verification. Settlement must read the payee only from
        # the authenticated (signed) agreement.
        jobs = json.loads((self.home / "jobs.json").read_text())
        jobs[job]["agreement"]["payee"] = "stranger-principal"
        (self.home / "jobs.json").write_text(json.dumps(jobs, indent=1, sort_keys=True) + "\n")
        code, _, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 2, "settlement with a substituted payee must be refused")
        self.assertIn("AGREEMENT_NOT_AUTHENTIC", err)
        ledger = json.loads((self.home / "ledger.json").read_text())
        self.assertEqual(ledger["transfers"], [])
        owner = json.loads((self.home / "keys" / "owner.pub.json").read_text())["principal"]
        self.assertEqual(ledger["balances"][owner], 10000)


class ConcurrentReservation(_T):
    def test_concurrent_reservations_cannot_overcommit(self):
        # Eight commissions race for a 200 budget at 50 each. The writer
        # lock serializes the read-modify-write; exactly four win.
        self.setup_basic(budget=200, price=50)
        orig_read = commission._read_json

        def slow_read(path, default):
            value = orig_read(path, default)
            if str(path).endswith("allowances.json"):
                time.sleep(0.05)  # widen the race window
            return value

        commission._read_json = slow_read
        barrier = threading.Barrier(8)
        results = []
        try:
            def one(i):
                barrier.wait()
                inp = self.input("in-cc-%d.txt" % i, "cc-%d" % i, "concurrent text %d" % i)
                code, _, _ = self.cli("commission", "--caller", "agent",
                                      "--offer", "offer-text-digest-v1",
                                      "--input", inp)
                results.append(code)

            threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            commission._read_json = orig_read
        self.assertEqual(results.count(0), 4,
                         "exactly four of eight racing commissions may reserve: %r" % (results,))
        self.assertEqual(results.count(2), 4)
        jobs = json.loads((self.home / "jobs.json").read_text())
        self.assertEqual(len(jobs), 4)
        allow = self.agent_allowance()
        self.assertEqual(allow["reserved"], 200)
        self.assertLessEqual(allow["reserved"] + allow["spent"], allow["granted"])


class AccountingReview(_T):
    """Terrynce's accounting review at 8ca2218: three new reproduced cases
    plus the invariant review of every writer of balances and reservations.
    Each regression fails against the frozen 8ca2218 code (COMMISSION_MODULE
    pointing at the 8ca2218 copy) and passes after the fix.

    The invariant, restated: one job cannot reserve, release, or settle
    twice; amounts remain valid; commitments cannot exceed funded authority
    (the allowance AND the owner's funded account balance).
    """

    def _principal(self, name):
        return json.loads((self.home / "keys" / ("%s.pub.json" % name)).read_text())["principal"]

    def _balances(self):
        return json.loads((self.home / "ledger.json").read_text())["balances"]

    def _signed_offer(self, price):
        """A validly seller-signed offer written straight to offers.json
        (exercises the commission-time defense for offers no current client
        would produce)."""
        seller = self._principal("seller")
        body = {
            "schema": "commission.offer.v1",
            "offer_id": "offer-text-digest-v1",
            "seller": seller,
            "service": commission.SERVICE,
            "deliverable": commission.DELIVERABLE,
            "acceptance": commission.ACCEPTANCE,
            "price": price,
            "currency": commission.CURRENCY + " (simulated)",
            "note": "results only; the seller retains its implementation",
        }
        sig = commission.sign_record(body, commission._load_key(self.home, "seller"))
        offers = json.loads((self.home / "offers.json").read_text())
        offers["offer-text-digest-v1"] = {"record": body, "signature": sig}
        (self.home / "offers.json").write_text(json.dumps(offers, indent=1, sort_keys=True) + "\n")

    def _plant_rejected_verdict(self, job):
        """Leave exactly the state a crash between the verdict write and the
        reservation release leaves: a signed REJECTED verdict on record, the
        reservation still held."""
        jobs = json.loads((self.home / "jobs.json").read_text())
        jr = jobs[job]
        verdict = {
            "schema": "commission.verdict.v1",
            "job_id": job,
            "verdict": "rejected",
            "agreement_hash": jr["agreement_hash"],
            "checks": [],
            "verified_by": "owner",
            "at": "2026-09-23T00:00:00+00:00",
        }
        sig = commission.sign_record(verdict, commission._load_key(self.home, "owner"))
        jr["verdict"] = {"record": verdict, "signature": sig}
        jr["status"] = "VERIFIED_REJECTED"
        jr["events"].append({"type": "VERIFIED", "at": "2026-09-23T00:00:00+00:00",
                             "verdict": "rejected"})
        (self.home / "jobs.json").write_text(json.dumps(jobs, indent=1, sort_keys=True) + "\n")

    # -- 1. funded authority -------------------------------------------------
    def test_commitment_cannot_exceed_funded_balance(self):
        # Owner balance 10,000, allowance 20,000, price 15,000: the allowance
        # covers it but the funded account does not. Commission is refused.
        self.cli("init")
        code, _, err = self.cli("delegate", "--caller", "owner", "--budget", "20000")
        self.assertEqual(code, 0, err)
        code, _, err = self.cli("offer", "--caller", "seller", "--price", "15000")
        self.assertEqual(code, 0, err)
        inp = self.input("in-fund.txt", "fund-1")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp)
        self.assertEqual(code, 2,
                         "a 15,000 commitment against a 10,000 funded balance must be "
                         "refused even with a 20,000 allowance")
        self.assertIn("INSUFFICIENT_OWNER_FUNDS", err)
        # nothing moved: no job, no reservation, balance untouched
        self.assertEqual(self._balances()[self._principal("owner")], 10000)
        self.assertEqual(self.agent_allowance()["reserved"], 0)
        self.assertEqual(json.loads((self.home / "jobs.json").read_text()), {})

    def test_commitment_within_funded_balance_succeeds(self):
        # The control: a funded commitment still commissions and settles.
        self.setup_basic(budget=200, price=50)
        self.settle(self.commission_job("fund-ok-1"))
        self.assertEqual(self._balances()[self._principal("owner")], 9950)

    # -- 2. monetary validity ------------------------------------------------
    def test_negative_offer_price_refused_before_signing(self):
        self.cli("init")
        code, _, err = self.cli("offer", "--caller", "seller", "--price", "-50")
        self.assertEqual(code, 2)
        self.assertIn("INVALID_AMOUNT", err)
        self.assertEqual(json.loads((self.home / "offers.json").read_text()), {},
                         "no offer may be signed or stored with a negative price")

    def test_zero_price_not_supported(self):
        self.cli("init")
        code, _, err = self.cli("offer", "--caller", "seller", "--price", "0")
        self.assertEqual(code, 2)
        self.assertIn("INVALID_AMOUNT", err)

    def test_negative_budget_refused(self):
        self.cli("init")
        code, _, err = self.cli("delegate", "--caller", "owner", "--budget", "-100")
        self.assertEqual(code, 2)
        self.assertIn("INVALID_AMOUNT", err)
        self.assertEqual(self.allowances(), {})

    def test_commission_refuses_negative_price_offer(self):
        # A validly signed but negative-price offer (no current client writes
        # one; the defense sits at commission time): nothing may be reserved.
        self.setup_basic()
        self._signed_offer(-50)
        inp = self.input("in-neg.txt", "neg-1")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--offer", "offer-text-digest-v1", "--input", inp)
        self.assertEqual(code, 2, "commission against a negative-price offer must be refused")
        self.assertIn("OFFER_INVALID", err)
        self.assertEqual(self.agent_allowance()["reserved"], 0)

    # -- 3. concurrent verification ------------------------------------------
    def test_concurrent_verify_of_wrong_submission_releases_once(self):
        self.setup_basic()
        job = self.commission_job("cv-1")
        self.cli("work", "--caller", "seller", "--job", job, "--wrong-input")
        self.cli("submit", "--caller", "seller", "--job", job)
        orig_read = commission._read_json

        def slow_read(path, default):
            value = orig_read(path, default)
            if str(path).endswith("jobs.json"):
                time.sleep(0.05)  # widen the race window
            return value

        commission._read_json = slow_read
        barrier = threading.Barrier(2)
        results = []
        try:
            def one():
                barrier.wait()
                code, out, err = self.cli("verify", "--caller", "owner", "--job", job)
                results.append((code, out, err))

            threads = [threading.Thread(target=one) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        finally:
            commission._read_json = orig_read
        codes = sorted(r[0] for r in results)
        self.assertEqual(codes, [2, 2], "both verifications reject: %r" % (results,))
        allow = self.agent_allowance()
        self.assertEqual(allow["reserved"], 0,
                         "two concurrent rejections must release exactly once, "
                         "never twice: reserved=%r" % (allow["reserved"],))
        self.assertEqual(allow.get("released_jobs", []).count(job), 1)
        jobs = json.loads((self.home / "jobs.json").read_text())
        events = [e["type"] for e in jobs[job]["events"]]
        self.assertEqual(events.count("RESERVATION_RELEASED"), 1)

    def test_verify_accept_is_idempotent(self):
        self.setup_basic()
        job = self.commission_job("va-1")
        self.cli("work", "--caller", "seller", "--job", job)
        self.cli("submit", "--caller", "seller", "--job", job)
        code, _, err = self.cli("verify", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)
        code, out, err = self.cli("verify", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)
        self.assertIn("already verified", out)
        # the job still settles exactly once afterwards
        code, _, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads((self.home / "ledger.json").read_text())["transfers"]), 1)

    # -- invariant review: every writer --------------------------------------
    def test_regrant_preserves_commitments_and_refuses_reduction(self):
        self.setup_basic(budget=200, price=50)
        self.commission_job("rg-1")  # reserved 50
        code, _, _ = self.cli("revoke", "--caller", "owner")
        self.assertEqual(code, 0)
        code, _, err = self.cli("delegate", "--caller", "owner", "--budget", "300")
        self.assertEqual(code, 0, err)
        allow = self.agent_allowance()
        self.assertEqual((allow["granted"], allow["reserved"], allow["spent"]), (300, 50, 0),
                         "re-granting must not wipe live commitments")
        code, _, _ = self.cli("revoke", "--caller", "owner")
        self.assertEqual(code, 0)
        code, _, err = self.cli("delegate", "--caller", "owner", "--budget", "40")
        self.assertEqual(code, 2, "a grant below live commitments must be refused")
        self.assertIn("ALLOWANCE_REDUCTION_REFUSED", err)
        self.assertEqual(self.agent_allowance()["granted"], 300)

    def test_settle_refuses_unfunded_commitment(self):
        self.setup_basic()
        job = self.commission_job("uf-1")
        self.cli("work", "--caller", "seller", "--job", job)
        self.cli("submit", "--caller", "seller", "--job", job)
        self.cli("verify", "--caller", "owner", "--job", job)
        # Corrupt the ledger so the funded balance no longer covers the
        # commitment: settlement must refuse rather than drive it negative.
        ledger = json.loads((self.home / "ledger.json").read_text())
        owner = self._principal("owner")
        ledger["balances"][owner] = 10
        (self.home / "ledger.json").write_text(json.dumps(ledger, indent=1, sort_keys=True) + "\n")
        code, _, err = self.cli("settle", "--caller", "owner", "--job", job)
        self.assertEqual(code, 2)
        self.assertIn("SETTLEMENT_REFUSED", err)
        ledger2 = json.loads((self.home / "ledger.json").read_text())
        self.assertEqual(ledger2["transfers"], [])
        self.assertEqual(ledger2["balances"][owner], 10)

    def test_crash_between_verdict_and_release_reconciles_once(self):
        self.setup_basic()
        job = self.commission_job("cr2-1")
        self.cli("work", "--caller", "seller", "--job", job, "--wrong-input")
        self.cli("submit", "--caller", "seller", "--job", job)
        self._plant_rejected_verdict(job)
        self.assertEqual(self.agent_allowance()["reserved"], 50)
        code, out, err = self.cli("verify", "--caller", "owner", "--job", job)
        self.assertEqual(code, 2, err)  # still rejected
        allow = self.agent_allowance()
        self.assertEqual(allow["reserved"], 0,
                         "the retry must complete the release exactly once")
        self.assertEqual(allow.get("released_jobs", []), [job])
        # a third verify changes nothing
        code, _, _ = self.cli("verify", "--caller", "owner", "--job", job)
        self.assertEqual(code, 2)
        self.assertEqual(self.agent_allowance()["reserved"], 0)
        self.assertEqual(len(self.agent_allowance().get("released_jobs", [])), 1)

    def test_crash_between_release_and_verdict_never_double_releases(self):
        self.setup_basic()
        job = self.commission_job("cr2-2")
        self.cli("work", "--caller", "seller", "--job", job, "--wrong-input")
        self.cli("submit", "--caller", "seller", "--job", job)
        # The release was written but the verdict never was.
        allowances = self.allowances()
        agent = next(iter(allowances))
        allowances[agent]["reserved"] -= 50
        allowances[agent].setdefault("released_jobs", []).append(job)
        (self.home / "allowances.json").write_text(json.dumps(allowances, indent=1, sort_keys=True) + "\n")
        code, _, err = self.cli("verify", "--caller", "owner", "--job", job)
        self.assertEqual(code, 2, err)  # rejected
        allow = self.agent_allowance()
        self.assertEqual(allow["reserved"], 0,
                         "the retry must not release a second time")
        self.assertEqual(len(allow.get("released_jobs", [])), 1)
        jobs = json.loads((self.home / "jobs.json").read_text())
        self.assertEqual(jobs[job]["status"], "VERIFIED_REJECTED")
        self.assertEqual(jobs[job]["verdict"]["record"]["verdict"], "rejected")


if __name__ == "__main__":
    unittest.main()
