# Implementation Plan: Signed Webhook Producer and Receiver

**Branch**: `001-signed-webhook` | **Date**: 2026-09-12 | **Spec**: `specs/001-signed-webhook/spec.md`

**Input**: Feature specification from `/specs/001-signed-webhook/spec.md`

## Summary

Implement a small signed-webhook producer and receiver as independent Python modules. The producer canonicalizes and signs one frozen payload shape. The receiver validates canonicalization, time, signature, and replay state. The two halves are developed independently and survive only if an owner-protected end-to-end composition test passes.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Python standard library (`json`, `hashlib`, `hmac`, `re`)

**Storage**: In-memory replay set for the local fixture

**Testing**: Owner-authored Python check scripts executed under Airlock

**Target Platform**: Local Linux CI fixture

**Project Type**: Single Python project

**Performance Goals**: Not a benchmark; deterministic correctness only

**Constraints**: No network calls, no external side effects, no worker edits to frozen planning or acceptance artifacts

**Scale/Scope**: Two implementation files, three worker tasks, one protected composition test

## Constitution Check

*GATE: Must pass before implementation. Re-check at final composition.*

- Frozen Project Definition: PASS — owner artifacts are immutable and protected.
- Task-Scoped Authority: PASS — T101/T102 map only to `src/producer.py`; T201 maps only to `src/receiver.py`.
- Independent Acceptance: PASS — final acceptance is the owner-protected Airlock composition test.
- Provider-Independent Continuation: PASS — successor handoff contains only frozen artifacts, accepted checkpoint evidence, and owner-signed state.
- Exact Nonce Preservation: PASS — FR-004 and the protected integration test bind the cross-task invariant.

## Project Structure

### Documentation (this feature)

```text
.specify/memory/constitution.md
specs/001-signed-webhook/
├── spec.md
├── plan.md
├── tasks.md
└── contracts/
    └── webhook.md
```

### Source Code (repository root)

```text
src/
├── producer.py
└── receiver.py

tests/
├── test_producer_checkpoint.py
├── test_producer.py
├── test_receiver.py
└── test_convergence.py
```

**Structure Decision**: Use one minimal Python project so task boundaries and final composition are visible without framework noise.

## Complexity Tracking

No constitution violations are preregistered.
