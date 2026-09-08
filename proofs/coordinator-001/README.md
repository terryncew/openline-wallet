# COORDINATOR-001 — controlled settlement reconciliation

Frozen scope: one maintenance job, two separately credentialed scripted signers,
real Wallet exact-action mandates and receiver evaluation, real Airlock protected-path
and command checks, a trusted local coordinator, and simulated integer money.
This directory is an experiment, not a supported payment service or marketplace.
It adds no Wallet runtime API and makes no changes to Airlock.

## Question and stopping rule

Can this controlled arrangement preserve an agreed job and settle it once when
its coordinator dies after the provider commits the transfer but before the
coordinator records the acknowledgment? Stop when that case passes with hostile
controls or identifies a limitation. No discovery, reputation, exchange, DAO,
real funds, external agent accounts, or model API spend.

## Boundaries

Buyer and worker have separate Wallet principals and agent keys. Each signer runs
in its own subprocess and is given its own agent key, a public bundle, and a
challenge. Owner keys stay outside the signer directories. The fixture operator
controls both owners. This is **not independent external operation**, OS credential
isolation, or resistance to an agent that can read arbitrary same-user files.
Signing processes are deterministic actors, not live language models.

The agreement binds the repository path and base SHA, task, protected checks,
parties, deadline, amount (10,000 simulated cents), currency and provider key.
Both parties authorize its digest before submission. Submission binds the exact
candidate; buyer acceptance binds both candidate and evaluator evidence.
Airlock passing only makes the result eligible. It never authorizes settlement.
A buyer's exact acceptance authorizes the frozen simulated obligation; no separate
release role or cancellation of already-accepted obligations is implemented.

The coordinator, evaluator filesystem, payment process and databases are trusted.
Parties have no database-write API. A database administrator could rewrite local
state; retained signatures make changed signed statements detectable, not storage
immutable. Airlock's `protected_files_check`, `WorktreeSandbox`, and `run_checks`
are used directly. This does not exercise `airlock init`, tournament selection,
or claim that Git worktrees sandbox malicious code. The target fixture is
intentionally buggy and its protected target check fails before the patch.

An accepted job is durably recorded as PENDING before provider contact. The bank
atomically debits, credits, and stores a signed result under the agreement digest.
Lookup precedes retry, and every retry uses that same ID and payload. An unavailable
or invalid provider response remains PENDING. Even a negative lookup is safe only
because this simulator has durable atomic deduplication with no key expiration.
Real providers may not offer that contract. Losing either database, Byzantine
providers, distributed consensus and reconciliation after retention expiry are
outside this proof. The bank's subprocess transport is coordinator-owned; it is
not a publicly authenticated payments API.

The test deliberately gives the buyer enough funds for **two** payouts. Its unsafe
negative control retries one logical obligation with a fresh payment ID and pays
twice. The bounded coordinator must leave one transfer, 10,000 cents per account,
and 20,000 cents conserved after real exit code 73, restart and repeated retries.
Observed revocation is retained across rejected actions and coordinator restart.
This is future-use rejection, not cancellation or closure of an accepted payment.

## Reproduce

From the Wallet repository root, with Python 3.11–3.13 and Git:

```sh
git clone https://github.com/terryncew/openline-airlock.git ../coordinator-airlock
git -C ../coordinator-airlock checkout fb02207f3ac561368beeabf9ff168076bf828824
python -m pip install -e . -e ../coordinator-airlock
python proofs/coordinator-001/verify.py proofs/coordinator-001/frozen
python proofs/coordinator-001/reproduce.py --output coordinator-artifacts
python proofs/coordinator-001/verify.py coordinator-artifacts
```

Use a new output directory each run. Reproduction refuses a dirty or incorrectly
pinned Airlock checkout. The separate Actions workflow repeats this path on all
three Python versions and uploads diagnostics even after failure. Existing Wallet
CI remains intact. No production mutation, payment credentials or secrets needed.

## Evidence and falsifiers

`frozen/result.json` pins source and evidence hashes, source revisions, test names,
and the actual local runtime. `evidence.json` retains the agreement, Wallet bundles,
holder presentations, signed gate decisions, Airlock command evidence, chained
coordinator events, signed payment result and final simulated balances. Private
keys and temporary databases are not exported. Both parties can retain identical
copies of this public history; network delivery and acknowledgment are not tested.

The offline verifier checks source/file integrity, Wallet mandate and action
bindings, contract state transitions, evaluation-to-acceptance binding, provider
signature and payment binding, and conservation. It also rejects four semantic
mutations after bypassing the outer hash check. Fixture roots and receiver keys
are self-attested: signatures establish consistency, not independent endorsement
or proof that tests really ran. Reproduction supplies the executable check.

Fail the experiment if either party can accept altered terms, a worker can accept
its own result, passing checks alone pays, protected-check edits pass, a forged or
misbound payment closes the job, interruption loses uncertainty, or retries create
two transfers. An external buyer and worker, hostile process isolation, or a real
provider require a separate earned experiment; this receipt claims none of them.
