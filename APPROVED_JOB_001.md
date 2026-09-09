# APPROVED-JOB-001 — owner-approved work survives worker replacement

This proof asks one narrow question: can an owner approve a software-maintenance job once, let one worker leave a bounded handoff, replace that worker, and still evaluate the successor against the same approved agreement?

It reuses the existing boundaries. Wallet supplies subject-specific authority and revocation. Airlock supplies protected-path and command evaluation. APPROVED-JOB-001 adds only a durable job record and a bounded handoff binding.

The fixture has an ordinary test plus one approved requirement the ordinary test misses: `total(items)` must return the right sum without mutating the caller's list. Worker A fixes the obvious numeric behavior but sorts the list in place, then records its commit and unresolved requirement. Its authority is revoked. Worker B receives fresh authority and must continue from the recorded handoff commit.

The good continuation removes the mutation and must become `ELIGIBLE`. The bad continuation still passes the ordinary test but must be `REJECTED` by the approved requirement. In the bad arm Worker B first proposes deleting that requirement; the proposal is retained but cannot change the agreement or the decision. Candidate edits to the approved check are rejected before execution.

The proof also requires an exact handoff binding, blocks agreement replacement after approval, rejects an old revoked worker, and keeps the event chain on one job digest.

This is not full context portability. The successor receives a saved commit plus a small handoff record, not the old model's complete chat history, hidden state, memory, credentials, or every human intention. Freezing an incomplete requirement preserves the omission. No payment, marketplace, live model call, merge, or deployment is part of this proof; COORDINATOR-001 remains the separate settlement result.

Reproduce from Wallet main with Python 3.11–3.13 and the pinned Airlock checkout:

```sh
git clone https://github.com/terryncew/openline-airlock.git .deps/airlock
git -C .deps/airlock checkout fb02207f3ac561368beeabf9ff168076bf828824
python -m pip install -e . -e .deps/airlock
python proofs/approved-job-001/run.py --output approved-job-artifacts
python proofs/approved-job-001/run.py --verify approved-job-artifacts
```

The dedicated GitHub Actions workflow runs the same proof on Python 3.11, 3.12, and 3.13. Existing Wallet CI remains separate and must stay green.
