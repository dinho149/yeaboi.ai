"""Screen builder functions for the mode selection flow.

# See docs: "Architecture" — this module contains the rendering functions
# for the mode selection, intake, offline, export, import, and delete screens.
# These are pure functions that return Rich Panel renderables — no I/O or state.
"""

from __future__ import annotations

from typing import Any

import rich.box
from rich.align import Align
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.shared._animations import COLOR_RGB, shimmer_style
from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import PAD
from yeaboi.ui.shared._mascot import render_head
from yeaboi.ui.shared._tips import TIP_ROTATE_SECONDS

# Tip-change quack: the beak toggles for the first _QUACK_SECONDS of each tip
# window at _QUACK_HZ (so a couple of open/close cycles), then holds still.
_QUACK_SECONDS = 0.6
_QUACK_HZ = 6.0

# ---------------------------------------------------------------------------
# Mode definitions
# ---------------------------------------------------------------------------

_MODE_CARDS: list[dict[str, Any]] = [
    {
        "key": "team-analysis",
        "title": "Analysis",
        "description": "Analyse your team's board to learn velocity, estimation patterns, and delivery signals.",
        "available": True,
        "color": "rgb(100,180,100)",
    },
    {
        "key": "project-planning",
        "title": "Planning",
        "description": "Decompose your project into epics, user stories, tasks, and a sprint plan.",
        "available": True,
        "color": "rgb(110,140,220)",
    },
    {
        "key": "daily-standup",
        "title": "Standup",
        "description": "Run a daily standup: detect team activity, sprint-day confidence, and deliver a summary.",
        "available": True,
        "color": "rgb(200,100,180)",
    },
    {
        "key": "retro",
        "title": "Retro",
        "description": "Run a collaborative sprint retro: teammates add cards from a browser, then AI drafts actions.",
        "available": True,
        "color": "rgb(80,190,190)",
    },
    {
        "key": "performance",
        "title": "Performance",
        "description": "Manage each engineer: 1:1 prep, 1:1 summaries, and 6-month reviews from real delivery data.",
        "available": True,
        "color": "rgb(220,110,90)",
    },
    {
        "key": "reporting",
        "title": "Reporting",
        "description": "Summarise delivered work for the business — last sprint or last month, as slides, HTML or MD.",
        "available": True,
        "color": "rgb(140,120,230)",
    },
    {
        "key": "usage",
        "title": "Usage",
        "description": "View API token usage, session history, and cost estimates.",
        "available": True,
        "color": "rgb(220,160,60)",
    },
    {
        "key": "settings",
        "title": "Settings",
        "description": "Manage API keys, LLM provider, and board configuration.",
        "available": True,
        "color": "rgb(160,160,180)",
    },
]

# ---------------------------------------------------------------------------
# Intake mode definitions — shown when the user selects "+ New Project"
# ---------------------------------------------------------------------------

