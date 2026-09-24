# SUPPORTED.md — commission-work-001

## Supported

- One service (`text_digest`), commissioned by an agent, performed by a
  seller, verified by the buyer's receiver, settled exactly once from a
  pre-reserved simulated allowance.
- Authority rules, demonstrated and tested: the agent cannot raise its
  budget, rewrite acceptance, change the payee, or authorize itself; each
  refusal is a command-layer check. Every consequential record is signed
  by its authorizing principal (agent: agreement, buyer side: verdict,
  seller: result, owner: receipt) and every signature is verified on
  every read, so the signed body is the sole source of truth. The owner's
  wallet mandate genuinely gates new agent commissions.
- Fund safety: reservations before work; budget accounting counts spent
  plus reserved; concurrent commissions serialize on a writer lock so
  racing jobs cannot over-commit one allowance; rejection releases the
  reservation; exactly-once settlement.
- Atomic, deduplicated settlement: the transfer carries an idempotency
  key checked inside the writer lock; amount and payee come only from
  the authenticated agreement. A crash after the transfer is written
  leaves one transfer; reconciliation reports the committed state
  truthfully and a retry completes the local records, never a second
  payment.
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
- No key-custody separation between agent and owner. This is a
  trusted-operator simulation: the operator holds every private key on
  this host and chooses `--caller`. The authority boundary is
  demonstrated through tested rules, signature attribution, and mandate
  gating — not through separate custody of keys.
- No evidence about external adoption. Separate local identities demonstrate
  an authority boundary, not a market.
