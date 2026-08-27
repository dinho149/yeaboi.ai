"""The Niko page's screen builder — one conversation, one panel.

Follows the mandatory page structure (title / subtitle / viewport / composer /
buttons, rooted in ``build_page_panel``) and borrows the planning chat's
:class:`ChatComposer` for the input buffer, which is pure editing state with no
terminal in it.

Deliberately its own transcript renderer rather than the planning chat's: that
one draws stage rails, question cards, epic tables and review gates, none of
which exist here. Niko's transcript is three kinds of row — what you asked, what
it read, and what it said — and a builder that renders three things is the
smaller thing to keep correct.
"""

from __future__ import annotations

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.session.chat._composer import ChatComposer
from yeaboi.ui.shared._components import (
    NIKO_THEME,
    PAD,
    build_action_buttons,
    build_page_panel,
    build_scrollbar,
    calc_viewport,
    niko_title,
)
from yeaboi.ui.shared._voice_input import input_box_title

#: Rows the composer may grow to before it scrolls internally.
COMPOSER_MAX_ROWS = 4

#: Buttons under the composer. "Ask" is what Enter already does — it is here so
#: the row is navigable by mouse and by someone reading rather than typing.
NIKO_ACTIONS = ["Ask", "New", "Back"]

_WHO = {"user": "You", "assistant": "Niko"}


def wrap_rows(text: str, width: int) -> list[str]:
    """Wrap one paragraph-ish blob to ``width``, keeping its own line breaks."""
    from textwrap import wrap

    out: list[str] = []
    for line in (text or "").splitlines() or [""]:
        out.extend(wrap(line, max(8, width)) or [""])
    return out


def transcript_rows(turns: list[dict], width: int) -> list[Text]:
    """The conversation as flat, styled rows.

    ``turns`` is a list of ``{"role", "text", "tools"}`` dicts — the shape the
    page keeps, and the shape a replayed conversation reduces to, so the live
    view and a saved snapshot render through one function.
    """
    theme = NIKO_THEME
    rows: list[Text] = []
    for turn in turns:
        role = turn.get("role", "assistant")
        if rows:
            rows.append(Text(""))
        who = Text(PAD)
        who.append(_WHO.get(role, role), style=f"bold {theme.accent_bright}" if role == "assistant" else "bold white")
        rows.append(who)
        for tool in turn.get("tools") or []:
            line = Text(PAD + "  ")
            name = tool.get("name", "")
            if tool.get("ok", True):
                line.append(f"· read {name}", style=theme.dim)
            else:
                line.append(f"· {name} had nothing to read", style=theme.warn)
            rows.append(line)
        body = turn.get("text", "")
        if body:
            style = theme.desc if role == "assistant" else "white"
            rows.extend(Text(PAD + "  " + row, style=style) for row in wrap_rows(body, width - len(PAD) - 4))
        route = turn.get("route", "")
        if route:
            hop = Text(PAD + "  ")
            hop.append(f"→ that lives at {route}", style=theme.accent)
            rows.append(hop)
    return rows


def _empty_rows(chips: list[dict], width: int) -> list[Text]:
    """The opening state: who Niko is, and three things worth asking."""
    theme = NIKO_THEME
    rows = [Text(""), Text(PAD + "Hey — I'm Niko, the duck's assistant.", style=f"bold {theme.accent_bright}")]
    rows.extend(
        Text(PAD + row, style=theme.desc)
        for row in wrap_rows(
            "I can tell you what yeaboi does, read your own delivery and agent data, and point you at "
            "the right screen. I can't change anything — that's on purpose.",
            width - len(PAD) - 2,
        )
    )
    if chips:
        rows.append(Text(""))
        rows.append(Text(PAD + "TRY ASKING", style=f"bold {theme.muted}"))
        for chip in chips:
            line = Text(PAD + "  ")
            line.append("· ", style=theme.accent)
            line.append(chip.get("label", ""), style="white")
            rows.append(line)
    return rows


