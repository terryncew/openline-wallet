"""Controlled actors on one host, not independent organizations or hostile OS users."""
import concurrent.futures
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from openline_wallet.crypto import public_key_hex, save_private_key, load_private_key
from openline_wallet.receiver import create_presentation
from openline_wallet.wallet import Wallet
from coordinator import Coordinator, action, digest, git, transaction, provider_process, bank

HERE = Path(__file__).resolve().parent


def actor(home, role, kind, payload, challenge, bundle):
    """Signer subprocess receives only its own credential directory."""
    presentation = create_presentation(bundle=bundle, mandate_id=digest(payload)[:24] + kind,
        subject_id=role, subject_key=load_private_key(Path(home) / "agent.key"),
        action=action(kind, payload), receiver_challenge=challenge)
    return {"bundle": bundle, "presentation": presentation}


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.repo = self.root / "maintenance"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Coordinator fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "calc.py").write_text("def total(items):\n    return 0\n")
        (self.repo / "check.py").write_text("from calc import total\nassert total([2, 3]) == 5\nassert total([]) == 0\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "Frozen bug and acceptance check")
        base = git(self.repo, "rev-parse", "HEAD")
        self.wallets = {}
        self.granted = set()
        parties = {}
        for role in ("buyer", "worker"):
            home = self.root / "credentials" / role
            key = Ed25519PrivateKey.generate()
            save_private_key(home / "agent.key", key)
            w = Wallet.create(self.root / "owners" / role)
            self.wallets[role] = w
            parties[role] = {"principal": w.principal_id, "root": w.root_public_key}
        self.bank_home = self.root / "provider"
        key = Ed25519PrivateKey.generate()
        save_private_key(self.bank_home / "provider.key", key)
        with transaction(self.bank_home / "bank.sqlite") as db:
            db.execute("CREATE TABLE balances(principal TEXT PRIMARY KEY, amount INTEGER NOT NULL CHECK(amount>=0))")
            db.execute("CREATE TABLE transfers(id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            for role, amount in (("buyer", 20000), ("worker", 0)):
                db.execute("INSERT INTO balances VALUES (?, ?)", (parties[role]["principal"], amount))
        self.terms = {"schema": "coordinator.agreement.v1", "task": "Fix total(items) to sum items",
                      "repository": str(self.repo.resolve()), "base": base,
                      "checks": [[sys.executable, "check.py"]], "protected": ["check.py"],
                      "amount": 10000, "currency": "SIM_USD", "deadline": int(time.time()) + 3600,
                      "provider_key": public_key_hex(key), **parties}
        self.c = Coordinator.create(self.root / "coordinator", self.terms)

    def request(self, role, kind, payload, *, signed_payload=None, signer=None):
        signing = signed_payload or payload
        signer = signer or role
        w = self.wallets[signer]
        mid = digest(signing)[:24] + kind
        # Owner grants exact payload-bound scope; agent never receives the root key.
        if signer in self.granted:
            w.revoke(signer)
        self.granted.add(signer)
        w.grant(subject_id=signer,
                subject_public_key=public_key_hex(load_private_key(self.root / "credentials" / signer / "agent.key")),
                scopes=[action(kind, signing)], expires_at=datetime.now(timezone.utc)+timedelta(minutes=10),
                mandate_id=mid)
        challenge = self.c.challenge(role, kind, payload)
        result = subprocess.run([sys.executable, str(HERE / "test_coordinator.py"), "actor",
                                 str(self.root / "credentials" / signer), signer, kind],
                                 input=json.dumps({"payload": signing, "challenge": challenge, "bundle": w.export_bundle()}),
                                 text=True, capture_output=True, check=True, timeout=10)
        return json.loads(result.stdout)

    def send(self, role, kind, payload):
        req = self.request(role, kind, payload)
        return self.c.receive(role, kind, payload, **req)

    def agree(self):
        for role in ("buyer", "worker"):
            self.send(role, "agree", {"job": self.c.job})

    def submit(self, bad=False, protected=False):
        (self.repo / "calc.py").write_text("def total(items):\n    return " + ("0" if bad else "sum(items)") + "\n")
        if protected:
            (self.repo / "check.py").write_text("pass\n")
        git(self.repo, "add", ".")
        if bad and not protected:
            # Distinct incorrect implementation still needs an actual patch.
            (self.repo / "calc.py").write_text("def total(items):\n    return 1\n")
            git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "Worker candidate")
        candidate = git(self.repo, "rev-parse", "HEAD")
        self.send("worker", "submit", {"job": self.c.job, "candidate": candidate})
        return self.c.evaluate(self.repo)

    def accept_payload(self):
        s = self.c.state()
        return {"job": self.c.job, "candidate": s["candidate"], "evaluation": s["evaluation"]}

    def ready(self):
        self.agree()
        self.submit()
        self.send("buyer", "accept", self.accept_payload())

    def balances(self):
        with transaction(self.bank_home / "bank.sqlite") as db:
            return dict(db.execute("SELECT principal, amount FROM balances").fetchall()), db.execute("SELECT count(*) FROM transfers").fetchone()[0]

    def crash_reconcile(self):
        self.ready()
        result = subprocess.run([sys.executable, str(HERE / "coordinator.py"), "crash",
                                 str(self.c.home), str(self.bank_home)], timeout=10)
        assert result.returncode == 73
        self.c = Coordinator(self.c.home)
        assert self.c.state()["status"] == "PENDING"
        balances, count = self.balances()
        assert count == 1 and balances[self.terms["worker"]["principal"]] == 10000
        def unavailable(*_):
            raise TimeoutError("acknowledgment unavailable")
        assert self.c.settle(unavailable)["status"] == "PENDING"
        result = self.c.settle(provider_process(self.bank_home))
        assert result["status"] == "SETTLED"
        for _ in range(3):
            assert Coordinator(self.c.home).settle(provider_process(self.bank_home))["payment"] == result["payment"]
        balances, count = self.balances()
        assert count == 1 and sum(balances.values()) == 20000
        return result


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = Fixture(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_process_death_lost_ack_restart_and_duplicate_retries(self):
        self.f.crash_reconcile()

    def test_agreement_cannot_be_reinitialized(self):
        with self.assertRaises(Exception):
            Coordinator.create(self.f.c.home, {**self.f.terms, "amount": 99999})
        self.assertEqual(Coordinator(self.f.c.home).terms, self.f.terms)

    def test_changed_agreement_rejected(self):
        p = {"job": "0" * 64}
        req = self.f.request("buyer", "agree", p)
        with self.assertRaisesRegex(ValueError, "AGREEMENT_MISMATCH"):
            self.f.c.receive("buyer", "agree", p, **req)

    def test_worker_cannot_impersonate_buyer(self):
        p = {"job": self.f.c.job}
        req = self.f.request("buyer", "agree", p, signer="worker")
        with self.assertRaisesRegex(ValueError, "PARTY_MISMATCH"):
            self.f.c.receive("buyer", "agree", p, **req)

    def test_payload_tampering_stopped_by_wallet(self):
        self.f.agree()
        p = {"job": self.f.c.job, "candidate": "a" * 40}
        req = self.f.request("worker", "submit", p, signed_payload={**p, "candidate": "b" * 40})
        with self.assertRaisesRegex(ValueError, "AUTHORITY_STOPPED"):
            self.f.c.receive("worker", "submit", p, **req)

    def test_airlock_pass_never_pays(self):
        self.f.agree()
        self.assertEqual(self.f.submit()["status"], "ELIGIBLE")
        with self.assertRaisesRegex(ValueError, "BUYER_ACCEPTANCE_REQUIRED"):
            self.f.c.settle(provider_process(self.f.bank_home))
        self.assertEqual(self.f.balances()[1], 0)

    def test_bad_patch_rejected(self):
        self.f.agree()
        self.assertEqual(self.f.submit(bad=True)["status"], "REJECTED")

    def test_protected_check_rewrite_rejected(self):
        self.f.agree()
        self.assertEqual(self.f.submit(protected=True)["status"], "REJECTED")

    def test_worker_cannot_accept_own_result(self):
        self.f.agree()
        self.f.submit()
        p = self.f.accept_payload()
        req = self.f.request("worker", "accept", p)
        with self.assertRaisesRegex(ValueError, "ACCEPTANCE_STATE"):
            self.f.c.receive("worker", "accept", p, **req)

    def test_acceptance_wrong_evaluation_rejected(self):
        self.f.agree()
        self.f.submit()
        p = {**self.f.accept_payload(), "evaluation": "0" * 64}
        req = self.f.request("buyer", "accept", p)
        with self.assertRaisesRegex(ValueError, "ACCEPTANCE_STATE"):
            self.f.c.receive("buyer", "accept", p, **req)

    def test_signed_wrong_payment_and_forgery_stay_pending(self):
        from openline_wallet.crypto import sign_record
        self.f.ready()
        for key, wrong_request in ((Ed25519PrivateKey.generate(), False),
                                   (load_private_key(self.f.bank_home / "provider.key"), True)):
            def forged(op, request):
                return sign_record({"request": {**request, "amount": 1} if wrong_request else request,
                                    "status": "SETTLED"}, key)
            self.assertEqual(self.f.c.settle(forged)["status"], "PENDING")
        self.assertEqual(self.f.balances()[1], 0)

    def test_concurrent_settlement_single_transfer(self):
        self.f.ready()
        def pay(_):
            return Coordinator(self.f.c.home).settle(provider_process(self.f.bank_home))
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(pay, range(4)))
        self.assertTrue(all(x["status"] == "SETTLED" for x in results))
        self.assertEqual(self.f.balances()[1], 1)
        self.assertEqual(sum(self.f.balances()[0].values()), 20000)

    def test_provider_reused_key_changed_amount_rejected(self):
        self.f.ready()
        result = self.f.c.settle(provider_process(self.f.bank_home))
        request = {**result["payment"]["request"], "amount": 1}
        with self.assertRaisesRegex(ValueError, "IDEMPOTENCY_CONFLICT"):
            bank(self.f.bank_home, "transfer", request)
        self.assertEqual(self.f.balances()[1], 1)

    def test_negative_control_new_payment_key_duplicates_transfer(self):
        # Same logical obligation, unsafe retry invents a fresh provider key.
        self.f.ready()
        first = self.f.c.settle(provider_process(self.f.bank_home))
        request = {**first["payment"]["request"], "id": "unsafe-fresh-retry-key"}
        bank(self.f.bank_home, "transfer", request)
        balances, count = self.f.balances()
        self.assertEqual(count, 2)
        self.assertEqual(balances[self.f.terms["worker"]["principal"]], 20000)

    def test_failed_provider_transfer_rolls_back_debit(self):
        request = {"id": self.f.c.job, "buyer": self.f.terms["buyer"]["principal"],
                   "worker": "missing-payee", "amount": 10000, "currency": "SIM_USD"}
        before = self.f.balances()
        with self.assertRaisesRegex(ValueError, "UNKNOWN_PAYEE"):
            bank(self.f.bank_home, "transfer", request)
        self.assertEqual(self.f.balances(), before)

    def test_replayed_agreement_after_restart_rejected(self):
        p = {"job": self.f.c.job}
        req = self.f.request("buyer", "agree", p)
        self.f.c.receive("buyer", "agree", p, **req)
        self.f.c = Coordinator(self.f.c.home)
        with self.assertRaisesRegex(ValueError, "AGREEMENT_STATE"):
            self.f.c.receive("buyer", "agree", p, **req)

    def test_revoked_mandate_cannot_authorize_agreement(self):
        p = {"job": self.f.c.job}
        req = self.f.request("buyer", "agree", p)
        self.f.wallets["buyer"].revoke("buyer")
        old_bundle = req["bundle"]
        req["bundle"] = self.f.wallets["buyer"].export_bundle()
        with self.assertRaisesRegex(ValueError, "AUTHORITY_STOPPED"):
            self.f.c.receive("buyer", "agree", p, **req)
        self.assertEqual(self.f.c.state()["agreed"], [])
        self.f.c = Coordinator(self.f.c.home)
        req["bundle"] = old_bundle
        from openline_wallet.errors import WalletError
        with self.assertRaisesRegex(WalletError, "BUNDLE_HEAD_STALE"):
            self.f.c.receive("buyer", "agree", p, **req)

    def test_crash_before_external_transfer_can_resume(self):
        self.f.ready()
        def unavailable(*_):
            raise TimeoutError()
        self.assertEqual(self.f.c.settle(unavailable)["status"], "PENDING")
        self.assertEqual(self.f.balances()[1], 0)
        result = Coordinator(self.f.c.home).settle(provider_process(self.f.bank_home))
        self.assertEqual(result["status"], "SETTLED")
        self.assertEqual(self.f.balances()[1], 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "actor":
        p = json.load(sys.stdin)
        print(json.dumps(actor(sys.argv[2], sys.argv[3], sys.argv[4], p["payload"], p["challenge"], p["bundle"])))
    else:
        unittest.main()
