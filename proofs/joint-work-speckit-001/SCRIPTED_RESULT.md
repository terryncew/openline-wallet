# JOINT-WORK-SPECKIT-001 scripted result

Status: **FROZEN SCRIPTED PASS**

Verdict:

```text
SCRIPTED_SPECKIT_ARM_PASS_LIVE_NOT_RUN
```

All three CI matrix legs (Python 3.11, 3.12, and 3.13) independently produced the preregistered pass with zero provider calls and zero provider spend.

The pinned Spec Kit artifact root remained unchanged. T101 and T201 overlapped in isolated worktrees under separate Wallet authority. Worker A could not act on T201. T101 reached its checkpoint while T102 remained unresolved. Worker A was revoked; its next T102 action was stopped with `MANDATE_REVOKED`. The signed handoff verified, the successor descended from the accepted T101 checkpoint, and successor T102 passed.

The fixed-nonce candidate remained locally green but Airlock rejected it because the protected cross-task convergence requirement failed. The valid combined candidate passed the same protected Airlock gate and was `ELIGIBLE`.

Earned claim:

> In a zero-provider scripted fixture using a pinned GitHub Spec Kit project definition, Wallet enforced separate task authority and revocation/successor handoff, while Airlock rejected a locally-green cross-task requirement violation and accepted the valid combined implementation.

No live provider, Spec Kit slash command, or production/external effect was run. This freeze does not authorize live spend.

Evidence:
- branch head: `1b957806765d056ca85134de175d3068a0292c7c`
- push workflow: `34673044241`
- PR workflow: `34673069133`
- PR: `#32`
- Spec Kit pin: `d848fb4e18f44640ad6b42e60a280551ee90cdce`
- Airlock pin: `3ef34fb0100516e458cb362a7448c78a72da097b`
- frozen Spec Kit root: `e6bbaf6901a2ae50e6763f593402224bbda5fad3d8b12b51920dada07e6d8c50`

A live-provider arm, if pursued, must be a separately preregistered extension after this scripted result is merged. It must not rewrite this receipt or upgrade this claim retroactively.
