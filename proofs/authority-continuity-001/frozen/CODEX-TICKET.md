# CODEX TICKET — authority-continuity-001 harness (Phase 3 apparatus)

**Prereg:** `PREREG-v1.0.md`, sha256
`3807bc088bc0236e2ce869018f9355c05581fa6082c3f948daddab90adb62e1a`
(FROZEN — do not edit, do not reinterpret. If the prereg is untestable
as written, stop and report; do not adjust it.)

**Conformance profile:** OPENLINE-AUTHORITY-CONTINUITY-v0.1, spec sha256
`2f96264c7dbc92cf13587ab932eb4d14aa876f6cfce6950d2f96909f3efe0c26`.

## Build only this

A deterministic harness executing PREREG-v1.0 §"Exact sequence"
(S1…S11) and §"Adversarial controls" (C1…C7), plus the independent
verifier per §"Independent verifier". Nothing more. No new features,
no SDK, no product surface.

Suggested location (existing convention):
`openline-receipt-gate/experiments/authority-continuity-001/`.
New branch. No merge. No PR opened by you.

## Hard constraints

- $0 spend. Zero model calls. No paid APIs. No network.
- Workers are scripted deterministic processes (separate OS processes,
  separate home dirs, distinct Ed25519 identities per the prereg's key
  derivation). Do NOT use Claude Code, Codex, or any model CLI.
- Do NOT claim provider replacement anywhere — not in code, logs,
  docs, or output. The prereg explicitly marks it unproven.
- Vendoring: copy `PREREG-v1.0.md` into the experiment dir
  byte-identical; add a CI/check step asserting its sha256 equals the
  frozen hash above. If it differs, fail loudly.
- Canonical serialization, artifact schema, key derivation, verifier
  isolation: exactly as the prereg specifies. No improvisation.

## Deliverables

1. `harness/` — driver executing S1…S11 and C1…C7 deterministically.
2. `verify.py` — independent verifier per prereg §"Independent
   verifier" (clean subprocess, read-only bundle view, exit non-zero
   on first disagreement).
3. `RUNBOOK.md` — exact commands to run the experiment and the
   verifier, and how to reproduce from a clean clone.
4. Export bundle generation (mandate, grants, revocation, all
   receipts incl. refusals, public keys, profile hash, run log).
5. `RESULTS.md` — filled by the run: per-step decisions, per-control
   outcomes, AC-01…AC-10 conformance results, SHA-256 of every frozen
   artifact, explicit claim ceiling (copy prereg §"What this run does
   NOT claim").
6. Raw run artifacts preserved (run log, all submitted attempts,
   receiver decisions, verifier report).

## Rules of engagement

- Follow the prereg exactly. Any ambiguity or untestable step: stop
  and report the exact section — do not invent a resolution.
- If the first run exposes an apparatus defect: classify the run as
  apparatus failure, fix the apparatus, and report. Never alter an
  acceptance rule to make a run pass.
- AC-09 uses the existing frozen TRUST-ROOT-SUCCESSION-001 evidence;
  do not simulate trust-root succession inside the run.
- When done: report branch name, commit SHA, and the run's
  PASS/FAIL/INCONCLUSIVE verdict with the evidence paths. Do not
  merge. Do not open a PR unless asked.
