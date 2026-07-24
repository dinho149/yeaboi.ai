"""Build the engineer roster for Performance mode from Jira / Azure DevOps.

The roster is the list of *real people who did work* — we derive it from the
assignees on recently-updated tickets, not from the plan's team-size number. This
reuses the same recent-activity helpers the Daily Standup collector uses
(``jira_recent_activity`` / ``azdevops_recent_activity``), so there is no new API
surface to maintain and the roster reflects who is actually active on the board.

Tool helpers are imported lazily (optional SDKs), same convention as
standup/collector.py and standup/sprint_context.py. Everything degrades to an
empty roster on missing config / auth failure — the page then shows a clear
"connect Jira or Azure DevOps" hint instead of crashing.

# See docs: "Daily Standup" — recent-activity collection
# See docs: "Tools" — Jira, Azure DevOps
"""

from __future__ import annotations

import logging

from yeaboi.agent.state import EngineerRef

logger = logging.getLogger(__name__)

# How far back to look when discovering who's on the team. A month comfortably
# covers a sprint or two so short-term absentees still appear.
_ROSTER_LOOKBACK_DAYS = 30


def _distinct_authors(items: list[dict]) -> list[str]:
    """Return distinct non-empty author display names, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        name = (item.get("author") or "").strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _jira_members(jira_project: str, days: int) -> list[EngineerRef]:
    """Best-effort Jira roster: distinct assignees on recently-updated + in-progress issues.

    Changelog and comment items are excluded — they credit anyone who touched a
    ticket (a commenting stakeholder, a drive-by field edit), which is activity
    worth showing in the standup feed but not evidence of team membership. Only
    the assignee-credited kinds (``issue``/``wip``) count toward the roster.
    """
    if not jira_project:
        return []
    try:
        from yeaboi.tools.jira import jira_recent_activity

        items = jira_recent_activity(jira_project, days=days, include_changelog=False, include_comments=False)
    except ImportError:
        logger.warning("Jira SDK not installed — skipping Jira roster")
        return []
    except Exception as e:  # noqa: BLE001 — roster is best-effort
        logger.warning("Jira roster failed: %s", e)
        return []
    return [EngineerRef(name=name, source="jira") for name in _distinct_authors(items)]


def _azdo_members(azdo_project: str, days: int) -> list[EngineerRef]:
    """Best-effort Azure DevOps roster: distinct assignees on recent work items."""
    if not azdo_project:
        return []
    try:
        from yeaboi.tools.azure_devops import azdevops_recent_activity

        items = azdevops_recent_activity(azdo_project, days=days)
    except ImportError:
        logger.warning("Azure DevOps SDK not installed — skipping AzDO roster")
        return []
    except Exception as e:  # noqa: BLE001 — roster is best-effort
        logger.warning("Azure DevOps roster failed: %s", e)
        return []
    return [EngineerRef(name=name, source="azuredevops") for name in _distinct_authors(items)]


def fetch_roster(
    *,
    jira_project: str = "",
    azdo_project: str = "",
    days: int = _ROSTER_LOOKBACK_DAYS,
) -> list[EngineerRef]:
    """Return the engineer roster from Jira + Azure DevOps assignees.

    Both sources are merged and de-duplicated by display name (first source wins),
    sorted alphabetically for a stable page. When neither tracker is configured the
    result is an empty list — the caller shows a "connect a tracker" message.

    Args:
        jira_project: Jira project key (falls back to JIRA_PROJECT_KEY inside the tool).
        azdo_project: Azure DevOps project (falls back to AZURE_DEVOPS_PROJECT).
        days: look-back window for discovering active assignees.
    """
    # Resolve unset identifiers from config so the TUI can call with no args.
    if not jira_project and not azdo_project:
        from yeaboi.config import get_azure_devops_project, get_jira_project_key

        jira_project = get_jira_project_key() or ""
        azdo_project = get_azure_devops_project() or ""

    logger.info("fetch_roster: jira=%r azdo=%r days=%d", jira_project, azdo_project, days)
    merged: dict[str, EngineerRef] = {}
    for ref in _jira_members(jira_project, days) + _azdo_members(azdo_project, days):
        merged.setdefault(ref.name, ref)  # first source (Jira) wins on a name clash

    roster = sorted(merged.values(), key=lambda r: r.name.lower())
    logger.info("fetch_roster: %d engineer(s) discovered", len(roster))
    return roster
