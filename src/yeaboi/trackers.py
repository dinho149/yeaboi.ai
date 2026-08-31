"""The issue-tracker registry: every tracker a sprint plan can sync to.

Before this existed, "which tracker?" was a two-way if/else hand-rolled at a
dozen call sites — intake velocity, the active-sprint probe, the small-mode
sprint targets, the epic-review sync buttons, the project-card sync, and the
MCP ``plan_sync`` destination check. Four trackers make that combinatorial;
one registry makes it a lookup.

Each :class:`TrackerSpec` is a bundle of callables so imports stay lazy — the
heavy SDK clients (`jira`, the AzDO REST wrappers) must not load until a
tracker is actually used. Everything here returns plain data in the shapes the
intake node has always consumed; the velocity dict deliberately keeps its
``jira_team_size`` key, because that key is the wire every caller reads.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TrackerSpec:
    """One tracker, as every dispatch site sees it.

    ``sync_all`` returns the sync *function* (``(state, on_progress) ->
    (result, updated_state)``) rather than being it, so the sync module loads
    only when a sync actually runs. ``result_summary`` flattens that module's
    own result dataclass into the keys the MCP wire and the TUI report.
    """

    key: str
    label: str
    is_configured: Callable[[], bool]
    fetch_velocity: Callable[[], dict | None]
    fetch_active_sprint: Callable[[], tuple[int | None, str | None, str]]
    fetch_sprint_targets: Callable[[], tuple[list[dict], str]]
    sync_all: Callable[[], Callable]
    result_summary: Callable[[object], dict]


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


def _jira_configured() -> bool:
    from yeaboi.config import get_jira_base_url, get_jira_email, get_jira_project_key, get_jira_token

    return bool(get_jira_base_url() and get_jira_email() and get_jira_token() and get_jira_project_key())


def _jira_velocity() -> dict | None:
    """Avg velocity AND team size from the last 3 closed sprints in Jira.

    # See docs: "Scrum Standards" — capacity planning
    #
    # Thin wrapper around the jira_fetch_velocity @tool in tools/jira.py: the
    # tool owns the connection logic and returns a JSON string; this parses it
    # back to the dict the intake node consumes.
    """
    try:
        from yeaboi.tools.jira import jira_fetch_velocity

        result = jira_fetch_velocity.invoke({})
        if result.startswith("Error"):
            logger.debug("jira_fetch_velocity returned: %s", result)
            return None
        data = json.loads(result)
        if "velocity_error" in data:
            logger.debug("jira_fetch_velocity: zero velocity but team_size=%s", data.get("jira_team_size"))
        return data
    except Exception:
        logger.debug("Failed to fetch Jira velocity", exc_info=True)
    return None


def _jira_active_sprint() -> tuple[int | None, str | None, str]:
    try:
        from yeaboi.tools.jira import jira_fetch_active_sprint

        result = jira_fetch_active_sprint.invoke({})
        if result.startswith("Error"):
            return None, None, result.removeprefix("Error: ")
        data = json.loads(result)
        return data["sprint_number"], data.get("start_date"), f"Active sprint: {data['sprint_name']}"
    except Exception as exc:
        logger.debug("Failed to fetch Jira sprints for sprint selection", exc_info=True)
        return None, None, f"Jira connection failed: {exc}"


def _jira_sprint_targets() -> tuple[list[dict], str]:
    try:
        from yeaboi.config import get_jira_project_key
        from yeaboi.tools.jira import _make_jira_client, fetch_board_sprints, find_scrum_board_id

        jira = _make_jira_client()
        if jira is None:
            return [], "Jira not configured"
        key = get_jira_project_key() or ""
        if not key:
            return [], "JIRA_PROJECT_KEY not set"
        board_id = find_scrum_board_id(jira, key)
        if board_id is None:
            return [], f"No Jira board found for project {key}"
        targets = []
        for item in fetch_board_sprints(jira, board_id, states=("active", "future")):
            num_match = re.search(r"(\d+)\s*$", item["name"])
            targets.append(
                {
                    "name": item["name"],
                    "external_id": str(item["id"] or ""),
                    "state": item["state"],
                    "start_date": item["start_date"],
                    "number": int(num_match.group(1)) if num_match else None,
                }
            )
        return targets, f"{len(targets)} open sprint(s) on the board"
    except Exception as exc:
        logger.debug("Failed to fetch Jira sprint targets", exc_info=True)
        return [], f"Jira connection failed: {exc}"


def _jira_sync() -> Callable:
    from yeaboi.jira_sync import sync_all_to_jira

    return sync_all_to_jira


def _jira_summary(result) -> dict:
    return {
        "epic": result.epic_key,
        "sprints_created": dict(result.sprints_created),
        "sprints_updated": dict(result.sprints_updated),
    }


# ---------------------------------------------------------------------------
# Azure DevOps Boards
# ---------------------------------------------------------------------------


def _azdevops_configured() -> bool:
    from yeaboi.azdevops_sync import is_azdevops_board_configured

    return is_azdevops_board_configured()


def _azdevops_velocity() -> dict | None:
    """Velocity from Azure DevOps iterations, parsed out of the tool's text.

    Keeps the ``jira_team_size`` key on purpose — it is the wire every velocity
    consumer reads, whatever the tracker.
    """
    try:
        from yeaboi.tools.azure_devops import azdevops_fetch_velocity

        result = azdevops_fetch_velocity.invoke({})
        if result.startswith("Error") or result.startswith("No completed"):
            logger.debug("azdevops_fetch_velocity returned: %s", result)
            return None
        vel_match = re.search(r"Team velocity:\s*([\d.]+)", result)
        size_match = re.search(r"Team size:\s*(\d+)", result)
        per_dev_match = re.search(r"Per-developer velocity:\s*([\d.]+)", result)
        if vel_match and size_match and per_dev_match:
            return {
                "team_velocity": float(vel_match.group(1)),
                "jira_team_size": int(size_match.group(1)),  # reuse key for compat
                "per_dev_velocity": float(per_dev_match.group(1)),
            }
        return None
    except Exception:
        logger.debug("Failed to fetch Azure DevOps velocity", exc_info=True)
    return None


def _azdevops_active_sprint() -> tuple[int | None, str | None, str]:
    try:
        from yeaboi.tools.azure_devops import azdevops_fetch_active_iteration

        result = azdevops_fetch_active_iteration.invoke({})
        if result.startswith("Error") or result.startswith("No active"):
            return None, None, result.removeprefix("Error: ")
        num_match = re.search(r"Sprint number:\s*(\d+)", result)
        name_match = re.search(r"Sprint name:\s*(.+)", result)
        date_match = re.search(r"Start date:\s*(\S+)", result)
        sprint_num = int(num_match.group(1)) if num_match else None
        sprint_name = name_match.group(1).strip() if name_match else "Unknown"
        start_date = date_match.group(1) if date_match and date_match.group(1) else None
        if sprint_num:
            return sprint_num, start_date, f"Active iteration: {sprint_name}"
        return None, None, "Could not determine active iteration number"
    except Exception as exc:
        logger.debug("Failed to fetch Azure DevOps iteration for sprint selection", exc_info=True)
        return None, None, f"Azure DevOps connection failed: {exc}"


def _azdevops_sprint_targets() -> tuple[list[dict], str]:
    try:
        from yeaboi.tools.azure_devops import fetch_team_iterations_meta

        targets = []
        for it in fetch_team_iterations_meta():
            if it["time_frame"] == "past":
                continue
            num_match = re.search(r"(\d+)\s*$", it["name"])
            targets.append(
                {
                    "name": it["name"],
                    "external_id": it["path"],
                    "state": "active" if it["time_frame"] == "current" else "future",
                    "start_date": it["start_date"],
                    "number": int(num_match.group(1)) if num_match else None,
                }
            )
        # Active first, then future — same ordering the Jira path gets from its
        # states tuple.
        targets.sort(key=lambda t: (t["state"] != "active", t["start_date"] or "9999"))
        return targets, f"{len(targets)} open iteration(s) for the team"
    except Exception as exc:
        logger.debug("Failed to fetch AzDO iteration targets", exc_info=True)
        return [], f"Azure DevOps connection failed: {exc}"


def _azdevops_sync() -> Callable:
    from yeaboi.azdevops_sync import sync_all_to_azdevops

    return sync_all_to_azdevops


def _azdevops_summary(result) -> dict:
    return {
        "epic": result.epic_id,
        "sprints_created": dict(result.iterations_created),
        "sprints_updated": dict(result.iterations_updated),
    }


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------


def _linear_configured() -> bool:
    from yeaboi.linear_sync import is_linear_configured

    return is_linear_configured()


def _linear_velocity() -> dict | None:
    """Velocity from the last 3 completed cycles — same wire keys as Jira's."""
    try:
        from yeaboi.tools.linear import linear_fetch_velocity

        result = linear_fetch_velocity.invoke({})
        if result.startswith("Error"):
            logger.debug("linear_fetch_velocity returned: %s", result)
            return None
        return json.loads(result)
    except Exception:
        logger.debug("Failed to fetch Linear velocity", exc_info=True)
    return None


