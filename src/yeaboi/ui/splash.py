"""Startup splash animation — "YEABOI" fades in then out.

# See docs: "Architecture" — the splash is a CLI-layer component shown
# before the setup wizard or mode selection screen. It replaces the static
# welcome panel as the first branded intro users see.

Animation sequence:
  Phase 1 — Paint in: the duck waddles left→right and the wordmark appears in
            his wake, as though he's drawing it (per-mode intros fade in instead).
  Phase 2 — Shine:    a diagonal white glint sweeps across the finished wordmark.
  Phase 3 — Crumble:  the wordmark dissolves top-left → bottom-right into the menu.
"""

from __future__ import annotations

import logging
import math
import re
import time

import rich.box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.shared._ascii_font import render_ascii_text, render_ascii_text_large
from yeaboi.ui.shared._components import NEUTRAL_BG
from yeaboi.ui.shared._wordmarks import get_shadow_wordmark

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Animation constants
# ---------------------------------------------------------------------------

_FRAME_TIME = 1.0 / 60  # ~60fps target

# Brand blue — same as mode_select._COLOR_RGB
_BRAND_RGB = (70, 100, 180)

# Brand wordmark — "YEABOI" in the ANSI Shadow figlet style (6 rows, all padded
# to the same width so Rich's per-line centre-justify keeps them aligned). This
# is a fixed hand-baked asset (no figlet/pyfiglet runtime dependency); the
# compact two-line render_ascii_text() font is still used for mode titles.
_WORDMARK: list[str] = [
    "██╗   ██╗███████╗ █████╗ ██████╗  ██████╗ ██╗",
    "╚██╗ ██╔╝██╔════╝██╔══██╗██╔══██╗██╔═══██╗██║",
    " ╚████╔╝ █████╗  ███████║██████╔╝██║   ██║██║",
    "  ╚██╔╝  ██╔══╝  ██╔══██║██╔══██╗██║   ██║██║",
    "   ██║   ███████╗██║  ██║██████╔╝╚██████╔╝██║",
    "   ╚═╝   ╚══════╝╚═╝  ╚═╝╚═════╝  ╚═════╝ ╚═╝",
]
_WORDMARK_WIDTH = 45  # cell width of every row above

# How far each successive wordmark row is nudged ahead of the one above it,
# so the shine reads as a slanted streak of light rather than a vertical bar.
_SHINE_ROW_SKEW = 0.03

# Duck paint-in intro: columns the duck advances per frame. Brisk enough to keep
# the whole boot snappy (the splash is on the critical path to the menu) while the
# letters are still visibly *drawn* in his wake rather than zoomed past.
_DUCK_PAINT_STEP = 2.4
# Walk cycle: vertical bob (pixels) applied to the duck as he strides, so he
# waddles up-and-down instead of gliding flat. Paired with the wing flap.
_WALK_BOB = (0, 0, 1, 1, 0, 0, 1, 1)
# Intro: he walks in from the left to this fraction of the wordmark's left edge,
# then YEABOI bursts out from behind him at _EXCLAIM_STEP cols/frame (fast — an
# exclamation), while he recoils with _EXCLAIM_RECOIL (a lean-back bob per frame).
_EXCLAIM_STEP = 5.0
_EXCLAIM_RECOIL = (3, 3, 2, 2, 1, 1, 0)

# ── New brand splash choreography (jump in → quack → wordmark → waddle-clear) ──
_SPLASH_JUMP = 6  # pixel rows of headroom above the baseline for the jump arc
_SPLASH_WALK_STEP = 2.6  # cols/frame the duck strides while clearing the wordmark
_SPLASH_CLEAR_LAG = 11  # cols behind the duck's left edge where the wordmark wipes


# ---------------------------------------------------------------------------
# Easing
# ---------------------------------------------------------------------------


def _ease_out_cubic(t: float) -> float:
    """Cubic ease-out: fast start, smooth deceleration."""
    return 1 - (1 - t) ** 3


# ---------------------------------------------------------------------------
# Frame builder
# ---------------------------------------------------------------------------


