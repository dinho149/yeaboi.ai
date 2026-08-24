"""User-global launch budget for supervised coding-agent runs.

Design ported from ruflo's ``global-ai-budget.ts`` (MIT), re-implemented for
yeaboi: the ledger lives under the user's home — never the workspace — so every
yeaboi instance on the machine shares one budget. The failure it prevents is
multiplicative spend: N worktrees each launching "just a couple" of agent runs
silently exhausting the user's hourly Claude quota.

Three files under ``paths.get_ship_dir()`` (0700):

- ``ai-budget.json``          — the enforcement ledger (launches + active permits)
- ``ai-budget.lock``          — O_EXCL mutation lock with stale takeover
- ``ai-budget-receipts.jsonl``— append-only telemetry, never read for enforcement

Rules, all deliberate:

- ``reserve()`` checks circuit → concurrency → hourly → daily, in that order,
  and **a reservation counts as a launch immediately** — the hourly/daily
  invariant is on launches, not completions, so a run that dies early still
  spent its slot. ``release()`` frees only the concurrency slot.
- **Fails closed**: an unreadable ledger or an unobtainable lock denies the
  launch. An unaccountable launch is exactly what this fuse exists to prevent.
  The one escape hatch is ``YEABOI_AI_BUDGET_DISABLE=1``, which returns a
  ``bypass_…`` permit that release() ignores.
- A quota/429 error output from a *failed* launch opens a circuit breaker for
  60 minutes, user-wide. The pattern is never applied to successful output,
  which may legitimately discuss rate limiting in the user's own code.
- Stale state self-heals on read: launches older than 24 h are pruned, and an
  active permit is dropped once its process is dead or it is older than
  30 minutes (the cutoff tolerates PID reuse). A corrupt ledger starts fresh
  rather than blocking forever — worst case is one over-budget launch.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from yeaboi.paths import SHIP_BUDGET_FILE, SHIP_BUDGET_LOCK, SHIP_BUDGET_RECEIPTS, get_ship_dir

logger = logging.getLogger(__name__)

# Defaults chosen to be deliberately tight — a human raises them consciously
# via env, they never silently widen.
DEFAULT_MAX_CONCURRENT = 1  # YEABOI_AI_MAX_CONCURRENT
DEFAULT_MAX_PER_HOUR = 2  # YEABOI_AI_MAX_PER_HOUR
DEFAULT_MAX_PER_DAY = 12  # YEABOI_AI_MAX_PER_DAY
DEFAULT_QUOTA_PAUSE_MINUTES = 60  # YEABOI_AI_QUOTA_PAUSE_MINUTES

HOUR_S = 60 * 60
DAY_S = 24 * HOUR_S
# Abandoned-reservation cutoff. Must exceed the longest supervised run timeout
# so a live run is never reaped mid-flight; also the PID-reuse tolerance.
ACTIVE_STALE_S = 30 * 60
# A lock older than this belongs to a crashed process — take it over.
LOCK_STALE_S = 10.0
_LOCK_DEADLINE_S = 2.0
_LOCK_POLL_S = 0.025

_RECEIPTS_MAX_BYTES = 512 * 1024
_RECEIPTS_KEEP_LINES = 200

# Matched ONLY against the error output of a failed launch — but that output
# is the agent's own stdout tail, which can echo the user's code. So every
# alternative here requires an error *shape*, never a bare topic word: an
# agent that failed while WORKING ON rate-limiting code must not trip a
# user-global hour-long pause.
_QUOTA_ERROR_RE = re.compile(
    r"\b429\b|rate[\s_-]?limit(?:ed|_error|s? (?:hit|reached|exceeded))"
    r"|usage[\s_-]?limit|quota[\s_-]?(?:exceeded|reached|exhausted)"
    r"|too many requests|overloaded_error",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BudgetDecision:
    """The answer to "may I launch an agent right now?".

    ``reason`` is machine-readable-ish prose naming the limit that denied
    (e.g. ``"hourly-budget (2/2 in last hour)"``) so the TUI can show the
    user exactly which fuse blew and when it resets.
    """

    allowed: bool = False
    permit_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class BudgetStatus:
    """A read-only snapshot for status screens. Never used for enforcement."""

    active: int = 0
    launched_last_hour: int = 0
    launched_last_day: int = 0
    max_concurrent: int = DEFAULT_MAX_CONCURRENT
    max_per_hour: int = DEFAULT_MAX_PER_HOUR
    max_per_day: int = DEFAULT_MAX_PER_DAY
    paused_until: float = 0.0
    paused_reason: str = ""


def _now() -> float:
    """Wall-clock seconds; a seam for tests (the ledger persists across runs)."""
    return time.time()


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    # Zero is honoured, not "corrected": setting a limit to 0 is the user
    # saying deny everything, which is the one direction a fail-closed fuse
    # must always respect. Only nonsense (negatives) falls back.
    return value if value >= 0 else default


def limits() -> tuple[int, int, int, int]:
    """(max_concurrent, per_hour, per_day, quota_pause_minutes), env-resolved."""
    return (
        _int_env("YEABOI_AI_MAX_CONCURRENT", DEFAULT_MAX_CONCURRENT),
        _int_env("YEABOI_AI_MAX_PER_HOUR", DEFAULT_MAX_PER_HOUR),
        _int_env("YEABOI_AI_MAX_PER_DAY", DEFAULT_MAX_PER_DAY),
        _int_env("YEABOI_AI_QUOTA_PAUSE_MINUTES", DEFAULT_QUOTA_PAUSE_MINUTES),
    )


def is_disabled() -> bool:
    """True when the human explicitly disabled the fuse for this environment."""
    return os.getenv("YEABOI_AI_BUDGET_DISABLE", "").strip() in ("1", "true", "yes")


def looks_like_quota_error(error_output: str) -> bool:
    """Whether a failed launch's error output names a quota/rate-limit problem."""
    return bool(_QUOTA_ERROR_RE.search(error_output or ""))


