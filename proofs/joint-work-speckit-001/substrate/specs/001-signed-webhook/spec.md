# Feature Specification: Signed Webhook Producer and Receiver

**Feature Branch**: `001-signed-webhook`

**Created**: 2026-09-12

**Status**: Frozen for JOINT-WORK-SPECKIT-001

**Input**: User description: "Build a producer and receiver for one signed webhook interface as separate task groups, then compose them under owner-controlled acceptance."

## User Scenarios & Testing

### User Story 1 - Produce a Signed Webhook (Priority: P1)

A caller supplies an event, resource identifier, amount, timestamp, and nonce. The producer returns canonical payload bytes and an HMAC-SHA256 signature header.

**Why this priority**: The receiver cannot verify a webhook without an exact producer-side serialization and signature contract.

**Independent Test**: The producer can be tested locally for canonical encoding, signature shape, and valid nonce shape without running the receiver.

**Acceptance Scenarios**:

1. **Given** valid input fields, **When** the producer builds a request, **Then** it returns canonical UTF-8 JSON and a matching `v1=<hex>` signature.
2. **Given** a caller nonce, **When** the producer builds a request, **Then** the signed payload preserves that exact nonce value.

---

### User Story 2 - Verify and Replay-Protect the Webhook (Priority: P1)

A receiver validates the exact canonical payload, timestamp window, signature, and replay nonce before accepting the event.

**Why this priority**: Verification and replay resistance are the receiving half of the interface and can be implemented independently from the producer.

**Independent Test**: The receiver can be tested with owner-authored canonical fixtures without importing the producer.

**Acceptance Scenarios**:

1. **Given** a valid signed payload, **When** the receiver evaluates it, **Then** it returns accepted.
2. **Given** an already accepted nonce, **When** the same nonce is presented again with a valid signature, **Then** the receiver returns replay.
3. **Given** an invalid signature, stale timestamp, or malformed payload, **When** the receiver evaluates it, **Then** it returns the frozen error response.

---

### User Story 3 - Compose Independently Produced Halves (Priority: P1)

The project owner combines the independently produced producer and receiver and validates the frozen interface end to end.

**Why this priority**: Local plausibility is insufficient if the two halves disagree on a cross-task invariant.

**Independent Test**: The protected integration test invokes the real producer and real receiver together from the same frozen project state.

**Acceptance Scenarios**:

1. **Given** two distinct caller nonces, **When** the valid producer sends two requests through the receiver, **Then** both first deliveries are accepted.
2. **Given** the first accepted request, **When** it is delivered again, **Then** the receiver returns replay.
3. **Given** a locally-green producer that replaces every caller nonce with one valid constant nonce, **When** it is composed with the valid receiver, **Then** the protected integration test fails.

### Edge Cases

- Canonical payload bytes differ from the frozen serialization rule.
- Timestamp skew is greater than 300 seconds in either direction.
- A nonce is non-ASCII or shorter than 16 characters.
- A signature does not exactly match `v1=` plus 64 lowercase hexadecimal characters.
- A worker changes a frozen Spec Kit artifact, protected test, Airlock configuration, or the other worker's implementation path.

## Requirements

### Functional Requirements

- **FR-001**: The payload MUST contain exactly `amount_cents`, `event`, `nonce`, `resource_id`, and `timestamp`.
- **FR-002**: The producer MUST serialize the payload as UTF-8 JSON with sorted keys, separators `(',', ':')`, and no insignificant whitespace.
- **FR-003**: The producer MUST sign the exact canonical payload bytes with HMAC-SHA256 and return `X-OpenLine-Signature: v1=<64 lowercase hex>`.
- **FR-004**: The producer MUST preserve the exact caller-supplied nonce in the signed payload.
- **FR-005**: The receiver MUST accept timestamps whose absolute skew is at most 300 seconds and reject larger skew with status 422 and `stale_timestamp`.
- **FR-006**: The receiver MUST reject an invalid signature with status 401 and `invalid_signature`.
- **FR-007**: The receiver MUST reject malformed or non-canonical payloads with status 400 and `bad_payload`.
- **FR-008**: The receiver MUST accept a valid unseen nonce once and reject a later accepted payload using the same nonce with status 409 and `replay`.
- **FR-009**: Worker A is limited to producer implementation tasks T101/T102 in `src/producer.py`; Worker B is limited to receiver implementation task T201 in `src/receiver.py`.
- **FR-010**: The constitution, spec, plan, tasks, feature contract, local owner checks, protected integration test, and Airlock configuration MUST remain unchanged after owner freeze.
- **FR-011**: Final project acceptance MUST depend on the protected Airlock composition test, not worker-local success or Spec Kit convergence assessment alone.
- **FR-012**: A successor for T102 MUST be able to continue from the accepted T101 checkpoint using only frozen project artifacts, explicit owner-signed state, and successor authority.

### Key Entities

- **Webhook payload**: The five frozen request fields signed by the producer and consumed by the receiver.
- **Replay nonce**: Caller-supplied ASCII identifier preserved end to end and remembered by the receiver after acceptance.
- **Accepted checkpoint**: Exact producer commit and blob digest that passed the owner T101 checkpoint before worker revocation.

## Success Criteria

### Measurable Outcomes

- **SC-001**: T101 and T201 execute concurrently in isolated worktrees and both remain within their frozen file scopes.
- **SC-002**: The T101 checkpoint passes while T102 remains intentionally incomplete, proving real successor work remains.
- **SC-003**: After Worker A revocation, an attempted T102 action is refused with `MANDATE_REVOKED` before successor work begins.
- **SC-004**: A successor authorized for T102 completes the producer from the accepted checkpoint without private state transfer.
- **SC-005**: The preregistered fixed-nonce producer remains locally green while Airlock rejects the combined implementation at the protected integration test.
- **SC-006**: The valid combined producer and receiver pass the same protected Airlock composition gate.
- **SC-007**: Provider spend for this arm is exactly $0.

## Assumptions

- Python 3.11 or newer is available.
- The fixture uses only the Python standard library for webhook implementation.
- The owner supplies the shared fixture secret directly to local tests; it is not a production credential.
- This arm uses scripted workers only and earns no claim about Claude, Codex, or any other live provider.
