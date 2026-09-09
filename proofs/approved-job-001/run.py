"""APPROVED-JOB-001 — owner-approved work survives worker replacement.

Controlled local proof only. Wallet owns authority, Airlock owns evaluation, and
this file adds one durable job/handoff record. It is not a new agent framework.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import platform
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from openline_wallet.canonical import canonical_json
from openline_wallet.clock import parse_time
from openline_wallet.crypto import (
    load_private_key,
    public_key_hex,
    record_hash,
    save_private_key,
)
from openline_wallet.receiver import ReferenceGate, create_presentation
from openline_wallet.wallet import Wallet

AIRLOCK_SHA = "fb02207f3ac561368beeabf9ff168076bf828824"
WALLET_BASE = "4b813065f65ea41644bfbe917744151f13261474"
VERDICT = "APPROVED_JOB_WORKER_REPLACEMENT_ENFORCED"
HERE = Path(__file__).resolve().parent


def integer_evidence(value):
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
    return f"approved-job:{kind}:{digest(payload)}"


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


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


class ApprovedJob:
    def __init__(self, home):
        self.home = Path(home)
        self.path = self.home / "approved-job.sqlite"
        self.gate = ReferenceGate("approved-job-001")
        with transaction(self.path) as db:
            row = db.execute("SELECT body FROM agreement").fetchone()
            if not row:
                raise ValueError("AGREEMENT_MISSING")
            self.terms = json.loads(row[0])
            self.job = digest(self.terms)
            owner = self.terms["owner"]
            self.gate.pin_principal(owner["principal"], owner["root"])

    @classmethod
    def create(cls, home, terms):
        home = Path(home)
        home.mkdir(parents=True, exist_ok=True)
        required = {
            "schema", "owner", "repository", "base", "task", "requirement",
            "ordinary_checks", "acceptance_checks", "protected",
        }
        if set(terms) != required or terms["schema"] != "approved-job.agreement.v1":
            raise ValueError("AGREEMENT_SHAPE_OR_SCHEMA")
        if not terms["ordinary_checks"] or not terms["acceptance_checks"] or not terms["protected"]:
            raise ValueError("APPROVED_CHECKS_REQUIRED")
        if not terms["task"].strip() or not terms["requirement"].strip():
            raise ValueError("TASK_AND_REQUIREMENT_REQUIRED")
        with transaction(home / "approved-job.sqlite") as db:
            db.execute("CREATE TABLE agreement(body TEXT NOT NULL)")
            db.execute("INSERT INTO agreement VALUES (?)", (canonical_json(terms).decode(),))
            db.execute("CREATE TABLE state(body TEXT NOT NULL)")
            db.execute("INSERT INTO state VALUES (?)", (json.dumps({"status": "DRAFT", "proposals": []}),))
            db.execute("CREATE TABLE bundle(body TEXT NOT NULL)")
            db.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY, body TEXT NOT NULL)")
        return cls(home)

    def state(self):
        with transaction(self.path) as db:
            return json.loads(db.execute("SELECT body FROM state").fetchone()[0])

    def events(self):
        with transaction(self.path) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT body FROM events ORDER BY seq")]

    def challenge(self, subject, kind, payload):
        return self.gate.issue_challenge(
            principal_id=self.terms["owner"]["principal"],
            subject_id=subject,
            action=action(kind, payload),
        )

    def _authorize(self, db, subject, kind, payload, bundle, presentation):
        principal = self.terms["owner"]["principal"]
        if bundle.get("principal", {}).get("principal_id") != principal:
            raise ValueError("OWNER_PRINCIPAL_MISMATCH")
        previous = db.execute("SELECT body FROM bundle LIMIT 1").fetchone()
        if previous:
            old = json.loads(previous[0])
            self.gate.admit_bundle(old, now=parse_time(old["issued_at"], "persisted time"))
        self.gate.admit_bundle(bundle)
        if previous:
            db.execute("UPDATE bundle SET body=?", (json.dumps(bundle),))
        else:
            db.execute("INSERT INTO bundle VALUES (?)", (json.dumps(bundle),))
        receipt = self.gate.evaluate(presentation, expected_action=action(kind, payload))
        if receipt["decision"] != "ALLOWED":
            db.commit()  # retain the observed newer/revoked bundle head
            raise ValueError("AUTHORITY_STOPPED:" + str(receipt["reason_codes"]))
        return receipt

    def _save(self, db, state, event):
        previous = db.execute("SELECT body FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        row = {
            "job": self.job,
            "previous": digest(json.loads(previous[0])) if previous else None,
            **integer_evidence(event),
        }
        db.execute("INSERT INTO events(body) VALUES (?)", (json.dumps(row, sort_keys=True),))
        db.execute("UPDATE state SET body=?", (json.dumps(state, sort_keys=True),))

    def receive(self, subject, kind, payload, bundle, presentation):
        with transaction(self.path) as db:
            state = json.loads(db.execute("SELECT body FROM state").fetchone()[0])
            if payload.get("job") != self.job:
                raise ValueError("AGREEMENT_MISMATCH")
            if kind == "approve":
                if subject != "owner" or state["status"] != "DRAFT" or payload != {"job": self.job}:
                    raise ValueError("APPROVAL_STATE_OR_BINDING")
            elif kind == "handoff":
                if state["status"] != "APPROVED":
                    raise ValueError("HANDOFF_STATE")
                if set(payload) != {"job", "candidate", "note", "unresolved"}:
                    raise ValueError("HANDOFF_SHAPE")
                if not re.fullmatch(r"[0-9a-f]{40}", str(payload["candidate"])):
                    raise ValueError("HANDOFF_CANDIDATE_INVALID")
                if not isinstance(payload["note"], str) or not isinstance(payload["unresolved"], list):
                    raise ValueError("HANDOFF_CONTENT_INVALID")
            elif kind == "propose":
                if state["status"] != "HANDED_OFF":
                    raise ValueError("PROPOSAL_STATE")
                if set(payload) != {"job", "handoff", "proposed_changes"} or payload["handoff"] != state["handoff"]:
                    raise ValueError("PROPOSAL_BINDING")
            elif kind == "submit":
                if state["status"] != "HANDED_OFF":
                    raise ValueError("SUBMISSION_STATE")
                if set(payload) != {"job", "handoff", "candidate"} or payload["handoff"] != state["handoff"]:
                    raise ValueError("SUBMISSION_BINDING")
                if not re.fullmatch(r"[0-9a-f]{40}", str(payload["candidate"])):
                    raise ValueError("CANDIDATE_INVALID")
            else:
                raise ValueError("UNKNOWN_ACTION")

            receipt = self._authorize(db, subject, kind, payload, bundle, presentation)
            if kind == "approve":
                state.update(status="APPROVED", approved=self.job)
            elif kind == "handoff":
                state.update(
                    status="HANDED_OFF",
                    handoff=digest(payload),
                    handoff_candidate=payload["candidate"],
                    handoff_worker=subject,
                    handoff_note=payload["note"],
                    handoff_unresolved=payload["unresolved"],
                )
            elif kind == "propose":
                state["proposals"].append({
                    "worker": subject,
                    "proposal": payload["proposed_changes"],
                    "proposal_digest": digest(payload["proposed_changes"]),
                })
            elif kind == "submit":
                # Check authority first: a revoked previous worker stops at Wallet.
                if subject == state["handoff_worker"]:
                    raise ValueError("WORKER_REPLACEMENT_REQUIRED")
                state.update(status="SUBMITTED", candidate=payload["candidate"], submitted_worker=subject)
            self._save(db, state, {
                "kind": kind, "subject": subject, "payload": payload,
                "bundle": bundle, "presentation": presentation, "gate_receipt": receipt,
            })
        return state

    def evaluate(self, repo):
        from airlock.sandbox import WorktreeSandbox
        from airlock.sieve import protected_files_check, run_checks

        repo = Path(repo)
        if str(repo.resolve()) != self.terms["repository"]:
            raise ValueError("REPOSITORY_MISMATCH")
        with transaction(self.path) as db:
            state = json.loads(db.execute("SELECT body FROM state").fetchone()[0])
            if state["status"] != "SUBMITTED":
                raise ValueError("EVALUATION_STATE")
            base, handoff, candidate = self.terms["base"], state["handoff_candidate"], state["candidate"]
            git(repo, "merge-base", "--is-ancestor", base, handoff)
            git(repo, "merge-base", "--is-ancestor", handoff, candidate)
            paths = git(repo, "diff", "--name-only", base, candidate).splitlines()
            protected = protected_files_check(paths, self.terms["protected"])
            ordinary = {"status": "NOT_RUN"}
            acceptance = {"status": "NOT_RUN"}
            if protected["status"] == "PASS" and paths:
                with WorktreeSandbox(repo, candidate, prefix="approved-job-eval-") as worktree:
                    ordinary = run_checks(worktree, self.terms["ordinary_checks"], timeout=30, kind="ordinary")
                    if ordinary["status"] == "PASS":
                        acceptance = run_checks(
                            worktree, self.terms["acceptance_checks"], timeout=30, kind="approved_requirement"
                        )
            evidence = integer_evidence({
                "job": self.job, "approved": state["approved"], "handoff": state["handoff"],
                "handoff_candidate": handoff, "candidate": candidate, "ordinary": ordinary,
                "acceptance": acceptance, "protected": protected,
                "proposal_count": len(state["proposals"]),
            })
            ok = (
                bool(paths) and protected["status"] == "PASS" and ordinary["status"] == "PASS"
                and acceptance["status"] == "PASS"
            )
            state.update(
                status="ELIGIBLE" if ok else "REJECTED", evaluation=digest(evidence),
                ordinary_status=ordinary["status"], acceptance_status=acceptance["status"],
                protected_status=protected["status"],
            )
            self._save(db, state, {"kind": "evaluation", "evidence": evidence})
        return state


def actor(home, subject, kind, payload, challenge, bundle):
    presentation = create_presentation(
        bundle=bundle,
        mandate_id=digest(payload)[:24] + kind,
        subject_id=subject,
        subject_key=load_private_key(Path(home) / "agent.key"),
        action=action(kind, payload),
        receiver_challenge=challenge,
    )
    return {"bundle": bundle, "presentation": presentation}


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        self.repo = self.root / "maintenance"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Approved Job fixture")
        git(self.repo, "config", "user.email", "fixture@example.invalid")
        (self.repo / "calculator.py").write_text("def total(items):\n    return 0\n")
        (self.repo / "test_basic.py").write_text(
            "from calculator import total\nassert total([2, 3]) == 5\nassert total([]) == 0\n"
        )
        (self.repo / "approved_check.py").write_text(
            "from calculator import total\nitems = [3, 1, 2]\nbefore = list(items)\n"
            "assert total(items) == 6\nassert items == before, 'caller input mutated'\n"
        )
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "Frozen task and approved requirement")
        self.base = git(self.repo, "rev-parse", "HEAD")

        self.owner = Wallet.create(self.root / "owner-wallet")
        self.keys = {}
        for subject in ("owner", "worker-a", "worker-b"):
            key = Ed25519PrivateKey.generate()
            save_private_key(self.root / "credentials" / subject / "agent.key", key)
            self.keys[subject] = key

        self.terms = {
            "schema": "approved-job.agreement.v1",
            "owner": {"principal": self.owner.principal_id, "root": self.owner.root_public_key},
            "repository": str(self.repo.resolve()), "base": self.base,
            "task": "Fix total(items) so it returns the sum",
            "requirement": "Return the correct sum without mutating the caller-provided list",
            "ordinary_checks": [[sys.executable, "test_basic.py"]],
            "acceptance_checks": [[sys.executable, "approved_check.py"]],
            "protected": ["approved_check.py"],
        }
        self.job = ApprovedJob.create(self.root / "job", self.terms)
        self.used = set()

    def request(self, subject, kind, payload):
        if subject in self.used:
            self.owner.revoke(subject)
        self.used.add(subject)
        self.owner.grant(
            subject_id=subject, subject_public_key=public_key_hex(self.keys[subject]),
            scopes=[action(kind, payload)], expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            mandate_id=digest(payload)[:24] + kind,
        )
        challenge = self.job.challenge(subject, kind, payload)
        result = subprocess.run(
            [sys.executable, str(HERE / "run.py"), "actor", str(self.root / "credentials" / subject), subject, kind],
            input=json.dumps({"payload": payload, "challenge": challenge, "bundle": self.owner.export_bundle()}),
            text=True, capture_output=True, check=True, timeout=10,
        )
        return json.loads(result.stdout)

    def send(self, subject, kind, payload):
        req = self.request(subject, kind, payload)
        return self.job.receive(subject, kind, payload, **req)

    def approve(self):
        return self.send("owner", "approve", {"job": self.job.job})

    def make_partial(self):
        (self.repo / "calculator.py").write_text(
            "def total(items):\n    items.sort()\n    return sum(items)\n"
        )
        git(self.repo, "add", "calculator.py")
        git(self.repo, "commit", "-qm", "Worker A partial fix")
        return git(self.repo, "rev-parse", "HEAD")

    def handoff(self):
        partial = self.make_partial()
        return self.send("worker-a", "handoff", {
            "job": self.job.job, "candidate": partial,
            "note": "Ordinary behavior fixed; caller-input preservation remains unresolved.",
            "unresolved": ["caller input must remain order-equivalent"],
        })

    def revoke_worker_a(self):
        self.owner.revoke("worker-a")

    def continuation(self, good=True, protected=False):
        partial = self.job.state()["handoff_candidate"]
        git(self.repo, "checkout", "-q", "--detach", partial)
        if good:
            text = "def total(items):\n    return sum(items)\n"
        else:
            text = "def total(items):\n    # Worker B kept the in-place sort.\n    items.sort()\n    return sum(items)\n"
        (self.repo / "calculator.py").write_text(text)
        if protected:
            (self.repo / "approved_check.py").write_text("pass\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "Worker B continuation")
        return git(self.repo, "rev-parse", "HEAD")

    def propose_remove_requirement(self):
        state = self.job.state()
        return self.send("worker-b", "propose", {
            "job": self.job.job, "handoff": state["handoff"],
            "proposed_changes": {"acceptance_checks": [], "requirement": "Only return the numeric sum"},
        })

    def submit(self, candidate, subject="worker-b"):
        state = self.job.state()
        self.send(subject, "submit", {"job": self.job.job, "handoff": state["handoff"], "candidate": candidate})
        return self.job.evaluate(self.repo)


class ApprovedJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.f = Fixture(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_correct_replacement_continuation_becomes_eligible(self):
        self.f.approve(); self.f.handoff(); self.f.revoke_worker_a()
        result = self.f.submit(self.f.continuation(good=True))
        self.assertEqual((result["status"], result["ordinary_status"], result["acceptance_status"]),
                         ("ELIGIBLE", "PASS", "PASS"))
        self.assertEqual((result["handoff_worker"], result["submitted_worker"]), ("worker-a", "worker-b"))
        self.assertEqual(result["approved"], self.f.job.job)

    def test_bad_replacement_passes_ordinary_but_fails_approved_requirement(self):
        self.f.approve(); self.f.handoff(); self.f.revoke_worker_a()
        result = self.f.submit(self.f.continuation(good=False))
        self.assertEqual((result["status"], result["ordinary_status"], result["acceptance_status"]),
                         ("REJECTED", "PASS", "FAIL"))

    def test_agent_proposal_cannot_amend_approved_agreement(self):
        self.f.approve(); original = self.f.job.terms.copy(); self.f.handoff(); self.f.revoke_worker_a()
        state = self.f.propose_remove_requirement()
        self.assertEqual(self.f.job.terms, original)
        self.assertEqual(state["status"], "HANDED_OFF")
        result = self.f.submit(self.f.continuation(good=False))
        self.assertEqual((result["status"], result["acceptance_status"]), ("REJECTED", "FAIL"))

    def test_handoff_cannot_smuggle_agreement_change(self):
        self.f.approve(); partial = self.f.make_partial()
        payload = {"job": self.f.job.job, "candidate": partial, "note": "change rules", "unresolved": [],
                   "acceptance_checks": []}
        req = self.f.request("worker-a", "handoff", payload)
        with self.assertRaisesRegex(ValueError, "HANDOFF_SHAPE"):
            self.f.job.receive("worker-a", "handoff", payload, **req)

    def test_reinitialize_cannot_replace_approved_agreement(self):
        self.f.approve()
        with self.assertRaises(Exception):
            ApprovedJob.create(self.f.job.home, {**self.f.terms, "requirement": "weaker"})
        self.assertEqual(ApprovedJob(self.f.job.home).terms, self.f.terms)

    def test_changed_job_digest_rejected(self):
        self.f.approve(); partial = self.f.make_partial()
        payload = {"job": "0" * 64, "candidate": partial, "note": "x", "unresolved": []}
        req = self.f.request("worker-a", "handoff", payload)
        with self.assertRaisesRegex(ValueError, "AGREEMENT_MISMATCH"):
            self.f.job.receive("worker-a", "handoff", payload, **req)

    def test_replacement_must_bind_exact_handoff(self):
        self.f.approve(); self.f.handoff(); self.f.revoke_worker_a(); candidate = self.f.continuation(good=True)
        payload = {"job": self.f.job.job, "handoff": "0" * 64, "candidate": candidate}
        req = self.f.request("worker-b", "submit", payload)
        with self.assertRaisesRegex(ValueError, "SUBMISSION_BINDING"):
            self.f.job.receive("worker-b", "submit", payload, **req)

    def test_revoked_worker_a_cannot_resume_same_job(self):
        self.f.approve(); self.f.handoff(); candidate = self.f.continuation(good=False); state = self.f.job.state()
        payload = {"job": self.f.job.job, "handoff": state["handoff"], "candidate": candidate}
        req = self.f.request("worker-a", "submit", payload)
        self.f.owner.revoke("worker-a"); req["bundle"] = self.f.owner.export_bundle()
        with self.assertRaisesRegex(ValueError, "AUTHORITY_STOPPED"):
            self.f.job.receive("worker-a", "submit", payload, **req)
        self.assertEqual(self.f.job.state()["status"], "HANDED_OFF")

    def test_same_worker_cannot_count_as_replacement(self):
        self.f.approve(); self.f.handoff(); candidate = self.f.continuation(good=True); state = self.f.job.state()
        payload = {"job": self.f.job.job, "handoff": state["handoff"], "candidate": candidate}
        req = self.f.request("worker-a", "submit", payload)
        with self.assertRaisesRegex(ValueError, "WORKER_REPLACEMENT_REQUIRED"):
            self.f.job.receive("worker-a", "submit", payload, **req)

    def test_protected_approved_check_change_rejected_before_execution(self):
        self.f.approve(); self.f.handoff(); self.f.revoke_worker_a()
        result = self.f.submit(self.f.continuation(good=True, protected=True))
        self.assertEqual((result["status"], result["protected_status"], result["ordinary_status"],
                          result["acceptance_status"]), ("REJECTED", "FAIL", "NOT_RUN", "NOT_RUN"))

    def test_event_chain_remains_bound_to_same_job(self):
        self.f.approve(); self.f.handoff(); self.f.revoke_worker_a(); self.f.submit(self.f.continuation(good=True))
        previous = None
        for event in self.f.job.events():
            self.assertEqual(event["job"], self.f.job.job)
            self.assertEqual(event["previous"], previous)
            previous = digest(event)


def run_arm(good, proposal=False):
    with tempfile.TemporaryDirectory(prefix="approved-job-arm-") as root:
        f = Fixture(root); f.approve(); before = digest(f.job.terms); f.handoff(); f.revoke_worker_a()
        if proposal:
            f.propose_remove_requirement()
        final = f.submit(f.continuation(good=good)); after = digest(f.job.terms)
        return {
            "agreement_digest_before_handoff": before,
            "agreement_digest_after_replacement": after,
            "agreement": f.terms, "events": f.job.events(), "final": final,
            "handoff_scope": {
                "candidate_commit": final["handoff_candidate"], "note": final["handoff_note"],
                "unresolved": final["handoff_unresolved"], "full_chat_or_model_memory_transferred": False,
            },
        }


def verify(output):
    output = Path(output)
    report = json.loads((output / "result.json").read_text())
    evidence = json.loads((output / "evidence.json").read_text())
    assert report["schema"] == "approved-job-001.result.v1" and report["verdict"] == VERDICT
    assert report["tests_passed"] == len(report["tests"]) and report["tests_passed"] >= 11
    assert report["payment_or_marketplace"] is False and report["full_context_portability"] is False
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == report["source_sha256"]
    for name, expected in report["files_sha256"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == expected
    good, bad = evidence["good_replacement"], evidence["bad_replacement_with_agent_proposal"]
    for arm in (good, bad):
        assert arm["agreement_digest_before_handoff"] == arm["agreement_digest_after_replacement"] == digest(arm["agreement"])
        assert arm["handoff_scope"]["full_chat_or_model_memory_transferred"] is False
        assert (arm["final"]["handoff_worker"], arm["final"]["submitted_worker"]) == ("worker-a", "worker-b")
        previous = None
        for event in arm["events"]:
            assert event["job"] == arm["agreement_digest_before_handoff"] and event["previous"] == previous
            previous = digest(event)
    assert (good["final"]["status"], good["final"]["ordinary_status"], good["final"]["acceptance_status"]) == ("ELIGIBLE", "PASS", "PASS")
    assert (bad["final"]["status"], bad["final"]["ordinary_status"], bad["final"]["acceptance_status"]) == ("REJECTED", "PASS", "FAIL")
    assert len(bad["final"]["proposals"]) == 1 and bad["agreement"]["acceptance_checks"]
    print(json.dumps({"verdict": VERDICT, "verified": True}))


def reproduce(output):
    output = Path(output); output.mkdir(parents=True, exist_ok=False)
    import airlock
    airlock_repo = Path(airlock.__file__).resolve().parents[2]
    sha = git(airlock_repo, "rev-parse", "HEAD")
    if sha != AIRLOCK_SHA or git(airlock_repo, "status", "--porcelain"):
        raise SystemExit("AIRLOCK_SOURCE_PIN_OR_CLEANLINESS_FAILURE")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ApprovedJobTests)
    names = [test.id().split(".")[-1] for test in suite]
    with (output / "tests.txt").open("w") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped:
        raise SystemExit("APPROVED_JOB_TESTS_FAILED_OR_SKIPPED")
    evidence = {"good_replacement": run_arm(True), "bad_replacement_with_agent_proposal": run_arm(False, True)}
    (output / "evidence.json").write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    report = {
        "schema": "approved-job-001.result.v1", "verdict": VERDICT,
        "python": platform.python_version(), "wallet_base": WALLET_BASE, "airlock_commit": sha,
        "predecessor": "COORDINATOR-001", "tests_passed": result.testsRun, "tests": names,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "files_sha256": {
            "tests.txt": hashlib.sha256((output / "tests.txt").read_bytes()).hexdigest(),
            "evidence.json": hashlib.sha256((output / "evidence.json").read_bytes()).hexdigest(),
        },
        "payment_or_marketplace": False, "live_models": False, "full_context_portability": False,
        "claim_boundary": (
            "Controlled local signer processes and fixture repository. Proves one owner-approved agreement "
            "remains the acceptance reference across a bounded worker replacement; does not prove complete "
            "human-intent capture, full session or memory portability, hostile OS isolation, external operators, "
            "or payment settlement."
        ),
    }
    (output / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    verify(output)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "actor":
        p = json.load(sys.stdin)
        print(json.dumps(actor(sys.argv[2], sys.argv[3], sys.argv[4], p["payload"], p["challenge"], p["bundle"])))
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--output", type=Path)
        parser.add_argument("--verify", type=Path)
        args = parser.parse_args()
        if bool(args.output) == bool(args.verify):
            parser.error("choose exactly one of --output or --verify")
        verify(args.verify) if args.verify else reproduce(args.output)
