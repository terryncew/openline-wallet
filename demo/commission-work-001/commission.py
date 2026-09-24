#!/usr/bin/env python3
"""commission — thin local preview for commissioning agent work.

One complete experience, reusing the existing OpenLine machinery:

  owner (human-owned buyer account)
    -> delegates a bounded job budget to its agent (wallet mandate + allowance)
  seller (separate human-owned account, separate keys)
    -> offers one existing, legitimate capability as a service
  agent -> commissions bounded work against the seller's offer:
    the agreement is frozen before work (identities, task/input binding,
    deliverable, receiver-owned acceptance criteria, amount, currency,
    deadline, cancellation/settlement rules)
  seller -> performs the work, submits the result
    (the seller keeps its implementation; the buyer receives the result
    and the receipt; no capability is installed or inherited)
  buyer's receiver -> independently checks the result
  accepted work settles exactly one simulated payment (SIM_USD, simulated)
  rejected work does not settle

Reuses the merged machinery for the hard parts:
  - openline_wallet.canonical / crypto   (hashes, signatures, identity)
  - openline_wallet.wallet              (delegation mandates, revocation)
  - capability-installer settlement pattern (exactly-once ledger,
    ALREADY_SETTLED, signed receipts)

This preview adds only the commission-specific pieces:
  - the frozen agreement record (signed, hash-bound)
  - fund reservation against the agent's delegated budget
  - receiver-owned verification of a service result from its input
  - idempotent event-log reconciliation (read-only, no recovery subsystem)

Scope: one service, one operator host, simulated money. See README.md
and SUPPORTED.md for what this does and does not establish.

Authority model (stated plainly): every consequential record is signed by
the principal that may authorize it — the agent signs the agreement, the
buyer side signs the verdict, the seller signs the result, the owner signs
the receipt — and each signature is verified on every read, so the signed
body is the sole source of truth. The owner's wallet mandate genuinely
gates new agent commissions (no active mandate: no new work). But on this
single host the operator holds every private key: nothing here separates
key custody between agent and owner, and ``--caller`` is a label the
operator chooses. This preview is a trusted-operator simulation of the
authority boundary: it demonstrates and tests the rules (refusals,
mandates, signature attribution, exactly-once settlement), not custodial
separation between the agent and the owner.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from openline_wallet.canonical import canonical_json
from openline_wallet.crypto import (
    Ed25519PrivateKey,
    load_private_key,
    principal_id,
    public_key_hex,
    save_private_key,
    sha256_hex,
    sign_record,
    verify_record,
)
from openline_wallet.storage import atomic_write_json
from openline_wallet.wallet import Wallet, WalletError

HOME_ENV = "COMMISSION_HOME"
CURRENCY = "SIM_USD"
OWNER_START_BALANCE = 10000
SERVICE = "text_digest"
OFFER_ID = "offer-text-digest-v1"
DELIVERABLE = (
    "a digest report for the job input: input sha256, word count, "
    "line count, and the input nonce, returned as JSON"
)
ACCEPTANCE = {
    "type": "exact_recompute",
    "fields": ["input_sha256", "word_count", "line_count", "nonce"],
    "rule": (
        "the buyer's receiver recomputes sha256, word count, and line "
        "count from the frozen job input and requires every field to "
        "match the seller's submitted result exactly"
    ),
}
DEADLINE_HOURS = 2


class CommissionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# -- small helpers ------------------------------------------------------------


def _home(h: Path | None) -> Path:
    return h or Path(os.environ.get(HOME_ENV, ".commission-home"))


def _read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def _write_json(path: Path, obj) -> None:
    # All durable state uses the wallet's own atomic store (temp + rename +
    # fsync), the same primitive the wallet itself writes through.
    atomic_write_json(path, obj)


@contextmanager
def _state_lock(h: Path):
    """Serialize writers of the shared home state (allowances, ledger, jobs).

    Same convention as openline_wallet.effect_closure's writer lock: one
    live writer for this local ledger, across processes and threads.
    Reservations and settlements hold this lock for their whole
    read-modify-write, so concurrent commissions cannot over-commit.
    """
    lock_path = h / "state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _now():
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _now().isoformat()


def _load_key(h: Path, name: str):
    return load_private_key(h / "keys" / ("%s.key" % name))


def _pub(h: Path, name: str) -> str:
    return json.loads((h / "keys" / ("%s.pub.json" % name)).read_text())["public_key"]


def _principal(h: Path, name: str) -> str:
    return json.loads((h / "keys" / ("%s.pub.json" % name)).read_text())["principal"]


def _agreement_hash(agreement: dict) -> str:
    return sha256_hex(canonical_json(agreement))


def _require_caller(args, allowed: set[str]) -> None:
    if args.caller not in allowed:
        raise CommissionError(
            "CALLER_NOT_AUTHORIZED",
            "this step is for %s, not for '%s'"
            % (" or ".join(sorted(allowed)), args.caller),
        )


def _agent_mandate_active(h: Path) -> dict:
    """The buyer's agent may act only under an active owner-issued mandate."""
    agent = _principal(h, "agent")
    wallet = Wallet.open(h / "wallet")
    now = _now()
    for m in wallet.timeline().current(now):
        if m.get("subject_id") == agent and "commission:work" in m.get("scopes", []):
            return m
    raise CommissionError(
        "MANDATE_REVOKED",
        "the agent has no active commission mandate from the owner; "
        "new work is blocked",
    )


def _verify_signed(body: dict, signer_pub: str, label: str) -> None:
    ok, reason = verify_record(body["signature"], expected_public_key=signer_pub)
    if not ok:
        raise CommissionError("SIGNATURE_INVALID", "%s: %s" % (label, reason))
    signed = {k: v for k, v in body["signature"].items()
              if k not in ("signature", "payload_hash")}
    if canonical_json(signed) != canonical_json(body["record"]):
        raise CommissionError("RECORD_TAMPERED", "%s outer record does not match the signed body" % label)


