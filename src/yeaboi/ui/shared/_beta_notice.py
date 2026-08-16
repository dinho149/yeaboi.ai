"""One-time entry notice for modes that ship in beta.

A mode card's BETA chip says *that* a mode is unverified; it has nowhere to say
*how*. This is the screen that says how — shown once, the first time the mode is
opened, then never again. The chip on the card and the page header carry the
reminder from then on, which is what makes "once ever" honest rather than a
disclaimer the user clicks past and never sees again.

Modelled on :mod:`yeaboi.ui.shared._export_picker`: one modal run-loop that takes
the caller's Live/console/read_key, so it composes with any frame-timed page
loop. Returns True to enter the mode, False to go back to the menu.

The acknowledgement lives in ``~/.yeaboi/.env`` (see ``config.mark_beta_notice_seen``)
and is written only on Continue — backing out leaves the notice pending, so
someone who bailed still gets told next time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.beta import BETA_LABEL
from yeaboi.config import is_beta_notice_seen, mark_beta_notice_seen
from yeaboi.ui.shared._click import button_click, parse_click
from yeaboi.ui.shared._components import (
    AGENT_ADVISOR_THEME,
    AGENT_SECURITY_THEME,
    AGENT_STANDUP_THEME,
    AGENT_USAGE_THEME,
    PAD,
    PERFORMANCE_THEME,
    Theme,
    agent_advisor_title,
    agent_security_title,
    agent_standup_title,
    agent_usage_title,
    build_action_buttons,
    build_badge,
    build_page_panel,
    build_reveal_subtitle,
    performance_title,
)

logger = logging.getLogger(__name__)

_ACTIONS = ["Continue", "Back"]


@dataclass(frozen=True)
class _BetaMode:
    """Everything a beta notice needs to look like the mode it gates."""

    title_fn: Callable[..., Text]
    theme: Theme
    subtitle: str
    headline: str
    body: tuple[str, ...]


# Copy rule: name what can actually go wrong and what stays local. A generic
# "this feature is experimental" tells the user nothing they can act on, and
# reads as liability cover rather than information.
_BETA_MODES: dict[str, _BetaMode] = {
    "performance": _BetaMode(
        title_fn=performance_title,
        theme=PERFORMANCE_THEME,
        subtitle="Beta — worth thirty seconds",
        headline="Performance is in beta.",
        body=(
            "1:1 preps, completions and 6-month reviews are drafted from your tracker",
            "data — read them as a starting point, not an assessment.",
            "",
            "Coverage depends on how much of the work is actually on the board; sparse",
            "boards produce thin, sometimes misleading signals.",
            "",
            "Nothing is sent to anyone automatically. Exports stay on this machine",
            "under ~/.yeaboi/exports/performance.",
        ),
    ),
    "agent-usage": _BetaMode(
        title_fn=agent_usage_title,
        theme=AGENT_USAGE_THEME,
        subtitle="Beta — worth thirty seconds",
        headline="Agent Usage is in beta.",
        body=(
            "Costs are estimates: token counts come from your local agent session logs",
            "(Claude Code), priced from a dated public rate table — not your",
            "provider's bill. Unknown models are priced at a mid-tier guess and flagged.",
            "",
            "Only aggregates are stored. Session transcripts are read on this machine",
            "and never copied, uploaded, or persisted.",
        ),
    ),
    "agent-advisor": _BetaMode(
        title_fn=agent_advisor_title,
        theme=AGENT_ADVISOR_THEME,
        subtitle="Beta — worth thirty seconds",
        headline="Agent Advisor is in beta.",
        body=(
            "Recoverable-spend figures are estimates of opportunity, not promised",
            "savings: tokens are approximated from bytes and priced at your window's",
            "blended input rate, and every mechanism count is a floor.",
            "",
            "Transcripts and CLAUDE.md files are read on this machine only. The report",
            "keeps counts, byte totals and file paths — never their content.",
        ),
    ),
    "agent-standup": _BetaMode(
        title_fn=agent_standup_title,
        theme=AGENT_STANDUP_THEME,
        subtitle="Beta — worth thirty seconds",
        headline="Agent Standup is in beta.",
        body=(
            "The digest combines local agent sessions with agent-authored commits and",
            "PRs found in your trackers. Detection is a lower bound — agents that leave",
            "no marker are invisible, so absence of activity is not proof of idleness.",
            "",
            "Nothing is sent to anyone unless you deliver it. Exports stay on this",
            "machine under ~/.yeaboi/exports/agentwatch.",
        ),
    ),
    "agent-security": _BetaMode(
        title_fn=agent_security_title,
        theme=AGENT_SECURITY_THEME,
        subtitle="Beta — worth thirty seconds",
        headline="Agent Security is in beta.",
        body=(
            "Checks are deterministic pattern scans over your agent configs and session",
            "logs — an indicator, not a security audit. A clean report means no known",
            "pattern matched, not that your setup is safe.",
            "",
            "Findings reference file and line only; matched secrets are never stored",
            "or displayed. Everything stays on this machine.",
        ),
    ),
}


def _build_beta_notice_screen(
    *,
    mode_key: str,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Render the beta notice as a standard full-screen page.

    Follows the shared page structure (title → subtitle → content → buttons) and
    wears the gated mode's own title and theme, so the notice reads as part of
    entering that mode rather than as an interstitial bolted in front of it.
    """
    spec = _BETA_MODES[mode_key]
    theme = spec.theme

    # shimmer_tick=None (not 0.0) — 0.0 is the animated path frozen at tick 0,
    # which leaves a stationary highlight sitting in the wordmark. The picker
    # this screen is modelled on calls title_fn(width=...) for the same reason.
    title = spec.title_fn(shimmer_tick, width=width)
    title.append("  ")
    title.append_text(build_badge(BETA_LABEL))
    title.no_wrap = True
    title.overflow = "crop"

    lines: list = [Text(""), title, Text("")]
    lines.append(build_reveal_subtitle(spec.subtitle, sub_reveal, pad=PAD + "  "))
    lines.append(Text(""))
    lines.append(Text(PAD + spec.headline, style="bold white", justify="left"))
    lines.append(Text(""))
    for line in spec.body:
        lines.append(Text(PAD + line, style=theme.desc, justify="left") if line else Text(""))
    lines.append(Text(""))
    lines.append(Text(PAD + "You'll only see this once — the BETA tag stays on the page.", style=theme.muted))
    lines.append(Text(""))

    btn_top, btn_mid, btn_bot = build_action_buttons(_ACTIONS, action_sel)
    lines += [btn_top, btn_mid, btn_bot]

    panel = build_page_panel(Group(*lines), theme=theme, border_style=theme.sep, height=height)
    if mode_key.startswith("agent-"):
        # The Agents modes' gate wears the robo chrome companion, like the
        # pages behind it (see MusicLive.get_renderable's _duck_mascot stamp).
        panel._duck_mascot = "robo"
    return panel


