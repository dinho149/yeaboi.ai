"""Compatibility facade for shared tracker-backed team-roster discovery."""

from __future__ import annotations

from yeaboi.agent.state import EngineerRef
from yeaboi.team_roster import DEFAULT_LOOKBACK_DAYS, fetch_roster_result

_ROSTER_LOOKBACK_DAYS = DEFAULT_LOOKBACK_DAYS


def fetch_roster(
    *,
    jira_project: str = "",
    azdo_project: str = "",
    days: int = _ROSTER_LOOKBACK_DAYS,
    db_path=None,
    force_refresh: bool = False,
) -> list[EngineerRef]:
    """Return recent/WIP assignees using the shared lightweight roster service."""
    result = fetch_roster_result(
        jira_project=jira_project,
        azdo_project=azdo_project,
        days=days,
        db_path=db_path,
        force_refresh=force_refresh,
    )
    # identity/email are carried through, not dropped: they are what
    # performance/identity.py closes over to attach a person's commits, pages
    # and ceremony cards to them when those are authored under another handle.
    return [
        EngineerRef(
            name=member.name,
            source=member.source,
            external_id=member.identity,
            email=member.email,
        )
        for member in result.members
    ]
