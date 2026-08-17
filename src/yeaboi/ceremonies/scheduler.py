"""OS-native scheduling — the clock behind every recurring run.

The whole point of this module is that a ceremony fires *even when yeaboi is
closed*. Rather than keep a daemon alive, we register a job with the operating
system's own scheduler — launchd on macOS, crontab on Linux.

It grew out of the standup, which was for a long time the only thing with a
cadence, and it keeps that job's exact identifiers: ``JOB_STANDUP`` still maps
to the empty suffix, because ``com.yeaboi.standup.<session>`` is what is already
installed on real machines. ``standup/scheduler.py`` is now a shim over here.

Three job families, and every install/remove/status path takes the kind:

- ``JOB_STANDUP`` — interactive. A wrapper opens a Terminal window so the run
  can prompt for the user's own update.
- ``JOB_TRANSCRIPT_REMINDER`` — passive. One desktop notification, no window.
- ``ceremony_kind(name)`` — headless. Runs one declared ceremony to its channels
  and exits, and **never opens a window**: a ceremony is something you read in
  Slack at 09:00, not a terminal that appears while you are in a meeting.

Everything is stdlib + subprocess (no APScheduler dependency). Platform is
dispatched on ``sys.platform``. Windows is unsupported (returns a clear message)
— on-demand runs from the TUI still work everywhere.

# See docs: "Daily Standup" — scheduling
"""

from __future__ import annotations

import logging
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# How far ahead of its slot a fire may be and still count as on time. Covers
# clock skew, not a scheduler that woke up on the wrong day.
_EARLY_GRACE = timedelta(minutes=5)

if TYPE_CHECKING:  # a Ceremony is data the scheduler reads, never a dependency it needs at import
    from yeaboi.agent.state import Ceremony

# launchd label / crontab marker are keyed by session so multiple projects can
# each have their own schedule without clobbering one another.
_LABEL_PREFIX = "com.yeaboi.standup"
_CRON_MARKER = "# yeaboi-standup"

# The two original kinds. Removing one kind must never take the other with it,
# and teardown must never miss one: a job still firing after the user turned the
# feature off is the worst possible failure for something whose job is to nudge.
JOB_STANDUP = "standup"
JOB_TRANSCRIPT_REMINDER = "transcript-reminder"
# The pair that goes up and comes down together — see remove_schedule.
_STANDUP_FAMILY = (JOB_STANDUP, JOB_TRANSCRIPT_REMINDER)
# Suffix appended to the label/marker/dir for non-default kinds, so the existing
# standup job keeps the exact identifiers it already installed on real machines.
_KIND_SUFFIX = {JOB_STANDUP: "", JOB_TRANSCRIPT_REMINDER: "-transcript"}

# Ceremonies get their own namespace rather than another suffix on the standup
# one. Two reasons: ``com.yeaboi.standup-weekly-report`` would be a lie, and a
# ceremony *named* "transcript" would otherwise land on the reminder's exact
# label and silently replace it.
CEREMONY_PREFIX = "ceremony."
_CEREMONY_LABEL_PREFIX = "com.yeaboi.ceremony"
_CEREMONY_CRON_MARKER = "# yeaboi-ceremony"


def ceremony_kind(name: str) -> str:
    """The job kind for a declared ceremony."""
    return f"{CEREMONY_PREFIX}{name}"


def ceremony_name(kind: str) -> str:
    """The ceremony behind a job kind, or '' when the kind is not a ceremony."""
    return kind[len(CEREMONY_PREFIX) :] if kind.startswith(CEREMONY_PREFIX) else ""


