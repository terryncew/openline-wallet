# APPROVED-JOB-LIVE-001 — provider replacement continues one approved job

## Question

Can a real coding job continue from one verified checkpoint after the first model provider is removed from the continuation path, while the owner-approved agreement remains unchanged and the successor receives neither the previous provider's chat nor its credentials?

This is the smallest live composition earned by `PLATFORM-EXIT-LIVE-001` and `APPROVED-JOB-001`, rebuilt on Wallet main `eb9dc395e7b941f1966a96d9731a867eea6fd050` after `EGRESS-GATE-001` merged. It does not add a product API or a new agent framework.

`EGRESS-GATE-001` now defines the downstream product boundary as the **Receiver Gate**. This experiment verifies that boundary as a preflight but deliberately stops the coding job at Airlock `ELIGIBLE`; it does not send a production effect through the gate. That separation keeps the claim clean: continuity of approved work here, consequence control in EGRESS-GATE-001.

## Fixture

The owner approves one maintenance job: `total(items)` must return the correct sum without mutating the caller's list.

The starting implementation has two defects: it sorts the caller's list in place and returns `0`.

Worker A is allowed to make one bounded checkpoint. The checkpoint is accepted only if:

- only `calculator.py` changed;
- the ordinary numeric check now passes; and
- the owner-approved no-mutation check still fails.

The controller commits that exact state, records its SHA in the existing `approved-job.agreement.v1` handoff, and revokes Worker A. It then creates a verified handoff projection from the frozen agreement and independent check results. No Worker A chat, hidden model state, provider credential, or private subject key is put in that projection.

Worker B must continue from the exact checkpoint commit. It receives the verified projection and the checked-out repository, fixes the remaining requirement, and submits one descendant commit. Existing Airlock protected-path and command evaluation decides whether the result becomes `ELIGIBLE`.

The negative control creates an otherwise-correct fix from the original base instead of the verified handoff. It must be rejected by the ancestry requirement.

## Two modes

`scripted` mode uses two separate local subprocess workers. It is the credential-free CI proof for the harness and invariants.

`real` mode uses Claude Code before the handoff and Codex after it. The wrapper forwards only the credential family required by that provider and does not forward GitHub/SSH credentials or the other provider's credential. Claude and Codex also receive separate filesystem `HOME` directories; Codex keeps only its own `CODEX_HOME`. Provider A is not invoked after the verified checkpoint.

The provider-loss condition is deliberately induced. A successful run does **not** prove that Anthropic suffered an outage; it proves that continuation does not require another call to provider A after the handoff.

## Falsifier

Fail the experiment if any of the following occurs:

- Worker A cannot reach the discriminating checkpoint;
- the handoff agreement digest changes across replacement;
- Worker B requires Worker A's chat, credentials, filesystem home, or another Worker A invocation;
- Worker B changes anything other than the permitted candidate file;
- the final candidate is not a descendant of the exact handoff commit;
- the frozen ordinary or approved acceptance checks fail;
- Airlock does not return `ELIGIBLE`; or
- the restart-from-base negative control is accepted.

## Claim boundary

A passing real run would support this narrow statement:

> A real Claude Code worker produced a verified partial checkpoint; Claude was absent from the continuation path after that checkpoint; a real Codex worker continued from the exact checkpoint under the same owner-approved agreement; and Airlock accepted the final descendant candidate without transferring Claude's chat or provider credential.

It would **not** establish full conversation/context portability, an observed provider outage, production deployment safety, payment safety, external-effect deduplication, or universal model portability. It also does not claim that `EGRESS-GATE-001` and this job handoff form one production transaction; the Receiver Gate is verified separately and no downstream effect is executed in this proof.

## Reproduce the controlled proof

Use Wallet main plus the pinned Airlock checkout:

```sh
git clone https://github.com/terryncew/openline-airlock.git .deps/airlock
git -C .deps/airlock checkout fb02207f3ac561368beeabf9ff168076bf828824
python -m pip install -e . -e .deps/airlock
python proofs/approved-job-live-001/run.py --output approved-job-live-artifacts --mode scripted
python proofs/approved-job-live-001/run.py --verify approved-job-live-artifacts
```

The GitHub workflow first reproduces `APPROVED-JOB-001` and the current `EGRESS-GATE-001` Receiver Gate proof, then runs this controlled continuation proof on Python 3.11, 3.12, and 3.13. Its manual `run_real_hosts=true` arm repeats the Receiver Gate preflight, installs the same pinned Claude Code and Codex CLI versions already used by `PLATFORM-EXIT-LIVE-001`, requires repository provider secrets, runs real mode, and preserves only the non-secret evidence directory.

## Live-host environment repair (run 16)

Run 16 at Wallet `31bd64bf030b9c079adf0bf2f0bcacd6bf55a4ac` passed the controlled matrix but failed the real continuation. The preserved `provider-b.log` reports bubblewrap namespace setup failures, including `loopback: Failed RTM_NEWADDR: Operation not permitted`. Codex returned zero while reporting it could not read or edit files. The resulting empty patch was misleadingly reported as an unapproved path.

The live job now targets `ubuntu-22.04` instead of the moving `ubuntu-latest` image and performs a credential-free Codex sandbox preflight before either provider is called. It checks an allowed workspace write and denied writes outside the workspace and into `.git`, under both network-enabled and restricted-network policies. The second probe exercises the namespace setup required by Codex's file helper, which imposes restricted networking independently of the shell configuration. It does not invoke the actual agent file tool.

Workspace-write isolation remains enabled. Temporary directories are excluded from the writable roots. The preflight has no unrestricted fallback, preserves failure logs, and cannot produce a passing report unless both probes complete. Empty worker patches remain failures with a distinct diagnostic. Acceptance checks and handoff ancestry requirements are unchanged.

This is an environment repair awaiting a real-host run, not evidence that live continuation has passed. Dispatch `APPROVED-JOB-LIVE-001` on the patched branch with `run_real_hosts=true`; require both the real handoff and real-host verdict steps to pass. Ordinary push/PR runs skip real hosts.

Source basis: [Codex 0.153.0 filesystem helper](https://github.com/openai/codex/blob/rust-v0.153.0/codex-rs/exec-server/src/fs_sandbox.rs) forces restricted networking. [Ubuntu documents namespace restrictions introduced in 23.10/24.04](https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007). Those restrictions are a plausible host-level cause; the saved log does not contain an AppArmor audit record. The runner change must therefore be validated by the new preflight and live run.
