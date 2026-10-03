# PREREG-v1.0 — Flagship authority-continuity experiment

**Program:** OPENLINE-LEVERAGE-001
**Version:** 1.0 — FROZEN. See PREREG-v1.0.sha256.
**Conformance profile:** OPENLINE-AUTHORITY-CONTINUITY-v0.1 (frozen,
spec sha256 `2f96264c7dbc92cf13587ab932eb4d14aa876f6cfce6950d2f96909f3efe0c26`).
**Date frozen:** 2026-10-03
**Spend:** $0. Zero model calls. No paid API usage.

## Claim under test

An approved job can survive replacement of the worker or provider without
transferring authority to the replacement automatically, losing the old
receipts, or allowing the revoked worker to continue.

This run tests **worker replacement**. Provider-level composition (two
genuinely distinct model providers) is **not claimed by this run** and is
marked separately unproven (see §9). Nothing in this run simulates a
provider and calls it provider replacement.

## Roles and keys (frozen)

All keypairs are Ed25519, generated deterministically:

```
seed(role) = SHA256("OPENLINE-LEVERAGE-001/PREREG-v1.0/" || role)
roles: owner, receiver, worker-a, worker-b, worker-c
```

| Role | Identity | Purpose |
|---|---|---|
| Owner O | `owner` | approves the mandate, issues grants, issues the revocation and the successor grant |
| Receiver R | `receiver` | independent process; pins O's public key; renders every accept/refuse decision; signs receipts |
| Worker A | `worker-a` | begins the job under grant G_A |
| Worker B | `worker-b` | successor; continues only under explicit successor grant G_B |
| Worker C | `worker-c` | adversarial identity for the wrong-worker control |

Workers are scripted deterministic processes: separate OS processes,
separate home directories, no shared memory, no credential transfer.
This run tests authority mechanics, not model behavior — it does not
claim to be a live-model experiment.

## The mandate (frozen)

Mandate M, job J-001: *"Prepare and execute a vendor transfer of 100
units in two phases. Phase 1 (prepare): assemble the transfer order.
Phase 2 (execute): commit the transfer to the receiver-guarded ledger."*

Acceptance criteria (frozen, objective): phase-1 evidence must contain a
well-formed transfer order (payer, payee, amount = 100, phase = 1);
phase-2 evidence must reference the accepted phase-1 receipt and contain
a matching execution record (phase = 2). The receiver checks evidence
against these criteria without trusting the worker.

The consequential effect is the receiver-guarded ledger state change on
phase-2 acceptance. Simulated money; the state change is real within
the experiment.

## Canonical serialization (frozen)

Every signed artifact: JSON object, keys sorted lexicographically,
separators `(",", ":")`, UTF-8, no whitespace. `payload_hash` =
SHA-256 over the canonical bytes of the artifact with the `signature`
(and `payload_hash` itself) removed. Signatures are Ed25519 over the
`payload_hash` bytes, hex-encoded.

## Artifact schema (frozen)

```json
mandate.json:
  { "mandate_id": "hex (SHA-256 of canonical job description)",
    "job": { "description": "str", "phases": ["prepare", "execute"],
             "acceptance_criteria": "str" },
    "owner_pubkey": "hex", "issued_seq": "int", "signature": "hex" }

grant.json:
  { "grant_id": "str (unique)", "issuer_pubkey": "hex",
    "subject_pubkey": "hex", "mandate_id": "hex",
    "scope": "\"full\" | \"phase-1\" | \"phase-2\"",
    "valid_from_seq": "int", "valid_until_seq": "int | null",
    "signature": "hex" }

revocation.json:
  { "revocation_id": "str", "grant_id": "str",
    "issuer_pubkey": "hex", "seq": "int", "reason": "str",
    "signature": "hex" }

receipt.json:
  { "receipt_id": "str", "mandate_id": "hex",
    "worker_pubkey": "hex", "action": "str",
    "grant_id": "str | null", "decision": "\"ACCEPTED\" | \"REFUSED\"",
    "reason_codes": ["str"], "prev_receipt_hash": "hex | null",
    "payload_hash": "hex", "signature": "hex (receiver)" }
```

The receiver maintains a monotonic sequence counter; every artifact it
admits is stamped with the next sequence number. Revocation ordering is
judged by sequence position. An attempt already underway when a
revocation is recorded is out of scope; the *next* consequential
attempt must be refused.

## Exact sequence (frozen — S1…S11)

- **S1.** Owner O signs mandate M. Receiver admits it (receipt: mandate
  admitted).
- **S2.** Owner O signs grant G_A naming worker A for mandate M
  (scope `full`). Receiver admits it.
- **S3.** Worker A submits phase-1 completion with evidence. Receiver
  verifies evidence against the acceptance criteria and G_A's validity
  → **ACCEPT**. Receipt R_A1 (ACCEPTED), chained.
- **S4.** Owner O signs revocation event REV_A for grant G_A with reason
  "worker A removed from job". Receiver records it at its sequence
  position (receipt: revocation recorded).
- **S5.** Stale worker A attempts phase 2 citing G_A → receiver
  **REFUSES** (grant revoked). *Control C1.*
- **S6.** Adversarial controls C2…C7 execute (see below); each must be
  **REFUSED**, with no ledger or receipt-log change.
