"""Gather the team's *delivered* (completed) work over a reporting period.

Deterministic (no LLM): reuses the same team-wide recent-activity helpers the Daily
Standup collector uses (``jira_recent_activity`` / ``azdevops_recent_activity``) plus
the standup's ``sprint_context.gather`` for the active-sprint name and length, then
keeps only the tickets whose status means *done*. These DeliveredItem rows are the
concrete evidence the business-report prompt reasons over.

Tool helpers are imported lazily (optional SDKs), same convention as
performance/activity.py. Degrades to an empty list on missing config — the engine
turns that into a warning, never a crash.

# See docs: "Daily Standup" — recent-activity collection, sprint context
"""

from __future__ import annotations

import logging

from yeaboi.agent.state import DeliveredItem

logger = logging.getLogger(__name__)

# Reporting periods the TUI/engine understand.
PERIOD_LAST_SPRINT = "last_sprint"
PERIOD_LAST_MONTH = "last_month"
PERIOD_QUARTER = "quarter"  # label is set per-quarter at runtime (e.g. "Q3 2026")

PERIOD_LABELS = {
    PERIOD_LAST_SPRINT: "Last sprint",
    PERIOD_LAST_MONTH: "Last month (~2 sprints)",
    PERIOD_QUARTER: "Whole quarter",
}

# Statuses that mean a ticket actually shipped. Compared case-insensitively; the
# tracker's raw status label is preserved on the DeliveredItem for display.
_COMPLETED_STATUSES = frozenset(
    {"done", "closed", "resolved", "released", "completed", "shipped", "accepted", "deployed"}
)


def _is_completed(status: str) -> bool:
    """Return True when a tracker status label means the work is delivered."""
    return (status or "").strip().lower() in _COMPLETED_STATUSES


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


def period_days(period: str, *, sprint_length_weeks: int = 2) -> int:
    """Return the look-back window in days for a reporting ``period``.

    "Last sprint" = one sprint length; "Last month" = ~2 sprints (min 28 days) so a
    one-week-sprint team still gets a sensible month-ish window.
    """
    try:
        weeks = int(sprint_length_weeks or 2)
    except (TypeError, ValueError):
        weeks = 2
    weeks = max(1, weeks)
    if period == PERIOD_LAST_MONTH:
        return max(28, 2 * weeks * 7)
    return max(7, weeks * 7)


def gather_delivered_work(
    period: str,
    *,
    state: dict | None = None,
    jira_project: str = "",
    azdo_project: str = "",
    days_override: int | None = None,
) -> tuple[list[DeliveredItem], list[str], list[str]]:
    """Return the team's completed tickets over ``period``.

    Args:
        period: PERIOD_LAST_SPRINT / PERIOD_LAST_MONTH / PERIOD_QUARTER.
        state: saved session state (for sprint length); may be None.
        jira_project / azdo_project: tracker identifiers (resolved from config if unset).
        days_override: when set (quarter report), use this exact look-back window in
            days instead of deriving it from ``period``, and skip the active-sprint
            probe (the caller already knows the sprint names from the selection).

    Returns:
        ``(items, sprint_names, warnings)`` — the completed DeliveredItems, the
        active sprint name(s) seen (best-effort; empty when ``days_override`` is set),
        and any warnings (e.g. no tracker configured) to surface on the report.
    """
    state = state or {}
    warnings: list[str] = []
    if not jira_project and not azdo_project:
        from yeaboi.config import get_azure_devops_project, get_jira_project_key

        jira_project = get_jira_project_key() or ""
        azdo_project = get_azure_devops_project() or ""

    if not jira_project and not azdo_project:
        logger.info("gather_delivered_work: no tracker configured")
        return [], [], ["No Jira or Azure DevOps board configured — connect one in Settings to report delivered work."]

    sprint_names: list[str] = []
    if days_override is not None:
        days = max(1, days_override)
        logger.info("gather_delivered_work: period=%s window=%dd (explicit)", period, days)
    else:
        try:
            length_weeks = int(state.get("sprint_length_weeks") or 2)
        except (TypeError, ValueError):
            length_weeks = 2
        days = period_days(period, sprint_length_weeks=length_weeks)
        logger.info("gather_delivered_work: period=%s window=%dd", period, days)

        # Best-effort active-sprint name(s) for framing (reuses the standup helper).
        try:
            from yeaboi.standup import sprint_context

            ctx = sprint_context.gather(state, jira_project=jira_project, azdo_project=azdo_project)
            if ctx.sprint_name:
                sprint_names.append(ctx.sprint_name)
        except Exception as e:  # noqa: BLE001 — sprint context is best-effort
            logger.warning("sprint_context gather failed (non-fatal): %s", e)

    raw = _collect_items(jira_project, azdo_project, days)
    items = [
        DeliveredItem(
            key=it.get("key", ""),
            title=it.get("title", ""),
            status=it.get("status", ""),
            source=it.get("source", ""),
            assignee=(it.get("author", "") or "").strip(),
        )
        for it in raw
        if _is_completed(it.get("status", ""))
    ]
    if raw and not items:
        warnings.append("Recent activity was found, but nothing is marked Done/Closed in this window yet.")
    logger.info("gather_delivered_work: %d delivered item(s) of %d touched", len(items), len(raw))
    return items, sprint_names, warnings
