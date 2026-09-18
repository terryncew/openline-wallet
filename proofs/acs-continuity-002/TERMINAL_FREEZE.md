# ACS-CONTINUITY-002 — TERMINAL FREEZE

**ACS-CONTINUITY-002 IS TERMINAL.**
**NO -003 AUTHORIZED.**
**THIS RESULT PROVES ONLY A PLANNED ONE-WAY HOST REPLACEMENT.**
**IT DOES NOT PROVE ARBITRARY FAILOVER, ACTIVE-ACTIVE AUTHORITY,
DISTRIBUTED EXACTLY-ONCE, OR CONSENSUS-FREE MULTI-WRITER CONSERVATION.**

Integrity-only audit performed 2026-09-17 (PDT). No scientific code was
executed during this audit — hashes recomputed from disk, RESULT.json
read field-by-field. All checks PASS.

## -001 remains permanently FAIL

ACS-CONTINUITY-001: `FAIL_ACS_CONTINUITY_CONSERVATION_OR_REPLAY`, CLOSED.
Untouched at freeze time — prereg `9f1e77180fbfe4ca9f5cf188db122d1095ac7e5002f6d42913ba1751f30f4bd5`,
terminal record `11ce5cf6ea514f4f7d74c0a13d2455d1d68d8b65eaa4145d5215b634b23d605e`,
helper `532cb969d5397c88562efb7d09287d4a57f14e647855bc1a35ed620420a64eb7`,
runner `b2845f035b7f2137591f881e7a6b1b57503101afb64b39574c4883799bb4c541`,
RESULT.json `10f15b46e8778400ef83fc426829ab7ce9eaff7bbad7bdf1726cf864e367ea79`.
Nothing in -002 reinterprets, repairs, overwrites, or softens -001.

## Exact -001 → -002 repair (the only functional delta)

`experiment/run_acs_continuity.py`, case_T7, line 324:

```
-    p = subprocess.Popen([PY, ACS_HOST, "commit", A7, 60],
+    p = subprocess.Popen([PY, ACS_HOST, "commit", A7, str(60)],
```

Raw integer `60` in a subprocess argv list made Python raise TypeError
before launch in -001, so T7's wall-clock SIGKILL sub-path never executed.
The fix converts it to the exact string representation subprocess requires —
the same conversion the runner's own `cli()` applies everywhere else.

Plus one synthetic regression test (`experiment/test_sigkill_launch.py`;
`/tmp` fixtures only; no scientific contact) proving the fixed launch path
reaches the child process.

## Frozen hashes (SHA-256, recomputed from disk at freeze time)

| Artifact | SHA-256 |
|---|---|
| PREREGISTRATION.md | `1bb93452d3911e8f91d4f3260b36b02bcd30632c2ad063a141524e6add2ec958` |
| SEAL.md | `4e6242f2c808782ea1297b0c3dfcc9a2628cd98a581bd83b57952ac8208888f5` |
| TERMINAL_RECORD.md | `0a6dd4239eb227a05dd09fcd032254c3674af0b879d31fe31a5b3b195450d1c3` |
| experiment/acs_host.py | `532cb969d5397c88562efb7d09287d4a57f14e647855bc1a35ed620420a64eb7` (byte-identical to -001) |
| experiment/run_acs_continuity.py | `a2729de06731f98397800bfe17b545ae3003fb52b47402a38aa17c5f7ba83e3c` |
| experiment/test_sigkill_launch.py | `1b3ca2d6ebee8d6d73365a7c38ed0e57db0973f0642e7db144a21c4c9ae43a95` |
| experiment/policy/budget_policy.json | `04f6fcc323576ee22de2ecb5852571cc109a4f7a8e747080d6c80f4d94f420c3` (byte-identical to -001) |
| outputs/RESULT.json | `f8f6ef34639a13b98482c686653280f6428925c030164198383433a6acc0fdcf` |

## Pins