def _identity(kind: str) -> tuple[str, str]:
    """(launchd label stem, crontab marker stem) for one job kind.

    Every path that names a job on disk resolves through here, so the standup's
    historical identifiers and the ceremony namespace cannot drift apart.
    """
    if kind in _KIND_SUFFIX:
        suffix = _KIND_SUFFIX[kind]
        return f"{_LABEL_PREFIX}{suffix}", f"{_CRON_MARKER}{suffix}"
    name = ceremony_name(kind)
    if name:
        from yeaboi.ceremonies.store import valid_name

        if not valid_name(name):
            raise ValueError(f"ceremony name {name!r} cannot be used in a job label")
        return f"{_CEREMONY_LABEL_PREFIX}.{name}", f"{_CEREMONY_CRON_MARKER}-{name}"
    raise ValueError(f"unknown job kind {kind!r}")


def _opens_a_window(kind: str) -> bool:
    """Only the standup does. Everything else runs headless."""
    return kind == JOB_STANDUP


def _base_argv() -> list[str]:
    """Prefer an installed ``yeaboi`` (or the legacy alias); fall back to the module."""
    exe = shutil.which("yeaboi") or shutil.which("scrum-agent")
    return [exe] if exe else [sys.executable, "-m", "yeaboi.cli"]


def job_path() -> str:
    """The PATH a scheduled job should run with — this process's, or a sane floor.

    launchd hands a job ``/usr/bin:/bin:/usr/sbin:/sbin`` and cron gives it even
    less, so ``git`` (Homebrew, Xcode, asdf, wherever it lives) is simply absent.
    The standup job never hit this because its wrapper opens a Terminal that
    sources the user's profile — a headless ceremony is the first thing here
    exposed to it, and a standup that cannot run git is a standup with no code
    activity in it: wrong rather than broken, which is the worse of the two.

    Captured at install time on purpose. It is the PATH of the shell the user
    set the ceremony up in, which is the one they mean.
    """
    return os.environ.get("PATH", "") or "/usr/local/bin:/usr/bin:/bin"


def _executable_args(session_id: str, kind: str = JOB_STANDUP) -> list[str]:
    """Return the argv the scheduler should invoke for one job kind.

    The standup run is *interactive*: attached to a terminal it prompts for the
    user's update + confirm; with no TTY it falls back to headless
    generate+deliver. The reminder is the opposite — passive, no terminal, it
    posts one desktop notification and exits. A ceremony is headless too, and
    ``--scheduled`` is what arms the staleness and spend guards that only a
    fired run wants.
    """
    base = _base_argv()
    if kind == JOB_TRANSCRIPT_REMINDER:
        return [*base, "--standup-remind-transcript", "--standup-session", session_id]
    name = ceremony_name(kind)
    if name:
        return [*base, "ceremonies", "run", name, "--session", session_id, "--scheduled"]
    return [*base, "--standup-run", "--standup-interactive", "--standup-session", session_id]


def parse_time(hhmm: str) -> tuple[int, int]:
    """Parse 'HH:MM' → (hour, minute). Raises ValueError on bad input."""
    parts = hhmm.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time {hhmm!r} — expected HH:MM")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f"Time out of range: {hhmm!r}")
    return hour, minute


def run_time(standup_time: str, lead_minutes: int) -> tuple[int, int]:
    """Return the (hour, minute) the job should FIRE — ``lead_minutes`` before the standup.

    Wraps around midnight (mod 1440) if the lead pushes before 00:00, so an early
    standup with a large lead still yields a valid clock time.
    """
    hour, minute = parse_time(standup_time)
    total = (hour * 60 + minute - int(lead_minutes)) % 1440
    return total // 60, total % 60


def run_time_str(standup_time: str, lead_minutes: int) -> str:
    """Return the fire time as 'HH:MM' (for display on the dashboard)."""
    h, m = run_time(standup_time, lead_minutes)
    return f"{h:02d}:{m:02d}"


def weekday_list(weekdays: str) -> list[int]:
    """Expand a weekday spec ('1-5', '1,3,5') into a list of ints (Mon=1..Sun=7)."""
    result: list[int] = []
    for chunk in weekdays.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            result.extend(range(int(lo), int(hi) + 1))
        else:
            result.append(int(chunk))
    return result or [1, 2, 3, 4, 5]


