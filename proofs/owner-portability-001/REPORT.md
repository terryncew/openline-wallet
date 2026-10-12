# OWNER-PORTABILITY-001

**Local proof: PASS. Full repository suite: one unrelated environment/tool-version
failure remains; seven optional external-adapter tests skipped.**

Implemented on `experiment/owner-portability-001`, from the existing `main`
checkout. Prepared for the user-authorized push of this experiment branch;
nothing is merged, deployed or published to production. The independent-review
addendum is [REVIEW.md](REVIEW.md), with baseline reproduction, receipt audit,
supplemental controls and an [OAuth/token-exchange comparison](OAUTH_COMPARISON.md).
The preregistration is
[PREREGISTRATION.md](PREREGISTRATION.md); it was written before implementation
and execution. This is self-attested preregistration, not a trusted timestamp.

## Decisive results

The separate offline public verifier reports:

```json
{"verdict":"OWNER_PORTABILITY_LOCAL_PASS","transactions":8,
 "permitted":3,"refused":5,"wallet_events":5,"gate_receipts":4,
 "owner_decisions":4,"independent_receivers_verified":2}
```

| Step | Receiver | Agent | Decision | Protected simulated effects |
|---|---|---|---|---|
| Approved v1 reservation | Website A | A | PERMITTED | 1 |
| Advertising sharing required | Website B | A | REFUSED: ADVERTISING_SHARING_REFUSED | 0 |
| Wallet not recognized | Unknown website | A | REFUSED: WALLET_NOT_RECOGNIZED | 0 |
| Receiver declines arrangement | Declining website | A | REFUSED: ARRANGEMENT_DECLINED | 0 |
| Owner revoked A | Website A | A | REFUSED: MANDATE_REVOKED | 0 |
| Owner grants narrowly scoped successor | Website A | B | PERMITTED | 1 |
| Website A changes to v2 | Website A | B | REFUSED: CONSENT_BINDING_MISMATCH | 0 |
| Owner explicitly approves v2; new exact mandate | Website A | B | PERMITTED | 1 |

Both independently keyed, independently configured A/B Gates admit the **same
signed owner-held bundle**. Signed admission records bind its exact hash. B can
verify the authority and still refuse the arrangement. A and B have different
signed terms and separate receiver state; they share no authorization database.

The owner signs an explicit B refusal linked to B's signed refusal transaction.
The original mandate event, root-signed revocations, successor grants, original
receipt and subsequent Gate receipts survive in the final bundle. Signed
website transactions preserve policy refusals outside the Wallet's existing
Gate-receipt schema. The owner-signed evidence packet binds the complete
experiment transcript; no existing schema is extended or repurposed.

## What was added

- `src/openline_wallet/owner_portability.py`: small policy adapter, offline demo,
  and read-only public verifier, accessible through `python -m`.
- `tests/test_owner_portability.py`: 25 tests, including six re-signed semantic
  forgery cases in one test. The review adds seven direct receiver control tests
  in `tests/test_owner_portability_review.py`; see the addendum for current counts.
- This proof directory: preregistration, synthetic signed evidence, signed
  adversarial refusals, exact test logs, source hashes and this report.

Wallet, ReferenceGate, existing crypto helpers, security contracts, dependency
manifest and existing tests remain unchanged. The adapter calls the existing
`admit_bundle`, `issue_challenge`, `create_presentation` and `evaluate` machinery.
It does not duplicate mandate issuance, revocation, holder authentication or
challenge authorization. Its additional policy checks decide whether a website
accepts the owner's proposed arrangement before any simulated effect.

## Preference and consent boundary

The preference document is the first root-signed record in an append-only,
hash-linked owner decision log. It records no advertising sharing, explicit
approval for new financial obligations, and no automatic changed-terms consent.
Its signature proves that the pinned synthetic owner asserted the constraints;
it is **not authorization or blanket contractual agreement**. The existing
Wallet continues to declare `wallet_policy_authority=NONE` and the Gate continues
to own action authorization.

An approval is a separate root-signed owner decision binding the website,
signed terms hash, version, exact action, financial approval flag and expiry.
The mandate scope also binds the exact signed terms and operation through a
SHA-256 action identifier. A signed withdrawal names the exact approval hash.
Receivers retain observed withdrawals and valid revocations even on refused
requests, rejecting attempts to roll back the observed history.

