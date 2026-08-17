"""MCP tools: ceremonies — read what the team has scheduled and what it did.

Read-only by design, and the reason is the same one that keeps the standup's
own schedule off this surface: declaring a ceremony installs a **launchd or
crontab job on the user's machine**, which outlives the session, survives
reboots and spends money unattended. That is a decision made at the terminal
the job will run on, with the consent flow that comes with it — not by a tool
call from somewhere else. Declare from the TUI or `yeaboi ceremonies add`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from yeaboi.mcp.runtime import run_readonly

logger = logging.getLogger(__name__)


def _list(session_id: str):
    from yeaboi.ceremonies.scheduler import installed_ceremonies
    from yeaboi.ceremonies.store import CeremonyStore
    from yeaboi.mcp.tools_sessions import resolve_session_id

    resolved = resolve_session_id(session_id)
    with CeremonyStore() as store:
        declared = store.list(resolved)
        rows = []
        for ceremony in declared:
            last = store.last_run(resolved, ceremony.name)
            rows.append({**asdict(ceremony), "last_run": asdict(last) if last else None})
    # The store says what is declared; the OS says what will actually fire, and
    # the gap between them is invisible until a morning goes quiet.
    return {"session_id": resolved, "ceremonies": rows, "installed_jobs": installed_ceremonies(resolved)}


def _history(session_id: str, ceremony: str, limit: int):
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    from yeaboi.ceremonies.store import CeremonyStore
    from yeaboi.mcp.tools_sessions import resolve_session_id

    resolved = resolve_session_id(session_id)
    with CeremonyStore() as store:
        runs = store.runs(resolved, ceremony, limit=limit)
    return {"session_id": resolved, "runs": [asdict(run) for run in runs]}


def register(app) -> None:
    """Attach the ceremonies tools to the FastMCP app."""

    @app.tool()
    async def ceremonies_list(session_id: str = "") -> dict:
        """List the team's scheduled ceremonies: which mode each runs, when, where it
        lands, and how its last run went. ``installed_jobs`` is read off the operating
        system, so a ceremony that is declared but missing from it will not fire.

        Read-only: declaring one installs a job on the user's machine and is done at
        the terminal (`yeaboi ceremonies add`), never from here."""
        return await run_readonly(_list, session_id)

    @app.tool()
    async def ceremonies_history(session_id: str = "", ceremony: str = "", limit: int = 20) -> dict:
        """What the scheduled ceremonies actually did, newest first — including the runs
        the guards declined (too late after a sleep, over the monthly spend cap, paused)
        and the reason for each. Use to answer "did the standup post this morning, and
        if not, why not"."""
        return await run_readonly(_history, session_id, ceremony, limit)
