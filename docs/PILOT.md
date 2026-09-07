# Try Airlock on a real repository

Airlock is for developers who want coding agents to attempt useful work without turning every attempt into another PR to review. Your repository's checks and protected files decide what earns attention.

The first pilot is deliberately small: one repository, one bounded task, one independent evaluation, and a review of the result. No production deployment or unattended merge is required.

[Apply for a pilot](https://github.com/terryncew/openline-airlock/issues/new?template=pilot.yml) · [Read the operator guide](PILOT_OPERATOR.md)

## What you need

- A Git repository with a committed starting point and meaningful tests or other checks.
- Python 3.11 or newer, Git, and one supported coding-agent CLI you already use.
- A task with an observable acceptance condition: a bug, failing test, small maintenance issue, or measured performance improvement.
- A disposable branch or copy of the repository. Do not use production credentials or give an agent unrestricted access to your machine.

Airlock supports Claude Code, Codex, Aider, OpenCode, and Hermes. It uses the agent CLI and credentials you already have; provider charges remain yours. Worktree isolation is not a security sandbox, so run untrusted native code in a container or VM.

## The first run

From your repository, install the pinned v0.4.0 pilot build in a virtual environment:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install "git+https://github.com/terryncew/openline-airlock.git@eb22a318e537590c081e3dac4a4091c57865eb78"
airlock --help
```

On Windows, activate the environment using your shell's normal activation command. The version is pinned so the pilot can be reproduced; it is not a promise that this commit is the latest release.

Run the existing initializer:

```bash
airlock init
```

Read the discovered checks, protected paths, and baseline status. If the checks are weak, incomplete, or already failing, stop and record that. Airlock cannot infer every maintainer constraint. Add any task-specific checks before the agent starts; do not let the agent author its own acceptance criteria.

Choose one small task. For example:

```bash
airlock solve "Fix the failing test in tests/test_widget.py without changing the test or public API."
airlock inbox
airlock review
```

Replace the example with your actual task. You can also pass an existing GitHub issue number or URL. The normal solve path uses four attempts over two rounds, although the configured execution and installed agents determine what can run. Do not launch a large sweep for the first pilot.

A survivor is a candidate for human review, not permission to merge. Inspect the diff and the recorded checks before accepting anything. Zero survivors is a legitimate result.

## What we want to learn

A useful pilot can end with a kept patch, a rejected patch, a baseline failure, or an inability to encode the maintainer's real constraint. Please tell us what you expected, what Airlock actually returned, how much time or money it used, and whether you would run it again.

Do not paste private repository contents, credentials, customer data, or proprietary logs into a public issue. A sanitized failure summary is enough to begin. Use a private channel for confidential material only after both parties agree on one.

There is no paid plan, hosted service, insurance coverage, or SLA being offered by this pilot. We are testing whether the existing product saves developer attention and what would justify a commercial service.
