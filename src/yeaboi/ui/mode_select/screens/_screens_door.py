"""The door — Projects vs Sessions — shown after the landing split, before a menu.

Two ways to work in every world: a project, where every run inside shares
context, or a session, one run of one mode with nothing carried over. Two
cards side by side in the chosen world's accent, the page's one question on
the frame's top border. Pure builders only — the run loop lives in
:mod:`yeaboi.ui.mode_select` (Phase 0b of ``select_mode``).

Geometry note: the builder and :func:`door_at_pos` share :func:`_door_bounds`
rather than mirroring each other's maths, the same rule the landing split
follows.

# See docs: "TUI system" — shared component structure
"""

from __future__ import annotations

from typing import Any

from rich.align import Align
from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import AGENTS_THEME, SOLO_THEME, TEAM_THEME, Theme, build_page_panel

_DOOR_CARDS: list[dict[str, Any]] = [
    {
        "key": "projects",
        "title": "Projects",
        "verb": "Everything inside shares context",
        "detail": "Runs here read each other's history",
    },
    {
        "key": "sessions",
        "title": "Sessions",
        "verb": "One-off run, nothing carried over",
        "detail": "One standup, report or analysis",
    },
]

_WORLD_THEMES: dict[str, Theme] = {"solo": SOLO_THEME, "team": TEAM_THEME, "agents": AGENTS_THEME}

# Card interior rows: blank(1) + wordmark(2) + rule(1) + blank(1) + verb(1) +
# detail(1) = 7; +2 for the vertical padding = 9. Read only by this page's
# centering maths — the card Panel never carries it as height=.
_TITLE_ROWS = 2
_CARD_ROWS = 9
_HINT_ROWS = 4  # the active-project line (or its blank) + the key hints + the footer's two rows
_GUTTER_COLS = 3

_HEADING = "How do we work today?"
_HEADING_STYLE = "rgb(152,156,170)"
_TITLE_RESTING = "rgb(96,100,114)"
_VERB_SELECTED = "bold rgb(234,237,243)"
_VERB_RESTING = "rgb(128,132,146)"
_DETAIL_SELECTED = "rgb(168,172,184)"
_DETAIL_RESTING = "rgb(96,100,114)"
_ACTIVE_STYLE = "rgb(128,132,146)"


def door_index(key: str) -> int:
    """Position of the door *key* in the cards, 0 for an unknown one."""
    return next((i for i, card in enumerate(_DOOR_CARDS) if card["key"] == key), 0)


def world_theme(world: str) -> Theme:
    """The accent palette of the chosen world (Team for an unknown one)."""
    return _WORLD_THEMES.get(world, TEAM_THEME)


