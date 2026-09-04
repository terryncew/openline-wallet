# PLATFORM-EXIT-LIVE-001

This is the MCP compatibility layer above the already-frozen `PLATFORM-EXIT-001`
Wallet/Gate acceptance contract.

The core proof establishes that current authority can move between independent
receiver contexts, old signed authority can remain authentic while losing
standing, and the Receiver Gate owns the execution decision.

This experiment asks the narrower product question:

> Can one user replace the AI host without transferring the first provider's
> credentials or giving either provider control of authority?

## Architecture

```text
User-owned OpenLine Wallet
        |
        | current signed history
        v
Claude MCP sidecar ----\
                        >---- Receiver Gate ----> staging effect ledger
Codex MCP sidecar -----/
```

MCP is transport only. Each provider gets a different user-controlled OpenLine
subject key. Provider credentials are not stored in the Wallet and are not
passed from Claude to Codex.

This development demo proves **protocol separation**, not hostile local-process
isolation. A coding host running as the same OS user may be able to read files
that user can read. Production custody therefore needs an OS/keychain/HSM or
remote-signer boundary; file mode `0600` is not a defense against a malicious
same-user agent.

The sidecar reads the current Wallet bundle on every tool call. The receiver
admits that current history, issues a one-use exact-action challenge, verifies
the subject-bound presentation, and applies the demo effect only after the Gate
returns `ALLOWED`.

## Deterministic acceptance test

Install the optional MCP dependency and run the suite:

```bash
python -m pip install -e ".[mcp]"
python -m unittest discover -s tests -v
```

`test_platform_exit_live.py` connects through the official MCP Python SDK's
in-memory client, but crosses a real localhost HTTP receiver boundary.

The fixed sequence is:

1. Claude subject has `deploy:staging`.
2. The Claude MCP tool call is `ALLOWED` and the receiver records one effect.
3. The operator syncs the signed receipt, revokes Claude, and grants Codex the same narrow scope.
4. The old Claude MCP sidecar calls again using the *current* Wallet history and its old subject key.
5. Receiver Gate returns `STOPPED / MANDATE_REVOKED`; no effect is written.
6. Codex calls through its separate MCP sidecar and is `ALLOWED`.
7. The final Wallet bundle contains the pre-switch receiver receipt and the same user principal history.

The deterministic CI test earns the transport/authority split by itself. The
real-host result below separately demonstrates that the pinned Claude Code and
Codex hosts consumed the MCP path successfully.

## Frozen real-host result

Successful GitHub Actions run:

```text
Experiment     PLATFORM-EXIT-LIVE-001
Run            33841050124
Head           711ced888befc6ed9f64dd22cc30e9a14f7b5cb1
Claude Code    2.1.260
Codex CLI      0.153.0
MCP            2.1.1

Claude before  ALLOWED
Claude after   STOPPED / MANDATE_REVOKED
Codex after    ALLOWED

Effects        2
Receipts       3
Wallet head    3
Verdict        PLATFORM_EXIT_LIVE_CONTINUITY_ENFORCED
```

The receiver recorded exactly the two permitted effects: `claude-before` and
`codex-after`. The denied `claude-after` attempt produced a signed STOPPED
receipt and no effect.

The final verifier also confirmed:

- the old Claude mandate is revoked;
- the successor Codex mandate is active;
- the pre-switch Claude receipt remains in Wallet history;
- the Wallet has no policy authority;
- decision authority remains `RECEIVER_GATE`.

The non-secret evidence uploaded by that run is frozen in
[`proofs/platform-exit-live-001/`](proofs/platform-exit-live-001/).

Artifact SHA-256:

```text
281ae549b31f7a9f4ec940395da488d2b6fa5eb12a9c9bb70471dcc314532203
```

The directory includes the exact result JSON, host logs, final Wallet bundle,
effect ledger, three signed receiver receipts, and the artifact's internal
`SHA256SUMS.txt`. Private Wallet and subject keys are not included.

## Reproduce the live path

Prepare a disposable workspace:

```bash
openline-wallet-platform-exit prepare platform-exit-live
```

Start the receiver with the command written to:

```text
platform-exit-live/start-gate.txt
```

The workspace also contains:

```text
platform-exit-live/claude-mcp.json
platform-exit-live/codex-mcp.toml
```

Those files point both hosts at the same `current.olw` but give each a distinct
subject key and mandate ID.

Use Claude with the generated MCP config and ask it to call `deploy_staging`
once. Then switch the authority:

```bash
openline-wallet-platform-exit switch platform-exit-live
```

Without changing the old Claude sidecar, ask it to call the tool again. The
receiver should return `STOPPED / MANDATE_REVOKED`. Configure Codex with the
generated TOML and ask it to call `deploy_staging` once. It should be
`ALLOWED`.

Finally:

```bash
openline-wallet-platform-exit verify platform-exit-live
```

## Falsifiers

The live claim fails if any of these happen:

- Codex needs Claude's provider credential, conversation state, or private subject key.
- The old Claude subject executes after the receiver has admitted the revocation.
- A denied tool call mutates the receiver effect ledger.
- The pre-switch receiver receipt disappears when authority moves.
- The MCP worker can issue, revoke, or redefine its own Wallet mandate.
- The Wallet itself produces an execution authorization without the Receiver Gate.

None of those falsifiers occurred in run `33841050124`.

## Claim boundary

The real-host run earns this statement for the tested configuration:

> A real Claude host acted before the switch, the same Claude authority was
> stopped after revocation, and a real Codex host continued under the successor
> mandate while user-owned authority history remained intact.

A shorter public rendering is:

> I changed the AI. I didn't have to give up the permissions and history that belonged to me.

Do not widen this into a general provider-portability claim. The demonstrated
configuration is one owner, one continuously running localhost Receiver Gate,
two pinned real AI hosts, and an intentionally safe staging-effect ledger.

Still unearned: provider credential portability, production key custody,
durable Gate restart recovery, cross-machine revocation propagation,
production deployment safety, multi-party federation, and public-network
coordination.
