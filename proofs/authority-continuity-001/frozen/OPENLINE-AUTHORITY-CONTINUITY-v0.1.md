# OPENLINE-AUTHORITY-CONTINUITY-v0.1 — Conformance Profile

**Version:** 0.1
**Status:** FROZEN (see FREEZE-v0.1.md). No changes without a version bump.
**Date frozen:** 2026-10-03

## Purpose

This profile defines, in technology-neutral terms, what it means for any
implementation to demonstrate that **owner authority and historical proof
survive worker/provider replacement while revoked or stale workers lose
authority immediately**.

It is a receiver-side conformance profile, not a framework. Any
implementation — including a hostile third-party implementation built
without knowledge of OpenLine — must be able to pass or fail each check.
OpenLine is one implementation under test, not the definition of the test.

## Normative vocabulary

These terms are defined here and used with exactly these meanings in the
checks below. No implementation-specific machinery (no named products,
protocols, or classes) appears in any normative requirement.

- **Owner**: the principal holding root authority over a job. Identified by
  a public key the receiver pins as the **trust root**.
- **Mandate**: the owner-approved job: what is to be done plus the
  acceptance criteria. Identified by a stable **mandate identifier**
  (e.g., a digest of the approved job description).
- **Grant**: a signed, tamper-evident authorization artifact issued by the
  owner (or a holder of owner-delegated authority) to one specific
  **worker** for one mandate. A grant carries: issuer, subject worker
  identity (public key), mandate identifier, scope limits, a unique grant
  identifier, and either a validity window or a monotonic sequence number.
- **Worker**: the acting party. Identified by its public key. Workers are
  untrusted by the receiver.
- **Receipt**: a signed, tamper-evident record of a consequential action,
  chained to prior receipts so the full history is verifiable and
  ordered.
- **Receiver**: the independent party that accepts or refuses
  consequential actions. The receiver trusts the pinned trust root and
  its own verification logic; it trusts no worker and no provider.
- **Revocation event**: an owner-signed, tamper-evidently sequenced record
  ending a grant's validity. Attempts are judged against the revocation
  point: an attempt already underway when revocation is recorded is out
  of scope; the *next* consequential attempt must be refused.
- **Succession event**: an owner-signed record advancing the trust root
  (or a mandate) to a successor. A self-declared succession — signed by
  anyone other than the currently pinned trust root — is not a
  succession event.
- **Consequential action / effect**: an action that changes state the
  owner cares about (spending, deploying, mutating shared data,
  publishing). Proposals and drafts are not consequential.

## Verdict rule

An implementation **conforms** iff all ten checks pass as specified.
A check that cannot be executed for apparatus reasons records
**INCONCLUSIVE**, never a pass. A single failed check fails the profile.

## The ten checks

---

### AC-01 — Worker cannot self-authorize

**Intent:** authority originates with the owner; a worker's own signature
confers nothing.

**Input:** a consequential action request accompanied by an authority
artifact signed by the requesting worker itself (or by no owner at all).

**Receiver-observable condition:** the receiver traces the artifact's
signature chain and finds it does not terminate at the pinned owner
trust root.

**Expected result:** REFUSE. The action must not take effect, and no
receipt may attribute the mandate to the worker.

**Evidence emitted:** a machine-readable refusal record citing the
reason (authority not rooted at the owner).

**Exact falsifier:** the action takes effect, or the receiver accepts the
request without an owner-rooted grant.

---

### AC-02 — Valid authority accepted

**Intent:** legitimate, well-formed authority works.

**Input:** a consequential action request with a valid owner-signed grant:
correct worker identity, correct mandate identifier, within scope and
validity, trust root current, grant identifier unused.

**Receiver-observable condition:** signature chain terminates at the
pinned trust root; all grant fields check out; no revocation or
succession supersedes it.

**Expected result:** ACCEPT. The effect occurs, and a receipt is emitted
chaining this action to the prior receipts.

**Evidence emitted:** an acceptance receipt referencing the grant
identifier and chaining to the previous receipt.

**Exact falsifier:** a fully valid grant is refused, or the action is
accepted without emitting a chained receipt.

---

### AC-03 — Revoked authority refused on the next consequential attempt

**Intent:** revocation ends authority; the boundary is the next attempt,
not retroactive cancellation.

**Input:** (a) an owner-signed revocation event for worker A's grant,
recorded at sequence position T_rev; (b) a consequential attempt from A
at a later position T > T_rev.

**Receiver-observable condition:** the receiver compares the attempt
against the recorded revocation point.

**Expected result:** REFUSE. No effect from the post-revocation attempt.

**Evidence emitted:** the revocation record (with its sequence position)
and a refusal record referencing it.

**Exact falsifier:** the post-revocation attempt takes effect.

---

### AC-04 — Stale / replayed authorization refused

**Intent:** a captured valid authorization cannot be reused as if fresh.

