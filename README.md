# OpenLine Wallet

**The model proposes, the receiver decides, and the proof travels.**

OpenLine Wallet keeps user-owned authority history outside the AI provider.

A receiver can verify the current mandate, decide whether an exact action is allowed, and return a signed receipt. You can replace the model without replacing the authority history.

## What exists now

**Wallet** is a local Python CLI and reference library. It stores signed permission records, revocations, and action receipts in a folder you control, and exports a JSON bundle a receiving service can verify without calling the original AI provider.

**Receiver Gate** is the included receiving-side reference implementation. It pins the user's identity, checks that a bundle is signed and newer than the last one accepted, issues a one-time challenge tied to the exact action, and returns a signed `ALLOWED` or `STOPPED` receipt. The model cannot approve itself.

Three public proofs cover different parts of the problem:

- **[PLATFORM_EXIT_LIVE_001.md](PLATFORM_EXIT_LIVE_001.md)** — a real Claude host acted before a switch, the same Claude authority was stopped after revocation, and a real Codex host continued under a successor mandate against one continuously running localhost Receiver Gate.
- **[APPROVED_JOB_LIVE_001.md](APPROVED_JOB_LIVE_001.md)** — Claude produced a verified partial code checkpoint; Claude was then absent from the continuation path; Codex continued from that exact checkpoint under the same owner-approved agreement; and Airlock accepted the final descendant candidate without receiving Claude's chat or provider credential.
- **[AUTHORITY_IN_TIME_001.md](AUTHORITY_IN_TIME_001.md)** — in a controlled fixture, the receiver called work revocation-protected only when its supported detection, propagation, receiver, stop, and uncertainty budget beat the cancellation horizon. Late revocation was recorded as late, not rewritten as prevention.

The temporal boundary is important: **revocation is a race against consequence.** An action that already crossed the receiver while authority was current may keep executing. Three facts must remain separate: revocation issued, revocation observed, and outstanding effects closed.

Exported bundles expire after ten minutes by default to bound stale-history exposure. Receivers can require a shorter age for riskier actions.

## Try the provider-switch demo

```bash
git clone https://github.com/terryncew/openline-wallet.git
cd openline-wallet
python -m venv .venv
source .venv/bin/activate
pip install -e .
openline-wallet demo-platform-exit
```

You should see:

```text
Platform A   ALLOWED — agent-a / deploy:staging
Move         wallet history and Platform A receipt preserved
Platform B   STOPPED — old mandate / MANDATE_REVOKED
Platform B   ALLOWED — agent-b / deploy:staging
Verdict      PLATFORM_EXIT_CONTINUITY_ENFORCED
```

The old permission still has a valid signature. Platform B stops it because the newer history says it was revoked. Agent B passes because it holds the key named in the current permission.

That distinction is the point: **a record can be genuine and still be too old to use.**

## Real Claude -> Codex provider-switch result

`PLATFORM-EXIT-LIVE-001` puts one stdio MCP tool and a receiver-owned localhost Gate above the same Wallet/Gate contract.

On September 4, 2026, one real-host run used Claude Code 2.1.260 and Codex CLI 0.153.0 against one continuously running localhost Receiver Gate:

```text
Claude before  ALLOWED
Claude after   STOPPED / MANDATE_REVOKED
Codex after    ALLOWED
Effects        2
Receipts       3
Verdict        PLATFORM_EXIT_LIVE_CONTINUITY_ENFORCED
```

The earned claim is deliberately narrow:

> A real Claude host acted before the switch, the same Claude authority was stopped after revocation, and a real Codex host continued under the successor mandate while user-owned authority history remained intact.

The run did **not** transfer Claude's provider credential, conversation state, or private subject key to Codex. The Wallet carried the user's authority history; the Receiver Gate made each execution decision.

Frozen evidence is in [`proofs/platform-exit-live-001/`](proofs/platform-exit-live-001/). The successful GitHub Actions run was `33841050124` at commit `711ced888befc6ed9f64dd22cc30e9a14f7b5cb1`; the uploaded evidence artifact SHA-256 is `281ae549b31f7a9f4ec940395da488d2b6fa5eb12a9c9bb70471dcc314532203`.

This result is **not** a general provider-portability claim. It covers one owner, one continuously running localhost Gate, two real AI hosts, and an intentionally safe staging-effect ledger. Provider credential portability, production key custody, durable Gate restart recovery, cross-machine revocation propagation, and production deployment safety remain unearned.

