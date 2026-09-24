# commission-work-001: pay an AI seller for one service under frozen rules

An agent commissions a seller, the seller does the work, the buyer's own
acceptance criteria decide whether the result passes, and payment settles
exactly once from a pre-reserved allowance. All money is simulated
(`SIM_USD (simulated)`), all identities are local test principals, and the
whole run takes seconds on one machine.

Run it:

```bash
bash demo/commission-work-001/quickstart.sh
```

Or step by step:

```bash
C="python3 demo/commission-work-001/commission.py"
$C init                                        # mint simulated funds, create identities
$C delegate --caller owner --budget 200        # owner gives its agent a bounded allowance
$C offer --caller seller --price 50            # seller offers one service (text_digest)
printf '{"nonce": "job-1"}\nthe quick brown fox\n' > /tmp/in.txt
$C commission --caller agent --offer offer-text-digest-v1 --input /tmp/in.txt
$C work --caller seller --job <job-id>         # agreement is frozen before work starts
$C submit --caller seller --job <job-id>
$C verify --caller owner --job <job-id>        # buyer's receiver, not the seller
$C settle --caller owner --job <job-id>        # exactly once; receipt saved
```

## What this shows

- The agreement freezes before work: identities, task, input binding,
  deliverable, acceptance criteria, amount, deadline, cancellation rules.
- The agent is weaker than the owner in the rules this preview tests: it
  cannot raise its budget, rewrite acceptance, change the payee, or
  authorize itself. Each attempt is refused. (Trusted-operator simulation:
  on this host the operator holds every private key and chooses
  `--caller`; what is genuinely enforced is signature attribution — every
  consequential record is signed by its authorizing principal and verified
  on every read — plus the owner's mandate gating new commissions.)
- Funds are reserved before work, and budget accounting counts spent plus
  reserved: settled money is gone, so a second job against a spent budget
  is refused (`INSUFFICIENT_ALLOWANCE`). Concurrent commissions serialize
  on a writer lock; racing commissions cannot over-commit one allowance.
- Settlement is atomic and deduplicated. The transfer carries an
  idempotency key checked inside the writer lock; the amount and payee
  come only from the signed agreement, never from mutable job copies. A
  crash after the transfer is written leaves exactly one transfer:
  reconciliation reports the committed state truthfully and a retry
  completes the local records without a second payment. A replay of a
  completed settlement is refused (`ALREADY_SETTLED`).
- Revocation blocks new work but never erases an already-earned obligation:
  an accepted job still settles after the mandate is revoked.
- An interrupted run reconciles read-only: `reconcile` reads the durable
  records, changes nothing, and names the next step for each job.

## Limits (read before believing anything)

- Simulated money only. `SIM_USD (simulated)` has no value and no
  connection to any real payment system.
- Local-only. The owner, agent, and seller are separate identities on one
  machine. This demonstrates an authority boundary, not external adoption
  and not a functioning market.
- Trusted-operator simulation. The three identities have separate keys, but
  one operator holds all of them on this host and chooses `--caller`. The
  preview demonstrates and tests the authority rules — refusals, mandates,
  signature attribution, exactly-once settlement — but it does not
  establish key-custody separation between the agent and the owner.
- One service. The seller runs one deterministic local function
  (`text_digest`: word/line counts plus a SHA-256 of the input). The
  buyer's receiver recomputes every field from the frozen input.
- No discovery, auctions, reputation, tokens, or dispute resolution. If a
  substantial missing primitive blocked this one transaction, the demo
  would have stopped there; none did.

## Files

- `commission.py` — the demo CLI (buyer/agent/seller/receiver roles)
- `seller_impl/text_digest.py` — the one commissioned service
- `quickstart.sh` — the full run: happy path, rejections, refusals,
  revocation, funds reservation, tamper, and interruption
- `SUPPORTED.md` — what is supported here vs explicitly not
