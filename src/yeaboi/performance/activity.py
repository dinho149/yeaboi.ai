"""Gather one engineer's worked tickets across the current + prior sprint.

Deterministic (no LLM): reuses the same recent-activity helpers the Daily Standup
collector uses (``jira_recent_activity`` / ``azdevops_recent_activity``) plus the
standup's ``sprint_context.gather`` for the active-sprint name and start date, then
filters to the chosen engineer and splits their tickets into the *current* vs
*previous* sprint window by timestamp. This EngineerActivity is the concrete
evidence the 1:1-prep prompt reasons over.

Tool helpers are imported lazily (optional SDKs), same convention as
performance/roster.py. Degrades to an empty EngineerActivity on missing config.

# See docs: "Daily Standup" — recent-activity collection, sprint context
"""

from __future__ import annotations

import logging
from collections import Counter

from yeaboi.agent.state import EngineerActivity, EngineerStory

logger = logging.getLogger(__name__)


def _collect_items(jira_project: str, azdo_project: str, days: int) -> list[dict]:
    """Fetch recent activity from Jira + AzDO over ``days``; tag each with its source."""
    items: list[dict] = []
    if jira_project:
        try:
            from yeaboi.tools.jira import jira_recent_activity

            for it in jira_recent_activity(jira_project, days=days):
                it = dict(it)
                it["source"] = "jira"
                items.append(it)
        except ImportError:
            logger.warning("Jira SDK not installed — skipping Jira activity")
        except Exception as e:  # noqa: BLE001 — activity is best-effort
            logger.warning("Jira activity failed: %s", e)
    if azdo_project:
        try:
            from yeaboi.tools.azure_devops import azdevops_recent_activity

            for it in azdevops_recent_activity(azdo_project, days=days):
                it = dict(it)
                it["source"] = "azuredevops"
                items.append(it)
        except ImportError:
            logger.warning("Azure DevOps SDK not installed — skipping AzDO activity")
        except Exception as e:  # noqa: BLE001 — activity is best-effort
            logger.warning("Azure DevOps activity failed: %s", e)
    return items


def gather_engineer_activity(
    engineer: str,
    *,
    state: dict | None = None,
    jira_project: str = "",
    azdo_project: str = "",
    sprints: int = 2,
) -> EngineerActivity:
    """Return ``engineer``'s worked tickets across the current + prior sprint.

    The look-back window is ``sprints`` × the plan's sprint length (default 2 weeks
    each). Tickets updated on/after the live sprint start date are tagged
    ``current``; older ones ``previous``. Empty EngineerActivity when no tracker is
    configured or the engineer has no recent activity.

    Args:
        engineer: display name to filter activity by (matches the ``author`` field).
        state: saved session state (for sprint length); may be None.
        jira_project / azdo_project: tracker identifiers (resolved from config if unset).
        sprints: how many sprints back to look.
    """
    state = state or {}
    if not jira_project and not azdo_project:
        from yeaboi.config import get_azure_devops_project, get_jira_project_key

        jira_project = get_jira_project_key() or ""
        azdo_project = get_azure_devops_project() or ""

    # Window = sprints × sprint length (weeks) → days.
    try:
        length_weeks = int(state.get("sprint_length_weeks") or 2)
    except (TypeError, ValueError):
        length_weeks = 2
    days = max(7, sprints * length_weeks * 7)
    logger.info("gather_engineer_activity: engineer=%r window=%dd (sprints=%d)", engineer, days, sprints)

    # Live sprint context supplies the active sprint name + start date used to
    # split current vs previous. Best-effort — reuses the standup helper.
    current_sprint = ""
    start_date = ""
    try:
        from yeaboi.standup import sprint_context

        ctx = sprint_context.gather(state, jira_project=jira_project, azdo_project=azdo_project)
        current_sprint = ctx.sprint_name
        start_date = ctx.start_date
    except Exception as e:  # noqa: BLE001 — sprint context is best-effort
        logger.warning("sprint_context gather failed (non-fatal): %s", e)

    items = _collect_items(jira_project, azdo_project, days)

    # Filter to this engineer's tickets and split by the sprint start date.
    stories: list[EngineerStory] = []
    source_counter: Counter[str] = Counter()
    target = engineer.strip().lower()
    for it in items:
        if (it.get("author") or "").strip().lower() != target:
            continue
        ts = (it.get("timestamp") or "")[:10]
        # A ticket updated on/after the active-sprint start belongs to "current";
        # everything else falls into the prior window. No start date → all current.
        sprint_bucket = "current" if (not start_date or ts >= start_date) else "previous"
        source = it.get("source", "")
        source_counter[source] += 1
        stories.append(
            EngineerStory(
                key=it.get("key", ""),
                title=it.get("title", ""),
                status=it.get("status", ""),
                kind=it.get("kind", ""),
                sprint=sprint_bucket,
                source=source,
            )
        )

    activity = EngineerActivity(
        engineer=engineer,
        current_sprint=current_sprint,
        previous_sprint="",  # tracker APIs don't expose the prior sprint's name cheaply
        stories=tuple(stories),
        total_items=len(stories),
        sources=tuple(sorted(source_counter.items())),
    )
    logger.info("gather_engineer_activity: %d ticket(s) for %s", activity.total_items, engineer)
    return activity
