"""Linear integration tools — the GraphQL tracker.

# See docs: "Tools" — tool types, risk levels
#
# Linear is GraphQL-only: one endpoint, every read and write a query or a
# mutation against it. The personal API key rides the ``Authorization`` header
# bare (no ``Bearer``). Semantic mapping, matching Linear's own Jira import:
#   Epic   → Project
#   Story  → Issue (``estimate`` carries the points)
#   Task   → sub-issue (``parentId``)
#   Sprint → Cycle (the team must have cycles enabled)
"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from yeaboi.config import get_linear_api_key, get_linear_team_key

logger = logging.getLogger(__name__)

API_URL = "https://api.linear.app/graphql"

_MISSING_CONFIG_MSG = (
    "Error: Linear is not configured. Add LINEAR_API_KEY (and optionally LINEAR_TEAM_KEY) "
    "via Settings ▸ Connections or `yeaboi connections add linear`."
)

_TIMEOUT = 15

#: One page of anything. A board summary that needed more is already a signal.
_PAGE_SIZE = 50

#: Internal priority words → Linear's priority integers (1 urgent … 4 low).
_PRIORITY_TO_LINEAR: dict[str, int] = {
    "critical": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
}


class LinearError(RuntimeError):
    """A Linear call that failed, with a user-facing message."""


def _linear_error_msg(status: int, errors: list | None = None) -> str:
    """One user-facing line per failure class — mirrors ``_jira_error_msg``."""
    if status == 400 and errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        return f"Error: Linear rejected the request — {first.get('message', 'invalid query')}"
    if status in (400, 401, 403):
        return "Error: Linear rejected the API key — check LINEAR_API_KEY."
    if status == 429:
        return "Error: Linear rate limit hit — try again in a minute."
    return f"Error: Unexpected Linear response ({status})."


def _linear_request(query: str, variables: dict | None = None) -> dict:
    """POST one GraphQL document and return its ``data``, or raise LinearError.

    A 200 with an ``errors`` array is still a failure — GraphQL's way of saying
    no — and is raised with the first error's message.
    """
    api_key = get_linear_api_key()
    if not api_key:
        raise LinearError(_MISSING_CONFIG_MSG)
    import httpx

    resp = httpx.post(
        API_URL,
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=_TIMEOUT,
    )
    body = {}
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        pass
    errors = body.get("errors") if isinstance(body, dict) else None
    if resp.status_code != 200 or errors:
        raise LinearError(_linear_error_msg(resp.status_code, errors))
    data = body.get("data")
    if not isinstance(data, dict):
        raise LinearError("Error: Linear returned an empty response.")
    return data


def _resolve_team(team_key: str = "") -> dict:
    """The team to work in: the argument, else LINEAR_TEAM_KEY, else the sole team.

    Raises LinearError with the fix when the key names nothing or the
    workspace has several teams and no key chooses one.
    """
    data = _linear_request("query { teams(first: 50) { nodes { id key name } } }")
    teams = [t for t in data.get("teams", {}).get("nodes", []) if isinstance(t, dict)]
    if not teams:
        raise LinearError("Error: The Linear API key can see no teams.")
    wanted = team_key.strip() or (get_linear_team_key() or "").strip()
    if wanted:
        team = next((t for t in teams if str(t.get("key", "")).lower() == wanted.lower()), None)
        if team is None:
            known = ", ".join(str(t.get("key", "")) for t in teams)
            raise LinearError(f"Error: No Linear team with key '{wanted}'. Teams: {known}")
        return team
    if len(teams) == 1:
        return teams[0]
    known = ", ".join(str(t.get("key", "")) for t in teams)
    raise LinearError(f"Error: Several Linear teams ({known}) — set LINEAR_TEAM_KEY to choose one.")


# ---------------------------------------------------------------------------
# Non-@tool helpers used by linear_sync.py (not exposed to the agent)
# ---------------------------------------------------------------------------


def fetch_team_cycles(states: tuple[str, ...] = ("active", "future")) -> list[dict]:
    """The team's cycles as ``{id, name, number, state, start_date, end_date}``.

    ``state`` is derived from the cycle's own timestamps the way the sync
    modules expect: active / future / closed.
    """
    team = _resolve_team()
    data = _linear_request(
        """
        query($teamId: String!) {
          team(id: $teamId) {
            cycles(first: 50) {
              nodes { id name number startsAt endsAt completedAt progress }
            }
          }
        }
        """,
        {"teamId": team["id"]},
    )
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    cycles = []
    for node in data.get("team", {}).get("cycles", {}).get("nodes", []):
        starts, ends = str(node.get("startsAt") or ""), str(node.get("endsAt") or "")
        if node.get("completedAt") or (ends and ends < now):
            state = "closed"
        elif starts and starts <= now:
            state = "active"
        else:
            state = "future"
        if state not in states:
            continue
        cycles.append(
            {
                "id": str(node.get("id") or ""),
                "name": str(node.get("name") or f"Cycle {node.get('number')}"),
                "number": node.get("number"),
                "state": state,
                "start_date": starts[:10],
                "end_date": ends[:10],
            }
        )
    cycles.sort(key=lambda c: (c["state"] != "active", c["start_date"] or "9999"))
    return cycles


def create_sub_issue(parent_id: str, title: str, description: str = "") -> dict:
    """Create one sub-issue under a parent issue. Returns {id, identifier, url}."""
    team = _resolve_team()
    data = _linear_request(
        """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) { success issue { id identifier url } }
        }
        """,
        {"input": {"teamId": team["id"], "title": title, "description": description, "parentId": parent_id}},
    )
    payload = data.get("issueCreate", {})
    if not payload.get("success"):
        raise LinearError("Error: Linear did not create the sub-issue.")
    return payload["issue"]


def add_issues_to_cycle(cycle_id: str, issue_ids: list[str]) -> None:
    """Move issues into a cycle, one mutation per issue."""
    for issue_id in issue_ids:
        data = _linear_request(
            "mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) { success } }",
            {"id": issue_id, "input": {"cycleId": cycle_id}},
        )
        if not data.get("issueUpdate", {}).get("success"):
            raise LinearError("Error: Linear did not move an issue into the cycle.")


def _label_ids(team_id: str, names: list[str]) -> list[str]:
    """Resolve label names to ids, creating any that do not exist yet."""
    if not names:
        return []
    data = _linear_request(
        "query($teamId: ID) { issueLabels(first: 100, filter: { team: { id: { eq: $teamId } } })"
        " { nodes { id name } } }",
        {"teamId": team_id},
    )
    existing = {
        str(n.get("name", "")).lower(): str(n.get("id", ""))
        for n in data.get("issueLabels", {}).get("nodes", [])
        if isinstance(n, dict)
    }
    ids = []
    for name in names:
        found = existing.get(name.lower())
        if not found:
            created = _linear_request(
                "mutation($input: IssueLabelCreateInput!) {"
                " issueLabelCreate(input: $input) { success issueLabel { id } } }",
                {"input": {"teamId": team_id, "name": name}},
            )
            payload = created.get("issueLabelCreate", {})
            if not payload.get("success"):
                continue  # a label is decoration — its failure must not sink the story
            found = str(payload.get("issueLabel", {}).get("id", ""))
        if found:
            ids.append(found)
    return ids


# ---------------------------------------------------------------------------
# @tool functions (exposed to the agent)
# ---------------------------------------------------------------------------


@tool
def linear_read_board(team_key: str = "") -> str:
    """Read the current state of a Linear team: active cycle, backlog size, and velocity.

    Falls back to LINEAR_TEAM_KEY (or the workspace's sole team) when team_key
    is not provided. Returns a formatted summary with team name, active cycle,
    backlog count, and average velocity from the last 3 completed cycles.
    """
    # See docs: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("linear_read_board called with team_key=%r", team_key)
    if not get_linear_api_key():
        return _MISSING_CONFIG_MSG
    try:
        team = _resolve_team(team_key)
        data = _linear_request(
            """
            query($teamId: String!, $page: Int!) {
              team(id: $teamId) {
                name
                activeCycle { name number startsAt endsAt }
                backlog: issues(
                  first: $page
                  filter: { cycle: { null: true }, state: { type: { nin: ["completed", "canceled"] } } }
                ) { nodes { id } }
              }
            }
            """,
            {"teamId": team["id"], "page": _PAGE_SIZE},
        )
        node = data.get("team", {}) or {}
        lines = [f"Team: {node.get('name', team['name'])} ({team['key']})", ""]
        cycle = node.get("activeCycle")
        if isinstance(cycle, dict):
            cycle_name = cycle.get("name") or f"Cycle {cycle.get('number')}"
            lines.append(f"Active cycle: {cycle_name}")
            if cycle.get("startsAt"):
                lines.append(f"  Start: {str(cycle['startsAt'])[:10]}")
            if cycle.get("endsAt"):
                lines.append(f"  End:   {str(cycle['endsAt'])[:10]}")
        else:
            lines.append("Active cycle: None")
        backlog = node.get("backlog", {}).get("nodes", [])
        suffix = "+" if len(backlog) >= _PAGE_SIZE else ""
        lines.append(f"Backlog issues: {len(backlog)}{suffix}")

        # Velocity is a separate, more expensive query — one computation,
        # shared with the fetcher.
        raw = _velocity_summary(team)
        if raw is None:
            lines.append("Avg velocity: no completed cycles found")
        else:
            lines.append(f"Avg velocity (last {raw['cycles']} cycles): {raw['team_velocity']:.1f} pts")
        return "\n".join(lines)
    except LinearError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in linear_read_board: %s", e)
        return f"Error: {e}"


def _velocity_summary(team: dict) -> dict | None:
    """Average completed points over the last 3 completed cycles + team size."""
    data = _linear_request(
        """
        query($teamId: String!) {
          team(id: $teamId) {
            members(first: 50) { nodes { id } }
            cycles(first: 50) { nodes { number completedAt completedScopeHistory } }
          }
        }
        """,
        {"teamId": team["id"]},
    )
    node = data.get("team", {}) or {}
    members = node.get("members", {}).get("nodes", [])
    completed = [c for c in node.get("cycles", {}).get("nodes", []) if isinstance(c, dict) and c.get("completedAt")]
    completed.sort(key=lambda c: str(c.get("completedAt")))
    sample = completed[-3:]
    if not sample:
        return None
    totals = []
    for cycle in sample:
        history = cycle.get("completedScopeHistory") or []
        try:
            totals.append(float(history[-1]) if history else 0.0)
        except (TypeError, ValueError):
            totals.append(0.0)
    return {
        "team_velocity": sum(totals) / len(totals),
        "team_size": max(1, len(members)),
        "cycles": len(sample),
    }


@tool
def linear_fetch_velocity(team_key: str = "") -> str:
    """Fetch average team velocity and team size from the last 3 completed cycles.

    Returns a JSON string with keys: team_velocity, jira_team_size, per_dev_velocity.
    (The `jira_team_size` key name is the wire every velocity consumer reads,
    whatever the tracker.) Returns an error string starting with "Error:" on failure.

    # See docs: "Scrum Standards" — capacity planning
    """
    logger.debug("linear_fetch_velocity called with team_key=%r", team_key)
    if not get_linear_api_key():
        return _MISSING_CONFIG_MSG
    try:
        team = _resolve_team(team_key)
        summary = _velocity_summary(team)
        if summary is None:
            return "Error: No completed cycles found — velocity cannot be computed yet."
        velocity, size = summary["team_velocity"], summary["team_size"]
        payload = {
            "team_velocity": round(velocity, 1),
            "jira_team_size": size,
            "per_dev_velocity": round(velocity / size, 1),
        }
        if velocity == 0:
            payload["velocity_error"] = "Completed cycles carry no points"
        return json.dumps(payload)
    except LinearError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in linear_fetch_velocity: %s", e)
        return f"Error: {e}"


@tool
def linear_fetch_active_sprint(team_key: str = "") -> str:
    """Fetch the team's currently active cycle.

    Returns a JSON string with keys: sprint_number, sprint_name, start_date.
    Returns an error string starting with "Error:" when no cycle is active or
    cycles are disabled for the team.
    """
    logger.debug("linear_fetch_active_sprint called with team_key=%r", team_key)
    if not get_linear_api_key():
        return _MISSING_CONFIG_MSG
    try:
        team = _resolve_team(team_key)
        data = _linear_request(
            "query($teamId: String!) { team(id: $teamId) { activeCycle { name number startsAt } } }",
            {"teamId": team["id"]},
        )
        cycle = data.get("team", {}).get("activeCycle")
        if not isinstance(cycle, dict) or cycle.get("number") is None:
            return "Error: No active cycle — enable cycles for the team in Linear, or start one."
        return json.dumps(
            {
                "sprint_number": int(cycle["number"]),
                "sprint_name": str(cycle.get("name") or f"Cycle {cycle['number']}"),
                "start_date": str(cycle.get("startsAt") or "")[:10] or None,
            }
        )
    except LinearError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in linear_fetch_active_sprint: %s", e)
        return f"Error: {e}"


@tool
def linear_create_epic(title: str, description: str = "", internal_id: str = "") -> str:
    """Create a single project-level container in Linear (a Project).

    Each yeaboi project gets one Linear Project that all stories link to —
    this is NOT called per-feature. Only call this after the user has
    explicitly confirmed they want to create items in Linear.
    Pass internal_id (e.g. 'epic-1') to get a 'Mapping:' line for tracking.
    Returns the new project's id and URL on success.
    """
    logger.debug("linear_create_epic called: title=%r", title)
    if not get_linear_api_key():
        return _MISSING_CONFIG_MSG
    try:
        team = _resolve_team()
        data = _linear_request(
            """
            mutation($input: ProjectCreateInput!) {
              projectCreate(input: $input) { success project { id name url } }
            }
            """,
            {"input": {"name": title, "description": description, "teamIds": [team["id"]]}},
        )
        payload = data.get("projectCreate", {})
        if not payload.get("success"):
            return "Error: Linear did not create the project."
        project = payload["project"]
        lines = [f"Created Project: {project['id']} — {title}", f"URL: {project.get('url', '')}"]
        if internal_id:
            lines.append(f"Mapping: {internal_id} → {project['id']}")
        return "\n".join(lines)
    except LinearError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in linear_create_epic: %s", e)
        return f"Error: {e}"


@tool
def linear_create_story(
    title: str,
    project_id: str,
    story_points: int = 0,
    priority: str = "medium",
    description: str = "",
    internal_id: str = "",
    labels: list[str] | None = None,
) -> str:
    """Create an issue in Linear linked to a project.

    Only call this after the user has explicitly confirmed they want to create
    items in Linear. story_points maps to Linear's `estimate`; priority is one
    of critical/high/medium/low (mapped to Linear's 1–4). labels are created on
    the team when missing. Pass internal_id (e.g. 'story-3') to get a
    'Mapping:' line. Returns the new issue's identifier and URL on success.
    """
    logger.debug("linear_create_story called: title=%r, project=%s", title, project_id)
    if not get_linear_api_key():
        return _MISSING_CONFIG_MSG
    try:
        team = _resolve_team()
        input_payload: dict = {
            "teamId": team["id"],
            "title": title,
            "description": description,
            "projectId": project_id,
            "priority": _PRIORITY_TO_LINEAR.get(priority.strip().lower(), 3),
        }
        if story_points:
            input_payload["estimate"] = int(story_points)
        label_ids = _label_ids(team["id"], labels or [])
        if label_ids:
            input_payload["labelIds"] = label_ids
        data = _linear_request(
            """
            mutation($input: IssueCreateInput!) {
              issueCreate(input: $input) { success issue { id identifier url } }
            }
            """,
            {"input": input_payload},
        )
        payload = data.get("issueCreate", {})
        if not payload.get("success"):
            return "Error: Linear did not create the issue."
        issue = payload["issue"]
        lines = [f"Created Issue: {issue['identifier']}", f"URL: {issue.get('url', '')}"]
        if labels:
            lines.append(f"Labels: {', '.join(labels)}")
        if internal_id:
            lines.append(f"Mapping: {internal_id} → {issue['identifier']}")
        return "\n".join(lines)
    except LinearError as e:
        return str(e)
    except Exception as e:
        logger.error("Unexpected error in linear_create_story: %s", e)
        return f"Error: {e}"


@tool
def linear_create_sprint(sprint_name: str, start_date: str, end_date: str, goal: str = "") -> str:
    """Create a new cycle for the team in Linear.

    Only call this after the user has explicitly confirmed they want to create
    items in Linear. start_date and end_date are ISO dates (e.g. '2026-09-01');
    goal becomes the cycle description. The team must have cycles enabled —
    the error says so when it does not. Returns the new cycle's id and name.
    """
    logger.debug("linear_create_sprint called: name=%r", sprint_name)
    if not get_linear_api_key():
        return _MISSING_CONFIG_MSG
    try:
        team = _resolve_team()
        input_payload: dict = {"teamId": team["id"], "startsAt": start_date, "endsAt": end_date}
        if sprint_name:
            input_payload["name"] = sprint_name
        if goal:
            input_payload["description"] = goal
        data = _linear_request(
            "mutation($input: CycleCreateInput!) { cycleCreate(input: $input) { success cycle { id name number } } }",
            {"input": input_payload},
        )
        payload = data.get("cycleCreate", {})
        if not payload.get("success"):
            return "Error: Linear did not create the cycle — are cycles enabled for the team?"
        cycle = payload["cycle"]
        return f"Created cycle '{cycle.get('name') or sprint_name}' (ID: {cycle['id']})"
    except LinearError as e:
        message = str(e)
        if "cycle" in message.lower() or "disabled" in message.lower():
            return "Error: The team has cycles disabled — enable them in Linear's team settings, then retry."
        return message
    except Exception as e:
        logger.error("Unexpected error in linear_create_sprint: %s", e)
        return f"Error: {e}"
