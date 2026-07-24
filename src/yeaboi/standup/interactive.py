"""Interactive scheduled standup run — prompts for your update before generating.

The OS scheduler (launchd/cron) fires this a few minutes before the standup. When
it runs attached to a terminal (macOS opens Terminal for it), it gives the user a
short, timed window to type their own update and confirm, then generates +
delivers. If there's no response within the window it proceeds anyway (inferring),
so the standup is never blocked. When it runs with NO terminal (headless Linux
cron), it skips the prompts and behaves exactly like the plain headless run.

Timed input uses ``select.select`` on stdin (POSIX) so the countdown can expire
without a keypress — there's no cross-platform way to do this on Windows, but
scheduling is unsupported there anyway.

# See docs: "Daily Standup" — scheduling, interactive run
"""

from __future__ import annotations

import logging
import select
import sys
from datetime import date

logger = logging.getLogger(__name__)


def _timed_input(prompt: str, timeout: float) -> str | None:
    """Print ``prompt`` and read one line, or return None if ``timeout`` elapses.

    Returns the typed line (without newline) on input, or None on timeout/EOF.
    Only used when stdin is a TTY.
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        sys.stdout.write("\n")
        return None
    line = sys.stdin.readline()
    if not line:  # EOF (Ctrl-D)
        return None
    return line.rstrip("\n")


def run_interactive_standup(
    session_id: str,
    *,
    channels: list[str] | None = None,
    window_seconds: float = 90.0,
    db_path=None,
    today: date | None = None,
) -> int:
    """Run a standup, prompting for the user's update first when a TTY is present.

    Returns a process exit code (0 = delivered, non-zero = error). Falls back to
    the plain headless generate+deliver when stdin is not a TTY.
    """
    from yeaboi.standup.engine import run_standup

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    logger.info("run_interactive_standup: session=%s interactive=%s", session_id, interactive)

    if not interactive:
        # Headless (e.g. Linux cron with no display) — same as the plain run.
        run_standup(session_id, channels=channels, deliver=True, db_path=db_path, today=today)
        return 0

    from yeaboi.config import get_standup_user_name
    from yeaboi.standup.store import StandupStore

    today = today or date.today()
    print("\n─── Daily Standup ───\n")
    print(f"Session: {session_id}")
    print(f"(auto-continues in {int(window_seconds)}s if you don't respond)\n")

    # 1. Timed prompt for the user's own update.
    update = _timed_input("Your update for today (Enter to skip): ", window_seconds)
    if update and update.strip():
        member = get_standup_user_name()
        with StandupStore(db_path or _default_db()) as store:
            store.save_my_update(session_id, today.isoformat(), member, update.strip())
        print(f"Saved your update as {member}.")

    # 2. Quick confirm (default Yes) with a short window.
    confirm = _timed_input("Send standup now? (Y/n): ", 15.0)
    if confirm is not None and confirm.strip().lower() in ("n", "no"):
        print("Standup cancelled.")
        return 0

    # 3. Generate + deliver.
    print("\nGenerating and delivering standup…\n")
    try:
        report = run_standup(session_id, channels=channels, deliver=True, db_path=db_path, today=today)
    except Exception as e:
        logger.error("interactive standup failed: %s", e, exc_info=True)
        print(f"Error: {e}")
        _hold()
        return 1

    from yeaboi.standup.render import format_standup_plaintext

    print(format_standup_plaintext(report))
    if report.warnings:
        print("\n⚠ Notices:")
        for w in report.warnings:
            print(f"  - {w}")
    _hold()
    return 0


def _default_db():
    from yeaboi.paths import get_db_path

    return get_db_path()


def _hold() -> None:
    """Keep the terminal window open briefly so the user can read the result."""
    _timed_input("\nDone — press Enter to close (auto-closes in 20s). ", 20.0)
