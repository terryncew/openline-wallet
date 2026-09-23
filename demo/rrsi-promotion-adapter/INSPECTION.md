# RRSI source inspection

Source: https://github.com/google-research/rrsi
Pinned commit: `e4d1a7a0388e02b388bc40eb0a125fcfc7123f8d` (2026-09-21).
The clone is treated as outside apparatus: read-only, never modified.

## (a) Candidate worktree/commit creation

`rrsi/gitops.py`. Every candidate harness is drafted in its own git worktree
on a new branch `<domain>/r<t><variant>` forked off the incumbent commit
(`worktree_add`). Edits are committed there with `commit_path` (author
`rrsi@localhost`). The candidate's identity is the commit; the harness
identity is the harness tree hash `git rev-parse <commit>:<harness path>`
(`tree_hash`). Commits outside the harness path never look like a change.

## (b) Internal selection and incumbent-update path

`rrsi/selection.py` implements Algorithm 2 as pure functions. Each screened
candidate is judged (`judge`): a noise-adjusted floor (`S' >= S* - delta`), a
cost rule (added inference tokens must be paid for by measured gain), and
domain guards. The winner is the argmax of score among admissible candidates
(`select_round`); with no admissible candidate the incumbent stands.
`rrsi/loop.py` then fast-forwards `refs/heads/evolve/<domain>` to the winner
commit (`fast_forward`) and records the new incumbent commit plus harness
tree hash in `runs/<domain>/frontier.json`.

## (c) How the downstream runtime selects and loads its harness

The executing harness is whatever the checkout the driver runs in contains.
Coding domain: `domains/coding/scripts/run_eval.sh` sets
`PYTHONPATH=<repo>/third_party` and runs harbor with
`--agent harbor_terminus2:AgentHarness`, importing the harness module from
that checkout's `third_party/harbor_terminus2`. Workspace/eng domains:
`workspace_driver.py` inserts `<checkout>/third_party/archipelago` into
`sys.path` and imports `harness_workspace.main` / `harness_eng.main` via
`RRSI_HARNESS_MODULE`. For evaluation the driver runs from the candidate
worktree, so the candidate's harness is what executes. There is no separate
deployment step inside RRSI: "which harness runs" is "which commit the
checkout is at".

## (d) Credentials and filesystem permissions that would allow promotion

None beyond local filesystem access. Git runs as the local OS user with
`user.email=rrsi@localhost`; promotion is `git update-ref
refs/heads/evolve/<domain>` executed by the same process and user that ran
the proposer and the evaluator. There is no credential in RRSI that gates
promotion. The proposer, the evaluator, and the "deployer" are one
principal; write access to the repo directory is the entire authority.

## The distinction this adapter enforces

- RRSI's research incumbent: the `evolve/<domain>` branch ref inside RRSI's
  own search workspace. RRSI may manage it freely.
- The operator's deployed harness: a separate protected runtime. RRSI has
  zero write authority over it.

Inside RRSI these two roles collapse into one process. The adapter keeps
them apart: the optimizer signs a candidate package, and only the
operator-owned receiver — after its own checks, against its pinned base —
may write the exact accepted artifact into the deployment dir. A signed
receipt alone is not enforcement; the mandate check, the hash re-verification
at invocation, and the operator-only write path are.
