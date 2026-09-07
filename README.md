# Agent Watchdog

A small, standalone advisor for completed agent attempts. It distinguishes observable task progress from action repetition and enforces caller-supplied hard budgets.

## What changed in 0.3.0

The previous implementation could recommend `KILL RUN` after five different successful pytest commands because it compared only their first tokens. It also described `1 - freshness` as burn rate. Both were incorrect. Action-only input now reports `INSUFFICIENT_PROGRESS_EVIDENCE` and cannot trigger a loop kill. Action diversity remains a diagnostic only.

The old action-only `calibrate()` kill-line API is disabled and raises `ACTION_ONLY_CALIBRATION_UNSUPPORTED`. The old `kill_threshold` argument remains accepted for compatibility, but cannot authorize termination. Existing integrations must review the changed status and recommendation contract before upgrading.

## Observation contract

The caller owns the execution loop and supplies an independent, task-specific verifier. The verifier returns `ProgressEvidence(objective_id, state_fingerprint, progress)`. The objective is fixed for the run. `progress` is a nonnegative, monotonic measure of verified work toward that objective, such as the number of independently accepted test cases or a task-specific integer score. The evaluator defines what counts; the watchdog does not infer progress from successful exit codes, new commands, changed output, state fingerprints, or an agent's claimed hypothesis.

A baseline may be supplied before the run. Without one, the first verified observation establishes the baseline and does not invent an improvement. Each verified observation that fails to exceed the best progress increments the non-progress count, even when the state changes. Only a verified advance resets that count. Missing observations do not reset it and cannot independently trigger a loop kill. The default allowance is five observed non-progressing attempts; the caller may set `max_no_progress_steps`. A terminal stop is sticky for that run.

```python
from agent_watchdog import AgentWatchdog, Attempt, ProgressEvidence

# Implement this in your trusted evaluator. It must inspect external receipts
# or task-owned state and must never trust the agent's claim of progress.
def verify_attempt(attempt):
    return ProgressEvidence(
        objective_id="test-sweep-001",
        state_fingerprint=trusted_test_state_digest(),
        progress=verified_completed_test_count(),
    )

dog = AgentWatchdog(
    objective_id="test-sweep-001",
    verifier=verify_attempt,
    baseline=ProgressEvidence("test-sweep-001", "initial-state", 0),
    max_no_progress_steps=5,
    max_elapsed_seconds=600,
    max_spend="2.00",
)

# The caller executes and evaluates the work; the watchdog never does.
dog.log_attempt(Attempt("pytest test_a.py", result=trusted_result, outcome="success"))
status = dog.audit(elapsed_seconds=elapsed_from_supervisor, spent=actual_spend_usd)

if status.status == "RED":
    # The execution owner decides how to stop safely.
    stop_or_drain_run(status.reason)
```

`log_action(action)` is retained for old callers. It records the complete trimmed action, preserving arguments and case, but yields UNKNOWN unless independently verified progress is also available. A custom normalizer can group action families for diagnostics; it has no effect on stopping.

## Status and budgets

`GREEN` means a verified objective advance was observed. `AMBER` means observed non-progress is below the allowance. `UNKNOWN` means progress evidence is insufficient. `RED` means observed stagnation or a hard ceiling has been reached. `HOLD` means an external mutation has an uncertain outcome and requires reconciliation. The `reason` and `recommendation` fields provide the specific decision.

The existing `freshness` field is diagnostic action diversity. `burn_rate` is now actual cumulative spend divided by elapsed seconds, in the configured spend unit per second, or `None` when either measurement is unavailable or elapsed time is zero. `spent` is a Decimal cumulative amount. Unknown spending is never estimated or reported as zero.

`record_usage()` and `audit()` accept cumulative, nondecreasing actual elapsed time and spending. Hard time and spend ceilings are independent of progress, repetition, and the verifier. They apply even when no action or result is available. The caller must supply fresh meter readings; the library has no continuous visibility inside a running agent.

## External mutations

An attempt with `external_mutation="uncertain"` enters a sticky HOLD before progress evaluation. Subsequent `log_attempt()` calls are rejected with `RECONCILIATION_REQUIRED`. There is no retry mechanism. A caller-owned, read-only `reconciliation_verifier` must explicitly confirm resolution before `resolve_uncertainty()` clears the hold. Resolution does not reset the non-progress allowance or a terminal stop. Hard ceilings remain active while an outcome is uncertain; the recommendation is then `STOP AND RECONCILE`, not a blind retry.

This is an in-memory, advisory utility. It does not provide durable recovery, execution fencing, continuous agent introspection, provider cancellation, or authority to mutate external systems. Those guarantees belong to the execution owner. No Airlock or Wallet integration is included.

## Verification

Run `python -m unittest discover -s tests -v`. The paired regression includes the exact original source and verifies its false kill, a productive five-test sweep, and a stalled sequence with different commands and superficial hypotheses. Additional tests cover missing evidence, unchanged or regressed progress, hard ceilings, actual spending, and uncertain mutations. CI runs tests, compilation, and wheel build across the declared Python versions.

See `proofs/watchdog-001/receipt.json` for the frozen local result and source hashes. The receipt is an unsigned engineering record, not an independent certificate or a live-agent safety claim. Freeze this standalone repair after the full downstream CI passes; Airlock integration remains deferred.
