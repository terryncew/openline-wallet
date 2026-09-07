# PROVIDER-EFFECT-LIVE-001 — Run 7 reappraisal and reconciliation repair

Base: `96056fe1b0fbf1c781626122ef6c6bafc2085757`. Its tree matches the
original provider implementation at `c35e7f4`. The original experiment,
preregistration, source pin, and frozen receipts are not rewritten.

## The result we actually earned

Run 34163238256 created disposable PR #9 and issued one real GitHub merge.
The provider returned success with merge SHA
`826dccd653f35743f4b0e25a02ed3c6c8ed321e0`. Independent public GitHub
reads confirm that PR #9 is merged, that the commit has the exact initial
base and reviewed head as its two parents, and that the disposable base branch
points to that merge commit.

The original signed result remains **INCONCLUSIVE**. The pre-dispatch arm
stopped without a merge; the held-acknowledgement arm observed the successful
provider response, revoked the mandate, and held closure until after the
acknowledgement was released. Its receiver then failed to reconcile because
GitHub's immediate PR GET returned `merged=true` with a null merge commit SHA.
The subsequent recovery tried to validate that missing SHA and remained
uncertain. Arm B has no signed effect receipt or signed closure certificate.

The reappraised conclusion is
`LIVE_GITHUB_MERGE_OBSERVED_CLOSURE_UNRESOLVED`. This is a genuine external
mutation and a useful negative result. It is not a complete live effect-closure
proof. Do not change the original verdict to green or reconstruct a receiver
certificate using a new key.

## The smallest repair

`github_effect.py` now performs bounded, read-only reconciliation after a
successful merge response. It waits for the terminal PR to expose a valid
merge commit SHA, binds that SHA to the accepted response when available, and
checks the commit's two parents. Recovery uses the same bounded read path.
A missing value, contradictory SHA, wrong parent, timeout, or failed read
remains uncertain. No merge request is retried. The original effect lock,
durable intent, revocation gate, and signed receipt schemas are unchanged.

The regression suite includes a replay of run 7's recorded null-SHA response,
delayed visibility, permanent missing data, wrong SHA, wrong parents, and
read-only recovery. The 25 existing provider tests and seven new tests pass
locally. The complete upstream CI matrix and a live run of the repaired
implementation have not yet been performed.

## Evidence and claim boundaries

`proofs/provider-effect-live-001/attempt-7-original.zip` is the unchanged
GitHub artifact. `reappraisal.json` records independent public observations
and exact source/target identities. The artifact's outer and inner hashes,
signed result, Gate records, revoked Wallet history, and timing were checked.
The external observations are public API reads, not an independent
cryptographic provider witness.

The earlier historical source-provenance limitation remains recorded. This
repair does not alter WALLET-004, WALLET-EFFECT-CLOSURE-001, WALLET-CLOSURE-SET-001,
or the frozen controlled PROVIDER-EFFECT-001 result.

## Stop condition

Do not rerun attempt 7 or reuse PR #9. No new live mutation is needed to
establish that its merge happened. A future full closure claim would require
the repaired source to pass downstream CI and a fresh disposable run to
produce both signed Arm B receipts and externally reconciled terminal state.
Until then, retain the narrower observed-merge result. Do not weaken the Gate
or create another infrastructure layer just to obtain a green workflow.
