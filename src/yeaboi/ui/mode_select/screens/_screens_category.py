"""The landing split — Solo vs Team vs Agents — shown between the splash and a menu.

Rounded world-cards side by side, each carrying its mascot (the OG duck for
Solo, the duck trio for Team, the robotic duck for Agents), a solid-accent block title, a
verb line, and an accent-middot capability list. The page's one question lives
in the outer frame's border, not floating in space. Under the cards, the front
page: one story at a time from the same paper the desktop home prints, so the
split keeps the reader up to date without taking the choice off the screen.

The signature is that the selected card is *alive*: accent-bright border, a
dark accent-tinted interior, and the mascot's wing flapping on the animation
clock — the resting card sits still and dim. Choosing a side wakes that world
up, so the selection state needs no extra chrome. Pure builders only — the run
loop lives in :mod:`yeaboi.ui.mode_select` (Phase 0 of ``select_mode``).

Geometry note: the builder and :func:`category_at_pos` share one helper
(:func:`_category_bounds`) instead of hand-mirroring each other's maths — the
lesson of ``mode_at_row``'s lock-step comment, applied from the start.

# See docs: "TUI system" — shared component structure
"""

from __future__ import annotations

import re
import textwrap
from typing import TYPE_CHECKING, Any

from rich.align import Align
from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.beta import BETA_LABEL
from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import (
    AGENTS_THEME,
    LANDING_DETAIL_RESTING,
    LANDING_DETAIL_SELECTED,
    LANDING_HEADING_STYLE,
    LANDING_VERB_RESTING,
    LANDING_VERB_SELECTED,
    SOLO_THEME,
    TEAM_THEME,
    build_badge,
    build_page_panel,
)
from yeaboi.ui.shared._mascot import FRAMES, flock_cells, flock_head_cells, full_cells, mini_cells

if TYPE_CHECKING:
    from yeaboi.news.edition import Page