Agents sign only holder presentations. A presentation cannot approve terms.
The B continuation receives B's private subject key and public documents only;
it receives neither A's key nor an owner key. The controller owns the Wallet
and signs owner decisions. This same-process fixture demonstrates input/key
separation, not OS isolation or protection from a compromised controller.

## Tests and preserved failures

| Check | Exact result |
|---|---|
| New suite | 25 passed, 0 failed, 0 skipped |
| Relevant combined suite (excludes optional external RRSI and live-host sandbox modules) | 207 passed, 0 failed, 0 skipped; includes 182 existing + 25 new tests |
| Original implementation complete repository suite | 221 discovered: 213 passed, 1 failed, 7 skipped |
| Offline demo and separate verifier process | PASS: 8 transactions, 3 effects, 5 refusals |
| Dependency consistency, repeated editable install, wheel build | Passed |
| Existing platform-exit demo and bundle verification | PLATFORM_EXIT_CONTINUITY_ENFORCED / EVIDENCE_VALID |
| Existing self-attested provider-effect reproduction/verification and frozen predecessor evidence | Passed during initial environment setup |

The original implementation's sole complete-suite failure is the existing
`test_real_cli_creates_helper_in_preflight_home`: it requires Codex CLI 0.153.0,
while this host supplies 0.159.2. No provider was called by that test; it checks
`--version` and helper creation. It is unrelated to the local Wallet/Gate proof.
The unchanged baseline commit `c46e82a324199050239540f1825a3b0c942e4053`
reproduces exactly this failure: 196 tests, 188 passed, 1 failed, 7 skipped.
The failing test and preflight helper are byte-identical to that baseline.
The version assertion runs before the helper-alias assertions, so helper creation
on 0.159.2 is not established by this failed test. See
[baseline-review-findings.json](baseline-review-findings.json) for exact hashes,
traceback and reproduction, and [REVIEW.md](REVIEW.md) for review-revision counts.

The seven skipped RRSI cases require a separately pinned external repository;
this proof needs neither that repository nor a real provider host. Their exact
unexecuted coverage is enumerated in the review addendum; they are not counted
as passes and no dependency was fetched to change their status.

The first pre-implementation existing-suite run had five errors because sandbox
fixtures used the read-only `/home/agent`. The full reruns used a writable home
path for test fixtures, ultimately through a test-only `Path.home` override.
The first new-suite run had one expectation failure: a forged nested event was
rejected as `BUNDLE_SIGNATURE_INVALID` at the outer bundle signature, rather
than the test's expected event-signature failure. Only the expected diagnostic
was corrected; the rejection requirement was retained.

Failure logs remain in `logs/existing-suite-initial.log`,
`logs/adversarial-initial.log`, and `logs/full-suite-final.log`; passed runs are
in `logs/adversarial-final.log` and `logs/relevant-suite-final.log`. Initial
successful story evidence remains in `evidence/`; the final run is in
`final-evidence/`. Final adversarial signed refusal packets are in
`adversarial-evidence-final/` (earlier packets retained separately).

Negative cases cover unsigned preferences, forged authority, wrong holder,
agent self-approval, replayed/expired challenges, tampered/changed terms,
missing/withdrawn/expired consent, cross-site/action consent, financial approval,
advertising refusal, unknown wallet, declining receiver, owner-log forks,
stale bundles and revocations observed during a policy refusal. Every rejected
case checks no new effect. The verifier also rejects packet tampering, wrong
pins, re-signed wrong effect bindings, fake revoked-agent success, removed
receipts/refusals, substituted admission bindings and duplicate effects.

## Portability versus the site-specific baseline

The baseline is intentionally a simple pair of local token registries: A accepts
`token-a`; B refuses `token-a`; B accepts its separately registered `token-b`.
It is executed in the demo and produces three recorded results. This is a
comparison with that toy configuration, not a benchmark or model of OAuth,
Poppy, federated login or their possible portability extensions.

| Item | Portable in this proof? | Website cooperation still needed |
|---|---|---|
| Owner identity, signed mandate/revocation history | Same evidence verified at A and B | Pin/recognize the owner root and import the Wallet protocol |
| Signed preferences and refusal history | Carried unchanged as owner-held evidence | Understand and choose to accept the constraints |
| Agent replacement | B's own key works under a new owner mandate | Admit the current bundle; enforce revocation and scope |
| Approval of terms | Carried as evidence, but bound to one site/version/action | Offer compatible terms and check exact consent |
| Authorization decision | Each receiver retains its own decision | May refuse for any supported policy, including non-recognition |
| Site account, login, contractual validity | Not demonstrated | Outside this fixture |

