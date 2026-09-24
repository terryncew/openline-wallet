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
- The agent is weaker than the owner: it cannot raise its budget, rewrite
  acceptance, change the payee, or authorize itself. Each attempt is refused.
- Funds are reserved before work. Parallel jobs cannot commit the same
  balance twice. Rejection releases the reservation; nothing pays.
- Settlement is exactly once. A replay is refused (`ALREADY_SETTLED`).
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
