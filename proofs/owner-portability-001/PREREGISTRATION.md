# OWNER-PORTABILITY-001 preregistration

Recorded before implementing or executing the experiment. Synthetic, offline,
zero paid APIs. No source changes to Wallet, ReferenceGate, crypto, or protocols.

PASS requires all of the following; any unexpected outcome is FAIL and retained:

1. Independently keyed receivers A and B admit the same owner-root-signed bundle.
2. Root-signed preferences are portable constraints, never consent. Explicit owner
   approval binds site, exact signed terms bytes/version, exact action and lifetime.
3. A permits a simulated reservation under matching consent and narrow mandate.
   B requires advertising sharing and refuses before effects. Unknown-root and
   cooperating-but-declining receivers may refuse.
4. Changed terms invalidate old approval. Missing/withdrawn approval and agent
   self-approval cannot cause effects; a separately approved compatible new version can.
5. After owner revokes A and grants B, A is stopped, B continues using only B's
   subject key and public evidence. Original events, refusals and receipts survive.
6. Forged signatures, stale bundle/log, replay, tampered terms, cross-site/action
   approval and consent expiry fail closed. Every negative case has zero effects.
7. Public artifacts contain no private keys. A separate offline verifier checks
   pinned identities, signatures, chain continuity, exact bindings, Gate decisions,
   effect links and history preservation; tampering is rejected.
8. A site-specific baseline permits A's local token and refuses that token at B;
   it needs a distinct B registration. This describes this toy baseline only,
   and establishes no inherent limitation of OAuth or Poppy.

Required checks: existing unittest suite (passed/skipped/failed distinguished),
new adversarial suite, demonstration, separate verifier and tampering checks.
Evidence is self-attested synthetic evidence, not proof of real website adoption,
legal consent, secrecy isolation, complete logs, durable recovery or network revocation.
