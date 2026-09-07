# PROVIDER-EFFECT-001 — GitHub merge boundary

Base: `ec385ce2c0cdc253d5e634d1963f254203205e30` (merged WALLET-CLOSURE-SET-001). The earlier proofs and receipts remain unchanged.

## What this builds

A narrow receiver adapter for one GitHub pull request merge. The Wallet signs the permission history, the receiver keeps the provider credential, and the Gate rechecks the original exact grant before the request. The receiver persists an intent before its one permitted merge request, checks GitHub's response against the actual merged PR and merge commit parents, and signs separate effect evidence. No new policy engine or global coordinator is introduced.

The action binds the canonical repository name, immutable repository ID, PR number, reviewed head SHA, and base branch name. The initial base SHA is recorded and checked during preflight. GitHub's synchronous merge endpoint supports a conditional PR head SHA but no conditional base SHA. Therefore the action deliberately does not promise an exact-base compare-and-swap. If the base advances during the request, the resulting commit's first parent is recorded as base drift. A separate repository-side fence would be needed for an exact-base guarantee.

The receiver uses a single-writer SQLite journal with synchronous durable commits, signed state records, a one-use target, and a permanent closure seal. Prepared tickets are durably recorded. An in-flight request is never automatically retried. A restart does not restore execution capability; it may only reconcile or fence work that was never dispatched. Missing acknowledgements remain unattributed, and a negative read cannot clear an uncertain request. A terminal exact PR merge can establish that this one PR cannot be merged again; it cannot prove who performed the merge if the acknowledgement was lost.

## Controlled result

The offline experiment runs an explicitly unsafe admission-only control and the repaired adapter through a real loopback HTTP server and JSON transport. The unsafe control admits, holds before the PUT, revokes, then allows the old action to merge. The repaired arm closes before a held action can reach GitHub. A second arm executes the merge, holds its successful HTTP acknowledgement, revokes, attempts closure, and proves closure waits until the receiver reconciles the terminal result. It checks exact head and merge parents, one mutation, no retry, signed receipts, and preserved authority history.

The result is `CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED`. The fixture is not GitHub, and this is not a live provider result. The full upstream test suite and remote CI must run on the pushed branch. The previous frozen receipts are not modified or rerun for novelty.

## Real disposable run

The live runner is included as `python -m openline_wallet.github_effect_live`. It has three commands: `inspect`, `run`, and `recover`. It never creates a repository or silently chooses a PR. The operator must provide an isolated test repository with an existing PR targeting an unprotected branch whose name begins with `olp-test-`. The default branch is refused. The PR must be open, non-draft, and mergeable. The repository must be controlled for the experiment; no other actor or automation should be able to mutate the test PR or base during the run.

GitHub's merge endpoint requires repository Contents write permission. Use a short-lived, repository-scoped credential supplied through `OPENLINE_GITHUB_TOKEN`; do not paste it into a chat, commit it, or include it in evidence. The reference runner deliberately stores disposable identity, holder, and Gate keys in a separate private state directory, not in the public evidence packet. Those keys are not production key custody.

First inspect the exact test target without mutation:

```bash
python -m openline_wallet.github_effect_live inspect \
  --repository OWNER/DISPOSABLE-REPO --pr 7 --output target.json
```

Review the returned action hash, immutable repository ID, head SHA, base branch, and base SHA. The second command requires explicit values from that file; a stale target is refused before creating an effect:

```bash
python -m openline_wallet.github_effect_live run \
  --target target.json --state ./private-provider-state \
  --output ./provider-live-evidence \
  --confirm-repository OWNER/DISPOSABLE-REPO --confirm-pr 7 \
  --confirm-action github:merge:EXACT_ACTION_HASH --allow-merge
```

The runner first performs a no-mutation revocation control, then issues a fresh disposable grant for the actual merge. It holds the successful GitHub response while the receiver retains the effect lock, revokes the grant, and verifies that closure waits for reconciliation. The actual provider may complete too quickly to establish a server-side held-request race. The test therefore earns only the observable held-acknowledgement boundary. It does not test cancellation of GitHub's internal queue or downstream workflows.

If a request times out or returns an ambiguous error, no automatic retry is made. Preserve the private state and public diagnostics. After the original process is stopped, use the recovery command with the same state and credential:

```bash
python -m openline_wallet.github_effect_live recover \
  --state ./private-provider-state --output ./provider-recovery-evidence
```

Recovery may revoke remaining disposable test grants, seal never-dispatched preparations, and perform read-only GitHub reconciliation. It never calls the merge endpoint. An unresolved result remains unresolved. Do not delete a journal, create a new Gate, or replay the original request to make the experiment pass.

## CI and evidence

The normal CI matrix runs the complete suite, the earlier closure checks, this controlled HTTP reproduction and verifier, then the original platform-exit and wheel stages on Python 3.11–3.13. Diagnostics upload on failure. The real mutation is deliberately excluded from automatic PR and push CI because it requires an explicitly selected disposable repository and an authorized credential. The `run` command's output is a candidate live observation; it must be inspected and frozen separately after an actual run. Its self-attestation is not independent GitHub evidence.

The controlled verifier checks source hashes, all evidence files, signed Wallet and Gate records, the unsafe negative control, the exact merged head and commit parents, closure ordering, and one-mutation history. It does not convert a fixture result into a live claim. No secrets are included in the root-ready patch ZIP or the controlled evidence packet.

## Limits and next gate

This proves a receiver-controlled capability for one exact PR/head/base branch, under trusted local key/journal custody and one local writer. It does not prove arbitrary provider effect closure, exact-base atomicity, durable production standing, Byzantine provider honesty, merge-queue cancellation, GitHub Actions completion, deployment safety, or that no other GitHub credential can merge the PR. A signed certificate is evidence only and cannot authorize a new effect.

The next earned evidence is an actual disposable GitHub run. If that produces a late rejected effect, an unresolved remote outcome, or a base-drift failure, preserve it and implement only the smallest provider-supported repair. Do not create another synthetic coordination layer or claim global closure from this adapter.

## CI source-provenance correction

The first PR #11 runs passed the complete 76-test suite but stopped at the
historical verifier. The previously prepared closure-set source packet differs
from the exact merged Git tree, including canonical.py. The experiment's base
commit identifies its starting point; it does not establish that every staged
source byte was committed at that revision.

The historical reappraisal now uses a byte-pinned recorded-source snapshot,
checks all 14 source hashes from the unchanged frozen result, and runs the
original evidence verifiers in an isolated subprocess. The result is pinned
independently. It reports Git-tree source identity as UNRESOLVED and cannot
promote the old result into a live, production, or source-provenance claim.
The archived packet contains no signing private keys. The current application
and original receipts remain unchanged.

CI retains the full current test suite and replaces redundant historical
reproductions with this exact evidence reappraisal. It then runs the new
provider proof, platform-exit demo, and wheel build. Historical source or
record tampering is a hard failure. This correction does not establish that
the old staged source was the original main-branch implementation.
