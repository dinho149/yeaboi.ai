"""MCP tools: Retro history (read-only).

The live retro itself stays in the TUI — it is a real-time browser board
(collaborative card entry, timers, presence), not something a single tool
call can host. What agents can usefully read is the *outcome*: past boards
with their cards, reactions, and action items.
"""

from __future__ import annotations

import logging

from yeaboi.mcp.runtime import run_readonly

logger = logging.getLogger(__name__)


def _retro_history(session_id: str, limit: int) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.retro.store import RetroStore

    resolved = resolve_session_id(session_id)
    with RetroStore(get_db_path()) as store:
        history = store.get_history(resolved, limit=limit)
        latest = store.get_latest_report(resolved)
    return {"session_id": resolved, "history": history, "latest_report": latest}


def _retro_export(session_id: str) -> dict:
    from yeaboi.mcp.tools_sessions import resolve_session_id
    from yeaboi.paths import get_db_path
    from yeaboi.retro.export import export_retro
    from yeaboi.retro.store import RetroStore

    resolved = resolve_session_id(session_id)
    with RetroStore(get_db_path()) as store:
        report = store.get_latest_report(resolved)
        run_history = store.get_history(resolved, limit=30)
    if report is None:
        raise ValueError(f"No retro recorded for session {resolved!r} — run a retro board from the yeaboi TUI first.")
    paths = export_retro(report, history=run_history)
    logger.info("Retro exported via MCP: session=%s date=%s", resolved, report.date)
    return {
        "session_id": resolved,
        "retro_date": report.date,
        "markdown": str(paths["markdown"]),
        "html": str(paths["html"]),
    }


def register(app) -> None:
    """Attach the retro tools to the FastMCP app."""

    @app.tool()
    async def retro_history(session_id: str = "", limit: int = 30) -> dict:
        """Get past sprint retrospectives (cards, reactions, action items) for a session.
        Blank session_id = most recent session. Running a live retro board requires the yeaboi TUI."""
        return await run_readonly(_retro_history, session_id, limit)

    @app.tool()
    async def retro_export(session_id: str = "") -> dict:
        """Export the most recent retrospective as Markdown + HTML files (under
        ~/.yeaboi/exports/retro/) and return their paths. Blank session_id = most recent session."""
        return await run_readonly(_retro_export, session_id)
