# Run 002 — INCONCLUSIVE_APPARATUS (audit correction)

The harness printed PASS, but post-run test review found that C4 and C5 were
refused as `grant_not_admitted` before the receiver evaluated worker and mandate
binding. Thus those controls did not isolate the frozen binding checks. This is
a false-positive apparatus classification and is conservatively corrected here
to INCONCLUSIVE_APPARATUS. The raw output is preserved unchanged. Run 003
reorders validation without changing any acceptance criterion.
