"""yeaboi MCP server — stdio entry point (`yeaboi-mcp`).

# See docs: "MCP Server"

Runs a Model Context Protocol server over stdio so AI coding agents
(Claude Code, Cursor, Codex CLI, VS Code, …) can call yeaboi's planning
pipeline, standups, delivery reports, and performance workflows as tools.

Configure it in any MCP client as:

    command: uvx
    args: ["--from", "yeaboi[mcp]", "yeaboi-mcp"]

Critical stdio rule: stdout carries the JSON-RPC stream, so nothing in this
process may print() to stdout — human-facing output goes to stderr, and all
diagnostics go to ~/.yeaboi/logs/mcp/mcp.log.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_MISSING_MCP_MESSAGE = (
    "yeaboi-mcp requires the 'mcp' extra.\n"
    "Install it with:  pip install 'yeaboi[mcp]'\n"
    "or run it via:    uvx --from 'yeaboi[mcp]' yeaboi-mcp"
)

_INSTRUCTIONS = (
    "yeaboi is an AI Scrum Master. Use these tools to plan projects into epics, "
    "stories, tasks and sprints (intake_questions → plan_generate → plan_export), "
    "run daily standups, produce stakeholder delivery reports, and prep engineer "
    "1:1s and reviews. Results come in an envelope {ok, llm_mode, warnings, data}; "
    "llm_mode 'fallback' means no LLM was available and data is a deterministic "
    "skeleton — surface the warning to the user."
)


def create_app():
    """Build the FastMCP app with every yeaboi tool registered.

    Kept separate from main() so tests can drive the app through the SDK's
    in-memory transport without spawning a process.
    """
    # The mcp SDK is an optional extra — imported here, not at module level,
    # so `import yeaboi.mcp.server` works everywhere and only *running* the
    # server requires the extra (same lazy-import convention as tools/).
    from mcp.server.fastmcp import FastMCP

    from yeaboi.mcp import (
        tools_anonymize,
        tools_performance,
        tools_planning,
        tools_reporting,
        tools_retro,
        tools_sessions,
        tools_standup,
        tools_team,
    )

    app = FastMCP("yeaboi", instructions=_INSTRUCTIONS)
    modules = (
        tools_planning,
        tools_sessions,
        tools_standup,
        tools_reporting,
        tools_performance,
        tools_retro,
        tools_team,
        tools_anonymize,
    )
    for module in modules:
        module.register(app)
    return app


def main() -> None:
    """Console-script entry point: configure logging, then serve stdio."""
    try:
        import mcp  # noqa: F401
    except ImportError:
        print(_MISSING_MCP_MESSAGE, file=sys.stderr)
        raise SystemExit(1) from None

    # Load ~/.yeaboi/.env (API keys, tracker credentials) exactly like the CLI
    # does — the provider fallback and tracker-backed tools depend on it.
    from yeaboi.config import load_user_config
    from yeaboi.logging_setup import attach_mode_handler, configure_logging

    load_user_config()
    configure_logging()
    attach_mode_handler("mcp")
    logger.info("yeaboi MCP server starting (stdio)")

    app = create_app()
    try:
        app.run()  # stdio transport — blocks until the client disconnects
    finally:
        logger.info("yeaboi MCP server stopped")


if __name__ == "__main__":
    main()
