# STOP-BARRIER-COLD-001

## Why this exists

External trigger: *Stop Means Stop: Measuring and Repairing the Enforcement Gap in Agent-Framework Control Primitives* (`arXiv:2607.14166`), with companion artifact pinned at commit `656840348dcf9660f8db7add9f3ca9f794a55926`.

The paper reports that control primitives named approval, rejection, cancellation, and timeout do not necessarily create an effect barrier. Its model-free probes reproduce sibling-effect leaks, replay double execution, cancellation orphans, and timeout zombies across the tested agent frameworks. SOUNDGATE repairs those classes by mediating effect release and fencing late work, under an explicit complete-mediation condition.

This is an external falsifier for a boundary OpenLine has deliberately left narrow.

Pinned OpenLine base: `6cbaf646cf8953cbad474dd1b609966c19689e9d`.

No production code is changed by this experiment.

## Existing OpenLine boundary being tested

`EffectClosure.prepare()` holds one effect ticket without applying it.

`EffectClosure.finish()` revalidates the original grant at the final receiver-owned effect frontier. Revocation admission, the frontier, and closure share one receiver-local serialization domain.

`EffectClosure.close()` may return signed `EFFECT_CLOSED` only after work already inside the frontier has drained and pending tickets under the revoked mandate have lost standing.

Exact replay of an already completed ticket is documented as process-local idempotence.

Separately, EGRESS-GATE-001 preserves a downstream acknowledgement failure as `UNRESOLVED` and does not automatically retry the call.

Those are the primitives under test. This experiment does not reinterpret a Wallet revocation being *issued* as proof that effects are already closed.

## External clauses mapped onto equivalent OpenLine primitives

The SOUNDGATE paper's operator barrier clauses are used as an external test shape, not copied as a new OpenLine API.

| External clause | OpenLine equivalent tested | Scope |
| --- | --- | --- |
| Hold until decision | `EffectClosure.prepare()` | One prepared effect ticket |
| Reject prevents held effect | authentic mandate revocation + `EffectClosure.close()` + later `finish()` | Receiver-local revoked mandate |
| Replay does not duplicate | repeated `finish()` of the exact same completed ticket | Exact ticket, current process |
| Cancel fences late work | signed `EFFECT_CLOSED` must wait for an active frontier and fence pending tickets | Receiver-local serialized effect path |
| Timeout zombie | No equivalent primitive | Not tested / not claimed |
| Run-wide sibling approval pause | No equivalent primitive | Not tested / not claimed |

The EGRESS-GATE-001 ambiguity path is also exercised: a downstream call that may have happened but loses its acknowledgement must produce one downstream attempt, `UNRESOLVED`, and no automatic retry.

## Frozen falsifier

STOP-BARRIER-COLD-001 fails if any of these happen:

1. A prepared-but-unfinished mediated ticket writes an effect.
2. A held ticket executes after the receiver has returned `EFFECT_CLOSED` for its authentic revoked mandate.
3. Replaying the exact same completed ticket advances the receiver ledger twice.
4. `EFFECT_CLOSED` returns while an effect already inside the receiver frontier can still land later.
5. A pending mediated effect lands after `EFFECT_CLOSED`.
6. An ambiguous MCP downstream outcome is automatically retried by the current Receiver Gate path.
7. Verification silently widens the result into run-wide approval, timeout, cross-ticket exactly-once, complete mediation, or external provider queue-fencing claims.

A crucial negative distinction is intentional: an effect already inside the frontier may commit after **revocation is issued**. That does not falsify OpenLine. Closure is not claimed until the receiver has observed the new standing, drained the capable frontier, and signed `EFFECT_CLOSED`.

## Cases

### 1. Hold → revoke → close → release

Prepare one effect ticket. Confirm the target ledger is unchanged. Revoke the mandate, obtain receiver-local effect closure, then attempt to finish the held ticket.

Expected: `STOPPED / MANDATE_REVOKED`; zero effects.

### 2. Exact ticket replay

Finish one prepared ticket, then submit that exact ticket again.

Expected: identical result; receiver ledger count remains one.

This does not claim arbitrary deduplication across newly created tickets, processes, or external queues.

### 3. Revocation issuance while an effect is already at the frontier

Hold the actual local ledger write inside `effect_frontier`. Issue authentic revocation while that effect is capable of committing. Start closure concurrently.

Expected ordering:

```text
REVOCATION_ISSUED
FIRST_EFFECT_COMMITTED
EFFECT_CLOSED_RETURNED
```

The first effect is allowed to finish because it was already inside the serialized frontier. Closure must not return early. A second pending ticket must be stopped after closure, and the ledger must not advance after `EFFECT_CLOSED`.

This directly preserves the distinction:

```text
revocation issued
revocation observed
outstanding effects closed
```

### 4. Ambiguous egress outcome

The downstream target records one attempt, then simulates acknowledgement loss.

Expected: Receiver Gate returns `UNRESOLVED`, `effect_applied = null`, and makes no automatic second attempt.

## Candidate status

This is a cold external experiment, not a feature patch.

The source has been inspected against current main, but the Wallet-bound reproduction must run in the actual repository checkout before any result is frozen.

If the current implementation passes unchanged, the only earned claim is:

> Within the existing receiver-local serialized effect path, held work did not escape after EFFECT_CLOSED, exact ticket replay did not duplicate the effect, and ambiguous egress was not automatically retried.

That would be an externally induced confirmation of existing local invariants. It would **not** establish run-wide sibling pause semantics, a receiver timeout barrier, cross-ticket or distributed exactly-once execution, kernel/network complete mediation, or fencing of queues owned by GitHub, Kubernetes, Kafka, NATS, cloud providers, or arbitrary agent frameworks.

If any equivalent probe fails, fix only the violated effect-fencing invariant and rerun the identical probe.

## Run

From repository root:

```bash
python -m pip install -e .
rm -rf stop-barrier-cold-artifacts
python proofs/stop-barrier-cold-001/run.py --output stop-barrier-cold-artifacts
python proofs/stop-barrier-cold-001/run.py --verify stop-barrier-cold-artifacts
```

The dedicated workflow runs the same probe on Python 3.11, 3.12, and 3.13. Ordinary Wallet CI remains separate and must also stay green.