def show_beta_notice(
    live,
    console,
    read_key,
    frame_time,
    supports_timeout,
    *,
    mode_key: str,
) -> bool:
    """Show the one-time beta notice; return True to enter the mode.

    Returns True immediately (rendering nothing at all) when the notice has
    already been acknowledged, so the gate is invisible after the first run.
    Back/Esc return False and record nothing.
    """
    if mode_key not in _BETA_MODES or is_beta_notice_seen(mode_key):
        return True

    logger.info("Beta notice shown for %s", mode_key)
    sel = 0
    while True:
        w, h = console.size
        panel = _build_beta_notice_screen(mode_key=mode_key, action_sel=sel, width=w, height=h)
        live.update(panel)
        # Some phase loops pass a _key() that doesn't take the kwarg — same
        # TypeError fallback the export picker and the phase loops use.
        try:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
        except TypeError:
            k = read_key()
        # "" is a timeout tick (or a consumed mouse event), not a keypress.
        if not k:
            continue

        clicked = parse_click(k)
        if clicked is not None:
            idx = button_click(console, panel, clicked[0], clicked[1], _ACTIONS)
            if idx is None:
                continue  # clicked off the button row
            sel = idx
            k = "enter"

        if k == "left":
            sel = max(0, sel - 1)
        elif k == "right":
            sel = min(len(_ACTIONS) - 1, sel + 1)
        elif k in ("enter", " "):
            if sel == 0:
                logger.info("Beta notice acknowledged for %s — entering mode", mode_key)
                mark_beta_notice_seen(mode_key)
                return True
            logger.info("Beta notice declined for %s — returning to menu", mode_key)
            return False
        elif k in ("esc", "q"):
            logger.info("Beta notice dismissed for %s (esc) — returning to menu", mode_key)
            return False