def _allowances(h: Path) -> dict:
    return _read_json(h / "allowances.json", {})


def _pub_by_principal(h: Path, principal: str) -> str:
    for name in ("owner", "agent", "seller"):
        if _principal(h, name) == principal:
            return _pub(h, name)
    raise CommissionError("UNKNOWN_PRINCIPAL", "no local key for principal %s" % principal[:24])


def _authenticated_agreement(h: Path, job: dict) -> dict:
    """The signed body is the sole source of truth.

    Amount and payee come only from the agreement whose signature verifies
    against the agent's key and whose hash matches the frozen hash. Mutable
    copies in jobs.json cannot move money: a substituted payee or amount
    fails authentication and settlement is refused.
    """
    agreement = job.get("agreement")
    sig = job.get("agreement_signature")
    if not isinstance(agreement, dict) or not isinstance(sig, dict):
        raise CommissionError("AGREEMENT_NOT_AUTHENTIC",
                              "no signed agreement on record for this job")
    agent_principal = _principal(h, "agent")
    if agreement.get("agent") != agent_principal:
        raise CommissionError("AGREEMENT_NOT_AUTHENTIC",
                              "the agreement names a different agent")
    try:
        _verify_signed({"record": agreement, "signature": sig},
                       _pub(h, "agent"), "agreement")
    except CommissionError as e:
        raise CommissionError("AGREEMENT_NOT_AUTHENTIC",
                              "the agreement's signature does not verify: %s" % e.code)
    if _agreement_hash(agreement) != job.get("agreement_hash"):
        raise CommissionError("AGREEMENT_NOT_AUTHENTIC",
                              "the agreement does not match the frozen hash")
    return agreement


def _money(amount: int) -> str:
    return "%d SIM_USD (simulated)" % amount


# -- init / delegate / offer --------------------------------------------------


