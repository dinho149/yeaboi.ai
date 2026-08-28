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
from rich.padding import Padding
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from yeaboi.beta import BETA_LABEL, BETA_RGB
from yeaboi.ui.shared._animations import BLACK_RGB, COLOR_RGB, lerp_color, shimmer_style
from yeaboi.ui.shared._ascii_font import render_ascii_text
from yeaboi.ui.shared._components import PAD, build_badge, build_page_panel
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
        "key": "poker",
        "title": "Poker",
        "description": "Run planning poker: the team votes on sprint or backlog tickets in a browser; points sync.",
        "available": True,
        "color": "rgb(230,200,70)",
    },
    {
        "key": "performance",
        "title": "Performance",
        "description": "Manage each engineer: 1:1 prep, 1:1 summaries, and 6-month reviews from real delivery data.",
        # Beta, not unavailable: the mode runs, but its output hasn't been
        # validated against a real team's tracker yet. "available" must stay True
        # — it gates Enter, the click handler, and the tip jump key.
        "available": True,
        "badge": BETA_LABEL,
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
        "key": "ship",
        "title": "Ship",
        "description": "Hand any epic, story or task to a coding agent: isolated branch, your approval, then a PR.",
        # Beta like Performance: the mode runs end to end, but it drives a
        # coding agent against the user's own repository — "available" stays
        # True (it gates Enter, click, and the tip jump), the badge carries
        # the caveat.
        "available": True,
        "badge": BETA_LABEL,
        "color": "rgb(235,140,60)",
    },
    {
        "key": "usage",
        "title": "Usage",
        "description": "View API token usage, session history, and cost estimates.",
        "available": True,
        "color": "rgb(220,160,60)",
        # A local dashboard over the usage DB — no LLM call, so no credential gate.
        "llm": False,
    },
    {
        "key": "settings",
        "title": "Settings",
        "description": "Manage API keys, LLM provider, and board configuration.",
        "available": True,
        "color": "rgb(160,160,180)",
        # Makes no LLM call, and is where a broken key gets fixed — gating it
        # would lock the user out of the only screen that helps them.
        "llm": False,
    },
]

# ---------------------------------------------------------------------------
# Agents mode definitions — the second category on the landing split. Kept as a
# SEPARATE list, never merged into _MODE_CARDS: the welcome tests pin exact
# renders and hardcoded indices against _MODE_CARDS, and the two menus are
# separate screens sharing one builder (_build_mode_screen(cards=...)).
# ---------------------------------------------------------------------------

_AGENT_CARDS: list[dict[str, Any]] = [
    {
        "key": "agent-usage",
        "title": "Usage",
        "description": "See what your AI agents cost: tokens, cache, per-model and per-project spend, daily trend.",
        "available": True,
        "badge": BETA_LABEL,
        "color": "rgb(70,190,230)",
    },
    {
        # Advisor sits beside Usage deliberately: Usage says what the agents
        # cost, Advisor says how much of that was avoidable.
        "key": "agent-advisor",
        "title": "Advisor",
        "description": "Find recoverable agent spend: re-read waste, cache health, and what each is costing you.",
        "available": True,
        "badge": BETA_LABEL,
        "color": "rgb(240,180,70)",
    },
    {
        "key": "agent-standup",
        "title": "Standup",
        "description": "A daily digest of what your agents did: sessions worked, commits and PRs, open threads.",
        "available": True,
        "badge": BETA_LABEL,
        "color": "rgb(120,210,170)",
    },
    {
        "key": "agent-security",
        "title": "Security",
        "description": "Audit your agent setup: permissions, MCP servers, secrets exposure, risky commands.",
        "available": True,
        "badge": BETA_LABEL,
        "color": "rgb(230,90,120)",
    },
]

# ---------------------------------------------------------------------------
# Intake mode definitions — shown when the user selects "+ New Project"
# ---------------------------------------------------------------------------

