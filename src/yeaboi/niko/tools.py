"""Niko's tool surface — every yeaboi read, and one suggestion.

# See docs: "Tools" — tool types, the @tool decorator, risk levels
# See docs: "The ReAct Loop" — Thought → Action → Observation

Two rules define this module, and both are load-bearing:

**Read-only.** Niko answers and points; it never changes anything. There is no
tool here that writes, deletes, schedules or spends, so no confirmation gate to
forget and no path by which a hallucinated argument destroys a sprint plan. The
one tool with an effect is :func:`navigate`, and its effect is a suggestion the
surface may ignore.

**Never through the MCP dispatcher.** Every tool calls the same engine or store
function ``src/yeaboi/mcp/tools_*.py`` calls, directly. Going through
``McpDispatcher`` / ``POST /api/tool/{name}`` instead would re-enter
``yeaboi.mcp.runtime._ENGINE_LOCK`` — a plain ``threading.Lock`` — from inside a
call that already holds it, and the ``niko_ask`` MCP tool would deadlock for an
hour. Reusing the private helpers is the point: they are the validation and the
shaping, and a second copy is a second thing to drift.

Every tool returns a JSON-able dict and never raises: a failure becomes
``{"error": ...}``, which the loop hands back to the model as an observation.
A tool that raised would end the turn with a traceback instead of an answer.
"""

from __future__ import annotations

import json
import logging
import pathlib
from collections.abc import Callable

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

#: The desktop route registry, generated in yeaboi-desktop and committed here.
#: Read rather than re-declared so ``navigate`` can never offer a route the
#: window does not serve. The packaged copy comes first: only ``src/yeaboi``
#: ships, so a wheel has no ``contracts/`` to read (see the force-include in
#: pyproject.toml). The source checkout has no ``data/`` and falls through.
_PACKAGED_MANIFEST = pathlib.Path(__file__).resolve().parents[1] / "data" / "routes_manifest.json"
_REPO_MANIFEST = pathlib.Path(__file__).resolve().parents[3] / "contracts" / "v1" / "routes_manifest.json"


def _guard(what: str, fn: Callable, /, *args, **kwargs) -> dict:
    """Run one read and wrap it. A tool never raises into the turn."""
    logger.info("niko tool start: %s", what)
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 — every failure is an observation, not a crash
        logger.warning("niko tool failed: %s: %s", what, exc)
        return {"error": str(exc) or type(exc).__name__}
    from yeaboi.mcp.runtime import to_jsonable

    logger.info("niko tool ok: %s", what)
    return to_jsonable(result) if not isinstance(result, dict) else to_jsonable(result)


# ---------------------------------------------------------------------------
# What yeaboi is
# ---------------------------------------------------------------------------


@tool
def list_capabilities() -> dict:
    """List everything yeaboi can do: the Solo and Team menus, the Agents family, and the
    three categories they sit under. Use this when the user asks what yeaboi is,
    what it can do, where a feature lives, or what they should try next.
    ``modes`` is the Team menu; ``solo`` is the Solo menu.
    """

    def _cards() -> dict:
        from yeaboi.ui.mode_select.screens._screens import _AGENT_CARDS, _INTAKE_CARDS, _MODE_CARDS, _SOLO_CARDS
        from yeaboi.ui.mode_select.screens._screens_category import _CATEGORY_CARDS

        return {
            "categories": _CATEGORY_CARDS,
            "solo": _SOLO_CARDS,
            "modes": _MODE_CARDS,
            "agents": _AGENT_CARDS,
            "intake": _INTAKE_CARDS,
        }

    return _guard("list_capabilities", _cards)


@tool
def list_routes() -> dict:
    """List every screen the desktop app serves, with the capability each belongs to.
    Use this before calling `navigate` so you offer a route that actually exists.
    """
    return _guard("list_routes", lambda: {"routes": known_routes()})


def known_routes() -> list[dict]:
    """The committed desktop route surface. Empty when the manifest is missing."""
    for candidate in (_PACKAGED_MANIFEST, _REPO_MANIFEST):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            logger.warning("niko: route manifest unreadable at %s (%s)", candidate, exc)
            return []
        return [row for row in data.get("routes", []) if isinstance(row, dict)]
    logger.warning("niko: no route manifest found — navigate will refuse every route")
    return []


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------


