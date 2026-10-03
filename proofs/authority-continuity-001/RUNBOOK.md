# AUTHORITY-CONTINUITY-001 runbook

## Preconditions

Use Python 3.11+ with the repository dependencies installed. No credentials,
network access, paid API, or model call is used. Confirm that the frozen
preregistration and profile hashes match their recorded full hashes.

## Reproduce

From the repository root:

```sh
python proofs/authority-continuity-001/run.py
python -m unittest proofs/authority-continuity-001/test_authority_continuity.py -v
python proofs/authority-continuity-001/verify.py \
  --bundle proofs/authority-continuity-001/export \
  --profile proofs/authority-continuity-001/frozen/OPENLINE-AUTHORITY-CONTINUITY-v0.1.md
sha256sum -c proofs/authority-continuity-001/SHA256SUMS.txt
```

`run.py` deletes and regenerates only the current `artifacts/` and `export/`
directories. It never changes `frozen/` or `failed-runs/`. Worker A, pre-grant
Worker B, and authorized Worker B execute in subprocesses with distinct
temporary homes. The verifier is copied to a fresh directory and invoked with
`python -I`, a clean HOME and environment, a read-only export copy, and a
read-only frozen profile copy.

## Outputs

The complete portable input is `export/`; private keys and runtime state are
not exported. `artifacts/` contains the classification, environment, frozen
hash check, and isolated-verifier report. `failed-runs/` permanently preserves all six inconclusive apparatus attempts,
their available raw outputs, and exact defect explanations.
