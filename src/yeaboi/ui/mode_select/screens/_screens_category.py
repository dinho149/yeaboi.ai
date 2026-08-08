"""The landing split — Humans vs Agents — shown between the splash and a menu.

Two rounded world-cards side by side, each carrying its FULL-BODY mascot (the
duck for Humans, the robotic duck for Agents), a solid-accent block title, a
verb line, and an accent-middot capability list. The page's one question lives
in the outer frame's border, not floating in space.

The signature is that the selected card is *alive*: accent-bright border, a
dark accent-tinted interior, and the mascot's wing flapping on the animation
clock — the resting card sits still and dim. Choosing a side wakes that world
up, so the selection state needs no extra chrome. Pure builders only — the run
loop lives in :mod:`yeaboi.ui.mode_select` (Phase 0 of ``select_mode``).

Geometry note: the builder and :func:`category_at_pos` share one helper
(:func:`_category_columns`) instead of hand-mirroring each other's maths — the
lesson of ``mode_at_row``'s lock-step comment, applied from the start.

# See docs: "TUI system" — shared component structure
"""

from __future__ import annotations

import textwrap
from typing import Any

import rich.box
from rich.align import Align
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import NEUTRAL_BG, build_page_panel
from yeaboi.ui.shared._mascot import FRAMES, render_full

_CATEGORY_CARDS: list[dict[str, Any]] = [
    {
        "key": "humans",
        "title": "Humans",
        "verb": "Run your team's scrum",
        "capabilities": ["planning", "standups", "retros", "poker", "reviews"],
        "color": "rgb(100,180,100)",
        "bright": "rgb(120,215,125)",
        "dim": "rgb(55,95,58)",
        "tint": "rgb(17,28,20)",  # card-bg convention: a dark shade of the accent
        "mascot": "duck",
    },
    {
        "key": "agents",
        "title": "Agents",
        "verb": "Watch your AI agents work",
        "capabilities": ["cost", "daily digests", "security posture"],
        "color": "rgb(90,160,210)",
        "bright": "rgb(125,195,245)",
        "dim": "rgb(50,88,115)",
        "tint": "rgb(15,24,32)",
        "mascot": "robo",
    },
]

# Card interior rows: blank(1) + full-body mascot(18) + blank(1) + title(2) +
# blank(1) + verb(1) + capabilities(2 reserved) = 26; +2 borders = 28. The card
# Panel does NOT carry this as height= (that is the signature of a full-screen
# panel) — it is what the body naturally renders to, and only this page's
# vertical-centering maths reads it.
_MASCOT_ROWS = 18
_CARD_ROWS = 28
_HINT_ROWS = 2  # blank + the key-hint line pinned at the bottom
_MIN_SPLIT_WIDTH = 62  # below this the two halves collide; callers keep the guard screen
_GUTTER_COLS = 2  # breathing room between the two cards

# The quiet layer around the living cards.
_HEADING = "Who are we working with today?"
_HEADING_STYLE = "rgb(152,156,170)"
_REST_BORDER = "rgb(58,62,72)"
_VERB_SELECTED = "bold rgb(234,237,243)"
_VERB_RESTING = "rgb(128,132,146)"
_CAPS_SELECTED = "rgb(168,172,184)"
_CAPS_RESTING = "rgb(96,100,114)"


def _category_columns(width: int) -> int:
    """The 1-based terminal column where the right (Agents) half begins.

    The panel splits its inner width into two equal columns; borders and padding
    are symmetric, so the midpoint of the full width is the boundary. Shared by
    the builder's grid and the click hit-test so they cannot drift.
    """
    return width // 2 + 1


def _capability_rows(card: dict[str, Any], *, selected: bool, budget: int) -> list[Text]:
    """The middot capability list as up to two reserved, centred rows.

    The middots carry the card's accent while the words stay muted — the list
    reads as structure, not a sentence, and never ellipsizes mid-word.
    """
    words_style = _CAPS_SELECTED if selected else _CAPS_RESTING
    dot_style = card["bright"] if selected else card["dim"]
    joined = " · ".join(card["capabilities"])
    rows: list[Text] = []
    for line in textwrap.wrap(joined, max(16, budget), break_long_words=False)[:2]:
        row = Text(justify="center")
        for i, part in enumerate(line.split(" · ")):
            if i:
                row.append(" · ", style=dot_style)
            row.append(part, style=words_style)
        row.no_wrap = True
        row.overflow = "ellipsis"
        rows.append(row)
    while len(rows) < 2:
        # A space, not "": Align.center() of a truly empty Text renders ZERO
        # rows, so an empty filler would make a one-line card shorter than a
        # two-line one — and the grid would misalign the two bottom borders.
        rows.append(Text(" "))
    return rows


