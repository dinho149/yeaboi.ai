"""Gather the sprint context a standup needs: dates, length, and burn-down points.

Combines the saved plan (session state) with a best-effort *live* read of the
active sprint from Jira or Azure DevOps. The live read supplies the start date
and the committed/completed story points that drive burn-down confidence; when no
tracker is connected we fall back to the plan's capacity with no burn data, and
confidence degrades to "sprint day only / insufficient data".

Tool helpers are imported lazily (optional SDKs), same convention as collector.py.

# See docs: "Daily Standup" — sprint-day & confidence
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SprintContext:
    sprint_name: str = ""
    start_date: str = ""  # ISO YYYY-MM-DD, empty when unknown
    sprint_length_weeks: int = 2
    capacity_points: float = 0.0  # committed points for the active sprint
    completed_points: float = 0.0
    have_burn: bool = False  # True only when live committed points were found


def _active_sprint_capacity_from_state(state: dict) -> float:
    """Return the first (active) sprint's committed capacity from the saved plan, or 0."""
    sprints = state.get("sprints") or []
    if not sprints:
        return 0.0
    first = sprints[0]
    # Sprint may be a dataclass or a plain dict depending on load path.
    cap = getattr(first, "capacity_points", None)
    if cap is None and isinstance(first, dict):
        cap = first.get("capacity_points")
    try:
        return float(cap) if cap is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _live_progress(jira_project: str, azdo_project: str) -> dict:
    """Best-effort live active-sprint progress from Jira, then Azure DevOps. {} if none."""
    if jira_project:
        try:
            from yeaboi.tools.jira import jira_active_sprint_progress

            prog = jira_active_sprint_progress(jira_project)
            if prog:
                return prog
        except ImportError:
            logger.warning("Jira SDK not installed — skipping live sprint progress")
        except Exception as e:
            logger.warning("Jira live sprint progress failed: %s", e)
    if azdo_project:
        try:
            from yeaboi.tools.azure_devops import azdevops_active_sprint_progress

            prog = azdevops_active_sprint_progress(azdo_project)
            if prog:
                return prog
        except ImportError:
            logger.warning("Azure DevOps SDK not installed — skipping live sprint progress")
        except Exception as e:
            logger.warning("Azure DevOps live sprint progress failed: %s", e)
    return {}


def gather(state: dict, *, jira_project: str = "", azdo_project: str = "") -> SprintContext:
    """Assemble a SprintContext from saved plan state + live tracker progress."""
    state = state or {}
    length = state.get("sprint_length_weeks") or 2
    try:
        length = int(length)
    except (TypeError, ValueError):
        length = 2

    ctx = SprintContext(sprint_length_weeks=length, capacity_points=_active_sprint_capacity_from_state(state))

    prog = _live_progress(jira_project, azdo_project)
    if prog:
        ctx.sprint_name = prog.get("sprint_name", ctx.sprint_name)
        ctx.start_date = prog.get("start_date", ctx.start_date)
        committed = prog.get("committed_points")
        if committed:  # a live committed total means we can do real burn-down
            ctx.capacity_points = float(committed)
            ctx.completed_points = float(prog.get("completed_points", 0.0))
            ctx.have_burn = True
    logger.info(
        "sprint_context: name=%r start=%r len=%dw capacity=%.1f completed=%.1f burn=%s",
        ctx.sprint_name,
        ctx.start_date,
        ctx.sprint_length_weeks,
        ctx.capacity_points,
        ctx.completed_points,
        ctx.have_burn,
    )
    return ctx
