"""Local owner-operated buyer UX over the existing Exchange and Wallet.

This is the trusted-operator preview, not remote authentication. No browser
endpoint can sign, delegate, commission or revoke. Each approval uses a new
agent with a one-job price budget, then freezes the existing signed agreement.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path

from openline_wallet.canonical import canonical_json
from openline_wallet import Wallet
from .kernel import Exchange, ExchangeError
from .evidence import export_selected


def prepare(x, listing_id, input_path, max_budget, hours=1):
    hours = float(hours)
    listing = x.registry.get(listing_id)
    if not listing or listing["standing"] != "active":
        raise ValueError("worker listing is not active")
    if type(max_budget) is not int or max_budget < listing["price"]:
        raise ValueError("maximum authorized budget is below the offered price")
    if not math.isfinite(hours) or not 0 < hours <= 24:
        raise ValueError("expiry must be finite and between zero and 24 hours")
    path = Path(input_path).resolve()
    raw = path.read_bytes()
    # The existing commission contract requires a nonce in the first line.
    if not json.loads(raw.splitlines()[0]).get("nonce"):
        raise ValueError("input must begin with a JSON nonce line")
    return {"format": "openline.exchange.buyer-review.v1", "listing_id": listing_id,
            "worker": listing["seller_id"], "offer_id": listing["offer_id"],
            "service": listing["service"], "input_path": str(path),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "agreed_price": listing["price"], "maximum_budget": max_budget,
            # Wallet canonical JSON deliberately forbids floating values.
            "hours": format(hours, '.12g'), "scope": "commission:work / exact_recompute text_digest",
            "currency": "SIM_USD (simulated)"}


def review_hash(plan):
    return hashlib.sha256(canonical_json(plan)).hexdigest()


def authorize(x, plan, approved_hash):
    if review_hash(plan) != approved_hash:
        raise ValueError("review digest does not match explicit approval")
    current = prepare(x, plan["listing_id"], plan["input_path"],
                      plan["maximum_budget"], plan["hours"])
    if current != plan:
        raise ValueError("reviewed input, offer, worker or terms changed; review again")
    # Fresh allowance grants only the agreed price even when the buyer's
    # ceiling is higher. Existing Wallet expiry and revocation are reused.
    identity = "buyer-" + approved_hash[:24]
    jobs = x.read_json("jobs.json", {})
    agent_path = x.chome / "keys" / (identity + ".pub.json")
    if agent_path.exists():
        principal = x.principal(identity)
        for job in jobs.values():
            a = job["agreement"]
            if a["agent"] == principal and a["input_sha256"] == plan["input_sha256"]:
                return {"job_id": job["job_id"], "agent_identity": identity,
                        "review_sha256": approved_hash, "replayed": True}
        # Never re-grant a revoked identity during a retry.
        x.commission_mod._agent_mandate_active(x.chome, identity)
    else:
        x.cli_ok("init-identity", "--name", identity, "--role", "agent")
        x.cli_ok("delegate", "--caller", "owner", "--to", identity,
                 "--budget", str(plan["agreed_price"]), "--hours", str(plan["hours"]))
    job_id = x.commission(x.registry.get(plan["listing_id"]), agent_name=identity,
                          input_path=plan["input_path"])
    return {"job_id": job_id, "agent_identity": identity,
            "review_sha256": approved_hash, "replayed": False}


def standing(x, identity):
    principal = x.principal(identity)
    wallet = Wallet.open(x.chome / "wallet")
    try:
        x.commission_mod._agent_mandate_active(x.chome, identity)
        state = "ACTIVE"
    except Exception:
        state = "NOT ACTIVE"
    return {"agent_identity": identity, "principal": principal, "standing": state,
            "allowance": x.read_json("allowances.json", {}).get(principal),
            "authority_history_retained_locally": bool(wallet)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Owner-operated local buyer review; SIM_USD only")
    ap.add_argument("--home", required=True)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("review")
    p.add_argument("--listing", required=True); p.add_argument("--input", required=True)
    p.add_argument("--max-budget", type=int, required=True); p.add_argument("--hours", type=float, default=1)
    p.add_argument("--out", required=True)
    p = sub.add_parser("authorize")
    p.add_argument("--review", required=True); p.add_argument("--approve-sha256", required=True)
    p = sub.add_parser("status"); p.add_argument("--agent", required=True)
    p = sub.add_parser("revoke"); p.add_argument("--agent", required=True)
    p = sub.add_parser("revoke-worker"); p.add_argument("--listing", required=True)
    p.add_argument("--reason", default="owner withdrew this worker from local selection")
    p = sub.add_parser("complete"); p.add_argument("--job", required=True)
    p.add_argument("--wrong-input", action="store_true", help="local failure demonstration")
    p = sub.add_parser("evidence"); p.add_argument("--job", action="append", required=True)
    p.add_argument("--out", required=True)
    args = ap.parse_args(argv); x = Exchange(args.home)
    if args.command == "review":
        plan = prepare(x, args.listing, args.input, args.max_budget, args.hours)
        with open(args.out, "x") as f: json.dump(plan, f, indent=2)
        out = {"review": plan, "approve_sha256": review_hash(plan)}
    elif args.command == "authorize":
        out = authorize(x, json.loads(Path(args.review).read_text()), args.approve_sha256)
    elif args.command == "status": out = standing(x, args.agent)
    elif args.command == "revoke":
        x.cli_ok("revoke", "--caller", "owner", "--of", args.agent); out = standing(x, args.agent)
    elif args.command == "revoke-worker":
        x.revoke_seller(args.listing, args.reason)
        out = {"listing_id": args.listing, "standing": "revoked",
               "boundary": "local registry withdrawal; existing receipts and earned obligations retained"}
    elif args.command == "complete":
        job = x.read_json("jobs.json")[args.job]
        seller = job["agreement"]["seller"]
        listing = next(l for l in x.registry.all() if l["seller_id"] == seller)
        if job.get("verdict") is None:
            x.deliver(args.job, listing["identity"], wrong_input=args.wrong_input)
        out = x.adjudicate(args.job)
    else:
        out = export_selected(x, args.job)
        with open(args.out, "x") as f: json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
