"""Screen builder functions for the mode selection flow.

# See docs: "Architecture" — this module contains the rendering functions
# for the mode selection, intake, offline, export, import, and delete screens.
# These are pure functions that return Rich Panel renderables — no I/O or state.
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

from yeaboi.ui.shared._animations import COLOR_RGB, shimmer_style
from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import PAD
from yeaboi.ui.shared._mascot import render_head, render_head_shades
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
_COMPANION_COLS = 44  # right-hand lane width (bubble + duck); wide enough for the
# tip bubble to fit the full control row (incl. `g open`) on its border.

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
    desc_max_lines: int = 1,
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

    # Reserve description space on the selected item so switching never changes the
    # row height. ``desc_max_lines`` rows are always reserved: the welcome screen
    # passes 2 so long copy wraps instead of truncating (never an ellipsis); intake
    # /offline keep the original single clipped line.
    if selected:
        desc_lines: list[Text] = [Text(justify="left") for _ in range(max(1, desc_max_lines))]
        if desc_reveal > 0:
            base = "white" if available else "rgb(70,70,80)"
            solid_count = int(desc_reveal)
            frac = desc_reveal - solid_count  # 0.0–1.0 fade for the next char
            if desc_max_lines >= 2:
                budget = max(1, desc_width) if desc_width is not None else len(mode["description"])
                wrapped = textwrap.wrap(mode["description"], budget)[: len(desc_lines)]
                consumed = 0
                for line_i, wline in enumerate(wrapped):
                    lt = desc_lines[line_i]
                    lt.append(_PAD)
                    shown = max(0, solid_count - consumed)  # chars revealed on this line
                    lt.append(wline[:shown], style=base)
                    if available and 0 <= (solid_count - consumed) < len(wline) and frac > 0:
                        gray = int(255 * frac)  # sub-char fade on the cursor's line
                        lt.append(wline[shown], style=f"rgb({gray},{gray},{gray})")
                    if not available and line_i == len(wrapped) - 1 and shown >= len(wline):
                        lt.append("  (coming soon)", style="rgb(60,60,70)")
                    consumed += len(wline)
            else:
                # Single line: clip with an ellipsis (a wrapped continuation would
                # lose the _PAD indent and add an unaccounted row).
                desc_full = mode["description"]
                if desc_width is not None and len(desc_full) > desc_width:
                    desc_full = desc_full[: max(1, desc_width - 1)].rstrip() + "…"
                lt = desc_lines[0]
                lt.append(_PAD + desc_full[:solid_count], style=base)
                if available and frac > 0 and solid_count < len(desc_full):
                    gray = int(255 * frac)
                    lt.append(desc_full[solid_count], style=f"rgb({gray},{gray},{gray})")
                if not available and solid_count >= len(desc_full):
                    lt.append("  (coming soon)", style="rgb(60,60,70)")

        items.append(Text(""))
        items.extend(desc_lines)

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

    # Row 2 — quiet keycap control hints. Kept STATIC (no tip_brightness lerp): the
    # controls are a fixed affordance and pulsing them in/out with the tip read as
    # distracting. Each hint pairs the literal key with its action word ("[ prev").
    dot_dim = f"rgb({_TIP_DOT_DIM[0]},{_TIP_DOT_DIM[1]},{_TIP_DOT_DIM[2]})"
    key_style = f"bold rgb({_TIP_KEY[0]},{_TIP_KEY[1]},{_TIP_KEY[2]})"

    # Gaps kept tight so the full row (with `g open` present) fits the companion
    # duck's 36-col lane on ONE line — otherwise it wraps and `t hide` drops onto a
    # second line that gets clipped at the panel foot.
    def _hint(key: str, label: str, *, gap: str = "   ") -> None:
        if control.plain:
            control.append(gap)
        control.append(key, style=key_style)
        control.append(f" {label}", style=dot_dim)

    control = Text(justify="center")
    # Browse the tips manually with the [ and ] keys (rotation keeps running).
    _hint("[", "prev", gap="")
    _hint("]", "next", gap="  ")
    # Jump-into-feature — only when this tip maps to a selectable mode card. Key
    # is `g` (Enter is already bound to the *selected* card, not this tip).
    if tip.mode_key is not None:
        _hint("g", "open")
    _hint("t", "hide")

    return [tip_line, control]


# Rows the music pocket adds at the bottom-right of the welcome panel: a rounded
# roof arching up-and-over the music row. The panel's own bottom border (no bottom
# padding) is the pocket's floor, so the box fuses onto the bottom edge.
_MUSIC_POCKET_ROWS = 2


class _WelcomeFrame:
    """Renders the welcome Panel, then draws the app-wide music pocket over its
    bottom rows via the shared :func:`draw_music_pocket` — the SAME routine the rest
    of the app uses, so the welcome bar and every sub-page bar are pixel-identical.
    The welcome reserves two blank rows at its foot (``_MUSIC_POCKET_ROWS``) for the
    pocket to occupy. Not a Panel, so MusicLive leaves it alone (no flat subtitle).
    """

    def __init__(self, panel: Panel) -> None:
        self.panel = panel

    def __rich_console__(self, console, options):
        from rich.segment import Segment

        from yeaboi.ui.shared._music_bar import draw_music_pocket

        lines = console.render_lines(self.panel, options, pad=False)
        draw_music_pocket(console, options, lines)
        # Newlines BETWEEN rows only — a trailing one scrolls a full-height frame
        # up by a row (the "bottom border creeps up on entry" glitch).
        for i, line in enumerate(lines):
            if i:
                yield Segment.line()
            yield from line


def _build_update_box(*, cols: int) -> Panel | None:
    """The bottom-right update advisory as its own box, above the duck's speech
    bubble — shown only when a newer release exists (and not on a dev build).

    Styled warmer and heavier than the tip bubble (amber border + keycap) so it
    reads as *more pressing* than an ambient tip: it tells the user a new version
    is out and that ``ctrl+U`` installs it in place. Returns None when there's
    nothing to advertise, so the companion lane just shows the tip + duck. Reads
    the check state lazily like :func:`_build_version_row` (monkeypatchable seam).
    """
    from yeaboi.update_check import get_update_status

    status = get_update_status()
    if not status["update_available"] or status.get("is_dev"):
        return None

    amber = f"rgb({_TIP_DOT_ON[0]},{_TIP_DOT_ON[1]},{_TIP_DOT_ON[2]})"
    body = Text(justify="left")
    body.append(f"v{status['latest']}", style=f"bold {amber}")
    body.append(" is out\n", style="rgb(198,198,208)")
    body.append("press ", style="rgb(198,198,208)")
    body.append("ctrl+U", style=f"bold {amber}")
    body.append(" to update", style="rgb(198,198,208)")
    return Panel(
        body,
        box=rich.box.ROUNDED,
        border_style=amber,
        padding=(0, 1),
        width=cols - 2,
        title="update",
        title_align="left",
    )


def _build_version_row(width: int, *, suppress_upgrade: bool = False) -> Text:
    """Build the bottom-left version hint: current version + changelog keycap.

    Sits as the last interior row of the mode screen — bottom-left, opposite the
    music bar (which lives on the Panel's bottom *border*, right-aligned). When
    the background PyPI check has found a newer release, the row grows into an
    upgrade advisory with the exact command to run — unless ``suppress_upgrade``
    is set, in which case that advisory is omitted because the bottom-right
    :func:`_build_update_box` is carrying it instead (wide/companion layout). Reads
    the check state lazily (like ``_build_tip_rows`` reads tips config) so no call
    site changes and tests can monkeypatch ``yeaboi.update_check.get_update_status``.
    """
    from yeaboi.update_check import get_update_status

    status = get_update_status()
    dim = f"rgb({_TIP_DOT_DIM[0]},{_TIP_DOT_DIM[1]},{_TIP_DOT_DIM[2]})"
    accent = f"rgb({_TIP_DOT_ON[0]},{_TIP_DOT_ON[1]},{_TIP_DOT_ON[2]})"
    key_style = f"bold rgb({_TIP_KEY[0]},{_TIP_KEY[1]},{_TIP_KEY[2]})"

    # Lead with _PAD so the row's left edge lines up with the mode titles above
    # (which are all indented by _PAD) rather than sitting flush to the panel pad.
    row = Text(justify="left")
    row.append(_PAD, style="rgb(120,120,140)")
    row.append(f"v{status['current']}", style="rgb(120,120,140)")
    if status["update_available"] and not suppress_upgrade:
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
    duck_lift: int | None = None,
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
            desc_max_lines=2,  # welcome copy wraps to 2 lines rather than truncating
        )
        body.extend(items)
        # title (2) + selected's blank (1) + its 2 description lines (2) = 5.
        item_rows = 2 + (3 if is_sel else 0)
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

    # Bottom-right update box (above the duck's tip bubble) when a newer release
    # exists and there's room for the companion lane — it's more pressing than a
    # tip. When it shows, the bottom-left version row drops its inline advisory so
    # the same news isn't in two places.
    update_box = _build_update_box(cols=_COMPANION_COLS) if show_companion else None

    # Bottom-left version hint (+ upgrade advisory when a newer release exists and
    # the update box isn't already carrying it), opposite the music bar below it.
    version_row = _build_version_row(width, suppress_upgrade=update_box is not None)

    # The duck quacks when a new tip appears: his beak toggles open/closed a few
    # times over the first _QUACK_SECONDS of each tip window (tips rotate every
    # TIP_ROTATE_SECONDS). shimmer_tick is the continuous animation clock.
    _tw = shimmer_tick % TIP_ROTATE_SECONDS
    beak_open = _tw < _QUACK_SECONDS and int(_tw * _QUACK_HZ) % 2 == 1

    if show_companion:
        # Modes + duck are column-split; the version row is pinned at the FOOT of
        # the left column (not below the whole grid) so the right lane runs the
        # full inner height and the duck bottom-anchors flush with it — otherwise
        # a full-width version row underneath floats the duck up by a line.
        inner_h = height - 3  # top border + top pad + bottom border (no bottom pad)
        grid_h = max(0, inner_h - _MUSIC_POCKET_ROWS)  # music pocket sits below the grid
        body_area = max(0, grid_h - 1)  # reserve the version row at the column foot
        mid_top = max(0, (body_area - body_h) // 2)
        mid_bot = max(0, body_area - body_h - mid_top)
        left_col = Group(
            *[Text("") for _ in range(mid_top)],
            *body,
            *[Text("") for _ in range(mid_bot)],
            version_row,  # bottom-left, level with the duck's controls opposite it
        )
        # Table.grid is a borderless fixed-column splitter: mode list keeps its
        # width, the duck + speech bubble get a reserved right-hand lane.
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1)
        grid.add_column(width=_COMPANION_COLS)
        grid.add_row(
            left_col,
            _build_companion(
                tip_rows[0],
                controls=tip_rows[1],
                beak_open=beak_open,
                update_box=update_box,
                duck_lift=duck_lift,
            ),
        )
        # Reserve _MUSIC_POCKET_ROWS blank rows at the foot; _WelcomeFrame draws the
        # music pocket over them + the bottom border via the shared draw routine, so
        # the welcome bar matches every sub-page bar exactly.
        is_welcome = True
        body_renderable: RenderableType = Group(grid, *[Text("") for _ in range(_MUSIC_POCKET_ROWS)])
    else:
        is_welcome = False
        inner_h = height - 3  # top border + top pad + bottom border (no bottom pad)
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

    panel = Panel(
        body_renderable,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        # No bottom padding: the last content row (the music pocket's row) sits
        # directly on the bottom border, which the frame reroutes up over it.
        padding=(1, 2, 0, 2),
    )
    if not is_welcome:
        return panel
    # Draw the music pocket over the reserved bottom rows. Returning a frame (not a
    # bare Panel) also means MusicLive won't stamp the flat music subtitle.
    return _WelcomeFrame(panel)


def mode_at_row(selected: int, *, width: int, height: int, row: int, col: int) -> int | None:
    """Map a 1-based terminal (row, col) click to a mode-card index, or None.

    Reproduces the vertical layout maths of :func:`_build_mode_screen` so a click
    anywhere on a mode's title/description block resolves to that mode. Rows above
    or below the (vertically-centred) mode list — the tip and version rows — and
    clicks inside the right-hand duck lane return None. Kept in lock-step with the
    builder: the layout constants (panel border + top padding, ``body_h``, the
    companion split, the 2 tip rows + 1 version row) must match exactly.
    """
    n = len(_MODE_CARDS)
    show_companion = width >= _COMPANION_MIN_WIDTH and height >= _COMPANION_MIN_HEIGHT
    # Clicks in the duck's reserved right-hand lane aren't menu clicks.
    if show_companion and col > width - _COMPANION_COLS:
        return None

    # body_h — total rows of the mode block (mirrors _build_mode_screen).
    body_h = 0
    for i in range(n):
        body_h += 2 + (3 if i == selected else 0)  # title (2) + selected's blank+2 desc lines (3)
        if i < n - 1:
            body_h += 1  # inter-item blank separator

    inner_h = height - 3  # top border + top pad + bottom border (no bottom pad)
    if show_companion:
        # grid = inner_h − music pocket (2) − version row (1); modes centre in it.
        grid_h = max(0, inner_h - _MUSIC_POCKET_ROWS - 1)
        mid_top = max(0, (grid_h - body_h) // 2)
    else:
        body_area = max(0, inner_h - 3)  # 2 tip rows + 1 version row pinned below
        mid_top = max(0, (body_area - body_h) // 2)

    # Panel top border (1) + top padding (1) → the content group's first row is at
    # 1-based terminal row 3; the mode block starts mid_top rows into it.
    y = 3 + mid_top
    for i in range(n):
        block = 2 + (3 if i == selected else 0)
        sep = 1 if i < n - 1 else 0
        if y <= row <= y + block + sep - 1:  # separator maps to the mode above it
            return i
        y += block + sep
    return None


def selected_title_offset(selected: int, *, width: int, height: int) -> int:
    """Return the ``top_offset`` (blank content rows above the title) at which the
    currently-selected mode's title sits in :func:`_build_mode_screen`.

    Used to start the select→top slide (:func:`_build_slide_frame`) from the item's
    *actual* resting position rather than a hardcoded centre, so a clicked item
    lifts from where it is instead of jumping to the middle first. Mirrors the
    vertical maths of :func:`_build_mode_screen`/:func:`mode_at_row` exactly (same
    ``body_h``, companion split, and centring), so the first slide frame lands the
    title on the same row it occupied a frame earlier.
    """
    n = len(_MODE_CARDS)
    show_companion = width >= _COMPANION_MIN_WIDTH and height >= _COMPANION_MIN_HEIGHT

    # body_h — total rows of the mode block (selected carries +3 for its blank+desc).
    body_h = 0
    for i in range(n):
        body_h += 2 + (3 if i == selected else 0)
        if i < n - 1:
            body_h += 1

    inner_h = height - 3  # top border + top pad + bottom border (no bottom pad)
    if show_companion:
        grid_h = max(0, inner_h - _MUSIC_POCKET_ROWS - 1)  # music pocket + version row
        mid_top = max(0, (grid_h - body_h) // 2)
    else:
        body_area = max(0, inner_h - 3)  # 2 tip rows + 1 version row pinned below
        mid_top = max(0, (body_area - body_h) // 2)

    # Every mode before the selected one contributes title(2) + separator(1) = 3
    # rows (none of them is selected, so no description block).
    return mid_top + 3 * selected


def duck_hit(width: int, height: int, *, row: int, col: int) -> bool:
    """Whether a 1-based click at (row, col) landed on the companion duck — used to
    trigger the click-the-duck double-shades gag.

    Mirrors the companion layout: the duck sits in the right-hand lane, bottom-
    aligned just above the ``chilling`` caption (1 row) at the lane foot. The
    resting head is 7 rows tall, so it spans a fixed band near the bottom of the
    panel. Generous by a row each way so the caption counts as the duck too.
    """
    if not (width >= _COMPANION_MIN_WIDTH and height >= _COMPANION_MIN_HEIGHT):
        return False
    if col <= width - _COMPANION_COLS:  # not in the duck's right-hand lane
        return False
    # The lane bottom-anchors in the grid (inner_h − music pocket); the head is now
    # its last element (chilling moved to the pocket row), so the head's bottom row
    # is the grid's bottom row.
    duck_bottom = height - 1 - _MUSIC_POCKET_ROWS  # 1-based row of the head's last row
    duck_top = duck_bottom - 6  # 7-row head
    return duck_top - 1 <= row <= duck_bottom + 2  # margin: crown above, caption below


def _build_companion(
    tip_line: Text,
    *,
    controls: Text | None = None,
    beak_open: bool = False,
    update_box: Panel | None = None,
    duck_lift: int | None = None,
) -> RenderableType:
    """Bottom-right idle duck (facing left, toward the menu) with the current tip
    in a speech bubble above it — and, above that, an optional ``update_box``.

    ``tip_line`` is the tip text from :func:`_build_tip_rows` (may be blank when
    tips are hidden — then only the duck shows). The bubble uses a plain, static
    copy of the tip: the per-frame cross-fade is dropped (it flickers in a box)
    and any leading emoji is stripped (a wide glyph in a bordered Panel breaks the
    border). ``update_box`` (from :func:`_build_update_box`) stacks above the tip
    bubble when a release is available. ``beak_open`` opens his bill for a quack
    when a new tip appears. Bottom-aligned so it sits in the corner regardless of
    terminal height.
    """
    # Duck faces left so he looks toward the mode list rather than the wall.
    # Otherwise static (no bob): the up/down breathing shifted both the duck and
    # the bubble, which read as jitter — the only motion is the tip-change quack.
    # duck_lift not None → play the double-shades gag (sunglasses raised by that
    # many pixels, second pair revealed underneath); otherwise the resting head.
    head = (
        render_head_shades(duck_lift, flip=True)
        if duck_lift is not None
        else render_head(0, flip=True, beak_open=beak_open)
    )
    has_controls = controls is not None and controls.plain.strip()
    parts: list[RenderableType] = []
    if update_box is not None:
        # More pressing than the tip: it sits at the top of the lane, above the
        # bubble, with a blank line separating the two boxes.
        parts.extend([update_box, Text("")])

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
        # The browse/hide controls live ON the bubble's bottom border (subtitle),
        # so they read as part of the tip box rather than a separate row.
        if has_controls:
            bubble.subtitle = controls
            bubble.subtitle_align = "center"
        # A small nub centred under the bubble that points down at the duck — a
        # tidy speech-bubble tail rather than a stray diagonal slash.
        tail = Align.center(Text("▾", style="rgb(90,100,110)"))
        parts.extend([bubble, tail])  # extend, not reassign — keep any update_box above
    elif has_controls:
        # Tips hidden → no bubble to carry the "t show tips" hint; keep it visible.
        parts.append(controls)
    parts.append(Align.center(head))
    # "chilling" is no longer tucked under the duck — it's rendered on the music
    # pocket row, just above the bottom border (see _build_music_pocket caption).
    return Align.center(Group(*parts), vertical="bottom")


def _build_update_screen(
    width: int,
    height: int,
    *,
    latest: str,
    command: str,
    spinner: str = "",
    done: bool = False,
    ok: bool = False,
    detail: str = "",
) -> Panel:
    """Modal shown by the ctrl+U update flow: a spinner while ``uv/pipx upgrade``
    runs, then a success or failure result.

    While running (``done=False``) it shows ``spinner`` + "updating to vX". On
    success it says the new version is installed and to restart; on failure it
    shows the manual command so the user can run it themselves. Any key dismisses
    the result (handled by the caller).
    """
    amber = f"rgb({_TIP_DOT_ON[0]},{_TIP_DOT_ON[1]},{_TIP_DOT_ON[2]})"
    rows: list[RenderableType] = []
    if not done:
        line = Text(justify="center")
        line.append(f"{spinner} ", style=amber)
        line.append(f"updating to v{latest}…", style="rgb(198,198,208)")
        rows.append(line)
        border = amber
    elif ok:
        rows.append(Align.center(Text(f"✓  updated to v{latest}", style=f"bold {amber}")))
        rows.append(Text(""))
        rows.append(Align.center(Text("restart yeaboi to use the new version", style="rgb(198,198,208)")))
        rows.append(Align.center(Text("press any key", style="rgb(120,130,140)")))
        border = amber
    else:
        rows.append(Align.center(Text("update failed", style="bold rgb(226,110,90)")))
        rows.append(Text(""))
        rows.append(Align.center(Text("run it yourself:", style="rgb(198,198,208)")))
        rows.append(Align.center(Text(command, style=f"bold {amber}")))
        if detail:
            rows.append(Text(""))
            rows.append(Align.center(Text(detail.splitlines()[-1][: max(10, width - 12)], style="rgb(120,130,140)")))
        rows.append(Align.center(Text("press any key", style="rgb(120,130,140)")))
        border = "rgb(226,110,90)"
    return Panel(
        Align.center(Group(*rows), vertical="middle"),
        border_style=border,
        box=rich.box.ROUNDED,
        expand=True,
        height=max(1, height),
        padding=(1, 2),
    )


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
    block_h = 2  # title(2) only — description is not shown during slide
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
