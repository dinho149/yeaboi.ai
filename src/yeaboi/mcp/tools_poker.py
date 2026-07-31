"""MCP tools: Poker session history (read-only).

The live poker session itself stays in the TUI — it is a real-time browser
board (secret voting, reveals, tracker write-backs) that a single tool call
cannot host. What agents can usefully read is the *outcome*: past sessions with
their tickets, votes, final points, and AI notes.
"""

from __future__ import annotations

import logging

from yeaboi.mcp.runtime import run_readonly

logger = logging.getLogger(__name__)


def _poker_history(session_id: str, limit: int) -> dict:
    from yeaboi.mcp.runtime import to_jsonable
    from yeaboi.paths import get_db_path
    from yeaboi.poker.store import PokerStore

    with PokerStore(get_db_path()) as store:
        # Poker sessions often run under auto-created quick sessions, so the
        # default listing is cross-session; session_id narrows it.
        rows = store.get_all_history(200)
        if session_id:
            rows = [r for r in rows if r.get("session_id") == session_id]
        rows = rows[:limit]
        latest = store.get_run_by_id(rows[0]["id"]) if rows else None
    # to_jsonable only converts a TOP-LEVEL dataclass; nested inside this dict the
    # report would fall to default=str. Convert here so latest_report is a
    # structured dict rather than its str() repr (same as tools_reporting).
    return {"history": rows, "latest_report": to_jsonable(latest) if latest is not None else None}


def _poker_export(session_id: str) -> dict:
    from yeaboi.paths import get_db_path
    from yeaboi.poker.export import export_poker
    from yeaboi.poker.store import PokerStore

    with PokerStore(get_db_path()) as store:
        rows = store.get_all_history(200)
        if session_id:
            rows = [r for r in rows if r.get("session_id") == session_id]
        report = store.get_run_by_id(rows[0]["id"]) if rows else None
        run_history = store.get_history(report.session_id, limit=30) if report and report.session_id else []
    if report is None:
        raise ValueError("No poker session recorded yet — run one from the yeaboi TUI Poker page first.")
    paths = export_poker(report, history=run_history)
    logger.info("Poker session exported via MCP: session=%s date=%s", report.session_id, report.date)
    return {
        "session_id": report.session_id,
        "poker_date": report.date,
        "markdown": str(paths["markdown"]),
        "html": str(paths["html"]),
    }


def register(app) -> None:
    """Attach the poker tools to the FastMCP app."""

    @app.tool()
    async def poker_history(session_id: str = "", limit: int = 30) -> dict:
        """Get past planning-poker sessions (tickets, votes, final story points, AI notes).
        Blank session_id = all sessions, newest first. Running a live voting session requires the yeaboi TUI."""
        return await run_readonly(_poker_history, session_id, limit)

    @app.tool()
    async def poker_export(session_id: str = "") -> dict:
        """Export the most recent poker session as Markdown + HTML files (under
        ~/.yeaboi/exports/poker/) and return their paths. Blank session_id = most recent session."""
        return await run_readonly(_poker_export, session_id)
