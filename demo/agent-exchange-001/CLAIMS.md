# CLAIMS.md — OpenLine Exchange Preview

Every substantive claim in this preview, categorized. The rule: a claim is
DEMONSTRATED only if the quickstart run shows it; DESCRIPTIVE only if the
architecture exhibits it; everything about real markets is FUTURE
POSSIBILITY and is not claimed.

## DEMONSTRATED (by `bash quickstart.sh`, all local, all simulated)

- One buyer agent posts a need; a broker searches a registry and ranks
  candidates by keyword overlap within budget. (`FIND` step; test:
  `test_matcher_excludes_over_budget_and_revoked`)
- Agents exchange messages through a pluggable `Transport` interface; the
  reference implementation is file-backed JSONL mailboxes, no network.
  (test: `test_transport.py`, 12 tests)
- The buyer's Wallet authorizes the commission: no active mandate, no new
  work (`MANDATE_REVOKED`); the agreement freezes before work with amount
  and payee bound to the agent's signature.
- The seller works with its own retained implementation and submits a
  signed result; the buyer receives the result, not the implementation.
- The buyer-controlled receiver verifies the result by exact recompute
  from the frozen input; accepted work settles **exactly once** in SIM_USD
  (one transfer per `settle:<agreement_hash>`); rejected work pays nothing
  and the reservation is released. (tests: `test_happy_path_settles_exactly_once`,
  `test_refused_work_pays_nothing_and_releases`)
- Interrupted commission retried with the identical request recovers the
  same job with exactly one reservation — never two, never a lost
  agreement. (test: `test_interrupted_commission_retries_to_one_job_one_reservation`)
- Interrupted verify (verdict committed, release incomplete) reconciles to
  release PENDING with the recovery command named; repeated verify
  completes exactly one release; reconcile is read-only. (tests:
  `test_interrupted_verify_reports_pending_then_releases_once`,
  `test_reconcile_is_read_only`)
- Agent swap: the revoked buyer agent's new commissions are refused; the
  new agent starts with a clean allowance and completes its own deal; the
  old agent's books are untouched. (test: `test_agent_swap_does_not_leak_authority`)
- Seller revocation: the listing is refused at selection before any funds
  move; existing receipts survive byte-identical; the registry no longer
  surfaces the listing. (test: `test_revoked_listing_refuses_and_receipts_survive`)
- Reservation audit BALANCED: committed reserved totals equal identifiable
  outstanding commitments.

Scope of every demonstrated claim: trusted-operator simulation (the
operator holds every key and chooses `--caller`), positive-only simulated
prices (zero-price not supported), one service (`text_digest`), one host,
SIM_USD (simulated) — no real money moved, no real counterparty involved.

## DESCRIPTIVE ARCHITECTURE (what the loop exhibits, not what it proves)

- The transport carries talk; the kernel decides whether talk becomes
  authority, installation, work, or payment.
- Producer proposes, receiver decides the consequence; selection (matcher)
  and runtime acceptance (receiver) are separate decisions.
- Buyer-controlled rules sit downstream of discovery: the registry can be
  replaced without touching the acceptance boundary.
- The five interfaces (Transport, Registry, Matcher, Receiver, Settlement)
  are independently replaceable; the reference implementations are the
  thinnest credible ones.

## FUTURE POSSIBILITY (not built, not demonstrated, not claimed)

- A functioning public marketplace; autonomous capability discovery;
  real-money payments; external adoption; agent-economy equilibrium;
  "BUY beats BUILD"; independent third-party marketplace demand;
  reputation predicting seller quality; liquidity; transaction-fee business
  models; thousands of agents; competing skill markets; real economic
  activity.
- None of the above is shown, implied by the UI, or promised by the docs.
  The UI banner and this file exist to keep that line visible.

## Deliberately not built

Discovery network, reputation system, token, auction mechanism, universal
agent-messaging standard, real-money payment integration. These are areas
where others will throw enormous engineering; the kernel is the control
boundary such systems need, not a competitor to them.
