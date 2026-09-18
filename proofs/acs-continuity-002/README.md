# ACS-CONTINUITY-002 — planned host replacement conserves authority

Switching AI hosts should not give the replacement a fresh credit limit.

## 1. The problem

Two independent agent hosts enforce the same portable policy
(`acs-continuity-budget-v1`, 100 units of owner authority).
Host A already committed 60 of the 100.

A portable policy is not enough on its own: the policy says what is
allowed, but the *consumed authority* lives in the host's state. If that
consumption does not travel, a naive fresh Host B starts from 100 again —
and the owner's budget silently doubles.

## 2. The handoff

A closes. On closing, A emits a successor-bound, signed continuity
receipt — a one-way, planned handoff, not a protocol for arbitrary
failover:

```json
{
  "original": 100,
  "committed": 60,
  "encumbered": 0,
  "available": 40,
  "successor": "B"
}
```

(signed with Ed25519 over canonical JSON; the receiving host verifies the
signature, the successor binding, the sequence freshness, and the source's
closure before importing.)

## 3. The result

Fresh Host B imports the receipt and starts from **40, not 100**:

- B tries to commit 60 → **refused** (only 40 available)
- B commits 40 → **admitted** (the 100 units stay conserved: 60 + 40)
- a replay of A's receipt → **refused** (stale sequence)
- the receipt presented to host C → **refused** (wrong successor)
- A tries to act after closure → **refused** (source is closed)
- an unresolved 10-unit effect stays **encumbered** across the cutover —
  it is not spendable by either host until it resolves

Crash probes (a real wall-clock SIGKILL mid-commit and a deterministic
crash at the atomic-rename danger point) left state disjunctive-consistent:
either the pre-commit state or the committed state, never torn, no handoff
emitted before closure. No shared mutable database, no consensus, and no
network coordination were used.

## 4. Scope

This proves **one thing**: across a *planned, one-way* replacement of one
independent enforcement host with another, a successor-bound handoff
preserved the owner's cumulative 100-unit authority history.

It does **not** prove arbitrary failover, active-active authority,
distributed exactly-once semantics, consensus-free multi-writer
conservation, or production readiness.

On ACS, accurately: ACS provides portable *policy* evaluation; the host
owns *state*. This study demonstrates one way to carry conserved authority
across a planned host replacement — portable policy plus portable
authority history.

## 5. Evidence

- Frozen result: `TERMINAL_FREEZE.md` (this directory) —
  `PASS_ACS_CONTINUITY_PLANNED_HOST_HANDOFF_CONSERVES_AUTHORITY`,
  one $0 run, 2026-09-18T02:41:51Z, all 11 cases (B0, T1–T10) PASS,
  all 12 conditions true.
- Machine-readable summary: `result-summary.json`.
- Integrity manifest (every sealed artifact hash): `INTEGRITY_MANIFEST.md`.
- Sealed experiment artifacts (workspace originals, read-only):
  `~/workspace/acs-continuity-002/` — preregistration, apparatus,
  qualification seal, terminal record, freeze record, RESULT.json.

Note: the predecessor study ACS-CONTINUITY-001 remains permanently
`FAIL_ACS_CONTINUITY_CONSERVATION_OR_REPLAY` (its T7 SIGKILL probe never
launched due to a harness argv bug). -002 is a fresh-numbered replication
with a one-line harness repair; it does not retroactively repair -001.