- **S7.** Worker B appears (new identity/key, clean home) and attempts
  phase 2 with no grant of its own → **REFUSED**. *Control C3.*
- **S8.** Owner O signs first-class successor grant G_B naming worker B
  for mandate M (scope `full`). The grant is a real signed artifact;
  succession is never inferred from "B started".
- **S9.** Worker B submits phase-2 completion with evidence referencing
  R_A1. Receiver verifies evidence, G_B's validity, G_A's revoked
  status, and chain continuity → **ACCEPT**. Receipt R_B2 (ACCEPTED)
  chained after R_A1. Ledger updated: transfer executed.
- **S10.** Receiver exports the bundle: mandate, G_A, G_B, REV_A, all
  receipts (including refusals), owner + receiver public keys,
  conformance profile hash, run log.
- **S11.** Independent verifier runs in a clean process (see §7) and
  re-derives every accept/refuse decision from the bundle alone.

## Adversarial controls (frozen — must all be REFUSED)

| ID | Attempt | Maps to |
|---|---|---|
| C1 | Stale A attempts phase 2 after REV_A, citing G_A | AC-03 |
| C2 | Byte-exact replay of A's previously valid phase-1 authorization, presented as fresh | AC-04 |
| C3 | B attempts phase 2 before any successor grant (no grant, then A's grant) | AC-05 |
| C4 | Valid owner signature, wrong worker: owner-signed grant naming B, presented by A | AC-05 |
| C5 | Valid owner signature, wrong job: owner-signed grant for a different mandate M2, presented for M1 | AC-02 (scope/mandate mismatch) |
| C6 | Tampered historical receipt: R_A1 with modified payload, original signature attached, submitted as chain tip | AC-10, AC-08 |
| C7 | Successor self-install: "succession" artifact signed by B (not the owner) claiming B holds A's authority | AC-09 (worker-level analogue) |

Each control is a deterministic adversarial attempt submitted to the
receiver. A control that cannot be executed as specified records the
run INCONCLUSIVE (apparatus), never a pass.

## Pass / fail / inconclusive rules (frozen)

**PASS** iff *all* of the following hold:

1. S3 ACCEPTED, S5/S7 REFUSED, C1…C7 all REFUSED, S9 ACCEPTED.
2. The mandate digest (`mandate_id`) is unchanged across the run.
3. Every receipt in the log verifies (signature + chain continuity);
   the log is append-only.
4. The ledger shows exactly one phase-2 execution, attributed to B
   under G_B; no effect from any refused attempt.
5. The independent verifier (S11) agrees with every recorded decision.
6. Conformance AC-01…AC-10 all PASS, with AC-09 satisfied by the
   reused frozen TRUST-ROOT-SUCCESSION-001 evidence (explicitly noted;
   trust-root succession is not claimed to have occurred in this run).

**FAIL** iff any PASS condition is violated. A failure is an
architecture failure: report it plainly, fix only the failed
invariant.

**INCONCLUSIVE** iff the apparatus fails (crash, hang, environment
fault) or a control cannot be executed as specified. Classify the run
as apparatus failure, fix the apparatus, bump the prereg or apparatus
version as appropriate, and rerun from zero. **Never alter an
acceptance rule to make a run pass.**

## Independent verifier (frozen constraints)

- Invocation: `python verify.py --bundle <dir> --profile <frozen profile>`.
- The verifier process receives **only**: the export bundle directory,
  the frozen conformance profile file, public verification material
  (owner + receiver public keys, inside the bundle), and its own code.
- It MUST NOT access: the live receipt store, any private key,
  runtime memory of the harness, provider credentials, network, or
  hidden state. The harness enforces this by spawning the verifier as
  a subprocess with a clean environment and a read-only view of the
  bundle directory.
- It re-checks: every signature (grants, revocation, receipts) against
  the pinned keys; hash-chain continuity; revocation ordering against
  attempt sequence positions; grant validity (subject, mandate, scope,
  sequence window) at each decision point; and re-renders each
  accept/refuse decision.
- Exit 0 iff every re-derived decision matches the run's recorded
  decisions; otherwise exit non-zero with the first disagreement
  identified. Its report is part of the frozen artifacts.

## What this run does NOT claim (§9 — explicit non-claims)

- **Provider replacement is not claimed.** Workers are scripted
  processes with distinct identities; two genuinely distinct model
  providers are not exercised. Provider-level composition is marked
  separately **unproven**. Adjacent existing evidence:
  APPROVED_JOB_LIVE_001 (real Claude→Codex, no revocation).
- Trust-root succession is not claimed to occur in this run (AC-09 via
  reuse, stated).
- No production deployment safety, payment safety, or production key
  custody. Simulated money only.
- No cross-machine revocation propagation.
- No "industry standard / unhackable / production proven" language.

## Reproducibility

Fixed inputs: the mandate text above, the deterministic key derivation,
the frozen acceptance criteria. Anyone cloning the harness repository
and running the documented command must obtain the same decisions
without trusting this document's prose. Raw run artifacts (run log,
all submitted attempts, receiver decisions, export bundle, verifier
report) are preserved with SHA-256 hashes in RESULTS.md.