@tool
def list_sessions() -> dict:
    """List the user's saved planning sessions: project name, when it was made, and
    how far the pipeline got. Use this to answer "what have I planned?".
    """
    from yeaboi.mcp.tools_sessions import _list_sessions

    return _guard("list_sessions", lambda: {"sessions": _list_sessions()})


@tool
def get_session(session_id: str = "") -> dict:
    """Get one saved planning session in detail: which artifacts exist (analysis,
    epics, stories, tasks, sprints) and how many of each. Blank `session_id`
    means the most recent session.
    """
    from yeaboi.mcp.tools_sessions import _get_session

    return _guard("get_session", _get_session, session_id)


@tool
def standup_history(session_id: str = "", limit: int = 10) -> dict:
    """Recent Daily Standup runs for a session: when each ran and what it found.
    Blank `session_id` means the most recent session.
    """
    from yeaboi.mcp.tools_standup import _standup_history

    return _guard("standup_history", _standup_history, session_id, limit)


@tool
def reporting_history(session_id: str = "", limit: int = 10) -> dict:
    """Recent delivery reports for a session. Blank `session_id` means the most
    recent session.
    """
    from yeaboi.mcp.tools_reporting import _reporting_history

    return _guard("reporting_history", _reporting_history, session_id, limit)


@tool
def retro_history(session_id: str = "", limit: int = 10) -> dict:
    """Recent retro boards for a session: when each ran and what came out of it."""
    from yeaboi.mcp.tools_retro import _retro_history

    return _guard("retro_history", _retro_history, session_id, limit)


@tool
def poker_history(session_id: str = "", limit: int = 10) -> dict:
    """Recent planning-poker sessions: what was estimated and what it landed on."""
    from yeaboi.mcp.tools_poker import _poker_history

    return _guard("poker_history", _poker_history, session_id, limit)


@tool
def team_roster() -> dict:
    """The team members yeaboi knows about, from the configured tracker."""
    from yeaboi.mcp.tools_team import _team_roster

    return _guard("team_roster", _team_roster, "", "")


@tool
def team_profile() -> dict:
    """The team's analysed delivery profile: velocity, estimation patterns, and the
    signals the Analysis mode computed. Use this for "how is my team doing?".
    """
    from yeaboi.mcp.tools_team import _team_profile_get

    return _guard("team_profile", _team_profile_get)


@tool
def performance_roster() -> dict:
    """The engineers Performance mode can prepare a 1:1 or six-month review for."""
    from yeaboi.mcp.tools_performance import _roster

    return _guard("performance_roster", _roster, "", "")


@tool
def ship_status() -> dict:
    """The Ship pipeline right now: any run in flight, and whether a diff is sitting
    at the approval gate waiting for the user.
    """
    from yeaboi.mcp.tools_ship import _status

    return _guard("ship_status", _status)


@tool
def ship_history(limit: int = 10) -> dict:
    """Recent Ship runs: which story each took on and how it ended."""
    from yeaboi.mcp.tools_ship import _history

    return _guard("ship_history", _history, limit)


# ---------------------------------------------------------------------------
# Agents (agentwatch)
# ---------------------------------------------------------------------------


@tool
def agents_usage_history(limit: int = 10) -> dict:
    """Recent Agent Usage reports: what the user's AI coding agents have been
    costing — tokens, cache, per-model and per-project spend.
    """
    from yeaboi.mcp.tools_agentwatch import _usage_history

    return _guard("agents_usage_history", _usage_history, limit)


@tool
def agents_advisor_history(limit: int = 10) -> dict:
    """Recent Agent Advisor reports: how much of the agent spend was recoverable —
    re-read waste, cache health, and what each agent is costing.
    """
    from yeaboi.mcp.tools_agentwatch import _advisor_history

    return _guard("agents_advisor_history", _advisor_history, limit)


