"""MCP tools: the provenance audit — verify and read the decision chain."""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_engine, run_readonly

logger = logging.getLogger(__name__)


def _audit(window_days: int):
    if window_days < 1 or window_days > 365:
        raise ValueError("window_days must be between 1 and 365.")
    from yeaboi.provenance.engine import run_provenance_audit

    return run_provenance_audit(window_days=window_days)


def _trace(entity_id: str, depth: int):
    if not (entity_id or "").strip():
        raise ValueError("entity_id is required — the audit lists the recorded ids.")
    if depth < 1 or depth > 5:
        raise ValueError("depth must be between 1 and 5.")
    from yeaboi.provenance.engine import trace_entity

    return trace_entity(entity_id.strip(), depth=depth)


def register(app) -> None:
    """Register the provenance tools on the FastMCP app."""

    @app.tool()
    async def provenance_audit(ctx: Context, window_days: int = 30) -> dict:
        """Verify yeaboi's tamper-evident decision chain and summarise what it
        recorded: every deterministic signal (standup practice nudges, blocker
        flags, confidence adjustments, conflict cards, performance preps and
        reviews) is chained with its evidence, and this audit re-verifies every
        link — an edited, deleted, or renumbered record is reported, never
        hidden. Deterministic and local; no LLM is involved."""
        return await run_engine(ctx, _audit, window_days)

    @app.tool()
    async def provenance_trace(entity_id: str, depth: int = 2) -> dict:
        """The "why" trail behind one recorded decision: its records (including
        retractions) plus the latest record behind each piece of evidence it
        used, breadth-first up to `depth` hops. Entity ids are listed by
        `provenance_audit`."""
        return await run_readonly(_trace, entity_id, depth)
