# INTEGRITY_MANIFEST — ACS-CONTINUITY-002 evidence package

All hashes SHA-256. The sealed experiment artifacts live read-only in the
investigator's workspace (`~/workspace/acs-continuity-002/`); this package
references them by hash rather than promoting the experimental apparatus
into production. No host implementation, runner, test harness, host state,
or ACS source is included here, and nothing in this package changes Wallet
runtime behavior.

## Sealed study artifacts (workspace originals)

| Artifact | SHA-256 | Role |
|---|---|---|
| PREREGISTRATION.md | `1bb93452d3911e8f91d4f3260b36b02bcd30632c2ad063a141524e6add2ec958` | frozen contract (read-only, pre-contact) |
| SEAL.md | `4e6242f2c808782ea1297b0c3dfcc9a2628cd98a581bd83b57952ac8208888f5` | pre-contact qualification seal |
| TERMINAL_RECORD.md | `0a6dd4239eb227a05dd09fcd032254c3674af0b879d31fe31a5b3b195450d1c3` | terminal verdict record |
| TERMINAL_FREEZE.md | `121c2205420fb2bd988d2cd777407ca26d6524007007f29ecd253c91b89d9770` | integrity-only terminal freeze (this package carries a copy) |
| experiment/acs_host.py | `532cb969d5397c88562efb7d09287d4a57f14e647855bc1a35ed620420a64eb7` | host mechanism (byte-identical to -001) |
| experiment/run_acs_continuity.py | `a2729de06731f98397800bfe17b545ae3003fb52b47402a38aa17c5f7ba83e3c` | runner (one-line harness fix vs -001) |
| experiment/test_sigkill_launch.py | `1b3ca2d6ebee8d6d73365a7c38ed0e57db0973f0642e7db144a21c4c9ae43a95` | synthetic regression test |
| experiment/policy/budget_policy.json | `04f6fcc323576ee22de2ecb5852571cc109a4f7a8e747080d6c80f4d94f420c3` | portable policy (byte-identical to -001) |
| outputs/RESULT.json | `f8f6ef34639a13b98482c686653280f6428925c030164198383433a6acc0fdcf` | frozen scientific result |

## This package's files

| File | SHA-256 |
|---|---|
| README.md | `e6489d8ce76759739b2b976b1679bf8c061e13be3c70cf655cac25376a337621` |
| TERMINAL_FREEZE.md | `121c2205420fb2bd988d2cd777407ca26d6524007007f29ecd253c91b89d9770` |
| result-summary.json | `f468052126da4993a5d76c315af9944cd19bd0163388044d7978bb68dba3d27c` |
| INTEGRITY_MANIFEST.md | (this file; verify by recomputation) |

## Pins

- ACS: `microsoft/agent-governance-toolkit`, tag `v4.1.0`,
  commit `0de71ca6c95cf8b9b975ac96f48eaa7826bbe258`
- Wallet base at study time: `4d26040c0f8e835184ebb710356c9601842610fe`

## Result identifiers

- Terminal class: `PASS_ACS_CONTINUITY_PLANNED_HOST_HANDOFF_CONSERVES_AUTHORITY`
- Scientific contact: 2026-09-18T02:41:51Z → 2026-09-18T02:41:58Z
- `rerun: false`, `spend_usd: 0`
- Predecessor ACS-CONTINUITY-001: `FAIL_ACS_CONTINUITY_CONSERVATION_OR_REPLAY`
  (permanent; not repaired by this study)

## How to verify

Recompute any hash above with `sha256sum <file>` and compare. The
workspace originals are read-only; the package files are committed in the
`proof/acs-continuity-002` branch history of this repo.
