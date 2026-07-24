"""MCP server package — expose yeaboi's modes as tools to AI coding agents.

# See docs: "MCP Server"

The Model Context Protocol (MCP) is the standard through which AI coding
tools (Claude Code, Cursor, Codex CLI, VS Code, Windsurf, …) call external
"servers" that provide tools. This package implements a stdio MCP server so
those agents can run yeaboi's planning pipeline, standups, delivery reports,
and performance workflows without the TUI.

Deliberately import-free: the ``mcp`` SDK is an optional extra
(``pip install 'yeaboi[mcp]'``), so importing ``yeaboi.mcp`` must always
succeed — ``server.main()`` guards the SDK import and prints an actionable
message when the extra is missing.
"""