def process_alive(pid: int) -> bool:
    """Whether *pid* names a live process. Shared with the engine's resume check.

    A pid owned by another user reads as alive: it exists, and refusing is the
    harmless direction for both callers.
    """

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


def _refuse_symlink(path: Path) -> None:
    """A symlinked ledger would let another local user redirect our writes."""
    try:
        if path.is_symlink():
            raise PermissionError(f"refusing symlinked budget file: {path}")
    except OSError as exc:  # lstat failed for some other reason
        raise PermissionError(f"cannot inspect budget file: {path}: {exc}") from exc


class _Lock:
    """O_EXCL lockfile with stale takeover; guards every ledger mutation."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._held = False

    def __enter__(self) -> _Lock:
        deadline = time.monotonic() + _LOCK_DEADLINE_S
        while True:
            try:
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, str(os.getpid()).encode("ascii"))
                os.close(fd)
                self._held = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - self._path.stat().st_mtime
                except OSError:
                    age = 0.0  # vanished between attempts — retry immediately
                if age > LOCK_STALE_S:
                    # The holder crashed; take the lock over.
                    logger.warning("Taking over stale budget lock (age %.1fs)", age)
                    self._path.unlink(missing_ok=True)
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError("budget lock is held") from None
                time.sleep(_LOCK_POLL_S)

    def __exit__(self, *exc: object) -> None:
        if self._held:
            self._path.unlink(missing_ok=True)
            self._held = False


def _read_ledger(path: Path) -> dict:
    _refuse_symlink(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        # A corrupt ledger starts fresh rather than blocking forever; worst
        # case is one over-budget launch, and the receipt trail still exists.
        logger.warning("Budget ledger is corrupt; starting fresh")
        return {}
    return data if isinstance(data, dict) else {}


def _write_ledger(path: Path, data: dict) -> None:
    _refuse_symlink(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _prune(data: dict, now: float) -> dict:
    launches = [e for e in data.get("launches", []) if isinstance(e, dict) and now - float(e.get("at", 0)) < DAY_S]
    active = []
    for entry in data.get("active", []):
        if not isinstance(entry, dict):
            continue
        age = now - float(entry.get("at", 0))
        if age >= ACTIVE_STALE_S:
            continue
        if not process_alive(int(entry.get("pid", 0))):
            continue
        active.append(entry)
    pruned = dict(data)
    pruned["launches"] = launches
    pruned["active"] = active
    return pruned


def _receipt(event: str, **fields: object) -> None:
    """Append one telemetry line; a receipt failure never affects enforcement."""
    try:
        get_ship_dir()
        _refuse_symlink(SHIP_BUDGET_RECEIPTS)
        line = json.dumps({"event": event, "at": _now(), **fields}, ensure_ascii=False)
        with open(SHIP_BUDGET_RECEIPTS, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if SHIP_BUDGET_RECEIPTS.stat().st_size > _RECEIPTS_MAX_BYTES:
            lines = SHIP_BUDGET_RECEIPTS.read_text(encoding="utf-8").splitlines()
            SHIP_BUDGET_RECEIPTS.write_text("\n".join(lines[-_RECEIPTS_KEEP_LINES:]) + "\n", encoding="utf-8")
    except Exception as exc:
        logger.warning("Could not write budget receipt: %s", exc)


def reserve(*, kind: str = "ship") -> BudgetDecision:
    """Try to reserve a launch slot. Denies with a named reason, never raises."""
    now = _now()
    if is_disabled():
        permit = f"bypass_{int(now)}_{os.getpid()}"
        logger.info("Budget fuse disabled via YEABOI_AI_BUDGET_DISABLE; issuing %s", permit)
        return BudgetDecision(allowed=True, permit_id=permit, reason="budget disabled by env")
    max_concurrent, per_hour, per_day, _pause_minutes = limits()
    try:
        get_ship_dir()
        with _Lock(SHIP_BUDGET_LOCK):
            data = _prune(_read_ledger(SHIP_BUDGET_FILE), now)
            paused_until = float(data.get("paused_until", 0) or 0)
            if paused_until > now:
                until = time.strftime("%H:%M", time.localtime(paused_until))
                reason = f"circuit-open until {until} ({data.get('paused_reason', 'quota error')})"
                _receipt("deny", kind=kind, reason=reason)
                return BudgetDecision(reason=reason)
            active = data.get("active", [])
            if len(active) >= max_concurrent:
                reason = f"global-concurrency ({len(active)}/{max_concurrent} active)"
                _receipt("deny", kind=kind, reason=reason)
                return BudgetDecision(reason=reason)
            launches = data.get("launches", [])
            hour_count = sum(1 for e in launches if now - float(e.get("at", 0)) < HOUR_S)
            if hour_count >= per_hour:
                reason = f"hourly-budget ({hour_count}/{per_hour} in last hour)"
                _receipt("deny", kind=kind, reason=reason)
                return BudgetDecision(reason=reason)
            if len(launches) >= per_day:
                reason = f"daily-budget ({len(launches)}/{per_day} in last 24h)"
                _receipt("deny", kind=kind, reason=reason)
                return BudgetDecision(reason=reason)
            permit = f"permit_{int(now)}_{os.getpid()}_{os.urandom(3).hex()}"
            launches.append({"at": now, "permit": permit, "kind": kind})
            active.append({"at": now, "permit": permit, "pid": os.getpid()})
            data["launches"] = launches
            data["active"] = active
            _write_ledger(SHIP_BUDGET_FILE, data)
    except Exception as exc:
        # Fail CLOSED: an unaccountable launch is what this fuse prevents.
        reason = f"budget-unavailable ({exc})"
        logger.error("Budget reserve failed closed: %s", exc)
        _receipt("deny", kind=kind, reason=reason)
        return BudgetDecision(reason=reason)
    logger.info("Budget reserved %s (%d/%d this hour)", permit, hour_count + 1, per_hour)
    _receipt("launch", kind=kind, permit=permit)
    return BudgetDecision(allowed=True, permit_id=permit)


def heartbeat(permit_id: str) -> None:
    """Refresh a live permit's timestamp so the stale-reaper leaves it alone.

    A whole ship run — agent, validation, an unbounded human gate wait — can
    outlive ``ACTIVE_STALE_S``; without this the concurrency slot would be
    reaped mid-run and a second launch allowed, silently breaking the
    "1 concurrent" invariant. One permit also deliberately covers a run's
    rework attempts: the budget counts *runs started*, not agent invocations.
    Never raises.
    """
    if not permit_id or permit_id.startswith("bypass_"):
        return
    try:
        with _Lock(SHIP_BUDGET_LOCK):
            data = _read_ledger(SHIP_BUDGET_FILE)
            touched = False
            for entry in data.get("active", []):
                if isinstance(entry, dict) and entry.get("permit") == permit_id:
                    entry["at"] = _now()
                    touched = True
            if touched:
                _write_ledger(SHIP_BUDGET_FILE, data)
    except Exception as exc:
        logger.warning("Budget heartbeat failed (slot may expire early): %s", exc)


def release(permit_id: str) -> None:
    """Free the concurrency slot. The launch itself stays counted. Never raises."""
    if not permit_id or permit_id.startswith("bypass_"):
        return
    try:
        with _Lock(SHIP_BUDGET_LOCK):
            data = _prune(_read_ledger(SHIP_BUDGET_FILE), _now())
            before = len(data.get("active", []))
            data["active"] = [e for e in data.get("active", []) if e.get("permit") != permit_id]
            if len(data["active"]) != before:
                _write_ledger(SHIP_BUDGET_FILE, data)
    except Exception as exc:
        logger.warning("Budget release failed (slot will expire on its own): %s", exc)
    _receipt("release", permit=permit_id)


def record_quota_error(detail: str) -> None:
    """Open the circuit breaker after a failed launch's quota error. Never raises."""
    now = _now()
    _minutes = limits()[3]
    try:
        with _Lock(SHIP_BUDGET_LOCK):
            data = _read_ledger(SHIP_BUDGET_FILE)
            data["paused_until"] = now + _minutes * 60
            data["paused_reason"] = (detail or "quota error")[:200]
            _write_ledger(SHIP_BUDGET_FILE, data)
    except Exception as exc:
        logger.error("Could not open budget circuit breaker: %s", exc)
        return
    logger.warning("Budget circuit open for %d minutes: %s", _minutes, detail[:200])
    _receipt("quota_pause", minutes=_minutes, detail=(detail or "")[:200])


def status() -> BudgetStatus:
    """A lock-free snapshot for the TUI/CLI. Empty on any read problem."""
    max_concurrent, per_hour, per_day, _pause = limits()
    now = _now()
    try:
        data = _prune(_read_ledger(SHIP_BUDGET_FILE), now)
    except Exception:
        data = {}
    launches = data.get("launches", [])
    return BudgetStatus(
        active=len(data.get("active", [])),
        launched_last_hour=sum(1 for e in launches if now - float(e.get("at", 0)) < HOUR_S),
        launched_last_day=len(launches),
        max_concurrent=max_concurrent,
        max_per_hour=per_hour,
        max_per_day=per_day,
        paused_until=float(data.get("paused_until", 0) or 0) if float(data.get("paused_until", 0) or 0) > now else 0.0,
        paused_reason=str(data.get("paused_reason", "")) if float(data.get("paused_until", 0) or 0) > now else "",
    )
