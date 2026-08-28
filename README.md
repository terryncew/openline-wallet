# OpenLine Wallet

**User-owned authority continuity for AI agents.**

Change AI providers without losing your mandates, revocations, or verified history. OpenLine Wallet carries evidence between systems. Each receiver keeps the final say over what happens.

> **Wallet owns continuity. Gate owns consequences.**

No tokens. No blockchain. No model call in the authorization path.

## See the whole product in 30 seconds

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
openline-wallet demo-platform-exit
```

Expected result:

```text
Platform A   ALLOWED — agent-a / deploy:staging
Move         wallet history and Platform A receipt preserved
Platform B   STOPPED — old mandate / MANDATE_REVOKED
Platform B   ALLOWED — agent-b / deploy:staging
Verdict      PLATFORM_EXIT_CONTINUITY_ENFORCED
Boundary     Wallet owns continuity. Gate owns consequences.
```

That is `PLATFORM-EXIT-001`: a person authorizes Agent A, gets a signed action receipt, leaves the provider, revokes Agent A, and authorizes Agent B. Platform B receives the history. Agent A's old mandate remains cryptographically genuine and still fails. Agent B proves possession of the current subject key and passes an independent receiver Gate.

## Use the CLI

Create a wallet and a subject key for an agent integration:

```bash
openline-wallet init
openline-wallet keygen-subject agent-a --output agent-a.key
```

Issue one exact, expiring mandate:

```bash
openline-wallet grant agent-a deploy:staging \
  --subject-key agent-a.key.pub \
  --expires 2h
```

Inspect and export the signed history:

```bash
openline-wallet show
openline-wallet export > wallet.olw
openline-wallet verify wallet.olw
```

Narrow or revoke current standing:

```bash
openline-wallet narrow agent-a deploy:staging --expires 30m
openline-wallet revoke agent-a
```

`verify` deliberately reports `EVIDENCE_VALID`, never `ALLOWED`. A receiver must pin the principal root, admit a fresh monotonic bundle head, verify the subject's one-use presentation, and apply its own action policy.

## What v0.1 implements

| User job | Mechanism |
|---|---|
| Create/import identity | Self-certifying principal root plus separately certified epoch key |
| Authorize | Epoch-signed, subject-bound, exact-scope mandate |
| See | Hash-linked signed event history and receiver receipts |
| Narrow/revoke | Reduce-only successor or root-signed revocation |
| Move | Root-signed receiver bundle with no private keys |
| Verify | Strict canonical JSON, Ed25519, pinned root, holder proof, one-use challenge |

Exports expire after at most **600 seconds**. That limit is intentional. A disconnected receiver that has not learned a revocation may still rely on a previously fresh bundle until its expiry. Local-first key ownership does not repeal propagation delay.

## Python API

```python
from openline_wallet import ReferenceGate, Wallet

wallet = Wallet.open(".openline-wallet")
bundle = wallet.export_bundle()

gate = ReferenceGate("my-service")
gate.pin_principal(wallet.principal_id, wallet.root_public_key)
gate.admit_bundle(bundle)
```

The reference Gate is intentionally separate from `Wallet`. Wallet methods can create and verify evidence; only `ReferenceGate.evaluate(...)` can sign an `ALLOWED` or `STOPPED` decision receipt.

## Security boundary

This is a **v0.1 reference implementation**, not production key custody.

- Root and epoch keys are stored as local mode-`0600` files. Use hardware-backed or encrypted custody before real consequential use.
- The receiver's root pin is an onboarding decision outside the bundle. A bundle cannot appoint its own trusted root.
- The product does not yet ship guardian recovery, multi-device synchronization, a witness network, or cross-machine transport.
- The included `ReferenceGate` keeps its admitted head and one-use challenges in memory. A production receiver must durably commit both before allowing an effect.
- v0.1 certifies one wallet epoch. Rotation and recovery remain outside the CLI.
- Exported bundles reveal their included authority history. Unlinkable selective disclosure is outside v0.1.
- A valid signature proves provenance. Current standing and exact-action permission still belong to the receiver.

See [SECURITY.md](SECURITY.md) for reporting and the complete boundary. See [PLATFORM_EXIT_001.md](PLATFORM_EXIT_001.md) for the acceptance contract.

## Development

```bash
python -m unittest discover -s tests -v
python examples/platform_exit_001.py
```

The test suite covers tampering, scope expansion, expiry, wrong keys, unpinned roots, replay, stale heads, revocation, and competing valid branches.

## Project history

This repository began in 2025 as a static OpenLine receipt viewer. The git history is preserved. v0.1 changes the product around the result of the WALLET-STANDING-001→004 research line: portable evidence is useful only when the receiver can independently determine its current standing.

The earlier viewer artifacts remain available in git at `3486bb1`.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
