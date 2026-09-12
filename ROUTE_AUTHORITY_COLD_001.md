# ROUTE-AUTHORITY-COLD-001 — does authority survive an agent relay?

## External trigger

Anthropic Claude Code issue `#92060` reports a route-dependent permission boundary: a direct read across a session folder boundary prompts, while the same information can be obtained by asking the other session to read its own file and relay the contents through cross-session messaging.

OpenLine should not assume it is immune. This experiment tests the current Wallet + `ReferenceGate` reference semantics before adding a repair.

## Question

If agent B lacks authority to read a protected source, can those same protected bytes still reach B when agent A is allowed to read the source and is also allowed to send ordinary peer messages?

The owner policy is frozen before the run:

- agent A may read `payments-research` and send ordinary peer messages;
- agent B may work in `fintech-memo` but may not read `payments-research`;
- generic permission to message another agent must not erase the source access boundary.

## Falsifier

The controlled fixture runs five cases:

1. B directly requests `data:read:payments-research` — must stop.
2. A requests the same protected read — must pass.
3. A sends benign public bytes through the generic peer-message action — must pass and advance an independent delivery witness.
4. A sends bytes originating in the protected source through that same generic message action — this is the falsifier.
5. A tries a payload-specific message action that was not granted — must stop, proving exact-action gating still works when the protected distinction is explicitly encoded in the action.

If case 4 is stopped while case 3 is allowed, the claimed hole is not reproduced. If case 4 is allowed and the delivery witness records the protected payload at B, the current reference abstraction is route-local: it enforces each encoded action but does not automatically propagate source authority across a relay.

## Why the delivery witness is separate

The witness advances only after the Gate returns `ALLOWED`. It records recipient, origin scope, and payload hash. The Gate is therefore not the only component claiming whether the protected bytes reached B in the fixture.

## Claim boundary

This is a local controlled falsifier, not a reproduction against Claude Code itself. It does not test every OpenLine component, claim that all peer messaging should be blocked, or prescribe payload inspection as the repair. A reproduced hole earns only this narrow statement:

> Under the current Wallet/ReferenceGate abstraction, separately authorized actions do not automatically carry a protected source's authority boundary through an agent-to-agent relay.

No product repair belongs in this experiment. Freeze the result first; only then design the smallest general fix.

## Run

```sh
python -m pip install -e .
python proofs/route-authority-cold-001/run.py --output route-authority-cold-artifacts
python proofs/route-authority-cold-001/verify.py route-authority-cold-artifacts
```
