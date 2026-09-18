# Demo: switching hosts must not reset authority

**Your AI can change. Your authority does not reset.**

Two independent agent hosts enforce the same portable policy: 100 units of
owner authority. Host A already committed 60. The owner switches to a fresh
Host B.

A naive B would start from 100 again — the owner's budget silently doubles.
This demo shows the frozen alternative: A's consumed authority travels with
a successor-bound signed receipt, so B starts from 40.

```
100 -> spend 60 -> switch host -> 40 remains
```

## Run it

```sh
python demo/acs-continuity-002/demo.py          # human transcript
python demo/acs-continuity-002/demo.py --json  # deterministic JSON receipt sequence
```

No model calls, no network, no install. The demo is a presentation of the
already-frozen ACS-CONTINUITY-002 result — it reads
`proofs/acs-continuity-002/result-summary.json` and refuses to present
anything unless that frozen result is the PASS verdict. It performs no
authority logic of its own.

## What you will see

- B starts from 40 available, not 100
- B refuses 60, admits 40
- replay of A's receipt refused; receipt shown to the wrong host refused
- A cannot spend after closure
- 10 encumbered units stay encumbered across the cutover
- invariant on both hosts: committed + encumbered + available = 100

## On ACS, accurately

ACS provides portable *policy* evaluation; the host owns *state*. This demo
presents one way to carry conserved authority across a *planned, one-way*
host replacement — portable policy plus portable authority history. It is
not arbitrary failover, not active-active, not a consensus claim.

## Evidence

Frozen result, hashes, and full case table:
[`proofs/acs-continuity-002/`](../../proofs/acs-continuity-002/)