_INTAKE_CARDS: list[dict[str, Any]] = [
    {
        # The live-chat front door: the agent asks (or infers from the
        # description) whether the work is Small or Large, replacing the old
        # small_project/smart cards. run_session treats "chat" as
        # "ask the size in conversation"; /small and /large force it.
        "key": "chat",
        "title": "Plan",
        "description": "Describe your project in chat — the agent asks what it needs and sizes it Small or Large.",
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
# Ten cards at two rows plus a spacer each, the selected description, and the
# bottom-left version row all fit in 40. An eleventh card is what pushes that
# row off — which is why Niko and Ceremonies are keycaps (see
# :func:`_build_version_row`). Below this the "size up" duck shows instead of a
# clipped menu.
_MIN_HEIGHT = 40

# The bottom-right duck companion + its speech-bubble tip need extra room: the
# bubble reserves a right-hand lane, so the longest mode title must still fit to
# its left. Only shown at/above these thresholds. On the WIDTH axis, _MIN_WIDTH
# (84) to _COMPANION_MIN_WIDTH-1 renders the full-width compact menu (tip pinned
# at the bottom). On height, _MIN_HEIGHT already clears the companion's vertical
# need (39), so a tall-enough-but-narrow terminal is the only compact case.
_COMPANION_MIN_WIDTH = 108
_COMPANION_MIN_HEIGHT = 39  # rows the full companion welcome (menu + tip bubble + duck + pocket) needs to fit
_COMPANION_HEAD_W = 13  # tight render width of the duck head (matches _mascot)
_COMPANION_REVEAL_FROM = 0.72  # entrance progress at which the tip/update box fade in
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

# Chip colour for an unavailable (COMING SOON) card — the same dead grey the
# disabled title uses, so the whole row reads as one state.
_DISABLED_BADGE_RGB = (90, 90, 100)


def mode_title_widths(cards: list[dict[str, Any]] | None = None) -> list[int]:
    """Block-font column width of every mode title, index-aligned to ``cards``
    (default ``_MODE_CARDS`` — pass ``_AGENT_CARDS`` for the Agents menu).

    The staggered intro reveal uses these to know when each title is fully wiped
    in (see the reveal loop in :mod:`yeaboi.ui.mode_select`).
    """
    cards = _MODE_CARDS if cards is None else cards
    return [max(len(line) for line in render_ascii_text(mode["title"])) for mode in cards]


def _card_badge(mode: dict[str, Any]) -> str:
    """Return the status chip text for a card, or "" when it has no status.

    An explicit ``badge`` wins; otherwise an unavailable card is a COMING SOON.

    ``badge`` is read with ``.get`` because ``_build_mode_row`` is also fed dicts
    synthesised at runtime — the Performance roster builds one per engineer —
    which set ``available`` but never ``badge``, and so must render no chip.
    """
    badge = mode.get("badge", "")
    if badge:
        return str(badge)
    return "" if mode.get("available", True) else "COMING SOON"


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
    full_lines = render_ascii_text(mode["title"])
    lines = full_lines
    if sweep_front is not None:
        lines = [
            line[: max(0, int(sweep_front - (row_base + r) * _SWEEP_ROW_WEIGHT))] for r, line in enumerate(full_lines)
        ]

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

    # Status chip (BETA / COMING SOON), pinned to the end of the second block-font
    # line. It goes on the title rather than the description because the
    # description only renders on the selected card, and a status marker you have
    # to arrow onto isn't labelling. Held back until the intro sweep has finished
    # drawing this row, so no chip floats beside a half-drawn wordmark.
    badge = _card_badge(mode)
    if badge and (sweep_front is None or len(lines[1]) == len(full_lines[1])):
        # An unavailable card's chip is grey in both selection states — the card
        # is disabled, and a chip that changed hue as you arrowed onto it would
        # read as a state change. A beta chip keeps its amber and only dims.
        badge_rgb = BETA_RGB if available else _DISABLED_BADGE_RGB
        rendered.append("  ")
        if override_style:
            # Menu fades restyle the whole row; the chip fades out with it rather
            # than staying lit over a dissolving title.
            rendered.append(f" {badge} ", style=override_style)
        else:
            rendered.append_text(build_badge(badge, rgb=badge_rgb, dim=not selected))

    # The row is exactly two block-font lines tall and the whole menu's click
    # hit-testing (mode_at_row / selected_title_offset) derives from that. Crop
    # rather than wrap so a long title plus a chip can never add a third row —
    # no_wrap alone is not enough, Rich still folds an over-long line.
    rendered.no_wrap = True
    rendered.overflow = "crop"

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
# Amber caution for the BETA badge — shared with the mode-card chip and the docs
# site's pill, and deliberately distinct from the gold NEW badge above.
_TIP_BETA = BETA_RGB


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

    # Row 1 — an optional BETA/NEW badge, then the tip, faded toward full body colour.
    # BETA wins over NEW: a maturity caveat outranks a freshness cue, and two
    # badges would push the centred line out of the companion duck's lane.
    tip_line = Text(justify="center")
    if tip.is_beta:
        tip_line.append(f" {BETA_LABEL} ", style=f"bold {lerp_color(b, _TIP_BG, _TIP_BETA)}")
        tip_line.append("  ")
    elif tip.is_new:
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

    def __init__(self, panel: Panel, compose: dict | None = None) -> None:
        self.panel = panel
        self.compose = compose  # the feedback bubble, drawn OVER the finished frame

    def __rich_console__(self, console, options):
        from rich.segment import Segment

        from yeaboi.ui.shared._music_bar import draw_back_pocket, draw_controls_pocket, draw_music_pocket

        lines = console.render_lines(self.panel, options, pad=False)
        draw_music_pocket(console, options, lines)
        if self.compose is not None:
            _draw_compose_bubble(console, options, lines, self.compose)
        # The welcome screen already lists its own controls, so the tab doesn't
        # belong here — target 0 so it eases back out instead of vanishing.
        draw_controls_pocket(console, options, lines, target=0.0)
        # Retract the back tab if it's still on screen (e.g. Esc'd back here from a
        # sub-page) — the welcome is never back-capable, so it only animates out.
        draw_back_pocket(console, options, lines, target=0.0)
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
    is out and that ``ctrl+U`` installs it and relaunches onto it. Returns None when
    there's nothing to advertise, so the companion lane just shows the tip + duck.
    Reads the check state lazily like :func:`_build_version_row` (monkeypatchable
    seam).
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


# What the version row costs beyond its own text: the panel's two borders plus
# two columns of padding each side (build_page_panel's ``padding=(1, 2, 0, 2)``).
_VERSION_ROW_CHROME = 6


def _version_row_budget(width: int, *, show_companion: bool) -> int:
    """Cells the bottom-left version row can actually draw into.

    Rich crops this row rather than wrapping it, so a chip that overruns simply
    vanishes — mid-word, silently. The companion layout pins the row inside the
    left grid column, so the duck's lane comes off the budget too.
    """
    return max(0, width - _VERSION_ROW_CHROME - (_COMPANION_COLS if show_companion else 0))


def _build_version_row(width: int, *, suppress_upgrade: bool = False, show_companion: bool = False) -> Text:
    """Build the bottom-left version hint: current version + changelog keycap.

    Sits as the last interior row of the mode screen — bottom-left, opposite the
    music bar (which lives on the Panel's bottom *border*, right-aligned). When
    the background PyPI check has found a newer release, the row grows into an
    upgrade advisory with the exact command to run — unless ``suppress_upgrade``
    is set, in which case that advisory is omitted because the bottom-right
    :func:`_build_update_box` is carrying it instead (wide/companion layout). Reads
    the check state lazily (like ``_build_tip_rows`` reads tips config) so no call
    site changes and tests can monkeypatch ``yeaboi.update_check.get_update_status``.
    When this process was relaunched by the ctrl+U update instead, there is nothing
    to advertise, so the row carries a ✓ updated chip confirming the version that
    actually took.
    """
    from yeaboi.update_check import get_update_status, is_fresh_restart

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
    elif not status["update_available"] and is_fresh_restart() and width >= 72:
        # This process was relaunched by the ctrl+U update — confirm the version
        # that actually took, so the restart visibly did something. Dropped first
        # on narrow terminals, like the upgrade command above. A newer release
        # discovered since the restart still wins the slot, in this layout via the
        # explicit check (the branch above is off when _build_update_box has it).
        row.append("  ·  ", style=dim)
        row.append("✓ updated", style=accent)
    # Keycaps, widest-terminal-last: each is appended only while the row still
    # fits its budget, so the rightmost are the first to go. Ceremonies and Niko
    # are keycaps rather than mode cards because the menu renders every card with
    # no scrolling, and an eleventh pushes THIS row off screen at the enforced
    # 84x40 minimum — trading version, changelog and feedback for one entry.
    # Niko is last because the duck himself is its primary affordance.
    budget = _version_row_budget(width, show_companion=show_companion)
    for key, label, wide_only in (
        ("c", "changelog", False),
        ("f", "feedback", False),
        ("a", "all tips", False),
        ("s", "schedule", True),
        ("n", "niko", True),
    ):
        if wide_only and width < 72:
            break
        if row.cell_len + len(label) + 7 > budget:  # "  ·  " + key + " " + label
            break
        row.append("  ·  ", style=dim)
        row.append(key, style=key_style)
        row.append(f" {label}", style=dim)
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
    sweep_skip: int | None = None,
    duck_lift: int | None = None,
    companion_intro: float = 1.0,
    extras_reveal: float | None = None,
    compose: dict | None = None,
    cards: list[dict[str, Any]] | None = None,
    mascot: str = "duck",
) -> Panel:
    """Build the full-screen mode selection layout.

    sweep_front: optional diagonal intro-reveal front (see _build_mode_row). None
    → every title fully shown. All titles share one front, so the menu wipes in as
    a single coherent top-left → bottom-right sweep.
    sweep_skip: index of one title to leave fully shown while the sweep reveals the
    rest — used by the return transition (the mode you came from is already home).
    cards / mascot: the card list this menu shows (default ``_MODE_CARDS``) and the
    companion sprite beside it ("duck" for Humans, "robo" for Agents). Only the
    *source* of the rows changes — every layout constant stays identical, and
    ``mode_at_row``/``selected_title_offset`` must be passed the same ``cards``.
    """
    cards = _MODE_CARDS if cards is None else cards
    show = visible if visible is not None else list(range(len(cards)))
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
    for i, mode in enumerate(cards):
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
            # sweep_skip keeps one title fully shown while the rest wipe in — used by
            # the return transition so the mode you came from stays put as the others
            # scroll back in around it.
            sweep_front=None if i == sweep_skip else sweep_front,
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
    version_row = _build_version_row(width, suppress_upgrade=update_box is not None, show_companion=show_companion)

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
                companion_intro=companion_intro,
                extras_reveal=extras_reveal,
                compose=compose,
                lane_h=grid_h,
                mascot=mascot,
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

    # No bottom padding: the last content row (the music pocket's row) sits
    # directly on the bottom border, which the frame reroutes up over it.
    # build_page_panel (main #104) applies the neutral base tint so the main
    # menu never shows the terminal's own background.
    panel = build_page_panel(body_renderable, height=height, padding=(1, 2, 0, 2))
    # The menu draws its own companion in-panel, but the stamp still matters:
    # MusicLive reads it into the chrome-mascot global, which the screensaver
    # uses — idling on the Agents menu must save with the robo, not the duck.
    panel._duck_mascot = mascot
    if not is_welcome:
        panel._no_back_hint = True  # the main menu's Esc isn't "go back" → no back tab
        return panel
    # Draw the music pocket over the reserved bottom rows. Returning a frame (not a
    # bare Panel) also means MusicLive won't stamp the flat music subtitle.
    frame = _WelcomeFrame(panel, compose=compose)
    frame._duck_mascot = mascot  # the frame isn't a Panel; the stamp rides on it too
    return frame


def mode_at_row(
    selected: int, *, width: int, height: int, row: int, col: int, cards: list[dict[str, Any]] | None = None
) -> int | None:
    """Map a 1-based terminal (row, col) click to a mode-card index, or None.

    Reproduces the vertical layout maths of :func:`_build_mode_screen` so a click
    anywhere on a mode's title/description block resolves to that mode. Rows above
    or below the (vertically-centred) mode list — the tip and version rows — and
    clicks inside the right-hand duck lane return None. Kept in lock-step with the
    builder: the layout constants (panel border + top padding, ``body_h``, the
    companion split, the 2 tip rows + 1 version row) must match exactly.
    Pass the same ``cards`` the builder was given (default ``_MODE_CARDS``).
    """
    n = len(_MODE_CARDS if cards is None else cards)
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


def selected_title_offset(selected: int, *, width: int, height: int, cards: list[dict[str, Any]] | None = None) -> int:
    """Return the ``top_offset`` (blank content rows above the title) at which the
    currently-selected mode's title sits in :func:`_build_mode_screen`.

    Used to start the select→top slide (:func:`_build_slide_frame`) from the item's
    *actual* resting position rather than a hardcoded centre, so a clicked item
    lifts from where it is instead of jumping to the middle first. Mirrors the
    vertical maths of :func:`_build_mode_screen`/:func:`mode_at_row` exactly (same
    ``body_h``, companion split, and centring), so the first slide frame lands the
    title on the same row it occupied a frame earlier. Pass the same ``cards``
    the builder was given (default ``_MODE_CARDS``).
    """
    n = len(_MODE_CARDS if cards is None else cards)
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


_COMPANION_CAPTION_ROWS = 1  # the "n  ask niko" line under the mascot


def _companion_duck_bottom(height: int) -> int:
    """1-based frame row of the mascot head's last row.

    The lane bottom-anchors, so everything anchored on the duck — the click band,
    the compose bubble, the sign-in bubble — derives from here rather than
    re-deriving it and drifting when the lane's foot changes.
    """
    return height - 1 - _MUSIC_POCKET_ROWS - _COMPANION_CAPTION_ROWS


def duck_hit(width: int, height: int, *, row: int, col: int) -> bool:
    """Whether a 1-based click at (row, col) landed on the companion duck — used to
    trigger the click-the-duck double-shades gag, which opens Niko.

    Mirrors the companion layout: the duck sits in the right-hand lane, bottom-
    aligned just above his ``n  ask niko`` caption at the lane foot. The resting
    head is 7 rows tall, so it spans a fixed band near the bottom of the panel.
    Generous by a row each way, which is what puts the caption itself in the band.
    """
    if not (width >= _COMPANION_MIN_WIDTH and height >= _COMPANION_MIN_HEIGHT):
        return False
    if col <= width - _COMPANION_COLS:  # not in the duck's right-hand lane
        return False
    duck_bottom = _companion_duck_bottom(height)
    duck_top = duck_bottom - 6  # 7-row head
    return duck_top - 1 <= row <= duck_bottom + 2  # margin: crown above, caption below


# The duck's compose bubble shares the tip bubble's lane width — it must NOT widen
# it. The split feeds the mode list's own width, so a wider lane reflows every
# description (growing the left column, which pushes the bottom-anchored duck
# down) and truncates the bottom-left hint row, both the moment you press `f`.
# Room is bought vertically instead, which costs the left column nothing.
_COMPOSE_MAX_ROWS = 12  # typing rows before the box scrolls with the cursor
# The overlay is free to be wider than the companion lane — nothing else is laid
# out against it — so it takes a comfortable writing width, clamped to the frame.
_COMPOSE_OVERLAY_COLS = 64
_COMPOSE_LEFT_MARGIN = 8  # frame columns kept clear to the bubble's left
_COMPOSE_TOP_MARGIN = 3  # …and above it
_COMPOSE_MIN_ROWS = 2
# Rows the bubble costs besides its message: two borders, the Type and Area rows
# and the blank under them. Plus the tail below it and the duck's own height —
# the lane is bottom-anchored, so anything over budget crops the duck, not the box.
_COMPOSE_CHROME_ROWS = 5
_COMPOSE_TAIL_ROWS = 1
_COMPANION_HEAD_H = 7  # rows the head renders at _COMPANION_HEAD_W


def _wrap_with_offsets(text: str, width: int) -> list[tuple[str, int]]:
    """Greedy word-wrap returning ``(line, start_offset)`` pairs.

    Written out rather than using textwrap because the caller has to map a cursor
    INDEX back to a (row, column) — which needs each line's offset into the
    original string, and needs every character preserved (textwrap drops the
    whitespace the cursor may be sitting on). A word longer than ``width`` is
    hard-broken, so a pasted URL or a keysmash wraps instead of running off the box.
    """
    width = max(4, width)
    out: list[tuple[str, int]] = []
    line, start = "", 0
    pos = 0  # index in ``text`` of the word being placed
    for w_i, word in enumerate(text.split(" ")):
        if w_i:
            pos += 1  # the space that separated the words
        while len(word) > width:  # hard-break an over-long word (a URL, a keysmash)
            if line:
                out.append((line, start))
                line = ""
            out.append((word[:width], pos))
            word, pos = word[width:], pos + width
        candidate = f"{line} {word}" if line else word
        if line and len(candidate) > width:
            out.append((line, start))
            line, start = word, pos
        else:
            if not line:
                start = pos
            line = candidate
        pos += len(word)
    out.append((line, start))
    return out


# A thick block down the left of the message area, on the rows that carry text —
# it reads as a quote gutter, so the typed message is visibly a block of writing
# rather than loose lines under the two selectors.
_COMPOSE_GUTTER = "▌ "
_COMPOSE_GUTTER_STYLE = "rgb(96,112,128)"


def _compose_window(values: tuple[str, ...], idx: int, width: int) -> tuple[int, int]:
    """The slice of ``values`` to show so ``idx`` is visible, kept as still as possible.

    The options are a FIXED list in a fixed order — they must not rotate under the
    selection. The window therefore only slides when the selection would otherwise
    fall off an end, so stepping through the middle moves the marker, not the words.
    Every entry costs ``len + 4`` columns (the ``‹ ›`` brackets it may need).
    """
    costs = [len(v) + 4 for v in values]
    if sum(costs) <= width:
        return 0, len(values)
    # Widest window starting at each position; pick the left-most one holding idx.
    best_start, best_end = idx, idx + 1
    for start in range(len(values)):
        total, end = 0, start
        while end < len(values) and total + costs[end] <= width:
            total += costs[end]
            end += 1
        if start <= idx < end and (end - start) > (best_end - best_start):
            best_start, best_end = start, end
    # Prefer the window that keeps the most context BEFORE the selection, so
    # moving right doesn't drag the whole list left one step at a time.
    return best_start, best_end


def _compose_chips(
    values: tuple[str, ...], idx: int, focused: bool, width: int, *, dim: str, text: str, accent: str
) -> Text:
    """One selector row: every option in its fixed order, the chosen one bracketed.

    The styles are passed in because the whole bubble fades on its entrance/exit.
    """
    row = Text(justify="left", no_wrap=True, overflow="ellipsis")
    start, end = _compose_window(values, idx, width)
    if start:
        row.append("… ", style=dim)  # more options off to the left (… not ‹, which
        #                              would read as a selection bracket)
    for i in range(start, end):
        if i > start:
            row.append("  ", style=dim)
        if i == idx:
            row.append(f"‹ {values[i]} ›", style=f"bold {accent}" if focused else text)
        else:
            row.append(values[i], style=dim)
    if end < len(values):
        row.append(" …", style=dim)
    return row


def _compose_message_rows(space: int) -> int:
    """How many message rows fit in ``space`` rows of frame above the tail."""
    return max(_COMPOSE_MIN_ROWS, min(_COMPOSE_MAX_ROWS, space - _COMPOSE_CHROME_ROWS))


def welcome_shows_companion(width: int, height: int) -> bool:
    """Whether the welcome screen has room for the duck's lane at this size.

    The composer is drawn over that lane, so a caller has to know: below this the
    bubble would render nothing while still swallowing every key.
    """
    return width >= _COMPANION_MIN_WIDTH and height >= _COMPANION_MIN_HEIGHT


def _draw_compose_bubble(console, options, lines: list, compose: dict) -> None:
    """Composite the feedback bubble over the finished welcome frame, in place.

    Drawn here rather than inside the companion lane so it can be as wide as it
    likes: the lane's width feeds the mode list's, so a wider lane would reflow
    every description and truncate the bottom-left hint row. Overdrawing touches
    neither — the frame underneath is exactly the frame without a bubble.

    Anchored on the duck: the layout puts him in the last ``_COMPANION_HEAD_H``
    rows of the grid, with the pocket's two rows and the bottom border below, so
    his top row is a fixed offset from the foot and the bubble stacks up from
    there. Right-aligned with where the tip bubble sits, growing leftward.
    """
    from rich.segment import Segment

    if not lines or not lines[-1]:
        return
    width = sum(seg.cell_length for seg in lines[-1]) or options.max_width
    height = len(lines)
    # Render over the page's own tint so the box doesn't punch a hole in it.
    base = lines[-1][0].style
    bg_style = Style(bgcolor=base.bgcolor) if base and base.bgcolor else None

    duck_top = _companion_duck_bottom(height) - _COMPANION_HEAD_H  # the row above his crown
    bottom = duck_top - 1 - _COMPOSE_TAIL_ROWS  # the tail sits between them
    right = width - 5  # level with the tip bubble's right edge
    cols = min(_COMPOSE_OVERLAY_COLS, right - _COMPOSE_LEFT_MARGIN)
    if cols < _COMPANION_COLS - 2 or bottom < 4:
        return  # too cramped to overlay — the duck keeps the screen to himself

    rows = _compose_message_rows(bottom - _COMPOSE_TOP_MARGIN)
    bubble = _build_compose_bubble(compose, cols=cols + 2, max_rows=rows)
    rendered = console.render_lines(bubble, options.update_width(cols), pad=True, style=bg_style)
    top = bottom - len(rendered) + 1
    if top < _COMPOSE_TOP_MARGIN:
        return
    left = right - cols + 1
    for i, row in enumerate(rendered):
        r = top + i
        if not 0 <= r < height:
            continue
        before, _mid, after = Segment.divide(lines[r], [left, right + 1, width])
        lines[r] = list(before) + list(row) + list(after)


def _build_compose_bubble(compose: dict, *, cols: int, max_rows: int = _COMPOSE_MAX_ROWS) -> RenderableType:
    """The duck's feedback composer: a speech bubble you actually write in.

    ``compose`` is the loop's state — ``kind``/``area`` (indices into
    FEEDBACK_TYPES / FEEDBACK_AREAS), ``buf``/``cur`` (the message and cursor),
    ``field`` (0 type, 1 area, 2 message) and an optional ``status`` shown instead
    of the hint while sending or once sent. It replaces the tip bubble in the
    companion lane, so the duck reads as the one asking for the feedback.
    """
    from yeaboi.feedback import FEEDBACK_AREAS, FEEDBACK_TYPES

    inner = max(12, cols - 6)  # borders (2) + padding (2) + breathing room
    buf, cur = compose.get("buf", ""), compose.get("cur", 0)
    status, field = compose.get("status", ""), compose.get("field", 2)
    live = not status  # nothing is focused once it is sending
    # Presence (0→1) drives the entrance/exit: the colours come up out of the
    # background and the message area unfolds a row at a time, so the bubble grows
    # from the duck rather than snapping into place.
    presence = min(1.0, max(0.0, compose.get("presence", 1.0)))
    text_style = lerp_color(presence, BLACK_RGB, (198, 198, 208))
    dim_style = lerp_color(presence, BLACK_RGB, (110, 110, 125))
    accent_style = lerp_color(presence, BLACK_RGB, (150, 170, 200))
    border_style = lerp_color(presence, BLACK_RGB, (120, 135, 150))
    gutter_style = lerp_color(presence, BLACK_RGB, (96, 112, 128))
    max_rows = max(1, int(round(max_rows * presence))) if presence < 1.0 else max_rows

    rows: list = []
    label_w = 6
    for i, (label, values, key) in enumerate((("Type", FEEDBACK_TYPES, "kind"), ("Area", FEEDBACK_AREAS, "area"))):
        line = Text(justify="left", no_wrap=True, overflow="ellipsis")
        focused = live and field == i
        line.append(label.ljust(label_w), style=accent_style if focused else dim_style)
        line.append_text(
            _compose_chips(
                values,
                compose.get(key, 0),
                focused,
                inner - label_w,
                dim=dim_style,
                text=text_style,
                accent=accent_style,
            )
        )
        rows.append(line)
    rows.append(Text(""))

    msg_w = max(8, inner - len(_COMPOSE_GUTTER))  # the gutter eats into the wrap width
    if not buf and live:
        placeholder = Text(justify="left")
        placeholder.append(_COMPOSE_GUTTER, style=gutter_style)
        if field == 2:
            placeholder.append(" ", style="reverse")  # the cursor waiting in an empty box
        placeholder.append("What's on your mind?", style=dim_style)
        rows.append(placeholder)
        rows.extend(Text("") for _ in range(min(2, max_rows - 1)))
    else:
        wrapped = _wrap_with_offsets(buf, msg_w)
        # Follow the cursor once the message outgrows the box.
        cur_row = max(i for i, (_ln, off) in enumerate(wrapped) if off <= cur)
        first = max(0, min(cur_row - max_rows + 1, len(wrapped) - max_rows))
        window = wrapped[first : first + max_rows]
        for line, off in window:
            t = Text(justify="left")
            t.append(_COMPOSE_GUTTER, style=gutter_style)
            col = cur - off
            if live and field == 2 and 0 <= col <= len(line):
                t.append(line[:col], style=text_style)
                t.append(line[col : col + 1] or " ", style="reverse")  # block cursor
                t.append(line[col + 1 :], style=text_style)
            else:
                t.append(line, style=text_style)
            rows.append(t)
        rows.extend(Text("") for _ in range(max(0, min(3, max_rows) - len(window))))

    hint = Text(justify="center")
    notice = compose.get("notice", "")
    if status:
        hint.append(f" {status} ", style=accent_style)
    elif notice:
        # A one-off result (a pasted screenshot, a clipboard miss) takes the hint
        # row until the next keypress clears it.
        hint.append(f" {notice} ", style=accent_style)
    else:
        # Single-space separators: the lane is only 44 wide, and the wider spacing
        # used to run the last word under the border.
        for i, (key, what) in enumerate((("\u2191/\u2193", "field"), ("Enter", "send"), ("Esc", "cancel"))):
            hint.append(" \u00b7 " if i else " ", style=dim_style)
            hint.append(key, style=accent_style)
            hint.append(f" {what}", style=dim_style)
        hint.append(" ", style=dim_style)
    bubble = Panel(
        Group(*rows),
        title=Text(" Tell the duck ", style=text_style),
        title_align="left",
        box=rich.box.ROUNDED,
        border_style=border_style,
        padding=(0, 1),
        width=cols - 2,
    )
    bubble.subtitle = hint
    bubble.subtitle_align = "center"
    return bubble


def _build_niko_caption(fade: float, *, left_pad: int) -> RenderableType:
    """The mascot's own label — ``n  ask niko`` — centred under his head.

    The duck is Niko's door, and the rotating tip that says so is on screen for
    six seconds in every three minutes and vanishes with ``t``. So this row is
    permanent, and :func:`duck_hit` already reaches it — the words are clickable
    too, not just the sprite.

    It always occupies its row. The lane bottom-anchors, so a row that came and
    went would move the duck and everything anchored on him; it fades instead.
    """
    # Key first, in the tip row's gold, so it reads as a control rather than prose.
    caption = Text()
    caption.append("n", style=f"bold {lerp_color(fade, BLACK_RGB, _TIP_DOT_ON)}")
    caption.append("  ask niko", style=lerp_color(fade, BLACK_RGB, _TIP_DOT_DIM))
    # Centred under the head rather than the lane, and padded from the head's own
    # left pad, so it glides in with him instead of sitting still while he slides.
    indent = max(0, (_COMPANION_HEAD_W - caption.cell_len) // 2)
    return Padding(caption, (0, 0, 0, left_pad + indent))


def _build_companion(
    tip_line: Text,
    *,
    controls: Text | None = None,
    beak_open: bool = False,
    update_box: Panel | None = None,
    duck_lift: int | None = None,
    companion_intro: float = 1.0,
    extras_reveal: float | None = None,
    compose: dict | None = None,
    lane_h: int = 40,
    mascot: str = "duck",
) -> RenderableType:
    """Bottom-right idle duck (facing left, toward the menu) with the current tip
    in a speech bubble above it — and, above that, an optional ``update_box``.

    ``compose`` swaps the tip bubble for the feedback composer (see
    :func:`_build_compose_bubble`) and drops the update box, so the whole lane
    belongs to what you're typing.

    ``tip_line`` is the tip text from :func:`_build_tip_rows` (may be blank when
    tips are hidden — then only the duck shows). The bubble uses a plain, static
    copy of the tip: the per-frame cross-fade is dropped (it flickers in a box)
    and any leading emoji is stripped (a wide glyph in a bordered Panel breaks the
    border). ``update_box`` (from :func:`_build_update_box`) stacks above the tip
    bubble when a release is available. ``beak_open`` opens his bill for a quack
    when a new tip appears. Bottom-aligned so it sits in the corner regardless of
    terminal height.

    ``companion_intro`` (0→1) drives the screen-load entrance: the duck glides in
    from the right, and only once he has settled do the tip bubble and update box
    fade in above him. Defaults to 1.0 (fully settled) so static renders are
    unchanged.
    """
    # Duck faces left so he looks toward the mode list rather than the wall.
    # duck_lift not None → play the double-shades gag (sunglasses raised by that
    # many pixels, second pair revealed underneath); otherwise the resting head.
    # The gag is duck-only (see _mascot.py), so the robo companion always rests.
    head = (
        render_head_shades(duck_lift, flip=True)
        if duck_lift is not None and mascot == "duck"
        else render_head(0, flip=True, beak_open=beak_open, mascot=mascot)
    )
    # Entrance slide: left-pad the head so it glides from the right edge of the lane
    # into its centre (at intro 1.0 the pad equals the centred pad, so it matches the
    # old Align.center(head) exactly). Extras only appear once he's ~settled.
    intro = min(1.0, max(0.0, companion_intro))
    center_pad = max(0, (_COMPANION_COLS - _COMPANION_HEAD_W) // 2)
    # Constant-speed (linear) glide: the duck reaches its resting column exactly at
    # intro 1.0, so the last few characters glide in rather than snapping — the
    # ease-out variants reached the spot early (~85%) then sat, which read as the
    # duck jumping the final stretch.
    left_pad = int(center_pad + (_COMPANION_COLS - center_pad) * (1.0 - intro))
    # Pad on BOTH sides to the full lane width so the duck's column never shifts when
    # the tip bubble/controls above him appear or disappear (otherwise the group
    # narrows and Align.center re-centres the lone duck).
    right_pad = max(0, _COMPANION_COLS - _COMPANION_HEAD_W - left_pad)
    duck = Padding(head, (0, right_pad, 0, left_pad))
    # Tip/update-box opacity. Normally derived from the duck's entrance (they appear
    # once he's ~settled); ``extras_reveal`` overrides it so the exit transition can
    # fade them out while the duck stays put (see Phase 2b in mode_select).
    if extras_reveal is None:
        reveal = min(1.0, max(0.0, (intro - _COMPANION_REVEAL_FROM) / (1.0 - _COMPANION_REVEAL_FROM)))
    else:
        reveal = min(1.0, max(0.0, extras_reveal))
    show_extras = reveal > 0.0

    has_controls = controls is not None and controls.plain.strip()
    parts: list[RenderableType] = []
    if compose is not None:
        # The composer is drawn as an OVERLAY over the finished frame (see
        # _draw_compose_bubble), so it can be wider than this lane without
        # resizing the mode list beside it. The lane itself just loses its tip.
        presence = min(1.0, max(0.0, compose.get("presence", 1.0)))
        tail = Align.center(Text("▾", style=lerp_color(presence, BLACK_RGB, (120, 135, 150))))
        # The caption rides along: it is what the lane's foot is measured from, so
        # dropping it here would float the duck a row while you type.
        return Align.center(Group(tail, duck, _build_niko_caption(1.0, left_pad=left_pad)), vertical="bottom")
    if update_box is not None and show_extras:
        # More pressing than the tip: it sits at the top of the lane, above the
        # bubble, with a blank line separating the two boxes.
        parts.extend([update_box, Text("")])

    tip = tip_line.plain.strip()
    while tip and not (tip[0].isascii() and tip[0].isalnum()):  # drop a leading emoji/glyph
        tip = tip[1:]
    tip = tip.strip()
    if tip and show_extras:
        # Fade the bubble in over the reveal window (dark → its resting colours).
        text_color = lerp_color(reveal, BLACK_RGB, (198, 198, 208))
        border_color = lerp_color(reveal, BLACK_RGB, (90, 100, 110))
        bubble = Panel(
            Text(tip, style=text_color, justify="left"),
            box=rich.box.ROUNDED,
            border_style=border_color,
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
        tail = Align.center(Text("▾", style=border_color))
        parts.extend([bubble, tail])  # extend, not reassign — keep any update_box above
    elif has_controls and show_extras:
        # Tips hidden → no bubble to carry the "t show tips" hint; keep it visible.
        parts.append(controls)
    parts.append(duck)
    # Faded by the entrance, not by ``reveal``: the caption is the duck's own
    # label, so it arrives with him rather than with the tip bubble above him.
    parts.append(_build_niko_caption(intro, left_pad=left_pad))
    return Align.center(Group(*parts), vertical="bottom")


_SIGNIN_OVERLAY_COLS = 72  # widest the sign-in bubble grows before it stops
_SIGNIN_LEFT_MARGIN = 6  # frame columns kept clear to its left
_SIGNIN_TOP_MARGIN = 2  # …and above it


def _build_subscription_bubble(
    *,
    cols: int,
    url: str = "",
    spinner: str = "",
    awaiting_code: bool = False,
    code: str = "",
    cursor: int = 0,
    copied: bool = False,
    done: bool = False,
    ok: bool = False,
    detail: str = "",
) -> RenderableType:
    """The duck's sign-in bubble: the `claude setup-token` flow, as something he says.

    ``claude setup-token`` runs on a pty the TUI owns (see
    :class:`yeaboi.claude_auth.SubscriptionSignIn`), and its stages are drawn here:
    waiting for the authorize URL, showing that URL while the code is typed back,
    and the result. It is the duck's bubble rather than a page of its own because
    the flow is a detour from Settings — the settings behind it stay on screen and
    stay readable, which a full-screen takeover cost.

    The URL is the one thing the user must be able to act on, so it is an OSC-8
    hyperlink *and* copyable. Copy is ``tab``, not ``c``: once the code field is
    live, ``c`` is a character of the code.
    """
    amber = f"rgb({_TIP_DOT_ON[0]},{_TIP_DOT_ON[1]},{_TIP_DOT_ON[2]})"
    body, dim, muted = "rgb(198,198,208)", "rgb(120,130,140)", "rgb(150,150,165)"
    inner = max(12, cols - 4)
    rows: list[RenderableType] = []

    if done and ok:
        rows.append(Align.center(Text("\u2713  signed in", style=f"bold {amber}")))
        rows.append(Align.center(Text("your subscription token is saved", style=body)))
        rows.append(Align.center(Text("press any key", style=dim)))
        border = amber
    elif done:
        rows.append(Align.center(Text("sign-in failed", style="bold rgb(226,110,90)")))
        if detail:
            rows.append(Align.center(Text(detail[:inner], style=body)))
        rows.append(Align.center(Text("press any key", style=dim)))
        border = "rgb(226,110,90)"
    elif not url:
        line = Text(justify="center")
        line.append(f"{spinner} ", style=amber)
        line.append("opening your browser\u2026", style=body)
        rows.append(line)
        rows.append(Align.center(Text("esc to cancel", style=dim)))
        border = amber
    else:
        # "if it asks" rather than "then paste": the browser callback often
        # completes the flow on its own and no code is ever shown, which made the
        # old wording read as a step that had gone missing.
        rows.append(Align.center(Text("approve in the browser — paste a code if it asks", style=body)))
        rows.append(Text(""))
        # The URL has no spaces, so nothing will break it for us — it is sliced to
        # the bubble's inner width. Left-aligned rather than centred per line: the
        # lines are full-width except the last, and centring that one floats it out
        # of the block. Every line links to the whole URL.
        for offset in range(0, len(url), inner):
            rows.append(Text(url[offset : offset + inner], justify="left", style=f"{muted} underline link {url}"))
        rows.append(Text(""))
        field = Text(justify="center")
        field.append("code  ", style=muted)
        if awaiting_code:
            # Block cursor drawn into the value, the same gesture the settings rows
            # use for an in-place edit.
            field.append((code[:cursor] + "\u2588" + code[cursor:]) or "\u2588", style=body)
        else:
            field.append("waiting for the browser\u2026", style=dim)
        rows.append(field)
        border = amber

    bubble = Panel(
        Group(*rows),
        box=rich.box.ROUNDED,
        border_style=border,
        padding=(0, 1),
        width=cols,
        title=Text("sign in", style=f"bold {border}") if not done else None,
        title_align="left",
    )
    if not done and url:
        # Controls ride the bottom border, the way the tip bubble's do.
        bubble.subtitle = Text.assemble(
            ("tab", f"bold {amber}"),
            (" copied" if copied else " copy", dim),
            ("  enter", f"bold {amber}"),
            (" submit", dim),
            ("  esc", f"bold {amber}"),
            (" cancel", dim),
        )
        bubble.subtitle_align = "center"
    return bubble


def _draw_signin_bubble(console, options, lines: list, state: dict) -> None:
    """Composite the sign-in bubble over the finished frame, in place.

    Same anchoring as :func:`_draw_compose_bubble`: the duck occupies the last
    ``_COMPANION_HEAD_H`` rows above the music pocket, so his top row is a fixed
    offset from the foot and the bubble stacks up from there, right-aligned with
    where his tip bubble sits and growing leftward. Drawn over the frame rather
    than inside a lane so the page underneath is untouched — which is the point:
    the settings stay exactly where they were.
    """
    from rich.segment import Segment

    if not lines or not lines[-1]:
        return
    width = sum(seg.cell_length for seg in lines[-1]) or options.max_width
    height = len(lines)
    base = lines[-1][0].style
    bg_style = Style(bgcolor=base.bgcolor) if base and base.bgcolor else None

    duck_top = _companion_duck_bottom(height) - _COMPANION_HEAD_H  # the row above his crown
    bottom = duck_top - 1 - _COMPOSE_TAIL_ROWS
    right = width - 5
    cols = min(_SIGNIN_OVERLAY_COLS, right - _SIGNIN_LEFT_MARGIN)
    if cols < 32 or bottom < 4:
        return  # too cramped to overlay — the flow still works, it just isn't drawn

    bubble = _build_subscription_bubble(cols=cols, **state)
    # height=None matters: the incoming options carry the frame's full height, and
    # a Panel rendered under it expands to fill the screen instead of sizing to its
    # content — which is how this drew nothing at all rather than a bubble.
    bubble_opts = options.update(width=cols, height=None)
    rendered = console.render_lines(bubble, bubble_opts, pad=True, style=bg_style)
    top = bottom - len(rendered) + 1
    if top < _SIGNIN_TOP_MARGIN:
        return
    left = right - cols + 1
    for i, row in enumerate(rendered):
        r = top + i
        if not 0 <= r < height:
            continue
        before, _mid, after = Segment.divide(lines[r], [left, right + 1, width])
        lines[r] = list(before) + list(row) + list(after)
    # Tail pointing down at the duck, level with his head.
    tail_row = bottom + 1
    if 0 <= tail_row < height:
        tail = console.render_lines(
            Align.right(Text("\u25be", style="rgb(90,100,110)")),
            bubble_opts,
            pad=True,
            style=bg_style,
        )
        if tail:
            before, _mid, after = Segment.divide(lines[tail_row], [left, right + 1, width])
            lines[tail_row] = list(before) + list(tail[0]) + list(after)


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
    restart_in: int | None = None,
    can_restart: bool = False,
) -> Panel:
    """Modal shown by the ctrl+U update flow: a spinner while ``uv/pipx upgrade``
    runs, then a success or failure result.

    While running (``done=False``) it shows ``spinner`` + "updating to vX". On
    success the app relaunches itself onto the new version, so the screen counts
    ``restart_in`` down and offers esc as the way out. ``can_restart`` defaults to
    False — the honest screen, asking for a manual restart — so that only a caller
    that has actually resolved a relaunch command (see
    ``update_check.resolve_relaunch_command``) can promise one. On failure it shows the manual
    upgrade command so the user can run it themselves. Any key dismisses the
    result (handled by the caller).
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
        if can_restart:
            rows.append(Align.center(Text(f"restarting in {max(0, restart_in or 0)}…", style="rgb(198,198,208)")))
            rows.append(Align.center(Text("esc to stay on this one", style="rgb(120,130,140)")))
        else:
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
    return build_page_panel(
        Align.center(Group(*rows), vertical="middle"),
        border_style=border,
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
    panel = build_page_panel(
        Align.center(Group(*rows), vertical="middle"),
        border_style="rgb(226,186,96)",
        height=max(1, height),
        padding=(1, 2),
    )
    # This screen already shows the mascot centred — tell the app-wide music
    # chrome not to stamp a second duck in the corner (see MusicLive.get_renderable).
    panel._no_companion_duck = True
    return panel


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

    panel = build_page_panel(content, height=height)
    # These are menu<->page transition frames: we're on our way to/from the menu,
    # so the back tab must already be folding away rather than waiting for the
    # final menu frame (which is what made the retract look late).
    panel._no_back_hint = True
    return panel
