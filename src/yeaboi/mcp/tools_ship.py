"""MCP tools: ship — read the supervised story → PR runs and the launch budget.

Read-only by design. Launching a run is deliberately NOT an MCP tool: a run
holds a live subprocess for many minutes behind the server's single engine
lock (blocking every other tool), and its approval gate is a human decision
made at a terminal — the same reasoning that keeps output-sharing off this
surface. Runs start from the TUI's Ship card or `yeaboi ship run`.
"""

from __future__ import annotations

import logging

from yeaboi.mcp.runtime import run_readonly

logger = logging.getLogger(__name__)


def _history(limit: int):
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    from dataclasses import asdict

    from yeaboi.ship.store import ShipStore

    # asdict per run: to_jsonable only unpacks a TOP-LEVEL dataclass, and a
    # nested one would degrade to its repr via json's default=str.
    with ShipStore() as store:
        return {"runs": [asdict(run) for run in store.list_runs(limit=limit)]}


def _status():
    from dataclasses import asdict

    from yeaboi.ship import budget
    from yeaboi.ship.store import ShipStore

    with ShipStore() as store:
        runs = store.list_runs(limit=1)
    return {"latest": asdict(runs[0]) if runs else None, "budget": asdict(budget.status())}


def register(app) -> None:
    """Attach the ship tools to the FastMCP app."""

    @app.tool()
    async def ship_history(limit: int = 10) -> dict:
        """BETA — List the user's supervised coding-agent runs (yeaboi ship), newest first:
        story, status, validation verdict, cost, and the PR each approved run opened.

        Runs are launched from the TUI or `yeaboi ship run`, never from here — the
        approval gate is a human decision made at a terminal."""
        return await run_readonly(_history, limit)

    @app.tool()
    async def ship_status() -> dict:
        """BETA — The latest ship run plus the user-global launch budget posture
        (active permits, hourly/daily counts, circuit-breaker state). Use to answer
        "is a run in flight / why was a launch denied" without starting anything."""
        return await run_readonly(_status)