def _linear_active_sprint() -> tuple[int | None, str | None, str]:
    try:
        from yeaboi.tools.linear import linear_fetch_active_sprint

        result = linear_fetch_active_sprint.invoke({})
        if result.startswith("Error"):
            return None, None, result.removeprefix("Error: ")
        data = json.loads(result)
        return data["sprint_number"], data.get("start_date"), f"Active cycle: {data['sprint_name']}"
    except Exception as exc:
        logger.debug("Failed to fetch Linear cycle for sprint selection", exc_info=True)
        return None, None, f"Linear connection failed: {exc}"


def _linear_sprint_targets() -> tuple[list[dict], str]:
    try:
        from yeaboi.tools.linear import fetch_team_cycles

        targets = []
        for cycle in fetch_team_cycles(states=("active", "future")):
            targets.append(
                {
                    "name": cycle["name"],
                    "external_id": cycle["id"],
                    "state": cycle["state"],
                    "start_date": cycle["start_date"],
                    "number": cycle.get("number"),
                }
            )
        return targets, f"{len(targets)} open cycle(s) for the team"
    except Exception as exc:
        logger.debug("Failed to fetch Linear cycle targets", exc_info=True)
        return [], f"Linear connection failed: {exc}"


def _linear_sync() -> Callable:
    from yeaboi.linear_sync import sync_all_to_linear

    return sync_all_to_linear


