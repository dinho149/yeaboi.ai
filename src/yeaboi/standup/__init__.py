"""Daily Standup mode — scheduled, delivery-capable daily scrum for a session.

The standup subsystem detects what a team did since the last standup (from
ticket, code, and docs activity), computes which day of the sprint you're on
plus a confidence score, and delivers a summary to configured channels. It can
run headlessly on an OS schedule (launchd/cron) so it fires even when the main
app is closed.

# See docs: "Daily Standup" — engine, collector, confidence, delivery, scheduling

Public API is re-exported here so callers can do
``from yeaboi.standup import run_standup`` without knowing the module layout.
Submodules are imported lazily inside functions where they pull optional
integration SDKs (Jira/GitHub/etc.), mirroring the tools/ lazy-import convention.
"""

from yeaboi.standup.store import StandupStore

__all__ = ["StandupStore", "run_standup"]


def run_standup(session_id: str, **kwargs: object):  # type: ignore[no-untyped-def]
    """Thin re-export of engine.run_standup (lazy import to keep this package light)."""
    from yeaboi.standup.engine import run_standup as _run

    return _run(session_id, **kwargs)  # type: ignore[arg-type]
