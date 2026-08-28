# PLATFORM-EXIT-001

`PLATFORM-EXIT-001` is the acceptance contract for OpenLine Wallet v0.1.

## Claim

A person can move signed authority history between two independent receiver contexts. After the second receiver admits the current history, superseded authority remains authentic but cannot execute; a current subject-bound successor can.

## Fixed sequence

1. The principal issues Agent A a two-hour `deploy:staging` mandate.
2. Platform A pins the principal root, admits the bundle, verifies a one-use holder proof, and signs an `ALLOWED` receipt.
3. The wallet records that receipt.
4. The principal revokes Agent A and issues Agent B the same narrow scope.
5. Platform B pins the same root and admits the new bundle head.
6. Platform B rejects the older pre-exit bundle as stale.
7. Agent A proves possession of its original key and presents its still-validly-signed mandate. The Gate stops it because standing is `REVOKED`.
8. Agent B proves possession of its current key. The Gate allows the exact action.
9. The wallet history and Platform A receipt survive export and re-import.

## Pass conditions

- Platform A current action: `ALLOWED`
- Original Agent A event signature after revocation: valid
- Pre-exit bundle after current-head admission: `BUNDLE_HEAD_STALE`
- Agent A on Platform B: `STOPPED / MANDATE_REVOKED`
- Agent B on Platform B: `ALLOWED`
- Platform A action receipt in moved bundle: preserved and signature-valid
- Wallet policy authority: `NONE`
- Decision authority: `RECEIVER_GATE`

Run it:

```bash
openline-wallet demo-platform-exit --output platform-exit-artifacts
```

The command exits nonzero unless every pass condition holds.

## Falsifiers

The product claim fails if any of these occur:

- A modified event or receipt verifies.
- A subject can add scope through `narrow`.
- A receiver accepts a bundle from an unpinned root.
- A stale or competing head replaces an admitted current head.
- A copied presentation executes twice.
- Agent A executes after Platform B has durably admitted the revocation.
- The wallet emits an action authorization without a receiver Gate.

## Unearned claims

This demo does not establish cross-machine propagation, production key custody, durable receiver restart recovery, secure guardian recovery, mobile/offline synchronization, or global identity proof. Those remain separate engineering problems. The 600-second export lifetime bounds offline exposure; it does not make revocation instantaneous.
