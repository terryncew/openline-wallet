# PLATFORM-EXIT-LIVE-001

This is the MCP compatibility layer above the already-frozen `PLATFORM-EXIT-001`
Wallet/Gate acceptance contract.

The core proof already establishes that current authority can move between
independent receiver contexts, old signed authority can remain authentic while
losing standing, and the Receiver Gate owns the execution decision.

This experiment asks the product question:

> Can the user replace the AI provider without transferring the first
> provider's credentials or giving either provider control of authority?

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

CI earns the transport/authority split. It does **not** earn the claim that
Claude Code and Codex themselves successfully consumed the server.

## Real-host run

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
once with a release name such as `claude-before`.

Then switch the authority:

```bash
openline-wallet-platform-exit switch platform-exit-live
```

Without restarting or changing the old Claude sidecar, ask it to call the tool
again. It should receive:

```text
STOPPED / MANDATE_REVOKED
```

Configure Codex with the generated TOML block and ask it to call
`deploy_staging` once. It should be `ALLOWED`.

Finally:

```bash
openline-wallet-platform-exit verify platform-exit-live
```

A real-provider pass is:

```text
Claude before  ALLOWED
Claude after   STOPPED / MANDATE_REVOKED
Codex after    ALLOWED
Verdict        PLATFORM_EXIT_LIVE_CONTINUITY_ENFORCED
Boundary       Wallet owns continuity. Gate owns consequences.
```

## Falsifiers

The live claim fails if any of these happen:

- Codex needs Claude's provider credential, conversation state, or private subject key.
- The old Claude subject executes after the receiver has admitted the revocation.
- A denied tool call mutates the receiver effect ledger.
- The pre-switch receiver receipt disappears when authority moves.
- The MCP worker can issue, revoke, or redefine its own Wallet mandate.
- The Wallet itself produces an execution authorization without the Receiver Gate.

## Claim boundary

If the deterministic test passes, we have proved an MCP transport path that
preserves the existing Wallet/Gate authority split.

Only an actual Claude -> Codex host run earns the public statement:

> I changed the AI. I didn't have to give up the permissions and history that belonged to me.

Still unearned: production key custody, provider credential portability,
durable Gate restart recovery, cross-machine revocation propagation, and
production deployment safety. The receiver effect here is intentionally a safe
local staging ledger, not real infrastructure.