_DAY_NAMES = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}


def weekday_spec(days: Iterable[int]) -> str:
    """Compress a set of weekday ints (Mon=1..Sun=7) into a spec string.

    Inverse of ``weekday_list``: ``{1,2,3,4,5}`` → ``"1-5"``, ``{1,3,5}`` →
    ``"1,3,5"``, ``{1,2,4,5}`` → ``"1-2,4-5"``. Empty input falls back to the
    weekday default ``"1-5"`` (matching ``weekday_list("")``).
    """
    uniq = sorted({d for d in days if 1 <= int(d) <= 7})
    if not uniq:
        return "1-5"
    parts: list[str] = []
    start = prev = uniq[0]
    for d in uniq[1:] + [None]:  # sentinel flushes the last run
        if d is not None and d == prev + 1:
            prev = d
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if d is not None:
            start = prev = d
    return ",".join(parts)


def weekday_spec_label(weekdays: str) -> str:
    """Human-readable form of a weekday spec, for dashboards and the hub card.

    ``"1-5"`` → ``"Mon–Fri"``, ``"1-7"`` → ``"Every day"``, ``"1,3,5"`` →
    ``"Mon, Wed, Fri"``.
    """
    days = sorted(set(weekday_list(weekdays)))
    if days == list(range(1, 8)):
        return "Every day"
    if len(days) >= 2 and days == list(range(days[0], days[-1] + 1)):
        return f"{_DAY_NAMES[days[0]]}–{_DAY_NAMES[days[-1]]}"
    return ", ".join(_DAY_NAMES[d] for d in days if d in _DAY_NAMES)


# ---------------------------------------------------------------------------
# macOS — launchd
# ---------------------------------------------------------------------------


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _label(session_id: str, kind: str = JOB_STANDUP) -> str:
    return f"{_identity(kind)[0]}.{session_id.replace('/', '_')}"


def _plist_path(session_id: str, kind: str = JOB_STANDUP) -> Path:
    return _launch_agents_dir() / f"{_label(session_id, kind)}.plist"


def _launcher_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "yeaboi"


def _session_launcher_dir(session_id: str, kind: str = JOB_STANDUP) -> Path:
    # Only the standup writes launcher scripts (it is the one kind that opens a
    # window); for every other kind this is the directory that is checked for
    # and found absent during teardown.
    safe = session_id.replace("/", "_")
    suffix = _KIND_SUFFIX.get(kind)
    if suffix is None:
        return _launcher_dir() / f"ceremony-{ceremony_name(kind)}-{safe}"
    return _launcher_dir() / f"standup{suffix}-{safe}"


def _wrapper_path(session_id: str) -> Path:
    # The filename is what macOS Background Task Management shows in the
    # "can run in the background" popup / Login Items — keep it "yeaboi-standup"
    # (session id lives in the parent directory instead).
    return _session_launcher_dir(session_id) / "yeaboi-standup"


def _run_script_path(session_id: str) -> Path:
    return _session_launcher_dir(session_id) / "run.sh"


def _legacy_launcher_path(session_id: str) -> Path:
    # Pre-overhaul single launcher script; cleaned up on install/remove so a
    # reinstall fully replaces the old osascript-labeled background item.
    safe = session_id.replace("/", "_")
    return _launcher_dir() / f"standup-{safe}.sh"


