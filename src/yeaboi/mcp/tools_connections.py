"""MCP tools: the read-only connector catalog.

Reading which integrations exist is safe on this surface; *connecting* one is
not, which is why there is no verify or write tool here. The settings capability
already records the reason — an MCP server must not rewrite host credentials —
and firing the host's stored key at a host of the client's choosing is the same
mistake wearing a different hat.
"""

from __future__ import annotations

import logging

# Context must be importable from module globals — FastMCP evaluates the
# stringified type hints (PEP 563) of tool functions against this namespace.
from mcp.server.fastmcp import Context

from yeaboi.mcp.runtime import run_readonly

logger = logging.getLogger(__name__)


def _list(family: str, connected_only: bool):
    from yeaboi.connectors.engine import list_connections

    return list_connections(family=family, connected_only=connected_only)


def _fetch(key: str, since: str):
    from yeaboi.connectors.engine import fetch_ops_events

    return fetch_ops_events(key, since=since)


def register(app) -> None:
    """Attach the connector tools to the FastMCP app."""

    @app.tool()
    async def connections_list(
        ctx: Context,
        family: str = "",
        connected_only: bool = True,
    ) -> dict:
        """List the read-only integrations yeaboi can gather from, and which are set up.

        family narrows to one group (observability, incidents, errors, cloud, …).
        connected_only defaults to true — pass false for the full catalog of what
        could be added. Field values are never returned: each field reports only
        whether it is set.
        """
        return await run_readonly(_list, family, connected_only)

    @app.tool()
    async def connections_fetch(
        ctx: Context,
        key: str = "",
        since: str = "14d",
    ) -> dict:
        """Read what production did over a window: incidents, alerts and error spikes.

        key narrows to one connector (see connections_list); empty gathers from
        every connected one. since is a window like 14d, 48h or 2w.

        Returns bounded counts, titles, services, severities and timestamps —
        never a log line, a stack trace, a metric series or a person's name, and
        never a credential. Nothing is written to the vendor or stored locally.
        """
        return await run_readonly(_fetch, key, since)
