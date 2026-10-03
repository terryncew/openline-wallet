# Run 001 — INCONCLUSIVE_APPARATUS

The isolated verifier returned `FAIL` at negative-control coverage even though
C1 through C7 executed and were refused. The verifier incorrectly treated the
separate frozen-sequence label `S7` as a negative-control identifier because it
added every non-null `control` field to the control set. This is an apparatus
bookkeeping defect, not an invariant violation. The run was frozen before the
verifier was repaired; its raw export and reports are preserved unchanged here.
