"""Standup scheduling — a shim over :mod:`yeaboi.ceremonies.scheduler`.

The OS-job installer used to live here, back when the standup was the only
thing in yeaboi with a cadence. It now serves every ceremony, so it moved; this
module stays because the standup's own callers (the CLI flags, the TUI schedule
wizard) address it by this name, and because the standup job's identifiers —
``com.yeaboi.standup.<session>`` and its crontab twin — are already installed on
real machines and must not change.

Import from :mod:`yeaboi.ceremonies.scheduler` for new code.
"""

from __future__ import annotations

from yeaboi.ceremonies.scheduler import (
    JOB_STANDUP,
    JOB_TRANSCRIPT_REMINDER,
    get_schedule_status,
    install_schedule,
    install_transcript_reminder,
    parse_time,
    remove_schedule,
    run_time,
    run_time_str,
    transcript_reminder_offset,
    weekday_list,
    weekday_spec,
    weekday_spec_label,
)

__all__ = [
    "JOB_STANDUP",
    "JOB_TRANSCRIPT_REMINDER",
    "get_schedule_status",
    "install_schedule",
    "install_transcript_reminder",
    "parse_time",
    "remove_schedule",
    "run_time",
    "run_time_str",
    "transcript_reminder_offset",
    "weekday_list",
    "weekday_spec",
    "weekday_spec_label",
]
