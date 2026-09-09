# EGRESS-GATE-001 — receiver-owned MCP consequence boundary

EGRESS-GATE-001 asks one narrow question: can a receiver stop a syntactically valid MCP tool call from becoming a downstream side effect when the call violates an owner-approved parameter boundary, independent of which upstream model proposed it?

The build reuses Wallet rather than adding another authority system. `ReferenceGate` remains the reference authorization primitive; `EffectGate` adds effect-time revalidation. The product boundary is called the **Receiver Gate**. Earlier “Receipt Gate” wording is treated as shorthand for the same receiver-side boundary, not a third component.

## What was added

`openline_wallet.mcp_egress` is a small local/development shim for MCP `tools/call`. It validates the modern `Mcp-Method` / `Mcp-Name` routing headers when protocol version `2026-07-28` is present, checks one receiver-owned integer range contract, checks current Wallet authority, revalidates authority at the effect frontier, forwards an allowed request to the target, and emits a signed consequence receipt bound to the exact proposal hash and contract hash.

The first contract is intentionally tiny rather than a new policy language:

```json
{
  "schema": "openline.receiver.mcp_contract.v1",
  "contract_id": "account-limit-v1",
  "wallet_scope": "mcp:account.set_limit",
  "tool_name": "account.set_limit",
  "rule": {
    "rule_id": "amount-0-5000",
    "parameter": "amount",
    "minimum": 0,
    "maximum": 5000
  }
}
```

A successful consequence receipt records the exact MCP proposal hash, contract and rule, Wallet authorization receipt hash, effect-frontier receipt hash, downstream result hash, receiver identity, decision, and timestamp. Rejections are signed too. If the downstream call may have happened but its acknowledgement is lost, the shim returns `UNRESOLVED` and does not retry automatically.

## Falsifier

The proof uses a separate fake MCP target that owns its own append-only call witness. The broad transport schema accepts integer amounts from 0 through 1,000,000. The owner contract allows `account.set_limit` only from 0 through 5,000.

1. `amount=2000` is authorized and reaches the target exactly once.
2. `amount=50000` is valid under the broad schema but violates the receiver contract. The Receiver Gate rejects it and the target-owned call counter does not move.
3. The worker is revoked. A later otherwise-valid `amount=2000` request is rejected and the target-owned counter still does not move.
4. Negative control: the same `amount=50000` request bypasses OpenLine and reaches the target, advancing the target-owned witness.

The target witness is deliberately separate from OpenLine’s signed receipt trail so the gate is not the only source claiming that a rejected call was never invoked.

## Scope

This is not a production MCP gateway, hosted authorization service, complete policy language, or proof that all unsafe actions are blocked. The reference HTTP shim is loopback-only and protects `tools/call` requests. Its local development mode keeps the Wallet subject credential and receiver boundary in one user-controlled process for convenience; production deployments should separate those roles according to their threat model.

The proof does not claim zero defect leakage in general. It establishes the specific receiver-side boundary above, including one schema-valid negative control and one revocation case. Router/provider chaos testing is a later experiment, only if this shim survives real integration.

## Run

```sh
python -m pip install -e .
python -m unittest discover -s tests -p 'test_mcp_egress.py' -v
python proofs/egress-gate-001/run.py --output egress-gate-artifacts
python proofs/egress-gate-001/run.py --verify egress-gate-artifacts
```

The dedicated GitHub Actions workflow runs the same proof on Python 3.11, 3.12, and 3.13. Existing Wallet CI remains separate and must stay green.
