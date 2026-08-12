"""The chat page builder — one screen for the whole planning conversation.

Follows the mandatory page structure from the tui-standards skill: blank /
title / blank / status strip / blank / viewport / composer / hint, rooted in
build_page_panel (enforced by tests/unit/test_screen_backgrounds.py).

Layout is a centered reading column: chat content is capped at ~110 columns
and centered on wide terminals so the conversation reads like a chat instead
of edge-to-edge text; at ≤ ~120 columns the margin degenerates to the house
PAD and the page looks like every other left-gutter screen. The viewport
slices pre-wrapped, column-relative transcript lines (see ChatTranscript's
per-message caches), prepends the centering margin per visible row, and
pairs them with an always-visible scrollbar rail in a borderless two-column
Table (the standup-hub device); geometry is handed back to the driver via
publish_geometry so follow-the-bottom pinning can never diverge from what is
displayed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from io import StringIO

import rich.box
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.session.screens._screens import _planning_title
from yeaboi.ui.session.screens._screens_input import _image_hint, _voice_hint
from yeaboi.ui.shared._animations import loading_border_color
from yeaboi.ui.shared._components import (
    PAD,
    PLANNING_THEME,
    build_key_hints,
    build_page_panel,
    build_progress_dots,
    build_scrollbar,
)
from yeaboi.ui.shared._scroll import publish_geometry
from yeaboi.ui.shared._voice_input import input_box_title

from ._commands import SlashCommand
from ._composer import NEWLINE_KEY, ChatComposer
from ._transcript import ChatTranscript

_COL_W_MAX = 110
_COMPOSER_MAX_ROWS = 6
_MENU_MAX_ROWS = 8

# The plan's journey, pinned under the title so the user always knows where
# they are. "Build" covers the analysis→epics→stories→tasks generation run —
# the subtitle carries the fine-grained [n/6] detail.
_CHAT_STAGES = ["Describe", "Questions", "Review", "Epic", "Build", "Sprints"]

_EXAMPLE_TEXT = (
    "Example: \"We're building a mobile app for restaurant reservations. "
    "The team is 4 developers, we use React Native and Node.js, and we need "
    'to launch an MVP in 3 months."'
)

_TIPS_PAIRS = [
    ("/help", "all commands"),
    ("⌃V", "paste a screenshot"),
    ("/small /large", "set plan size"),
    ("␣␣", "double-tap Space to speak"),
    ("/form", "fill it out as a form"),
    (NEWLINE_KEY, "new line"),
    ("Ctrl+U", "clear the box"),
    ("/finish", "answer the rest with defaults (Esc stops)"),
    ("/export", "save plan + chat"),
    ("/questions", "see what I'll ask"),
    ("/summary", "your answers so far"),
]

# Rendered once per column width — static copy, never per-frame work.
_tips_cache: dict[int, list[Text]] = {}


# Page-panel border (2) + padding (4): the cells the hint row loses to chrome.
_HINT_CHROME = 6
# "   Esc Esc leave" — reserved out of the budget, never dropped.
_ESC_TAIL_CELLS = 16


def _hint_cells(pairs: list[tuple[str, str]], extras: str) -> int:
    """Rendered width of a hint row, counted arithmetically.

    Integer maths only: this runs inside every frame at 30fps, so measuring by
    building Rich objects would be per-frame work for a static string.
    """
    total = sum(len(key) + 1 + len(label) for key, label in pairs) + 3 * max(0, len(pairs) - 1)
    if extras:
        total += 2 + Text(extras).cell_len
    return total


def _fit_hint(pairs: list[tuple[str, str]], extras: str, avail: int) -> tuple[list[tuple[str, str]], str]:
    """Drop the lowest-value hint segments until the row fits ``avail`` cells.

    Rich ellipsizes from the right, which amputates whichever hints happen to be
    last — below 120 columns that was "Esc Esc leave", so the escape hatch was
    invisible on a normal terminal while the row still ended mid-word. Dropping
    whole segments keeps the row honest: what is shown is shown completely, and
    the controls drawer (panel._hint_tab) still lists every one of them.

    Sacrifice order, cheapest first: the screenshot hint (also in the composer's
    title chip and /help), "/ commands" (typing "/" opens the menu by itself),
    "PgUp/PgDn scroll" (the wheel does it), "Ctrl+U clear" (in /help, and
    recoverable), then the menu's own keys. Never dropped: Enter send, the
    newline key, and the Esc tail the caller appends.

    The list has to reach the menu pairs: with a choices menu up — the normal
    intake state — dropping only the first three still overflows 80 columns,
    and what gets amputated is the newline key this hint exists to teach.
    """
    budget = avail - _ESC_TAIL_CELLS
    if _hint_cells(pairs, extras) <= budget:
        return pairs, extras
    if extras:
        extras = ""
        if _hint_cells(pairs, extras) <= budget:
            return pairs, extras
    for victim in ("/", "PgUp/PgDn", "Ctrl+U", "Space", "↑/↓"):
        if _hint_cells(pairs, extras) <= budget:
            break
        pairs = [pair for pair in pairs if pair[0] != victim]
    return pairs, extras


@dataclass
class ChoiceRows:
    """Inline choice options drawn under the newest bubble.

    options: (label, checked) — checked marks multi-select toggles and the
    single-select pre-selection. highlight is the ❯ row; the driver moves it
    only while the composer is empty (one unambiguous arrow-key rule).

    auto_submit: a bare digit keypress picks and submits that row in one
    stroke. Only for menus whose labels are commands (the size question, the
    review verdict) — never for data-entry questions, where a typed "12"
    must stay free text.
    """

    options: list[tuple[str, bool]] = field(default_factory=list)
    highlight: int = 0
    multi: bool = False
    auto_submit: bool = False


@dataclass
class PipelineProgress:
    """Live stage checklist drawn under the transcript while the plan builds.

    Like ChoiceRows this renders as extra viewport lines recomputed per frame
    (the pipeline loops already paint 30fps), so the spinner and elapsed
    counters never freeze and the transcript's per-message caches are never
    touched. stages: (label, "done"|"active"|"pending").
    """

    stages: list[tuple[str, str]] = field(default_factory=list)
    step: int = 0
    total: int = 6
    run_started: float = 0.0
    active_started: float = 0.0
    active_node: str = ""  # driver bookkeeping: reset active_started on change


_SPINNER_GLYPHS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _fmt_elapsed(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _progress_rows(progress: PipelineProgress, theme) -> list[Text]:
    """The checklist rows: ✓ done, animated spinner + elapsed on the active
    stage, dim ○ pending, and a [n/6] · total footer."""
    now = time.monotonic()
    rows: list[Text] = [Text("")]
    for label, status in progress.stages:
        row = Text("  ", no_wrap=True, overflow="crop")
        if status == "done":
            row.append("✓ ", style=f"bold {theme.accent_bright}")
            row.append(label, style=theme.accent)
        elif status == "active":
            glyph = _SPINNER_GLYPHS[int(now * 10) % len(_SPINNER_GLYPHS)]
            row.append(glyph + " ", style=f"bold {theme.accent_bright}")
            row.append(label, style="bold white")
            row.append("  " + _fmt_elapsed(now - progress.active_started), style="dim")
        else:
            row.append("○ ", style="dim")
            row.append(label, style="dim")
        rows.append(row)
    footer = Text("  ", no_wrap=True, overflow="crop")
    footer.append(f"[{progress.step}/{progress.total}]", style=f"bold {theme.accent}")
    footer.append(" · total " + _fmt_elapsed(now - progress.run_started), style="dim")
    rows.append(footer)
    return rows


_DUCK_LANE = 16  # chrome duck width + right margin (see _music_bar._DUCK_W)
_BUBBLE_RESERVE = 19  # speech-bubble chrome (7) + minimum readable text (12)


def _column_metrics(width: int) -> tuple[int, int]:
    """(column width, left margin) for the centered reading column.

    inner_w is the panel content area minus the scrollbar rail; the column
    keeps ≥ 8 spare columns so the margin never collapses below the house PAD.
    The corner duck overlays the page's right edge, so the column shrinks and
    leans left rather than sliding its right edge (the composer corner, the
    user bubbles) underneath him; wide terminals re-center naturally.
    """
    inner_w = max(48, width - 7)
    col_w = max(40, min(_COL_W_MAX, inner_w - 8))
    max_col = width - _DUCK_LANE - len(PAD) - 4
    # Leave the duck a speech lane wherever the column can afford it: without
    # the extra 19 columns (bubble chrome + minimum text) his quips only fit
    # past ~180 cols and every bubble silently vanishes on normal terminals.
    # Below the 76-col floor the reading column wins — no bubble, and the
    # transcript whispers carry everything durable.
    if max_col - _BUBBLE_RESERVE >= 76:
        max_col -= _BUBBLE_RESERVE
    if max_col >= 40:
        col_w = min(col_w, max_col)
    margin_cap = width - _DUCK_LANE - col_w - 4
    if margin_cap - _BUBBLE_RESERVE >= len(PAD):
        margin_cap -= _BUBBLE_RESERVE  # stop centering just short of his lane
    margin = max(len(PAD), min((inner_w - col_w) // 2, margin_cap))
    return col_w, margin


def _stage_dot(stage: str, graph_state: dict) -> int:
    """Map the driver's stage word to a progress-dots index."""
    if stage == "intake":
        if not graph_state.get("messages"):
            return 0  # still describing the project
        if graph_state.get("pending_review") == "project_intake":
            return 2  # reviewing the intake summary
        return 1
    return {"capacity": 2, "epic": 3, "pipeline": 4, "review": 4, "chat": len(_CHAT_STAGES)}.get(stage, 0)


