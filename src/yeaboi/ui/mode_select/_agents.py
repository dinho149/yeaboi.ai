"""Routing and pages for the Agents family (agentwatch) in the TUI.

One dispatch entry (:func:`route_agent_mode`) instead of three more branches in
``select_mode``'s routing chain. Each mode wraps itself in ``mode_log`` (its own
log file under ~/.yeaboi/logs/agentwatch/) and its one-time beta notice.

The page bodies land phase by phase with their engines (Agent Usage, Agent
Standup, Agent Security); until a mode's engine exists this shows an honest
placeholder screen rather than falling through to another mode's flow.

Imports from :mod:`yeaboi.ui.mode_select` happen lazily inside function bodies —
the package imports this module's callers, so a top-level import is a cycle.
"""

from __future__ import annotations

import logging
import time

from rich.console import Console, Group
from rich.text import Text

from yeaboi.logging_setup import mode_log
from yeaboi.ui.shared._beta_notice import show_beta_notice
from yeaboi.ui.shared._components import (
    AGENT_SECURITY_THEME,
    AGENT_STANDUP_THEME,
    AGENT_USAGE_THEME,
    Theme,
    agent_security_title,
    agent_standup_title,
    agent_usage_title,
    build_page_panel,
    build_reveal_subtitle,
)

logger = logging.getLogger(__name__)

_MODE_META: dict[str, tuple[str, Theme]] = {
    "agent-usage": ("Agent Usage", AGENT_USAGE_THEME),
    "agent-standup": ("Agent Standup", AGENT_STANDUP_THEME),
    "agent-security": ("Agent Security", AGENT_SECURITY_THEME),
}

_TITLE_FNS = {
    "agent-usage": agent_usage_title,
    "agent-standup": agent_standup_title,
    "agent-security": agent_security_title,
}


def route_agent_mode(
    key: str,
    *,
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
) -> None:
    """Enter one Agents mode from the menu; returns when the user backs out."""
    label, _theme = _MODE_META.get(key, (key, AGENT_USAGE_THEME))
    with mode_log("agentwatch"):
        logger.info("%s opened", label)
        if not show_beta_notice(live, console, read_key, frame_time, supports_timeout, mode_key=key):
            logger.info("%s beta notice declined — back to menu", label)
            return
        if key == "agent-usage":
            _run_agent_usage_page(console, live, read_key, frame_time, supports_timeout)
        elif key == "agent-standup":
            _run_agent_standup_page(console, live, read_key, frame_time, supports_timeout)
        elif key == "agent-security":
            _run_agent_security_page(console, live, read_key, frame_time, supports_timeout)
        logger.info("%s closed", label)


def _run_placeholder_page(
    console: Console,
    live,
    read_key,
    frame_time: float,
    supports_timeout: bool,
    *,
    key: str,
    body_lines: tuple[str, ...],
) -> None:
    """An honest holding page for a mode whose engine hasn't landed yet.

    Renders the mode's own title/theme so the entrance transition reads
    correctly, states what is coming, and waits for any key.
    """
    theme = _MODE_META[key][1]
    title_fn = _TITLE_FNS[key]
    start = time.monotonic()
    while True:
        w, h = console.size
        tick = time.monotonic() - start
        body = Group(
            Text(""),
            title_fn(tick, width=w),
            build_reveal_subtitle("Coming together", tick, justify="center"),
            Text(""),
            *[Text(line, justify="center", style="rgb(160,160,175)") for line in body_lines],
            Text(""),
            Text("press any key to go back", justify="center", style="rgb(90,90,105)"),
        )
        live.update(build_page_panel(body, theme=theme, height=h))
        k = read_key(timeout=frame_time) if supports_timeout else read_key()
        if k:
            return


def _run_agent_usage_page(console, live, read_key, frame_time, supports_timeout) -> None:
    """Agent Usage — replaced by the real dashboard in the Agent Usage phase."""
    _run_placeholder_page(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        key="agent-usage",
        body_lines=("The agent cost dashboard is being wired up.",),
    )


def _run_agent_standup_page(console, live, read_key, frame_time, supports_timeout) -> None:
    """Agent Standup — replaced by the real hub in the Agent Standup phase."""
    _run_placeholder_page(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        key="agent-standup",
        body_lines=("The agent standup digest is being wired up.",),
    )


def _run_agent_security_page(console, live, read_key, frame_time, supports_timeout) -> None:
    """Agent Security — replaced by the real hub in the Agent Security phase."""
    _run_placeholder_page(
        console,
        live,
        read_key,
        frame_time,
        supports_timeout,
        key="agent-security",
        body_lines=("The agent security scan is being wired up.",),
    )
