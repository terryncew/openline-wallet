"""Bounded local experiment. Trusted coordinator/evaluator; simulated funds only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import contextmanager

from openline_wallet.canonical import canonical_json
from openline_wallet.crypto import record_hash, sign_record, verify_record, load_private_key
from openline_wallet.receiver import ReferenceGate


def integer_evidence(value):
    """Wallet canonical records forbid floats; retain timing as decimal text."""
    if isinstance(value, float):
        return str(value)
    if isinstance(value, dict):
        return {k: integer_evidence(v) for k, v in value.items()}
    if isinstance(value, list):
        return [integer_evidence(v) for v in value]
    return value


def digest(value):
    return record_hash(value)


def action(kind, payload):
    return f"job:{kind}:{digest(payload)}"


@contextmanager
def transaction(path):
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class Coordinator:
    def __init__(self, home):
        self.home = Path(home)
        self.path = self.home / "coordinator.sqlite"
        self.gate = ReferenceGate("coordinator-001")
        with transaction(self.path) as db:
            row = db.execute("SELECT body FROM agreement").fetchone()
            self.terms = json.loads(row[0])
            self.job = digest(self.terms)
            for role in ("buyer", "worker"):
                p = self.terms[role]
                self.gate.pin_principal(p["principal"], p["root"])

    @classmethod
    def create(cls, home, terms):
        home = Path(home)
        home.mkdir(parents=True, exist_ok=True)
        if terms["buyer"]["principal"] == terms["worker"]["principal"]:
            raise ValueError("DISTINCT_PARTIES_REQUIRED")
        if type(terms["amount"]) is not int or terms["amount"] <= 0 or terms["currency"] != "SIM_USD":
            raise ValueError("SIMULATED_POSITIVE_INTEGER_AMOUNT_REQUIRED")
        if not terms["checks"] or not terms["protected"]:
            raise ValueError("CHECKS_REQUIRED")
        # Exclusive initialization: an existing agreement is never replaced.
        with transaction(home / "coordinator.sqlite") as db:
            db.execute("CREATE TABLE agreement(body TEXT NOT NULL)")
            db.execute("INSERT INTO agreement VALUES (?)", (canonical_json(terms).decode(),))
            db.execute("CREATE TABLE state(body TEXT NOT NULL)")
            db.execute("INSERT INTO state VALUES (?)", (json.dumps({"status": "OFFERED", "agreed": []}),))
            db.execute("CREATE TABLE bundles(principal TEXT PRIMARY KEY, body TEXT NOT NULL)")
            db.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        return cls(home)

    def state(self):
        with transaction(self.path) as db:
            return json.loads(db.execute("SELECT body FROM state").fetchone()[0])

    def challenge(self, role, kind, payload):
        return self.gate.issue_challenge(principal_id=self.terms[role]["principal"],
                                        subject_id=role, action=action(kind, payload))

    def _authorize(self, db, role, kind, payload, bundle, presentation):
        principal = self.terms[role]["principal"]
        if bundle.get("principal", {}).get("principal_id") != principal:
            raise ValueError("PARTY_MISMATCH")
        # Re-admit persisted heads before incoming authority, including after restart.
        previous = db.execute("SELECT body FROM bundles WHERE principal=?", (principal,)).fetchone()
        if previous:
            old = json.loads(previous[0])
            # Rehydrate monotonic history without treating its old issue time as current.
            from openline_wallet.clock import parse_time
            self.gate.admit_bundle(old, now=parse_time(old["issued_at"], "persisted time"))
        self.gate.admit_bundle(bundle)
        db.execute("INSERT OR REPLACE INTO bundles VALUES (?, ?)", (principal, json.dumps(bundle)))
        receipt = self.gate.evaluate(presentation, expected_action=action(kind, payload))
        if receipt["decision"] != "ALLOWED":
            # Preserve observed revocation even when the requested action is denied.
            db.commit()
            raise ValueError("AUTHORITY_STOPPED:" + str(receipt["reason_codes"]))
        return receipt

    def _save(self, db, state, event):
        previous = db.execute("SELECT body FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        row = {"job": self.job, "previous": digest(json.loads(previous[0])) if previous else None, **event}
        db.execute("INSERT INTO events(body) VALUES (?)", (json.dumps(row, sort_keys=True),))
        db.execute("UPDATE state SET body=?", (json.dumps(state, sort_keys=True),))

    def receive(self, role, kind, payload, bundle, presentation):
        import time
        with transaction(self.path) as db:
            s = json.loads(db.execute("SELECT body FROM state").fetchone()[0])
            if time.time() > self.terms["deadline"]:
                raise ValueError("DEADLINE_PASSED")
            if payload.get("job") != self.job:
                raise ValueError("AGREEMENT_MISMATCH")
            if kind == "agree":
                if s["status"] != "OFFERED" or role in s["agreed"] or payload != {"job": self.job}:
                    raise ValueError("AGREEMENT_STATE")
            elif kind == "submit":
                if role != "worker" or s["status"] != "AGREED" or set(payload) != {"job", "candidate"}:
                    raise ValueError("SUBMISSION_STATE")
                import re
                if not re.fullmatch(r"[0-9a-f]{40}", payload["candidate"]):
                    raise ValueError("CANDIDATE_INVALID")
            elif kind == "accept":
                expected = {"job": self.job, "candidate": s.get("candidate"), "evaluation": s.get("evaluation")}
                if role != "buyer" or s["status"] != "ELIGIBLE" or payload != expected:
                    raise ValueError("ACCEPTANCE_STATE_OR_BINDING")
            else:
                raise ValueError("UNKNOWN_ACTION")
            receipt = self._authorize(db, role, kind, payload, bundle, presentation)
            if kind == "agree":
                s["agreed"].append(role)
                if len(s["agreed"]) == 2:
                    s["status"] = "AGREED"
            elif kind == "submit":
                s.update(status="SUBMITTED", candidate=payload["candidate"])
            else:
                s["status"] = "ACCEPTED"
            self._save(db, s, {"kind": kind, "payload": payload, "bundle": bundle,
                               "presentation": presentation, "gate_receipt": receipt})
        return s

    def evaluate(self, repo):
        """Receiver-owned local evaluation. Never accepts a worker's verdict."""
        from airlock.sieve import protected_files_check, run_checks
        from airlock.sandbox import WorktreeSandbox
        with transaction(self.path) as db:
            s = json.loads(db.execute("SELECT body FROM state").fetchone()[0])
            if s["status"] != "SUBMITTED":
                raise ValueError("EVALUATION_STATE")
            if str(Path(repo).resolve()) != self.terms["repository"]:
                raise ValueError("REPOSITORY_MISMATCH")
            base, candidate = self.terms["base"], s["candidate"]
            git(repo, "merge-base", "--is-ancestor", base, candidate)
            paths = git(repo, "diff", "--name-only", base, candidate).splitlines()
            protected = protected_files_check(paths, self.terms["protected"])
            checks = {"status": "NOT_RUN"}
            if protected["status"] == "PASS" and paths:
                with WorktreeSandbox(Path(repo), candidate, prefix="coordinator-eval-") as wt:
                    checks = run_checks(wt, self.terms["checks"], timeout=30, kind="target")
            evidence = {"job": self.job, "base": base, "candidate": candidate,
                        "checks": integer_evidence(checks), "protected": protected}
            ok = bool(paths) and protected["status"] == checks["status"] == "PASS"
            s.update(status="ELIGIBLE" if ok else "REJECTED", evaluation=digest(evidence))
            self._save(db, s, {"kind": "evaluation", "evidence": evidence})
        return s

    def settle(self, provider, *, crash_after_transfer=False):
        with transaction(self.path) as db:
            s = json.loads(db.execute("SELECT body FROM state").fetchone()[0])
            if s["status"] == "SETTLED":
                return s
            if s["status"] not in ("ACCEPTED", "PENDING"):
                raise ValueError("BUYER_ACCEPTANCE_REQUIRED")
            request = {"id": self.job, "buyer": self.terms["buyer"]["principal"],
                       "worker": self.terms["worker"]["principal"], "amount": self.terms["amount"],
                       "currency": "SIM_USD"}
            s["status"] = "PENDING"
            self._save(db, s, {"kind": "payment_intent", "request": request})
        # The durable intent commits BEFORE crossing the external boundary.
        try:
            receipt = provider("lookup", request)
            if receipt is None:
                receipt = provider("transfer", request)
            if crash_after_transfer:
                os._exit(73)  # Real process death after external commit, before local acknowledgment.
            if not verify_record(receipt, expected_public_key=self.terms["provider_key"])[0]:
                raise ValueError("PROVIDER_SIGNATURE")
            if receipt.get("request") != request or receipt.get("status") != "SETTLED":
                raise ValueError("PROVIDER_BINDING")
        except (TimeoutError, OSError, ValueError, subprocess.TimeoutExpired):
            return self.state()  # Unknown stays unknown; no alternative payment key.
        with transaction(self.path) as db:
            s = json.loads(db.execute("SELECT body FROM state").fetchone()[0])
            if s["status"] != "SETTLED":
                s.update(status="SETTLED", payment=receipt)
                self._save(db, s, {"kind": "settlement", "receipt": receipt})
        return s