See [`PLATFORM_EXIT_LIVE_001.md`](PLATFORM_EXIT_LIVE_001.md) for the experiment and claim boundary.

## Real approved-job continuation result

`APPROVED-JOB-LIVE-001` asks a different question: can a real coding job continue from a verified checkpoint after the first model provider is removed from the continuation path, while the owner-approved agreement stays unchanged?

The frozen run used Claude Code 2.1.260 and Codex CLI 0.153.0.

Claude produced a bounded partial checkpoint where the ordinary numeric check passed but the owner-approved no-mutation requirement still failed. The controller committed the exact checkpoint and handoff projection. Claude made zero calls after handoff.

Codex then continued from that exact descendant state and fixed the remaining requirement. Airlock evaluated the final candidate under the same approved agreement.

```text
Claude stage   ordinary PASS / approved acceptance FAIL
Claude after handoff calls   0
Codex stage    ordinary PASS / approved acceptance PASS
Airlock        ELIGIBLE
Restart-from-base control     REJECTED
Verdict        APPROVED_JOB_LIVE_CONTINUATION_ENFORCED
```

The earned claim is:

> A real Claude Code worker produced a verified partial checkpoint; Claude was absent from the continuation path after that checkpoint; a real Codex worker continued from the exact checkpoint under the same owner-approved agreement; and Airlock accepted the final descendant candidate without transferring Claude's chat or provider credential.

This does **not** establish full session portability, outage resilience, production deployment or payment safety, production key custody, durable Receiver Gate restart, universal model portability, or cross-machine revocation propagation.

See [`APPROVED_JOB_LIVE_001.md`](APPROVED_JOB_LIVE_001.md) for the full experiment and limits.

## Authority in time

`AUTHORITY-IN-TIME-001` tests when a receiver is allowed to call an action revocation-protected at all.

The frozen rule is:

```text
required_time =
    detect_bound
  + propagation_bound
  + receiver_bound
  + stop_bound
  + uncertainty_bound

remaining_margin = consequence_horizon - required_time
```

A receiver may label work `ADMIT_REVOCATION_PROTECTED` only when every required bound is present, supported, and `remaining_margin > 0`.

Equality is already too late. A negative margin, missing bound, unsupported bound, or double-counted uncertainty refuses the protection claim before the action is armed.

The controlled experiment passed five discriminating cases with verdict:

```text
CONTROLLED_TEMPORAL_ADMISSION_ENFORCED
```

The earned claim is narrow:

> In this controlled fixture, the receiver admitted revocation-protected work only within a supported timing budget and distinguished prevention from late revocation.

It does not establish worst-case transport bounds, provider-side cancellation, multi-machine clock integrity, queue fencing, multi-receiver temporal closure, or a universal revocation guarantee.

See [`AUTHORITY_IN_TIME_001.md`](AUTHORITY_IN_TIME_001.md).

## Basic CLI

Create a wallet:

```bash
openline-wallet init
```

For the demo, create a key for Agent A. Real integrations can provide an existing Ed25519 public key instead.

```bash
openline-wallet keygen-subject agent-a --output agent-a.key
```

Allow that agent to deploy to staging for two hours:

```bash
openline-wallet grant agent-a deploy:staging \
  --subject-key agent-a.key.pub \
  --expires 2h
```

See the current permissions and history:

```bash
openline-wallet show
openline-wallet history
```

Export a bundle for a receiving service:

```bash
openline-wallet export > wallet.olw
openline-wallet verify wallet.olw
```

Shorten or revoke a permission:

```bash
openline-wallet narrow agent-a deploy:staging --expires 30m
openline-wallet revoke agent-a
```

Private keys stay in the local wallet directory. They are never included in `wallet.olw`.

## What the receiving service does

The receiver requires more than a valid signature. It:

1. Stores the user's public identity key during onboarding.
2. Checks that the bundle is signed by that key and is newer than the last bundle it accepted.
3. Gives the agent a one-time challenge tied to the exact action.
4. Returns a signed `ALLOWED` or `STOPPED` receipt.

The included `ReferenceGate` demonstrates that flow:

```python
from openline_wallet import ReferenceGate, Wallet

wallet = Wallet.open(".openline-wallet")
bundle = wallet.export_bundle()

gate = ReferenceGate("my-service")
gate.pin_principal(wallet.principal_id, wallet.root_public_key)
gate.admit_bundle(bundle)
```

