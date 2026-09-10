# Temporal Authority Profile v0

Status: **draft profile derived from frozen AUTHORITY-IN-TIME-001**.

This document defines the minimum information a receiver needs before it may claim that a consequential action is protected by a bounded revocation path. It is not a production API, a universal latency guarantee, or a new execution mechanism.

## Purpose

An authority grant can be valid while its revocation path is too slow to stop the consequence. A receiver therefore must decide temporal protection before admitting the action, using the last preventable moment rather than the later moment when an effect becomes visible.

## Required profile

A temporal-authority decision binds:

```text
action_id
authority_id / mandate_id
receiver_id
consequence_horizon
detect_bound
propagation_bound
receiver_bound
stop_bound
uncertainty_bound
bounds_basis
clock_model
required_time
remaining_margin
admission_decision
```

All duration values must use one explicit unit and one declared clock model. A concrete implementation should use unambiguous field names such as `_ms` when milliseconds are used.

`bounds_basis` records the support for each bound. Support may be measured, externally guaranteed, or assumed, but those categories must remain distinguishable. An assumption is not promoted to a measured or guaranteed bound merely because one run completed quickly.

## Admission rule

```text
required_time =
    detect_bound
  + propagation_bound
  + receiver_bound
  + stop_bound
  + uncertainty_bound

remaining_margin = consequence_horizon - required_time
```

A receiver may claim revocation protection only when:

- every required bound is present;
- every bound has explicit support;
- uncertainty is counted exactly once;
- the clock model is defined;
- the timing basis is applicable to this action and receiver; and
- `remaining_margin > 0`.

The receiver must refuse the protection claim when the margin is zero or negative, a required bound is missing or unsupported, the clock basis is undefined, or uncertainty could be double-counted.

Admission decisions are therefore limited to:

```text
ADMIT_REVOCATION_PROTECTED
REFUSE_REVOCATION_PROTECTION
```

A refusal is not a statement that the underlying authority is invalid. It means the receiver cannot support the stronger claim that revocation can still beat consequence.

## Three separate facts

Every receipt or report that discusses revocation must preserve these as separate evidence-bearing facts:

```text
REVOCATION_ISSUED
REVOCATION_OBSERVED
EFFECTS_CLOSED
```

`REVOCATION_ISSUED` means the authority owner produced the authentic revocation.

`REVOCATION_OBSERVED` means the relevant receiver incorporated that revocation into its current authority state.

`EFFECTS_CLOSED` means outstanding consequential work can no longer escape the applicable effect frontier.

None of these facts implies either of the others.

## Bound violations

Temporal protection depends on the bounds that justified admission. If an observed interval violates one of those bounds, the receiver must invalidate the temporal-protection claim and enter reconciliation/effect-closure handling.

A lucky outcome does not preserve the claim. If a run happens to stop before consequence despite violating a promised bound, the action was still outside the supported profile.

A late revocation must never be rewritten as prevention. If the last cancellation opportunity has passed and an irreversible effect later appears, the record must say that revocation was issued or observed too late to prevent that effect.

## Effect evidence

Missing effect evidence is not effect closure. Closure requires affirmative evidence from the relevant receiver/effect frontier after the point at which the consequence could have appeared.

The temporal profile governs whether revocation protection may be promised at admission. Existing effect-closure machinery remains responsible for establishing what happened to outstanding work afterward.

## Scope boundary

Temporal Authority Profile v0 does not establish:

- worst-case public-network transport latency;
- provider-side cancellation guarantees;
- synchronized multi-machine clocks;
- queue fencing;
- arbitrary multi-receiver closure;
- universal revocation protection; or
- protection for an action whose consequence is already irreversible.

Those require evidence from the actual receiver and consequence path.

The governing distinction remains:

**Revocation issued. Revocation observed. Outstanding effects closed.**