def bank(home, operation, request):
    """Separate durable simulator. Atomic transfer + stable-key deduplication."""
    home = Path(home)
    with transaction(home / "bank.sqlite") as db:
        row = db.execute("SELECT body FROM transfers WHERE id=?", (request["id"],)).fetchone()
        if row:
            receipt = json.loads(row[0])
            if receipt["request"] != request:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return receipt
        if operation == "lookup":
            return None
        if operation != "transfer" or type(request["amount"]) is not int or request["amount"] <= 0 or request["currency"] != "SIM_USD" or request["buyer"] == request["worker"]:
            raise ValueError("INVALID_TRANSFER")
        amount = request["amount"]
        if db.execute("UPDATE balances SET amount=amount-? WHERE principal=? AND amount>=?",
                      (amount, request["buyer"], amount)).rowcount != 1:
            raise ValueError("INSUFFICIENT_FUNDS")
        if db.execute("UPDATE balances SET amount=amount+? WHERE principal=?", (amount, request["worker"])).rowcount != 1:
            raise ValueError("UNKNOWN_PAYEE")
        receipt = sign_record({"schema": "coordinator.simulated-payment.v1", "request": request,
                               "status": "SETTLED"}, load_private_key(home / "provider.key"))
        db.execute("INSERT INTO transfers VALUES (?, ?)", (request["id"], json.dumps(receipt)))
        return receipt


def provider_process(home):
    def call(operation, request):
        result = subprocess.run([sys.executable, __file__, "bank", str(home)],
                                input=json.dumps({"operation": operation, "request": request}),
                                text=True, capture_output=True, timeout=10)
        if result.returncode:
            raise ValueError("PROVIDER_FAILURE")
        return json.loads(result.stdout)
    return call


if __name__ == "__main__":
    if sys.argv[1] == "bank":
        p = json.load(sys.stdin)
        print(json.dumps(bank(sys.argv[2], p["operation"], p["request"])))
    elif sys.argv[1] == "crash":
        Coordinator(sys.argv[2]).settle(provider_process(sys.argv[3]), crash_after_transfer=True)