def _placeholder(stage: str, graph_state: dict, choices: ChoiceRows | None) -> str:
    """Ghost text for the empty composer — tells the user what fits here now."""
    if choices is not None and choices.options:
        if stage == "intake" and not graph_state.get("messages"):
            if len(choices.options) >= 3:
                # The greeting's third row is the form preference.
                return "Press 1 or 2 to size it, 3 for a form — or just describe your project…"
            return "Press 1 or 2 to size it — or just describe your project…"
        return "Pick with ↑/↓ — or just type your answer…"
    if stage == "intake" and not graph_state.get("messages"):
        return "Describe your project — a few sentences is enough…"
    if stage == "intake":
        return "Type an answer — /skip /defaults /form /finish all work…"
    if stage in ("review", "epic"):
        return "accept — or tell me what to change…"
    if stage == "chat":
        return "Ask anything about the plan — /export saves it…"
    return "Type a message…"


def _tips_card_lines(col_w: int, theme) -> list[Text]:
    """The getting-started card: example description + keycap cheatsheet.

    Shown until the first user message. Rendered as transcript-adjacent lines
    so it scrolls with the conversation, and cached per width — the content
    is static.
    """
    if col_w in _tips_cache:
        return _tips_cache[col_w]
    from yeaboi.ui.session._utils import _render_to_lines

    card_w = min(col_w, 78)
    grid = Table.grid(padding=(0, 3), pad_edge=False)
    grid.add_column()
    grid.add_column()
    for i in range(0, len(_TIPS_PAIRS), 2):
        row = _TIPS_PAIRS[i : i + 2]
        # Pair up, but never silently drop a tip when the list is odd — zip()
        # used to, which is a quiet way to lose the one you just added.
        grid.add_row(build_key_hints([row[0]]), build_key_hints([row[1]]) if len(row) > 1 else Text(""))
    card = Panel(
        Group(Text(_EXAMPLE_TEXT, style="dim italic"), Text(""), grid),
        title=f"[bold {theme.accent}] Getting started [/]",
        title_align="left",
        border_style=theme.sep,
        box=rich.box.ROUNDED,
        padding=(0, 2),
        width=card_w,
    )
    buf = Console(file=StringIO(), width=card_w, force_terminal=True, color_system="truecolor")
    lines = [Text.from_ansi(line) for line in _render_to_lines(buf, card, card_w)]
    _tips_cache[col_w] = lines
    return lines


