"""Shared screen infrastructure and summary screen for the TUI session.

# See docs: "Architecture" — pure functions that build Rich Panel screens.
# Contains shared constants, action bar builder, and the intake summary screen.
# Input screens are in _screens_input.py, pipeline screens in _screens_pipeline.py.
"""

from __future__ import annotations

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared._animations import lerp_color as _shared_lerp_color
from yeaboi.ui.shared._animations import scrollbar_column
from yeaboi.ui.shared._components import PAD, planning_title
from yeaboi.ui.shared._scroll import publish_geometry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PAD = PAD  # alias for backward compatibility within this module

# Consistent width for all input boxes across screens (question, chat, edit).
_INPUT_BOX_W_MAX = 74

# Action button styling — rounded box-drawing buttons matching project dashboard.
# Each tuple: (accent_border_rgb, accent_label_rgb, grey_border_rgb, grey_label_rgb).
# Focused buttons interpolate from grey → accent based on fade_t (0.0–1.0).
_ACTION_COLORS: dict[str, tuple[tuple[int, int, int], ...]] = {
    "Accept": ((60, 160, 80), (80, 200, 100), (40, 50, 40), (50, 60, 50)),
    "Edit": ((100, 100, 120), (140, 140, 160), (40, 40, 50), (50, 50, 60)),
    "Regenerate": ((100, 100, 120), (140, 140, 160), (40, 40, 50), (50, 50, 60)),
    "Export": ((70, 100, 180), (100, 140, 220), (40, 40, 50), (50, 50, 60)),
    "Jira": ((70, 100, 180), (100, 140, 220), (40, 40, 50), (50, 50, 60)),
    "Azure DevOps": ((70, 100, 180), (100, 140, 220), (40, 40, 50), (50, 50, 60)),
}
_ACTION_BTN_W = 12  # minimum character width per button
_ACTION_BTN_GAP = 2  # gap between buttons


def _lerp_rgb(t: float, a: tuple[int, int, int], b: tuple[int, int, int]) -> str:
    """Interpolate between two RGB tuples, returning an rgb(...) style string.

    Delegates to shared lerp_color() — previously a local duplicate.
    """
    return _shared_lerp_color(t, a, b)


def _build_action_bar(
    actions: list[str],
    selected: int,
    fades: list[float] | None = None,
) -> tuple[Text, Text, Text]:
    """Build three Text lines (top/mid/bot) for rounded action buttons.

    # See docs: "Architecture" — button rendering matches project dashboard style
    # from mode_select/_project_cards.py. Uses Unicode box-drawing characters
    # (╭╮│╰╯─) to create rounded-corner button appearance.
    # fades: per-button 0.0–1.0 interpolation from grey to accent colour.
    # When a button gains focus fade_t animates 0→1; when it loses focus, 1→0.
    """
    if fades is None:
        fades = [1.0 if i == selected else 0.0 for i in range(len(actions))]

    top = Text(justify="center")
    mid = Text(justify="center")
    bot = Text(justify="center")

    for i, action in enumerate(actions):
        if i > 0:
            top.append(" " * _ACTION_BTN_GAP)
            mid.append(" " * _ACTION_BTN_GAP)
            bot.append(" " * _ACTION_BTN_GAP)

        # Button width adapts to label length (minimum _ACTION_BTN_W)
        inner = max(_ACTION_BTN_W - 2, len(action) + 2)

        colors = _ACTION_COLORS.get(action, _ACTION_COLORS["Edit"])
        t = fades[i] if i < len(fades) else 0.0
        # Interpolate between grey and accent colour
        border = _lerp_rgb(t, colors[2], colors[0])
        if t > 0.5:
            label_s = f"bold {_lerp_rgb(t, colors[3], colors[1])}"
        else:
            label_s = _lerp_rgb(t, colors[3], colors[1])

        # Center label inside button
        pad_l = (inner - len(action)) // 2
        pad_r = inner - len(action) - pad_l
        centered = " " * pad_l + action + " " * pad_r

        top.append("\u256d" + "\u2500" * inner + "\u256e", style=border)
        mid.append("\u2502", style=border)
        mid.append(centered, style=label_s)
        mid.append("\u2502", style=border)
        bot.append("\u2570" + "\u2500" * inner + "\u256f", style=border)

    return top, mid, bot


