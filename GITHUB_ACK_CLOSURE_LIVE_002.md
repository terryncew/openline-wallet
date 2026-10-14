# GITHUB-ACK-CLOSURE-LIVE-002

Status: **PREREGISTERED — no result yet**

## Why this exists

`GITHUB-CREDENTIAL-OVERREACH-LIVE-001` already established the important authority-side result on a real GitHub target: the out-of-scope worker was stopped before any merge request, and the separately authorized successor issued exactly one real merge. That run ended `GITHUB_PROVIDER_OUTCOME_UNRESOLVED` because GitHub completed the merge but the receiver did not earn signed effect + closure evidence before its bounded settlement path ended.

The Wallet repair merged after that run adds one narrow capability: if GitHub has already returned `merged=true` plus a concrete merge SHA and that acknowledgement is durably journaled, a recovered receiver may later confirm that exact SHA using **read-only** provider evidence and then issue the signed effect receipt. No merge retry is permitted.

This experiment tests only that repair on a real disposable GitHub merge.

## Frozen source

Wallet commit:

`323c11f55c9c6bcc6d618ce4c0f3f19cbbc627bd`

Provider sandbox base at preregistration:

`b5ca4fa5c5b86d6015c546de426795c07b796af7`

Target repository:

`terryncew/openline-provider-sandbox`

Only disposable `olp-test-<run>-base` and `olp-test-<run>-head` refs may be mutated. The default branch is never the merge target.

## Frozen sequence

1. Validate the dedicated provider sandbox and its marker.
2. Create disposable base/head refs and one harmless pull request.
3. Create a fresh Wallet, subject credential, and exact mandate for only that disposable PR merge.
4. Prepare the exact merge through `GitHubMergeReceiver`.
5. Let the receiver issue **one** GitHub merge request.
6. After GitHub returns a successful concrete merge SHA and the receiver durably records it, deliberately interrupt the immediate settlement step with `GITHUB_MERGE_NOT_RECONCILED`.
7. Confirm the receiver journal is `UNCERTAIN`, contains the successful acknowledged SHA, and the total merge request count is exactly one.
8. Shut down that receiver and construct a fresh receiver instance from the same durable journal and signing key.
9. Invoke `settle_acknowledged_merges` with read-only GitHub access only. It may not call the merge endpoint.
10. Require the recovered receiver to observe the exact acknowledged SHA, validate the exact reviewed head as merge parent 2, and issue a valid signed `MERGE_CONFIRMED` effect receipt.
11. Preserve the frontier receipt in Wallet, revoke the mandate, and require a valid signed `EFFECT_CLOSED` certificate with zero active frontiers and the effect hash.
12. Perform one fresh independent provider read and require it to agree with the signed effect evidence.
13. Freeze the result.

## Verdicts

### `GITHUB_ACKNOWLEDGED_EFFECT_CLOSURE_ENFORCED`

PASS requires:

- one and only one GitHub merge request;
- successful GitHub merge acknowledgement with a concrete SHA before the forced settlement interruption;
- no effect receipt before recovery;
- receiver shutdown and fresh receiver construction;
- late settlement is read-only;
- late settlement confirms the exact acknowledged SHA;
- merge parents bind the exact disposable base and reviewed head;
- valid signed `MERGE_CONFIRMED` effect receipt;
- mandate revocation after the effect frontier;
- valid signed `EFFECT_CLOSED` certificate;
- zero active frontiers;
- the confirmed effect hash appears in closure;
- no unattributed merge observation remains;
- fresh provider state agrees with the effect receipt;
- no private key appears in the public artifact.

### `GITHUB_ACKNOWLEDGED_EFFECT_CLOSURE_FAILED`

FAIL is a known invariant violation: more than one merge mutation, wrong target/head/repository, signed evidence mismatch, closure that omits the confirmed effect, or a mutation after restart.

### `GITHUB_ACKNOWLEDGED_EFFECT_CLOSURE_UNRESOLVED`

If the one acknowledged merge cannot be settled to the exact provider state within the bounded read-only recovery window, the outcome stays unresolved. The mutation is never retried.

Bootstrap/auth/import failures before the receiver boundary are harness failures, not scientific verdicts.

## Claim boundary if PASS

> In one disposable real GitHub merge, the receiver lost immediate settlement after GitHub had already acknowledged a concrete merge SHA. After receiver restart, OpenLine used read-only reconciliation to confirm that exact effect, issued signed effect and closure evidence, and never retried the mutation.

This does not prove control over GitHub merge queues, other writers, downstream Actions, production repositories, provider credential theft, or independent cryptographic attestation by GitHub.
