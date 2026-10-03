# AUTHORITY-CONTINUITY-001 results

## Classification

**PASS** (apparatus run 007). Runs 001–006 are preserved under `failed-runs/`
as **INCONCLUSIVE_APPARATUS** with their raw outputs where any existed and exact
defect explanations. The repairs changed only apparatus bookkeeping, validation
ordering, and syntax; no frozen criterion or acceptance rule changed.

The run establishes worker-authority continuity across replacement: the same
approved mandate continued from A to B only after an explicit owner-signed
successor grant; A could not act after revocation; B inherited no authority;
A's receipt remained intact; and the export-only verifier agreed with all 15
recorded receiver decisions.

## Frozen inputs and execution

- Preregistration SHA-256: `3807bc088bc0236e2ce869018f9355c05581fa6082c3f948daddab90adb62e1a`.
- Conformance profile SHA-256: `2f96264c7dbc92cf13587ab932eb4d14aa876f6cfce6950d2f96909f3efe0c26`.
- Frozen handoff/source commit recorded by run: `a75c1405de606bfe877d17265a73d9ebdac1dc25`.
- Commands: `python proofs/authority-continuity-001/run.py` → `PASS`;
  `python -m unittest proofs/authority-continuity-001/test_authority_continuity.py -v`;
  standalone `verify.py --bundle ... --profile ...` → `PASS`.
- Repository suite after `python -m pip install -e '.[mcp]'`: 258 passed, 8 skipped, 11 failed because the unrelated coordinator proof requires an unavailable `airlock` module.
- Exact environment is machine-readable in `artifacts/environment.json`.

## Negative controls

| Control | Outcome | Evidence |
|---|---|---|
| C1 stale A after revocation | REFUSED (`grant_revoked`) | export run log |
| C2 byte-exact replay | REFUSED (`replayed_authorization`) | export run log |
| C3 B before grant (no grant and A's grant) | REFUSED | export run log |
| C4 valid signature, wrong worker | REFUSED | export run log |
| C5 valid signature, wrong job | REFUSED | export run log |
| C6 tampered historical receipt | REFUSED (`receipt_signature_invalid`) | export run log |
| C7 successor self-install | REFUSED (`authority_not_rooted_at_owner`) | export run log |

All refusals left both the receipt chain and ledger unchanged.

## Conformance

| Check | Result | Source |
|---|---|---|
| AC-01 | PASS | C7 refusal, produced by this run |
| AC-02 | PASS | A phase 1 and B phase 2 acceptance, produced by this run |
| AC-03 | PASS | REV-A followed by C1 refusal, produced by this run |
| AC-04 | PASS | C2 refusal, produced by this run |
| AC-05 | PASS | C3 and C4 refusals, produced by this run |
| AC-06 | PASS | signed G-B and chained B receipt, produced by this run |
| AC-07 | PASS for runtime/worker replacement | pre-replacement receipts reverified after replacement, produced by this run; provider replacement unproven |
| AC-08 | PASS | isolated export-only verifier, produced by this run |
| AC-09 | PASS only by preregistered reuse | separately frozen TRUST-ROOT-SUCCESSION-001 evidence; not produced or reproduced here. C7 supplies only the worker-level analogue |
| AC-10 | PASS | append-only chain and tamper refusal, produced by this run |

## Claim ceiling and limitations

Supported: worker-authority continuity for this deterministic, simulated-money,
single-machine experiment. Unsupported: live provider replacement, trust-root
succession in this run, production deployment or key custody, external receiver
adoption, payment safety, cross-machine revocation propagation, and universal
framework compatibility. AC-09's prior frozen evidence is referenced exactly as
the preregistration directs; this repository handoff does not contain that
evidence, and this run does not manufacture it.
