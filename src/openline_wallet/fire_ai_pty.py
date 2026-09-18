"""Pty driver for FIRE-AI-LIVE-001 Worker A.

Keeps ONE interactive ``claude`` process alive across revocation so the same
AI worker session (same PID, same session transcript) performs the
pre-revocation turn and the post-revocation continuation turn.

Only stdlib is used (pty/select/termios). The scientific evidence comes from
the Receiver (receipts/ledger), never from parsing the TUI; the transcript
JSONL is used only for turn-end detection and session-continuity evidence.
"""
from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import termios
import time
import tty
from dataclasses import dataclass, field
from pathlib import Path


_ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]")
_ANSI2 = re.compile(rb"\x1b[()][AB0]")
_WS = re.compile(rb"\s+")

TRUST_MARKER = b"Yes,Itrustthisfolder"
# Shown by claude-code when ANTHROPIC_API_KEY is set in the environment.
API_KEY_MARKER = b"DoyouwanttousethisAPIkey?"
LOGIN_MARKER = b"Selectloginmethod"
IDLE_HINT_MARKERS = (b"forshortcuts",)  # main prompt status line fragments

TURN_END_REASONS = {"end_turn", "stop_sequence"}


def _norm(raw: bytes) -> bytes:
    text = _ANSI.sub(b"", raw)
    text = _ANSI2.sub(b"", text)
    return _WS.sub(b"", text)