def _linear_summary(result) -> dict:
    return {
        "epic": result.project_id,
        "sprints_created": dict(result.cycles_created),
        "sprints_updated": dict(result.cycles_updated),
    }


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

#: Every tracker, in offer order — the order the choice prompt lists them and
#: auto-detection tries them, which is why Jira stays first. The lambdas are
#: late bindings on purpose: a test monkeypatching ``_jira_configured`` must be
#: seen by a spec built before the patch.
TRACKERS: dict[str, TrackerSpec] = {
    "jira": TrackerSpec(
        key="jira",
        label="Jira",
        is_configured=lambda: _jira_configured(),
        fetch_velocity=lambda: _jira_velocity(),
        fetch_active_sprint=lambda: _jira_active_sprint(),
        fetch_sprint_targets=lambda: _jira_sprint_targets(),
        sync_all=lambda: _jira_sync(),
        result_summary=lambda result: _jira_summary(result),
    ),
    "azdevops": TrackerSpec(
        key="azdevops",
        label="Azure DevOps",
        is_configured=lambda: _azdevops_configured(),
        fetch_velocity=lambda: _azdevops_velocity(),
        fetch_active_sprint=lambda: _azdevops_active_sprint(),
        fetch_sprint_targets=lambda: _azdevops_sprint_targets(),
        sync_all=lambda: _azdevops_sync(),
        result_summary=lambda result: _azdevops_summary(result),
    ),
    "linear": TrackerSpec(
        key="linear",
        label="Linear",
        is_configured=lambda: _linear_configured(),
        fetch_velocity=lambda: _linear_velocity(),
        fetch_active_sprint=lambda: _linear_active_sprint(),
        fetch_sprint_targets=lambda: _linear_sprint_targets(),
        sync_all=lambda: _linear_sync(),
        result_summary=lambda result: _linear_summary(result),
    ),
}


def by_key(key: str) -> TrackerSpec | None:
    return TRACKERS.get(key)


def configured() -> list[str]:
    """The keys of every configured tracker, in offer order."""
    return [key for key, spec in TRACKERS.items() if spec.is_configured()]


def pick(preferred: str = "") -> TrackerSpec | None:
    """The tracker to use: the preferred one when configured, else the first."""
    if preferred:
        spec = TRACKERS.get(preferred)
        if spec is not None and spec.is_configured():
            return spec
        return None
    return next((TRACKERS[key] for key in configured()), None)


def label_for(preferred: str = "") -> str:
    """The label of the tracker :func:`pick` would use, or ``""``."""
    spec = pick(preferred)
    return spec.label if spec else ""


def resolve_choice(text: str, options: list[str]) -> str:
    """Map a typed tracker choice onto a key from ``options``.

    Accepts a 1-based index into the offered list, a key, or a label (case
    blind); anything else falls back to the first option — the same forgiving
    default the two-way prompt always had.
    """
    cleaned = (text or "").strip().lower()
    if cleaned.isdigit() and 1 <= int(cleaned) <= len(options):
        return options[int(cleaned) - 1]
    for key in options:
        spec = TRACKERS[key]
        if cleaned in (key, spec.label.lower()):
            return key
    # Loose aliases: "azure devops" and bare "azure" both mean the boards.
    if cleaned.startswith("azure") and "azdevops" in options:
        return "azdevops"
    return options[0]


def fetch_velocity(preferred: str = "") -> dict | None:
    """Velocity from the preferred tracker, else the first configured one."""
    spec = pick(preferred)
    return spec.fetch_velocity() if spec else None


def fetch_active_sprint(preferred: str = "") -> tuple[int | None, str | None, str]:
    spec = pick(preferred)
    if spec is None:
        return None, None, "No tracker configured"
    return spec.fetch_active_sprint()


def fetch_sprint_targets(preferred: str = "") -> tuple[list[dict], str]:
    spec = pick(preferred)
    if spec is None:
        return [], "No tracker configured"
    return spec.fetch_sprint_targets()
