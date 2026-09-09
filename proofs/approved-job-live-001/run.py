"""APPROVED-JOB-LIVE-001 — bounded work continues across provider replacement.

This experiment composes the already-proved APPROVED-JOB-001 agreement/handoff
boundary with real Claude Code and Codex hosts when explicitly requested. CI uses
scripted subprocess workers so pull requests need no provider credentials.

The outage condition is induced: provider A is simply absent from the continuation
path after its verified checkpoint. This does not claim an observed Anthropic outage,
full conversation portability, production deployment safety, or duplicate-effect
closure outside the fixture repository.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

AIRLOCK_SHA = "fb02207f3ac561368beeabf9ff168076bf828824"
WALLET_BASE = "eb9dc395e7b941f1966a96d9731a867eea6fd050"
VERDICT = "APPROVED_JOB_LIVE_CONTINUATION_ENFORCED"
HERE = Path(__file__).resolve().parent
PREDECESSOR = HERE.parent / "approved-job-001" / "run.py"


def load_predecessor():
    spec = importlib.util.spec_from_file_location("approved_job_001", PREDECESSOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("APPROVED_JOB_PREDECESSOR_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def command_result(command, cwd, env, timeout):
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return completed


def redacted(text: str) -> str:
    for name in (
        "ANTHROPIC_API_KEY",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    ):
        value = os.environ.get(name)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def provider_env(provider: str, provider_home: Path | None = None) -> tuple[dict[str, str], list[str]]:
    keep = (
        "PATH", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TERM",
        "NO_COLOR", "PYTHONIOENCODING",
    )
    env = {name: os.environ[name] for name in keep if name in os.environ}
    if provider_home is not None:
        provider_home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(provider_home.resolve())
    elif "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    if provider == "codex" and os.environ.get("CODEX_HOME"):
        env["CODEX_HOME"] = os.environ["CODEX_HOME"]
    credentials = {
        "claude": ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"),
        "codex": ("OPENAI_API_KEY",),
    }[provider]
    passed = []
    for name in credentials:
        value = os.environ.get(name)
        if value:
            env[name] = value
            passed.append(name)
    # Explicitly do not forward repository, SSH, or the other provider's credentials.
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "SSH_AUTH_SOCK"):
        env.pop(name, None)
    if provider == "claude":
        env.pop("OPENAI_API_KEY", None)
    else:
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    return env, passed


def version_line(binary: str, env: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            [binary, "--version"], text=True, capture_output=True, env=env,
            check=False, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"unavailable:{type(exc).__name__}"
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0] if text else f"exit-{result.returncode}"


def git(module, repo: Path, *args: str) -> str:
    return module.git(repo, *args)


def run_check(repo: Path, filename: str) -> dict:
    # These are evaluator reads, not candidate writes. Disable bytecode output so
    # checking the candidate cannot dirty the fixture with __pycache__/ and then
    # masquerade as a worker-authored path change.
    result = subprocess.run(
        [sys.executable, "-B", filename], cwd=repo, text=True, capture_output=True,
        check=False, timeout=30,
    )
    return {
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "returncode": result.returncode,
        "stdout": result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
    }


def changed_paths(module, repo: Path) -> list[str]:
    # APPROVED-JOB-001's git helper strips outer whitespace. That means the
    # leading status column in porcelain output can disappear for an unstaged
    # change (" M file" becomes "M file"). Parse both forms instead of slicing
    # at a fixed offset.
    rows = git(module, repo, "status", "--porcelain").splitlines()
    paths = []
    for row in rows:
        if len(row) >= 4 and row[2] == " ":
            paths.append(row[3:])
            continue
        parts = row.split(maxsplit=1)
        if len(parts) == 2:
            paths.append(parts[1])
    return sorted(paths)


def require_only_calculator(module, repo: Path) -> list[str]:
    paths = changed_paths(module, repo)
    if not paths:
        raise RuntimeError("WORKER_MADE_NO_CHANGES: see provider log for blocked tools or incomplete work")
    if paths != ["calculator.py"]:
        raise RuntimeError("WORKER_CHANGED_UNAPPROVED_PATHS:" + ",".join(paths))
    return paths


def codex_sandbox_config(network: bool = True) -> list[str]:
    return [
        "--config", "sandbox_mode=\"workspace-write\"",
        "--config", f"sandbox_workspace_write.network_access={str(network).lower()}",
        "--config", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
        "--config", "sandbox_workspace_write.exclude_slash_tmp=true",
    ]


def sandbox_preflight(output: Path) -> None:
    """Exercise namespace setup and write boundaries without a model or credentials."""
    output.mkdir(parents=True, exist_ok=False)
    results = []
    # Codex 0.153.0 refuses helper aliases below the OS temporary directory.
    # Keep its isolated HOME outside /tmp; never borrow the authenticated home.
    with tempfile.TemporaryDirectory(
        prefix=".approved-job-sandbox-", dir=Path.home()
    ) as root:
        root = Path(root)
        repo = root / "workspace"
        repo.mkdir()
        (repo / ".git").mkdir()
        outside = root / "outside.txt"
        outside.write_text("unchanged")
        env, _ = provider_env("codex", root / "home")
        for name in ("OPENAI_API_KEY", "CODEX_HOME"):
            env.pop(name, None)
        env["CODEX_HOME"] = str(root / "codex-home")
        Path(env["CODEX_HOME"]).mkdir()
        probe = (
            "from pathlib import Path\n"
            "import errno\n"
            "p=Path('allowed.txt'); p.write_text('ok'); assert p.read_text() == 'ok'\n"
            "for path in [Path('.git/forbidden'), Path(" + repr(str(outside)) + ")]:\n"
            "    try: path.write_text('forbidden')\n"
            "    except OSError as e:\n"
            "        if e.errno not in (errno.EACCES, errno.EPERM, errno.EROFS): raise\n"
            "    else: raise RuntimeError('SANDBOX_ALLOWED_FORBIDDEN_WRITE:' + str(path))\n"
            "print('SANDBOX_PROBE_PASS')\n"
        )
        # The file helper always uses restricted networking, independently of
        # the shell setting. Exercise that namespace path as well as the shell.
        for network in (True, False):
            label = "shell" if network else "restricted-helper-policy"
            command = ["codex", "sandbox", "linux", *codex_sandbox_config(network),
                       "--", sys.executable, "-c", probe]
            try:
                result = command_result(command, repo, env, 45)
            except (OSError, subprocess.TimeoutExpired) as exc:
                (output / f"sandbox-{label}.log").write_text(type(exc).__name__ + "\n")
                raise RuntimeError(f"CODEX_SANDBOX_PREFLIGHT_FAILED:{label}") from exc
            log = redacted((result.stdout or "") + "\n" + (result.stderr or ""))
            (output / f"sandbox-{label}.log").write_text(log)
            if result.returncode != 0 or "SANDBOX_PROBE_PASS" not in result.stdout:
                raise RuntimeError(f"CODEX_SANDBOX_PREFLIGHT_FAILED:{label}: see sandbox log")
            if outside.read_text() != "unchanged" or (repo / ".git/forbidden").exists():
                raise RuntimeError("CODEX_SANDBOX_PREFLIGHT_BOUNDARY_FAILURE")
            results.append({"path": label, "returncode": result.returncode, "verified": True})
    write_json(output / "sandbox-preflight.json", {
        "schema": "approved-job-live.sandbox-preflight.v1",
        "provider_called": False,
        "probes": results,
        "scope": "CLI shell with network enabled and restricted helper-equivalent policy; not an agent tool invocation",
    })


class LiveFixture:
    """Use APPROVED-JOB-001's authority/evaluation machinery with a staged task."""

    def __init__(self, root: Path, module):
        self.module = module
        self.root = Path(root)
        self.repo = self.root / "maintenance"
        self.repo.mkdir(parents=True)
        git(module, self.repo, "init", "-q")
        git(module, self.repo, "config", "user.name", "Approved Job Live fixture")
        git(module, self.repo, "config", "user.email", "fixture@example.invalid")

        # The initial bug has two observable parts. Worker A can fix the numeric
        # result while the caller-mutation requirement remains unresolved.
        (self.repo / "calculator.py").write_text(
            "def total(items):\n"
            "    items.sort()\n"
            "    return 0\n"
        )
        (self.repo / "test_basic.py").write_text(
            "from calculator import total\n"
            "assert total([2, 3]) == 5\n"
            "assert total([]) == 0\n"
        )
        (self.repo / "approved_check.py").write_text(
            "from calculator import total\n"
            "items = [3, 1, 2]\n"
            "before = list(items)\n"
            "assert total(items) == 6\n"
            "assert items == before, 'caller input mutated'\n"
        )
        git(module, self.repo, "add", ".")
        git(module, self.repo, "commit", "-qm", "Frozen live handoff task")
        self.base = git(module, self.repo, "rev-parse", "HEAD")

        self.owner = module.Wallet.create(self.root / "owner-wallet")
        self.keys = {}
        for subject in ("owner", "worker-a", "worker-b"):
            key = module.Ed25519PrivateKey.generate()
            module.save_private_key(self.root / "credentials" / subject / "agent.key", key)
            self.keys[subject] = key

        self.terms = {
            "schema": "approved-job.agreement.v1",
            "owner": {
                "principal": self.owner.principal_id,
                "root": self.owner.root_public_key,
            },
            "repository": str(self.repo.resolve()),
            "base": self.base,
            "task": "Fix total(items) so it returns the correct sum without mutating caller input",
            "requirement": "Correct numeric result and preserve caller-provided list order",
            "ordinary_checks": [[sys.executable, "test_basic.py"]],
            "acceptance_checks": [[sys.executable, "approved_check.py"]],
            "protected": ["test_basic.py", "approved_check.py"],
        }
        self.job = module.ApprovedJob.create(self.root / "job", self.terms)
        self.used: set[str] = set()

    def request(self, subject: str, kind: str, payload: dict) -> dict:
        if subject in self.used:
            self.owner.revoke(subject)
        self.used.add(subject)
        self.owner.grant(
            subject_id=subject,
            subject_public_key=self.module.public_key_hex(self.keys[subject]),
            scopes=[self.module.action(kind, payload)],
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            mandate_id=self.module.digest(payload)[:24] + kind,
        )
        challenge = self.job.challenge(subject, kind, payload)
        result = subprocess.run(
            [
                sys.executable,
                str(PREDECESSOR),
                "actor",
                str(self.root / "credentials" / subject),
                subject,
                kind,
            ],
            input=json.dumps({
                "payload": payload,
                "challenge": challenge,
                "bundle": self.owner.export_bundle(),
            }),
            text=True,
            capture_output=True,
            check=True,
            timeout=15,
        )
        return json.loads(result.stdout)

    def send(self, subject: str, kind: str, payload: dict) -> dict:
        request = self.request(subject, kind, payload)
        return self.job.receive(subject, kind, payload, **request)

    def approve(self) -> dict:
        return self.send("owner", "approve", {"job": self.job.job})

    def checkpoint(self, candidate: str, stage: dict) -> tuple[dict, dict]:
        payload = {
            "job": self.job.job,
            "candidate": candidate,
            "note": "Independent stage check passed numeric behavior; approved no-mutation requirement remains unresolved.",
            "unresolved": ["caller input must remain order-equivalent"],
        }
        state = self.send("worker-a", "handoff", payload)
        projection = {
            "schema": "approved-job-live.handoff.v1",
            "job": self.job.job,
            "agreement_digest": self.module.digest(self.job.terms),
            "candidate_commit": candidate,
            "task": self.terms["task"],
            "requirement": self.terms["requirement"],
            "verified_stage": {
                "ordinary": stage["ordinary"]["status"],
                "acceptance": stage["acceptance"]["status"],
            },
            "unresolved": payload["unresolved"],
            "previous_provider_chat_transferred": False,
            "previous_provider_credentials_transferred": False,
        }
        return state, projection

    def submit(self, candidate: str) -> dict:
        state = self.job.state()
        self.send(
            "worker-b",
            "submit",
            {"job": self.job.job, "handoff": state["handoff"], "candidate": candidate},
        )
        return self.job.evaluate(self.repo)