@dataclass
class ClaudeSession:
    pid: int
    fd: int
    workdir: Path
    transcript_path: Path
    argv: list[str]
    output_path: Path
    _output: bytearray = field(default_factory=bytearray, repr=False)
    _lines_seen: int = 0

    def is_alive(self) -> bool:
        # Reap a zombie child: kill(pid, 0) succeeds on zombies, so a
        # WNOHANG waitpid is the real liveness check for our own child.
        try:
            done, _ = os.waitpid(self.pid, os.WNOHANG)
            if done == self.pid:
                return False
        except ChildProcessError:
            return False
        except OSError:
            pass
        try:
            os.kill(self.pid, 0)
        except (OSError, ProcessLookupError):
            return False
        return True

    def _drain(self, timeout: float = 0.3) -> None:
        end = time.time() + timeout
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.2)
            if not ready:
                break
            try:
                chunk = os.read(self.fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            self._output += chunk

    def _wait_for(self, marker: bytes, timeout: float) -> bool:
        """Wait until the normalized output contains marker."""
        end = time.time() + timeout
        while time.time() < end:
            self._drain(0.5)
            if marker in _norm(bytes(self._output)):
                return True
            if not self.is_alive():
                return False
        return marker in _norm(bytes(self._output))

    def send_prompt(self, text: str) -> None:
        """Send one prompt line to the live session."""
        if not self.is_alive():
            raise RuntimeError("FIRE_AI_SESSION_DEAD")
        # Record transcript baseline so wait_turn_end only watches new records.
        self._lines_seen = self._count_transcript_lines()
        os.write(self.fd, text.encode("utf-8") + b"\r")

    def _count_transcript_lines(self) -> int:
        try:
            with open(self.transcript_path, "rb") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return 0

    def _new_assistant_ends(self) -> int:
        """Count post-baseline assistant records that ended a turn."""
        count = 0
        try:
            with open(self.transcript_path, "rb") as handle:
                lines = handle.readlines()
        except OSError:
            return 0
        for line in lines[self._lines_seen:]:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if record.get("type") == "assistant":
                reason = (record.get("message") or {}).get("stop_reason")
                if reason in TURN_END_REASONS:
                    count += 1
        return count

    def wait_turn_end(self, timeout: float) -> bool:
        """Wait for the current turn to end (transcript-based)."""
        end = time.time() + timeout
        last_activity = time.time()
        last_len = len(self._output)
        while time.time() < end:
            self._drain(1.0)
            if len(self._output) != last_len:
                last_activity = time.time()
                last_len = len(self._output)
            ended = self._new_assistant_ends()
            quiet = (time.time() - last_activity) >= 10.0
            if ended >= 1 and quiet:
                return True
            if not self.is_alive():
                return self._new_assistant_ends() >= 1
        return False

    def close(self) -> None:
        try:
            os.write(self.fd, b"\x03")  # best-effort interrupt
        except OSError:
            pass
        time.sleep(1.0)
        if self.is_alive():
            try:
                os.kill(self.pid, signal.SIGTERM)
            except OSError:
                pass
            end = time.time() + 5.0
            while self.is_alive() and time.time() < end:
                time.sleep(0.2)
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            with open(self.output_path, "wb") as handle:
                handle.write(bytes(self._output))
        except OSError:
            pass


def _find_session_transcript(config_dir: Path, workdir: Path,
                             known_before: set[str]) -> Path:
    # Claude Code encodes the cwd as '-' + path with '/' -> '-'.
    encoded = "-" + str(workdir).replace("/", "-")
    projects = config_dir / "projects" / encoded
    deadline = time.time() + 60.0
    while time.time() < deadline:
        if projects.is_dir():
            candidates = sorted(
                (p for p in projects.glob("*.jsonl")
                 if p.name not in known_before),
                key=lambda p: p.stat().st_mtime,
            )
            if candidates:
                return candidates[-1]
        time.sleep(1.0)
    raise RuntimeError("FIRE_AI_TRANSCRIPT_NOT_FOUND")


def spawn_claude_session(*, workdir: Path, mcp_config: Path,
                         allowed_tools: str, config_dir: Path,
                         artifacts_dir: Path,
                         argv: list[str] | None = None,
                         trust_timeout: float = 120.0,
                         expect_trust: bool = True,
                         idle_marker: bytes | None = IDLE_HINT_MARKERS[0],
                         check_auth: bool = True,
                         transcript_path: Path | None = None,
                         extra_env: dict | None = None) -> ClaudeSession:
    """Spawn one interactive claude process and return when it is at its prompt.

    Answers the first-run setup dialogs: the folder-trust dialog and the
    custom-API-key confirmation shown when ANTHROPIC_API_KEY is set. Both are
    environment setup, not experiment content. Raises on any failure; callers
    treat pre-scientific-contact failures as harness failures (not INCOMPLETE).
    """
    workdir = workdir.resolve()
    projects_dir = config_dir / "projects" / ("-" + str(workdir).replace("/", "-"))
    known_before = {p.name for p in projects_dir.glob("*.jsonl")} if projects_dir.is_dir() else set()

    if argv is None:
        argv = [
            "claude",
            "--mcp-config", str(mcp_config),
            "--allowedTools", allowed_tools,
        ]
    pid, fd = pty.fork()
    if pid == 0:
        # Child: raw mode so keystrokes reach the TUI immediately.
        try:
            tty.setraw(0)
        except termios.error:
            pass
        os.environ["TERM"] = "xterm-256color"
        os.environ["CLAUDE_CONFIG_DIR"] = str(config_dir)
        if extra_env:
            os.environ.update(extra_env)
        os.chdir(workdir)
        os.execvp(argv[0], argv)
        raise RuntimeError("exec failed")  # pragma: no cover

    session = ClaudeSession(
        pid=pid, fd=fd, workdir=workdir,
        transcript_path=transcript_path or Path("__pending__"),
        argv=argv,
        output_path=artifacts_dir / "worker-a-pty-output.bin",
    )
    try:
        # First-run setup dialogs, each answered at most once, in whatever
        # order they appear: the folder-trust dialog, and the custom-API-key
        # confirmation shown when ANTHROPIC_API_KEY is set in the environment.
        if idle_marker is not None:
            deadline = time.time() + trust_timeout
            answered: set[bytes] = set()
            while time.time() < deadline:
                session._drain(0.5)
                if not session.is_alive():
                    raise RuntimeError("FIRE_AI_SESSION_DIED_DURING_SETUP")
                norm = _norm(bytes(session._output))
                if check_auth and LOGIN_MARKER in norm:
                    raise RuntimeError("FIRE_AI_AUTH_MISSING")
                if (expect_trust and TRUST_MARKER not in answered
                        and TRUST_MARKER in norm):
                    time.sleep(1.5)
                    os.write(fd, b"\x1b[B")  # select "Yes, I trust this folder"
                    time.sleep(1.2)
                    session._drain(0.5)
                    os.write(fd, b"\r")
                    answered.add(TRUST_MARKER)
                    time.sleep(2.0)
                    continue
                if API_KEY_MARKER not in answered and API_KEY_MARKER in norm:
                    time.sleep(1.0)
                    os.write(fd, b"\x1b[A")  # move selection from "No" to "Yes"
                    time.sleep(1.0)
                    session._drain(0.5)
                    os.write(fd, b"\r")
                    answered.add(API_KEY_MARKER)
                    time.sleep(2.0)
                    continue
                if idle_marker in norm:
                    break
            else:
                raise RuntimeError("FIRE_AI_PROMPT_NOT_READY")
        session._drain(2.0)
        if transcript_path is None:
            session.transcript_path = _find_session_transcript(
                config_dir, workdir, known_before)
        session._lines_seen = session._count_transcript_lines()
        return session
    except Exception:
        session.close()
        raise
