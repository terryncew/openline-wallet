# FREEZE-v0.1 — conformance profile freeze record

**Profile:** OPENLINE-AUTHORITY-CONTINUITY-v0.1
**Spec file:** `conformance/OPENLINE-AUTHORITY-CONTINUITY-v0.1.md`
**SHA-256:** `2f96264c7dbc92cf13587ab932eb4d14aa876f6cfce6950d2f96909f3efe0c26`
**Frozen:** 2026-10-03
**Frozen by:** Muse, as program coordinator for OPENLINE-LEVERAGE-001,
under the user's Phase 2 order.

## Freeze statement

This profile is frozen. The flagship harness (Phase 3) must be written
against this exact version. No check may be added, removed, reworded, or
reinterpreted without a version bump (v0.2) and a new freeze record. If
implementation work reveals that a check is untestable as written, the
check is not "adjusted" — the profile version is bumped or the
experiment records the check as INCONCLUSIVE.

This ordering (profile frozen before harness) exists so the tests cannot
drift toward whatever the implementation happens to pass.

## Neutrality audit (stop rule applied)

Each of the 10 checks was reviewed against the program's stop rule: *a
check that cannot be stated without implementation-specific machinery
must be rewritten until a hostile third-party implementation could
either pass or fail it.*

Result: all 10 checks are stated using only the profile's own normative
vocabulary (owner, mandate, grant, worker, receipt, receiver, trust
root, revocation event, succession event, consequential action). No
check names Wallet, Airlock, Receipt Gate, or any OpenLine class,
product, or protocol. No check requires specific cryptography,
transport, or language. A third party with no knowledge of OpenLine can
implement a subject under test and be judged pass/fail/inconclusive on
every check.

## Companion artifact

`conformance/COVERAGE-MATRIX.md` maps each check to existing frozen
evidence vs. genuinely new flagship evidence required. It is
informative, not normative; it may be updated without bumping the
profile version, but it may not change what any check requires.
