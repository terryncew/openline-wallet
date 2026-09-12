---

description: "Frozen task list for JOINT-WORK-SPECKIT-001"
---

# Tasks: Signed Webhook Producer and Receiver

**Input**: Design documents from `/specs/001-signed-webhook/`

**Prerequisites**: `plan.md`, `spec.md`, `contracts/webhook.md`

**Tests**: Owner-authored checks are frozen before workers start. Workers do not author or modify the judge.

**Organization**: Producer and receiver tasks are separated so T101 and T201 can proceed in parallel. T102 is an explicit producer continuation task after the T101 checkpoint.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel because it touches a different implementation file and has no task dependency.
- **[Story]**: User-story traceability from `spec.md`.
- Exact implementation paths are part of the frozen task definition.

## Phase 1: Owner Freeze

- [x] T001 [US3] Freeze constitution, spec, plan, tasks, feature contract, owner checks, protected integration test, and Airlock configuration before worker execution

**Checkpoint**: Project definition is frozen and may not be changed by a worker.

---

## Phase 2: Parallel Implementation

- [ ] T101 [P] [US1] Implement canonical payload serialization and HMAC signature helper in `src/producer.py`; leave `build_request` unresolved at the accepted checkpoint
- [ ] T201 [P] [US2] Implement canonical validation, timestamp validation, signature verification, and replay protection in `src/receiver.py`

**Checkpoint**: T101 and T201 may be independently verified while remaining isolated from one another.

---

## Phase 3: Producer Continuation After Checkpoint

- [ ] T102 [US1] Complete `build_request` in `src/producer.py` from the accepted T101 checkpoint without changing the verified helpers

**Dependency**: T102 depends on accepted T101.

**Checkpoint**: Producer local check passes after T102.

---

## Phase 4: Protected Composition

- [ ] T301 [US3] Owner-only Airlock composition evaluates `tests/test_convergence.py` against the combined producer and receiver

**Dependency**: T301 depends on completed T102 and T201.

**Checkpoint**: Local worker success is not final; the candidate survives only if T301 passes with frozen planning and acceptance artifacts unchanged.

---

## Dependencies & Execution Order

- T001 precedes all worker execution.
- T101 and T201 are marked [P] and may run concurrently.
- T102 depends on T101 and may be reassigned to a successor after revocation.
- T301 depends on T102 and T201 and is never delegated to a worker.

## Parallel Opportunities

- Worker A: T101 in `src/producer.py`
- Worker B: T201 in `src/receiver.py`
- T101 and T201 share only the frozen Spec Kit project artifacts and no private worker state.

## Notes

- A worker task is not complete merely because its local check passes.
- A worker may not change `.specify/**`, `specs/**`, `tests/**`, or `.airlock/**`.
- The preregistered negative producer keeps local producer checks green while violating FR-004; T301 must reject it.
