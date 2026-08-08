"""The landing split — Humans vs Agents — shown between the splash and a menu.

Two cards side by side, each topped by its mascot (the duck for Humans, the
robotic duck for Agents), a block-font title, and a one-line description. The
selected half shimmers in its category accent; the other rests dimmed. Pure
builders only — the run loop lives in :mod:`yeaboi.ui.mode_select` (Phase 0 of
``select_mode``).

Geometry note: the builder and :func:`category_at_pos` share one helper
(:func:`_category_columns`) instead of hand-mirroring each other's maths — the
lesson of ``mode_at_row``'s lock-step comment, applied from the start.

# See docs: "TUI system" — shared component structure
"""

from __future__ import annotations

from typing import Any

from rich.align import Align
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.shared._animations import COLOR_RGB, shimmer_style
from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import build_page_panel
from yeaboi.ui.shared._mascot import render_head

_CATEGORY_CARDS: list[dict[str, Any]] = [
    {
        "key": "humans",
        "title": "Humans",
        "description": "Run your team's scrum: planning, standups, retros, poker, reviews.",
        "color": "rgb(100,180,100)",
        "mascot": "duck",
    },
    {
        "key": "agents",
        "title": "Agents",
        "description": "Watch your AI agents work: cost, daily digests, security posture.",
        "color": "rgb(90,160,210)",
        "mascot": "robo",
    },
]

# Rows the card stack occupies: head (7) + gap (1) + title (2) + gap (1) + desc (1).
_CARD_ROWS = 12
_HINT_ROWS = 2  # blank + the key-hint line pinned at the bottom
_MIN_SPLIT_WIDTH = 62  # below this the two halves collide; callers keep the guard screen


def _category_columns(width: int) -> int:
    """The 1-based terminal column where the right (Agents) half begins.

    The panel splits its inner width into two equal columns; borders and padding
    are symmetric, so the midpoint of the full width is the boundary. Shared by
    the builder's grid and the click hit-test so they cannot drift.
    """
    return width // 2 + 1


def _card_half(
    card: dict[str, Any],
    *,
    selected: bool,
    shimmer_tick: float,
    intro: float,
) -> RenderableType:
    """One category's stacked half: mascot head, title, description."""
    color = card["color"]
    rgb = COLOR_RGB.get(color, (120, 120, 140))

    title_lines = render_ascii_text(card["title"])
    title = Text(justify="center")
    total = max(len(line) for line in title_lines)
    for row_i, line in enumerate(title_lines):
        if selected:
            for i, ch in enumerate(line):
                title.append(ch, style=shimmer_style(color, i, total, shimmer_tick))
        else:
            dim = f"rgb({max(40, rgb[0] // 2)},{max(40, rgb[1] // 2)},{max(40, rgb[2] // 2)})"
            title.append(line, style=dim)
        if row_i == 0:
            title.append("\n")
    title.no_wrap = True
    title.overflow = "crop"

    desc = Text(card["description"], justify="center", style="white" if selected else "rgb(110,110,125)")
    desc.no_wrap = True
    desc.overflow = "ellipsis"

    # The head fades in with the intro (drawn dim until the reveal reaches it) —
    # a cheap stand-in for the menu's diagonal sweep that keeps this screen's
    # first paint from popping.
    head = render_head(0, mascot=card["mascot"]) if intro >= 0.5 else Text("")

    return Group(
        Align.center(head),
        Text(""),
        Align.center(title),
        Text(""),
        Align.center(desc),
    )


def _build_category_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    intro: float = 1.0,
) -> Panel:
    """Build the full-screen Humans/Agents landing split."""
    halves = [
        _card_half(card, selected=i == selected, shimmer_tick=shimmer_tick, intro=intro)
        for i, card in enumerate(_CATEGORY_CARDS)
    ]

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(*halves)

    heading = Text("Who are we working with today?", justify="center", style="rgb(150,150,165)")

    hint = Text(justify="center")
    for key, label in (("←/→", "switch"), ("enter", "choose"), ("q", "quit")):
        if hint.plain:
            hint.append("   ")
        hint.append(key, style="bold rgb(210,210,220)")
        hint.append(f" {label}", style="rgb(70,70,82)")

    inner_h = height - 3  # top border + top pad + bottom border (no bottom pad)
    body_h = _CARD_ROWS + 2  # heading + blank above the grid
    body_area = max(0, inner_h - _HINT_ROWS)
    mid_top = max(0, (body_area - body_h) // 2)
    mid_bot = max(0, body_area - body_h - mid_top)

    content = Group(
        *[Text("") for _ in range(mid_top)],
        heading,
        Text(""),
        grid,
        *[Text("") for _ in range(mid_bot)],
        Text(""),
        hint,
    )
    panel = build_page_panel(content, height=height, padding=(1, 2, 0, 2))
    panel._no_back_hint = True  # the landing screen's Esc is quit, not "go back"
    return panel


def category_at_pos(width: int, height: int, *, row: int, col: int) -> int | None:
    """Map a 1-based terminal click to a category index (0=humans, 1=agents).

    Any click inside the content band counts for the half it lands in — the
    cards are the whole screen, so precision clicking isn't required. The top
    border row and the bottom hint row return None.
    """
    if row <= 2 or row >= height - 1:
        return None
    return 0 if col < _category_columns(width) else 1