def scripted_worker(repo: Path, phase: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    if phase == "a":
        code = (
            "from pathlib import Path\n"
            "p=Path('calculator.py')\n"
            "s=p.read_text()\n"
            "assert 'items.sort()' in s and 'return 0' in s\n"
            "p.write_text(s.replace('return 0', 'return sum(items)'))\n"
        )
    else:
        code = (
            "from pathlib import Path\n"
            "p=Path('calculator.py')\n"
            "s=p.read_text()\n"
            "assert 'items.sort()' in s and 'return sum(items)' in s\n"
            "p.write_text('def total(items):\\n    return sum(items)\\n')\n"
        )
    return command_result([sys.executable, "-c", code], repo, env, 30)


def invoke_worker(mode: str, phase: str, repo: Path, projection: dict | None) -> dict:
    provider = "claude" if phase == "a" else "codex"
    provider_home = repo.parent / "provider-homes" / provider
    env, credential_names = provider_env(provider, provider_home)

    if mode == "scripted":
        result = scripted_worker(repo, phase, env)
        version = "scripted-subprocess"
        display_command = f"scripted-{provider}-worker"
    elif phase == "a":
        if not shutil.which("claude", path=env.get("PATH")):
            raise RuntimeError("CLAUDE_BINARY_MISSING")
        prompt = (
            "You are Worker A in a bounded continuity experiment. Work only in the current repository. "
            "The owner-approved job is to make total(items) return the correct sum without mutating caller input. "
            "For this checkpoint, make the smallest edit to calculator.py that fixes the numeric return value. "
            "Leave the existing items.sort() line unchanged so the second approved requirement remains unresolved. "
            "Do not inspect or modify test_basic.py or approved_check.py. Edit only calculator.py. Do not commit."
        )
        command = [
            "claude", "-p",
            "--allowedTools", "Read,Edit,Write",
            "--max-turns", "6",
            "--output-format", "json",
            prompt,
        ]
        result = command_result(command, repo, env, 300)
        version = version_line("claude", env)
        display_command = "claude -p [bounded Worker A prompt]"
    else:
        if projection is None:
            raise RuntimeError("HANDOFF_PROJECTION_REQUIRED")
        if not shutil.which("codex", path=env.get("PATH")):
            raise RuntimeError("CODEX_BINARY_MISSING")
        prompt = (
            "You are the replacement worker. The previous model provider is unavailable and must not be consulted. "
            "Continue only from the repository commit already checked out and the verified handoff below. "
            "Fix only the remaining owner-approved requirement. Edit only calculator.py; do not modify tests, "
            "approved checks, git configuration, or history, and do not commit.\n\nVERIFIED HANDOFF:\n"
            + json.dumps(projection, sort_keys=True)
        )
        command = [
            "codex", "exec", "--ephemeral", "--skip-git-repo-check",
            "--sandbox", "workspace-write",
            *codex_sandbox_config(),
            prompt,
        ]
        result = command_result(command, repo, env, 300)
        version = version_line("codex", env)
        display_command = "codex exec [verified replacement handoff]"

    log = redacted((result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or ""))
    return {
        "provider": provider,
        "phase": "before-handoff" if phase == "a" else "after-handoff",
        "mode": mode,
        "version": version,
        "returncode": result.returncode,
        "credential_env_names": credential_names,
        "forbidden_env_forwarded": sorted(
            name for name in ("GITHUB_TOKEN", "GH_TOKEN", "SSH_AUTH_SOCK") if name in env
        ),
        "other_provider_credentials_forwarded": (
            "OPENAI_API_KEY" in env if provider == "claude"
            else any(name in env for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"))
        ),
        "provider_home_isolated": Path(env["HOME"]).resolve() == provider_home.resolve(),
        "other_provider_home_visible_as_home": Path(env["HOME"]).resolve() == (
            repo.parent / "provider-homes" / ("codex" if provider == "claude" else "claude")
        ).resolve(),
        "command": display_command,
        "filesystem_sandbox": ("workspace-write" if mode == "real" and provider == "codex" else "not-applicable"),
        "tool_network_access": bool(mode == "real" and provider == "codex"),
        "log": log[-20000:],
    }


def require_provider_success(info: dict) -> None:
    if info["returncode"] != 0:
        raise RuntimeError(f"{info['provider'].upper()}_WORKER_FAILED:{info['returncode']}")
    if info["forbidden_env_forwarded"] or info["other_provider_credentials_forwarded"]:
        raise RuntimeError("PROVIDER_ENVIRONMENT_ISOLATION_FAILURE")
    if not info["provider_home_isolated"] or info["other_provider_home_visible_as_home"]:
        raise RuntimeError("PROVIDER_HOME_ISOLATION_FAILURE")


def make_commit(module, repo: Path, message: str) -> str:
    require_only_calculator(module, repo)
    git(module, repo, "add", "calculator.py")
    git(module, repo, "commit", "-qm", message)
    return git(module, repo, "rev-parse", "HEAD")


def airlock_pin() -> str:
    import airlock

    repo = Path(airlock.__file__).resolve().parents[2]
    sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True).strip()
    if sha != AIRLOCK_SHA or dirty:
        raise RuntimeError("AIRLOCK_SOURCE_PIN_OR_CLEANLINESS_FAILURE")
    return sha


def run_restart_falsifier(module) -> dict:
    """A valid fix rebuilt from base must not count as continuation from the handoff."""
    with tempfile.TemporaryDirectory(prefix="approved-job-live-restart-") as root:
        f = LiveFixture(Path(root), module)
        f.approve()
        env, _ = provider_env("claude", f.root / "provider-homes" / "claude")
        result = scripted_worker(f.repo, "a", env)
        if result.returncode != 0:
            raise RuntimeError("SCRIPTED_PREDECESSOR_FAILED")
        stage = {"ordinary": run_check(f.repo, "test_basic.py"), "acceptance": run_check(f.repo, "approved_check.py")}
        partial = make_commit(module, f.repo, "Worker A scripted checkpoint")
        f.checkpoint(partial, stage)
        f.owner.revoke("worker-a")

        # Deliberately restart from the original base instead of the verified handoff.
        git(module, f.repo, "checkout", "-q", "--detach", f.base)
        (f.repo / "calculator.py").write_text("def total(items):\n    return sum(items)\n")
        git(module, f.repo, "add", "calculator.py")
        git(module, f.repo, "commit", "-qm", "Invalid restart from original base")
        candidate = git(module, f.repo, "rev-parse", "HEAD")
        state = f.job.state()
        f.send(
            "worker-b",
            "submit",
            {"job": f.job.job, "handoff": state["handoff"], "candidate": candidate},
        )
        try:
            f.job.evaluate(f.repo)
        except subprocess.CalledProcessError:
            return {
                "restart_candidate": candidate,
                "handoff_candidate": partial,
                "rejected": True,
                "reason": "candidate is not a descendant of the verified handoff",
            }
        raise RuntimeError("RESTART_FROM_BASE_WAS_ACCEPTED")


def reproduce(output: Path, mode: str) -> None:
    output.mkdir(parents=True, exist_ok=False)
    module = load_predecessor()
    airlock_sha = airlock_pin()
    invocations: list[dict] = []

    with tempfile.TemporaryDirectory(prefix="approved-job-live-") as root:
        f = LiveFixture(Path(root), module)
        f.approve()
        agreement_before = module.digest(f.job.terms)

        worker_a = invoke_worker(mode, "a", f.repo, None)
        invocations.append({k: v for k, v in worker_a.items() if k != "log"})
        (output / "provider-a.log").write_text(worker_a["log"] + "\n")
        require_provider_success(worker_a)
        changed_a = require_only_calculator(module, f.repo)
        stage = {
            "ordinary": run_check(f.repo, "test_basic.py"),
            "acceptance": run_check(f.repo, "approved_check.py"),
        }
        if stage["ordinary"]["status"] != "PASS" or stage["acceptance"]["status"] != "FAIL":
            raise RuntimeError("HANDOFF_STAGE_NOT_DISCRIMINATING")
        partial = make_commit(module, f.repo, "Worker A verified checkpoint")
        _state, projection = f.checkpoint(partial, stage)
        f.owner.revoke("worker-a")
        write_json(output / "handoff.json", projection)
        (output / "handoff.patch").write_text(git(module, f.repo, "diff", f.base, partial) + "\n")

        # No Worker A/provider-A call or provider-A filesystem home survives into
        # the continuation phase. Provider credentials were environment-scoped;
        # any CLI state written under the isolated Claude HOME is discarded here.
        provider_a_home = f.repo.parent / "provider-homes" / "claude"
        if provider_a_home.exists():
            shutil.rmtree(provider_a_home)
        provider_a_home_removed_before_worker_b = not provider_a_home.exists()
        if not provider_a_home_removed_before_worker_b:
            raise RuntimeError("PROVIDER_A_HOME_SURVIVED_HANDOFF")

        git(module, f.repo, "checkout", "-q", "--detach", partial)
        worker_b = invoke_worker(mode, "b", f.repo, projection)
        invocations.append({k: v for k, v in worker_b.items() if k != "log"})
        (output / "provider-b.log").write_text(worker_b["log"] + "\n")
        require_provider_success(worker_b)
        changed_b = require_only_calculator(module, f.repo)
        before_submit = {
            "ordinary": run_check(f.repo, "test_basic.py"),
            "acceptance": run_check(f.repo, "approved_check.py"),
        }
        if before_submit["ordinary"]["status"] != "PASS" or before_submit["acceptance"]["status"] != "PASS":
            raise RuntimeError("REPLACEMENT_DID_NOT_COMPLETE_APPROVED_JOB")
        candidate = make_commit(module, f.repo, "Worker B replacement continuation")
        final = f.submit(candidate)
        agreement_after = module.digest(f.job.terms)
        if final["status"] != "ELIGIBLE":
            raise RuntimeError("FINAL_CANDIDATE_NOT_ELIGIBLE")
        if agreement_before != agreement_after:
            raise RuntimeError("APPROVED_AGREEMENT_CHANGED")
        if git(module, f.repo, "merge-base", "--is-ancestor", partial, candidate) != "":
            # git merge-base --is-ancestor prints nothing on success.
            raise RuntimeError("UNEXPECTED_ANCESTRY_OUTPUT")

        (output / "continuation.patch").write_text(git(module, f.repo, "diff", partial, candidate) + "\n")
        evidence = {
            "agreement": f.terms,
            "agreement_digest_before_handoff": agreement_before,
            "agreement_digest_after_replacement": agreement_after,
            "base_commit": f.base,
            "handoff_commit": partial,
            "final_candidate": candidate,
            "worker_a_changed_paths": changed_a,
            "worker_b_changed_paths": changed_b,
            "handoff_stage": stage,
            "replacement_pre_submit": before_submit,
            "handoff_projection": projection,
            "final": final,
            "events": f.job.events(),
            "provider_invocations": invocations,
            "provider_a_invocations_after_handoff": 0,
            "full_chat_or_model_memory_transferred": False,
            "provider_a_credentials_transferred_to_provider_b": False,
            "provider_a_home_removed_before_worker_b": provider_a_home_removed_before_worker_b,
            "provider_filesystem_home_transferred_to_provider_b": False,
            "receiver_boundary": "RECEIVER_GATE",
            "external_effect_executed": False,
        }
        write_json(output / "evidence.json", evidence)

    restart = run_restart_falsifier(module)
    write_json(output / "restart-falsifier.json", restart)

    files = [
        "evidence.json", "handoff.json", "handoff.patch", "continuation.patch",
        "provider-a.log", "provider-b.log", "restart-falsifier.json",
    ]
    report = {
        "schema": "approved-job-live-001.result.v1",
        "verdict": VERDICT,
        "mode": mode,
        "live_models": mode == "real",
        "python": platform.python_version(),
        "wallet_predecessor_base": WALLET_BASE,
        "airlock_commit": airlock_sha,
        "predecessor": "APPROVED-JOB-001",
        "receiver_boundary_preflight": "EGRESS-GATE-001",
        "source_sha256": sha256(Path(__file__)),
        "files_sha256": {name: sha256(output / name) for name in files},
        "outage_condition": "induced provider-A absence after verified checkpoint",
        "actual_provider_outage_observed": False,
        "production_effect": False,
        "payment_or_marketplace": False,
        "full_context_portability": False,
        "claim_boundary": (
            "One bounded fixture job, one exact git checkpoint, one owner-approved agreement, and one "
            "replacement continuation. The current EGRESS-GATE-001 Receiver Gate is verified separately as a "
            "preflight; this experiment itself stops at Airlock ELIGIBLE and executes no downstream effect. "
            "Real mode uses Claude Code before the handoff and Codex after it; provider A is intentionally absent "
            "from the continuation path. On GitHub-hosted Linux runners, Codex retains its workspace-write filesystem "
            "sandbox with tool network access enabled and temporary directories excluded from writable roots; "
            "the subprocess still receives no GitHub, SSH, or Claude credentials. This does not establish an "
            "actual provider outage, full conversation/memory portability, production deployment safety, "
            "or duplicate-effect closure for external systems."
        ),
    }
    write_json(output / "result.json", report)
    verify(output, require_real=(mode == "real"))


def verify(output: Path, require_real: bool = False) -> None:
    module = load_predecessor()
    report = json.loads((output / "result.json").read_text())
    evidence = json.loads((output / "evidence.json").read_text())
    restart = json.loads((output / "restart-falsifier.json").read_text())

    assert report["schema"] == "approved-job-live-001.result.v1"
    assert report["verdict"] == VERDICT
    if require_real:
        assert report["mode"] == "real" and report["live_models"] is True
    assert report["actual_provider_outage_observed"] is False
    assert report["production_effect"] is False
    assert report["full_context_portability"] is False
    assert sha256(Path(__file__)) == report["source_sha256"]
    for name, expected in report["files_sha256"].items():
        assert sha256(output / name) == expected

    assert evidence["agreement_digest_before_handoff"] == evidence["agreement_digest_after_replacement"]
    assert evidence["agreement_digest_before_handoff"] == module.digest(evidence["agreement"])
    assert evidence["handoff_stage"]["ordinary"]["status"] == "PASS"
    assert evidence["handoff_stage"]["acceptance"]["status"] == "FAIL"
    assert evidence["replacement_pre_submit"]["ordinary"]["status"] == "PASS"
    assert evidence["replacement_pre_submit"]["acceptance"]["status"] == "PASS"
    assert evidence["final"]["status"] == "ELIGIBLE"
    assert evidence["final"]["handoff_worker"] == "worker-a"
    assert evidence["final"]["submitted_worker"] == "worker-b"
    assert evidence["final"]["handoff_candidate"] == evidence["handoff_commit"]
    assert evidence["provider_a_invocations_after_handoff"] == 0
    assert evidence["full_chat_or_model_memory_transferred"] is False
    assert evidence["provider_a_credentials_transferred_to_provider_b"] is False
    assert evidence["provider_a_home_removed_before_worker_b"] is True
    assert evidence["provider_filesystem_home_transferred_to_provider_b"] is False
    assert evidence["receiver_boundary"] == "RECEIVER_GATE"
    assert evidence["external_effect_executed"] is False
    assert evidence["handoff_projection"]["previous_provider_chat_transferred"] is False
    assert evidence["handoff_projection"]["previous_provider_credentials_transferred"] is False
    assert [item["provider"] for item in evidence["provider_invocations"]] == ["claude", "codex"]
    assert [item["phase"] for item in evidence["provider_invocations"]] == ["before-handoff", "after-handoff"]
    for item in evidence["provider_invocations"]:
        assert item["forbidden_env_forwarded"] == []
        assert item["other_provider_credentials_forwarded"] is False
        assert item["provider_home_isolated"] is True
        assert item["other_provider_home_visible_as_home"] is False
    codex_invocation = evidence["provider_invocations"][1]
    if report["mode"] == "real":
        assert codex_invocation["filesystem_sandbox"] == "workspace-write"
        assert codex_invocation["tool_network_access"] is True
    else:
        assert codex_invocation["tool_network_access"] is False
    assert restart["rejected"] is True

    previous = None
    for event in evidence["events"]:
        assert event["job"] == evidence["agreement_digest_before_handoff"]
        assert event["previous"] == previous
        previous = module.digest(event)

    print(json.dumps({"verdict": VERDICT, "verified": True, "mode": report["mode"]}))


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path)
    group.add_argument("--verify", type=Path)
    group.add_argument("--sandbox-preflight", type=Path)
    parser.add_argument("--mode", choices=("scripted", "real"), default="scripted")
    parser.add_argument("--require-real", action="store_true")
    args = parser.parse_args()
    if args.sandbox_preflight:
        sandbox_preflight(args.sandbox_preflight)
    elif args.output:
        reproduce(args.output, args.mode)
    else:
        verify(args.verify, require_real=args.require_real)


if __name__ == "__main__":
    main()