def cmd_init(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    if (h / "wallet").exists():
        raise CommissionError("ALREADY_INITIALIZED", "home exists: %s" % h)
    for d in ("wallet", "keys", "receipts", "seller_runs", "seller_impl"):
        (h / d).mkdir(parents=True)
    # The owner's account. The buyer IS the human owner here; the agent is a
    # narrower identity the owner delegates to.
    wallet = Wallet.create(h / "wallet", label="commission preview owner wallet")
    for name, role in (("owner", "buyer-owner (human)"),
                       ("agent", "buyer-agent (delegated)"),
                       ("seller", "seller (human)")):
        key = Ed25519PrivateKey.generate()
        save_private_key(h / "keys" / ("%s.key" % name), key)
        _write_json(h / "keys" / ("%s.pub.json" % name),
                    {"public_key": public_key_hex(key),
                     "principal": principal_id(public_key_hex(key)),
                     "role": role})
    # Simulated funds only. No real money exists in this preview.
    _write_json(h / "ledger.json", {
        "currency": CURRENCY + " (simulated)",
        "balances": {_principal(h, "owner"): OWNER_START_BALANCE,
                     _principal(h, "seller"): 0},
        "transfers": [],
    })
    _write_json(h / "allowances.json", {})
    _write_json(h / "offers.json", {})
    _write_json(h / "jobs.json", {})
    # The seller's implementation lives in the seller's own directory and
    # is never copied into buyer state: the buyer buys results, not code.
    impl_src = Path(__file__).resolve().parent / "seller_impl" / "text_digest.py"
    (h / "seller_impl" / "text_digest.py").write_bytes(impl_src.read_bytes())
    print("initialized commission home: %s" % h)
    print("owner  (buyer, human): %s" % _principal(h, "owner"))
    print("agent  (delegated):   %s" % _principal(h, "agent"))
    print("seller (human):       %s" % _principal(h, "seller"))
    print("owner balance: %s" % _money(OWNER_START_BALANCE))
    print("next: owner delegates a bounded budget with `delegate`")
    return 0


def cmd_delegate(args) -> int:
    """Only the owner can grant (or raise) the agent's budget."""
    _require_caller(args, {"owner"})
    h = _home(Path(args.home) if args.home else None)
    agent_pub = _pub(h, "agent")
    wallet = Wallet.open(h / "wallet")
    try:
        mandate = wallet.grant(
            subject_id=_principal(h, "agent"),
            subject_public_key=agent_pub,
            scopes=["commission:work"],
            expires_at=_now() + timedelta(hours=args.hours),
        )
    except WalletError as e:
        raise CommissionError("DELEGATE_FAILED", str(e))
    allowances = _allowances(h)
    allowances[_principal(h, "agent")] = {
        "granted": args.budget,
        "reserved": 0,
        "spent": 0,
        "mandate_id": mandate["data"]["mandate_id"],
        "granted_by": "owner",
        "granted_at": _utcnow_iso(),
    }
    _write_json(h / "allowances.json", allowances)
    print("owner delegated a bounded job budget to the agent")
    print("agent allowance: %s (reserved 0, spent 0)" % _money(args.budget))
    print("the agent cannot raise this. only the owner can grant again.")
    return 0
def _offer_body(h: Path, price: int) -> dict:
    return {
        "schema": "commission.offer.v1",
        "offer_id": OFFER_ID,
        "seller": _principal(h, "seller"),
        "service": SERVICE,
        "deliverable": DELIVERABLE,
        "acceptance": ACCEPTANCE,
        "price": price,
        "currency": CURRENCY + " (simulated)",
        "note": "results only; the seller retains its implementation",
    }


def cmd_offer(args) -> int:
    """The seller offers one existing capability as a service."""
    _require_caller(args, {"seller"})
    h = _home(Path(args.home) if args.home else None)
    body = _offer_body(h, args.price)
    sig = sign_record(body, _load_key(h, "seller"))
    offers = _read_json(h / "offers.json", {})
    offers[OFFER_ID] = {"record": body, "signature": sig}
    _write_json(h / "offers.json", offers)
    print("seller offers '%s' as a service" % SERVICE)
    print("offer id: %s" % OFFER_ID)
    print("price: %s" % _money(args.price))
    print("deliverable: %s" % DELIVERABLE)
    print("acceptance (receiver-owned): exact recompute of every field from the job input")
    return 0


# -- commission (the agreement freezes here) ----------------------------------


def _job(h: Path, job_id: str) -> dict:
    jobs = _read_json(h / "jobs.json", {})
    job = jobs.get(job_id)
    if job is None:
        raise CommissionError("JOB_NOT_FOUND", "no such job: %s" % job_id)
    return job


def _save_job(h: Path, job: dict) -> None:
    jobs = _read_json(h / "jobs.json", {})
    jobs[job["job_id"]] = job
    _write_json(h / "jobs.json", jobs)


def _event(job: dict, kind: str, detail: dict | None = None) -> None:
    job["events"].append({"type": kind, "at": _utcnow_iso(), **(detail or {})})


def cmd_commission(args) -> int:
    """The agent commissions bounded work against a seller's offer.

    The agreement freezes here, before any work: identities, task/input
    binding, deliverable, receiver-owned acceptance criteria, amount,
    currency, deadline, cancellation/settlement rules. The agent acts only
    within its delegated mandate; the four narrower-than-owner checks are
    enforced in code.
    """
    _require_caller(args, {"agent"})
    # Narrower-than-owner authority, enforced first, before any state changes.
    if args.raise_budget is not None:
        raise CommissionError(
            "BUDGET_INCREASE_REFUSED",
            "the agent cannot raise its budget; only the owner can grant or "
            "raise an allowance",
        )
    if args.rewrite_acceptance:
        raise CommissionError(
            "ACCEPTANCE_REWRITE_REFUSED",
            "acceptance criteria are receiver-owned and frozen in the offer; "
            "the agent cannot rewrite them",
        )
    if args.payee is not None:
        raise CommissionError(
            "PAYEE_CHANGE_REFUSED",
            "the payee is the offer's seller; the agent cannot change it",
        )
    if args.price_override is not None:
        raise CommissionError(
            "PRICE_OVERRIDE_REFUSED",
            "the price is the offer's price; the agent cannot set it",
        )
    if args.self_authorize:
        raise CommissionError(
            "SELF_AUTHORIZATION_REFUSED",
            "the agent cannot authorize itself; only the owner delegates",
        )
    h = _home(Path(args.home) if args.home else None)
    _agent_mandate_active(h)
    offers = _read_json(h / "offers.json", {})
    offer = offers.get(args.offer)
    if offer is None:
        raise CommissionError("OFFER_NOT_FOUND", "no such offer: %s" % args.offer)
    _verify_signed(offer, _pub(h, "seller"), "offer")
    offer_body = offer["record"]
    amount = offer_body["price"]
    raw = Path(args.input).read_bytes()
    header_end = raw.index(b"\n")
    try:
        nonce = json.loads(raw[:header_end].decode("utf-8"))["nonce"]
    except Exception as e:  # noqa: BLE001
        raise CommissionError("INPUT_MALFORMED", "input must start with a JSON header line carrying a nonce: %s" % e)
    # Everything that touches shared budget state happens inside one writer
    # lock, so concurrent commissions serialize instead of over-committing.
    with _state_lock(h):
        allowances = _allowances(h)
        agent = _principal(h, "agent")
        allow = allowances.get(agent)
        if allow is None:
            raise CommissionError("NO_ALLOWANCE", "the owner has not delegated a budget to this agent")
        # Budget accounting: SPENT plus RESERVED against the granted amount.
        # Settled money is gone; only reserved-but-unsettled money is still
        # committable. A second sequential job after a full settlement is
        # refused, not silently funded twice.
        committed = allow["reserved"] + allow["spent"]
        if committed + amount > allow["granted"]:
            code = "ALREADY_RESERVED" if allow["reserved"] > 0 else "INSUFFICIENT_ALLOWANCE"
            raise CommissionError(
                code,
                "agent allowance: granted %s, reserved %s, already spent %s; %s does not fit"
                % (_money(allow["granted"]), _money(allow["reserved"]),
                   _money(allow["spent"]), _money(amount)),
            )
        jobs = _read_json(h / "jobs.json", {})
        for other in jobs.values():
            if other["agreement"].get("nonce") == nonce:
                raise CommissionError(
                    "NONCE_REUSED",
                    "nonce %r was already commissioned; every job needs a fresh input" % nonce,
                )
        job_id = "job-%s" % sha256_hex(canonical_json(
            {"agent": agent, "offer": args.offer, "nonce": nonce, "at": _utcnow_iso()}
        ))[:12]
        agreement = {
            "schema": "commission.agreement.v1",
            "job_id": job_id,
            "buyer": _principal(h, "owner"),
            "agent": agent,
            "seller": offer_body["seller"],
            "payee": offer_body["seller"],
            "offer_id": args.offer,
            "service": offer_body["service"],
            "input_sha256": sha256_hex(raw),
            "nonce": nonce,
            "deliverable": offer_body["deliverable"],
            "acceptance": offer_body["acceptance"],
            "amount": amount,
            "currency": CURRENCY + " (simulated)",
            "deadline": (_now() + timedelta(hours=DEADLINE_HOURS)).isoformat(),
            "rules": {
                "on_accept": "settle exactly once to the payee after the buyer's receiver verifies the result",
                "on_reject": "no payment; the reserved amount is released back to the agent allowance",
                "on_revoke": "revocation blocks new commissions; an already-earned (verified, accepted) obligation is never silently erased and remains payable",
                "replay": "settlement is idempotent; replaying settle cannot produce a second payment",
            },
        }
        # Per-role key custody inside this process: the agreement is signed
        # with the agent's key only. Every consequential record carries the
        # key of the principal that may authorize it, verifiable offline.
        sig = sign_record(agreement, _load_key(h, "agent"))
        job = {
            "job_id": job_id,
            "agreement": agreement,
            "agreement_hash": _agreement_hash(agreement),
            "agreement_signature": sig,
            "input_path": str(Path(args.input).resolve()),
            "status": "COMMISSIONED",
            "events": [],
            "submission": None,
            "verdict": None,
            "settlement": None,
        }
        _event(job, "AGREEMENT_FROZEN",
               {"amount": amount, "currency": "SIM_USD (simulated)",
                "reserved_from_allowance": allow["granted"]})
        allow["reserved"] += amount
        _write_json(h / "allowances.json", allowances)
        jobs[job_id] = job
        _write_json(h / "jobs.json", jobs)
    print("agreement frozen: %s" % job_id)
    print("buyer %s -> agent %s -> seller %s" % (agreement["buyer"][:16], agent[:16], agreement["seller"][:16]))
    print("service: %s | amount: %s | deadline: %s" % (SERVICE, _money(amount), agreement["deadline"]))
    print("reserved %s from the agent's allowance (granted %s, now reserved %s)"
          % (_money(amount), _money(allow["granted"]), _money(allow["reserved"])))
    print("the seller may now perform the work")
    return 0
# -- work and submit (seller side) ---------------------------------------------


def _clean_env() -> dict:
    env = {"PATH": "/usr/bin:/bin", "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    for leaked in ("COMMISSION_HOME", "CAPINSTALL_HOME", "RRSI_ADAPTER_HOME", "HOME", "XDG_CONFIG_HOME"):
        env.pop(leaked, None)
    return env


def cmd_work(args) -> int:
    """The seller performs the work with its own retained implementation.

    Idempotent per job: if this exact job was already worked, the recorded
    output is returned without re-dispatching the implementation.
    """
    _require_caller(args, {"seller"})
    h = _home(Path(args.home) if args.home else None)
    job = _job(h, args.job)
    if job["agreement"]["seller"] != _principal(h, "seller"):
        raise CommissionError("SELLER_MISMATCH", "this job is owed to a different seller")
    if job["status"] != "COMMISSIONED":
        raise CommissionError("WORK_WRONG_STATE", "job is %s; work is only performed once it is commissioned" % job["status"])
    run_path = h / "seller_runs" / ("%s.json" % job["job_id"])
    if run_path.exists():
        print("work already completed for this job; reusing the recorded output (no duplicate dispatch)")
        return 0
    impl = h / "seller_impl" / "text_digest.py"
    raw = Path(job["input_path"]).read_bytes()
    if args.wrong_input:
        # Demonstration hook: the seller honestly runs its implementation,
        # but on the wrong text. The result is validly signed yet does not
        # match the frozen input — the buyer's receiver catches it.
        raw = raw + b"\nextra words the buyer never asked for\n"
    r = subprocess.run(
        [sys.executable, str(impl)], input=raw, capture_output=True,
        env=_clean_env(), timeout=60,
    )
    if r.returncode != 0:
        raise CommissionError("WORK_FAILED", "seller implementation exited %d: %s" % (r.returncode, r.stderr.decode()[-300:]))
    report = json.loads(r.stdout.decode())
    result = {
        "schema": "commission.result.v1",
        "job_id": job["job_id"],
        "service": SERVICE,
        **report,
    }
    sig = sign_record(result, _load_key(h, "seller"))
    _write_json(run_path, {"record": result, "signature": sig})
    print("seller completed the work for %s" % job["job_id"])
    print("result digest: sha256 %s, words %d, lines %d, nonce %s"
          % (result["input_sha256"][:16], result["word_count"], result["line_count"], result["nonce"]))
    print("the seller's implementation stays with the seller; only this result is submitted")
    return 0


def cmd_submit(args) -> int:
    """The seller submits its signed result against the frozen agreement."""
    _require_caller(args, {"seller"})
    h = _home(Path(args.home) if args.home else None)
    with _state_lock(h):
        job = _job(h, args.job)
        if job["agreement"]["seller"] != _principal(h, "seller"):
            raise CommissionError("SELLER_MISMATCH", "this job is owed to a different seller")
        if job["submission"] is not None:
            raise CommissionError(
                "WORK_ALREADY_SUBMITTED",
                "a result was already submitted for this job; resubmission is refused, "
                "and the buyer verifies the recorded submission",
            )
        run_path = h / "seller_runs" / ("%s.json" % job["job_id"])
        if not run_path.exists():
            raise CommissionError("NO_WORK_DONE", "run `work` before `submit`")
        submitted = _read_json(run_path, None)
        if args.tamper_result:
            # Demonstration hook: alter the result after the seller signed it.
            submitted = {"record": dict(submitted["record"]), "signature": submitted["signature"]}
            submitted["record"]["word_count"] += 100
        _verify_signed(submitted, _pub(h, "seller"), "result")
        job["submission"] = submitted
        job["status"] = "SUBMITTED"
        _event(job, "RESULT_SUBMITTED", {"result_sha256": sha256_hex(canonical_json(submitted["record"]))[:16]})
        _save_job(h, job)
    print("seller submitted the result for %s" % job["job_id"])
    print("the buyer's receiver now checks it against the frozen agreement")
    return 0


# -- verify (buyer's receiver) -------------------------------------------------


def _recompute(raw: bytes) -> dict:
    header_end = raw.index(b"\n")
    nonce = json.loads(raw[:header_end].decode("utf-8"))["nonce"]
    body = raw[header_end + 1:]
    text = body.decode("utf-8")
    return {
        "input_sha256": sha256_hex(raw),
        "word_count": len(text.split()),
        "line_count": len(text.split("\n")),
        "nonce": nonce,
    }


def cmd_verify(args) -> int:
    """The buyer's receiver independently checks the result.

    Only the buyer side verifies; the seller cannot verify its own work.
    Every check is against the frozen agreement, never a live copy.
    """
    _require_caller(args, {"owner", "agent"})
    h = _home(Path(args.home) if args.home else None)
    job = _job(h, args.job)
    if job["status"] != "SUBMITTED":
        raise CommissionError("VERIFY_WRONG_STATE", "job is %s; nothing to verify" % job["status"])
    # The stored agreement must authenticate before anything is checked:
    # signature by the agent's key, hash matching the frozen hash.
    _authenticated_agreement(h, job)
    if args.tamper_agreement == "amount":
        # Demonstration hook: alter the frozen agreement before checking.
        # The tamper applies to a working copy only; the frozen record on
        # disk is never rewritten, and the reservation is released by the
        # frozen amount.
        agreement = dict(job["agreement"])
        agreement["amount"] += 1000
    elif args.tamper_payee:
        agreement = dict(job["agreement"])
        agreement["payee"] = "stranger-principal"
    elif args.tamper_input_hash:
        agreement = dict(job["agreement"])
        agreement["input_sha256"] = "0" * 64
    else:
        agreement = job["agreement"]
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str) -> bool:
        checks.append((name, ok, detail))
        return ok

    # 1. the agreement must be the exact agreement that was frozen.
    check("agreement_integrity",
          _agreement_hash(agreement) == job["agreement_hash"],
          "frozen hash %s" % job["agreement_hash"][:16])
    # 2. the agent that commissioned it still needs no mandate at verify
    #    time (revocation blocks NEW work, not verification of work owed),
    #    but the payee must be the offer's seller.
    check("payee_matches_offer",
          agreement["payee"] == agreement["seller"],
          "payee %s" % agreement["payee"][:16])
    # 3. a result must have been submitted.
    check("result_submitted", job["submission"] is not None, "submission on record")
    ok = all(c[1] for c in checks)
    if ok:
        sub = job["submission"]
        try:
            _verify_signed(sub, _pub(h, "seller"), "result")
            sig_ok, sig_detail = True, "signed by the agreement's seller"
        except CommissionError as e:
            sig_ok, sig_detail = False, "%s: %s" % (e.code, e.message)
        check("seller_signature", sig_ok, sig_detail)
        rec = sub["record"]
        check("result_binds_job",
              rec.get("job_id") == job["job_id"] and rec.get("nonce") == agreement["nonce"]
              and rec.get("input_sha256") == agreement["input_sha256"],
              "result names this job, nonce, and input hash")
        try:
            raw = Path(job["input_path"]).read_bytes()
            expected = _recompute(raw)
            match = all(rec.get(f) == expected[f] for f in ACCEPTANCE["fields"])
            check("acceptance_exact_recompute", match,
                  "receiver recomputed from the job input; every field %s"
                  % ("matches" if match else "does NOT match"))
        except Exception as e:  # noqa: BLE001
            check("acceptance_exact_recompute", False, "recompute failed: %s" % e)
        try:
            on_time = _now() <= datetime.fromisoformat(agreement["deadline"])
        except Exception:  # noqa: BLE001
            on_time = False
        check("within_deadline", on_time, "deadline %s" % agreement["deadline"])
    accepted = all(c[1] for c in checks)
    verdict = {
        "schema": "commission.verdict.v1",
        "job_id": job["job_id"],
        "verdict": "accepted" if accepted else "rejected",
        "agreement_hash": job["agreement_hash"],
        "checks": [{"name": n, "pass": ok_, "detail": d} for n, ok_, d in checks],
        "verified_by": args.caller,
        "at": _utcnow_iso(),
    }
    sig = sign_record(verdict, _load_key(h, args.caller))
    verdict_signed = {"record": verdict, "signature": sig}
    with _state_lock(h):
        job = _job(h, args.job)
        job["verdict"] = verdict_signed
        if accepted:
            job["status"] = "VERIFIED_ACCEPTED"
            _event(job, "VERIFIED", {"verdict": "accepted"})
        else:
            job["status"] = "VERIFIED_REJECTED"
            _event(job, "VERIFIED", {"verdict": "rejected"})
            # Cancellation rule: rejection releases the reservation by the
            # FROZEN amount (a tamper demonstration must not corrupt the
            # accounting).
            allowances = _allowances(h)
            allow = allowances[_principal(h, "agent")]
            frozen_amount = job["agreement"]["amount"]
            allow["reserved"] -= frozen_amount
            _write_json(h / "allowances.json", allowances)
            _event(job, "RESERVATION_RELEASED", {"amount": frozen_amount})
        _save_job(h, job)
    if accepted:
        print("ACCEPTED %s — the result matches the frozen agreement exactly" % job["job_id"])
    else:
        print("REJECTED %s — no payment; the reservation is released" % job["job_id"])
    for n, ok_, d in checks:
        print("  %s: %s — %s" % (n, "pass" if ok_ else "FAIL", d))
    _write_json(h / "receipts" / ("verdict_%s.json" % job["job_id"]),
                verdict_signed)
    return 0 if accepted else 2
# -- settle (exactly once) -------------------------------------------------------


def _settlement_record(h: Path, job: dict, agreement: dict,
                       verdict_hash: str, amount: int, payee: str) -> dict:
    """The settlement receipt, bound to the exact authenticated agreement and
    verdict. Signed by the owner's key; the signature is deterministic for a
    given key and body, so crash recovery reproduces the identical record.
    """
    receipt = {
        "schema": "commission.settlement.v1",
        "job_id": job["job_id"],
        "agreement_hash": job["agreement_hash"],
        "verdict_hash": verdict_hash,
        "settlement_id": "settle:" + job["agreement_hash"],
        "amount": amount,
        "currency": CURRENCY + " (simulated)",
        "from": agreement["buyer"],
        "to": payee,
        "note": "simulated payment; no real money moved",
        "at": _utcnow_iso(),
    }
    return {"record": receipt,
            "signature": sign_record(receipt, _load_key(h, "owner"))}


def _finalize_allowance(allow: dict, amount: int, settle_id: str) -> None:
    """Move the reservation to spent, exactly once per settlement id."""
    if settle_id in allow.setdefault("settled_jobs", []):
        return
    allow["reserved"] -= amount
    allow["spent"] += amount
    allow["settled_jobs"].append(settle_id)


def _transfer_committed(ledger: dict, settle_id: str) -> bool:
    return any(t.get("settlement_id") == settle_id
               for t in ledger.get("transfers", []))


def cmd_settle(args) -> int:
    """Pay the frozen agreement exactly once, only after acceptance.

    Settlement is one atomic, deduplicated transaction:

    - amount and payee come ONLY from the authenticated agreement (the
      signed body), never from mutable jobs.json fields;
    - the verdict must be the buyer's receiver's verdict on THIS agreement
      (signature by a buyer-side key, agreement_hash bound);
    - the transfer carries an idempotency key (``settle:<agreement_hash>``)
      that is checked inside the writer lock before anything is appended.

    A crash between the ledger write and the local-record writes leaves the
    transfer on record: the next settle sees the idempotency key, completes
    the local records, and never appends a second transfer. Reconciliation
    reports that committed-but-incomplete state truthfully; it never
    recommends re-executing an already-settled obligation.

    Rejected work does not settle. Replaying settle for an already-settled
    job is refused with ALREADY_SETTLED. An already-earned obligation
    (verified and accepted) remains payable even if the agent's mandate was
    revoked after verification: revocation blocks new work, it does not
    silently erase an obligation the seller earned.
    """
    _require_caller(args, {"owner", "agent"})
    h = _home(Path(args.home) if args.home else None)
    with _state_lock(h):
        job = _job(h, args.job)
        # 1. The exact authenticated agreement. A substituted payee or amount
        #    in jobs.json fails here and settlement is refused.
        agreement = _authenticated_agreement(h, job)
        amount = agreement["amount"]
        payee = agreement["payee"]
        buyer = agreement["buyer"]
        agent_principal = agreement["agent"]
        # 2. The verdict must accept, must be signed by a buyer-side key, and
        #    must bind this exact agreement hash.
        verdict = job.get("verdict")
        if verdict is None or verdict["record"]["verdict"] != "accepted":
            raise CommissionError(
                "SETTLEMENT_REFUSED",
                "no payment before the buyer's receiver accepts the result "
                "(job is %s)" % job["status"],
            )
        vrec = verdict["record"]
        verifier = vrec.get("verified_by")
        if verifier not in ("owner", "agent"):
            raise CommissionError("VERDICT_NOT_BUYER_SIDE",
                                  "the verdict was not produced by the buyer's side")
        if _principal(h, verifier) not in (buyer, agent_principal):
            raise CommissionError("VERDICT_NOT_BUYER_SIDE",
                                  "the verdict signer is not this job's buyer side")
        _verify_signed(verdict, _pub(h, verifier), "verdict")
        if vrec.get("agreement_hash") != job["agreement_hash"]:
            raise CommissionError("VERDICT_MISMATCH",
                                  "the verdict binds a different agreement")
        verdict_hash = sha256_hex(canonical_json(vrec))
        settle_id = "settle:" + job["agreement_hash"]
        ledger = _read_json(h / "ledger.json", None)
        assert ledger["currency"].startswith(CURRENCY), "simulated currency only"
        allowances = _allowances(h)
        allow = allowances.get(agent_principal)
        if allow is None:
            raise CommissionError("NO_ALLOWANCE", "no allowance on record for the job's agent")
        if _transfer_committed(ledger, settle_id):
            # A previous attempt committed the transfer but died before the
            # local records were finished. Complete them idempotently; the
            # transfer is never appended twice. A replay of a fully
            # completed settlement is still refused, with no side effects.
            recovered = False
            if job["settlement"] is None:
                job["settlement"] = _settlement_record(
                    h, job, agreement, verdict_hash, amount, payee)
                job["status"] = "SETTLED"
                _event(job, "SETTLED_RECOVERED",
                       {"settlement_id": settle_id, "amount": amount})
                recovered = True
            if settle_id not in allow.get("settled_jobs", []):
                _finalize_allowance(allow, amount, settle_id)
                recovered = True
            if recovered:
                _write_json(h / "allowances.json", allowances)
                _save_job(h, job)
                _write_json(h / "receipts" / ("settlement_%s.json" % job["job_id"]),
                            job["settlement"])
                print("settlement %s already committed; local records completed — "
                      "no second transfer" % job["job_id"])
                return 0
            raise CommissionError(
                "ALREADY_SETTLED",
                "job %s already settled; replay cannot produce a second payment" % job["job_id"],
            )
        if job["settlement"] is not None:
            raise CommissionError(
                "ALREADY_SETTLED",
                "job %s already settled; replay cannot produce a second payment" % job["job_id"],
            )
        if allow["reserved"] < amount:
            raise CommissionError(
                "RESERVATION_MISSING",
                "the reserved amount is gone; the agreement record is inconsistent",
            )
        # 3. Fresh settlement: the transfer is the atomic commit point. It is
        #    appended under the writer lock with the idempotency key, so a
        #    retry can always tell committed from uncommitted.
        ledger["balances"][buyer] -= amount
        ledger["balances"][payee] = ledger["balances"].get(payee, 0) + amount
        ledger["transfers"].append({
            "settlement_id": settle_id,
            "job_id": job["job_id"],
            "agreement_hash": job["agreement_hash"],
            "amount": amount,
            "currency": CURRENCY,
            "from": buyer,
            "to": payee,
            "at": _utcnow_iso(),
        })
        _write_json(h / "ledger.json", ledger)
        if os.environ.get("COMMISSION_CRASH_AFTER") == "ledger":
            # Demonstration/test hook only: die exactly where the old code
            # died, so the recovery path can be exercised honestly.
            raise CommissionError("CRASH_SIMULATED",
                                  "demonstration hook: the process dies after the ledger write")
        _finalize_allowance(allow, amount, settle_id)
        _write_json(h / "allowances.json", allowances)
        job["settlement"] = _settlement_record(
            h, job, agreement, verdict_hash, amount, payee)
        job["status"] = "SETTLED"
        _event(job, "SETTLED", {"amount": amount, "settlement_id": settle_id})
        _save_job(h, job)
        _write_json(h / "receipts" / ("settlement_%s.json" % job["job_id"]),
                    job["settlement"])
    mandate_note = ""
    try:
        _agent_mandate_active(h)
    except CommissionError:
        mandate_note = " (the agent's mandate was revoked after verification; this obligation was already earned, so it remains payable)"
    ledger = _read_json(h / "ledger.json", None)
    print("SETTLED %s: %s paid to the seller%s" % (job["job_id"], _money(amount), mandate_note))
    print("owner balance: %s | seller balance: %s"
          % (_money(ledger["balances"][buyer]), _money(ledger["balances"].get(payee, 0))))
    print("exactly one settlement; replay is refused")
    return 0


# -- inspect / receipt / status / revoke / reconcile ---------------------------


def cmd_inspect(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    job = _job(h, args.job)
    a = job["agreement"]
    print("job:            %s" % job["job_id"])
    print("status:         %s" % job["status"])
    print("agreement hash: %s" % job["agreement_hash"])
    print("buyer (owner):  %s" % a["buyer"][:24])
    print("agent:          %s" % a["agent"][:24])
    print("seller (payee): %s" % a["payee"][:24])
    print("service:        %s" % a["service"])
    print("input sha256:   %s" % a["input_sha256"][:24])
    print("nonce:          %s" % a["nonce"])
    print("amount:         %s" % _money(a["amount"]))
    print("deadline:       %s" % a["deadline"])
    print("verdict:        %s" % (job["verdict"]["record"]["verdict"] if job["verdict"] else "none"))
    print("settled:        %s" % ("yes" if job["settlement"] else "no"))
    print("events:")
    for e in job["events"]:
        print("  %s  %s" % (e["at"], e["type"]))
    return 0


def cmd_receipt(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    job = _job(h, args.job)
    if job["settlement"] is None:
        raise CommissionError("NO_RECEIPT", "job %s has no settlement receipt" % job["job_id"])
    _verify_signed(job["settlement"], _pub(h, "owner"), "settlement receipt")
    print(json.dumps(job["settlement"]["record"], indent=1, sort_keys=True))
    print("signature verifies against the owner's key")
    return 0


def cmd_status(args) -> int:
    h = _home(Path(args.home) if args.home else None)
    ledger = _read_json(h / "ledger.json", {})
    allowances = _allowances(h)
    print("balances (%s, simulated):" % CURRENCY)
    names = {n: _principal(h, n) for n in ("owner", "agent", "seller")}
    for principal, bal in ledger.get("balances", {}).items():
        who = next((n for n, pr in names.items() if pr == principal), principal[:16])
        print("  %-6s %-24s %s" % (who, principal[:24], _money(bal)))
    print("agent allowance:")
    agent = _principal(h, "agent")
    allow = allowances.get(agent)
    if allow:
        print("  granted %s | reserved %s | spent %s"
              % (_money(allow["granted"]), _money(allow["reserved"]), _money(allow["spent"])))
    else:
        print("  none delegated yet")
    print("jobs:")
    for job in _read_json(h / "jobs.json", {}).values():
        print("  %s  %s" % (job["job_id"], job["status"]))
    print("separate identities on one operator host demonstrate the authority boundary,")
    print("not external adoption and not a functioning market")
    return 0


def cmd_revoke(args) -> int:
    """Only the owner revokes the agent's commission mandate.

    Revocation blocks NEW commissions. It does not erase an already-earned
    obligation: a job verified and accepted before revocation remains payable.
    """
    _require_caller(args, {"owner"})
    h = _home(Path(args.home) if args.home else None)
    wallet = Wallet.open(h / "wallet")
    try:
        wallet.revoke(_principal(h, "agent"), reason="OWNER_REVOKED")
    except WalletError as e:
        raise CommissionError("REVOKE_FAILED", str(e))
    print("owner revoked the agent's commission mandate")
    print("new commissions are blocked; already-earned obligations remain payable")
    return 0


def cmd_reconcile(args) -> int:
    """Read-only reconciliation: re-derive each job's true next step from the
    durable records (frozen agreements, event log, settlement ledger).

    This is the existing reconciliation pattern — the same read-only
    re-reading of durable records the wallet's event replay and the
    acknowledged-merge reconciliation use — not a recovery subsystem. It
    performs no side effects: no work dispatch, no settlement, no retries.
    """
    h = _home(Path(args.home) if args.home else None)
    jobs = _read_json(h / "jobs.json", {})
    if not jobs:
        print("no jobs on record")
        return 0
    ledger = _read_json(h / "ledger.json", {})
    for job in sorted(jobs.values(), key=lambda j: j["job_id"]):
        a = job["agreement"]
        settle_id = "settle:" + job["agreement_hash"]
        if job["settlement"] is not None:
            state = "SETTLED — nothing further; replaying settle is refused"
        elif _transfer_committed(ledger, settle_id):
            # Truthful: the transfer is on record, so the obligation is paid.
            # Re-running settle completes the local records idempotently; it
            # never re-executes the settlement.
            state = ("settlement COMMITTED (transfer on record) but local records "
                     "incomplete — re-run settle to complete idempotently; "
                     "never a second transfer")
        elif job["status"] == "VERIFIED_ACCEPTED":
            state = "VERIFIED+ACCEPTED, unsettled — settle exactly once"
        elif job["status"] == "VERIFIED_REJECTED":
            state = "VERIFIED+REJECTED — no payment; reservation released"
        elif job["status"] == "SUBMITTED":
            state = "SUBMITTED, unverified — buyer's receiver must verify"
        elif job["status"] == "COMMISSIONED":
            ran = (h / "seller_runs" / ("%s.json" % job["job_id"])).exists()
            state = ("COMMISSIONED, work recorded but not submitted — seller may submit"
                     if ran else "COMMISSIONED, no work recorded — seller may work then submit")
        else:
            state = job["status"]
        print("%s  %s  (%s)" % (job["job_id"], job["status"], state))
        print("    agreement %s | amount %s | events %d"
              % (job["agreement_hash"][:16], _money(a["amount"]), len(job["events"])))
    print("reconciliation is read-only: no work dispatched, no payment made")
    return 0


# -- cli ----------------------------------------------------------------------


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="commission",
                                description="thin local preview for commissioning agent work")
    sub = p.add_subparsers(dest="command", required=True)

    def caller(sp):
        sp.add_argument("--caller", required=True, choices=["owner", "agent", "seller"])
        sp.add_argument("--home", default=None)

    s = sub.add_parser("init", help="one-time owner setup")
    s.add_argument("--home", default=None)
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("delegate", help="owner grants the agent a bounded budget")
    caller(s)
    s.add_argument("--budget", type=int, required=True)
    s.add_argument("--hours", type=float, default=2.0)
    s.set_defaults(fn=cmd_delegate)

    s = sub.add_parser("offer", help="seller offers the service")
    caller(s)
    s.add_argument("--price", type=int, required=True)
    s.set_defaults(fn=cmd_offer)

    s = sub.add_parser("commission", help="agent commissions bounded work; the agreement freezes")
    caller(s)
    s.add_argument("--offer", required=True)
    s.add_argument("--input", required=True)
    s.add_argument("--raise-budget", type=int, default=None)
    s.add_argument("--rewrite-acceptance", action="store_true")
    s.add_argument("--payee", default=None)
    s.add_argument("--price-override", type=int, default=None)
    s.add_argument("--self-authorize", action="store_true")
    s.set_defaults(fn=cmd_commission)

    s = sub.add_parser("work", help="seller performs the work")
    caller(s)
    s.add_argument("--job", required=True)
    s.add_argument("--wrong-input", action="store_true",
                   help="demo hook: run the implementation on different text")
    s.set_defaults(fn=cmd_work)

    s = sub.add_parser("submit", help="seller submits the signed result")
    caller(s)
    s.add_argument("--job", required=True)
    s.add_argument("--tamper-result", action="store_true",
                   help="demo hook: alter the result after the seller signed it")
    s.set_defaults(fn=cmd_submit)

    s = sub.add_parser("verify", help="buyer's receiver checks the result")
    caller(s)
    s.add_argument("--job", required=True)
    s.add_argument("--tamper-agreement", choices=["amount"], default=None,
                   help="demo hook: alter the frozen agreement before checking")
    s.add_argument("--tamper-payee", action="store_true",
                   help="demo hook: alter the payee before checking")
    s.add_argument("--tamper-input-hash", action="store_true",
                   help="demo hook: alter the bound input hash before checking")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("settle", help="pay the accepted job exactly once")
    caller(s)
    s.add_argument("--job", required=True)
    s.set_defaults(fn=cmd_settle)

    s = sub.add_parser("inspect", help="show a job: agreement, events, status")
    s.add_argument("--home", default=None)
    s.add_argument("--job", required=True)
    s.set_defaults(fn=cmd_inspect)

    s = sub.add_parser("receipt", help="show and verify the settlement receipt")
    s.add_argument("--home", default=None)
    s.add_argument("--job", required=True)
    s.set_defaults(fn=cmd_receipt)

    s = sub.add_parser("status", help="balances, allowance, jobs")
    s.add_argument("--home", default=None)
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("revoke", help="owner revokes the agent's mandate")
    caller(s)
    s.set_defaults(fn=cmd_revoke)

    s = sub.add_parser("reconcile", help="read-only: re-derive each job's next step")
    s.add_argument("--home", default=None)
    s.set_defaults(fn=cmd_reconcile)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except CommissionError as e:
        print("refused [%s]: %s" % (e.code, e.message), file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print("error [%s]: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