_CATEGORY_CARDS: list[dict[str, Any]] = [
    {
        "key": "solo",
        "title": "Solo",
        "verb": "Run your own show",
        "capabilities": ["planning", "standups", "analysis", "reports"],
        "color": SOLO_THEME.accent,
        "bright": SOLO_THEME.accent_bright,
        "dim": "rgb(95,80,45)",  # the resting shade — no theme slot for it
        "tint": "rgb(30,25,15)",  # card-bg convention: a dark shade of the accent
        "mascot": "duck",
        # A whole beta world, not just beta modes: the chip rides the card.
        "badge": BETA_LABEL,
    },
    {
        "key": "team",
        "title": "Team",
        "verb": "Run your team's scrum",
        "capabilities": ["planning", "standups", "retros", "poker", "reviews"],
        "color": TEAM_THEME.accent,
        "bright": TEAM_THEME.accent_bright,
        "dim": "rgb(55,95,58)",
        "tint": "rgb(17,28,20)",
        "mascot": "flock",
    },
    {
        "key": "agents",
        "title": "Agents",
        "verb": "Watch your AI agents work",
        "capabilities": ["cost", "recoverable spend", "daily digests", "security posture"],
        "color": AGENTS_THEME.accent,
        "bright": AGENTS_THEME.accent_bright,
        "dim": "rgb(50,88,115)",
        "tint": "rgb(15,24,32)",
        "mascot": "robo",
        "badge": BETA_LABEL,
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
    """Put every mascot back where it started (tests, and a fresh entrance)."""
    _WALK.clear()


def category_index(key: str) -> int:
    """Position of the category *key* in the cards, 0 for an unknown one."""
    return next((i for i, card in enumerate(_CATEGORY_CARDS) if card["key"] == key), 0)


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
_GUTTER_COLS = 1  # breathing room between the cards — one column, three-up is tight
_STRIP_ROWS = 3  # the front page: kicker row, headline, byline — pinned above the hint band
_EMPTY = "Nothing to read yet."

# The quiet layer around the living cards.
_HEADING = "Who are we working with today?"
_REST_BORDER = "rgb(58,62,72)"


def _category_bounds(width: int) -> list[tuple[int, int]]:
    """The 1-based terminal column span (start, end) of each world-card.

    The panel splits its inner width (frame border + padding are 3 columns a
    side) into equal card columns separated by ``_GUTTER_COLS``; the last card
    absorbs the division remainder. Shared by the builder's grid and the click
    hit-test so they cannot drift.
    """
    n = len(_CATEGORY_CARDS)
    inner_w = width - 6  # borders (2) + horizontal padding (4)
    card_w = max(20, (inner_w - (n - 1) * _GUTTER_COLS) // n)
    bounds: list[tuple[int, int]] = []
    start = 4  # 1-based: border (1) + left padding (2) put the first card at col 4
    for i in range(n):
        w = card_w if i < n - 1 else max(card_w, inner_w - (card_w + _GUTTER_COLS) * (n - 1))
        bounds.append((start, start + w - 1))
        start += w + _GUTTER_COLS
    return bounds


def _capability_rows(card: dict[str, Any], *, selected: bool, budget: int) -> list[Text]:
    """The middot capability list as up to two reserved, centred rows.

    The middots carry the card's accent while the words stay muted — the list
    reads as structure, not a sentence, and never ellipsizes mid-word.
    """
    words_style = LANDING_DETAIL_SELECTED if selected else LANDING_DETAIL_RESTING
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
    # Three-up at narrow widths the full trace (34 cells) no longer fits the
    # card, so the arrived state drops a tier: the mini trace, unshaded on the
    # near ground — the walk still reads through size, light and position.
    full_tier = half_width >= 34  # the card pads vertically only
    if True:
        step = _walk_step(card["key"], selected)
        anim = int(shimmer_tick * 8) % FRAMES
        if step >= _MASCOT_WALK_STEPS - 1:
            # Arrived: the biggest trace that fits, wings going, on the near ground.
            if card["mascot"] == "flock":
                cells = flock_cells(anim) if full_tier else flock_head_cells(anim)
            else:
                cells = (
                    full_cells(anim, mascot=card["mascot"]) if full_tier else mini_cells(anim, mascot=card["mascot"])
                )
            mascot: RenderableType = Group(
                *[Text("") for _ in range(_MASCOT_ROWS - len(cells))],
                *_shaded_rows(cells, 1.0),
            )
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

    verb = Text(card["verb"], justify="center", style=LANDING_VERB_SELECTED if selected else LANDING_VERB_RESTING)
    verb.no_wrap = True
    verb.overflow = "ellipsis"

    # A beta world wears the chip on the reserved row between wordmark and
    # verb — the row is blank either way, so the card height never moves.
    # Dim on the resting card, matching the mode-row treatment; it enters with
    # the wordmark it annotates, not before it.
    chip = build_badge(card["badge"], dim=not selected) if card.get("badge") and intro >= 0.5 else Text(" ")

    inner_budget = max(16, half_width - 2)  # card padding
    caps = _capability_rows(card, selected=selected, budget=inner_budget)

    body = Group(
        Text(""),
        Align.center(mascot),
        Text(""),
        Align.center(title),
        Align.center(chip),
        Align.center(verb),
        *[Align.center(row) for row in caps],
    )
    # No frame, and no background wash. Boxes side by side made the choice
    # look like a form; the mascot walking forward is what marks the live one.
    # Vertical padding only — three-up the columns are the scarce dimension,
    # and every row centres itself. The card stays the same height either way —
    # ``test_card_height_matches_the_layout_constant`` pins it to _CARD_ROWS.
    return Padding(body, (1, 0))


def _joined(parts: list[tuple[str, str]]) -> Text:
    """One centred row of ``part · part``, empties dropped; a single space when nothing is left."""
    row = Text(justify="center", no_wrap=True, overflow="ellipsis")
    for part, style in parts:
        if not part:
            continue
        if row.plain:
            row.append(" · ", style=_REST_BORDER)
        row.append(part, style=style)
    return row if row.plain else Text(" ")


def _strip_rows(page: Page | None, *, edition: str = "", inside: str = "") -> list[Text]:
    """The front page's rows: kicker · counter · edition line, the headline, the byline.

    Always exactly ``_STRIP_ROWS`` Texts, a blank one a single space, so the
    cards above never move as the paper arrives or the page turns.
    """
    if page is None:
        empty = Text(_EMPTY if edition else " ", justify="center", style=LANDING_DETAIL_RESTING)
        return [_joined([(edition, LANDING_DETAIL_RESTING)]), empty, Text(" ")]
    resting = LANDING_DETAIL_RESTING
    top = _joined([(page.kicker, resting), (page.counter, resting), (edition, resting)])
    headline = Text(page.item.title, justify="center", style=LANDING_VERB_SELECTED, no_wrap=True, overflow="ellipsis")
    foot = _joined([(page.byline, LANDING_DETAIL_SELECTED), (page.read, resting), (inside, resting)])
    return [top, headline, foot]


def _build_category_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    intro: float = 1.0,
    page: Page | None = None,
    edition: str = "",
    inside: str = "",
) -> Panel:
    """Build the full-screen landing split, the front page's strip under the cards."""
    bounds = _category_bounds(width)
    widths = [end - start + 1 for start, end in bounds]
    halves = [
        _card_half(card, selected=i == selected, shimmer_tick=shimmer_tick, intro=intro, half_width=widths[i])
        for i, card in enumerate(_CATEGORY_CARDS)
    ]

    # Explicit column widths from the shared bounds — a ratio grid rounds its
    # own way, and the click hit-test must land where the cards actually are.
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
    for key, label in (
        ("←/→", "switch"),
        ("enter", "choose"),
        ("[/]", "turn"),
        ("o", "open"),
        ("i", "inside"),
        ("q", "quit"),
    ):
        if hint.plain:
            hint.append("   ")
        hint.append(key, style="bold rgb(210,210,220)")
        hint.append(f" {label}", style="rgb(70,70,82)")

    inner_h = height - 3  # top border + top pad + bottom border (no bottom pad)
    body_h = _CARD_ROWS
    body_area = max(0, inner_h - _HINT_ROWS - _STRIP_ROWS)
    mid_top = max(0, (body_area - body_h) // 2)
    mid_bot = max(0, body_area - body_h - mid_top)
    # The strip enters with the wordmarks; before that its rows are held blank.
    strip = _strip_rows(page, edition=edition, inside=inside) if intro >= 0.5 else [Text(" ")] * _STRIP_ROWS

    content = Group(
        *[Text("") for _ in range(mid_top)],
        grid,
        *[Text("") for _ in range(mid_bot)],
        *strip,
        Text(""),
        hint,
        # Two rows for the chrome's footer note: it is drawn over the last three
        # rendered rows, two of which are content, and the hint has to clear them.
        Text(""),
        Text(""),
    )
    # The page's one question rides the outer frame's border — structural,
    # never a floating line of copy. It stays on the TOP border here: the chrome
    # that carries a note on the bottom one is not in this tree.
    panel = build_page_panel(
        content,
        height=height,
        padding=(1, 2, 0, 2),
        title=Text(f" {_HEADING} ", style=LANDING_HEADING_STYLE),
        title_align="center",
    )
    panel._no_back_hint = True  # the landing screen's Esc is quit, not "go back"
    # The screen already features both mascots — a third duck in the chrome
    # corner is a crowd, so opt out (same stamp the too-small guard uses).
    panel._no_companion_duck = True
    # And no music bar: this screen is one question with two answers, and the
    # player is furniture for the pages you settle into.
    panel._no_music = True
    return panel


def category_at_pos(width: int, height: int, *, row: int, col: int) -> int | None:
    """Map a 1-based terminal click to a category index (0=solo, 1=team, 2=agents).

    Any click inside the content band counts for the card region it lands in —
    the cards are the whole screen, so precision clicking isn't required: a
    gutter (and the frame padding either side) counts for the nearest card. The
    top border row, the front page's strip and the hint band return None.
    """
    if row <= 2 or row >= height - _HINT_ROWS - _STRIP_ROWS:
        return None
    bounds = _category_bounds(width)
    for i, (_start, end) in enumerate(bounds[:-1]):
        if col <= end + _GUTTER_COLS:
            return i
    return len(bounds) - 1


def strip_at_pos(height: int, *, row: int) -> bool:
    """Whether a 1-based terminal row lands on the front page's strip."""
    return height - _HINT_ROWS - _STRIP_ROWS <= row < height - _HINT_ROWS
