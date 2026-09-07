# PROVIDER-VERIFIER-BINDING-001

## Scope and finding

The independent audit found no remaining effect-closure hole within the stated
single-receiver GitHub merge contract. It identified a verifier weakness:
several signed records were accepted using their own embedded public keys,
while only selected principal, mandate, and target relationships were checked.

The original verifier can accept a validly signed closure from an unrelated
receiver when the other checked fields are preserved and the disposable packet
is re-signed. The new regression reproduces that acceptance using the exact
historical verifier, then confirms that the repaired verifier rejects the same
substitution against the expected receiver context.

## Repair

Only evidence appraisal, its tests, and the CI invocation change. The verifier
now binds the expected principal root and derived principal ID, receiver IDs
and keys, subject IDs and grant keys, mandates, action and canonical merge
target. It verifies the Wallet's authenticated grant/revocation history and
binds each admission, frontier decision, stopped result, effect receipt, and
closure to the correct receiver. It checks receipt hash links, revocation heads,
closure scope, confirmed-effect hashes, target identity, commit parents, and
held-acknowledgement ordering.

Historical verification uses the explicitly pinned principal, receiver, and
target expectations from the original packet. A fresh fixture requires either
a caller-supplied trusted context or the explicit `--self-attested-fixture`
option. The latter proves internal consistency only; it is not an independent
identity anchor. No receipt grants authority by virtue of being signed.

## Historical source closure

The original result and all original evidence files remain byte-for-byte
unchanged. `historical-source-packet.zip` is a new, hash-bound copy of the seven
source files recorded in the original result. Its SHA-256 is pinned in the
verifier, and every member is checked against the original source-hash map.

This preserves historical source appraisal after the verifier changes. It does
not assert that the old source packet has exact Git-tree identity with the
historical base commit. That provenance limitation remains unresolved. Fresh
fixture packets must bind the exact current source set.

## Verification

The 20 new regressions include a clean historical packet, a fresh fixture,
explicit-context requirements, a legacy accepted-forgery falsifier, foreign
receiver signatures, validly signed wrong principal/mandate/gate/subject/target,
revocation-head and scope substitutions, broken receipt relationships, invalid
Wallet history, and source-set/archive tampering.

The available local suite passed 82 tests, including the existing 32 provider
tests, 14 local effect-closure tests, the new verifier tests, and available
predecessor tests. A fresh controlled reproduction and explicit self-attested
verification passed. Historical predecessor reappraisal passed, preserving the
unresolved Git-tree provenance field. Python compilation and archive integrity
passed. The complete upstream CI matrix and wheel build remain pending until
the patch is pushed.

## Claim and stopping boundary

The controlled result remains `CONTROLLED_TRANSPORT_BOUNDARY_ENFORCED`.
WALLET-004, local closure, and fixed-set closure retain their original scopes.
Live run 34163238256 remains `INCONCLUSIVE`, reappraised as
`LIVE_GITHUB_MERGE_OBSERVED_CLOSURE_UNRESOLVED`. The repair to delayed GitHub
merge evidence is unchanged. No new live mutation, sandbox change, policy
engine, receipt schema, or runtime authority path is introduced.

After the full downstream CI passes, freeze this verifier repair. Do not
rerun the old live target, manufacture missing receipts, or broaden the
revocation claim.
