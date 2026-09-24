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


if __name__ == "__main__":
    unittest.main()
