# OWNER-PORTABILITY-001 independent-review addendum

Prepared for review on `experiment/owner-portability-001`; the branch remains
unmerged. Only the specifically requested GitHub Git operations are permitted
external operations in this preparation. No provider/API calls, real-site
transactions, workflow dispatch, deployment or production effects were performed.
The original eight-transaction signed evidence is unchanged.

## Receipt audit

The machine-readable [review-authority.json](review-authority.json) contains
record hashes, verification checks and credential-binding findings. The audit
checks each website transaction against its separately pinned receiver key,
checks each existing Gate receipt, and binds each simulated effect to the exact
ALLOWED Gate receipt, action and approval. Policy refusals are website-signed
transaction records; they do not invent an action Gate receipt.

| Step | Site / agent / terms | Signed transaction outcome | Action Gate receipt | Effects | Transaction hash prefix |
|---|---|---|---|---:|---|
| 1 | site-a / agent-a / v1 | PERMITTED | ALLOWED | 1 | `65896df3674c` |
| 2 | site-b / agent-a / v1 | REFUSED: ADVERTISING_SHARING_REFUSED | none: policy refusal | 0 | `2decf728fbba` |
| 3 | unknown-site / agent-a / v1 | REFUSED: WALLET_NOT_RECOGNIZED | none: policy refusal | 0 | `a9b9565ef5f6` |
| 4 | declining-site / agent-a / v1 | REFUSED: ARRANGEMENT_DECLINED | none: policy refusal | 0 | `777504e7cee0` |
| 5 | site-a / agent-a / v1 | REFUSED: MANDATE_REVOKED | STOPPED | 0 | `9c1650d7658e` |
| 6 | site-a / agent-b / v1 | PERMITTED | ALLOWED | 1 | `c067e5f2fdeb` |
| 7 | site-a / agent-b / v2 | REFUSED: CONSENT_BINDING_MISMATCH | none: policy refusal | 0 | `52d233ae4c56` |
| 8 | site-a / agent-b / v2 | PERMITTED | ALLOWED | 1 | `4c65bb1ee110` |

Five recorded refusals have `effect=null`; the only three signed effects belong
to the three permitted Website A transactions. The supplement also checks no
effect-count increase on rejected requests. Signed records establish what this
local fixture records, not the absence of undisclosed effects on an external site.

## Independent receiver enforcement and key separation

A/B have different signing keys, separately signed terms, separate Gate
instances, and independent in-memory admission/challenge/replay state. Each
signs admission of the exact same bundle. The recorded Website B transaction
then refuses its advertising-sharing terms at policy evaluation, **before an
action Gate receipt**. Its lack of an action receipt is deliberate refusal, not
proof that it authorized a protected action.

Separate supplemental controls exercise both Gates directly: holder binding,
challenge replay, revocation after importing newer history, stale history, and
cross-receiver challenge rejection. These controls have no simulated effects.
They are not added to the eight story transactions and do not turn Website B's
refusal into acceptance. The preserved signed control receipts and test cases
allow reviewers to inspect enforcement separately from cooperation.

The original and supplemental tests bind Agent B's own public key to its new
mandate. Presenting Agent A's key for B's identity cannot satisfy that binding.
B's presentation path takes public documents plus B's own subject key, not A's
key or the owner's signing key. The controller owns and signs the grant; no grant
is self-issued by B. Both agents run in one researcher-controlled fixture: these
checks demonstrate input separation and cryptographic subject binding, not
process isolation or a secrecy guarantee against the controller.

Seven supplemental review tests passed: 20 Gate checks, 16 signed Gate receipts,
zero website transactions and zero protected effects. Together with the original
25 experiment tests, the review owner suite has **32 passes**. The relevant
combined suite has **214 passes**, with no failures or skips. Full-suite outcomes
below retain the existing failure and all seven skips. Logs are
[review-owner-tests.log](logs/review-owner-tests.log),
[review-relevant-suite.log](logs/review-relevant-suite.log) and
[review-full-suite.log](logs/review-full-suite.log).

The current public verifier is deliberately limited to the fixed experiment
transcript. Unlike the live Website adapter, it does not advance observed
owner/bundle heads in the policy-refusal branch before returning to the next
transaction. The recorded eight outcomes remain valid; reviewers must not treat
it as a general-purpose replay verifier for arbitrary refusal/rollback sequences.
This review documents that limit without changing the authorization engine or
expanding the original proof's claim.

## Baseline investigation

The exact unchanged baseline is
`c46e82a324199050239540f1825a3b0c942e4053` (also the observed remote `main` tip
before push). A `git archive` source snapshot was tested using this environment's
existing dependencies, with its own `src` first on `PYTHONPATH` and an assertion
that `openline_wallet` loaded from that snapshot. No worktree was created and
no repository test/helper was changed. A test-only `Path.home` override uses a
writable directory outside `/tmp` for fixtures because the injected home is
read-only; the same override is used for the experiment full-suite comparison.

