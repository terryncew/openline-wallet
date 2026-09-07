# WALLET-CLOSURE-SET-001

## The earned boundary

Base: `a8633858a04d33c0e2930214d7c93105f67a3398` (the merged WALLET-EFFECT-CLOSURE-001 commit). This extension does not alter the frozen WALLET-004 or EFFECT-CLOSURE-001 receipts.

The new test asks whether a receiver-local closure may be promoted into a claim about a fixed set of receivers. The answer must remain incomplete until every named receiver provides its own valid closure. A quorum, propagation acknowledgement, auditor signature, or expired report cannot stand in for a missing effect frontier.

The September 2 EFFECTBOUND paper is the external trigger for this work. Its treatment of provider-boundary effect closure is acknowledged; this experiment does not claim novelty for that concept.

## Protocol and implementation

`src/openline_wallet/closure_set.py` adds a small evidence-only layer. The principal signs an exact, sorted membership of receiver IDs and pinned public keys. A second principal-signed request binds that membership, the exact revoked grant, the complete Wallet bundle hash, the revocation event, the current head, an expiry, and a nonce. Membership is immutable for the request. There is no quorum setting or automatic member replacement.

Each receiver calls its existing `EffectClosure.close` under the same lock used by the actual staging-ledger frontier. It then signs a fresh witness containing the original local closure and the exact request hash. The timestamp is sampled after acquiring that lock, so waiting for an in-flight effect cannot backdate the closure. The verifier checks every member's signature, identity, scope, grant, head, timestamp, and zero active-frontier claim. Missing, duplicate, forged, mismatched, or unknown evidence leaves the set incomplete.

The optional auditor report is recomputable from the signed receiver evidence. Its signature records provenance only. Neither the Wallet, relay, nor auditor acquires authority to execute an effect. Existing v1 Wallet receipts and local closure schemas remain unchanged.

## Discriminating experiment

Three isolated processes own separate Gate keys, private receiver state, and staging ledgers. The test harness acts as an untrusted relay. It can withhold, reorder, duplicate, and counterfeit messages, but does not receive the principal's root key, the holder private key, or any receiver private key. The multiprocessing control pipe is test-only and is not a public protocol endpoint.

The test performs an ordinary effect before revocation, prepares two actions that remain upstream of their frontiers, and holds a third action inside the final ledger writer. The user revokes the grant. Two receivers close, but the set remains incomplete. The third closure request waits until the in-flight effect has actually finished. Only then can the third signed witness complete the set. Releasing the two upstream actions after closure produces no effects. The earlier effects remain part of history.

The negative controls include a missing member, a forged signature, duplicate evidence, an extra member, a replay under a fresh request nonce, and an unreachable member. The focused unit tests also cover validly re-signed wrong scopes/heads/mandates, expiry, malformed membership, auditor promotion, local uncertainty, and the clock-sampling race.

## Run and verify

From a complete checkout of the pinned base with the patch installed:

```bash
python -m pip install -e ".[mcp]"
python -m unittest discover -s tests -v
python proofs/wallet-closure-set-001/reproduce.py --output closure-set-artifacts
python proofs/wallet-closure-set-001/verify.py closure-set-artifacts
```

Use a fresh output directory. The reproduction creates only disposable keys and local staging-ledger effects. A failure preserves a partial trace and receiver diagnostics, including unresolved intents when present, and emits `INCONCLUSIVE`. It cannot issue a successful receipt from a failed run. Successful evidence is signed with a disposable auditor key and bound to exact source hashes; the signing key is not an independent witness.

CI runs the complete test suite, the previous effect-closure reproduction and verifier, this new reproduction and verifier, uploads diagnostic artifacts even on failure, then retains the original platform-exit and wheel stages on Python 3.11–3.13. If CI fails, inspect both push and PR runs for the same commit, preserve the first failure and all later steps, and run the full downstream path after a root-cause fix. Do not weaken a closure assertion to obtain green CI.

## Limits and falsifiers

The result establishes a fixed-set conjunction of three cooperating receiver-local staging-ledger closures. It does not establish that the membership contains every receiver in the world, nor does it prove effect closure for GitHub, Kubernetes, Kafka, NATS, or other external provider queues. No production key custody, durable Gate restart recovery, multi-machine clock integrity, receiver-key rotation, Byzantine receiver honesty, or arbitrary external irreversible effect is claimed.

A signed set report is historical evidence for one exact request. It expires for live appraisal and never authorizes later effects. A fresh request requires fresh receiver witnesses. The verifier's explicit historical-time parameter is for offline evidence checking, not an untrusted transport parameter. A real integration must also establish durable receiver standing, authenticated transport, and a real provider-side effect frontier before claiming operational closure.

The falsifier is one now-rejected effect from a named receiver after a valid set closure for that grant, within the stated local execution assumptions. A missing or uncertain receiver being counted as closed is also a direct protocol falsification. A test failure is preserved as a negative result, not converted into a stronger claim.

The next earned work is an actual provider-boundary integration or a concrete external failure, rather than another layer of simulated coordination. The existing receipts remain frozen.
