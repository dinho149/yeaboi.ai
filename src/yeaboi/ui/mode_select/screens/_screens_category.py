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

import re
import textwrap
from typing import Any

from rich.align import Align
from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import AGENTS_THEME, HUMANS_THEME, build_page_panel
from yeaboi.ui.shared._mascot import FRAMES, mini_cells, render_full

_CATEGORY_CARDS: list[dict[str, Any]] = [
    {
        "key": "humans",
        "title": "Humans",
        "verb": "Run your team's scrum",
        "capabilities": ["planning", "standups", "retros", "poker", "reviews"],
        "color": HUMANS_THEME.accent,
        "bright": HUMANS_THEME.accent_bright,
        "dim": "rgb(55,95,58)",  # the resting shade — no theme slot for it
        "tint": "rgb(17,28,20)",  # card-bg convention: a dark shade of the accent
        "mascot": "duck",
    },
    {
        "key": "agents",
        "title": "Agents",
        "verb": "Watch your AI agents work",
        "capabilities": ["cost", "daily digests", "security posture"],
        "color": AGENTS_THEME.accent,
        "bright": AGENTS_THEME.accent_bright,
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
_TITLE_ROWS = 2  # the block wordmark's own height, reserved while it is late
# The resting mascot's trace, and how far down the frame he stands. Fewer rows
# above than below, so the smaller duck sits high in the card the way something
# further away sits higher on the ground.
_MASCOT_MINI_ROWS = 12
_MASCOT_BACK_ABOVE = 2
# How many paces the walk takes. The last one is the arrival — the swap to the
# full trace — so the small duck is seen taking _MASCOT_WALK_STEPS - 1 of them.
_MASCOT_WALK_STEPS = 4
# Paces per render. The screen redraws on the shared frame clock, so this is
# the walk's speed: a whole pace every four frames or so.
_WALK_RATE = 0.15
# How much of his own colour the furthest-back duck keeps. The rest is the page
# behind him, which is what a thing in shadow at a distance looks like.
# The resting duck's idle: a one-row hop every few beats, on a clock slow
# enough to read as "alive back there" rather than as a second thing moving.
_MASCOT_HOP_HZ = 2
_MASCOT_HOP_CYCLE = 6
_MASCOT_SHADE = 0.28
_SHADE_TOWARD = (16, 16, 20)  # NEUTRAL_BG, as a triple to mix with

# How far each card's mascot has walked, 0 (back of the shot) to 1 (arrived).
# Module state for the reason the section rule's position is: the card is built
# from scratch every frame and has nowhere else to keep it.
_WALK: dict[str, float] = {}


def reset_category_walk() -> None:
    """Put both mascots back where they started (tests, and a fresh entrance)."""
    _WALK.clear()


def _shaded_rows(cells, level: float) -> list[Text]:
    """The sprite's cells as Texts, its colours dimmed toward the page.

    Depth is light as well as size: at the back of the shot he is in shadow,
    and each pace forward brings him further into it. ``level`` is 1.0 for his
    own colours and _MASCOT_SHADE for the furthest back.
    """
    rows: list[Text] = []
    for row in cells:
        line = Text(no_wrap=True, overflow="crop")
        for glyph, style in row:
            line.append(glyph, style=_shade_style(style, level) if style else None)
        rows.append(line)
    return rows


def _shade_style(style: str, level: float) -> str:
    """Dim every ``rgb(...)`` in a cell style toward the page background."""

    def _one(match: re.Match[str]) -> str:
        r, g, b = (int(v) for v in match.group(1, 2, 3))
        br, bg, bb = _SHADE_TOWARD
        return "rgb({},{},{})".format(*(round(c * level + t * (1.0 - level)) for c, t in ((r, br), (g, bg), (b, bb))))

    return re.sub(r"rgb\((\d+),(\d+),(\d+)\)", _one, style)


def _walk_step(key: str, selected: bool) -> int:
    """Advance this mascot toward or away from the front, and say which pace.

    Quantised on purpose: a duck gliding smoothly forward is a duck being
    dragged, and there are only two traces to draw him at anyway.
    """
    target = 1.0 if selected else 0.0
    at = _WALK.get(key, target)  # first sight of a card is wherever it belongs
    at = min(1.0, at + _WALK_RATE) if at < target else max(0.0, at - _WALK_RATE)
    _WALK[key] = at
    return min(_MASCOT_WALK_STEPS - 1, int(at * _MASCOT_WALK_STEPS))


_CARD_ROWS = 28
_HINT_ROWS = 4  # blank + the key-hint line + the two rows the footer note lands on
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
    # Full-body mascot — the hero, and the card's depth cue. The selected one
    # has walked forward: the full trace, wings flapping on the shared clock.
    # The resting one has walked back into the screen — the smaller trace, held
    # still, standing higher in the frame, because distance puts a thing further
    # up the ground plane as well as making it smaller. Both occupy the same
    # _MASCOT_ROWS, so the card never changes height as the selection moves.
    # The mascot leads the entrance; the name follows him (see the title below).
    if True:
        step = _walk_step(card["key"], selected)
        if step >= _MASCOT_WALK_STEPS - 1:
            # Arrived: the full trace, wings going, feet on the near ground.
            mascot: RenderableType = render_full(int(shimmer_tick * 8) % FRAMES, mascot=card["mascot"])
        else:
            # Still coming: the smaller trace, standing on the same ground the
            # full one does — his feet are on the title either way, and only
            # the size and the light say how far back he is. Wings beat once
            # per pace, and the shadow lifts as he nears the front.
            # A hop, on a slow clock of its own: one row up for a beat, and his
            # wings with it. Enough that he is alive back there, not enough to
            # pull the eye off the card that is actually selected.
            # Mid-stride he is off the ground. Walking is an arc, not a slide:
            # standing still he only hops now and then, but while he is crossing
            # he lifts on every other pace and plants on the ones between.
            if step > 0:
                lift = 1 if step % 2 else 0
            else:
                beat = int(shimmer_tick * _MASCOT_HOP_HZ) % _MASCOT_HOP_CYCLE
                lift = 1 if beat == 0 else 0
            beat = int(shimmer_tick * _MASCOT_HOP_HZ) % _MASCOT_HOP_CYCLE
            hop = lift
            above = _MASCOT_ROWS - _MASCOT_MINI_ROWS - hop
            mascot = Group(
                *[Text("") for _ in range(above)],
                *_shaded_rows(
                    mini_cells(step * 2 + beat, mascot=card["mascot"]),
                    _MASCOT_SHADE + (1.0 - _MASCOT_SHADE) * (step / max(1, _MASCOT_WALK_STEPS - 1)),
                ),
                *[Text("") for _ in range(hop)],
            )
    else:
        mascot = Group(*[Text("") for _ in range(_MASCOT_ROWS)])

    # Block title in SOLID accent — bright on the living card, dim on the
    # resting one. No shimmer: mid-sweep it read as patchy dots.
    # The duck arrives first and the name follows him. It was the other way
    # round — the wordmark landing on an empty card, with the mascot appearing
    # under a name already there — which reads as the art being late.
    title_lines = render_ascii_text(card["title"]) if intro >= 0.5 else [""] * _TITLE_ROWS
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
    # No frame, and no background wash. Two boxes side by side made the choice
    # look like a form; the mascot walking forward is what marks the live one.
    # The padding keeps the geometry the border used to occupy, so the card is
    # the same height either way — ``test_card_height_matches_the_layout_constant``
    # pins that to _CARD_ROWS.
    return Padding(body, (1, 2))


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
        # Two rows for the chrome's footer note: it is drawn over the last three
        # rendered rows, two of which are content, and the hint has to clear them.
        Text(""),
        Text(""),
    )
    panel = build_page_panel(
        content,
        height=height,
        padding=(1, 2, 0, 2),
    )
    panel._no_back_hint = True  # the landing screen's Esc is quit, not "go back"
    # The screen already features both mascots — a third duck in the chrome
    # corner is a crowd, so opt out (same stamp the too-small guard uses).
    panel._no_companion_duck = True
    # The question goes ON the bottom border, in the chrome's own frame shape —
    # floating a row above it, it read as one more thing on the page.
    panel._footer_note = _HEADING
    # And no music bar: this screen is one question with two answers, and the
    # player is furniture for the pages you settle into.
    panel._no_music = True
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