def _center_in_panel(rendered: Text, *, width: int, height: int, block_h: int) -> Panel:
    """Vertically centre a pre-built ``rendered`` Text block inside the panel."""
    inner_h = height - 4  # panel border + padding
    top_pad = max(0, (inner_h - block_h) // 2)
    bot_pad = max(0, inner_h - block_h - top_pad)

    content = Group(
        *[Text("") for _ in range(top_pad)],
        rendered,
        *[Text("") for _ in range(bot_pad)],
    )

    # Neutral dark base — the splash owns its background like every other page,
    # so all users see the same boot screen regardless of terminal theme.
    return Panel(
        content,
        style=f"on {NEUTRAL_BG}",
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _block_left_pad(text_lines: list[str], width: int) -> str:
    """Common left pad that centres the whole block by its widest row.

    Rich's per-line ``justify="center"`` rstrips each line and centres it by
    its own stripped width — rows with shorter trailing content (e.g. the P
    in STANDUP, whose bottom rows end 5 cells early) shift sideways and break
    the glyph columns. Centring the block once and rendering left-anchored
    keeps every row aligned, matching build_ascii_title's left-justify.
    """
    block_w = max((len(line) for line in text_lines), default=0)
    inner_w = max(0, width - 6)  # panel border + padding, matches _resolve_wordmark
    return " " * max(0, (inner_w - block_w) // 2)


def _build_splash_frame(
    text_lines: list[str],
    *,
    width: int,
    height: int,
    opacity: float = 1.0,
    rgb: tuple[int, int, int] = _BRAND_RGB,
) -> Panel:
    """Build a fade frame: the whole wordmark in one ``rgb`` colour at ``opacity``.

    text_lines: ASCII-art rows (a tall ANSI-Shadow wordmark, or the compact
        two-line render_ascii_text fallback). The block is centred as a whole
        (one shared left pad from ``_block_left_pad``) so rows with uneven
        trailing content never mis-align.
    opacity: 0.0–1.0 controls visibility. At 0 the text is invisible
        (spaces only) so it blends with any terminal background. At 1 the
        text is full ``rgb``.
    rgb: base colour (defaults to the brand blue used by the splash).
    """
    pad = _block_left_pad(text_lines, width)
    rendered = Text(justify="left")
    if opacity < 0.01:
        # At very low opacity, replace characters with spaces so nothing is
        # visible — avoids a near-black colour standing out against the
        # terminal background regardless of its colour scheme.
        for line_idx, line in enumerate(text_lines):
            rendered.append(pad + " " * len(line))
            if line_idx < len(text_lines) - 1:
                rendered.append("\n")
    else:
        r = int(rgb[0] * opacity)
        g = int(rgb[1] * opacity)
        b = int(rgb[2] * opacity)
        style = f"bold rgb({r},{g},{b})"
        for line_idx, line in enumerate(text_lines):
            rendered.append(pad)
            rendered.append(line, style=style)
            if line_idx < len(text_lines) - 1:
                rendered.append("\n")

    return _center_in_panel(rendered, width=width, height=height, block_h=len(text_lines))


def _shine_style(pos: float, hotspot: float, rgb: tuple[int, int, int] = _BRAND_RGB) -> str:
    """Per-character style: full ``rgb``, blended towards white near the glint.

    A tight Gaussian ``hotspot`` (0–1 across the wordmark) sweeps past each
    character at normalised column ``pos``; characters near it flare white.
    """
    dist = abs(pos - hotspot)
    intensity = math.exp(-(dist * dist) / 0.012)
    r, g, b = rgb
    r2 = int(r + (255 - r) * intensity)
    g2 = int(g + (255 - g) * intensity)
    b2 = int(b + (255 - b) * intensity)
    return f"bold rgb({r2},{g2},{b2})"


def _build_shine_frame(
    text_lines: list[str],
    *,
    width: int,
    height: int,
    hotspot: float,
    rgb: tuple[int, int, int] = _BRAND_RGB,
) -> Panel:
    """Build a shine frame: the fully-lit wordmark with a diagonal glint sweeping.

    ``hotspot`` travels roughly -0.2 → 1.2 so the highlight enters from the left,
    crosses the letters, and exits right. Each lower row is nudged slightly ahead
    (``_SHINE_ROW_SKEW``) so the highlight reads as a slanted streak of light.
    """
    span = max(len(line) for line in text_lines) - 1 or 1
    pad = _block_left_pad(text_lines, width)
    rendered = Text(justify="left")
    for line_idx, line in enumerate(text_lines):
        rendered.append(pad)
        for col, ch in enumerate(line):
            if ch == " ":
                rendered.append(" ")
                continue
            pos = col / span + line_idx * _SHINE_ROW_SKEW
            rendered.append(ch, style=_shine_style(pos, hotspot, rgb))
        if line_idx < len(text_lines) - 1:
            rendered.append("\n")

    return _center_in_panel(rendered, width=width, height=height, block_h=len(text_lines))


def _as_rgb(color: tuple[int, int, int] | str) -> tuple[int, int, int]:
    """Coerce an ``(r,g,b)`` tuple or an ``"rgb(r,g,b)"`` string to a tuple."""
    if isinstance(color, tuple):
        return color
    nums = re.findall(r"\d+", color)
    if len(nums) >= 3:
        return (int(nums[0]), int(nums[1]), int(nums[2]))
    return _BRAND_RGB


def _resolve_wordmark(word: str, available_width: int) -> list[str]:
    """Return the tall ANSI-Shadow rows for *word* if they fit, else compact art.

    Falls back to the two-line render_ascii_text font when the terminal is too
    narrow for the baked wordmark (e.g. "Performance" on an 80-col terminal), so
    the intro never wraps into an unreadable mess.
    """
    art = get_shadow_wordmark(word)
    if art and len(art[0]) + 6 <= available_width:  # +6 for panel border + padding
        return art
    return render_ascii_text(word)


def _build_crumble_frame(
    text_lines: list[str],
    *,
    width: int,
    height: int,
    progress: float,
    rgb: tuple[int, int, int] = _BRAND_RGB,
) -> Panel:
    """Build a dissolve frame: characters clear top-left → bottom-right.

    ``progress`` 0→1 sweeps a diagonal front across the block — a character at
    (row, col) has cleared once ``(row + col)`` normalised is past ``progress`` —
    so the wordmark crumbles away from the top-left corner instead of fading as a
    whole. Cleared cells become spaces so nothing lingers against the background.
    """
    pad = _block_left_pad(text_lines, width)
    max_r = max(len(text_lines) - 1, 0)
    max_c = max((len(line) for line in text_lines), default=1) - 1
    denom = max(max_r + max_c, 1)
    style = f"bold rgb({rgb[0]},{rgb[1]},{rgb[2]})"
    rendered = Text(justify="left")
    for r, line in enumerate(text_lines):
        rendered.append(pad)
        for c, ch in enumerate(line):
            if ch == " " or (r + c) / denom <= progress:
                rendered.append(" ")
            else:
                rendered.append(ch, style=style)
        if r < len(text_lines) - 1:
            rendered.append("\n")
    return _center_in_panel(rendered, width=width, height=height, block_h=len(text_lines))


def _build_run_frame(
    text_lines: list[str],
    *,
    width: int,
    height: int,
    duck_col: int,
    rgb: tuple[int, int, int] = _BRAND_RGB,
    reveal_front: float | None = None,
    duck_frame: int = 0,
    duck_bob: int = 0,
) -> Panel:
    """The wordmark with the duck composited on top at column ``duck_col`` — used
    to run him left→right across the text. The full-body duck (legs and all) faces
    right (his travel direction), his wing flaps via ``duck_frame`` and he bobs up
    by ``duck_bob`` pixels (the walk cycle); his transparent cells let the wordmark
    show through.

    ``reveal_front`` paints the wordmark *in his wake*: only wordmark cells whose
    canvas column is left of ``reveal_front`` are drawn (the rest stay blank), so
    as the duck advances the letters appear from under his body. ``None`` shows the
    whole wordmark (he just runs over a fully-drawn logo).
    """
    from yeaboi.ui.shared._mascot import mini_cells

    inner_w = max(1, width - 6)  # matches _block_left_pad / _center_in_panel
    cells = mini_cells(duck_frame)  # full body, faces right = direction of travel
    duck_h = len(cells)
    block_h = max(len(text_lines), duck_h) + 1  # +1 row headroom so the walk bob never clips his crown
    canvas: list[list[tuple[str, str | None]]] = [[(" ", None)] * inner_w for _ in range(block_h)]

    brand = f"bold rgb({rgb[0]},{rgb[1]},{rgb[2]})"
    pad_len = len(_block_left_pad(text_lines, width))
    wm_top = (block_h - len(text_lines)) // 2
    for r, line in enumerate(text_lines):
        for c, ch in enumerate(line):
            x = pad_len + c
            if ch != " " and 0 <= x < inner_w and (reveal_front is None or x < reveal_front):
                canvas[wm_top + r][x] = (ch, brand)

    duck_top = (block_h - duck_h - 1) // 2 + 1 - duck_bob  # rest 1 row down; bob lifts him into the headroom
    for r, row in enumerate(cells):
        for c, (glyph, style) in enumerate(row):
            if glyph == " " and style is None:
                continue  # transparent duck cell — let the wordmark show
            x = duck_col + c
            if 0 <= x < inner_w and 0 <= duck_top + r < block_h:
                canvas[duck_top + r][x] = (glyph, style)

    rows: list[Text] = []
    for row in canvas:
        line = Text()
        for glyph, style in row:
            line.append(glyph, style=style)
        rows.append(line)
    return _center_in_panel(Group(*rows), width=width, height=height, block_h=block_h)


def _run_wordmark_animation(
    console: Console,
    live: object,
    text_lines: list[str],
    rgb: tuple[int, int, int],
    *,
    fade_in_frames: int,
    shine_frames: int,
    fade_out_frames: int,
    frame_time: float,
    crumble: bool = False,
    run_duck: bool = False,
) -> None:
    """Drive ``live`` through reveal → diagonal shine → exit for a wordmark.

    Shared by the brand splash and the per-mode intros. ``live`` is any object
    with an ``update(renderable)`` method (a Rich Live). Glint travels from just
    off the left edge to past the right edge so it enters and fully exits cleanly.

    The reveal is one of two: when ``run_duck`` the duck waddles left→right and
    *paints the wordmark in his wake* (letters emerge from under him — the splash);
    otherwise the whole block fades up from nothing (the per-mode intros).
    """
    shine_start, shine_end = -0.25, 1.4

    # Phase 1 — Reveal.
    if run_duck:
        # The duck waddles in from the left and stops at the wordmark's left edge;
        # then YEABOI bursts out from behind him (reveal sweeps right past his body)
        # as he recoils, as if he's exclaiming it.
        from yeaboi.ui.shared._mascot import mini_cells

        _cells = mini_cells()
        _duck_w = len(_cells[0]) if _cells else 0
        w, h = console.size
        _rest = len(_block_left_pad(text_lines, w))  # wordmark's left column

        # Phase A — walk in from off-left to his resting spot, no wordmark yet.
        _col = float(-_duck_w)
        _step_i = 0
        while _col < _rest:
            live.update(
                _build_run_frame(
                    text_lines,
                    width=w,
                    height=h,
                    duck_col=int(_col),
                    rgb=rgb,
                    reveal_front=0.0,  # hide the whole wordmark while he arrives
                    duck_frame=_step_i // 3,
                    duck_bob=_WALK_BOB[_step_i % len(_WALK_BOB)],
                ),
                refresh=True,
            )
            time.sleep(frame_time)
            _step_i += 1
            _col += _DUCK_PAINT_STEP

        # Phase B — YEABOI springs out from behind him; he leans back (recoil bob).
        _front = float(_rest)
        _r = 0
        inner_w = max(1, w - 6)
        while _front < inner_w:
            live.update(
                _build_run_frame(
                    text_lines,
                    width=w,
                    height=h,
                    duck_col=_rest,
                    rgb=rgb,
                    reveal_front=_front,
                    duck_frame=0,
                    duck_bob=_EXCLAIM_RECOIL[_r] if _r < len(_EXCLAIM_RECOIL) else 0,
                ),
                refresh=True,
            )
            time.sleep(frame_time)
            _front += _EXCLAIM_STEP
            _r += 1

        # Settle on the fully-drawn wordmark (duck gone), then straight to the crumble.
        w, h = console.size
        live.update(_build_splash_frame(text_lines, width=w, height=h, opacity=1.0, rgb=rgb), refresh=True)
    else:
        # Fade in: nothing → colour.
        for frame in range(fade_in_frames):
            t = _ease_out_cubic(frame / max(fade_in_frames - 1, 1))
            w, h = console.size
            live.update(_build_splash_frame(text_lines, width=w, height=h, opacity=t, rgb=rgb), refresh=True)
            time.sleep(frame_time)
        # Phase 2 — Shine: a diagonal glint sweeps across the fully-lit wordmark
        # (per-mode intros only; the duck splash's paint already served as reveal).
        for frame in range(shine_frames):
            t = frame / max(shine_frames - 1, 1)
            hotspot = shine_start + (shine_end - shine_start) * t
            w, h = console.size
            live.update(_build_shine_frame(text_lines, width=w, height=h, hotspot=hotspot, rgb=rgb), refresh=True)
            time.sleep(frame_time)

    # Phase 3 — Exit: either a whole-block fade (per-mode intros) or a top-left →
    # bottom-right crumble (the brand splash), depending on ``crumble``.
    for frame in range(fade_out_frames):
        t = _ease_out_cubic(frame / max(fade_out_frames - 1, 1))
        w, h = console.size
        if crumble:
            live.update(_build_crumble_frame(text_lines, width=w, height=h, progress=t, rgb=rgb), refresh=True)
        else:
            live.update(_build_splash_frame(text_lines, width=w, height=h, opacity=1.0 - t, rgb=rgb), refresh=True)
        time.sleep(frame_time)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _compose_splash_frame(
    wordmark: list[str],
    *,
    width: int,
    height: int,
    duck_cells: list,
    duck_x: int,
    duck_lift: int = 0,
    reveal_front: float | None = None,
    clear_line: float | None = None,
    rgb: tuple[int, int, int] = _BRAND_RGB,
    marks: list[tuple[int, int, str, str]] | None = None,
) -> Panel:
    """Composite one brand-splash frame: the wordmark plus the duck on top of it.

    The duck's feet sit on the baseline; ``duck_lift`` raises him (the jump arc /
    walk bob). ``reveal_front`` reveals the wordmark left→right (columns left of the
    front are shown) as it appears; ``clear_line`` wipes it left→right (columns left
    of the line are hidden) as the duck walks over it. ``marks`` are extra glyphs in
    canvas space (the quack ripple). His transparent cells let the wordmark show
    through underneath.
    """
    inner_w = max(1, width - 6)
    duck_h = len(duck_cells)
    wm_h = len(wordmark)
    block_h = duck_h + _SPLASH_JUMP + 1
    baseline = block_h - 1
    canvas: list[list[tuple[str, str | None]]] = [[(" ", None)] * inner_w for _ in range(block_h)]
    # One brand colour for every wordmark cell — the ░▀▄ glyphs carry the texture
    # themselves (the scaled menu font keeps its light-shade dots and half-block
    # edges), exactly like a menu title, so no extra dither is needed.
    brand = f"bold rgb({rgb[0]},{rgb[1]},{rgb[2]})"

    wm_w = max((len(line) for line in wordmark), default=0)
    wm_left = max(0, (inner_w - wm_w) // 2)
    wm_top = baseline - wm_h + 1
    for r, line in enumerate(wordmark):
        for c, ch in enumerate(line):
            if ch == " ":
                continue
            x = wm_left + c
            if not (0 <= x < inner_w):
                continue
            if reveal_front is not None and x >= reveal_front:
                continue  # not yet revealed
            if clear_line is not None and x < clear_line:
                continue  # already wiped behind the duck
            canvas[wm_top + r][x] = (ch, brand)

    duck_top = baseline - duck_h + 1 - duck_lift
    for r, row in enumerate(duck_cells):
        for c, (glyph, style) in enumerate(row):
            if glyph == " " and style is None:
                continue  # transparent duck cell — let the wordmark show through
            x = duck_x + c
            y = duck_top + r
            if 0 <= x < inner_w and 0 <= y < block_h:
                canvas[y][x] = (glyph, style)

    for mx, my, mg, ms in marks or []:
        if 0 <= mx < inner_w and 0 <= my < block_h:
            canvas[my][mx] = (mg, ms)

    rows: list[Text] = []
    for row in canvas:
        line = Text()
        for glyph, style in row:
            line.append(glyph, style=style)
        rows.append(line)
    return _center_in_panel(Group(*rows), width=width, height=height, block_h=block_h)


def _run_splash_intro(
    console: Console, live: object, wordmark: list[str], rgb: tuple[int, int, int], *, frame_time: float
) -> None:
    """Drive the brand splash: the duck jumps in, quacks, YEABOI bursts out in his
    voice, then he waddles across it and wipes it clean into the menu.

    Sprites come from the shared mascot (the mini full-body duck, facing right —
    his travel direction). Layout is recomputed from ``console.size`` each frame so
    a resize mid-intro never tears the frame.
    """
    from yeaboi.ui.shared._mascot import mini_cells

    w, h = console.size
    inner_w = max(1, w - 6)
    duck_w = max((len(r) for r in mini_cells(0)), default=0)
    wm_w = max((len(line) for line in wordmark), default=0)
    wm_left = max(0, (inner_w - wm_w) // 2)
    wm_right = wm_left + wm_w
    # Rest just left of the wordmark so YEABOI appears to his right; never off-screen.
    rest_x = max(1, min(wm_left - duck_w - 1, inner_w - duck_w - 1))

    # ── Phase 1: JUMP IN — arc from off the left edge onto the rest spot. ──
    start_x = -duck_w - 2
    jump_frames = 24
    for f in range(jump_frames + 1):
        p = f / jump_frames
        dx = int(start_x + (rest_x - start_x) * _ease_out_cubic(p))
        lift = int(round(_SPLASH_JUMP * 4 * p * (1 - p)))  # parabola: rise then land
        w, h = console.size
        live.update(
            _compose_splash_frame(
                wordmark,
                width=w,
                height=h,
                duck_cells=mini_cells(f),
                duck_x=dx,
                duck_lift=lift,
                reveal_front=0.0,
                rgb=rgb,
            ),
            refresh=True,
        )
        time.sleep(frame_time)

    # ── Phase 2: EXCLAIM — after he lands he rears back, then YEABOI bursts out to
    #    his right as he snaps forward (his recoil alone is the exclamation). ──
    # Wind-up: rear back over a few frames.
    for lift in (1, 2, 3, 3):
        w, h = console.size
        live.update(
            _compose_splash_frame(
                wordmark,
                width=w,
                height=h,
                duck_cells=mini_cells(0),
                duck_x=rest_x,
                duck_lift=lift,
                reveal_front=0.0,
                rgb=rgb,
            ),
            refresh=True,
        )
        time.sleep(frame_time)

    # Burst: YEABOI sweeps out left→right while he snaps back down (recoil).
    front = float(wm_left)
    step_i = 0
    while front < wm_right + 1:
        lift = _EXCLAIM_RECOIL[step_i] if step_i < len(_EXCLAIM_RECOIL) else 0
        w, h = console.size
        live.update(
            _compose_splash_frame(
                wordmark,
                width=w,
                height=h,
                duck_cells=mini_cells(0),
                duck_x=rest_x,
                duck_lift=lift,
                reveal_front=front,
                rgb=rgb,
            ),
            refresh=True,
        )
        time.sleep(frame_time)
        front += _EXCLAIM_STEP
        step_i += 1

    # Hold the finished wordmark for a beat.
    for _ in range(8):
        w, h = console.size
        live.update(
            _compose_splash_frame(wordmark, width=w, height=h, duck_cells=mini_cells(0), duck_x=rest_x, rgb=rgb),
            refresh=True,
        )
        time.sleep(frame_time)

    # ── Phase 4: waddle across, wiping the wordmark clean behind him, then off. ──
    col = float(rest_x)
    step_i = 0
    while col < inner_w:
        clear_line = col + _SPLASH_CLEAR_LAG
        w, h = console.size
        live.update(
            _compose_splash_frame(
                wordmark,
                width=w,
                height=h,
                duck_cells=mini_cells(step_i // 3),
                duck_x=int(col),
                duck_lift=_WALK_BOB[step_i % len(_WALK_BOB)],
                clear_line=clear_line,
                rgb=rgb,
            ),
            refresh=True,
        )
        time.sleep(frame_time)
        col += _SPLASH_WALK_STEP
        step_i += 1


def show_splash(console: Console) -> None:
    """Show the startup splash animation (~2s). Non-interactive, timed.

    # See docs: "Architecture" — this replaces _build_welcome_panel() as
    # the first thing users see. "YEABOI" fades in from nothing, a diagonal
    # glint sweeps across it, then it fades back out to nothing.

    Alt-screen management: the animation runs on Live(screen=True) so Rich
    double-buffers each frame — the fade/shine never flickers. Live restores
    the normal screen on exit; the next fullscreen UI (setup wizard or
    mode-select) re-enters alt-screen with its own screen=True. The cost is a
    single brief flash at the boundary, far preferable to a flickering intro.
    """
    w, h = console.size
    # "YEABOI" in the main-menu block font, scaled up (see render_ascii_text_large)
    # — the same alphabet the mode titles use, just bigger. Pick the largest scale
    # that leaves room for the duck beside it; fall back to the compact font when
    # the terminal is too narrow for even the 2× render.
    inner_w = max(1, w - 6)
    duck_w = 22  # mini duck sprite width
    text_lines = render_ascii_text("YEABOI")
    for _scale in (3, 2):
        _candidate = render_ascii_text_large("YEABOI", _scale)
        if max(len(line) for line in _candidate) + duck_w + 4 <= inner_w:
            text_lines = _candidate
            break

    logger.info("splash: shown")
    _splash_start = time.monotonic()

    # The duck now traverses the whole screen (jump in → waddle across), so a
    # non-buffered inline Live diff-redraws a dozen scattered rows every frame and
    # tears badly. Run on screen=True: Rich double-buffers the alternate screen and
    # writes one atomic full-frame each refresh, so the motion is flicker-free. Its
    # __exit__ restores the normal screen, so we re-enter the alt-screen right after
    # to keep the seamless splash → wizard/menu handoff (one continuous alt-screen).
    console.set_alt_screen(True)
    console.clear()

    # Use a plain Live (not make_live/MusicLive): the splash is a non-interactive
    # intro with no music key controls, so the persistent music bar must NOT be
    # stamped onto its border — it first appears on the next fullscreen screen.
    with Live(
        _build_splash_frame(text_lines, width=w, height=h, opacity=0.0),
        console=console,
        # auto_refresh off: the animation loop drives one deterministic render per
        # frame (via update(refresh=True)). Otherwise Rich's background thread
        # re-renders on its own cadence too, and the two interleave into flicker.
        auto_refresh=False,
        screen=True,
        vertical_overflow="crop",
    ) as live:
        _run_splash_intro(console, live, text_lines, _BRAND_RGB, frame_time=_FRAME_TIME)

    # Re-enter the alt-screen (screen=True Live left it on exit) so the wizard /
    # mode-select draws straight over the splash with no clear-to-shell gap.
    console.set_alt_screen(True)
    logger.debug("splash: completed in %.2fs", time.monotonic() - _splash_start)


def play_wordmark_intro(
    console: Console,
    live: object,
    word: str,
    color: tuple[int, int, int] | str,
    *,
    frame_time: float = _FRAME_TIME,
) -> None:
    """Play a snappy fade-in + shine intro for *word* on an existing ``live``.

    Used for the cinematic per-mode entrances (Planning, Retro, …): reuses the
    caller's Rich Live so there is no nested-Live flicker, renders the mode name
    as an ANSI-Shadow wordmark (falling back to the compact font when the
    terminal is too narrow), and tints it with the mode's accent ``color`` (an
    ``(r,g,b)`` tuple or ``"rgb(r,g,b)"`` string). Timing is derived from
    ``frame_time`` so it looks the same regardless of the caller's frame rate.
    """
    rgb = _as_rgb(color)
    text_lines = _resolve_wordmark(word, console.size[0])
    logger.debug("splash: wordmark intro '%s' shown", word)

    def _frames(seconds: float) -> int:
        if frame_time <= 0:
            return 1
        return max(1, round(seconds / frame_time))

    _run_wordmark_animation(
        console,
        live,
        text_lines,
        rgb,
        fade_in_frames=_frames(0.32),
        shine_frames=_frames(0.75),
        fade_out_frames=_frames(0.24),
        frame_time=frame_time,
    )