def _applescript_literal(text: str) -> str:
    """Escape ``text`` for embedding inside an AppleScript double-quoted string."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _write_launcher_scripts(session_id: str) -> Path:
    """Write the two launcher scripts; return the wrapper launchd should execute.

    Two files instead of one on purpose:

    - ``yeaboi-standup`` (the wrapper, launchd's ``ProgramArguments[0]``) calls
      osascript to open a Terminal window. Making it the *first* executable means
      macOS attributes the background item to "yeaboi-standup", not "osascript".
    - ``run.sh`` holds the actual interactive CLI command — the thing the new
      Terminal window runs. Keeping it a separate file (rather than inlining it
      into the AppleScript string) keeps the quoting layers manageable.

    The run.sh path is shell-quoted (it lives under "Application Support", which
    contains a space) and then AppleScript-escaped — the missing shell quoting is
    exactly what used to break every scheduled fire.
    """
    session_dir = _session_launcher_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)

    run_script = _run_script_path(session_id)
    cmd = " ".join(shlex.quote(a) for a in _executable_args(session_id))
    run_script.write_text(f"#!/bin/sh\n# Auto-generated by yeaboi — Daily Standup schedule.\n{cmd}\n")
    run_script.chmod(0o755)

    wrapper = _wrapper_path(session_id)
    # Layered quoting, inside-out: shell-quote run.sh for the shell Terminal
    # spawns, escape that for the AppleScript string literal, then shell-quote
    # the whole -e argument for the wrapper's own shell line.
    terminal_cmd = _applescript_literal(shlex.quote(str(run_script)))
    osa = f'tell application "Terminal" to do script "{terminal_cmd}"'
    wrapper.write_text(
        "#!/bin/sh\n"
        "# Auto-generated by yeaboi — Daily Standup schedule.\n"
        "# Named 'yeaboi-standup' so macOS Background Task Management shows this\n"
        "# name (not 'osascript') in Login Items & Extensions.\n"
        f"exec /usr/bin/osascript -e {shlex.quote(osa)}\n"
    )
    wrapper.chmod(0o755)
    return wrapper


def _install_launchd(session_id: str, hour: int, minute: int, weekdays: list[int], kind: str = JOB_STANDUP) -> str:
    label = _label(session_id, kind)
    # launchd Weekday: Sunday=0/7, Monday=1..Saturday=6. Our 1-7 (Mon-Sun) maps
    # directly except Sunday which we send as 0.
    intervals = [{"Hour": hour, "Minute": minute, "Weekday": (0 if wd == 7 else wd)} for wd in weekdays]

    if _opens_a_window(kind):
        # The wrapper opens a Terminal window (via osascript) so the run is
        # INTERACTIVE (it can prompt for the user's update). LaunchAgents run in
        # the GUI session, so AppleScript to Terminal works while logged in.
        program = [str(_write_launcher_scripts(session_id))]
        _legacy_launcher_path(session_id).unlink(missing_ok=True)
    else:
        # No wrapper, no osascript, no Terminal. That whole stack exists only to
        # make the standup run INTERACTIVE; a notification is passive and a
        # ceremony lands in a channel, so both run the CLI directly and never
        # surprise anyone with a window opening.
        program = _executable_args(session_id, kind)
    plist = {
        "Label": label,
        "ProgramArguments": program,
        "StartCalendarInterval": intervals,
        "RunAtLoad": False,
    }
    if not _opens_a_window(kind):
        # The window-opening kind inherits the user's profile through Terminal.
        # Every other kind gets launchd's bare /usr/bin:/bin, where git does not
        # exist — so it carries the PATH of the shell that installed it.
        plist["EnvironmentVariables"] = {"PATH": job_path()}
    path = _plist_path(session_id, kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        plistlib.dump(plist, fh)
    logger.info("scheduler[launchd]: wrote %s (kind=%s)", path, kind)
    # Reload: bootout any existing instance, then bootstrap the fresh plist.
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, timeout=10, check=False)
    proc = subprocess.run(["launchctl", "load", str(path)], capture_output=True, text=True, timeout=10, check=False)
    if proc.returncode != 0:
        logger.warning("scheduler[launchd]: load returned %d: %s", proc.returncode, proc.stderr.strip())
    if kind == JOB_TRANSCRIPT_REMINDER:
        return f"Transcript reminder set for {hour:02d}:{minute:02d} ({path.name})"
    name = ceremony_name(kind)
    if name:
        return f"{name} scheduled via launchd at {hour:02d}:{minute:02d} ({path.name})"
    return f"Scheduled via launchd at {hour:02d}:{minute:02d} — opens a terminal to prompt ({path.name})"


def _cleanup_launchers(session_id: str, kind: str = JOB_STANDUP) -> None:
    """Remove the wrapper/run.sh pair (and the pre-overhaul .sh launcher)."""
    shutil.rmtree(_session_launcher_dir(session_id, kind), ignore_errors=True)
    if kind == JOB_STANDUP:
        _legacy_launcher_path(session_id).unlink(missing_ok=True)


def _remove_launchd(session_id: str, kind: str = JOB_STANDUP) -> str:
    path = _plist_path(session_id, kind)
    if not path.exists():
        _cleanup_launchers(session_id, kind)
        return "No launchd schedule found."
    subprocess.run(["launchctl", "unload", str(path)], capture_output=True, timeout=10, check=False)
    path.unlink(missing_ok=True)
    _cleanup_launchers(session_id, kind)
    logger.info("scheduler[launchd]: removed %s (kind=%s)", path, kind)
    return "Removed launchd schedule."


def _status_launchd(session_id: str, kind: str = JOB_STANDUP) -> dict:
    path = _plist_path(session_id, kind)
    return {"platform": "launchd", "installed": path.exists(), "path": str(path)}


# ---------------------------------------------------------------------------
# Linux — crontab
# ---------------------------------------------------------------------------


def _read_crontab() -> list[str]:
    proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10, check=False)
    if proc.returncode != 0:
        # No crontab yet (or error) → treat as empty.
        return []
    return proc.stdout.splitlines()


def _write_crontab(lines: list[str]) -> None:
    content = "\n".join(lines).rstrip("\n") + "\n"
    subprocess.run(["crontab", "-"], input=content, text=True, timeout=10, check=True)


def _cron_marker(session_id: str, kind: str = JOB_STANDUP) -> str:
    """The trailing comment that identifies one job.

    No two kinds can collide: every marker ends with a SPACE before the session
    id ("# yeaboi-standup s1"), so it is not a substring of
    "# yeaboi-standup-transcript s1", and "# yeaboi-ceremony-report s1" is not
    one of "# yeaboi-ceremony-report-weekly s1". Removing one kind therefore
    never takes another with it — which matters, because leaving a job firing
    after the user disabled it is the worst failure this feature has.
    """
    return f"{_identity(kind)[1]} {session_id}"


def _cron_command(session_id: str, kind: str) -> str:
    """The shell command for one job's crontab line.

    cron gives a job an even barer PATH than launchd does, and it hands the
    whole line to /bin/sh — so a leading ``PATH=…`` assignment is both valid and
    the only per-entry way to say it. ``%`` is cron's own line terminator and
    has to be escaped, or the command silently stops at the first one.
    """
    cmd = " ".join(shlex.quote(arg) for arg in _executable_args(session_id, kind))
    if _opens_a_window(kind):
        return cmd
    return f"PATH={shlex.quote(job_path())} {cmd}".replace("%", r"\%")


def _install_cron(session_id: str, hour: int, minute: int, weekdays: list[int], kind: str = JOB_STANDUP) -> str:
    marker = _cron_marker(session_id, kind)
    # cron weekday: 0-6 (Sun-Sat) or 1-7; we send Mon-Sun as 1-7 (7 = Sunday, accepted by cron).
    dow = ",".join(str(wd) for wd in weekdays)
    entry = f"{minute} {hour} * * {dow} {_cron_command(session_id, kind)} {marker}"

    # Remove any prior block for this session+kind, then append the new one.
    lines = [ln for ln in _read_crontab() if marker not in ln]
    lines.append(entry)
    _write_crontab(lines)
    logger.info("scheduler[cron]: installed entry for %s (kind=%s)", session_id, kind)
    if kind == JOB_TRANSCRIPT_REMINDER:
        return f"Transcript reminder set for {hour:02d}:{minute:02d}"
    name = ceremony_name(kind)
    if name:
        return f"{name} scheduled via crontab at {hour:02d}:{minute:02d}"
    return f"Scheduled via crontab at {hour:02d}:{minute:02d}"


def _remove_cron(session_id: str, kind: str = JOB_STANDUP) -> str:
    marker = _cron_marker(session_id, kind)
    lines = _read_crontab()
    kept = [ln for ln in lines if marker not in ln]
    if len(kept) == len(lines):
        return "No crontab schedule found."
    _write_crontab(kept)
    logger.info("scheduler[cron]: removed entry for %s (kind=%s)", session_id, kind)
    return "Removed crontab schedule."


def _status_cron(session_id: str, kind: str = JOB_STANDUP) -> dict:
    marker = _cron_marker(session_id, kind)
    installed = any(marker in ln for ln in _read_crontab())
    return {"platform": "cron", "installed": installed, "path": "crontab"}


# ---------------------------------------------------------------------------
# Public API — platform dispatch
# ---------------------------------------------------------------------------


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def install_schedule(
    session_id: str,
    standup_time: str,
    weekdays: str = "1-5",
    lead_minutes: int = 10,
    kind: str = JOB_STANDUP,
) -> str:
    """Install/replace an OS job for a session. Returns a status message.

    ``standup_time`` is when the standup HAPPENS (e.g. "10:00"); the job fires
    ``lead_minutes`` earlier so the summary is delivered before the meeting. A
    NEGATIVE lead therefore fires *after* the standup, which is exactly what the
    transcript reminder wants — ``run_time`` already wraps mod 1440, so no new
    time arithmetic is needed for it.
    """
    hour, minute = run_time(standup_time, lead_minutes)  # lead-adjusted fire time
    wd = weekday_list(weekdays)
    logger.info(
        "install_schedule: session=%s kind=%s standup=%s lead=%d fire=%02d:%02d weekdays=%s",
        session_id,
        kind,
        standup_time,
        lead_minutes,
        hour,
        minute,
        weekdays,
    )
    try:
        if _is_macos():
            return _install_launchd(session_id, hour, minute, wd, kind)
        if _is_linux():
            return _install_cron(session_id, hour, minute, wd, kind)
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("install_schedule failed: %s", e)
        return f"Failed to install schedule: {e}"
    return "Scheduling is not supported on this platform — use on-demand standup runs instead."


def install_transcript_reminder(
    session_id: str, standup_time: str, weekdays: str = "1-5", after_minutes: int = 45
) -> str:
    """Remind the user to drop the transcript, ``after_minutes`` AFTER the standup."""
    return install_schedule(
        session_id, standup_time, weekdays, lead_minutes=-int(after_minutes), kind=JOB_TRANSCRIPT_REMINDER
    )


def remove_schedule(session_id: str, kind: str | None = None) -> str:
    """Remove a session's job(s). ``kind=None`` removes the whole STANDUP family.

    Removing both standup kinds together is the default on purpose. A user who
    turns their standup off has turned the whole thing off, and a reminder still
    firing afterwards is the worst failure this feature can have — so the safe
    behaviour is what you get by not thinking about it.

    It is deliberately *not* "every job this session has". Ceremonies are
    declared separately and removed by name: switching the standup off must not
    silently take the Monday delivery report with it.
    """
    kinds = _STANDUP_FAMILY if kind is None else (kind,)
    logger.info("remove_schedule: session=%s kinds=%s", session_id, ",".join(kinds))
    messages: list[str] = []
    try:
        for one in kinds:
            if _is_macos():
                messages.append(_remove_launchd(session_id, one))
            elif _is_linux():
                messages.append(_remove_cron(session_id, one))
            else:
                return "Scheduling is not supported on this platform."
    except (subprocess.SubprocessError, OSError) as e:
        logger.error("remove_schedule failed: %s", e)
        return f"Failed to remove schedule: {e}"
    # Report the removal that actually happened; "no schedule found" from the
    # kind that was never installed is noise.
    removed = [m for m in messages if m.startswith("Removed")]
    return removed[0] if removed else (messages[0] if messages else "No schedule found.")


def install_ceremony(session_id: str, name: str, at: str, weekdays: str = "1-5") -> str:
    """Install/replace the job for one declared ceremony.

    No lead time: a standup is delivered *before* a meeting that starts at a
    known hour, whereas a ceremony's time is simply when it should happen.
    """
    return install_schedule(session_id, at, weekdays, lead_minutes=0, kind=ceremony_kind(name))


def remove_ceremony(session_id: str, name: str) -> str:
    """Tear down one ceremony's job. Removing it from the store is the caller's half."""
    return remove_schedule(session_id, kind=ceremony_kind(name))


def installed_ceremonies(session_id: str) -> list[str]:
    """Ceremony names with a job actually installed for ``session_id``.

    Read off the operating system rather than the store, because those are the
    two things that can disagree — a ceremony deleted while its plist survived
    is exactly the state where the store is the wrong place to ask.
    """
    safe = session_id.replace("/", "_")
    names: list[str] = []
    try:
        if _is_macos():
            prefix, suffix = f"{_CEREMONY_LABEL_PREFIX}.", f".{safe}.plist"
            for path in _launch_agents_dir().glob(f"{_CEREMONY_LABEL_PREFIX}.*.{safe}.plist"):
                names.append(path.name[len(prefix) : -len(suffix)])
        elif _is_linux():
            token = f"{_CEREMONY_CRON_MARKER}-"
            tail = f" {session_id}"
            for line in _read_crontab():
                # `in` first: rpartition hands back the WHOLE line when the
                # separator is absent, so a standup entry would otherwise read
                # as a ceremony named after its own crontab line.
                _before, found, marker = line.rpartition(token)
                if found and marker.endswith(tail):
                    names.append(marker[: -len(tail)])
    except (subprocess.SubprocessError, OSError) as e:
        # A discovery failure must not read as "nothing is installed" to a
        # caller about to decide something; it reads as "cannot tell" upstream,
        # which is why this logs rather than swallowing.
        logger.warning("installed_ceremonies failed: %s", e)
    return sorted(set(names))


def get_schedule_status(session_id: str, kind: str = JOB_STANDUP) -> dict:
    """Return {platform, installed, path} describing one job's state."""
    try:
        if _is_macos():
            return _status_launchd(session_id, kind)
        if _is_linux():
            return _status_cron(session_id, kind)
    except (subprocess.SubprocessError, OSError) as e:
        logger.warning("get_schedule_status failed: %s", e)
    return {"platform": "unsupported", "installed": False, "path": ""}


def _installed_fire_time(session_id: str, kind: str) -> tuple[int, int] | None:
    """The (hour, minute) an installed job fires at, or None if there isn't one."""
    if _is_macos():
        path = _plist_path(session_id, kind)
        if not path.exists():
            return None
        with path.open("rb") as fh:
            plist = plistlib.load(fh)
        intervals = plist.get("StartCalendarInterval") or []
        if isinstance(intervals, dict):
            intervals = [intervals]
        for entry in intervals:
            if isinstance(entry, dict) and "Hour" in entry and "Minute" in entry:
                return int(entry["Hour"]), int(entry["Minute"])
        return None
    if _is_linux():
        marker = _cron_marker(session_id, kind)
        for line in _read_crontab():
            if marker not in line:
                continue
            fields = line.split()
            if len(fields) >= 2 and fields[0].isdigit() and fields[1].isdigit():
                return int(fields[1]), int(fields[0])
        return None
    return None


def transcript_reminder_offset(session_id: str, standup_time: str) -> int:
    """Minutes AFTER the standup that the installed reminder fires (0 = none).

    The existence of the OS job is the on/off setting, which needs no storage —
    but the offset is a value, and a wizard that cannot read it back would
    silently reset "2 hours after" to the default the next time the user walked
    through the steps to change something else. The job already encodes it, so
    read it from there rather than adding a config column that could disagree
    with what is actually installed.
    """
    try:
        fire = _installed_fire_time(session_id, kind=JOB_TRANSCRIPT_REMINDER)
        if fire is None:
            return 0
        hour, minute = parse_time(standup_time)
    except (subprocess.SubprocessError, OSError, ValueError, plistlib.InvalidFileException) as e:
        logger.warning("transcript_reminder_offset failed: %s", e)
        return 0
    return ((fire[0] * 60 + fire[1]) - (hour * 60 + minute)) % 1440


def occurrence_for(ceremony: Ceremony, now: datetime) -> datetime | None:
    """The scheduled slot this fire belongs to, or None when it cannot be read.

    Local time throughout, because that is what launchd and cron fire in.

    The slot, not today's clock time. The case this exists for is a laptop
    asleep across the slot, and that sleep does not politely end before
    midnight: a 09:00 Monday job that launchd coalesces and fires at 07:00
    Tuesday belongs to *Monday*, but comparing it to Tuesday's own 09:00 makes
    it look two hours early. Weekdays are part of the answer too — a
    Monday-only report woken on Wednesday is measured from Monday.

    Two callers need exactly this, which is why it is one function: staleness
    asks how far past the slot the fire is, and a one-shot skip asks which slot
    is being skipped. A bool skip would be consumed by that Tuesday-morning
    fire and burn itself on the occurrence the user already saw.
    """
    try:
        hour, minute = parse_time(ceremony.at)
    except ValueError:
        logger.warning("ceremony %s has an unparseable time %r", ceremony.name, ceremony.at)
        return None
    try:
        days = {d for d in weekday_list(ceremony.weekdays) if 1 <= d <= 7}
    except ValueError:
        logger.warning("ceremony %s has an unparseable weekday spec %r", ceremony.name, ceremony.weekdays)
        days = set()

    todays = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # A fire that just beats its own slot is early, never stale — reading it as
    # late for the previous occurrence is the one way this guard could suppress
    # a run that is perfectly on time. The window is minutes wide on purpose: a
    # scheduler firing *hours* early is not an early fire, it is a late one for
    # the occurrence before, which is precisely the wake-up case above.
    if todays - _EARLY_GRACE <= now < todays and (not days or now.isoweekday() in days):
        return todays

    # Walk back to the latest occurrence at or before now. A week covers every
    # spec, since weekdays repeat every seven days.
    for back in range(8):
        candidate = todays - timedelta(days=back)
        if candidate > now:
            continue
        if days and candidate.isoweekday() not in days:
            continue
        return candidate
    return None


def next_occurrence(ceremony: Ceremony, now: datetime | None = None) -> str:
    """The ISO date of the next slot, or '' when the cadence cannot be read.

    What a surface passes to ``store.set_skip_next``: "skip the next one" has
    to resolve to a *date* at the moment it is asked, because that is the only
    thing a coalesced fire the following morning can be checked against.
    """
    moment = now or datetime.now()
    try:
        hour, minute = parse_time(ceremony.at)
        days = {d for d in weekday_list(ceremony.weekdays) if 1 <= d <= 7}
    except ValueError:
        logger.warning("ceremony %s: cannot read its cadence to find the next slot", ceremony.name)
        return ""
    todays = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    for ahead in range(8):
        candidate = todays + timedelta(days=ahead)
        if candidate <= moment:
            continue
        if days and candidate.isoweekday() not in days:
            continue
        return candidate.date().isoformat()
    return ""
