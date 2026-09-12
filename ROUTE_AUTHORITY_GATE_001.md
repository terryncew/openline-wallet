# ROUTE-AUTHORITY-GATE-001 — smallest general route-invariant repair

Status: **preregistered repair experiment**

## Parent result

This experiment follows the frozen negative receipt `ROUTE-AUTHORITY-COLD-001`.
That receipt is not modified or rerun.

The parent falsifier established one narrow hole: an unauthorized recipient could
not directly read a protected source, yet the same protected information could
reach that recipient after an authorized reader used a separately authorized
generic peer-message action.

## Repair

The receiver owner may now register a source action with the authority that any
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

PASS:

`ROUTE_AUTHORITY_REPAIR_PASS`

FAIL:

`ROUTE_AUTHORITY_REPAIR_FAILED`

Any broken control:

`INCONCLUSIVE_SETUP_OR_CONTROL_FAILURE`

## Claim boundary if PASS

> In one bounded local fixture, a receiver-owned carried-authority check made the
> consequence invariant to the tested route: after a worker observed a protected
> source, a separately authorized generic send could not deliver a transformed
> result to a recipient lacking that source authority, while unexposed messaging
> and delivery to an independently authorized recipient still worked.

## Run

```sh
python -m pip install -e ".[mcp]"
python -m unittest discover -s tests -v
python proofs/route-authority-gate-001/run.py --output route-authority-gate-artifacts
python proofs/route-authority-gate-001/verify.py route-authority-gate-artifacts
```
