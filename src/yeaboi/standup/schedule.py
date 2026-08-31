"""Saving a standup schedule — the config write plus the OS jobs it implies.

The wizard's *steps* stay with each surface (arrow keys in the terminal, a form
on the desktop); what a completed wizard DOES lives here, so both surfaces
install exactly the same jobs. Deliberately not in ``engine.py``: this is a
settings write, not a pipeline, and the engine glob would force it into the
parity registry as a capability of its own.

The transcript reminder has no config column — the installed job IS the
setting, so its offset is read back off the OS rather than stored twice.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Minutes AFTER the standup that a transcript reminder may fire; 0 = none.
REMINDER_PRESETS: tuple[int, ...] = (0, 30, 60, 120)

#: Presets the pickers offer. Every surface shows the same shortlist, and each
#: also accepts a value outside it.
TIME_PRESETS: tuple[str, ...] = ("09:00", "09:30", "10:00", "10:30", "11:00")
LEAD_PRESETS: tuple[int, ...] = (5, 10, 15, 30)

#: Channel key → what picking it actually does.
CHANNEL_DESCRIPTIONS: dict[str, str] = {
    "terminal": "print in the terminal the run opens",
    "desktop": "macOS/Linux system notification",
    "slack": "post to Slack (needs SLACK_WEBHOOK_URL)",
    "email": "send via SMTP (needs STANDUP_SMTP_* settings)",
}


def nearest_reminder_preset(minutes: int) -> int:
    """Snap an installed offset onto the shortlist the pickers offer."""
    if not minutes or minutes in REMINDER_PRESETS:
        return minutes if minutes in REMINDER_PRESETS else 0
    return min((p for p in REMINDER_PRESETS if p), key=lambda p: abs(p - minutes))


def current_schedule(session_id: str, *, db_path=None) -> dict:
    """The saved schedule fields plus the reminder offset the OS actually has."""
    from yeaboi.ceremonies.scheduler import transcript_reminder_offset
    from yeaboi.paths import get_db_path
    from yeaboi.standup.delivery import ALL_CHANNELS
    from yeaboi.standup.store import StandupStore

    with StandupStore(db_path or get_db_path()) as store:
        config = store.load_config(session_id) or {}
    time_val = config.get("time", "10:00")
    channels = [c for c in config.get("delivery_channels", ["terminal"]) if c in ALL_CHANNELS] or ["terminal"]
    return {
        "session_id": session_id,
        "enabled": bool(config.get("enabled")),
        "time": time_val,
        "lead_minutes": int(config.get("lead_minutes", 10)),
        "weekdays": config.get("weekdays", "1-5"),
        "delivery_channels": channels,
        "remind_after": transcript_reminder_offset(session_id, time_val),
        "valid_channels": list(ALL_CHANNELS),
    }


def apply_schedule(
    session_id: str,
    *,
    enabled: bool,
    time: str,
    weekdays: str,
    lead_minutes: int,
    delivery_channels: list[str],
    remind_after: int = 0,
    db_path=None,
) -> str:
    """Merge-save the schedule fields and install (or remove) the OS jobs.

    Identity and scope fields pass through untouched — this writes only what a
    schedule wizard collects. Returns the scheduler's status message.
    """
    from yeaboi.ceremonies.scheduler import (
        JOB_TRANSCRIPT_REMINDER,
        install_schedule,
        install_transcript_reminder,
        remove_schedule,
    )
    from yeaboi.paths import get_db_path
    from yeaboi.standup.store import StandupStore

    with StandupStore(db_path or get_db_path()) as store:
        existing = store.load_config(session_id) or {}
        store.save_config(
            session_id,
            enabled=enabled,
            time=time,
            lead_minutes=lead_minutes,
            weekdays=weekdays,
            delivery_channels=delivery_channels,
            timezone=existing.get("timezone", ""),
            repo_path=existing.get("repo_path", ""),
            my_aliases=existing.get("my_aliases", ""),
            tracker_sources=existing.get("tracker_sources", ["jira"]),
            team_members=existing.get("team_members", []),
            roster_configured=existing.get("roster_configured", False),
            code_sources=existing.get("code_sources", []),
            github_owners=existing.get("github_owners", []),
            github_repositories=existing.get("github_repositories", []),
            github_excluded_repositories=existing.get("github_excluded_repositories", []),
            azdo_projects=existing.get("azdo_projects", []),
            azdo_repositories=existing.get("azdo_repositories", []),
            code_scope_configured=existing.get("code_scope_configured", False),
            documentation_sources=existing.get("documentation_sources", []),
            documentation_scope_configured=existing.get("documentation_scope_configured", False),
            automation_markers=existing.get("automation_markers", ""),
            automation_handling=existing.get("automation_handling", "exclude"),
            transcript_dir=existing.get("transcript_dir", ""),
            transcript_review_enabled=existing.get("transcript_review_enabled", True),
            habit_detection=existing.get("habit_detection", "on"),
            habit_rules=existing.get("habit_rules", ""),
            habit_ai_match=existing.get("habit_ai_match", "on"),
            context_deps=existing.get("context_deps"),
        )
    if enabled:
        message = install_schedule(session_id, time, weekdays, lead_minutes)
        # A reminder is only meaningful alongside a scheduled standup; when the
        # schedule is off, remove_schedule below tears BOTH kinds down, so a
        # disabled standup can never leave notifications firing.
        if remind_after:
            message += "  " + install_transcript_reminder(session_id, time, weekdays, remind_after)
        else:
            remove_schedule(session_id, kind=JOB_TRANSCRIPT_REMINDER)
    else:
        message = remove_schedule(session_id)  # every kind
    logger.info(
        "standup schedule saved: session=%s enabled=%s remind_after=%s -> %s",
        session_id,
        enabled,
        remind_after,
        message,
    )
    return message
