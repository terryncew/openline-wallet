#!/usr/bin/env python3
"""OpenLine Exchange Preview — full kernel loop, narrated.

Need -> Find -> Contact -> Offer -> Authorize -> Work -> Verify ->
Accept/Refuse -> Settle -> Receipt -> Revoke, then agent swap and seller
revocation. All local, all simulated (SIM_USD), controlled agents only.

Usage: python3 run_demo.py [--home PATH]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from exchange.kernel import Exchange, ExchangeError  # noqa: E402


def say(step: str, text: str) -> None:
    print("\n=== %s ===\n%s" % (step, text))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=".exchange-home")
    args = ap.parse_args()
    x = Exchange(Path(args.home))

    say("BOOTSTRAP", "Three seller agents advertise a digest-report capability.\n"
        "The buyer-owner funds buyer-agent with 300 SIM_USD (simulated).")
    x.bootstrap(budget=300)
    for m in x.transport.receive("buyer-agent"):
        pass  # offers sit in the buyer's inbox; the UI shows them

    say("NEED", "Buyer-agent posts a need: a digest report of a field note.")
    need = x.post_need("digest report of a field note",
                       ["digest", "report"], 50, service="text_digest")

    say("FIND", "The broker searches the registry and ranks candidates.")
    ranked = x.find(need)
    for listing, score, reasons in ranked:
        print("  %s: %d SIM_USD (simulated) — %s"
              % (listing["seller_name"], listing["price"], "; ".join(reasons)))

    say("CONTACT / OFFER / AUTHORIZE",
        "Buyer-agent selects the best candidate and contacts the seller.\n"
        "The seller proposes the offer; the owner already authorized the\n"
        "buyer-agent's budget, so the Wallet lets the commission proceed.")
    deal = x.run_deal(need, ranked, index=0)
    say("WORK / VERIFY / ACCEPT / SETTLE / RECEIPT",
        "Seller %s worked, the buyer-controlled receiver verified the result,\n"
        "and the commission settled exactly once: %d SIM_USD (simulated).\n"
        "Receipt: %s"
        % (deal["listing"]["seller_name"], deal["settlement"]["amount"],
           deal["settlement"]["settlement_id"]))

    say("REFUSE PATH", "A second need; this time the seller works on the wrong\n"
        "input. The receiver rejects it: no payment, reservation released.")
    need2 = x.post_need("digest of a second field note", ["digest"], 100,
                        service="text_digest")
    ranked2 = x.find(need2)
    deal2 = x.run_deal(need2, ranked2, index=0,
                       text="a completely different field note", wrong_input=True)
    print("verdict: %s (job %s)" % (deal2["verdict"], deal2["job_id"]))

    say("AGENT SWAP", "The owner revokes buyer-agent and delegates buyer-agent-b.\n"
        "Authority does not leak: the old agent is blocked, the new agent\n"
        "starts with a clean allowance.")
    swap = x.swap_buyer_agent()
    print("old agent '%s' blocked; new agent '%s' allowance: granted %s, reserved %s"
          % (swap["old_agent"], swap["new_agent"],
             swap["new_allowance"].get("granted"), swap["new_allowance"].get("reserved")))

    say("NEW AGENT WORKS", "Buyer-agent-b commissions its own deal under its own budget.")
    need3 = x.post_need("digest for the swapped agent", ["digest"], 100,
                        service="text_digest")
    ranked3 = x.find(need3)
    deal3 = x.run_deal(need3, ranked3, index=0, agent_name="agent-b")
    print("verdict: %s, settled %s SIM_USD (simulated)"
          % (deal3["verdict"], deal3["settlement"]["amount"]))

    say("SELLER REVOCATION", "Seller C's listing is revoked. Future use stops;\n"
        "existing receipts survive.")
    seller_c = [l for l in x.registry.all() if l["seller_name"] == "Seller C"][0]
    x.revoke_seller(seller_c["listing_id"], "seller left the preview")
    try:
        x.select([(seller_c, 1.0, ["forced"])])
        print("ERROR: revoked listing was selectable")
        return 1
    except ExchangeError as e:
        print("revoked listing refused at selection: [%s]" % e.code)

    say("RECONCILE", "Read-only reconciliation over the durable records:")
    out = x.reconcile()
    for line in out.splitlines():
        if "audit" in line or "BALANCED" in line or "MISMATCH" in line:
            print("  " + line)

    snap = x.snapshot("demo")
    say("DONE", "Snapshot written: %s\n"
        "Render the developer-preview UI with:\n"
        "  python3 ui/dashboard.py %s -o ui/index.html" % (snap, snap))
    return 0


if __name__ == "__main__":
    sys.exit(main())
