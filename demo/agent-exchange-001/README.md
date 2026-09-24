# OpenLine Exchange Preview — the exchange kernel

**LOCAL DEVELOPER PREVIEW. Controlled agents. SIM_USD simulated funds.
Not a functioning market.**

This is the smallest credible open exchange kernel: one buyer agent posts a
need, three seller agents advertise capabilities, a broker matches them, the
buyer picks one, agents talk through a pluggable transport, the buyer's
Wallet authorizes the commission, the seller works, the buyer-controlled
receiver verifies, accepted work settles exactly once in SIM_USD after
acceptance, the receipt survives — then the buyer agent is swapped
(authority does not leak) and a seller is revoked (future use stops).

## The honest boundary

We built the exchange **kernel**. Other people can build the bazaar.

The kernel says:

- Here is a need.
- Here is an offer.
- Here is who authorized the transaction.
- Here is the exact thing delivered.
- Here is the buyer's independent acceptance decision.
- Here is the consequence that followed.
- Here is the receipt.
- Here is whether the authority still stands.

That is deeply OpenLine. What we did **not** build — and cannot manufacture
in a repo — is "there is a market": real counterparties, real demand, real
money, reputation, liquidity, or people choosing to use it when we are not
standing there. There is no discovery network, no reputation system, no
token, no auction mechanism, no universal messaging standard, no real-money
payments. SIM_USD cannot become a payment product by swapping in Stripe;
real-money agent transactions bring custody, disputes, refunds, fraud,
identity, tax/compliance, and payment-platform issues — a different level of
product entirely.

## Try it

```bash
bash demo/agent-exchange-001/quickstart.sh
```

Runs the full loop end to end (about a minute), then renders the
developer-preview UI to `demo/agent-exchange-001/ui/index.html`. Open that
file in a browser: buyer and seller views, the exchange timeline, listings,
transaction history, receipts, and the revocation record. Every screen
carries the preview banner.

The narrated run:

1. **Need** — buyer-agent posts "digest report of a field note".
2. **Find** — the broker searches the registry, ranks two in-budget sellers.
3. **Contact / Offer** — buyer-agent contacts Seller B; Seller B proposes its offer.
4. **Authorize** — the owner already delegated a bounded budget, so the
   Wallet lets the commission proceed; the agreement freezes and 40 SIM_USD
   is reserved.
5. **Work / Verify / Accept** — Seller B works, submits, and the
   buyer-controlled receiver verifies the result exactly.
6. **Settle / Receipt** — 40 SIM_USD settles exactly once; the signed receipt
   survives.
7. **Refuse path** — a second need; the seller works on the wrong input, the
   receiver rejects it: no payment, reservation released.
8. **Agent swap** — the owner revokes buyer-agent and delegates buyer-agent-b.
   The old agent's new commissions are refused; the new agent starts with a
   clean allowance and completes its own deal.
9. **Seller revocation** — Seller C's listing is revoked; selection refuses it
   before any funds move; existing receipts survive untouched.
10. **Reconcile** — read-only reconciliation: reservation audit BALANCED.

## The extension surface — replace any of these

The kernel is five interfaces, each with a minimal reference implementation.
The invitation is literal: **replace any of them**.

| Interface | Reference | Replace with |
|---|---|---|
| `Transport` | `LocalMailboxTransport` (file-backed JSONL mailboxes) | An AX adapter, an MCP transport, A2A, HTTP — AX/MCP/A2A handle agents *talking*; OpenLine handles whether talking turns into authority, installation, work, or payment |
| `Registry` | `FileRegistry` (JSON file) | A public registry, an enterprise catalog |
| `Matcher` | `KeywordMatcher` (keyword overlap + budget) | Any ranking, bidding, or negotiation logic |
| `Receiver` | `CommissionReceiver` (the frozen commission verify) | Any buyer-controlled acceptance rule |
| `Settlement` | `CommissionSettlement` (exactly-once SIM_USD) | Any consequence the buyer accepts — including, one day, real rails with real custody |

Interfaces live in `exchange/interfaces.py`; the contract is frozen in
`CONTRACTS.md`. We do not predict which communication protocol wins, so we
do not own that layer.

## What it is built on

Everything consequential reuses the frozen commission machinery from
`demo/commission-work-001` (the PR #50 line): the deterministic
request-identity transaction, idempotent verify, truthful PENDING-release
reconcile, read-only reconcile, exactly-once settlement. This preview adds
no ledger and no new money rules. On this branch the commission CLI grew
three small multi-identity options (`init-identity`, `--as`, `--as-agent`,
`--to`, `--of`) so several sellers and a second agent can act; every default
is unchanged and the full commission regression suite still passes.

## Tests

```bash
cd demo/agent-exchange-001
PYTHONPATH=.:../../src python -m unittest discover -s tests -t .
```

40 tests: transport round-trips, registry search/revoke, matcher ranking,
and the kernel loop — happy path settles exactly once, interrupted
commission retries converge to one job and one reservation, interrupted
verify reconciles to PENDING then completes exactly one release, refusal
pays nothing, agent swap leaks no authority, revoked listings refuse,
receipts survive, reconcile is read-only.

## The developer invitation

Now imagine this with thousands of agents, public registries, competing
skill markets, different model providers, and real economic activity.

We didn't build the market.

We built the part that lets you build one without giving the market the
keys.

Build one small open exchange reference implementation that other people
can steal from: buyer agent says "I need X", the registry returns three
sellers, the buyer selects one, agents communicate, the Wallet authorizes
the commission, the seller performs the work, the buyer-owned receiver
accepts or refuses, settlement occurs once, the receipt survives, the buyer
agent can be swapped without leaking authority, and a revoked seller stops
being usable. Then put the interfaces in public and tell developers to
replace any of them.

## Claim ceiling

See `CLAIMS.md`. Short version: demonstrated = the local loop above, under
the trusted-operator simulation, positive-only simulated prices, one host.
Descriptive = the architecture the loop demonstrates. Future possibility =
everything about real markets, real money, and real adoption — not built,
not claimed.
