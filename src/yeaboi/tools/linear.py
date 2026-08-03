"""Linear tools — 3 read-only + 1 write (with user-confirmation guard in docstrings).

# See docs: "Tools" — tool types, @tool decorator, risk levels
#
# This module mirrors tools/notion.py's shape (module docstring covering the
# client choice and auth model, a single ``_MISSING_CONFIG_MSG``, @tool-decorated
# functions, read tools low-risk and the write tool carrying an explicit "only
# call after the user confirms" note) while mirroring tools/jira.py's *semantics*
# — Linear is an issue tracker, so its read tools are the twins of
# ``jira_read_board`` / ``jira_fetch_velocity`` / ``jira_fetch_active_sprint``
# and deliberately return the same JSON shapes. That is what lets the intake
# node's tracker seam in agent/nodes.py treat Linear as a third tracker
# alongside Jira and Azure DevOps without a new parsing path.
#
# Why stdlib urllib and not an SDK?
# Every other integration here wraps a maintained per-service SDK (PyGithub,
# jira, atlassian-python-api, notion-client). Linear publishes no official
# Python SDK — only TypeScript — and the community ports are thin, unmaintained
# GraphQL wrappers. Rather than take a dependency that could go stale, this
# module speaks to the single GraphQL endpoint directly with stdlib
# ``urllib.request``, which is already this project's convention for non-SDK
# HTTP (see update_check.py, telemetry.py, retro/tunnel.py, standup/delivery.py).
# Zero new dependencies, and the request surface is four fixed queries.
#
# Auth: Linear uses a *personal API key* sent in a bare ``Authorization`` header
# — note there is NO ``Bearer`` prefix, which is the single most common mistake
# when calling this API by hand (the ``Bearer`` form is for OAuth2 access tokens
# only). Keys are created at Linear → Settings → Security & access → Personal
# API keys and look like ``lin_api_…``.
#
# ONE real divergence from Jira: Linear generates Cycles automatically on a
# fixed cadence rather than having someone create a sprint, so there is no
# ``linear_create_cycle`` twin of ``jira_create_sprint``. Writing cycles from a
# CLI would fight the product. Story write-back (``linear_create_issue``) is the
# whole write surface.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

from langchain_core.tools import tool

from yeaboi.config import get_linear_api_key, get_linear_team_key

logger = logging.getLogger(__name__)

# Shown whenever the Linear API key is missing — single source of truth for the message.
_MISSING_CONFIG_MSG = "Error: Linear is not configured. Ensure LINEAR_API_KEY is set in your .env file."

# Shown when no team can be determined — the second thing that can be unset.
_MISSING_TEAM_MSG = "Error: No team_key provided and LINEAR_TEAM_KEY is not set in .env."

_API_URL = "https://api.linear.app/graphql"

# Linear has no server-side "how many issues match" count, so a backlog size is
# measured by fetching ids up to this ceiling and reporting "N+" past it. High
# enough to be exact for real backlogs, low enough to stay one request.
_MAX_BACKLOG_ISSUES = 250

# Per-cycle issue ceiling when summing completed estimate points.
_MAX_CYCLE_ISSUES = 250

# Number of closed cycles sampled for velocity — matches jira_fetch_velocity's
# 3-sprint window so the two trackers produce comparable numbers.
_VELOCITY_SAMPLE = 3

_REQUEST_TIMEOUT_SECONDS = 30


class LinearAPIError(Exception):
    """A Linear API failure carrying an HTTP-ish status code.

    GraphQL reports application errors inside a 200 response, so ``status``
    is either the real HTTP status (transport failures) or a status *derived*
    from the GraphQL error code (401 for authentication, 429 for rate limits).
    Either way callers get one exception type with one ``status`` attribute,
    which is what lets ``_linear_error_msg`` mirror ``_jira_error_msg`` and
    ``_notion_error_msg``.
    """

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _linear_error_msg(e: LinearAPIError) -> str:
    """Return a user-friendly message for common Linear failure codes."""
    if e.status == 401:
        return "Error: Linear authentication failed. Check LINEAR_API_KEY in .env."
    if e.status == 403:
        return "Error: Linear permission denied. The API key's user cannot see this team or issue."
    if e.status == 404:
        return f"Error: Linear resource not found — verify the team key. ({e.message})"
    if e.status == 429:
        return "Error: Linear rate limit reached. Wait a moment and try again."
    return f"Error: Linear API error {e.status}: {e.message}"


# GraphQL error codes Linear returns inside a 200 body, mapped to the HTTP
# status the same failure would carry on a REST API. Keeping the mapping in one
# dict means _graphql stays a transport function and every tool renders errors
# through the same _linear_error_msg.
_GRAPHQL_CODE_STATUS: dict[str, int] = {
    "AUTHENTICATION_ERROR": 401,
    "FORBIDDEN": 403,
    "RATELIMITED": 429,
}


def _graphql(query: str, variables: dict, *, timeout: int = _REQUEST_TIMEOUT_SECONDS) -> dict:
    """POST a GraphQL query to Linear and return the ``data`` object.

    Raises :class:`LinearAPIError` on transport failure, on a non-2xx response,
    and on a 200 response whose body carries a GraphQL ``errors`` array — the
    last case matters because GraphQL signals "your key is invalid" with HTTP
    200, so a naive status check would treat an auth failure as success.
    """
    api_key = get_linear_api_key()
    if not api_key:
        # Callers check for the missing key before getting here; this is a guard
        # so the module can never issue an unauthenticated request by accident.
        raise LinearAPIError(401, "LINEAR_API_KEY is not set")

    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - fixed https _API_URL constant
        _API_URL,
        data=body,
        headers={
            # No "Bearer" — Linear personal API keys go in the header verbatim.
            "Authorization": api_key,
            "Content-Type": "application/json",
            "User-Agent": "yeaboi",
        },
        method="POST",
    )

    logger.info("Linear API call starting: %d bytes to %s", len(body), _API_URL)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https _API_URL constant
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:200]
        except Exception:  # pragma: no cover — body already consumed/unreadable
            logger.debug("Linear API: could not read error body", exc_info=True)
        logger.error("Linear API call failed: HTTP %s %s", e.code, detail)
        raise LinearAPIError(e.code, detail or str(e.reason)) from e
    except urllib.error.URLError as e:
        logger.error("Linear API call failed: network error %s", e.reason)
        raise LinearAPIError(0, f"could not reach {_API_URL} ({e.reason})") from e
    except json.JSONDecodeError as e:
        logger.error("Linear API call failed: response was not JSON")
        raise LinearAPIError(0, "Linear returned a non-JSON response") from e

    errors = payload.get("errors") or []
    if errors:
        first = errors[0] if isinstance(errors[0], dict) else {}
        code = str((first.get("extensions") or {}).get("code", ""))
        message = str(first.get("message", "unknown GraphQL error"))
        status = _GRAPHQL_CODE_STATUS.get(code, 400)
        logger.error("Linear API call failed: GraphQL %s — %s", code or "error", message)
        raise LinearAPIError(status, message)

    data = payload.get("data") or {}
    logger.info("Linear API call succeeded: %d top-level field(s)", len(data))
    return data


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
# Linear filters teams by their short key ("YEA"), not by UUID, which is the
# only team identifier a user can read off their own screen — every yeaboi tool
# therefore takes a team_key and resolves it here.

_TEAM_QUERY = """
query TeamByKey($key: String!) {
  teams(filter: { key: { eq: $key } }, first: 1) {
    nodes {
      id
      key
      name
      activeCycle { number name startsAt endsAt }
    }
  }
}
"""

_BACKLOG_QUERY = """
query Backlog($key: String!, $first: Int!) {
  issues(
    filter: { team: { key: { eq: $key } }, state: { type: { eq: "backlog" } } }
    first: $first
  ) {
    nodes { id }
    pageInfo { hasNextPage }
  }
}
"""

# isPast is Linear's own "this cycle has ended" predicate — cheaper and less
# error-prone than comparing endsAt against a client clock across time zones.
_CLOSED_CYCLES_QUERY = """
query ClosedCycles($key: String!, $cycles: Int!, $issues: Int!) {
  cycles(
    filter: { team: { key: { eq: $key } }, isPast: { eq: true } }
    last: $cycles
  ) {
    nodes {
      number
      name
      startsAt
      endsAt
      issues(filter: { completedAt: { null: false } }, first: $issues) {
        nodes {
          estimate
          assignee { id name }
        }
      }
    }
  }
}
"""

_CREATE_ISSUE_MUTATION = """
mutation CreateIssue($input: IssueCreateInput!) {
  issueCreate(input: $input) {
    success
    issue { id identifier title url }
  }
}
"""


def _resolve_team_key(team_key: str) -> str:
    """Return the explicit team key, else LINEAR_TEAM_KEY, else "".

    Linear team keys are upper-case by convention ("YEA") and the GraphQL
    ``eq`` filter is case-sensitive, so a user typing "yea" would silently match
    nothing. Normalising here means every tool behaves the same way.
    """
    return (team_key.strip() or (get_linear_team_key() or "")).strip().upper()


def _fetch_team(key: str) -> dict:
    """Return the team node for ``key``. Raises LinearAPIError(404) if absent."""
    logger.debug("Resolving Linear team %r", key)
    data = _graphql(_TEAM_QUERY, {"key": key})
    nodes = ((data.get("teams") or {}).get("nodes")) or []
    if not nodes:
        raise LinearAPIError(404, f"no team with key '{key}'")
    logger.debug("Resolved Linear team %r to %r", key, nodes[0].get("name", ""))
    return nodes[0]


def _cycle_label(cycle: dict) -> str:
    """Name a cycle for display.

    Linear cycles are numbered and only *optionally* named, so a cycle with no
    name must still read as something — "Cycle 42" matches how Linear's own UI
    labels an unnamed cycle.
    """
    name = (cycle.get("name") or "").strip()
    number = cycle.get("number")
    return name or f"Cycle {number}"


def _completed_points(cycle: dict) -> tuple[float, set[str]]:
    """Return (summed estimate points, assignee ids) for a cycle's completed issues.

    Issues with no estimate contribute 0 — the same treatment Jira gives an
    unpointed Done issue — so an unestimated team reads as zero velocity rather
    than crashing.
    """
    issues = ((cycle.get("issues") or {}).get("nodes")) or []
    total = 0.0
    assignees: set[str] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        estimate = issue.get("estimate")
        if estimate is not None:
            try:
                total += float(estimate)
            except (TypeError, ValueError):
                logger.debug("Linear: unparseable estimate %r skipped", estimate)
        assignee = issue.get("assignee")
        if isinstance(assignee, dict) and assignee.get("id"):
            assignees.add(str(assignee["id"]))
    return total, assignees


@tool
def linear_read_board(team_key: str = "") -> str:
    """Read the current state of a Linear team: active cycle, backlog size, and velocity.

    Linear's equivalent of a Jira board is a team, identified by its short key —
    the prefix on every issue id (the "ABC" in "ABC-123"). Falls back to the
    LINEAR_TEAM_KEY env var when team_key is not provided. Returns a formatted
    summary with team name, the active cycle and its dates, backlog count, and
    average velocity from the last 3 closed cycles.
    """
    # See docs: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.info("linear_read_board called: team_key=%r", team_key)
    if not get_linear_api_key():
        logger.warning("linear_read_board skipped — Linear not configured")
        return _MISSING_CONFIG_MSG

    key = _resolve_team_key(team_key)
    if not key:
        return _MISSING_TEAM_MSG

    try:
        team = _fetch_team(key)
        lines: list[str] = [
            f"Team: {team.get('name', key)} ({key})",
            "",
        ]

        cycle = team.get("activeCycle") or {}
        if cycle:
            lines.append(f"Active cycle: {_cycle_label(cycle)}")
            if cycle.get("startsAt"):
                lines.append(f"  Start: {str(cycle['startsAt'])[:10]}")
            if cycle.get("endsAt"):
                lines.append(f"  End: {str(cycle['endsAt'])[:10]}")
        else:
            lines.append("Active cycle: none (this team has cycles disabled or none running)")

        # Backlog size — no server-side count exists, so fetch ids to a ceiling.
        backlog = _graphql(_BACKLOG_QUERY, {"key": key, "first": _MAX_BACKLOG_ISSUES})
        issues = (backlog.get("issues") or {}).get("nodes") or []
        has_more = bool(((backlog.get("issues") or {}).get("pageInfo") or {}).get("hasNextPage"))
        lines.append(f"Backlog: {len(issues)}{'+' if has_more else ''} issues")

        # Average velocity over the sampled closed cycles.
        closed = _graphql(
            _CLOSED_CYCLES_QUERY,
            {"key": key, "cycles": _VELOCITY_SAMPLE, "issues": _MAX_CYCLE_ISSUES},
        )
        cycles = (closed.get("cycles") or {}).get("nodes") or []
        if cycles:
            totals = [_completed_points(c)[0] for c in cycles]
            avg = sum(totals) / len(totals)
            lines.append(f"Avg velocity (last {len(totals)} cycles): {avg:.1f} pts")
        else:
            lines.append("Avg velocity: no closed cycles found")

        logger.info("linear_read_board completed for team %s", key)
        return "\n".join(lines)

    except LinearAPIError as e:
        logger.error("Linear API error in linear_read_board (team=%s): %s", key, e)
        return _linear_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in linear_read_board (team=%s): %s", key, e)
        return f"Error: {e}"


@tool
def linear_fetch_velocity(team_key: str = "") -> str:
    """Fetch average team velocity and team size from the last 3 closed Linear cycles.

    Samples the team's 3 most recently completed cycles, sums the estimate
    points of the issues completed in each, and averages them. Team size is the
    number of unique assignees on that completed work. Per-developer velocity is
    derived by dividing team velocity by that count.

    Returns a JSON string with keys: team_velocity, jira_team_size, per_dev_velocity.
    Returns an error string starting with "Error:" on failure.

    # See docs: "Scrum Standards" — capacity planning
    #
    # The key is spelled ``jira_team_size`` rather than ``linear_team_size``
    # deliberately: agent/nodes.py's tracker seam consumes one dict shape from
    # whichever tracker is configured, and azdevops_fetch_velocity already
    # reuses the same key. Renaming it per-tracker would fork the consumer.
    #
    # The whole-team velocity must be normalised to per-developer because the
    # feature team may be a subset of the full Linear team. E.g. team avg =
    # 25 pts with 5 devs → 5 pts/dev. If 2 devs work on the feature →
    # feature velocity = 10 pts, not 25.
    """
    logger.info("linear_fetch_velocity called: team_key=%r", team_key)
    if not get_linear_api_key():
        logger.warning("linear_fetch_velocity skipped — Linear not configured")
        return _MISSING_CONFIG_MSG

    key = _resolve_team_key(team_key)
    if not key:
        return _MISSING_TEAM_MSG

    try:
        data = _graphql(
            _CLOSED_CYCLES_QUERY,
            {"key": key, "cycles": _VELOCITY_SAMPLE, "issues": _MAX_CYCLE_ISSUES},
        )
        cycles = (data.get("cycles") or {}).get("nodes") or []
        if not cycles:
            logger.info("linear_fetch_velocity: no closed cycles for team %s", key)
            return "Error: No closed cycles found — velocity cannot be computed."

        totals: list[float] = []
        assignees: set[str] = set()
        for cycle in cycles:
            points, cycle_assignees = _completed_points(cycle)
            totals.append(points)
            assignees |= cycle_assignees
            logger.debug("Linear cycle %s: %.1f pts, %d assignee(s)", _cycle_label(cycle), points, len(cycle_assignees))

        team_velocity = sum(totals) / len(totals)
        team_size = max(len(assignees), 1)

        if team_velocity <= 0:
            # Return team size even when velocity is zero — the headcount is
            # still useful for capping "increase team" recommendations. Same
            # contract as jira_fetch_velocity.
            logger.info("linear_fetch_velocity: zero velocity for team %s (size=%d)", key, team_size)
            return json.dumps(
                {
                    "team_velocity": 0,
                    "jira_team_size": team_size,
                    "per_dev_velocity": 0,
                    "velocity_error": "Computed velocity is zero — no estimate points on completed issues.",
                }
            )

        per_dev = team_velocity / team_size
        logger.info(
            "linear_fetch_velocity: team=%.1f size=%d per_dev=%.1f (team %s)", team_velocity, team_size, per_dev, key
        )
        return json.dumps(
            {
                "team_velocity": round(team_velocity),
                "jira_team_size": team_size,
                "per_dev_velocity": per_dev,
            }
        )

    except LinearAPIError as e:
        logger.error("Linear API error in linear_fetch_velocity (team=%s): %s", key, e)
        return _linear_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in linear_fetch_velocity (team=%s): %s", key, e)
        return f"Error: {e}"


@tool
def linear_fetch_active_cycle(team_key: str = "") -> str:
    """Fetch the currently active Linear cycle number and name.

    A Linear cycle is the direct analogue of a sprint. Unlike Jira — where the
    sprint number has to be parsed out of a free-text name like "Sprint 104" —
    Linear numbers cycles natively, so the number is read straight off the API.

    Returns a JSON string with keys: sprint_number, sprint_name, start_date.
    Returns an error string starting with "Error:" on failure.

    # See docs: "Scrum Standards" — sprint planning
    #
    # The keys are the sprint_* ones from jira_fetch_active_sprint on purpose —
    # see the note in linear_fetch_velocity about the shared consumer.
    """
    logger.info("linear_fetch_active_cycle called: team_key=%r", team_key)
    if not get_linear_api_key():
        logger.warning("linear_fetch_active_cycle skipped — Linear not configured")
        return _MISSING_CONFIG_MSG

    key = _resolve_team_key(team_key)
    if not key:
        return _MISSING_TEAM_MSG

    try:
        team = _fetch_team(key)
        cycle = team.get("activeCycle") or {}
        if not cycle:
            logger.info("linear_fetch_active_cycle: no active cycle for team %s", key)
            return f"Error: No active cycle on team '{key}'"

        number = cycle.get("number")
        if number is None:
            logger.warning("Linear active cycle for team %s has no number", key)
            return f"Error: Active cycle on team '{key}' has no cycle number"

        result: dict = {
            "sprint_number": int(number),
            "sprint_name": _cycle_label(cycle),
        }
        start = str(cycle.get("startsAt") or "")[:10]
        if start:
            result["start_date"] = start

        logger.info("linear_fetch_active_cycle: %s (start=%s)", result["sprint_name"], start or "unknown")
        return json.dumps(result)

    except LinearAPIError as e:
        logger.error("Linear API error in linear_fetch_active_cycle (team=%s): %s", key, e)
        return _linear_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in linear_fetch_active_cycle (team=%s): %s", key, e)
        return f"Error: {e}"


@tool
def linear_create_issue(
    title: str,
    description: str = "",
    team_key: str = "",
    estimate: int = 0,
    parent_id: str = "",
    internal_id: str = "",
) -> str:
    """Create a user story as an issue in Linear.

    Only call this after the user has explicitly confirmed they want to create issues in Linear.
    Falls back to LINEAR_TEAM_KEY env var when team_key is not provided.
    estimate is Linear's story-point field — pass 0 to leave the issue unestimated.
    parent_id is the UUID of a parent issue to nest this one under; Linear has no
    separate "epic" type, so an epic is modelled as a parent issue.
    Pass internal_id (e.g. 'story-3') to record the mapping between the internal
    artifact ID and the created Linear identifier — the response will include a
    'Mapping:' line for tracking.
    Returns the new issue's identifier (e.g. 'ABC-42') and URL on success.
    """
    # See docs: "Guardrails" — human-in-the-loop pattern; this tool is classified
    # WRITE in tools/risk.py, so the graph pauses in human_review before it runs.
    logger.info("linear_create_issue called: title=%r team_key=%r estimate=%d", title, team_key, estimate)
    if not get_linear_api_key():
        logger.warning("linear_create_issue skipped — Linear not configured")
        return _MISSING_CONFIG_MSG

    key = _resolve_team_key(team_key)
    if not key:
        return _MISSING_TEAM_MSG
    if not title.strip():
        return "Error: Provide a title for the issue."

    try:
        # issueCreate needs the team's UUID, not its key — resolve it first.
        team = _fetch_team(key)
        payload: dict = {"teamId": team["id"], "title": title.strip()}
        if description.strip():
            payload["description"] = description
        if estimate:
            payload["estimate"] = int(estimate)
        if parent_id.strip():
            payload["parentId"] = parent_id.strip()

        data = _graphql(_CREATE_ISSUE_MUTATION, {"input": payload})
        result = data.get("issueCreate") or {}
        if not result.get("success"):
            logger.error("Linear issueCreate reported failure for %r in team %s", title, key)
            return f"Error: Linear declined to create the issue '{title}'."

        issue = result.get("issue") or {}
        identifier = issue.get("identifier", "")
        logger.info("Created Linear issue %s in team %s", identifier or "(unknown)", key)

        lines = [
            f"Created Linear issue: {identifier} — {title.strip()}",
            f"Team: {team.get('name', key)} ({key})",
        ]
        if issue.get("url"):
            lines.append(f"URL: {issue['url']}")
        if estimate:
            lines.append(f"Estimate: {estimate} pts")
        # Record the internal→Linear mapping so downstream nodes can track which
        # internal story ID corresponds to this Linear identifier.
        if internal_id:
            lines.append(f"Mapping: {internal_id} → {identifier}")
        return "\n".join(lines)

    except LinearAPIError as e:
        logger.error("Linear API error in linear_create_issue (team=%s): %s", key, e)
        return _linear_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in linear_create_issue (team=%s): %s", key, e)
        return f"Error: {e}"
