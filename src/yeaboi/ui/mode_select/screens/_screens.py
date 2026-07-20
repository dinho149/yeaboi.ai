"""Screen builder functions for the mode selection flow.

# See docs: "Architecture" — this module contains the rendering functions
# for the mode selection, intake, offline, export, import, and delete screens.
# These are pure functions that return Rich Panel renderables — no I/O or state.
"""

from __future__ import annotations

from typing import Any

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared._animations import COLOR_RGB, shimmer_style
from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import PAD

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

# ---------------------------------------------------------------------------
# Rendering helpers — mode selection
# ---------------------------------------------------------------------------


def _build_mode_row(
    mode: dict[str, Any],
    *,
    selected: bool,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    override_style: str = "",
) -> list:
    """Render a mode as ASCII art title + optional description underneath.

    Returns a list of Rich renderables (1–3 items depending on state).
    desc_reveal: float — the fractional part fades in the next character for
        a smoother typewriter effect (e.g. 5.4 = 5 solid chars + 1 at 40% opacity).
    """
    available = mode["available"]
    color = mode["color"]
    lines = render_ascii_text(mode["title"])

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
) -> Panel:
    """Build the full-screen mode selection layout."""
    show = visible if visible is not None else list(range(len(_MODE_CARDS)))
    fading = fade_indices or []

    # Mode rows
    body: list = []
    body_h = 0
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
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show[-1]:
            body.append(Text(""))
            body_h += 1

    # Bottom-pinned discoverability tip — so users learn features exist from the
    # very first screen, not only inside a session. Tips rotate every few seconds
    # off the render loop's shimmer_tick, cross-fading between one another, and can
    # be switched off entirely. Rendered as two quiet rows: the tip itself, then
    # position dots + a keycap control hint.
    tip_rows = _build_tip_rows(shimmer_tick, tip_offset=tip_offset)

    # Bottom-left version hint (+ upgrade advisory when a newer release exists),
    # opposite the music bar on the border below it.
    version_row = _build_version_row(width)

    # Reserve two rows for the tip block plus one for the version row; centre the
    # mode rows in the space above.
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

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
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