- ACS: `microsoft/agent-governance-toolkit`, tag `v4.1.0`,
  commit `0de71ca6c95cf8b9b975ac96f48eaa7826bbe258`
- Wallet base: `4d26040c0f8e835184ebb710356c9601842610fe`
- Policy SHA (in RESULT.json): `04f6fcc323576ee22de2ecb5852571cc109a4f7a8e747080d6c80f4d94f420c3`

## Scientific invocation (exact, one shot)

```
cd ~/workspace/acs-continuity-002
~/workspace/.venvs/bac/bin/python experiment/run_acs_continuity.py
```

Contact: 2026-09-18T02:41:51Z → 2026-09-18T02:41:58Z.
`rerun: false`. `spend_usd: 0`. Python 3.12.3, cryptography 50.0.1.
No paid calls, no network, no new packages, no new DB, no federation.

## Qualification (pre-contact, synthetic)

- `test_sigkill_launch.py`: PASS — exact fixed T7 argv launched via Popen
  on a synthetic `/tmp` fixture; child returned 0 with `ok:true`; state
  60/0/40. No experiment state touched.
- `run_acs_continuity.py --qualify`: 27/27 PASS (synthetic fixtures only).

## Outcome table (all PASS)

| Case | Result |
|---|---|
| B0 | fresh host: available==100 at start (nothing inherited); commit(60) admitted → 60/0/40 |
| T1 | H1 60/0/40 conserved across cutover (bytes identical at import); B refused 60, admitted 40 → 100/0/0; handoff conserved |
| T2 | closed A2 refused re-close/commit (no equivocation); white-box stale-seq probe rejected (check7: stale seq) |
| T3 | replay of H1 rejected at freshness layer (check7: stale seq 2 ≤ recorded max 2); B unchanged 100/0/0 |
| T4 | H1 (successor B) presented to C rejected (check4: successor binding mismatch); C unchanged |
| T5 | fan-out of H1 to C rejected (check4: successor binding mismatch); no second live host minted; B still 100/0/0 |
| T6 | closed source refused commit/encumber/re-close; state 60/0/40 closed |
| T7 | fault-injection crash: pre-commit state intact, tmp ignored, no handoff; **SIGKILL: disjunctive-consistent**; host proceeded to close normally |
| T8 | handoff survived source death; fresh B8 imported 60/0/40 |
| T9 | destination crash: state 60/0/40 durable, consumed set intact, replay rejected (check7: stale seq 2 ≤ recorded max 2) |
| T10 | encumbered 10 conserved across cutover (50/10/40); 60 and 41 refused; 40 admitted → 90/10/0; released → 90/0/10 |

All 12 frozen conditions true: baseline reset; conservation across cutover;
reject over-budget; admit valid ≤40; replay safe; wrong-successor safe; fork
safe; source-reuse safe; crash safe; encumbered conserved; no shared mutable
coordination; all cases pass.

## Invariant states (observed)

committed + encumbered + available = 100 held in every observed state:
60/0/40, 100/0/0, 30/0/70, 50/10/40, 90/10/0, 90/0/10 — 12 triples extracted
from the case details, all summing to 100.

## Coordination

`inventory_check.ok: true` — "only hosts/*/outputs written; policy
byte-identical". No shared mutable database, no consensus, no federation,
no network coordination used.

## Maximum claim boundary

"Across a planned replacement of one independent ACS enforcement host with
another, a successor-bound OpenLine handoff preserved the owner's cumulative
100-unit authority history without a shared mutable database."

Not claimed: ACS is broken; arbitrary failover; active-active authority;
distributed exactly-once semantics; arbitrary clone recovery; consensus-free
multi-writer conservation; production infrastructure standing.

## Terminality

ACS-CONTINUITY-002 IS TERMINAL. No rerun, no repair, no tuning under this
identity. NO -003 AUTHORIZED. No merge, no public claim — returned for
review first. This record is additive; all sealed -002 artifacts remain
read-only and unchanged.
