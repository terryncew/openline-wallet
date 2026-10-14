# GITHUB-CREDENTIAL-OVERREACH-LIVE-001 — valid worker credential, real GitHub consequence

Status: **PREREGISTERED — no result yet**

## Why this experiment exists

`CREDENTIAL-OVERREACH-LIVE-001` earned the receiver-side scope claim in a bounded localhost fixture: a process held a current valid worker credential, requested an action outside its owner-approved mandate, and the Receiver returned a signed `STOPPED / ACTION_OUTSIDE_MANDATE` receipt with no effect.

Separately, `PROVIDER-EFFECT-LIVE-001` observed a real GitHub merge, but its original live run did not earn complete signed effect closure. The current Wallet implementation now contains the bounded read-only merge settlement repair and signed GitHub effect/closure machinery.

This experiment composes only those two boundaries. It does not add another model-behavior test.

## Fresh question

> Does a valid worker identity remain narrow when the consequence is a real GitHub mutation, and can a separately authorized successor then perform that exact mutation with signed effect and closure evidence?

## Frozen target

Only `terryncew/openline-provider-sandbox` may be touched.

The launcher must verify the repository identity and `SANDBOX.json` marker before creating disposable `olp-test-<run>-base` and `olp-test-<run>-head` branches and one harmless pull request. The default branch is never a mutation target.

The provider mutation is one merge of that disposable PR. No production repository, money, deployment, or external user data is involved.

Wallet source is pinned to:

`ebe6b2882095aba64611f5802ad5fdcf8e65961a`

## Frozen sequence

1. Validate the dedicated sandbox repository and marker.
2. Create disposable base/head refs from sandbox `main`, write one harmless marker file to the disposable head, and open one disposable PR.
3. Read until the exact PR/head/base target is mergeable.
4. Create a fresh owner Wallet.
5. Give Worker A a valid current subject credential and active mandate that **does not** contain the exact GitHub merge action.
6. Worker A signs a holder presentation for the exact merge action and submits it to a `GitHubMergeReceiver`.
7. The Receiver must return exactly `STOPPED / ACTION_OUTSIDE_MANDATE`.
8. At that point there must have been **zero GitHub merge requests** and the disposable PR must still be unmerged.
9. Preserve the signed STOP receipt in Wallet history and revoke Worker A.
10. Create a fresh Worker B credential and a new owner mandate containing exactly the disposable PR merge action.
11. Worker B presents that exact authority to a fresh receiver.
12. The receiver may issue exactly one GitHub merge mutation. No retry is permitted.
13. GitHub must settle to the exact reviewed head and a two-parent merge commit with the reviewed head as parent 2.
14. The receiver must produce a valid signed effect receipt bound to that merge commit.
15. Preserve the frontier decision receipt in Wallet history and revoke Worker B.
16. The receiver must produce a valid signed `EFFECT_CLOSED` certificate with zero active frontiers and the confirmed effect hash.
17. Fresh provider reads must agree that the exact disposable PR merged to the exact effect-receipt commit.
18. Freeze the result.

## Verdicts

### `GITHUB_CREDENTIAL_OVERREACH_CONTAINMENT_ENFORCED`

PASS requires all of the following:

- Worker A used a current valid subject key and active mandate.
- Worker A's mandate did not contain the GitHub merge action.
- Worker A's exact merge presentation received one valid signed `STOPPED / ACTION_OUTSIDE_MANDATE`.
- No GitHub merge request occurred before Worker B.
- Worker A was revoked.
- Worker B used a different subject key and a fresh mandate containing exactly the merge action.
- Exactly one GitHub merge request occurred in the entire receiver phase.
- The merge settled to the exact disposable PR/head/base identity.
- The effect receipt is valid, signed, bound to the exact merge commit, and reports `MERGE_CONFIRMED`.
- Worker B was revoked.
- The closure certificate is valid, signed, reports `EFFECT_CLOSED`, has zero active frontiers, and contains the confirmed effect hash.
- Fresh provider reads agree with the effect evidence.
- No private key appears in the public artifact.

### `GITHUB_CREDENTIAL_OVERREACH_CONTAINMENT_FAILED`

FAIL is a known falsification: the out-of-scope Worker A request reaches the provider mutation frontier, is allowed, causes a merge, lacks the expected signed STOP evidence, or any known final provider/effect/closure binding contradicts the preregistered invariants.

### `GITHUB_PROVIDER_OUTCOME_UNRESOLVED`

If the one permitted GitHub merge request is dispatched but the provider outcome cannot be attributed and closed with the required signed evidence, the result is inconclusive. The mutation is never retried. Read-only reconciliation may record what GitHub ultimately shows, but it cannot upgrade an unattributed merge into a PASS.

A bootstrap, credential, import, or other harness failure before the experiment reaches the receiver boundary is a CI failure, not a scientific verdict.

## Claim boundary if PASS

> In one disposable GitHub fixture, a process holding a current valid worker credential requested a real GitHub merge outside its owner-approved mandate. The receiver rejected it before any merge request. The owner revoked that worker, authorized a different worker for that exact merge, and the receiver performed one GitHub merge with signed effect and closure evidence.

Short rendering:

> The credential was valid. The GitHub action wasn't. Nothing reached GitHub. A separately authorized worker then performed the exact merge, and the receiver closed the effect with proof.

## Nonclaims

No claim that Claude, Codex, or any model attempted the forbidden action. No claim that OpenLine prevents subject-key theft or isolates a malicious same-user process. No claim over GitHub merge queues, other writers, Actions side effects, production branches, production recovery, deployments, payments, or legal liability. The GitHub provider observation is still an API observation, not an independent cryptographic attestation from GitHub.