**Input:** a byte-exact copy of a previously valid grant (or a grant
whose validity window has passed / whose sequence number has been
superseded), presented as a fresh authorization.

**Receiver-observable condition:** the receiver checks grant-identifier
consumption / sequence currency / validity window.

**Expected result:** REFUSE as replayed or stale.

**Evidence emitted:** a refusal record citing replay or staleness.

**Exact falsifier:** the replayed authorization is accepted as fresh.

---

### AC-05 — Replacement does not inherit authority

**Intent:** becoming "the replacement worker" confers zero authority by
itself.

**Input:** worker B (a new identity/key, presented as A's replacement)
attempts a consequential action on the mandate, presenting either A's
grant or no grant of its own.

**Receiver-observable condition:** B holds no owner-signed grant naming
B for this mandate.

**Expected result:** REFUSE. No receipt may attribute the mandate to B.

**Evidence emitted:** a refusal record; the receipt log shows no
B-attributed mandate action.

**Exact falsifier:** B's action is accepted on A's grant, on no grant, or
on any artifact not naming B.

---

### AC-06 — Explicitly authorized successor may continue

**Intent:** the owner — not the replacement event — authorizes
continuation.

**Input:** an owner-signed grant naming worker B for the same mandate
(mandate identifier unchanged, or an explicit, owner-signed successor
link between mandates); B performs the next step of the job.

**Receiver-observable condition:** B's grant is valid per AC-02 and
explicitly names B.

**Expected result:** ACCEPT. B-era receipts chain after A-era receipts;
the mandate identifier is unchanged (or the successor link is intact).

**Evidence emitted:** B's grant, acceptance receipts chaining B-era work
after A-era work, unchanged mandate identifier.

**Exact falsifier:** a properly authorized B is refused, or B's
acceptance rewrites, drops, or reorders A-era receipts.

---

### AC-07 — Provider replacement preserves receipts

**Intent:** changing the worker's provider or runtime must not damage
history.

**Input:** the worker's provider/runtime is replaced; the full
historical receipt set is presented to the receiver before and after.

**Receiver-observable condition:** the receiver re-verifies every
pre-replacement receipt's signature and chain position.

**Evidence emitted:** a verification report showing all pre-replacement
receipts verifiable and attributable to their era, before and after.

**Exact falsifier:** any pre-replacement receipt becomes unverifiable,
unattributable, or missing after the provider change.

---

### AC-08 — Independent verification

**Intent:** the proof travels without the prover.

**Input:** exported artifacts only — receipts, grants, revocation and
succession records, the owner public key. No access to the workers, the
providers, or the original runtime.

**Receiver-observable condition:** a third party, using only the exports,
re-derives chain validity, grant validity, revocation ordering, and the
accept/refuse decisions.

**Expected result:** the independent re-derivation agrees with the
original decisions on every check.

**Evidence emitted:** the independent verification report, including the
inputs hashed.

**Exact falsifier:** the independent run disagrees with any original
decision, or it requires state not present in the exports.

---

### AC-09 — Trust-root succession without successor self-install

**Intent:** only the current trust root can install its successor.

**Input:** (a) an owner-signed succession event naming a successor trust
root; (b) separately, a self-declared succession signed by the
would-be successor (not by the currently pinned root).

**Receiver-observable condition:** the receiver admits (a) only if
signed by the currently pinned root with a valid sequence; it evaluates
(b) against the same rule.

**Expected result:** (a) is honored — exactly one trust root is current
at all times, the old root's fresh authority is refused, pre-succession
receipts stay verifiable but are marked historical; (b) is refused.

**Evidence emitted:** the succession record, the self-install refusal,
and proof of atomic transition (no moment with zero or two current
roots).

**Exact falsifier:** a self-installed root is accepted; two roots are
current simultaneously; or the old root's fresh grants are still
accepted after succession.

---

### AC-10 — Append-only history

**Intent:** history can grow; it cannot be rewritten.

**Input:** the full receipt log before and after the replacement
sequence.

**Receiver-observable condition:** the receiver verifies the hash chain
over the entire log in both states.

**Expected result:** the post log equals the pre log plus appends only.

**Evidence emitted:** chained-hash verification over the full log,
before and after.

**Exact falsifier:** any pre-existing entry is modified, deleted, or
reordered.

---

## What this profile does not require

- Any specific cryptography, transport, programming language, or
  product. "Signed" and "tamper-evident" are the requirements; the
  mechanism is the implementation's choice.
- Any particular receiver architecture — only the observable
  accept/refuse behavior and the emitted evidence.
- That the checks run in the order listed, except where a check's input
  depends on an earlier check's output (noted per check; none do —
  each check is independently executable given its stated input).

## Neutrality stop rule (applied)

Each check above was reviewed against this rule: *if the check cannot
be stated without implementation-specific machinery, rewrite it until a
hostile third-party implementation could either pass or fail it.* No
check names a product, protocol, or class. The terms in "Normative
vocabulary" are the only shared concepts, and each is defined here.