Nothing here proves OAuth or Poppy inherently nonportable. The portable object
is owner-held, signed authority and decision evidence; another website's
cooperation, policy, permissions and exact consent are not magically inherited.

## Independent verification and limits

`verify` runs without the owner's keys, agent keys, persisted receiver state,
network or live services. Public pins are passed separately; it verifies owner
and receiver signatures, wallet event chains, admission equivalence, approval
bindings, revoked status, successful holder signatures/scopes, replay uniqueness,
simulated effect links, exact story outcomes and preservation of recorded history.
It reconstructs the experiment-specific signed transcript; it is not a new
production verifier protocol or a general legal-consent engine. The review
addendum documents its policy-refusal head-tracking limitation; this verifier
must not be used as a general replay engine for arbitrary transaction sequences.

The included trust-anchor file is self-attested fixture configuration. A real
verifier needs trusted owner/receiver identities obtained independently; replacing
both evidence and all pins defeats unauthenticated fixture trust. Signed receiver
statements attest local decisions; they do not prove external events, website
honesty, legal agreement or absence of undisclosed effects. The final packet binds
this recorded transcript, not every event that could have happened elsewhere.

Revocation/withdrawal rollback protection starts after a receiver observes the
new signed history. An offline receiver cannot discover a withheld newer record.
Wallet expiry bounds stale-history exposure; this experiment establishes no
network propagation, production storage, restart recovery, distributed timing,
real account transfer or enforceable cross-site agreement. No real identities,
logins, terms acceptance, websites, production keys, deployment or paid APIs
were used. Effects are signed descriptions of synthetic local reservations.

## Reproduce

From the existing checkout, use Python 3.11+ (validated here on 3.12.14):

```bash
cd /workspace/openline-wallet
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[mcp]'
.venv/bin/python -m pip check
.venv/bin/python -m unittest discover -s tests -p test_owner_portability.py -v
# Pick a NEW directory; refusing reuse preserves prior evidence.
.venv/bin/python -m openline_wallet.owner_portability run /tmp/owner-portability-review
.venv/bin/python -m openline_wallet.owner_portability verify \
  /tmp/owner-portability-review/evidence.json \
  --trust-anchors /tmp/owner-portability-review/trust-anchors.json
# Verify the retained final packet:
.venv/bin/python -m openline_wallet.owner_portability verify \
  proofs/owner-portability-001/final-evidence/evidence.json \
  --trust-anchors proofs/owner-portability-001/final-evidence/trust-anchors.json
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip wheel . --no-deps --wheel-dir /tmp/owner-portability-wheels
```

Installation uses the project's existing declared dependencies; no lockfile
exists. The installation may fetch packages; all proof execution is offline.
Record-specific signatures, challenges, synthetic keys and times vary per run;
the exact outcomes and verifier result must match. Installed versions are recorded
in `tool-versions.json`. To preserve adversarial evidence, set
`OWNER_PORTABILITY_TEST_EVIDENCE` to a new directory when running the new suite.

For the relevant combined check used here:

```bash
.venv/bin/python - <<'PY'
import unittest
from pathlib import Path
suite = unittest.TestSuite()
for path in sorted(Path('tests').glob('test_*.py')):
    if path.name not in {'test_approved_job_live_sandbox.py', 'test_rrsi_promotion_adapter.py'}:
        suite.addTests(unittest.defaultTestLoader.discover('tests', pattern=path.name))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
```

For the full suite on this host's read-only home:

```bash
mkdir -p /workspace/onboarding-test-home
.venv/bin/python - <<'PY'
from pathlib import Path
import unittest
from unittest.mock import patch
with patch.object(Path, 'home', return_value=Path('/workspace/onboarding-test-home')):
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('tests'))
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
```

This still returns failure for the documented CLI-version mismatch; it does not
skip or weaken that assertion. No real provider run is required for this proof.

Reusable environment `install_script` and `start_skill` were saved as a draft
using the [cloud-environment-onboarding:setup skill](skill://plugin_connector_1p_ed5feb9070a08191b08c81c47947bc16/setup/SKILL.md).
The install script was executed again successfully. Startup instructions use the
existing isolated checkout and describe CLI/test readiness; no permanent service
is required. Draft saving did not publish the environment. The implementation,
evidence, limitations and saved instructions are ready for review.