The full challenge and action flow is in [`examples/platform_exit_001.py`](examples/platform_exit_001.py).

One boundary matters: `openline-wallet verify` checks the evidence and prints `EVIDENCE_VALID`. Authorization belongs to the receiving service, which performs an effect only after its Gate returns `ALLOWED`.

## Why bundles expire

An exported bundle is valid for at most ten minutes. Suppose a user exports at 12:00, revokes at 12:01, and a disconnected service still holds the older export. That service may remain behind until the old bundle expires or it receives the update.

The ten-minute limit makes that exposure visible and bounded. Receivers can require a shorter age for riskier actions:

```python
gate = ReferenceGate("payments", max_bundle_age_seconds=30)
```

Expiry bounds stale-history exposure. It does not prove an already-started consequence can still be stopped. That depends on the receiver's timing and effect boundary.

## Current limits

This is a v0.1 reference implementation. It is ready to inspect, run, test, and integrate in a development environment. Keep it away from money, production infrastructure, medical systems, and physical machines for now.

- Local key files are permission-restricted but unencrypted.
- `ReferenceGate` keeps its accepted history and one-time challenges in memory. Production services need durable storage before executing effects.
- The CLI supports one active wallet signing key. Rotation and recovery come later.
- Multi-device sync, guardian recovery, and cross-machine revocation delivery are outside v0.1.
- Exported bundles reveal the permission history they contain.
- Multi-party federation and public-network coordination have not been demonstrated.

See [`SECURITY.md`](SECURITY.md) for the complete security boundary.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers modified bundles, scope expansion, expired exports, wrong keys, unrecognized identities, replayed requests, stale history, revocation, conflicting histories, and the deterministic MCP provider-switch path.

The provider-switch acceptance tests are documented in [`PLATFORM_EXIT_001.md`](PLATFORM_EXIT_001.md) and [`PLATFORM_EXIT_LIVE_001.md`](PLATFORM_EXIT_LIVE_001.md).

## How it works underneath

OpenLine Wallet uses Ed25519 signatures and strict canonical JSON. A long-lived identity key certifies a separate wallet signing key. Permission changes form a signed, hash-linked history. The receiving service stores the latest history it has accepted and refuses older or conflicting versions.

Those mechanics support one simple rule:

> **The wallet carries the user's history. The receiving service decides what that history permits.**

## The rest of OpenLine

Wallet is one part of a larger public stack. These are shipped in separate repositories, not bundled into this package:

- **[OpenLine Lite](https://github.com/terryncew/openline-lite)** — the front door: receiver-owned verification, model handoffs, evidence-impact analysis, and a GitHub Action.
- **[OpenLine Receipt Gate](https://github.com/terryncew/openline-receipt-gate)** — receiver-owned authorization for consequential Python and LangGraph tool calls, including the `@authorize` function guard.
- **[OpenLine Airlock](https://github.com/terryncew/openline-airlock)** — lets coding agents search for changes while keeping acceptance rules outside the agents.
- **[openline-otel](https://github.com/terryncew/openline-otel)** — Python OpenTelemetry receipt capture and Evidence Gateway.
- **[openline-otel-js](https://github.com/terryncew/openline-otel-js)** — JavaScript OpenTelemetry receipt capture with reciprocal Python/Node conformance.
- **[OpenLine Claim Graph](https://github.com/terryncew/openline-claim-graph)** — traces which accepted claims and decisions need reconsideration when upstream evidence changes.
- **[OLP Wire Canon](https://github.com/terryncew/olp-wire-canon)** — the portable byte-level receipt contract shared by producers and verifiers.

If you are new to OpenLine, start with the **[OpenLine Lite Start Here guide](https://github.com/terryncew/openline-lite/blob/main/START_HERE.md)**. You do not need to install the whole stack.

## Vision

[`VISION.md`](VISION.md) describes an unproven architectural direction for OpenLine as a shared public protocol for coordination among people and replaceable machine representatives. It is intentionally separate from the claims demonstrated by the experiments in this repository.

## Project history

This repository began in 2025 as a browser-based receipt viewer. The current Python implementation grew from the WALLET-STANDING research series in the OpenLine Receipt Gate project. The earlier viewer remains available in git at `3486bb1`.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).