def _composer_panel(composer: ChatComposer, width: int, *, busy: bool) -> tuple[Panel, int]:
    """The input box, and how many rows it costs."""
    rows, _cursor_row, cursor_col = composer.visual_rows(width - 8, COMPOSER_MAX_ROWS)
    content = Text(justify="left", no_wrap=True, overflow="crop")
    if busy:
        content = Text("  Thinking…", style="dim", justify="left", no_wrap=True, overflow="crop")
    elif composer.is_empty():
        content = Text("  ", justify="left", no_wrap=True, overflow="crop")
        content.append(" ", style="reverse bold white")
        content.append(" Ask Niko anything…", style="dim italic")
    else:
        for index, (chunk, is_cursor_row) in enumerate(rows):
            if index:
                content.append("\n")
            if not is_cursor_row:
                content.append("  " + chunk, style="bold white")
                continue
            col = min(cursor_col, len(chunk))
            content.append("  " + chunk[:col], style="bold white")
            content.append(chunk[col] if col < len(chunk) else " ", style="reverse bold white")
            content.append(chunk[col + 1 :], style="bold white")
    panel = Panel(
        content,
        title=input_box_title("Ask", width),
        title_align="left",
        border_style=NIKO_THEME.accent if busy else "white",
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=width,
    )
    return panel, (1 if busy or composer.is_empty() else len(rows)) + 4


def _build_niko_screen(
    state: dict,
    *,
    scroll_offset: int = 0,
    action_sel: int = 0,
    width: int = 100,
    height: int = 40,
    shimmer_tick: float | None = None,
) -> Panel:
    """Build the Niko conversation page.

    ``state`` carries ``turns``, ``chips``, ``composer``, ``busy``, ``streaming``
    (the partial answer), ``actions`` (defaults to :data:`NIKO_ACTIONS`),
    ``read_only`` (hides the composer) and ``message`` (a one-line notice).
    """
    theme = NIKO_THEME
    composer: ChatComposer = state["composer"]
    busy = bool(state.get("busy"))
    read_only = bool(state.get("read_only"))
    col_w = max(40, width - 8)

    box, box_h = (None, 0) if read_only else _composer_panel(composer, col_w, busy=busy)
    viewport_h = max(3, calc_viewport(height, header_h=6, action_h=4 + box_h))

    turns = list(state.get("turns") or [])
    if busy and state.get("streaming"):
        turns = [*turns, {"role": "assistant", "text": state["streaming"], "tools": state.get("streaming_tools") or []}]
    rows = transcript_rows(turns, col_w) if turns else _empty_rows(state.get("chips") or [], col_w)

    total = len(rows)
    max_scroll = max(0, total - viewport_h)
    offset = max(0, min(scroll_offset, max_scroll))
    visible = rows[offset : offset + viewport_h]
    visible += [Text("")] * (viewport_h - len(visible))
    scrollbar = build_scrollbar(viewport_h, total, offset, max_scroll)
    if scrollbar is not None:
        frame = Table(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        frame.add_column(ratio=1)
        frame.add_column(width=1)
        frame.add_row(Group(*visible), scrollbar)
        viewport: object = frame
    else:
        viewport = Group(*visible)

    subtitle = Text(PAD + (state.get("message") or "Read-only — Niko looks things up and points the way"), style="dim")
    btn_top, btn_mid, btn_bot = build_action_buttons(state.get("actions") or NIKO_ACTIONS, action_sel)

    rest = [Text(""), box] if box is not None else [Text("")]
    return build_page_panel(
        Group(
            Text(""),
            niko_title(shimmer_tick, width=width),
            Text(""),
            subtitle,
            Text(""),
            viewport,
            *rest,
            btn_top,
            btn_mid,
            btn_bot,
        ),
        theme=theme,
        height=height,
    )
