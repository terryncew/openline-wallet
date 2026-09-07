# WALLET-EFFECT-CLOSURE-001

## Question and frozen falsifier

Triggered by the September 2 paper *When Does Authorization End? Effect Closure at Provider Boundaries* (EFFECTBOUND). Its provider-boundary work is acknowledged; this experiment does not claim that effect closure is novel.

An action admitted under valid grant G is held before its effect. G is revoked, the receiver reports revocation effective, and the action is released. One now-rejected effect after that report falsifies closure. Work already past its irreversible frontier is a different case and must be accounted for before closure is reported.

## Pinned baseline and negative result

Base: `9df63798c0f520e9d6c8dc85dd293ebdeaa9c11f` (Wallet v0.1). The exact original `gate_http.py` is preserved in `proofs/wallet-effect-closure-001/baseline_gate_http.py`, Git blob `192006de121e9ee02f7384861e71a0011607735f`. Its hash is checked before loading. The original Wallet and ReferenceGate cryptographic implementations are used.

The negative control holds the original ledger writer after an ALLOWED Gate decision. A newer, authentic revoked bundle is admitted. Releasing the held writer still appends one effect. This establishes the upstream admission gap. The original API did not actually issue an effect-closure certificate, so this is a falsification of the stronger interpretation of its boundary, not a claim that its documented v0.1 contract was false.

## Repair and linearization boundary

The local staging receiver now separates preparation from the final effect decision. Preparation consumes the original one-use holder challenge and stores the signed admission, but cannot authorize the eventual effect. The receiver revalidates that exact grant against its newest admitted standing inside the same lock that protects the real ledger write. Revocation admission, frontier execution, and closure use one serialization domain. A callback already crossing the frontier finishes before closure may be signed. A callback waiting outside the frontier must recheck and stop after revocation.

`POST /close` accepts an authentic Wallet bundle and exact revoked mandate ID. It returns a signed `EFFECT_CLOSED` certificate only after the local lock establishes zero active frontiers and all matching pending tickets have lost standing. An ordinary `/admit` response explicitly says `AUTHORIZATION_ADMITTED_ONLY`; it is not a closure claim. The original v1 Wallet decision receipt schema and existing MCP response fields are preserved. The final receipt, rather than the earlier admission, is imported into Wallet history. Additional signed evidence binds the admission, frontier verdict, exact effect ID and actual effect result.

A single receiver-owned writer lock protects the local ledger. A durable intent is recorded before the effect callback. A crash, failed acknowledgement, corrupt ledger, or unresolved intent seals the receiver; restart does not silently clear uncertainty. Exact ticket replay is idempotent within the process. This is fail-closed recovery, not a completed crash-reconciliation or exactly-once distributed protocol.

## Local result

The pinned baseline reproduced one late effect. The repaired hold–revoke–close–release path produced zero effects, a signed closure, and a final signed `STOPPED / MANDATE_REVOKED` receipt. The receiver-local result is `LOCAL_EFFECT_CLOSURE_ENFORCED`. The self-attested experiment receipt and source hashes are in `proofs/wallet-effect-closure-001/result.json`. `SHA256SUMS.txt` covers all captured evidence. The disposable signing key is not an independent witness.

The exact patch was rerun locally: 14 focused tests passed, the evidence verifier passed, and the reproduction passed. The complete upstream checkout was unavailable in this runtime, so the full upstream suite and downstream GitHub CI remain unverified.

The focused suite covers the negative control, real HTTP race, frontier drain ordering, successor grants, narrowing, expiry, replay, forged admissions, rollback, single-writer ownership, corrupted storage and crash uncertainty. It does not replace the upstream full test suite.

## Run

From the repository root after installation:

```bash
python -m unittest discover -s tests -v
python proofs/wallet-effect-closure-001/reproduce.py --output effect-closure-artifacts
python proofs/wallet-effect-closure-001/verify.py effect-closure-artifacts
python -m openline_wallet demo-platform-exit --output platform-exit-artifacts
python -m openline_wallet verify platform-exit-artifacts/wallet-after.olw
python -m pip wheel . --no-deps --wheel-dir dist
```

Use a fresh empty output directory. The reproduction generates only disposable keys. CI runs the experiment and verifier on Python 3.11–3.13, uploads diagnostics even if its step fails, then preserves the original platform-exit and wheel stages. A failure is not permission to weaken the invariant or skip downstream checks.

## Earned boundary and remaining work

This proves local serialization for one trusted receiver-owned staging ledger and cooperating adapter. It does not establish closure for GitHub, Kubernetes, Kafka, NATS, arbitrary provider queues, multiple receivers, crashed external services, production deployments, or irreversible work outside the adapter. A provider integration must identify its real effect frontier and either fence it or prove all capable in-flight work has drained. No universal effect-closure or revised WALLET-004 claim is earned here.

The original WALLET-004 transport result remains a propagation result. Its prior evidence is not rewritten. This experiment is a separate local effect-closure extension. The next cross-system proof must measure propagation and frontier closure independently, including uncertain or unreachable receivers.

Remote status: the connected GitHub write endpoint returned 403 during branch creation, so no remote commit or new CI run has been created. This package is a locally tested candidate. The source baseline is pinned; do not merge over a changed main without reviewing the delta.
