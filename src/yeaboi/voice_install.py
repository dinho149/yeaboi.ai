"""In-app installer for the optional dictation stack — packages, then the model.

Voice input ships as the ``yeaboi[voice]`` extra (``sounddevice`` +
``faster-whisper``) rather than as a core dependency, because ``ctranslate2``
and ``onnxruntime`` publish **no sdist** and only build wheels for macOS
x86_64/arm64, ``manylinux_2_28`` x86_64/aarch64 and ``win_amd64``. Making them
required would turn ``pip install yeaboi`` into a hard failure on musl, on glibc
older than 2.28, and on 32-bit and armv7 hosts — users who can run every other
mode today.

The cost of that choice used to land entirely on the user: quit the app,
reinstall with the extra, come back, then sit through a silent ~145 MB model
download on the first dictation. This module moves all of it inside the app, so
double-tapping Space is the only step. Nothing here imports Rich or any TUI
module — :mod:`yeaboi.ui.shared._voice_input` drives it, and ``cli.py``'s
``--install-voice`` drives the same functions with plain prints.

Design notes / architectural decisions:
- **Additive commands only.** :func:`install_plan` never returns the command a
  human would type (``uv sync``, ``uv tool install``): both *rebuild* the
  environment this process is executing out of. See the table in
  :func:`install_plan` for the per-method substitution and why each one is safe.
- **The two packages, never the extra.** Installing ``yeaboi[voice]`` would
  reinstall yeaboi itself — replacing the running process's own code and, in a
  source checkout, clobbering the editable install with a PyPI build.
- **Wheels only.** ``--only-binary=:all:`` turns an unsupported platform into a
  four-second honest answer instead of a twenty-minute doomed C++ build behind a
  progress bar (``av``/``tokenizers``/``numpy`` all ship sdists, so without the
  flag pip cheerfully tries). ``VOICE_INSTALL_ALLOW_BUILD=1`` opts back in.
- **The child never touches the terminal.** A package manager writing to the TTY
  underneath a Rich ``Live`` shreds the display, so stdout and stderr are merged
  into one pipe read by a daemon thread and stdin is ``DEVNULL`` — a prompting
  pip must fail fast rather than eat the app's keystrokes.
- **The model download runs in a child, and the parent counts bytes on disk.**
  ``faster_whisper.utils.download_model`` hardcodes a disabled tqdm, and
  ``snapshot_download``'s ``tqdm_class`` only wraps the outer per-*file* bar —
  useless when one 145 MB ``model.bin`` is 95% of the payload. Polling the HF
  cache directory (which includes the in-flight ``.incomplete`` blob) gives real
  bytes, depends on no library internals, and makes cancel a real ``terminate``.
- **No MCP tool, deliberately.** Installing arbitrary packages into the host
  environment on an LLM's say-so is not a capability worth exposing; the TUI
  offer and the CLI flag are both human-initiated.
- **fs_policy is not involved.** That sandbox is enforced at explicit in-process
  call sites for user-supplied paths; a child process writing to ``site-packages``
  bypasses it entirely, so adding allow-rules for those directories would widen
  what the agent's file tools may touch for exactly zero enforcement gain.

# See docs: "Voice Input" — the installer is a helper, not an agent path. It
# makes no LLM calls and does not go through get_llm().
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# The `voice` extra's contents. Asserted equal to pyproject.toml's extra by
# tests/unit/test_voice_install.py, so the two cannot drift.
VOICE_PACKAGES: tuple[str, ...] = ("sounddevice", "faster-whisper")

# Rounded installed size of the wheel set (ctranslate2 + onnxruntime + av +
# numpy + tokenizers + the rest). Used only for the "~325 MB" in the offer, so
# approximate is fine — a wrong order of magnitude would not be.
PACKAGES_MB = 180

# One-time model download per size. The offer must not hardcode "140 MB": a user
# with VOICE_MODEL=large-v3 is agreeing to twenty times that.
MODEL_MB: dict[str, int] = {"tiny": 75, "base": 145, "small": 480, "medium": 1500, "large-v3": 3100}

# Whitelist for the size interpolated into the child's -c program. Everything
# else is rejected before a process is spawned.
MODEL_SIZES: frozenset[str] = frozenset(MODEL_MB)

_HF_ORG = "Systran"
_HF_API = "https://huggingface.co/api/models/{repo}/tree/main"

# Only one install per process, and only one across processes: pip is not safe
# against concurrent writes to the same site-packages, and two open yeaboi
# windows is an ordinary situation rather than an edge case.
_install_lock = threading.Lock()

_ALLOW_BUILD_ENV = "VOICE_INSTALL_ALLOW_BUILD"

# Progress cadence for the model download. 250 ms is fast enough that the bar
# looks live at 30 fps and slow enough that rglob() over the cache is free.
_POLL_SECONDS = 0.25

# No wall-clock timeout on the download — a 460 MB "small" on hotel wifi is
# legitimately twenty minutes, and killing a slow success is worse than waiting.
# A byte counter that has not moved in this long is reported as stalled instead.
_STALL_SECONDS = 120.0


@dataclass(frozen=True)
class InstallPlan:
    """How to add the dictation packages to *this* interpreter, right now."""

    method: str  # "uv-project" | "uv-tool" | "pipx" | "pip" | "blocked"
    argv: tuple[str, ...]
    display_command: str  # what we show and log; shlex-joined argv
    durable: bool  # survives a later `uv tool upgrade` / `pipx reinstall`
    blocked: str  # non-empty => refuse before spawning anything
    follow_up: str  # the command that makes a non-durable install durable


# ---------------------------------------------------------------------------
# Platform gate
# ---------------------------------------------------------------------------


def platform_support() -> tuple[bool, str]:
    """Return ``(supported, reason)`` for hosts where no wheel *can* exist.

    Gates only what is certain, because a stale allow-list that refuses a
    platform pip would have served is worse than a failed install with a real
    error message: 32-bit interpreters, architectures outside the four
    ctranslate2/onnxruntime build for, and non-glibc Linux (musl reports an
    empty name from ``platform.libc_ver``). A too-new CPython is deliberately
    *not* gated here — that is a moving target which resolves itself when the
    wheels land, so it surfaces through :func:`classify_failure` instead.
    """
    if sys.maxsize <= 2**32:
        return False, "32-bit Python (the speech engine ships 64-bit wheels only)"
    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64", "arm64", "aarch64"}:
        return False, f"{platform.machine()} CPUs have no speech-engine wheel"
    if sys.platform.startswith("linux"):
        libc, version = platform.libc_ver()
        if libc and libc != "glibc":
            return False, f"{libc} libc (the speech engine builds against glibc only)"
        if not libc:
            return False, "musl libc (the speech engine builds against glibc only)"
        parts = [int(p) for p in re.findall(r"\d+", version)[:2]] or [0]
        if tuple(parts) < (2, 17):
            return False, f"glibc {version} is older than the 2.17 the speech engine needs"
    return True, ""


# ---------------------------------------------------------------------------
# Sticky verdicts
# ---------------------------------------------------------------------------


def _verdict_key() -> str:
    """Identify the environment a permanent verdict applies to.

    Keyed on platform *and* Python version *and* interpreter path so a Python
    upgrade, or a reinstall somewhere else, silently invalidates a "no wheel"
    verdict instead of condemning the machine forever.
    """
    return f"{platform.platform()}|{sys.version_info[0]}.{sys.version_info[1]}|{sys.executable}"


# A stored verdict stops being trusted after this long. "Permanent" here means
# "nothing you can do in this session will change it", not "true forever": the
# two codes we persist both have futures. NO_WHEEL covers a CPython so new that
# no cp3XX wheel exists yet — which the platform gate deliberately does not
# pre-empt because it "resolves itself when the wheels land" — and a corporate
# mirror that has not synced ctranslate2 yet. EXTERNALLY_MANAGED can be resolved
# by an OS upgrade. The key already invalidates on a Python or interpreter
# change; this covers the case where the *world* changed and the machine did not,
# so one bad attempt cannot condemn a host for good.
_VERDICT_TTL_SECONDS = 30 * 24 * 3600.0


def read_verdict() -> tuple[str, str]:
    """Return a stored ``(code, message)`` permanent failure, or ``("", "")``.

    An expired verdict reads as absent, so the next double-tap re-offers.
    """
    from yeaboi.paths import get_voice_install_path

    try:
        raw = json.loads(get_voice_install_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    if not isinstance(raw, dict) or raw.get("key") != _verdict_key():
        return "", ""
    try:
        stamped = float(raw.get("at", 0.0))
    except (TypeError, ValueError):
        stamped = 0.0
    # A missing/0 stamp is a file written by an older build: treat it as fresh
    # rather than as 1970, which would expire every one of them on read.
    if stamped and time.time() - stamped > _VERDICT_TTL_SECONDS:
        logger.info("Voice-install verdict expired; dictation will be offered again")
        return "", ""
    return str(raw.get("code", "")), str(raw.get("message", ""))


def write_verdict(code: str, message: str) -> None:
    """Persist a permanent failure so the offer stops appearing. Best-effort."""
    from yeaboi.paths import get_voice_install_path

    try:
        get_voice_install_path().write_text(
            json.dumps({"key": _verdict_key(), "code": code, "message": message, "at": time.time()}),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Could not persist voice-install verdict: %s", exc)
        return
    finally:
        reset_unsupported_cache()
    logger.info("Voice install marked unavailable for up to %d days: %s", _VERDICT_TTL_SECONDS // 86400, code)


def clear_verdict() -> None:
    """Forget any stored permanent failure (used by tests and a manual retry)."""
    from yeaboi.paths import get_voice_install_path

    try:
        get_voice_install_path().unlink(missing_ok=True)
    except OSError:  # pragma: no cover - unlink on a readable dir practically cannot fail
        logger.warning("Could not clear voice-install verdict", exc_info=True)
    reset_unsupported_cache()


# Memoised because every input-box render asks for it (via voice_state) and the
# answer involves a JSON read. Cleared by clear_verdict() and refresh_imports().
_unsupported_cache: str | None = None


def unsupported_reason() -> str:
    """Return why dictation can never work here, or ``""`` if it might.

    Combines the pre-flight platform gate with any stored permanent verdict, so
    every surface asks one question instead of two.
    """
    global _unsupported_cache
    if _unsupported_cache is None:
        supported, reason = platform_support()
        _unsupported_cache = reason if not supported else read_verdict()[1]
    return _unsupported_cache


def reset_unsupported_cache() -> None:
    """Forget the memoised verdict (after writing or clearing one, and in tests)."""
    global _unsupported_cache
    _unsupported_cache = None


# ---------------------------------------------------------------------------
# The install command
# ---------------------------------------------------------------------------


def _is_source_checkout() -> bool:
    """True when this package is being run from a repo checkout, not a wheel."""
    repo_root = Path(__file__).resolve().parents[2]
    return (repo_root / "pyproject.toml").exists() and (repo_root / "src" / "yeaboi").is_dir()


def _pipx_venv_name() -> str:
    """Derive the pipx venv name from the interpreter path; ``""`` if it can't.

    The argument to ``pipx inject`` is the *venv* name, and the legacy install of
    this app is registered as ``scrum-agent`` rather than ``yeaboi`` — so it must
    be read off the path, never hardcoded.
    """
    try:
        parts = Path(sys.executable).resolve().parts
    except OSError:
        return ""
    if "venvs" not in parts:
        return ""
    name = parts[parts.index("venvs") + 1] if parts.index("venvs") + 1 < len(parts) else ""
    return name if re.fullmatch(r"[A-Za-z0-9._-]+", name or "") else ""


def _externally_managed() -> bool:
    """True for a PEP 668 system Python (Homebrew, Debian) outside a venv."""
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        return False  # inside a venv — always ours to write to
    import sysconfig

    stdlib = sysconfig.get_path("stdlib")
    return bool(stdlib) and (Path(stdlib) / "EXTERNALLY-MANAGED").exists()


def _binary_flags(program: str) -> tuple[str, ...]:
    if os.getenv(_ALLOW_BUILD_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        return ()
    return ("--no-build",) if program == "uv" else ("--only-binary=:all:",)


def install_plan(*, ignore_verdict: bool = False) -> InstallPlan:
    """Return the additive command that adds dictation to the running interpreter.

    ``ignore_verdict`` skips a *stored* past failure but never the platform gate,
    which is the one thing here that is genuinely certain. It is what
    ``--install-voice`` passes: a human typing the command explicitly is asking
    to retry, and refusing them on the strength of an old cached failure leaves
    no way back at all.

    :func:`yeaboi.voice.voice_install_command` returns what a *human* should type;
    two of its four branches are wrong to run against a live process, so this
    substitutes an additive equivalent:

    ============  ==========================================================
    detected      why not the printed command
    ============  ==========================================================
    source        ``uv sync`` is *exact*: it uninstalls anything absent from
                  the lockfile, out from under a running process, and it
                  resolves from ``cwd`` — which for a TUI is wherever the user
                  happened to launch it.
    uv tool       ``uv tool install --force`` deletes and recreates the venv
                  this process is executing out of. ``uv pip install --python``
                  targets the same interpreter and only adds.
    pipx          same rebuild hazard; ``pipx inject`` is the additive form and
                  is recorded in pipx metadata, so it survives a reinstall.
    pip / venv    ``sys.executable -m pip``, never a bare ``pip`` — which may
                  well belong to a different environment.
    ============  ==========================================================
    """
    supported, platform_reason = platform_support()
    if not supported:
        return InstallPlan("blocked", (), "", False, platform_reason, "")
    stored = "" if ignore_verdict else read_verdict()[1]
    if stored:
        return InstallPlan("blocked", (), "", False, stored, "")
    if _externally_managed():
        return InstallPlan(
            "blocked",
            (),
            "",
            False,
            "this is a system-managed Python — reinstall yeaboi with `uv tool install yeaboi` or `pipx install yeaboi`",
            "",
        )

    exe = sys.executable
    uv = shutil.which("uv")
    if _is_source_checkout() or "/uv/tools/" in exe.replace("\\", "/"):
        method = "uv-project" if _is_source_checkout() else "uv-tool"
        if not uv:
            return InstallPlan("blocked", (), "", False, "`uv` is not on PATH", "")
        argv = (uv, "pip", "install", "--python", exe, *_binary_flags("uv"), *VOICE_PACKAGES)
        follow_up = "uv sync --extra voice" if method == "uv-project" else "uv tool install --force 'yeaboi[voice]'"
        return InstallPlan(method, argv, shlex.join(argv), False, "", follow_up)

    venv_name = _pipx_venv_name()
    pipx = shutil.which("pipx")
    if venv_name and pipx:
        argv = (pipx, "inject", venv_name, *VOICE_PACKAGES)
        return InstallPlan("pipx", argv, shlex.join(argv), True, "", "")

    argv = (
        exe,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        *_binary_flags("pip"),
        *VOICE_PACKAGES,
    )
    return InstallPlan("pip", argv, shlex.join(argv), True, "", "")


def size_estimate_mb() -> int:
    """Rounded MB the offer is asking the user to agree to, model included."""
    from yeaboi.config import get_voice_model

    size = get_voice_model()
    model = 0 if model_is_cached(size) else MODEL_MB.get(size, MODEL_MB["base"])
    return PACKAGES_MB + model


# ---------------------------------------------------------------------------
# Running the installer
# ---------------------------------------------------------------------------


def _child_env() -> dict[str, str]:
    """Environment that stops the child drawing anything a pipe can't carry.

    Without the progress-bar kills, pip and uv emit carriage-return bars that
    arrive as one enormous line and defeat :func:`narrate` entirely; without
    ``COLUMNS`` they hard-wrap a package name across two lines and defeat it
    again. ``TERM=dumb`` and ``NO_COLOR`` strip the ANSI escapes that would
    otherwise be rendered literally inside a status line.
    """
    env = dict(os.environ)
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
            "PIP_PROGRESS_BAR": "off",
            "UV_NO_PROGRESS": "1",
            "NO_COLOR": "1",
            "CLICOLOR": "0",
            "TERM": "dumb",
            "COLUMNS": "200",
        }
    )
    return env


_NARRATIONS: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (
        re.compile(r"^\s*Downloading\s+([A-Za-z0-9._-]+?)-[\d.]+.*?\(([\d.]+\s*[kKMG]B)\)"),
        lambda m: f"downloading {m[1]} ({m[2]})",
    ),
    (re.compile(r"^\s*Downloading\s+([A-Za-z0-9._-]+?)-[\d.]"), lambda m: f"downloading {m[1]}"),
    (re.compile(r"^\s*Using cached\s+([A-Za-z0-9._-]+?)-[\d.]"), lambda m: f"using cached {m[1]}"),
    (re.compile(r"^\s*Collecting\s+([A-Za-z0-9._\[\]-]+)"), lambda m: f"resolving {m[1]}"),
    (re.compile(r"^\s*Building wheel for\s+([A-Za-z0-9._-]+)"), lambda m: f"building {m[1]}"),
    (re.compile(r"^\s*Installing collected packages"), lambda _m: "installing packages"),
    (re.compile(r"^\s*Resolved\s+(\d+)\s+packages?"), lambda m: f"resolved {m[1]} packages"),
    (re.compile(r"^\s*Prepared\s+(\d+)\s+packages?"), lambda m: f"downloaded {m[1]} packages"),
    (re.compile(r"^\s*Installed\s+(\d+)\s+packages?"), lambda m: f"installed {m[1]} packages"),
    (re.compile(r"^\s*Successfully installed"), lambda _m: "installed packages"),
)


def narrate(line: str) -> str:
    """Turn one installer output line into a short phrase, or ``""`` to ignore it.

    Callers keep the *previous* phrase when this returns empty, so raw resolver
    noise never reaches a one-line status. The status line has room for a few
    words, not for pip.
    """
    for pattern, render in _NARRATIONS:
        match = pattern.search(line)
        if match is not None:
            return render(match)
    return ""


_FAILURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "NO_WHEEL",
        re.compile(
            r"no matching distribution|could not find a version|"
            r"has no (?:source distribution or )?wheels?|"
            r"does(?:n't| not) have a (?:source distribution or )?wheel|"
            r"requires a different python",
            re.I,
        ),
    ),
    ("EXTERNALLY_MANAGED", re.compile(r"externally-managed-environment", re.I)),
    (
        "NO_NETWORK",
        re.compile(
            r"temporary failure in name resolution|getaddrinfo|failed to establish a new connection|"
            r"error sending request|certificate verify failed|network is unreachable|proxyerror",
            re.I,
        ),
    ),
    ("DISK_FULL", re.compile(r"no space left on device|errno 28|disk quota exceeded", re.I)),
    ("PERMISSION", re.compile(r"permission denied|errno 13|consider using the `--user` option", re.I)),
)

_FAILURE_MESSAGES: dict[str, str] = {
    "EXTERNALLY_MANAGED": (
        "This is a system-managed Python — installing here would need sudo and could break OS packages. "
        "Reinstall yeaboi with `uv tool install yeaboi` or `pipx install yeaboi`."
    ),
    "NO_NETWORK": "Can't reach the package index. Try again when you're online.",
    "DISK_FULL": "Out of disk — dictation needs roughly 400 MB of packages plus the speech model.",
    "PERMISSION": "Permission denied writing to this environment. yeaboi will not re-run itself with sudo.",
    "MANAGER_MISSING": "The package manager that installed yeaboi isn't on PATH any more.",
    "CANCELLED": "Install cancelled — nothing was changed.",
    "TIMEOUT": "The install took too long and was stopped.",
}

# Codes we refuse to retry: nothing about this machine will change by trying again.
PERMANENT_CODES: frozenset[str] = frozenset({"NO_WHEEL", "EXTERNALLY_MANAGED"})


def _host_description() -> str:
    """Name the host precisely enough that "no wheel" is actionable.

    The Python version matters as much as the platform here: a brand-new CPython
    with no cp3XX wheels yet looks identical to an unsupported OS unless we say
    which one it was.
    """
    parts = [f"{platform.system().lower()}/{platform.machine()}"]
    if sys.platform.startswith("linux"):
        libc, libc_version = platform.libc_ver()
        parts.append("(" + " ".join(filter(None, (libc or "musl", libc_version))) + ")")
    parts.append(f"on CPython {sys.version_info[0]}.{sys.version_info[1]}")
    return " ".join(parts)


def classify_failure(returncode: int, output: str) -> tuple[str, str]:
    """Map an installer exit into ``(code, human message)``.

    Pure over its inputs so the whole taxonomy is table-testable without ever
    running a package manager.
    """
    for code, pattern in _FAILURES:
        if pattern.search(output):
            if code == "NO_WHEEL":
                return code, (
                    "No prebuilt speech-engine wheel exists for "
                    f"{_host_description()} — dictation can't run on this machine yet."
                )
            return code, _FAILURE_MESSAGES[code]
    tail = " / ".join(line.strip() for line in output.strip().splitlines()[-3:] if line.strip())
    return "UNKNOWN", (
        f"Install failed (exit {returncode}). {tail}" if tail else f"Install failed (exit {returncode})."
    )


# How long a lock may sit untouched before another window may break it. Only
# consulted where a liveness probe is unavailable (Windows, see _pid_alive): the
# alternative there is a machine that can never install dictation again because
# one crashed run left its lock behind.
_LOCK_STALE_SECONDS = 3600.0


def _pid_alive(pid: int) -> bool | None:
    """Return whether *pid* is running, or ``None`` when that cannot be asked.

    ``os.kill(pid, 0)`` is a liveness probe on POSIX only. On Windows CPython it
    calls ``TerminateProcess(handle, sig)`` for every signal except
    ``CTRL_C_EVENT``/``CTRL_BREAK_EVENT`` — so using it as a probe would *kill*
    the other yeaboi window, mid-session, with exit code 0. Windows is a
    supported dictation platform (``win_amd64`` is one of the four wheel targets
    this module exists to work around) and two open windows is ordinary here, so
    that path answers ``None`` and the caller falls back to the lock's age.
    """
    if sys.platform == "win32":
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # alive, owned by another user
    return True


class _Lockfile:
    """Cross-process "one installer at a time" guard, with stale-pid recovery."""

    def __init__(self) -> None:
        from yeaboi.paths import get_bin_dir

        self.path = get_bin_dir() / "voice-install.lock"
        self.acquired = False

    def __enter__(self) -> _Lockfile:
        for _attempt in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if self._stale():
                    continue
                return self
            except OSError:  # pragma: no cover - unwritable bin dir: proceed rather than block
                self.acquired = True
                return self
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
            self.acquired = True
            return self
        return self

    def _stale(self) -> bool:
        try:
            pid = int(self.path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            pid = 0
        if pid and pid != os.getpid():
            alive = _pid_alive(pid)
            if alive:
                return False
            if alive is None and not self._expired():
                # Windows: no safe probe, so age is the only evidence we have.
                return False
        try:
            self.path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover
            return False
        return True

    def _expired(self) -> bool:
        """True when the lock is old enough to break without a liveness probe."""
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:  # pragma: no cover - the file we just failed to create
            return True
        return age > _LOCK_STALE_SECONDS

    def __exit__(self, *_exc: object) -> None:
        if self.acquired:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                logger.debug("Could not remove voice-install lock", exc_info=True)


def install_packages(
    on_line: Callable[[str], None],
    cancel_event: threading.Event | None = None,
    *,
    plan: InstallPlan | None = None,
    timeout: float = 900.0,
) -> tuple[bool, str]:
    """Install the dictation packages into this interpreter. Never raises.

    ``on_line`` receives a short narrated phrase per interesting output line (see
    :func:`narrate`); the raw stream goes to the log. Returns ``(ok, message)``,
    where a failure message is already human-readable.
    """
    plan = plan or install_plan()
    if plan.blocked:
        logger.info("Voice install refused before spawning: %s", plan.blocked)
        return False, plan.blocked
    if not _install_lock.acquire(blocking=False):
        return False, "An install is already running in this window."

    try:
        with _Lockfile() as lock:
            if not lock.acquired:
                return False, "Another yeaboi window is installing dictation right now."
            return _run_installer(plan, on_line, cancel_event, timeout)
    finally:
        _install_lock.release()


def _log_index_host() -> None:
    """Record which index the child will use, so a hijacked mirror is visible."""
    for var in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "PIP_INDEX_URL"):
        raw = os.getenv(var, "").strip()
        if raw:
            from urllib.parse import urlsplit

            logger.info("Voice install using %s host %s", var, urlsplit(raw).hostname or "?")


def _run_installer(
    plan: InstallPlan,
    on_line: Callable[[str], None],
    cancel_event: threading.Event | None,
    timeout: float,
) -> tuple[bool, str]:
    logger.info("Voice install starting: %s", plan.display_command)
    _log_index_host()
    from yeaboi.paths import get_bin_dir

    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is built from module constants and sys.executable
            list(plan.argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            cwd=str(get_bin_dir()),
            env=_child_env(),
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning("Voice install could not start: %s", exc)
        return False, f"Could not run the installer: {exc}"

    tail: deque[str] = deque(maxlen=200)

    def _pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            tail.append(line)
            logger.debug("voice-install: %s", line)
            phrase = narrate(line)
            if phrase:
                on_line(phrase)

    reader = threading.Thread(target=_pump, name="voice-install-read", daemon=True)
    reader.start()

    deadline = time.monotonic() + timeout
    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            _terminate(proc)
            logger.info("Voice install cancelled by the user")
            return False, _FAILURE_MESSAGES["CANCELLED"]
        if time.monotonic() > deadline:
            _terminate(proc)
            logger.warning("Voice install timed out after %.0fs", timeout)
            return False, f"{_FAILURE_MESSAGES['TIMEOUT']} Run it yourself: {plan.display_command}"
        time.sleep(_POLL_SECONDS)
    reader.join(timeout=2.0)

    if proc.returncode == 0:
        refresh_imports()
        ok, reason = verify_installed()
        if ok:
            logger.info("Voice install succeeded via %s", plan.method)
            return True, ""
        logger.warning("Voice install exited 0 but the packages are not importable: %s", reason)
        return False, "Installed, but this Python can't see the packages yet — restart yeaboi."

    code, message = classify_failure(proc.returncode, "\n".join(tail))
    logger.warning("Voice install failed (%s): exit %s", code, proc.returncode)
    if code in PERMANENT_CODES:
        write_verdict(code, message)
    return False, message


def _terminate(proc: subprocess.Popen) -> None:
    """Stop a child politely, then not politely."""
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:  # pragma: no cover - unkillable child
            logger.warning("Voice install child would not die")


# ---------------------------------------------------------------------------
# Making the new packages visible to this process
# ---------------------------------------------------------------------------


def refresh_imports() -> None:
    """Make a just-installed package importable, and un-stick everything memoised.

    Four caches in this app answer "is voice installed?" — the sticky-verdict
    memo here, the strict backend probe, the input-box chip and the welcome
    tips — and all four were written when the answer could not change
    mid-process. They can now, so each one has to be dropped here or the app
    keeps rendering "off" for the rest of the run. Anything memoising that
    question in future belongs in this function too.
    """
    import importlib

    importlib.invalidate_caches()
    reset_unsupported_cache()

    from yeaboi import voice

    voice.reset_probe()

    try:
        from yeaboi.ui.shared._tips import get_tips
        from yeaboi.ui.shared._voice_input import reset_voice_chip
    except ImportError:  # pragma: no cover - headless callers (CLI) have no UI package need
        return
    reset_voice_chip()
    get_tips.cache_clear()


def verify_installed() -> tuple[bool, str]:
    """Confirm both packages are really importable, not merely path-shaped.

    ``find_spec`` also succeeds for a *namespace* package, so a half-written
    install can pass the cheap probe. Requiring an ``origin`` means a real
    module file exists behind the name.
    """
    import importlib.util

    for module in ("sounddevice", "faster_whisper"):
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):
            spec = None
        if spec is None or spec.origin is None:
            return False, f"{module} is not importable"
    return True, ""


# ---------------------------------------------------------------------------
# The speech model
# ---------------------------------------------------------------------------


def model_repo_id(size: str) -> str:
    """Hugging Face repo holding the CTranslate2 build of a Whisper size."""
    return f"{_HF_ORG}/faster-whisper-{size}"


def model_cache_dir() -> Path:
    """Resolve the Hugging Face cache this machine will download into.

    Deliberately *not* relocated under ``~/.yeaboi``: a user who already has
    ``faster-whisper-base`` from another tool would otherwise pay 145 MB again.
    The trade is that the bytes land somewhere yeaboi does not own, so every
    surface that mentions the download also prints this path.
    """
    override = os.getenv("HF_HUB_CACHE") or os.getenv("HUGGINGFACE_HUB_CACHE")
    if override:
        return Path(override).expanduser()
    home = os.getenv("HF_HOME")
    if home:
        return Path(home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def _repo_dir(size: str) -> Path:
    return model_cache_dir() / f"models--{_HF_ORG}--faster-whisper-{size}"


def model_bytes_on_disk(size: str) -> int:
    """Bytes already fetched for ``size`` — completed blobs and in-flight ones.

    Counting ``blobs/<etag>.incomplete`` is what makes the progress bar move
    smoothly through a single 145 MB file instead of jumping when it lands.
    """
    repo = _repo_dir(size)
    if not repo.is_dir():
        return 0
    total = 0
    for path in repo.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:  # pragma: no cover - file vanished mid-scan
            continue
    return total


def model_is_cached(size: str) -> bool:
    """True when a usable snapshot of ``size`` is already on disk."""
    snapshots = _repo_dir(size) / "snapshots"
    if not snapshots.is_dir():
        return False
    return any((snapshot / "model.bin").exists() for snapshot in snapshots.iterdir())


def model_total_bytes(size: str, timeout: float = 8.0) -> int:
    """Total download size from the HF tree API; ``0`` when it can't be known.

    A zero total is not an error — the caller renders an indeterminate spinner
    rather than inventing a percentage.
    """
    url = _HF_API.format(repo=model_repo_id(size))
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310 - fixed https constant
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            entries = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - offline is an ordinary outcome here
        logger.info("Could not size the speech model: %s", exc)
        return 0
    if not isinstance(entries, list):
        return 0
    return sum(int(entry.get("size", 0)) for entry in entries if isinstance(entry, dict))


def download_model(
    size: str,
    on_progress: Callable[[str, float | None], None],
    cancel_event: threading.Event | None = None,
) -> tuple[bool, str]:
    """Fetch the Whisper model, reporting a real byte fraction. Never raises.

    The download runs in a child process and the fraction is computed by the
    parent from the cache directory's size. That buys three things a thread
    could not: a genuine cancel (``terminate``), byte-level progress without
    touching any huggingface_hub internal, and containment for the first
    ``ctranslate2``/``onnxruntime`` import — which can ``SIGILL`` on a CPU
    without AVX, and would take the whole TUI with it in-process.

    Returns ``(ok, message)``. A failure here is a *warning*, not a broken
    feature: the packages are installed, so the model simply downloads lazily on
    the first dictation exactly as it always did.
    """
    if size not in MODEL_SIZES:
        return False, f"Unknown speech model size {size!r}"
    if model_is_cached(size):
        return True, ""

    total = model_total_bytes(size)
    env = _child_env()
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    # Load-bearing, and invisible if you do not know to look for it: huggingface_hub
    # ships `hf_xet` transitively, and the Xet path stages content in a *separate*
    # cache and materialises the file only at the end — so the repo directory sits
    # at a couple of megabytes for the whole download and then jumps to 100%.
    # Measured here: 3% … 3% … 3% … 100%. The classic downloader streams into
    # blobs/<etag>.incomplete inside the repo, which is what _bytes_on_disk counts,
    # and gives a bar that actually moves (874 B → 2.6 MB → 13 MB → 23 MB → …).
    # A once-per-install download is worth more as honest progress than as a
    # marginally faster opaque one.
    env["HF_HUB_DISABLE_XET"] = "1"
    program = f"from faster_whisper.utils import download_model; download_model({size!r})"

    logger.info("Speech model download starting: %s (%d bytes expected)", size, total)
    try:
        proc = subprocess.Popen(  # noqa: S603 - size is whitelist-checked against MODEL_SIZES above
            [sys.executable, "-c", program],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning("Speech model download could not start: %s", exc)
        return False, f"Could not start the model download: {exc}"

    tail: deque[str] = deque(maxlen=100)

    def _pump() -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                tail.append(line)
                logger.debug("voice-model: %s", line)

    reader = threading.Thread(target=_pump, name="voice-model-read", daemon=True)
    reader.start()

    seen = 0
    moved_at = time.monotonic()
    while proc.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            _terminate(proc)
            logger.info("Speech model download cancelled by the user")
            return False, "Download stopped — it resumes where it left off."
        now = model_bytes_on_disk(size)
        if now > seen:
            seen, moved_at = now, time.monotonic()
        stalled = time.monotonic() - moved_at > _STALL_SECONDS
        # The engine knows the bytes, so it spells them: the UI would otherwise
        # have to re-fetch the total just to render "76/145 MB".
        detail = f"{seen // 1_000_000}/{total // 1_000_000} MB" if total else ""
        on_progress(
            "stalled — no data for a while" if stalled else detail,
            (min(seen / total, 0.999) if total else None),
        )
        time.sleep(_POLL_SECONDS)
    reader.join(timeout=2.0)

    if proc.returncode == 0:
        on_progress("", 1.0)
        logger.info("Speech model %s downloaded to %s", size, model_cache_dir())
        return True, ""

    output = "\n".join(tail)
    code, message = classify_failure(proc.returncode, output)
    logger.warning("Speech model download failed (%s): exit %s", code, proc.returncode)
    if code == "NO_NETWORK":
        return False, "Can't reach huggingface.co — the model will download on your first dictation instead."
    return False, message


def warm_model(size: str) -> tuple[bool, str]:
    """Load the model into :data:`yeaboi.voice._MODEL_CACHE`. Never raises.

    Without this the bar hits 100% and the user then waits another few seconds
    staring at "transcribing" while CTranslate2 reads the weights — which reads
    as a hang immediately after a progress bar promised completion.
    """
    from yeaboi import voice

    try:
        voice._get_model()
    except Exception as exc:  # noqa: BLE001 - a cold first transcription is the fallback
        logger.warning("Could not preload the speech model: %s", exc)
        return False, str(exc)
    logger.info("Speech model %s preloaded", size)
    return True, ""
