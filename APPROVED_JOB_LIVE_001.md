# APPROVED-JOB-LIVE-001 — provider replacement continues one approved job

**Status: PASS — frozen from GitHub Actions run 28 on September 9, 2026.**

## Question

Can a real coding job continue from one verified checkpoint after the first model provider is removed from the continuation path, while the owner-approved agreement remains unchanged and the successor receives neither the previous provider's chat nor its credentials?

`APPROVED-JOB-LIVE-001` is the smallest live composition of the earlier `PLATFORM-EXIT-LIVE-001` provider-switch result and `APPROVED-JOB-001` approved-job handoff. `EGRESS-GATE-001` remains the downstream Receiver Gate boundary. This proof stops at Airlock `ELIGIBLE`; it executes no production effect.

## Fixture

The owner approves one maintenance job: `total(items)` must return the correct sum without mutating the caller's list.

The starting implementation has two defects: it sorts the caller's list in place and returns `0`.

Worker A may create one bounded checkpoint. The checkpoint is valid only when `calculator.py` is the only changed path, the ordinary numeric check passes, and the owner-approved no-mutation check still fails. The controller commits that exact state and creates a verified handoff projection from the frozen agreement and independent check results.

Worker B must continue from that exact checkpoint, change only `calculator.py`, and satisfy the same owner-approved agreement. The restart-from-base negative control makes an otherwise-correct fix from the original base and must be rejected because it is not a descendant of the verified handoff.

## Frozen real-host result

Successful GitHub Actions run:

```text
Experiment       APPROVED-JOB-LIVE-001
Run              28 / 34413490635
Head             7d4e8fe501528e3e7758921d28b58e2cecd3f18c
Python           3.12.14
Claude Code      2.1.260
Codex CLI        0.153.0
Airlock          fb02207f3ac561368beeabf9ff168076bf828824
Receiver check   EGRESS-GATE-001

Claude stage     ordinary PASS / approved acceptance FAIL
Handoff commit   653a8067b3d45dde39957f9ad18d90822367cdef
Agreement digest 15f68e45ede5a541e529006735484acc634d493070bdf76fc7a9ce687e2771d2
Claude calls after handoff  0
Codex stage      ordinary PASS / approved acceptance PASS
Final candidate  db722a2ad1c4059b05e498749d44c2b5f9120c35
Airlock          ELIGIBLE
Restart control  REJECTED
External effect  none
Verdict          APPROVED_JOB_LIVE_CONTINUATION_ENFORCED
```

The controlled proof matrix also passed on Python 3.11, 3.12, and 3.13 in the same workflow run. Before either provider was used, both Codex sandbox preflight probes passed. The real-host job then passed provider credential checks, Codex authentication, the current Receiver Gate preflight, checkout-credential absence, the real Claude -> Codex handoff, the `--require-real` verifier, the private-key evidence check, and artifact preservation.

The evidence records that Worker A changed only `calculator.py`; its checkpoint passed the ordinary check while leaving the approved no-mutation requirement unresolved. The agreement digest remained unchanged across replacement. Worker A's isolated home was removed before Worker B, no Worker A credential or filesystem home was transferred to Worker B, no prior-provider chat/model memory was transferred, and Worker A was invoked zero times after the handoff.

Codex continued from the exact recorded handoff commit, changed only `calculator.py`, and produced a descendant candidate for which the ordinary, protected, and approved acceptance checks all passed. Airlock returned `ELIGIBLE`. The restart-from-base negative control was rejected because the candidate was not descended from the verified handoff.

## Frozen evidence

The successful Actions artifact `approved-job-live-001-real-hosts` is expanded under [`proofs/approved-job-live-001/frozen/`](proofs/approved-job-live-001/frozen/). The original uploaded artifact was 13,185 bytes and had SHA-256:

```text
96a55c659059ef4bdfca55b231c2e5664eba774c86dfb22d0b59a27c4af56ec2
```

`frozen/receipt.json` binds the terminal verdict to run `34413490635`, head `7d4e8fe501528e3e7758921d28b58e2cecd3f18c`, the provider/runtime versions, the Airlock pin, and the artifact digest. `frozen/SHA256SUMS.txt` hashes every preserved evidence file and the freeze receipt.

The preserved evidence includes the exact result JSON, full evidence JSON, verified handoff projection, Worker A and Worker B patches, redacted provider logs, the restart falsifier, and both credential-free sandbox preflight logs/report. The workflow's private-key evidence check passed before the artifact was finalized.

## Earned claim

This run earns the following statement for the tested configuration:

> A real Claude Code worker produced a verified partial checkpoint; Claude was absent from the continuation path after that checkpoint; a real Codex worker continued from the exact checkpoint under the same owner-approved agreement; and Airlock accepted the final descendant candidate without transferring Claude's chat or provider credential.

A shorter rendering is:

> The worker changed. The approved job did not restart, and the replacement did not inherit the previous provider's authority or private context.

## Claim boundary

Do not widen this into full context portability or a general outage-resilience claim. Provider-A absence was deliberately induced after the verified checkpoint; Anthropic did not have to suffer a real outage. The successor received a small verified handoff projection and the exact checked-out repository state, not the previous chat, hidden model state, credentials, or every human intention.

This proof also does **not** establish production deployment safety, payment safety, production key custody, external-effect deduplication, durable Receiver Gate restart recovery, universal model portability, or cross-machine revocation propagation. No downstream effect was executed here; `EGRESS-GATE-001` verifies the Receiver Gate boundary separately.

## Falsifiers

The experiment would have failed if Worker A could not reach the discriminating checkpoint; the agreement digest changed; Worker B needed Worker A's chat, credentials, filesystem home, or another Worker A call; either worker changed an unapproved path; the final candidate was not a descendant of the exact handoff; the frozen checks failed; Airlock did not return `ELIGIBLE`; or the restart-from-base negative control was accepted.

None of those falsifiers occurred in run `34413490635`.

## Reproduce the controlled proof

Use Wallet plus the pinned Airlock checkout:

```bash
git clone https://github.com/terryncew/openline-airlock.git .deps/airlock
git -C .deps/airlock checkout fb02207f3ac561368beeabf9ff168076bf828824
python -m pip install -e . -e .deps/airlock
python proofs/approved-job-live-001/run.py --output approved-job-live-artifacts --mode scripted
python proofs/approved-job-live-001/run.py --verify approved-job-live-artifacts
```

The manual real-host arm remains available as a reproducibility path, but run 28 is the frozen terminal receipt for this experiment. Earlier red runs remain part of Actions history and are not reclassified as evidence.