| Revision/run | Discovered | Passed | Failed | Skipped | Errors |
|---|---:|---:|---:|---:|---:|
| Unchanged baseline | 196 | 188 | 1 | 7 | 0 |
| Original implementation | 221 | 213 | 1 | 7 | 0 |
| Review revision (adds 7 control tests) | 228 | 220 | 1 | 7 | 0 |

The failure in all complete runs is
`test_approved_job_live_sandbox.LiveSandboxTests.test_real_cli_creates_helper_in_preflight_home`.
At line 39 the unchanged assertion requires substring **`0.153.0`** in
`codex --version`; the installed executable prints **`codex-cli 0.159.2\n`**.
The repository's live-host workflow pins `@openai/codex@0.153.0`. The failed
version assertion occurs **before** the helper-alias assertions, so this test
does not establish helper creation on 0.159.2. No provider request is involved.

The failing test file and its preflight helper match baseline byte for byte:

| File | SHA-256 on both baseline and branch |
|---|---|
| `tests/test_approved_job_live_sandbox.py` | `5080adfab186422bf03bb665298a3d5ddad5c6ccf634bc41d41e9d25e25a0320` |
| `proofs/approved-job-live-001/run.py` | `53af179ea8fd74f9009f026b0dbd999bff3d5916f5a84bc768b316d93fc3c83e` |

[baseline-review-findings.json](baseline-review-findings.json) records exact
traceback, hashes, imported source location and reproduction commands;
[baseline-review-full-suite.log](logs/baseline-review-full-suite.log) preserves
the actual full run. This is an unchanged baseline/toolchain incompatibility,
not a regression introduced by OWNER-PORTABILITY-001. No unrelated test was
rewritten, suppressed or given a fake version to produce a green result.

## Seven preserved skips and what remains untested

All seven are discovered RRSI promotion-adapter integration tests. Their existing
skip condition is unchanged: the local upstream fixture commit
`e4d1a7a0388e02b388bc40eb0a125fcfc7123f8d` is absent. The fixture component is
`third_party/harbor_terminus2/terminus_json_plain_parser.py`. No external repository
was fetched for this review.

| Skipped test | Coverage not executed |
|---|---|
| `test_accept_expected_signer` | Accepting an artifact signed by the pinned optimizer signer |
| `test_install_invoke_exact_artifact` | Installing/invoking the exact accepted component and issuing a signed loaded-digest receipt |
| `test_refuse_bad_dependency` | Rejecting an unapproved dependency and preventing its installation |
| `test_refuse_changed_base` | Rejecting a candidate whose incumbent base changed |
| `test_refuse_different_signer_before_checks` | Rejecting the wrong signer before operator checks |
| `test_refuse_substituted_artifact` | Refusing the next invocation after deployed bytes are substituted |
| `test_revocation_refuses_next_invocation` | Refusing the next invocation after revocation |

These are skipped, not passed. Passing local Wallet/Gate tests does not establish
these upstream-derived artifact installation/invocation paths. The same seven
skips occur on the baseline and the experiment branch; exact reasons remain in
both logs and the baseline findings JSON.

## Comparison and precise claim

[OAUTH_COMPARISON.md](OAUTH_COMPARISON.md) compares scoped delegation, token
exchange, distinct replacement credentials, signed local validation, DPoP,
revocation/introspection and rich authorization requests. Existing OAuth systems
can support cooperating sites and agent replacement; the toy token-registry
baseline cannot establish an inherent portability limit of OAuth or Poppy.

The earned claim is that one synthetic owner can carry signed authority and
recorded decisions to two **locally configured** receivers, refuse unacceptable
terms without an agent fabricating consent, replace a revoked agent with a new
key/scoped mandate, and reconstruct this recorded story from signed evidence.
Both receivers are operated by the experiment controller. This is simulated
cooperation and separately exercised enforcement, **not acceptance by an
independently operated website**, an outside endorsement, a real account transfer,
a legal-contract result or proof of security superiority over OAuth.

## Reviewer entry points

- [Original report and reproduction commands](REPORT.md)
- [Preregistration](PREREGISTRATION.md)
- [Unchanged final story evidence](final-evidence/evidence.json)
- [Self-attested public trust pins](final-evidence/trust-anchors.json)
- [Baseline findings](baseline-review-findings.json)
- [Receipt and key audit](review-authority.json)
- [OAuth comparison](OAUTH_COMPARISON.md)
- [Source hashes](source-hashes.json) and [artifact hashes](artifact-hashes.json)

Run the retained story verifier from the checkout:

```bash
.venv/bin/python -m openline_wallet.owner_portability verify \
  proofs/owner-portability-001/final-evidence/evidence.json \
  --trust-anchors proofs/owner-portability-001/final-evidence/trust-anchors.json
.venv/bin/python -m unittest discover -s tests -p 'test_owner_portability*.py' -v
```

The root-level artifact manifest indexes the review revision. Initial manifests
are retained separately for provenance; their old report checksum describes the
report before this addendum, not the current report.

The included pins are synthetic fixture assertions, not externally authenticated
identities. Read the limitations before interpreting a PASS result. Neither a
successful local run nor GitHub branch availability establishes deployment,
independent website adoption, a merge, or publication of the cloud environment.
