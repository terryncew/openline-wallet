# Run 004 — INCONCLUSIVE_APPARATUS (audit correction)

The harness and verifier both used request `special` labels to select the C2,
C6, and C7 refusal paths. Although the controls were real artifacts, relying on
a prover-supplied label meant export verification did not independently derive
those decisions from artifact state. This violates S11 and is classified
INCONCLUSIVE_APPARATUS, not PASS. Raw outputs are preserved. Run 005 removes
those label-dependent paths.