_INTAKE_CARDS: list[dict[str, Any]] = [
    {
        "key": "small_project",
        "title": "Small",
        "description": "1-2 tickets, one quick sprint. Just goal, team, and stack — no capacity planning.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
    {
        # Key stays "smart" — Large reuses the existing smart intake engine
        # (full capacity, bank-holiday, and multi-sprint planning). See intake.py.
        "key": "smart",
        "title": "Large",
        "description": "Multi-ticket epics with full capacity, bank-holiday, and multi-sprint planning.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
    {
        # Proactive intake: instead of describing a project by hand, point the
        # agent at the quarterly roadmap — it extracts candidate projects and
        # recommends Small or Large planning for each (roadmap/ package).
        "key": "roadmap",
        "title": "Roadmap",
        "description": "Point at your quarterly roadmap — AI extracts projects, ranks them, and picks Small or Large.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
    {
        "key": "offline",
        "title": "Offline",
        "description": "Export a blank template to fill in at your own pace, or import a completed one.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
]

# ---------------------------------------------------------------------------
# Offline sub-menu definitions — shown when user selects "Offline" intake
# ---------------------------------------------------------------------------

_OFFLINE_CARDS: list[dict[str, Any]] = [
    {
        "key": "export",
        "title": "Export",
        "description": "Save a blank template to scrum-questionnaire.md — fill it in at your own pace.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
    {
        "key": "import",
        "title": "Import",
        "description": "Load a completed questionnaire and jump straight to review.",
        "available": True,
        "color": "rgb(70,100,180)",
    },
]

_PAD = PAD  # alias for backward compatibility within this module

# Minimum terminal the welcome screen needs to show everything (all mode rows,
# the selected description, and the bottom hints) without clipping. Below either
# dimension the loop shows the "too small" duck instead (see
# :func:`_build_too_small_screen`). Tunable.
_MIN_WIDTH = 84
_MIN_HEIGHT = 32

# The bottom-right duck companion + its speech-bubble tip need extra room: the
# bubble reserves a right-hand lane, so the longest mode title must still fit to
# its left. Only shown above this (wider) threshold; between _MIN_* and here the
# menu renders full-width with the tip pinned at the bottom as before.
_COMPANION_MIN_WIDTH = 108
_COMPANION_MIN_HEIGHT = 32
_COMPANION_COLS = 36  # right-hand lane width (bubble + duck)

# ---------------------------------------------------------------------------
# Rendering helpers — mode selection
# ---------------------------------------------------------------------------


# Diagonal intro sweep: a character on absolute menu-row R and title-column C is
# revealed once the sweep front passes (R * _SWEEP_ROW_WEIGHT + C). A larger
# weight makes the front more vertical (top items lead by more); smaller makes it
# a flatter left-to-right curtain. This is the inverse of the splash crumble.
_SWEEP_ROW_WEIGHT = 4.0


def mode_title_widths() -> list[int]:
    """Block-font column width of every mode title, index-aligned to _MODE_CARDS.

    The staggered intro reveal uses these to know when each title is fully wiped
    in (see the reveal loop in :mod:`yeaboi.ui.mode_select`).
    """
    return [max(len(line) for line in render_ascii_text(mode["title"])) for mode in _MODE_CARDS]


def _build_mode_row(
    mode: dict[str, Any],
    *,
    selected: bool,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    override_style: str = "",
    desc_width: int | None = None,
    sweep_front: float | None = None,
    row_base: int = 0,
) -> list:
    """Render a mode as ASCII art title + optional description underneath.

    Returns a list of Rich renderables (1–3 items depending on state).
    desc_reveal: float — the fractional part fades in the next character for
        a smoother typewriter effect (e.g. 5.4 = 5 solid chars + 1 at 40% opacity).
    sweep_front / row_base — the diagonal intro reveal. Each block-font row at
        absolute menu-row ``row_base + r`` shows only the columns whose diagonal
        coordinate (row*_SWEEP_ROW_WEIGHT + col) is behind ``sweep_front``, so the
        whole menu wipes in as one coherent top-left → bottom-right sweep. None
        shows the full title. Both block-font lines are always present (just
        truncated), so the row height never changes.
    """
    available = mode["available"]
    color = mode["color"]
    lines = render_ascii_text(mode["title"])
    if sweep_front is not None:
        lines = [line[: max(0, int(sweep_front - (row_base + r) * _SWEEP_ROW_WEIGHT))] for r, line in enumerate(lines)]

    rendered = Text(justify="left")

    if override_style:
        rendered.append(_PAD + lines[0] + "\n", style=override_style)
        rendered.append(_PAD + lines[1], style=override_style)
    elif selected and available:
        total = max(len(lines[0]), len(lines[1]))
        rendered.append(_PAD)
        for i, ch in enumerate(lines[0]):
            rendered.append(ch, style=shimmer_style(color, i, total, shimmer_tick))
        rendered.append("\n" + _PAD)
        for i, ch in enumerate(lines[1]):
            rendered.append(ch, style=shimmer_style(color, i, total, shimmer_tick))
    elif selected and not available:
        rendered.append(_PAD + lines[0] + "\n", style="rgb(90,90,100)")
        rendered.append(_PAD + lines[1], style="rgb(90,90,100)")
    else:
        # Unselected: use a muted but visible version of the mode's accent color
        r, g, b = COLOR_RGB.get(color, (100, 100, 120))
        _dim_r = max(40, r // 2)
        _dim_g = max(40, g // 2)
        _dim_b = max(40, b // 2)
        _unsel_style = f"rgb({_dim_r},{_dim_g},{_dim_b})"
        rendered.append(_PAD + lines[0] + "\n", style=_unsel_style)
        rendered.append(_PAD + lines[1], style=_unsel_style)

    items: list = [rendered]

    # Always reserve space for description on the selected item to prevent
    # layout jumps when switching selection.
    if selected:
        desc_text = Text(justify="left")
        if desc_reveal > 0:
            desc_full = mode["description"]
            # Clip to a single line: a wrapped continuation loses the _PAD indent
            # and adds an unaccounted row that pushes the bottom content past the
            # panel. desc_width is the character budget for the text (excludes _PAD).
            if desc_width is not None and len(desc_full) > desc_width:
                desc_full = desc_full[: max(1, desc_width - 1)].rstrip() + "…"
            solid_count = int(desc_reveal)
            frac = desc_reveal - solid_count  # 0.0–1.0 fade for next char

            # Fully revealed characters
            solid = desc_full[:solid_count]

            if available:
                desc_text.append(_PAD + solid, style="white")
                # Sub-character fade: partially reveal the next character
                if frac > 0 and solid_count < len(desc_full):
                    gray = int(255 * frac)
                    desc_text.append(desc_full[solid_count], style=f"rgb({gray},{gray},{gray})")
            else:
                desc_text.append(_PAD + solid, style="rgb(70,70,80)")

            if not available and solid_count >= len(desc_full):
                desc_text.append("  (coming soon)", style="rgb(60,60,70)")

        items.append(Text(""))
        items.append(desc_text)

    return items


# Colour anchors for the tip cross-fade. Each is (background, full) — the tip
# lerps from the near-black background up to its full colour by tip_brightness(),
# so tips dissolve in and out instead of snapping.
_TIP_BG = (28, 28, 34)
_TIP_BODY = (198, 198, 208)  # soft grey-white for the tip text
_TIP_DOT_DIM = (70, 70, 82)  # inactive position dots (matches the app's hollow ○)
_TIP_DOT_ON = (226, 186, 96)  # warm accent for the active dot
_TIP_KEY = (210, 210, 220)  # the "t" keycap glyph


def _build_tip_rows(shimmer_tick: float, *, tip_offset: int = 0) -> list[Text]:
    """Build the bottom tip block: a rotating, cross-fading tip + a control row.

    Returns two centred rows so the mode list above stays vertically stable
    whether tips are on or off. The tip fades in and out via ``tip_brightness``
    (see README: "Architecture" — shared UI layer).

    ``tip_offset`` is the manual browse shift (bumped by the [ / ] keys); it moves
    through the list while auto-rotation keeps running (see :func:`resolve_index`).
    A ``NEW`` badge is prefixed for freshly-shipped features, and the current
    tip's mode (when it maps to a home card) gets a ``g open`` jump affordance.

    When tips are hidden, both rows aren't blank: the second keeps a quiet
    ``t show tips`` hint so the feature is always discoverable/recoverable.
    """
    from yeaboi.config import is_tips_enabled
    from yeaboi.ui.shared._animations import lerp_color
    from yeaboi.ui.shared._tips import resolve_index, tip_at, tip_brightness

    if not is_tips_enabled():
        # Persistent, quiet affordance so a user who pressed `t` can turn tips
        # back on — otherwise hidden tips are undiscoverable.
        show_hint = Text(justify="center")
        show_hint.append("t", style=f"bold rgb({_TIP_KEY[0]},{_TIP_KEY[1]},{_TIP_KEY[2]})")
        show_hint.append(
            " show tips", style=f"rgb({_TIP_DOT_DIM[0] + 45},{_TIP_DOT_DIM[1] + 45},{_TIP_DOT_DIM[2] + 45})"
        )
        return [Text(""), show_hint]

    idx = resolve_index(shimmer_tick, tip_offset)
    tip = tip_at(idx)
    b = tip_brightness(shimmer_tick)

    body_style = lerp_color(b, _TIP_BG, _TIP_BODY)

    # Row 1 — an optional NEW badge, then the tip, faded toward full body colour.
    tip_line = Text(justify="center")
    if tip.is_new:
        tip_line.append(" NEW ", style=f"bold {lerp_color(b, _TIP_BG, _TIP_DOT_ON)}")
        tip_line.append("  ")
    tip_line.append(tip.text, style=body_style)

    # Row 2 — quiet keycap control hints. No position indicator: an ambient,
    # auto-rotating tip doesn't need one, and it kept the row cluttered. Each
    # hint pairs the literal key with its action word (e.g. "[ prev"), matching
    # across the row so the real keys are unmistakable.
    dot_dim = lerp_color(b, _TIP_BG, _TIP_DOT_DIM)
    key_style = f"bold {lerp_color(b, _TIP_BG, _TIP_KEY)}"

    def _hint(key: str, label: str, *, gap: str = "      ") -> None:
        if control.plain:
            control.append(gap)
        control.append(key, style=key_style)
        control.append(f" {label}", style=dot_dim)

    control = Text(justify="center")
    # Browse the tips manually with the [ and ] keys (rotation keeps running).
    _hint("[", "prev", gap="")
    _hint("]", "next", gap="    ")
    # Jump-into-feature — only when this tip maps to a selectable mode card. Key
    # is `g` (Enter is already bound to the *selected* card, not this tip).
    if tip.mode_key is not None:
        _hint("g", "open")
    _hint("t", "hide")

    return [tip_line, control]


def _build_version_row(width: int) -> Text:
    """Build the bottom-left version hint: current version + changelog keycap.

    Sits as the last interior row of the mode screen — bottom-left, opposite the
    music bar (which lives on the Panel's bottom *border*, right-aligned). When
    the background PyPI check has found a newer release, the row grows into an
    upgrade advisory with the exact command to run. Reads the check state lazily
    (like ``_build_tip_rows`` reads tips config) so no call site changes and
    tests can monkeypatch ``yeaboi.update_check.get_update_status``.
    """
    from yeaboi.update_check import get_update_status

    status = get_update_status()
    dim = f"rgb({_TIP_DOT_DIM[0]},{_TIP_DOT_DIM[1]},{_TIP_DOT_DIM[2]})"
    accent = f"rgb({_TIP_DOT_ON[0]},{_TIP_DOT_ON[1]},{_TIP_DOT_ON[2]})"
    key_style = f"bold rgb({_TIP_KEY[0]},{_TIP_KEY[1]},{_TIP_KEY[2]})"

    row = Text(justify="left")
    row.append(f"v{status['current']}", style="rgb(120,120,140)")
    if status["update_available"]:
        row.append(" → ", style=dim)
        row.append(f"v{status['latest']}", style=accent)
        # On narrow terminals drop the command so the row never wraps.
        if width >= 72:
            row.append("  ·  ", style=dim)
            row.append(status["upgrade_command"], style=accent)
    row.append("  ·  ", style=dim)
    row.append("c", style=key_style)
    row.append(" changelog", style=dim)
    row.append("  ·  ", style=dim)
    row.append("f", style=key_style)
    row.append(" feedback", style=dim)
    row.append("  ·  ", style=dim)
    row.append("a", style=key_style)
    row.append(" all tips", style=dim)
    return row


def _build_mode_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible: list[int] | None = None,
    fade_style: str = "",
    fade_indices: list[int] | None = None,
    selected_style: str = "",
    tip_offset: int = 0,
    sweep_front: float | None = None,
) -> Panel:
    """Build the full-screen mode selection layout.

    sweep_front: optional diagonal intro-reveal front (see _build_mode_row). None
    → every title fully shown. All titles share one front, so the menu wipes in as
    a single coherent top-left → bottom-right sweep.
    """
    show = visible if visible is not None else list(range(len(_MODE_CARDS)))
    fading = fade_indices or []

    # Decide the companion up front so the mode description can be clipped to the
    # (narrower) left-column width when the duck lane is present — keeping it on
    # one line so the layout height stays predictable.
    show_companion = width >= _COMPANION_MIN_WIDTH and height >= _COMPANION_MIN_HEIGHT
    inner_w = width - 6  # borders (2) + horizontal padding (4)
    left_w = inner_w - _COMPANION_COLS if show_companion else inner_w
    desc_width = max(10, left_w - len(_PAD) - 2)

    # Mode rows
    body: list = []
    body_h = 0
    row_base = 0  # absolute menu-row of the current item's title, for the sweep
    for i, mode in enumerate(_MODE_CARDS):
        if i not in show:
            continue
        is_sel = i == selected

        if i in fading and fade_style:
            override = fade_style
        elif i == selected and selected_style:
            override = selected_style
        else:
            override = ""

        items = _build_mode_row(
            mode,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
            override_style=override,
            desc_width=desc_width,
            sweep_front=sweep_front,
            row_base=row_base,
        )
        body.extend(items)
        item_rows = 2 + (2 if is_sel else 0)
        body_h += item_rows
        row_base += item_rows
        if i < show[-1]:
            body.append(Text(""))
            body_h += 1
            row_base += 1

    # Discoverability tip. _build_tip_rows returns two rows: [tip text, controls].
    # On wide terminals the tip *text* moves into the duck's speech bubble
    # (bottom-right) and only the control hints stay pinned at the bottom; on
    # narrower terminals both rows stay pinned at the bottom as before.
    tip_rows = _build_tip_rows(shimmer_tick, tip_offset=tip_offset)

    # Bottom-left version hint (+ upgrade advisory when a newer release exists),
    # opposite the music bar on the border below it.
    version_row = _build_version_row(width)

    # The duck quacks when a new tip appears: his beak toggles open/closed a few
    # times over the first _QUACK_SECONDS of each tip window (tips rotate every
    # TIP_ROTATE_SECONDS). shimmer_tick is the continuous animation clock.
    _tw = shimmer_tick % TIP_ROTATE_SECONDS
    beak_open = _tw < _QUACK_SECONDS and int(_tw * _QUACK_HZ) % 2 == 1

    if show_companion:
        # Only the modes and the duck are column-split; the control + version rows
        # span the FULL width underneath (their original alignment, no wrap). The
        # duck bottom-aligns inside the grid so it perches just above those rows.
        inner_h = height - 4
        grid_h = max(0, inner_h - 2)  # reserve the control + version rows below
        mid_top = max(0, (grid_h - body_h) // 2)
        mid_bot = max(0, grid_h - body_h - mid_top)
        left_col = Group(
            *[Text("") for _ in range(mid_top)],
            *body,
            *[Text("") for _ in range(mid_bot)],
        )
        # Table.grid is a borderless fixed-column splitter: mode list keeps its
        # width, the duck + speech bubble get a reserved right-hand lane.
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(width=_COMPANION_COLS)
        grid.add_row(left_col, _build_companion(tip_rows[0], beak_open=beak_open))
        body_renderable: RenderableType = Group(grid, tip_rows[1], version_row)
    else:
        inner_h = height - 4
        body_area = max(0, inner_h - len(tip_rows) - 1)
        mid_top = max(0, (body_area - body_h) // 2)
        mid_bot = max(0, body_area - body_h - mid_top)
        content = Group(
            *[Text("") for _ in range(mid_top)],
            *body,
            *[Text("") for _ in range(mid_bot)],
            *tip_rows,
            version_row,
        )
        body_renderable = content

    return Panel(
        body_renderable,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_companion(tip_line: Text, *, beak_open: bool = False) -> RenderableType:
    """Bottom-right idle duck (facing left, toward the menu) with the current tip
    in a speech bubble above it.

    ``tip_line`` is the tip text from :func:`_build_tip_rows` (may be blank when
    tips are hidden — then only the duck shows). The bubble uses a plain, static
    copy of the tip: the per-frame cross-fade is dropped (it flickers in a box)
    and any leading emoji is stripped (a wide glyph in a bordered Panel breaks the
    border). ``beak_open`` opens his bill for a quack when a new tip appears.
    Bottom-aligned so it sits in the corner regardless of terminal height.
    """
    # Duck faces left so he looks toward the mode list rather than the wall.
    # Otherwise static (no bob): the up/down breathing shifted both the duck and
    # the bubble, which read as jitter — the only motion is the tip-change quack.
    duck = Group(
        render_head(0, flip=True, beak_open=beak_open),
        Text("chilling", style="rgb(120,130,140)", justify="center"),
    )
    parts: list[RenderableType] = []

    tip = tip_line.plain.strip()
    while tip and not (tip[0].isascii() and tip[0].isalnum()):  # drop a leading emoji/glyph
        tip = tip[1:]
    tip = tip.strip()
    if tip:
        bubble = Panel(
            Text(tip, style="rgb(198,198,208)", justify="left"),
            box=rich.box.ROUNDED,
            border_style="rgb(90,100,110)",
            padding=(0, 1),
            width=_COMPANION_COLS - 2,
        )
        # A short diagonal tail centred over the duck (which is centred in the
        # lane), so the bubble visibly points down at him.
        tail = Text(" " * (_COMPANION_COLS // 2 - 1) + "╲", style="rgb(90,100,110)")
        parts = [bubble, tail]
    parts.append(Align.center(duck))
    return Align.center(Group(*parts), vertical="bottom")


def _build_too_small_screen(width: int, height: int) -> Panel:
    """Guard screen shown when the terminal is below :data:`_MIN_WIDTH` /
    :data:`_MIN_HEIGHT` — the duck asks the user to size up so the welcome
    screen can show everything without clipping.
    """
    rows: list[RenderableType] = []
    if height >= 12:  # only seat the duck when there's vertical room for it
        rows.extend([Align.center(render_head(0)), Text("")])
    rows.extend(
        [
            Align.center(Text("your terminal's a bit cramped", style="bold rgb(226,186,96)")),
            Align.center(
                Text(f"give me at least {_MIN_WIDTH} × {_MIN_HEIGHT} to stretch out", style="rgb(198,198,208)")
            ),
            Align.center(Text(f"(you're at {width} × {height})", style="rgb(120,130,140)")),
        ]
    )
    return Panel(
        Align.center(Group(*rows), vertical="middle"),
        border_style="rgb(226,186,96)",
        box=rich.box.ROUNDED,
        expand=True,
        height=max(1, height),
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Rendering helpers — slide transition
# ---------------------------------------------------------------------------


def _build_slide_frame(
    mode: dict[str, Any],
    *,
    top_offset: int,
    width: int = 80,
    height: int = 24,
    style: str = "",
) -> Panel:
    """Render a mode title at a given vertical offset inside the frame.

    Used to animate the Planning title sliding from center to top.
    The description is intentionally not shown — it disappears on selection.
    top_offset: number of blank lines above the title (0 = pinned at top).
    """
    lines = render_ascii_text(mode["title"])
    title_style = style or "bold white"

    rendered = Text(justify="left")
    rendered.append(_PAD + lines[0] + "\n", style=title_style)
    rendered.append(_PAD + lines[1], style=title_style)

    inner_h = height - 4
    block_h = 2  # title(6) only — description is not shown during slide
    below = max(0, inner_h - top_offset - block_h)

    content = Group(
        *[Text("") for _ in range(top_offset)],
        rendered,
        *[Text("") for _ in range(below)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
