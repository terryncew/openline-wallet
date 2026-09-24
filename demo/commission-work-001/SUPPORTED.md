# SUPPORTED.md — commission-work-001

## Supported

- One service (`text_digest`), commissioned by an agent, performed by a
  seller, verified by the buyer's receiver, settled exactly once from a
  pre-reserved simulated allowance.
- Authority boundaries: agent cannot raise its budget, rewrite acceptance,
  change the payee, or authorize itself; each refusal is demonstrated and
  tested.
- Fund safety: reservations before work; parallel jobs cannot double-commit
  one allowance; rejection releases the reservation; exactly-once settlement.
- Revocation semantics: blocks new commissions, never erases an
  already-earned obligation.
- Interruption: read-only reconciliation that names the next step per job.
- Offline verification: every claim in the quickstart is checkable from the
  frozen records with `inspect`, `receipt`, and `status` — no network.

## Reused (not new)

- Identity, hashing, signatures, canonical JSON: `openline_wallet.canonical`,
  `openline_wallet.crypto`
- Delegation mandates and revocation: `openline_wallet.wallet`
- Exactly-once settlement pattern and signed receipts: the
  capability-installer settlement design

## Newly built for this demo

- Frozen agreement record (identities, task/input binding, deliverable,
  receiver-owned acceptance criteria, amount, currency, deadline,
  cancellation/settlement rules)
- Budget reservation ahead of work (parallel jobs cannot double-commit)
- Receiver-owned verification for this one service
- Idempotent event log per job; read-only `reconcile` over durable records

## Explicitly not supported

- No discovery network, no auctions, no reputation scores, no tokens.
- No real payments or public deployment. `SIM_USD (simulated)` everywhere.
- No seller capability import or inheritance: the seller keeps its
  implementation; the buyer gets a result plus a receipt. Nothing installs
  into the buyer's wallet.
- No dispute resolution beyond the frozen cancellation/settlement rules.
- No evidence about external adoption. Separate local identities demonstrate
  an authority boundary, not a market.
