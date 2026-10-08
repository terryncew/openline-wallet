# Buyer and selected evidence validation

The Exchange suite now runs 54 tests: original 43 plus 11 buyer/evidence
regressions, all passing. From the Wallet repository with Wallet installed:

```bash
PYTHONPATH=demo/agent-exchange-001 python -m unittest discover \
  -s demo/agent-exchange-001/tests -v
```

The new checks execute actual CLI review/authorization/status/completion/
selected export/revocation/worker withdrawal, including fractional expiry.
They cover changed inputs, revoked listings, insufficient budgets, invalid
expiry, approval digest mismatch, repeat approval, source preservation,
revoked delegates, and replacement identities that have no inherited grant.
They preserve the original receiver/settlement assertions.

The unmodified root suite has 196 tests: 189 passed and seven optional pinned
RRSI tests skipped. Five tests initially could not write to the cloud home;
one additionally required Codex CLI 0.153.0. With the locally installed pinned
CLI on PATH and a runner redirecting only Python `Path.home()` to
`/workspace/setup-artifacts/test-home`, all remaining tests pass. No assertions
or original tests were modified. See the paired Bureau PR's `VALIDATION.md`
for the exact runner and complete connected experiment results.

Wallet's provider-switch demo, signed bundle verification, wheel build,
provider-effect fixture and frozen predecessor reappraisal passed. The
connected Bureau suite has 36 passing tests (21 allocation/baseline plus 15
subordinate reciprocal-sharing regressions), plus actual browser interaction
validation and 7/7 public evidence bundle validation. All new transactions
use local controlled workers and SIM_USD; zero paid calls and real payments.