# ---------------------------------------------------------------------------
# Shared header — "Planning" ASCII title pinned at top of every screen
# ---------------------------------------------------------------------------


def _planning_title(shimmer_tick: float | None = None) -> Text:
    """Return the Planning ASCII title styled with the brand colour.

    Delegates to shared planning_title() — previously a local duplicate.
    Pass ``shimmer_tick`` (a monotonic elapsed clock) to animate the travelling
    highlight; ``None`` (default) renders the solid static title.
    """
    return planning_title(shimmer_tick)


# ---------------------------------------------------------------------------
# Screen builders — Intake Summary + Review (Phase C)
# ---------------------------------------------------------------------------


def _build_summary_screen(
    summary_lines: list[str],
    scroll_offset: int,
    menu_selected: int,
    *,
    width: int = 80,
    height: int = 24,
    status_msg: str = "",
    btn_fades: list[float] | None = None,
    shimmer_tick: float | None = None,
    scroll_meta: dict | None = None,
) -> Panel:
    """Build the scrollable intake summary screen with Accept/Edit/Export action bar.

    summary_lines: pre-rendered text lines from render_intake_summary().
    scroll_offset: first visible line index.
    menu_selected: 0=Accept, 1=Edit, 2=Export.
    status_msg: optional status message below the action bar.
    shimmer_tick: if set, animates the title's travelling highlight.
    """
    title = _planning_title(shimmer_tick)
    sub = Text(_PAD + "Review your answers", style="dim", justify="left")

    inner_h = height - 4
    header_h = 10  # blank + title(6) + blank + subtitle + blank(padding)
    action_h = 4  # blank + 3 button lines
    viewport_h = max(3, inner_h - header_h - action_h)

    # Clamp scroll offset
    max_scroll = max(0, len(summary_lines) - viewport_h)
    scroll_offset = max(0, min(scroll_offset, max_scroll))
    publish_geometry(scroll_meta, max_scroll, viewport_h)

    body: list = []

    # Build scrollbar column for the viewport
    sb = scrollbar_column(viewport_h, len(summary_lines), scroll_offset)

    # Content width for right-aligning the scrollbar.  Pad every line to
    # the same width so the scrollbar forms a consistent column on the right.
    content_w = width - 8  # panel border(2) + padding(4) + scrollbar(2)

    # Visible lines — use from_ansi() since summary_lines contain ANSI escape codes
    # from Rich's truecolor rendering (produced by _render_to_lines).
    visible = summary_lines[scroll_offset : scroll_offset + viewport_h]
    for i, line in enumerate(visible):
        row = Text.from_ansi("  " + line, justify="left")
        if sb[i]:
            # Pad to fixed width so scrollbar aligns in a column
            pad_needed = max(0, content_w - row.cell_len)
            row.append(" " * pad_needed)
            row.append_text(Text.from_markup(sb[i]))
        body.append(row)

    # Pad remaining viewport
    for i in range(len(visible), viewport_h):
        row = Text("", justify="left")
        if sb[i]:
            row.append(" " * content_w)
            row.append_text(Text.from_markup(sb[i]))
        body.append(row)

    # Action buttons — rounded box-drawing style matching project dashboard
    body.append(Text(""))
    btn_top, btn_mid, btn_bot = _build_action_bar(["Accept", "Edit", "Export"], menu_selected, fades=btn_fades)
    body.append(btn_top)
    body.append(btn_mid)
    body.append(btn_bot)

    if status_msg:
        body.append(Text(_PAD + status_msg, style="bright_green", justify="left"))

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
