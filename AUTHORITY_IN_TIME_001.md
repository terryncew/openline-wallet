# AUTHORITY-IN-TIME-001

## Question

Can a receiver refuse to call an action “revocation-protected” when its own supported timing budget cannot beat the last cancellation opportunity, and can it distinguish prevention from a revocation that arrives too late?

Report thesis: **Authorization is a race against consequence.**

Pinned base: `69dfdcd1d229fb887660a347018b3422c1020527`.

This is additive proof work. It does not alter Start Here, Airlock, the v1 Wallet receipt schema, `ReferenceGate`, `EffectClosure`, or the frozen effect-closure/coordinator receipts.

## Existing boundary reused

Wallet carries principal-owned, signed revocation history. `ReferenceGate` owns exact-action allow/stop decisions and signs those decisions. `EffectClosure` already establishes a separate receiver-local effect frontier and explicitly treats an admission as something weaker than an execution lease. AUTHORITY-IN-TIME-001 adds no production execution path. It tests an earlier question: whether a receiver may promise revocation protection at admission at all.

## Frozen timing rule

All intervals are defined from one case reference point (`T0`). The deadline is the cancellation cutoff, not the later visible completion time.

```text
required_time =
    detect_bound
  + propagation_bound
  + receiver_bound
  + stop_bound
  + uncertainty_bound

remaining_margin = consequence_horizon - required_time
```

The receiver may label the simulated action `ADMIT_REVOCATION_PROTECTED` only if every required bound is present, positively valued, explicitly supported, uncertainty is counted exactly once, and `remaining_margin > 0`.

Equality, a negative margin, an unknown/unsupported bound, or an extra timing component that could double-count queueing/jitter refuses the protection claim before the action is armed.

The fixture treats the cutoff as exclusive: `stop_complete < cancellation_cutoff` can prevent the consequence. Equality is already too late.

## Discriminating cases

| Case | Frozen expectation |
| --- | --- |
| Supported budget fits | Protected admission; authentic revocation observed; stop completes before cutoff; no effect |
| Supported budget exceeds cutoff | Protection refused before action; no effect |
| Exact equality | Protection refused before action; no effect |
| Test-only bypass | Authentic revocation is observed after cutoff; effect occurs; protection is never claimed |
| Violated propagation assumption | Protection claim is invalidated even though this run happens to stop before cutoff; reconciliation required |

The bypass exists only in `SimulatedAction.arm(..., test_only_bypass=True)` inside this proof fixture. No production Wallet or receiver bypass is added.

## Evidence model

Each captured case is a receiver-signed final case receipt. The aggregate experiment receipt binds every case receipt by hash. Each case keeps these facts separate:

- proposed policy and `policy_digest`
- `bounds_basis` and `assumed_bounds_ms`
- `measured_intervals_ms`
- `observed_event_times`
- receiver-signed base authority receipt
- receiver-signed temporal admission receipt
- authentic revoked Wallet bundle and receiver-signed `STOPPED / MANDATE_REVOKED` receipt when revocation is exercised
- explicit `cancellation_cutoff` and later visible-completion time
- effect-ledger evidence sampled only after visible completion
- `effect_observed`, `closure_status`, protection status, reconciliation status, and verdict

A short successful run does not promote a fixture assumption into a worst-case network bound.

## Falsifiers

The experiment fails if an unsafe, equal, missing, or unsupported budget earns the protection label; the protected case produces an effect; the late authentic revocation is described as prevention; missing effect evidence is treated as closure; or an observed bound violation leaves the protection claim standing without reconciliation.

The exact cutoff boundary is tested independently from budget equality. The former says when a stop is physically too late; the latter says when the receiver must refuse the protection promise before execution.

## Local candidate status

The pure timing suite passes locally on Python 3.13: 12 tests, 12 passed. It covers strict positive-margin admission, equality, exceeded budget, missing/unsupported bounds, uncertainty double-count defense, a component-bound violation, pre-cutoff prevention, exact-cutoff lateness, the fixture-only negative bypass, and the requirement for post-completion effect evidence before closure.

The integration reproduction and verifier are included, but this runtime does not contain an installable checkout of `openline-wallet`, so the Wallet-bound reproduction cannot be honestly reported as executed here. The connected GitHub integration also returned HTTP 403 on branch creation, so no remote CI result exists yet. The proof receipt must remain **candidate / not frozen** until that reproduction and verifier pass on the repository checkout.

## Run

From the repository root:

```bash
python -m pip install -e .
python -m unittest discover -s proofs/authority-in-time-001 -p 'test_*.py' -v
rm -rf authority-in-time-artifacts
python proofs/authority-in-time-001/reproduce.py --output authority-in-time-artifacts
python proofs/authority-in-time-001/verify.py authority-in-time-artifacts
```

The dedicated workflow runs those steps on Python 3.11, 3.12, and 3.13 and uploads the generated evidence even if a later step fails. Existing repository CI remains untouched and will still run on the pull request.

## Earned claim boundary

Only after the integration reproduction, verifier, and remote CI pass is this claim earned:

> In this controlled fixture, the receiver admitted revocation-protected work only within a supported timing budget and distinguished prevention from late revocation.

That would justify a draft temporal profile and an OpenLine Report. It would not establish worst-case transport bounds, provider-side cancellation, multi-machine clock integrity, queue fencing, multi-receiver temporal closure, or a universal revocation guarantee.

Three facts remain separate and evidence-bearing: **revocation issued; revocation observed; outstanding effects closed.**
