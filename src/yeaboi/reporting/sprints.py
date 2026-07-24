"""Sprint listing + quarter maths for the Reporting mode's quarter report.

Deterministic (no LLM), same lazy-import + graceful-degradation style as
standup/sprint_context.py. Supplies the sprint multi-select the user uses to
confirm which sprints make up a quarter:

- ``quarter_bounds`` — which calendar quarter a date falls in (Q1 starts January).
- ``list_sprints`` — a real list of sprints with date ranges: live tracker first
  (Jira, then Azure DevOps), falling back to sprints computed from the saved plan.
- ``mark_in_quarter`` — flag the sprints overlapping the quarter (pre-selected in UI).

``SprintRef`` is internal-only (never persisted), so this adds no schema change.

# See docs: "Reporting Mode" — quarter report
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, replace
from datetime import date, timedelta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SprintRef:
    """One sprint with its calendar range, for the quarter multi-select."""

    name: str = ""
    start_date: str = ""  # ISO YYYY-MM-DD (may be empty for undated plan sprints)
    end_date: str = ""  # ISO YYYY-MM-DD
    source: str = ""  # "jira" | "azuredevops" | "plan"
    in_quarter: bool = False  # True when it overlaps the detected quarter (pre-selected)


def quarter_bounds(today: date | None = None) -> tuple[str, str, str]:
    """Return ``(label, start_iso, end_iso)`` for the quarter ``today`` falls in.

    Quarters start in January: Q1=Jan–Mar, Q2=Apr–Jun, Q3=Jul–Sep, Q4=Oct–Dec.
    """
    today = today or date.today()
    q = (today.month - 1) // 3 + 1
    start_month = 3 * (q - 1) + 1
    end_month = start_month + 2
    start = date(today.year, start_month, 1)
    last_day = calendar.monthrange(today.year, end_month)[1]
    end = date(today.year, end_month, last_day)
    return f"Q{q} {today.year}", start.isoformat(), end.isoformat()


def _ranges_overlap(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    """True when [a_start, a_end] overlaps [b_start, b_end] (ISO date strings)."""
    if not a_start or not a_end:
        return False
    return a_start <= b_end and a_end >= b_start


def mark_in_quarter(sprints: list[SprintRef], q_start: str, q_end: str) -> list[SprintRef]:
    """Return ``sprints`` with ``in_quarter`` set where the range overlaps the quarter."""
    return [replace(s, in_quarter=_ranges_overlap(s.start_date, s.end_date, q_start, q_end)) for s in sprints]


def _from_tracker(jira_project: str, azdo_project: str, limit: int) -> list[SprintRef]:
    """Live sprint list from Jira, then Azure DevOps. [] when neither is available."""
    if jira_project:
        try:
            from yeaboi.tools.jira import jira_list_sprints

            rows = jira_list_sprints(jira_project, limit=limit)
            if rows:
                return [
                    SprintRef(name=r["name"], start_date=r["start_date"], end_date=r["end_date"], source="jira")
                    for r in rows
                ]
        except ImportError:
            logger.warning("Jira SDK not installed — skipping sprint list")
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning("jira_list_sprints failed: %s", e)
    if azdo_project:
        try:
            from yeaboi.tools.azure_devops import azdevops_list_sprints

            rows = azdevops_list_sprints(azdo_project, limit=limit)
            if rows:
                return [
                    SprintRef(name=r["name"], start_date=r["start_date"], end_date=r["end_date"], source="azuredevops")
                    for r in rows
                ]
        except ImportError:
            logger.warning("Azure DevOps SDK not installed — skipping sprint list")
        except Exception as e:  # noqa: BLE001 — best-effort
            logger.warning("azdevops_list_sprints failed: %s", e)
    return []


def _from_plan(state: dict, limit: int) -> list[SprintRef]:
    """Compute a sprint list from the saved plan when no tracker is available.

    The plan's Sprint dataclass carries no dates, so derive them from
    ``sprint_start_date`` + ``sprint_length_weeks`` × index (the same maths the
    Jira/AzDO sync uses on export).
    """
    plan_sprints = state.get("sprints") or []
    start_str = state.get("sprint_start_date") or ""
    if not plan_sprints or not start_str:
        return []
    try:
        base = date.fromisoformat(start_str[:10])
    except (TypeError, ValueError):
        return []
    try:
        weeks = int(state.get("sprint_length_weeks") or 2)
    except (TypeError, ValueError):
        weeks = 2
    weeks = max(1, weeks)
    out: list[SprintRef] = []
    for idx, sp in enumerate(plan_sprints):
        name = getattr(sp, "name", None)
        if name is None and isinstance(sp, dict):
            name = sp.get("name")
        start = base + timedelta(weeks=weeks * idx)
        end = start + timedelta(weeks=weeks) - timedelta(days=1)
        out.append(
            SprintRef(
                name=str(name or f"Sprint {idx + 1}"),
                start_date=start.isoformat(),
                end_date=end.isoformat(),
                source="plan",
            )
        )
    return out[-limit:] if limit and len(out) > limit else out


def list_sprints(
    state: dict | None = None,
    *,
    jira_project: str = "",
    azdo_project: str = "",
    limit: int = 12,
) -> list[SprintRef]:
    """Return up to ``limit`` recent sprints (newest last) with date ranges.

    Live tracker first (Jira → Azure DevOps), else derived from the saved plan.
    Empty list when nothing is available — the caller then falls back to reporting
    over the calendar-quarter dates directly.
    """
    state = state or {}
    if not jira_project and not azdo_project:
        from yeaboi.config import get_azure_devops_project, get_jira_project_key

        jira_project = get_jira_project_key() or ""
        azdo_project = get_azure_devops_project() or ""

    sprints = _from_tracker(jira_project, azdo_project, limit)
    if sprints:
        logger.info("list_sprints: %d sprint(s) from tracker", len(sprints))
        return sprints[-limit:] if limit and len(sprints) > limit else sprints

    sprints = _from_plan(state, limit)
    logger.info("list_sprints: %d sprint(s) from plan fallback", len(sprints))
    return sprints
