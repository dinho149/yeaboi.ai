"""agentwatch — the Agents product family (monitoring AI coding agents).

The package name avoids ``agents`` on purpose: ``yeaboi.agent`` is the LangGraph
core, and a sibling ``yeaboi.agents`` would be a standing one-character typo
hazard. User-facing copy still says "Agents" (CLI ``yeaboi agents …``, MCP
``agents_*`` tools, TUI ``agent-*`` cards).

Layout follows the standalone-mode blueprint (see the mode-blueprints skill):
``collector.py`` ingests local agent-session telemetry (Claude Code)
into ``store.py``'s tables; ``engine.py`` holds the headless pipelines the TUI,
CLI, and MCP surfaces adapt.
"""