def _door_bounds(width: int) -> list[tuple[int, int]]:
    """The 1-based terminal column span (start, end) of each door-card.

    The panel splits its inner width (frame border + padding are 3 columns a
    side) into two equal columns separated by ``_GUTTER_COLS``; the last card
    absorbs the division remainder.
    """
    n = len(_DOOR_CARDS)
    inner_w = width - 6  # borders (2) + horizontal padding (4)
    card_w = max(20, (inner_w - (n - 1) * _GUTTER_COLS) // n)
    bounds: list[tuple[int, int]] = []
    start = 4  # 1-based: border (1) + left padding (2) put the first card at col 4
    for i in range(n):
        w = card_w if i < n - 1 else max(card_w, inner_w - (card_w + _GUTTER_COLS) * (n - 1))
        bounds.append((start, start + w - 1))
        start += w + _GUTTER_COLS
    return bounds


def _card(card: dict[str, Any], *, selected: bool, theme: Theme, shimmer_tick: float, intro: float) -> RenderableType:
    """One door-card: block wordmark, an accent rule, the verb and its detail line."""
    lines = render_ascii_text(card["title"]) if intro >= 0.5 else [""] * _TITLE_ROWS
    title = Text(
        "\n".join(lines), justify="center", style=f"bold {theme.accent_bright}" if selected else _TITLE_RESTING
    )
    title.no_wrap = True
    title.overflow = "crop"

    # The rule is the live card's marker: a slow pulse between the two accent
    # shades, so the choice reads without a frame around it.
    rule_width = max(len(line.rstrip()) for line in lines) if intro >= 0.5 else 12
    if selected:
        pulse = theme.accent_bright if int(shimmer_tick * 2) % 2 == 0 else theme.accent
        rule = Text("─" * rule_width, justify="center", style=pulse)
    else:
        rule = Text(" ")

    verb = Text(card["verb"], justify="center", style=_VERB_SELECTED if selected else _VERB_RESTING)
    verb.no_wrap = True
    verb.overflow = "ellipsis"
    detail = Text(card["detail"], justify="center", style=_DETAIL_SELECTED if selected else _DETAIL_RESTING)
    detail.no_wrap = True
    detail.overflow = "ellipsis"

    body = Group(
        Text(""),
        Align.center(title),
        Align.center(rule),
        Text(""),
        Align.center(verb),
        Align.center(detail),
    )
    return Padding(body, (1, 0))


def _build_door_screen(
    selected: int,
    *,
    world: str = "team",
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    intro: float = 1.0,
    active_name: str = "",
) -> Panel:
    """Build the full-screen door: two cards in the world's accent.

    ``active_name`` names a project that is already active — shown under the
    cards so picking Sessions is visibly what clears it.
    """
    theme = world_theme(world)
    bounds = _door_bounds(width)
    widths = [end - start + 1 for start, end in bounds]
    halves = [
        _card(card, selected=i == selected, theme=theme, shimmer_tick=shimmer_tick, intro=intro)
        for i, card in enumerate(_DOOR_CARDS)
    ]

    grid = Table.grid()
    cells: list[RenderableType] = []
    for i, w in enumerate(widths):
        if i:
            grid.add_column(width=_GUTTER_COLS)
            cells.append(Text(""))
        grid.add_column(width=w)
        cells.append(halves[i])
    grid.add_row(*cells)

    hint = Text(justify="center")
    for key, label in (("←/→", "switch"), ("enter", "choose"), ("esc", "back"), ("q", "quit")):
        if hint.plain:
            hint.append("   ")
        hint.append(key, style="bold rgb(210,210,220)")
        hint.append(f" {label}", style="rgb(70,70,82)")

    active = Text(f"Active: {active_name}", justify="center", style=_ACTIVE_STYLE) if active_name else Text("")
    active.no_wrap = True
    active.overflow = "ellipsis"

    inner_h = height - 3  # top border + top pad + bottom border (no bottom pad)
    body_area = max(0, inner_h - _HINT_ROWS)
    mid_top = max(0, (body_area - _CARD_ROWS) // 2)
    mid_bot = max(0, body_area - _CARD_ROWS - mid_top)

    content = Group(
        *[Text("") for _ in range(mid_top)],
        grid,
        *[Text("") for _ in range(mid_bot)],
        active,
        hint,
        # Two rows for the chrome's footer note, which is drawn over the last
        # three rendered rows.
        Text(""),
        Text(""),
    )
    panel = build_page_panel(
        content,
        height=height,
        padding=(1, 2, 0, 2),
        title=Text(f" {_HEADING} ", style=_HEADING_STYLE),
        title_align="center",
    )
    # Esc goes back to the split, so the back tab stays. No corner duck — the
    # page is two words and a rule — and no music bar, for the same reason the
    # split has none: nobody settles here.
    panel._no_companion_duck = True
    panel._no_music = True
    return panel


def door_at_pos(width: int, height: int, *, row: int, col: int) -> int | None:
    """Map a 1-based terminal click to a door index (0=projects, 1=sessions).

    The frame padding either side counts for the nearest card; the gutter
    between the two, the top border and the hint band at the foot are dead.
    """
    if row <= 2 or row >= height - _HINT_ROWS:
        return None
    bounds = _door_bounds(width)
    for i, (start, end) in enumerate(bounds):
        if start <= col <= end:
            return i
    if col < bounds[0][0]:
        return 0
    if col > bounds[-1][1]:
        return len(bounds) - 1
    return None
