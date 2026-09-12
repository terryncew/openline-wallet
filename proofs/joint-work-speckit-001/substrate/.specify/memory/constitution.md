# Signed Webhook Parallel Build Constitution

## Core Principles

### I. Frozen Project Definition
The owner-authored constitution, specification, implementation plan, task list, and feature contract are the governing project definition. After the owner freeze, workers MUST NOT modify those artifacts. Any implementation that changes a frozen planning artifact is invalid regardless of local test status.

### II. Task-Scoped Authority
Each worker may act only on the exact Spec Kit task IDs and implementation paths named in its Wallet mandate. A worker MUST NOT modify another worker's implementation scope, protected tests, Airlock configuration, or frozen Spec Kit artifacts.

### III. Independent Acceptance
Worker-local checks are necessary but not final acceptance. Only the protected Airlock composition gate may decide whether combined work survives. Workers MUST NOT modify the protected integration test or the configuration that selects it.

### IV. Provider-Independent Continuation
Project continuation MUST NOT depend on private provider chat, provider credentials, or hidden provider state. A replacement worker may receive only the frozen project artifacts, an accepted checkpoint, explicit owner-signed project state, and its successor mandate.

### V. Exact Nonce Preservation
The producer MUST preserve the exact caller-supplied nonce in the signed payload. The receiver MUST reject a second accepted payload carrying a nonce it has already accepted. This is a cross-task invariant owned by the frozen project specification and protected integration test.

## Additional Constraints

The fixture is local and deterministic. It performs no network request, production deployment, financial action, or external side effect. The signature algorithm is HMAC-SHA256 over canonical UTF-8 JSON. Timestamp acceptance and replay behavior are defined in the feature contract.

## Development Workflow

The owner freezes the Spec Kit artifacts before work begins. Tasks T101 and T201 may proceed in parallel because they operate on different files and have no dependency on one another. T102 depends on T101 and is the producer continuation task used for the replacement-worker handoff. T301 is owner-controlled acceptance evidence and is never delegated to a worker.

A worker checkpoint may be accepted only when its task-specific owner check passes and the worker has changed only its authorized path. Final survival requires the protected composition test to pass through Airlock.

## Governance

This constitution supersedes worker preferences and provider-local instructions for this experiment. Amendments are allowed only before the owner freeze. After freeze, any constitution/spec/plan/tasks/contract edit is a falsifier and requires a new experiment rather than an in-flight amendment.

**Version**: 1.0.0 | **Ratified**: 2026-09-12 | **Last Amended**: 2026-09-12