def build_chat_screen(
    transcript: ChatTranscript,
    composer: ChatComposer,
    graph_state: dict,
    *,
    width: int = 80,
    height: int = 24,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    processing: bool = False,
    tick: float = 0.0,
    shimmer_tick: float | None = None,
    notice: str = "",
    choices: ChoiceRows | None = None,
    command_menu: list[SlashCommand] | None = None,
    subtitle: str = "",
    stage: str = "",
    stream_text: str | None = None,
    border_override: str | None = None,
    console: Console | None = None,
    progress: PipelineProgress | None = None,
) -> Panel:
    """Build the chat page.

    stage: the driver's _stage() word — drives the progress dots and the
    composer placeholder. stream_text: partial assistant reply mid-stream
    (rendered as the tail bubble with a ▌ cursor). border_override + notice
    double as the voice indicator surface for record_voice_input's
    render_status callback.
    """
    theme = PLANNING_THEME
    render_console = console or Console(file=StringIO(), width=width, force_terminal=True, color_system="truecolor")
    col_w, margin = _column_metrics(width)
    indent = " " * margin

    title = _planning_title(shimmer_tick)

    # -- status strip: journey dots + fine-grained detail --------------------
    if stage:
        strip = build_progress_dots(_CHAT_STAGES, _stage_dot(stage, graph_state), pad=PAD, theme=theme)
        if subtitle:
            strip.append("   " + subtitle, style="dim")
        strip.no_wrap = True
        strip.overflow = "ellipsis"
    else:
        strip = Text(PAD + (subtitle or "Plan your project in chat — /help lists commands"), style="dim")

    # -- composer panel (variable height) ----------------------------------
    box_w = col_w
    rows, _cursor_row_idx, cursor_col = composer.visual_rows(box_w - 8, _COMPOSER_MAX_ROWS)

    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    for idx, (chunk, is_cursor_row) in enumerate(rows):
        if idx:
            input_content.append("\n")
        prefix = "  "
        if not is_cursor_row or processing:
            input_content.append(prefix + chunk, style="bold white")
            continue
        col = min(cursor_col, len(chunk))
        input_content.append(prefix + chunk[:col], style="bold white")
        cursor_char = chunk[col] if col < len(chunk) else " "
        input_content.append(cursor_char, style="reverse bold white")
        input_content.append(chunk[col + 1 :], style="bold white")
    if composer.is_empty():
        if processing:
            input_content = Text("  Working…", style="dim", justify="left", no_wrap=True, overflow="crop")
        else:
            # Ghost placeholder: block cursor + stage-appropriate prompt.
            input_content = Text("  ", justify="left", no_wrap=True, overflow="crop")
            input_content.append(" ", style="reverse bold white")
            input_content.append(" " + _placeholder(stage, graph_state, choices), style="dim italic")

    if border_override is not None:
        border_color = border_override
    elif processing:
        border_color = loading_border_color(tick)
    else:
        border_color = "white"

    input_box = Panel(
        input_content,
        title=input_box_title("Message", box_w),
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )
    box_h = len(rows) + 4  # content rows + borders + vertical padding

    # -- /-menu rows above the composer ------------------------------------
    menu_lines: list[Text] = []
    if command_menu:
        for i, cmd in enumerate(command_menu[:_MENU_MAX_ROWS]):
            line = Text(indent)
            line.append(f"/{cmd.name}", style=f"bold {theme.accent_bright}" if i == 0 else theme.accent)
            line.append(f"  {cmd.help}", style="dim")
            menu_lines.append(line)

    # -- hint line ----------------------------------------------------------
    # Keycap-styled row for the eye; the plain ·-joined form feeds the
    # controls drawer via panel._hint_tab (draw_controls_pocket parses it).
    pairs: list[tuple[str, str]] = []
    if command_menu:
        pairs.append(("Tab", "complete"))
    if choices is not None and choices.options:
        pairs.append(("↑/↓", "choose"))
        # Arrows belong to the menu while one is up, so name the keys that
        # still reach the transcript — a menu usually sits under something
        # the answer depends on (the intake summary is 30 rows of it).
        pairs.append(("PgUp/PgDn", "scroll"))
        if choices.multi:
            pairs.append(("Space", "toggle"))
    pairs += [("Enter", "send"), (NEWLINE_KEY, "newline"), ("Ctrl+U", "clear"), ("/", "commands")]
    extras = (_voice_hint() + _image_hint()).strip()
    # The drawer gets every pair whatever the width; only the inline row sheds.
    plain_hint = " · ".join(f"{k} {label}" for k, label in pairs)
    if extras:
        plain_hint += " " + extras
    plain_hint += " · Esc leave"
    if notice:
        hint = Text(indent + notice, style="bold white", justify="left", no_wrap=True, overflow="ellipsis")
    else:
        shown_pairs, extras = _fit_hint(pairs, extras, max(20, width - margin - _HINT_CHROME))
        hint = build_key_hints(shown_pairs, pad=indent)
        if extras:
            hint.append("  " + extras.removeprefix("· "), style="rgb(110,110,125)")
        hint.append("   Esc Esc", style="bold rgb(210,210,220)")
        hint.append(" leave", style="rgb(110,110,125)")
        hint.no_wrap = True
        hint.overflow = "ellipsis"

    # -- viewport geometry ---------------------------------------------------
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + strip + blank
    composer_area_h = 1 + len(menu_lines) + box_h + 1  # blank + menu + box + hint
    viewport_h = max(3, inner_h - header_h - composer_area_h)

    # -- transcript lines (cached) + tips card + inline choices --------------
    lines = transcript.lines(col_w, graph_state, render_console, theme=theme, stream_text=stream_text)
    if not transcript.has_user_message():
        lines.extend(_tips_card_lines(col_w, theme))
        lines.append(Text(""))
    if choices is not None and choices.options:
        for i, (label, checked) in enumerate(choices.options):
            row = Text("  ")
            row.append("❯ " if i == choices.highlight else "  ", style=f"bold {theme.accent_bright}")
            if choices.multi:
                row.append("[x] " if checked else "[ ] ", style=theme.accent if checked else "dim")
            row.append(f"{i + 1}. {label}", style="bold white" if i == choices.highlight else "white")
            if not choices.multi and checked and i != choices.highlight:
                row.append("  (suggested)", style="dim")
            lines.append(row)
        lines.append(Text(""))
    if progress is not None and progress.stages:
        lines.extend(_progress_rows(progress, theme))
        lines.append(Text(""))

    total = len(lines)
    max_offset = max(0, total - viewport_h)
    scroll_offset = max(0, min(scroll_offset, max_offset))
    publish_geometry(scroll_meta, max_offset, viewport_h)

    # Two-column borderless table: the reading column (margin prepended per
    # row) plus a permanently visible scrollbar rail hugging the right edge.
    visible = lines[scroll_offset : scroll_offset + viewport_h]
    body_rows: list[Text] = []
    for i in range(viewport_h):
        row = Text(indent, no_wrap=True, overflow="crop")
        if i < len(visible):
            row.append_text(visible[i].copy())
        body_rows.append(row)
    rail = build_scrollbar(viewport_h, total, scroll_offset, max_offset, always_show=True)
    viewport = Table(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
    viewport.add_column(ratio=1)
    viewport.add_column(width=1)
    viewport.add_row(Group(*body_rows), rail or Text(""))

    content = Group(
        Text(""),
        title,
        Text(""),
        strip,
        Text(""),
        viewport,
        Text(""),
        *menu_lines,
        Padding(input_box, (0, 0, 0, margin)),
        hint,
    )

    panel = build_page_panel(content, theme=theme, height=height)
    # Hand the hint to the chrome so the controls drawer ("c") shows a
    # keybinding table parsed from the same ·-separated segments.
    panel._hint_tab = Text(PAD + plain_hint, style="dim", justify="left")
    return panel
