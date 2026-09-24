"""OpenLine Exchange Preview — the exchange kernel.

The smallest credible open exchange kernel: one buyer agent posts a need,
several seller agents advertise capabilities, a matcher ranks them, the buyer
picks one, agents talk through a pluggable Transport, the buyer's Wallet
authorizes the commission, the seller works, the buyer-controlled receiver
verifies, accepted work settles exactly once in SIM_USD (simulated), the
receipt survives, and then: the buyer agent is swapped (authority does not
leak) and a seller is revoked (future use stops).

Everything consequential reuses the frozen commission machinery
(demo/commission-work-001/commission.py): reserve -> work -> verify ->
settle exactly once -> receipt, with the deterministic request-identity
transaction, idempotent verify, truthful PENDING-release reconcile, and
read-only reconcile. This file adds no ledger and no new money rules.

What this is NOT: a functioning market. No real counterparties, no real
demand, no real money, no reputation, no liquidity. Controlled agents,
simulated funds, one host. See README.md.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import time
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from .interfaces import Receiver, Settlement
from .matcher import KeywordMatcher
from .registry import FileRegistry
from .transport import LocalMailboxTransport

COMMISSION_DIR = Path(__file__).resolve().parent.parent.parent / "commission-work-001"
CURRENCY_LABEL = "SIM_USD (simulated)"
BANNER = ("LOCAL DEVELOPER PREVIEW — controlled agents · SIM_USD simulated funds "
          "· NOT a functioning market")


class ExchangeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _load_commission():
    spec = importlib.util.spec_from_file_location(
        "commission_work", str(COMMISSION_DIR / "commission.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommissionReceiver(Receiver):
    """Reference Receiver: delegates to the commission preview's verify.

    Inherits its invariants: buyer-side only, check-then-act under one
    writer lock, idempotent replay completes a pending release exactly once.
    """

    def __init__(self, exchange: "Exchange"):
        self.x = exchange

    def check(self, job_id: str, caller: str = "owner") -> dict:
        code, out, err = self.x.cli("verify", "--caller", caller, "--job", job_id)
        if code == 0:
            verdict = "accepted"
        elif code == 2:
            verdict = "rejected"
        else:
            raise ExchangeError("RECEIVER_FAILED",
                                "verify exited %d: %s" % (code, err.strip()))
        job = self.x.read_json("jobs.json")[job_id]
        return {"verdict": verdict, "job_id": job_id,
                "checks": job["verdict"]["record"]["checks"],
                "output": out}


class CommissionSettlement(Settlement):
    """Reference Settlement: delegates to the commission preview's settle.

    Inherits exactly-once settlement via the idempotency key
    (settle:<agreement_hash>) checked inside the writer lock.
    """

    def __init__(self, exchange: "Exchange"):
        self.x = exchange

    def settle(self, job_id: str, caller: str = "owner") -> dict:
        code, out, err = self.x.cli("settle", "--caller", caller, "--job", job_id)
        if code != 0:
            raise ExchangeError("SETTLEMENT_FAILED",
                                "settle exited %d: %s" % (code, err.strip()))
        job = self.x.read_json("jobs.json")[job_id]
        rec = job["settlement"]["record"]
        return {"settlement_id": rec["settlement_id"], "amount": rec["amount"],
                "status": "settled", "output": out}


class Exchange:
    """Owns one exchange home and drives the kernel loop."""

    def __init__(self, home: Path | str):
        self.home = Path(home)
        self.chome = self.home / "commission"
        self.commission_mod = _load_commission()
        self.transport = LocalMailboxTransport(self.home / "mail")
        self.registry = FileRegistry(self.home / "registry.json")
        self.matcher = KeywordMatcher()
        self.receiver = CommissionReceiver(self)
        self.settlement = CommissionSettlement(self)
        self.timeline: list[dict] = []
        self.events: list[dict] = []
        self.needs: list[dict] = []
        self._t0 = time.monotonic()
        self._nonce = 0
        self._agents = ["buyer-agent", "broker", "seller-a", "seller-b", "seller-c"]

    # -- low-level ------------------------------------------------------

    def cli(self, *argv) -> tuple[int, str, str]:
        """Run one commission command against this exchange's commission home."""
        argv = [argv[0], "--home", str(self.chome), *argv[1:]]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.commission_mod.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def cli_ok(self, *argv) -> str:
        code, out, err = self.cli(*argv)
        if code != 0:
            raise ExchangeError("COMMISSION_STEP_FAILED",
                                "%s exited %d: %s" % (argv[0], code, err.strip() or out.strip()))
        return out

    def read_json(self, name: str, default=None):
        p = self.chome / name
        return json.loads(p.read_text()) if p.exists() else default

    def principal(self, name: str) -> str:
        return json.loads((self.chome / "keys" / ("%s.pub.json" % name)).read_text())["principal"]

    def _tick(self) -> str:
        return "%.1f" % (time.monotonic() - self._t0)

    def log(self, actor: str, event: str, detail: str = "") -> None:
        self.timeline.append({"t": self._tick(), "actor": actor,
                              "event": event, "detail": detail})

    def announce(self, kind: str, detail: str) -> None:
        self.events.append({"kind": kind, "detail": detail})
        self.log("exchange", kind, detail)

    def msg(self, sender: str, recipient: str, kind: str, body: dict) -> str:
        return self.transport.send(sender, recipient, kind, body)

    def fresh_nonce(self, tag: str) -> str:
        self._nonce += 1
        return "exch-%s-%d" % (tag, self._nonce)

    # -- bootstrap ------------------------------------------------------

    def bootstrap(self, budget: int = 300) -> None:
        """Create identities, fund the buyer side, publish three seller offers."""
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "snapshots").mkdir(parents=True, exist_ok=True)
        self.cli_ok("init")
        for name in ("seller-b", "seller-c"):
            self.cli_ok("init-identity", "--name", name, "--role", "seller")
        self.cli_ok("init-identity", "--name", "agent-b", "--role", "agent")
        self.cli_ok("delegate", "--caller", "owner", "--budget", str(budget))
        self.log("owner", "BUDGET_DELEGATED",
                 "buyer-agent allowance %d SIM_USD (simulated)" % budget)

        sellers = [
            ("seller", "Seller A", "offer-digest-a", 50,
             "digest-report", "text_digest", "text_digest@1.0"),
            ("seller-b", "Seller B", "offer-digest-b", 40,
             "digest-report", "text_digest", "text_digest@1.0"),
            ("seller-c", "Seller C", "offer-digest-c", 60,
             "digest-report", "text_digest", "text_digest@1.0"),
        ]
        for ident, label, offer_id, price, capability, service, artifact in sellers:
            self.cli_ok("offer", "--caller", "seller", "--as", ident,
                        "--price", str(price), "--offer-id", offer_id)
            listing = {
                "seller_id": self.principal(ident),
                "seller_name": label,
                "identity": ident,
                "capability": capability,
                "service": service,
                "artifact": artifact,
                "price": price,
                "currency": CURRENCY_LABEL,
                "terms": "results only; the seller retains its implementation",
                "required_permissions": [],
                "acceptance": {
                    "type": "exact_recompute",
                    "rule": "buyer receiver recomputes every field from the job input",
                },
                "evidence": [],
                "offer_id": offer_id,
                "description": "digest report service: input sha256, word and line counts",
            }
            listing_id = self.registry.register(listing)
            self.msg(ident, "buyer-agent", "OFFER_LIST",
                     {"listing_id": listing_id, "offer_id": offer_id,
                      "price": price, "capability": capability})
            self.log(ident, "OFFER_ADVERTISED",
                     "%s lists %s at %d SIM_USD (simulated)" % (label, capability, price))
        self.announce("EXCHANGE_BOOTSTRAPPED",
                      "3 sellers listed; buyer-agent funded with %d SIM_USD (simulated)" % budget)

    # -- the loop -------------------------------------------------------

    def post_need(self, description: str, keywords: list[str],
                  max_price: int, service: str | None = None) -> dict:
        need = {
            "need_id": "need-" + self.fresh_nonce("need"),
            "buyer": "buyer-agent",
            "description": description,
            "keywords": keywords,
            "max_price": max_price,
            "service": service,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self.needs.append(need)
        self.msg("buyer-agent", "broker", "NEED_POSTED", need)
        self.log("buyer-agent", "NEED_POSTED",
                 "%s (budget %d SIM_USD simulated)" % (description, max_price))
        return need

    def find(self, need: dict) -> list[tuple[dict, float, list[str]]]:
        candidates = self.registry.search({
            "keywords": need["keywords"],
            "max_price": need["max_price"],
            "service": need.get("service"),
        })
        ranked = self.matcher.rank(need, candidates)
        self.msg("broker", "buyer-agent", "MATCH_RESPONSE",
                 {"need_id": need["need_id"],
                  "candidates": [{"listing_id": l["listing_id"],
                                   "seller_name": l["seller_name"],
                                   "price": l["price"], "score": s,
                                   "reasons": r} for l, s, r in ranked]})
        self.log("broker", "MATCH_RESPONSE",
                 "%d candidate(s) for %s" % (len(ranked), need["need_id"]))
        return ranked

    def select(self, ranked: list[tuple[dict, float, list[str]]],
               index: int = 0) -> dict:
        if not ranked:
            raise ExchangeError("NO_CANDIDATES", "the matcher returned no candidates")
        listing, score, reasons = ranked[index]
        # Re-read the listing: a stale dict must not bypass a revocation.
        fresh = self.registry.get(listing["listing_id"])
        if fresh is None or fresh.get("standing") != "active":
            raise ExchangeError("LISTING_REVOKED",
                                "listing %s is revoked or gone; it cannot be commissioned"
                                % listing["listing_id"])
        listing = fresh
        self.msg("buyer-agent", listing["identity"], "MATCH_REQUEST",
                 {"listing_id": listing["listing_id"],
                  "offer_id": listing["offer_id"]})
        self.log("buyer-agent", "CONTACT",
                 "buyer-agent -> %s: requested %s" % (listing["identity"], listing["offer_id"]))
        self.msg(listing["identity"], "buyer-agent", "AGREEMENT_PROPOSED",
                 {"listing_id": listing["listing_id"],
                  "offer_id": listing["offer_id"], "price": listing["price"],
                  "terms": listing["terms"]})
        self.log("buyer-agent", "OFFER_SELECTED",
                 "%s at %d SIM_USD (simulated): %s"
                 % (listing["seller_name"], listing["price"], "; ".join(reasons)))
        return listing

    def _write_input(self, tag: str, text: str) -> str:
        nonce = self.fresh_nonce(tag)
        p = self.home / ("input-%s.txt" % nonce)
        p.write_text(json.dumps({"nonce": nonce}) + "\n" + text + "\n")
        return str(p)

    def commission(self, listing: dict, text: str = "field note: the river gauge read 4.2m at dawn",
                   agent_name: str = "agent", input_path: str | None = None) -> str:
        """Freeze the agreement and reserve funds. Returns job_id.

        input_path lets a caller retry the IDENTICAL request (same nonce)
        after an interruption, which is what makes recovery converge.
        """
        # Re-read: revocation between selection and commission must stop this
        # before any funds move.
        fresh = self.registry.get(listing["listing_id"])
        if fresh is None or fresh.get("standing") != "active":
            raise ExchangeError("LISTING_REVOKED",
                                "listing %s is revoked; commission refused before any funds move"
                                % listing["listing_id"])
        listing = fresh
        ident = listing["identity"]
        inp = input_path or self._write_input("job", text)
        args = ["commission", "--caller", "agent", "--as-agent", agent_name,
                "--offer", listing["offer_id"], "--input", inp]
        code, out, err = self.cli(*args)
        if code != 0:
            raise ExchangeError("COMMISSION_REFUSED",
                                "commission refused [%s]: %s" % (agent_name, err.strip()))
        job_id = None
        for line in out.splitlines():
            if line.startswith("agreement frozen:"):
                job_id = line.split(":")[1].strip()
            elif line.startswith("recovered the interrupted commission for this request:"):
                job_id = line.split(":")[1].strip()
        if job_id is None:
            raise ExchangeError("COMMISSION_FAILED",
                                "commission printed no job id: %s" % out.strip())
        self.msg("buyer-agent", ident, "AGREEMENT_FROZEN",
                 {"job_id": job_id, "offer_id": listing["offer_id"],
                  "amount": listing["price"]})
        self.log("buyer-agent", "AGREEMENT_FROZEN",
                 "%s reserved %d SIM_USD (simulated)"
                 % (job_id, listing["price"]))
        return job_id

    def deliver(self, job_id: str, seller_ident: str, wrong_input: bool = False) -> None:
        args = ["work", "--caller", "seller", "--as", seller_ident, "--job", job_id]
        if wrong_input:
            args.append("--wrong-input")
        self.cli_ok(*args)
        self.cli_ok("submit", "--caller", "seller", "--as", seller_ident, "--job", job_id)
        self.msg(seller_ident, "buyer-agent", "RESULT_DELIVERED", {"job_id": job_id})
        self.log(seller_ident, "RESULT_DELIVERED", job_id)

    def adjudicate(self, job_id: str) -> dict:
        """Buyer-controlled receiver verifies; accepted work settles exactly once."""
        result = self.receiver.check(job_id)
        verdict = result["verdict"]
        self.msg("receiver", "buyer-agent", "VERDICT",
                 {"job_id": job_id, "verdict": verdict})
        self.msg("receiver", "seller", "VERDICT",
                 {"job_id": job_id, "verdict": verdict})
        self.log("receiver", "VERDICT", "%s: %s" % (job_id, verdict.upper()))
        if verdict == "accepted":
            settled = self.settlement.settle(job_id)
            self.msg("exchange", "buyer-agent", "SETTLED",
                     {"job_id": job_id, **settled})
            receipt = self.read_json("jobs.json")[job_id]["settlement"]["record"]
            self.msg("exchange", "buyer-agent", "RECEIPT",
                     {"job_id": job_id,
                      "settlement_id": receipt["settlement_id"],
                      "amount": receipt["amount"]})
            self.log("exchange", "SETTLED_ONCE",
                     "%s settled %d SIM_USD (simulated); receipt %s"
                     % (job_id, receipt["amount"], receipt["settlement_id"]))
            result["settlement"] = settled
        else:
            self.log("exchange", "NO_PAYMENT",
                     "%s rejected; reservation released, nothing settled" % job_id)
        return result

    def run_deal(self, need: dict, ranked, index: int = 0,
                 text: str = "field note: the river gauge read 4.2m at dawn",
                 wrong_input: bool = False, agent_name: str = "agent") -> dict:
        listing = self.select(ranked, index)
        job_id = self.commission(listing, text=text, agent_name=agent_name)
        self.deliver(job_id, listing["identity"], wrong_input=wrong_input)
        result = self.adjudicate(job_id)
        return {"need": need, "listing": listing, "job_id": job_id, **result}

    # -- authority portability + revocation ------------------------------

    def swap_buyer_agent(self, old: str = "agent", new: str = "agent-b",
                         budget: int = 300) -> dict:
        """Revoke the old buyer agent; delegate a fresh agent.

        The new agent starts with its own allowance and zero committed
        funds: it cannot touch the old agent's reservations, settlements, or
        releases. The old agent's new commissions are refused.
        """
        before = self.read_json("allowances.json", {})
        old_principal = self.principal(old)
        old_allow = dict(before.get(old_principal, {}))
        self.cli_ok("revoke", "--caller", "owner", "--of", old)
        self.cli_ok("delegate", "--caller", "owner", "--to", new,
                    "--budget", str(budget))
        after = self.read_json("allowances.json", {})
        new_allow = after.get(self.principal(new), {})
        # The old agent is blocked: a fresh commission attempt must be refused.
        probe_input = self._write_input("probe", "probe text")
        code, _, err = self.cli("commission", "--caller", "agent",
                                "--as-agent", old, "--offer", "offer-digest-a",
                                "--input", probe_input)
        self.announce("AGENT_SWAPPED",
                      "buyer agent '%s' revoked; '%s' delegated %d SIM_USD (simulated). "
                      "Old agent commission probe: %s."
                      % (old, new, budget,
                         "REFUSED (%s)" % err.strip().split(":")[0] if code != 0
                         else "UNEXPECTEDLY ACCEPTED"))
        if code == 0:
            raise ExchangeError("AUTHORITY_LEAKED",
                                "the revoked agent could still commission")
        return {"old_agent": old, "new_agent": new,
                "old_allowance_preserved": old_allow,
                "new_allowance": new_allow}

    def revoke_seller(self, listing_id: str, reason: str) -> None:
        """Revoke a seller's listing. Future use stops; past receipts survive."""
        listing = self.registry.get(listing_id)
        if listing is None:
            raise ExchangeError("LISTING_NOT_FOUND", "no such listing: %s" % listing_id)
        self.registry.revoke(listing_id, reason)
        for recipient in ("buyer-agent", "broker"):
            self.msg("registry", recipient, "REVOKED",
                     {"listing_id": listing_id,
                      "seller_name": listing["seller_name"], "reason": reason})
        self.announce("SELLER_REVOKED",
                      "%s revoked: %s. Existing receipts survive; new commissions refused."
                      % (listing["seller_name"], reason))

    def reconcile(self) -> str:
        """Read-only reconciliation over the durable commission records."""
        code, out, err = self.cli("reconcile")
        if code != 0:
            raise ExchangeError("RECONCILE_FAILED", err.strip())
        self.log("exchange", "RECONCILE", "read-only; changed nothing")
        return out

    # -- snapshot for the UI --------------------------------------------

    def _mail_snapshot(self) -> list[dict]:
        seen: list[dict] = []
        for name in self._agents + ["receiver", "registry", "exchange"]:
            for m in self.transport.peek(name):
                seen.append({"msg_id": m["msg_id"], "sender": m["sender"],
                             "recipient": m["recipient"], "kind": m["kind"]})
        return seen

    def snapshot(self, name: str) -> Path:
        jobs_raw = self.read_json("jobs.json", {})
        jobs = []
        for jid, job in sorted(jobs_raw.items()):
            a = job["agreement"]
            jobs.append({
                "job_id": jid,
                "seller": a["seller"][:24],
                "seller_name": next(
                    (l["seller_name"] for l in self.registry.all()
                     if l["seller_id"] == a["seller"]), a["seller"][:16]),
                "amount": a["amount"],
                "status": job["status"],
                "verdict": (job.get("verdict") or {}).get("record", {}).get("verdict"),
            })
        receipts = []
        rdir = self.chome / "receipts"
        if rdir.is_dir():
            for rp in sorted(rdir.glob("settlement_*.json")):
                rec = json.loads(rp.read_text())["record"]
                receipts.append({"job_id": rec["job_id"],
                                 "settlement_id": rec["settlement_id"],
                                 "amount": rec["amount"],
                                 "hash": rec["settlement_id"]})
        ledger = self.read_json("ledger.json", {})
        agents = []
        for ident, role in (("owner", "buyer-owner (human)"),
                            ("agent", "buyer agent"),
                            ("agent-b", "buyer agent (swapped in)"),
                            ("seller", "Seller A"),
                            ("seller-b", "Seller B"),
                            ("seller-c", "Seller C")):
            try:
                agents.append({"name": ident, "role": role,
                               "principal": self.principal(ident)[:32]})
            except Exception:  # noqa: BLE001
                pass
        snap = {
            "meta": {"title": "OpenLine Exchange Preview",
                     "generated_at": datetime.now(timezone.utc).isoformat(),
                     "home": str(self.home), "simulated": True},
            "banner": BANNER,
            "agents": agents,
            "needs": self.needs,
            "listings": self.registry.all(),
            "timeline": self.timeline,
            "messages": self._mail_snapshot(),
            "jobs": jobs,
            "receipts": receipts,
            "ledger": {"balances": ledger.get("balances", {}),
                       "transfers": ledger.get("transfers", [])},
            "events": self.events,
        }
        path = self.home / "snapshots" / ("%s.json" % name)
        path.write_text(json.dumps(snap, indent=2))
        return path
