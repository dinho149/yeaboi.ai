"""The coding-agent driver: spawn and supervise a headless Claude Code run.

The provider seam is archon's three-method shape (``run`` / ``get_type`` /
``get_capabilities``) so a second agent CLI can slot in later; the Claude Code
implementation is ruflo's hard-won recipe, every detail of which was a
production failure upstream:

- **The prompt goes over stdin, never argv** — write then close; the EOF is
  what unblocks ``--print``. (Upstream scar: Windows re-tokenizes argv through
  ``cmd.exe`` and a prompt containing ``>`` created files.)
- **Session env vars are stripped** (``CLAUDE_SESSION_ID``,
  ``CLAUDE_PARENT_SESSION_ID``, ``CLAUDECODE``): Claude Code detects a nested
  session and exits immediately, and yeaboi is very often launched from inside
  one.
- **The child gets its own process group** (``start_new_session=True``) and
  the kill ladder targets the whole group: ``claude`` spawns grandchildren
  (MCP bridges, tools), and a plain ``kill()`` orphans them to init.
- **The JSON envelope is parsed leniently**: a schema surprise from an older
  or newer CLI degrades to raw text with a warning, never an exception —
  downstream, the deterministic bridge judges the run by the diff on disk,
  not by what the agent said.

Supervision follows ``voice_install._run_installer``: a daemon pump thread
reads stdout for the process's whole life (so the pipe can never fill and
block the child), and the supervisor loop polls a cancel event and a
monotonic deadline.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30 * 60  # one run implements one story; longer means stuck
_KILL_GRACE_S = 5.0
_POLL_S = 0.2
_PROBE_TIMEOUT_S = 5.0
_TAIL_LINES = 400

# Claude Code refuses to start inside another Claude session; these are how it
# knows. yeaboi is routinely launched from one, so the child must not inherit.
_SESSION_ENV_VARS = ("CLAUDE_SESSION_ID", "CLAUDE_PARENT_SESSION_ID", "CLAUDECODE")


@dataclass(frozen=True)
class DriverResult:
    """What one supervised run produced. ``ok`` means the process exited 0 —
    whether it actually *did* anything is the pipeline bridge's question."""

    ok: bool = False
    output: str = ""  # the agent's final text (envelope `result`, else raw tail)
    session_id: str = ""
    returncode: int = -1
    timed_out: bool = False
    cancelled: bool = False
    error: str = ""  # tail of output when the run failed
    duration_s: float = 0.0
    cost_usd: float = 0.0  # the envelope's own figure; 0.0 when absent
    num_turns: int = 0
    warnings: tuple[str, ...] = ()


class AgentDriver(Protocol):
    """The provider seam: anything that can run one prompt in one directory."""

    def run(
        self,
        prompt: str,
        cwd: Path,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        cancel_event: threading.Event | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> DriverResult: ...

    def get_type(self) -> str: ...

    def get_capabilities(self) -> dict[str, object]: ...


def _terminate_group(proc: subprocess.Popen) -> None:
    """SIGTERM the child's whole group, escalate to SIGKILL after a grace."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.terminate()
    try:
        proc.wait(timeout=_KILL_GRACE_S)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    try:
        proc.wait(timeout=_KILL_GRACE_S)
    except subprocess.TimeoutExpired:
        logger.error("Agent process %s would not die", proc.pid)


def _parse_envelope(raw: str) -> dict:
    """The ``--output-format json`` envelope, or {} — never an exception."""
    text = raw.strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        pass
    # Some CLI versions print progress lines before the envelope; try the tail.
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                return data if isinstance(data, dict) else {}
            except ValueError:
                continue
    return {}


class ClaudeCodeDriver:
    """Run Claude Code headless: ``claude --print --output-format json``."""

    def __init__(self, binary: str = "claude") -> None:
        self._binary = binary

    def get_type(self) -> str:
        return "claude_code"

    def get_capabilities(self) -> dict[str, object]:
        return {"session_resume": True, "structured_output": "best-effort", "cost_visibility": True}

    def available(self) -> tuple[bool, str]:
        """(usable, detail). Probed with a hard timeout; never raises."""
        path = shutil.which(self._binary)
        if not path:
            return False, f"{self._binary} not found on PATH"
        try:
            proc = subprocess.run(  # noqa: S603 — fixed binary, fixed args
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, f"{self._binary} --version failed: {exc}"
        if proc.returncode != 0:
            return False, f"{self._binary} --version exited {proc.returncode}"
        return True, (proc.stdout or "").strip()

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        for var in _SESSION_ENV_VARS:
            env.pop(var, None)
        return env

    def run(
        self,
        prompt: str,
        cwd: Path,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        cancel_event: threading.Event | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> DriverResult:
        """Supervise one run to completion, cancellation, or timeout. Never raises."""
        started = time.monotonic()
        warnings: list[str] = []
        try:
            proc = subprocess.Popen(  # noqa: S603 — fixed binary + flags; prompt over stdin
                [self._binary, "--print", "--output-format", "json"],
                cwd=str(cwd),
                env=self._child_env(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
        except OSError as exc:
            return DriverResult(error=f"could not launch {self._binary}: {exc}")
        logger.info("Agent run started (pid %s, cwd %s)", proc.pid, cwd)

        tail: deque[str] = deque(maxlen=_TAIL_LINES)

        def _pump() -> None:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.rstrip("\n")
                tail.append(line)
                if on_line is not None:
                    try:
                        on_line(line)
                    except Exception:  # a UI callback must never kill the pump
                        pass

        pump = threading.Thread(target=_pump, name="ship-agent-pump", daemon=True)
        pump.start()
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt)
            proc.stdin.close()
        except (BrokenPipeError, OSError) as exc:
            warnings.append(f"agent closed stdin early: {exc}")

        timed_out = cancelled = False
        deadline = started + timeout_s
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                logger.info("Agent run cancelled; terminating pid %s", proc.pid)
                _terminate_group(proc)
                break
            if time.monotonic() > deadline:
                timed_out = True
                logger.warning("Agent run exceeded %.0fs; terminating pid %s", timeout_s, proc.pid)
                _terminate_group(proc)
                break
            time.sleep(_POLL_S)
        proc.wait()
        pump.join(timeout=_KILL_GRACE_S)
        duration = time.monotonic() - started
        raw = "\n".join(tail)
        envelope = _parse_envelope(raw)
        if not envelope and raw.strip():
            warnings.append("agent output was not the expected JSON envelope; using raw text")
        output = str(envelope.get("result") or "") or raw
        ok = proc.returncode == 0 and not timed_out and not cancelled and not bool(envelope.get("is_error"))
        error = ""
        if not ok:
            error = raw[-2000:] if raw else (f"exit {proc.returncode}" if not timed_out else "timed out")
        logger.info(
            "Agent run finished: exit=%s ok=%s timed_out=%s cancelled=%s %.1fs",
            proc.returncode,
            ok,
            timed_out,
            cancelled,
            duration,
        )
        try:
            cost = float(envelope.get("total_cost_usd") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        try:
            turns = int(envelope.get("num_turns") or 0)
        except (TypeError, ValueError):
            turns = 0
        return DriverResult(
            ok=ok,
            output=output,
            session_id=str(envelope.get("session_id") or ""),
            returncode=proc.returncode if proc.returncode is not None else -1,
            timed_out=timed_out,
            cancelled=cancelled,
            error=error,
            duration_s=duration,
            cost_usd=cost,
            num_turns=turns,
            warnings=tuple(warnings),
        )
