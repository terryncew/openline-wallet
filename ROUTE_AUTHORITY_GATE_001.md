# ROUTE-AUTHORITY-GATE-001 — smallest general route-invariant repair

Status: **FROZEN — ROUTE_AUTHORITY_REPAIR_PASS**

## Parent result

This experiment follows the frozen negative receipt `ROUTE-AUTHORITY-COLD-001`.
That receipt remains unchanged.

The parent falsifier established one narrow hole: an unauthorized recipient could
not directly read a protected source, yet the same protected information could
reach that recipient after an authorized reader used a separately authorized
generic peer-message action.

## Repair

The receiver owner may register a source action with the authority that any
later recipient must independently hold.

When a subject successfully uses that protected source action, the Gate records
a conservative carried-scope set for that subject. Before a routed consequence,
the receiver requires two independent decisions:

1. the ordinary exact action must be `ALLOWED`;
2. the route must be `ALLOWED` for the actual recipient.

An unexposed sender has no carried scopes and ordinary messaging still works.
An exposed sender may route to a recipient that independently holds the carried
scope. Routing to a recipient that lacks it must stop.

No payload inspection or semantic classifier is used. The worker does not
declare its own lineage. The receiver configures the protected source before
execution.

## Conservative boundary

This first repair is subject/session level, not byte-level provenance. Once a
subject observes a protected source, its carried requirement is monotonic for
the lifetime of the Gate instance. There is deliberately no declassification
operation in this patch.

That can over-block benign messages from an exposed subject. The trade is
explicit: this experiment asks whether a receiver can close the route hole
without trusting the worker to identify which later words came from the source.

It does not yet prove cross-Gate propagation, cross-principal federation,
durable restart recovery of carried scopes, or safe declassification.

## Fresh falsifier

The fresh fixture uses a different source and different subjects than the
frozen parent receipt.

- `agent-source` may read `records:read:payroll` and send peer messages.
- `agent-blocked` may receive messages but lacks payroll authority.
- `agent-allowed` may receive messages and independently holds payroll authority.
- `agent-clean` may send messages and has never observed the protected source.

The receiver preregisters:

`records:read:payroll -> recipient must hold records:read:payroll`

The experiment requires all of the following:

- `agent-blocked` direct payroll read stops;
- an ordinary message from `agent-clean` to `agent-blocked` still clears;
- `agent-source` reads payroll and acquires the carried scope;
- its generic message action itself remains allowed;
- the route to `agent-blocked` stops with `RECIPIENT_LACKS_SOURCE_AUTHORITY`;
- no protected transformed payload reaches the independent delivery witness for `agent-blocked`;
- a fresh generic message action from the same exposed sender may route to `agent-allowed`;
- route receipts verify under the receiver Gate key.

## Frozen result

Parent negative merge:

`434f107a5a6bf3183fc3c11ceacbb3c78815bbf9`

Repair head:

`4503fdd5823550684aee0862cc376237a73db5f6`

PR:

`#35`

Conclusive workflow run:

`34713157773`

Python 3.11, 3.12, and 3.13 all passed the full unit suite, the fresh
ROUTE-AUTHORITY-GATE-001 experiment, and the independent verifier.

Terminal verdict on all three:

`ROUTE_AUTHORITY_REPAIR_PASS`

The fresh falsifier behaved as required:

- unauthorized direct payroll read stopped;
- clean sender -> unauthorized recipient ordinary message remained allowed;
- protected read by `agent-source` created the carried payroll scope;
- generic send by that exposed sender remained action-authorized;
- route to `agent-blocked` stopped with `RECIPIENT_LACKS_SOURCE_AUTHORITY`;
- the delivery witness recorded no protected transformed payload at `agent-blocked`;
- a fresh generic send from the exposed sender routed successfully to `agent-allowed`.

All other Wallet workflows on the same head also completed successfully:
CI, AUTHORITY-IN-TIME-001, STOP-BARRIER-COLD-001, ROUTE-AUTHORITY-COLD-001,
AGT-EXIT-COLD-001, APPROVED-JOB-001, APPROVED-JOB-LIVE-001, COORDINATOR-001,
JOINT-WORK-LIVE-001, JOINT-WORK-SPECKIT-001, and JOINT-WORK-SPECKIT-LIVE-001.

The three result hashes differ because each matrix run creates fresh ephemeral
keys and therefore different signed receipt bytes. The semantic verdict and
all preregistered outcome predicates were the same across Python versions.
Python 3.12 is frozen as the canonical result artifact.

## Earned claim

> In one bounded local fixture, a receiver-owned carried-authority check made the
> consequence invariant to the tested route: after a worker observed a protected
> source, a separately authorized generic send could not deliver a transformed
> result to a recipient lacking that source authority, while unexposed messaging
> and delivery to an independently authorized recipient still worked.

## Nonclaims

This does not prove byte-level provenance, safe declassification, cross-Gate
propagation, cross-principal federation, durable restart recovery of carried
authority, or a production deployment. It does not claim the worker can
self-report trustworthy lineage.

## Run

```sh
python -m pip install -e ".[mcp]"
python -m unittest discover -s tests -v
python proofs/route-authority-gate-001/run.py --output route-authority-gate-artifacts
python proofs/route-authority-gate-001/verify.py route-authority-gate-artifacts
```