@tool
def agents_security_history(limit: int = 10) -> dict:
    """Recent Agent Security scans: the posture of the AI coding agents working
    across the SDLC, the one-line verdict, and the issues with what to do about them.
    """
    from yeaboi.mcp.tools_agentwatch import _security_history

    return _guard("agents_security_history", _security_history, limit)


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


@tool
def llm_usage() -> dict:
    """yeaboi's own LLM spend: tokens and cost for the calls yeaboi itself made.
    This is NOT the AI coding agents' spend — that is `agents_usage_history`.
    """
    from yeaboi.mcp.tools_sessions import _usage_get

    return _guard("llm_usage", _usage_get)


@tool
def ceremonies_list(session_id: str = "") -> dict:
    """The team's scheduled ceremonies: which mode each runs, when, where it delivers,
    and whether the OS job that fires it is actually installed.
    """
    from yeaboi.mcp.tools_ceremonies import _list

    return _guard("ceremonies_list", _list, session_id)


@tool
def ceremonies_history(session_id: str = "", ceremony: str = "", limit: int = 10) -> dict:
    """What the scheduled ceremonies actually did: every fire, including the ones the
    guards declined and the ones that failed with nobody watching.
    """
    from yeaboi.mcp.tools_ceremonies import _history

    return _guard("ceremonies_history", _history, session_id, ceremony, limit)


@tool
def provenance_audit(window_days: int = 30) -> dict:
    """The decision chain: which decisions yeaboi recorded over a window, who or what
    made them, and where they conflict. Use this for "why did we decide X?".
    """
    from yeaboi.mcp.tools_provenance import _audit

    return _guard("provenance_audit", _audit, window_days)


@tool
def provenance_trace(entity_id: str, depth: int = 2) -> dict:
    """Trace one decision back through what produced it — a story, a sprint, a
    standup finding. `entity_id` comes from `provenance_audit`.
    """
    from yeaboi.mcp.tools_provenance import _trace

    return _guard("provenance_trace", _trace, entity_id, depth)


# ---------------------------------------------------------------------------
# The one tool with an effect
# ---------------------------------------------------------------------------


@tool
def navigate(route: str) -> dict:
    """Take the user to a screen. Call this when the answer is "that lives over there"
    — e.g. `/team/retro` for a retro, `/agents/usage` for agent spend. The route
    must be one `list_routes` returned. This only moves the user; it starts nothing.
    """
    known = {row.get("path", "") for row in known_routes()}
    if route not in known:
        logger.warning("niko: navigate refused unknown route %r", route)
        return {"error": f"{route!r} is not a route — call list_routes first.", "route": ""}
    logger.info("niko: navigate suggested route=%s", route)
    return {"route": route}


#: Every tool Niko may call, in the order the prompt introduces them.
NIKO_TOOLS = [
    list_capabilities,
    list_routes,
    list_sessions,
    get_session,
    standup_history,
    reporting_history,
    retro_history,
    poker_history,
    team_roster,
    team_profile,
    performance_roster,
    ship_status,
    ship_history,
    agents_usage_history,
    agents_advisor_history,
    agents_security_history,
    llm_usage,
    ceremonies_list,
    ceremonies_history,
    provenance_audit,
    provenance_trace,
    navigate,
]

TOOLS_BY_NAME = {t.name: t for t in NIKO_TOOLS}

#: The tool whose result the engine turns into a ``Navigate`` event.
NAVIGATE_TOOL = navigate.name


def call(name: str, arguments: dict | None) -> dict:
    """Run one tool by name, and never raise.

    A model can ask for a tool that does not exist, omit a required argument, or
    invent one — all three arrive here and all three must leave as an
    observation the loop can hand back. A raise would end the turn with a
    traceback where an answer belongs.
    """
    found = TOOLS_BY_NAME.get(name)
    if found is None:
        logger.warning("niko: model asked for unknown tool %r", name)
        return {"error": f"Unknown tool: {name}"}
    # .func rather than .invoke: the dict is wanted whole, for the record and
    # the tool card. invoke() would stringify it on the way out.
    try:
        return found.func(**(arguments or {}))
    except TypeError as exc:
        logger.warning("niko: bad arguments for %s: %s", name, exc)
        return {"error": f"{name} was called with the wrong arguments ({exc}). Check the tool's schema."}