def _card_half(
    card: dict[str, Any],
    *,
    selected: bool,
    shimmer_tick: float,
    intro: float,
    half_width: int = 52,
) -> RenderableType:
    """One world-card: full-body mascot, block title, verb line, capabilities.

    The selected card is the animated one — its mascot's wing flaps on the
    shared animation clock; the resting card holds frame 0 with everything
    dimmed. ``intro`` < 0.5 reserves the mascot's rows without drawing him, so
    the card never changes height while the entrance settles.
    """
    color = card["color"]

    # Full-body mascot — the hero. Wing flaps only on the living (selected) card.
    if intro >= 0.5:
        frame = int(shimmer_tick * 8) % FRAMES if selected else 0
        mascot: RenderableType = render_full(frame, mascot=card["mascot"])
    else:
        mascot = Group(*[Text("") for _ in range(_MASCOT_ROWS)])

    # Block title in SOLID accent — bright on the living card, dim on the
    # resting one. No shimmer: mid-sweep it read as patchy dots.
    title_lines = render_ascii_text(card["title"])
    title_style = f"bold {card['bright']}" if selected else card["dim"]
    title = Text("\n".join(title_lines), justify="center", style=title_style)
    title.no_wrap = True
    title.overflow = "crop"

    verb = Text(card["verb"], justify="center", style=_VERB_SELECTED if selected else _VERB_RESTING)
    verb.no_wrap = True
    verb.overflow = "ellipsis"

    inner_budget = max(16, half_width - 6)  # card borders + padding
    caps = _capability_rows(card, selected=selected, budget=inner_budget)

    body = Group(
        Text(""),
        Align.center(mascot),
        Text(""),
        Align.center(title),
        Text(""),
        Align.center(verb),
        *[Align.center(row) for row in caps],
    )
    # No ``height=``: the body is a fixed 26 rows (see _CARD_ROWS), so the card
    # sizes itself and stays an *inner* Panel — a Panel carrying height= with no
    # width= is what the full-screen-background guard looks for, and this one
    # inherits the page background rather than owning one.
    # ``test_card_height_matches_the_layout_constant`` pins the two together.
    return Panel(
        body,
        box=rich.box.ROUNDED,
        expand=True,
        padding=(0, 1),
        border_style=color if selected else _REST_BORDER,
        style=f"on {card['tint']}" if selected else f"on {NEUTRAL_BG}",
        title=Text(f" {card['key']} ", style=f"bold {card['bright']}" if selected else _REST_BORDER),
        title_align="center",
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
    inner_w = width - 6  # borders (2) + horizontal padding (4)
    half_w = max(20, (inner_w - _GUTTER_COLS) // 2)
    halves = [
        _card_half(card, selected=i == selected, shimmer_tick=shimmer_tick, intro=intro, half_width=half_w)
        for i, card in enumerate(_CATEGORY_CARDS)
    ]

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(width=_GUTTER_COLS)
    grid.add_column(ratio=1)
    grid.add_row(halves[0], Text(""), halves[1])

    hint = Text(justify="center")
    for key, label in (("←/→", "switch"), ("enter", "choose"), ("q", "quit")):
        if hint.plain:
            hint.append("   ")
        hint.append(key, style="bold rgb(210,210,220)")
        hint.append(f" {label}", style="rgb(70,70,82)")

    inner_h = height - 3  # top border + top pad + bottom border (no bottom pad)
    body_h = _CARD_ROWS
    body_area = max(0, inner_h - _HINT_ROWS)
    mid_top = max(0, (body_area - body_h) // 2)
    mid_bot = max(0, body_area - body_h - mid_top)

    content = Group(
        *[Text("") for _ in range(mid_top)],
        grid,
        *[Text("") for _ in range(mid_bot)],
        Text(""),
        hint,
    )
    # The page's one question rides the outer frame's border — structural,
    # never a floating line of copy.
    panel = build_page_panel(
        content,
        height=height,
        padding=(1, 2, 0, 2),
        title=Text(f" {_HEADING} ", style=_HEADING_STYLE),
        title_align="center",
    )
    panel._no_back_hint = True  # the landing screen's Esc is quit, not "go back"
    # The screen already features both mascots — a third duck in the chrome
    # corner is a crowd, so opt out (same stamp the too-small guard uses).
    panel._no_companion_duck = True
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
