# AGT-EXIT-COLD-001

Status: **preregistered external comparator — no production feature change**

Pinned revisions:

```text
OpenLine Wallet  24c92b6588410a5e3537bdccb2f0cefda387b2a5
Microsoft AGT    0533ceaf6c5b0975bfc71bff42f6ccd2d34c8adf
```

## Question

Can Microsoft Agent Governance Toolkit preserve independently usable **current authority**
across both a model-provider exit and a governance-control-plane/status-source exit,
while refusing to call an unsafe action revocation-protected when the stopping budget
cannot beat consequence?

The comparison deliberately separates two questions that are easy to blur:

**7a — historical receipt portability.** Can a past AGT receipt be verified outside
AGT? Current AGT source and documentation make this a likely low-signal pass: its MCP
governance receipts are Ed25519-signed and hash-chained and are designed for offline
third-party verification.

**7b — current standing portability.** After authority changes, can an independent
receiver authenticate which state is current without depending on the governance
status source/control plane being exited?

A signed historical `allow` receipt is not evidence that the authority is still live.

## Source inspection before build

The selected AGT MCP receipt adapter evaluates Cedar policy, creates a signed governance
receipt, stores it, and executes the tool only when the decision is `allow`. Its signed
payload binds the agent DID, tool, argument hash, policy id, decision, timestamp, session,
and previous receipt hash. It is historical decision evidence; it is not itself a current
authority-state head.

AGT also has real revocation mechanisms, and this experiment tests them rather than
pretending they do not exist:

- `CredentialManager` tracks scoped short-lived credentials and local revocation.
- `RevocationList` can persist revoked DIDs to JSON.
- `ExternalJWKSProvider` checks token expiry, consults a revocation endpoint, denies
  revoked keys, and fails closed when revocation status cannot be fetched.
- The comparator sets `revocation_cache_ttl_seconds=0`, a stronger ordinary setting,
  so the result cannot be manufactured from AGT's default cache window.

The question is not “does AGT revoke?” It does. The question is whether current standing
remains independently usable after the live governance status source itself is gone.

The pinned AGT main has a globally failing monorepo CI run (`34460169275`). That is not
counted as comparator evidence. The proof reruns the upstream tests for the exact
standalone MCP-receipt package it exercises. Setup/import/test failure becomes
`INCONCLUSIVE_AGT_ENVIRONMENT`, not an OpenLine win.

## Harness repair R1

The first branch execution is preserved as an environment-inconclusive receipt. On all
observed matrix legs, the pinned AGT receipt package ran **63 passing tests and one
failing test**: `TestSigning.test_signing_failure_raises`. The test still expects
`RuntimeError`, while the pinned adapter now deliberately raises its typed
`ReceiptSigningError` on receipt-signing failure. The failure is upstream
test/implementation drift in an invalid-key exception-type assertion; it does not
exercise any comparator arm.

R1 does not modify AGT and does not erase that failure. Every run still executes and
archives the full upstream receipt suite. Only when the failure matches that exact frozen
signature does R1 run a second scoped preflight excluding **only**
`test_signing_failure_raises`. If that scoped suite is not green, the experiment remains
`INCONCLUSIVE_AGT_ENVIRONMENT`. The original preregistration remains unchanged; this
repair is separately frozen in `harness-repair-001.json`.

## Frozen arms

### 1. Model-provider exit

Run one logical `DeployStaging` job through two provider-labelled workers using the same
AGT Cedar policy, agent DID, signer, and shared receipt chain. Both must execute and the
chain must verify.

### 2. Criterion 7a — historical receipt outside AGT

Export AGT receipts. A separate verifier imports no AGT code; it pins the Ed25519 public
key, reconstructs the documented canonical payload, verifies signatures and hashes, and
checks chain continuity.

Expected: low-signal pass.

### 3. Criterion 7b — current authority after governance status-source exit

For file-backed `RevocationList`, freeze a pre-revocation state, a post-revocation state,
and a tampered post-state with the revocation removed. The test asks whether ordinary
serialized state is self-authenticating and fresh, not whether it is useful local
persistence.

Then test the stronger online path, `ExternalJWKSProvider`, with revocation cache TTL zero:

1. valid signed token + empty revocation list → accept;
2. same token + revoked `kid` → deny;
3. same token + revocation endpoint unavailable → fail closed.

Fail-closed on (3) is good safety behavior. It is not continuity of current authority
across status-source exit: the receiver can refuse, but cannot authenticate which
authority is current from a portable state artifact.

### 4. Unsafe timing

Use the same unsafe shape frozen by `AUTHORITY-IN-TIME-001`:

```text
detect       10 ms
propagation  60 ms
receiver     20 ms
stop         10 ms
uncertainty  10 ms
-----------------
required    110 ms

consequence horizon = 100 ms
margin              = -10 ms
```

OpenLine must refuse `REVOCATION_PROTECTION`.

For AGT, search only the selected ordinary receipt, credential, and external-JWKS paths
for a comparable consequence-horizon/stopping-budget admission surface. If none exists,
record `NO_COMPARABLE_PROTECTION_CLAIM_SURFACE`. That is an unearned comparator criterion,
not an AGT security defect.

## Interpretation rule

The separator is not “OpenLine has signatures and AGT does not.” AGT has strong signatures.
It is not “AGT cannot revoke.” AGT has multiple revocation paths.

The question is:

> After a newer authority state exists and the governance status source being replaced
> is gone, what can the receiver independently authenticate **now**?

OpenLine's arm requires the receiver to receive a newer signed Wallet bundle. Once it
does, `ReferenceGate` pins the principal root, enforces freshness and monotonic history
heads, rejects the older bundle, stops the revoked mandate, and admits the successor.
This does **not** claim global dissemination or discovery of a hidden newer revocation.

## Run

CI checks out pinned Microsoft AGT into `_external/agt` without modifying it, installs
the standalone AGT MCP-receipt integration and selected direct-source dependencies,
reruns its upstream receipt tests, executes the comparator, independently verifies the
artifacts, and uploads diagnostics.

```bash
python proofs/agt-exit-cold-001/run.py --self-test
python proofs/agt-exit-cold-001/run.py --agt-root _external/agt --output agt-exit-cold-artifacts
python proofs/agt-exit-cold-001/verify.py agt-exit-cold-artifacts
```

No Wallet production source is modified.
