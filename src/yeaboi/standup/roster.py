"""Tracker-backed team discovery for Daily Standup.

The Standup Team picker deliberately mirrors Analysis mode: choose Jira,
Azure DevOps, or both, then select people from the resulting assignee roster.
Discovery is deterministic and best-effort; it never invokes an LLM.

# See docs: "Daily Standup" — recent-activity collection
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TRACKER_JIRA = "jira"
TRACKER_AZDO = "azure_devops"
TRACKER_SOURCES = (TRACKER_JIRA, TRACKER_AZDO)


def validate_tracker_sources(sources: list[str] | tuple[str, ...] | None) -> list[str]:
    """Return a stable, non-empty tracker selection or raise on unknown values."""
    selected = list(dict.fromkeys((TRACKER_JIRA,) if sources is None else sources))
    bad = [source for source in selected if source not in TRACKER_SOURCES]
    if bad:
        raise ValueError(f"unknown standup tracker source(s) {bad} — valid: {', '.join(TRACKER_SOURCES)}")
    if not selected:
        raise ValueError("select at least one standup tracker source")
    return [source for source in TRACKER_SOURCES if source in selected]


def default_tracker_sources(*, jira_project: str = "", azdo_project: str = "") -> list[str]:
    """First-run default: Jira when configured, otherwise Azure DevOps."""
    if jira_project:
        return [TRACKER_JIRA]
    if azdo_project:
        return [TRACKER_AZDO]
    return [TRACKER_JIRA]


def discover_team_members(
    sources: list[str] | tuple[str, ...] | None,
    *,
    jira_project: str = "",
    azdo_project: str = "",
    days: int = 30,
) -> list[str]:
    """Discover a stable union of recent assignees from selected trackers."""
    selected = validate_tracker_sources(sources)
    from yeaboi.performance.roster import fetch_roster

    logger.info("standup roster discovery: sources=%s days=%d", selected, days)
    selected_jira = jira_project if TRACKER_JIRA in selected else ""
    selected_azdo = azdo_project if TRACKER_AZDO in selected else ""
    if not selected_jira and not selected_azdo:
        logger.info("standup roster discovery: selected trackers are not configured")
        return []
    refs = fetch_roster(
        jira_project=selected_jira,
        azdo_project=selected_azdo,
        days=days,
    )
    names = sorted({ref.name.strip() for ref in refs if ref.name.strip()}, key=str.lower)
    logger.info("standup roster discovery: found %d member(s)", len(names))
    return names
