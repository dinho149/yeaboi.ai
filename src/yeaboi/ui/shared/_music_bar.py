"""Persistent music status bar — rendered on every screen's bottom border.

# See docs: "Music (ffplay)" and "TUI system" — this is the whole-app view for
# the optional background-music feature in :mod:`yeaboi.music`.

Two chokepoints let a single, always-visible music indicator cover the entire app
without touching ~30 screen builders:

- **Render.** :class:`MusicLive` subclasses Rich's ``Live`` and, on every
  ``update``, stamps a compact status line onto the ``Panel``'s bottom **border**
  (``Panel.subtitle``). Because it edits the border rather than adding a footer
  row, no screen needs its height recomputed. Every screen already renders through
  a single ``Live`` object built at ~4 sites, so swapping those to :func:`make_live`
  is all it takes. Meaningful subtitles set by transient popups are left untouched.
- **Control** lives in ``read_key`` (Ctrl+P / Ctrl+O) — see ``_input.py``.

:func:`nudge_music_bar` lets :mod:`yeaboi.music` force an immediate redraw
after a state change so even blocking-input screens reflect it instantly (most
screens already re-render at 30–60 fps).
"""

from __future__ import annotations

import logging
import math
import time

from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.text import Text

from yeaboi import music
from yeaboi.ui.shared._components import NEUTRAL_BG, PLANNING_THEME, Theme

logger = logging.getLogger(__name__)

# The MusicLive currently rendering the app, so music.py can nudge it after a
# state change. Set on every update(); there is only ever one live screen.
_active: MusicLive | None = None

# Rising block glyphs for the mini equalizer, tallest last.
_EQ_CHARS = "▁▂▃▄▅▆▇█"


def _eq_bars(count: int = 4) -> str:
    """Return a tiny animated equalizer string, driven by the wall clock.

    Each bar bounces at a slightly different rate/phase so the row looks lively.
    Because it reads ``time.monotonic()`` it advances on every render frame while
    music plays (the planning screens re-render continuously), with no timer of
    its own. Pure/stateless apart from the clock.
    """
    t = time.monotonic()
    return "".join(
        _EQ_CHARS[int((math.sin(t * (6.0 + 1.7 * i) + i) + 1.0) / 2.0 * (len(_EQ_CHARS) - 1))] for i in range(count)
    )


def _connecting_dots() -> str:
    """Return an animated ``connecting`` ellipsis (``   `` → ``.  `` → ``.. `` → ``...``).

    Driven by ``time.monotonic()`` like :func:`_eq_bars`, so it advances on the same
    render frames with no timer of its own. Shown while the stream is buffering (see
    :func:`yeaboi.music.is_connecting`) so the silent gap before audio starts looks
    like progress, not a broken player.
    """
    n = int(time.monotonic() * 2.5) % 4  # ~2.5 Hz cycle through 0..3 dots
    return ("." * n).ljust(3)


def build_music_subtitle(theme: Theme = PLANNING_THEME) -> Text:
    """Return the compact music status line for a Panel's bottom border.

    Shows the player state plus the two control-chord hints, e.g.
    ``♪ Lofi · playing   ctrl+P pause · ctrl+O channel``. When ffplay isn't installed it
    shows a dim, one-line install hint instead so the feature stays discoverable;
    when a spawned player died on its own it shows a dim crash notice in place of
    ``off`` (see :func:`yeaboi.music.last_error`).
    Styled with the shared Theme palette (no hardcoded RGB), matching the rest of
    the TUI.
    """
    available, _reason = music.is_music_available()
    if not available:
        return Text("♪ music: brew install ffmpeg ", style=theme.dim, justify="right")
    status = music.status()
    line = Text(justify="right")
    if status == "stopped":
        # A crashed daemon reverts to "stopped" (see music._reconcile_status); show
        # why rather than a bare "off" so a silently-broken player is diagnosable.
        err = music.last_error()
        line.append(f"♪ {err} " if err else "♪ off ", style=theme.dim if err else theme.muted)
        toggle_hint = "ctrl+P play"
    else:
        line.append("♪ ", style=theme.accent)
        line.append(music.current_channel_name(), style=theme.accent_bright)
        line.append(" · ", style=theme.muted)
        if status == "playing":
            # A freshly-started stream is "playing" but not yet audible while it
            # connects/buffers; show a progress ellipsis instead of the equalizer so
            # the silent gap doesn't read as broken (see music.is_connecting).
            if music.is_connecting():
                line.append("connecting", style=theme.value)
                line.append(_connecting_dots(), style=theme.accent_bright)
            else:
                line.append("playing ", style=theme.value)
                line.append(_eq_bars(), style=theme.accent_bright)  # animated equalizer
            toggle_hint = "ctrl+P pause"
        else:
            line.append("paused", style=theme.value)
            toggle_hint = "ctrl+P play"
    # Single tidy gap between the status/equalizer and the control hints (was a
    # double gap that left the visualizer stranded).
    line.append(f"  {toggle_hint} · ctrl+O channel ", style=theme.dim)
    return line


def draw_music_pocket(console, options, lines: list, *, preserve_content: bool = False) -> None:
    """Overwrite the bottom three rows of ``lines`` with the music pocket, in place.

    The alcove is a rounded roof, the music one line below it between the alcove's
    vertical walls, and the bottom border curving up-and-under (no floor beneath
    the music). This is the SINGLE implementation of the pocket geometry — both the
    app-wide :class:`_MusicPocketFrame` and the welcome screen's ``_WelcomeFrame``
    call it, so the two bars are pixel-identical (same columns, same look).

    A no-op when the panel is too narrow to box the bar or has fewer than three
    rows. Matches the panel's own border colour so it blends in on every screen.
    """
    if not lines or not lines[-1]:
        return
    width = sum(seg.cell_length for seg in lines[-1]) or options.max_width
    bstyle = lines[-1][0].style
    # The bottom-border segment carries `border_style on page_bg`, so its bgcolor
    # is the page's own tint. Use it as the base style for every row we render here
    # so blank padding cells inherit the page background (main #104) instead of
    # falling through to the terminal's default — otherwise the pocket band and the
    # gaps beside it show as an untinted (black) strip once the page is tinted.
    bg_style = Style(bgcolor=bstyle.bgcolor) if bstyle and bstyle.bgcolor else None
    # Many pages build their panel one row short (a legacy `h - 1` safety margin
    # from before the Live cropped overflow). Pad it back up to the true terminal
    # height so the pocket border lands on the BOTTOM row — otherwise it sits a row
    # high with a dark gap beneath ("the bottom border moved up on entry"). Blank
    # interior rows (│ … │ in the border colour) are inserted just above the border.
    term_h = options.max_height or (options.size.height if options.size else 0)
    if term_h and len(lines) < term_h:
        blank = Text()
        blank.append("│", style=bstyle)
        blank.append(" " * max(0, width - 2))
        blank.append("│", style=bstyle)
        blank_line = console.render_lines(blank, options.update_width(width), pad=True, style=bg_style)[0]
        lines[-1:-1] = [blank_line for _ in range(term_h - len(lines))]
    music = build_music_subtitle()
    mw = music.cell_len + 4  # ╭ + space + music + space + ╮
    if mw + 6 > width or len(lines) < 3:
        return
    right = width - 3  # ╮ column (inset from the right wall)
    left = right - mw + 1  # ╭ column
    # Roof row — the alcove's top border sits ABOVE the music, its rounded corners
    # spanning the pocket. The panel side walls (│ … │) are kept so it doesn't break.
    roof = Text()
    roof.append("│", style=bstyle)
    roof.append(" " * (left - 1))
    roof.append("╭" + "─" * (right - left - 1) + "╮", style=bstyle)
    roof.append(" " * (width - right - 2))
    roof.append("│", style=bstyle)
    # Text row — the music one line below the roof, bracketed by the alcove walls.
    textrow = Text()
    textrow.append("│", style=bstyle)
    textrow.append(" " * (left - 1))
    textrow.append("│ ", style=bstyle)
    textrow.append_text(music)
    textrow.append(" │", style=bstyle)
    textrow.append(" " * (width - right - 2))
    textrow.append("│", style=bstyle)
    # Border row — the bottom border rises at both alcove walls and curves
    # up-and-under the music, then drops back to the corners.
    border = Text()
    border.append("╰" + "─" * (left - 1) + "╯", style=bstyle)
    border.append(" " * (right - left - 1))
    border.append("╰" + "─" * (width - right - 2) + "╯", style=bstyle)
    sized = options.update_width(width)
    lines[-1] = console.render_lines(border, sized, pad=True, style=bg_style)[0]
    if preserve_content:
        # Splice ONLY the alcove columns onto the roof/text rows, leaving the rest of
        # those rows as-is — so e.g. the screensaver duck can walk right down at the
        # border and still show through the pocket band, instead of a blank strip.
        from rich.segment import Segment

        roof_alcove = Text()
        roof_alcove.append("╭" + "─" * (right - left - 1) + "╮", style=bstyle)
        text_alcove = Text()
        text_alcove.append("│ ", style=bstyle)
        text_alcove.append_text(music)
        text_alcove.append(" │", style=bstyle)
        asized = options.update_width(right - left + 1)
        for idx, alcove in ((-3, roof_alcove), (-2, text_alcove)):
            seg = console.render_lines(alcove, asized, pad=True, style=bg_style)[0]
            lft, _mid, rgt = Segment.divide(lines[idx], [left, right + 1, width])
            lines[idx] = list(lft) + list(seg) + list(rgt)
    else:
        lines[-3] = console.render_lines(roof, sized, pad=True, style=bg_style)[0]
        lines[-2] = console.render_lines(textrow, sized, pad=True, style=bg_style)[0]


# ── Back tab (bottom-left "go back" pocket) ────────────────────────────────
# Mirrors the music pocket on the LEFT, shown on every back-capable screen (all
# but the main menu, which marks itself with ``_no_back_hint``). Clickable: its
# on-screen rect is published in ``_back_region`` and ``read_key`` turns a click
# there into an Esc, so it works app-wide without touching per-screen loops.
_back_region: tuple[int, int, int, int] | None = None  # (x0, y0, x1, y1), 1-based inclusive
# Eased presence 0→1: the tab glides IN when a back-capable screen shows it
# (target 1) and glides back OUT when you leave for a screen that doesn't
# (target 0). Its slide position and click-gate both derive from this.
_back_presence = 0.0


def back_region() -> tuple[int, int, int, int] | None:
    """The clickable rect of the back tab this frame (1-based inclusive), or None
    when it isn't shown. Read by ``read_key`` to map a click there onto Esc."""
    return _back_region


def _clear_back_region() -> None:
    """Forget last frame's back-tab rect so a click only maps to Esc on a frame
    that actually drew the tab (reset at the top of every render)."""
    global _back_region
    _back_region = None


# Force-retract latch: set when a back press fires so the tab folds away on the
# press (even while the still-back-capable screen keeps asking to show it), rather
# than waiting for the destination screen to render. The fold holds for a short
# beat first so it doesn't snap away the instant the key lands.
_BACK_RETRACT_DELAY = 0.1  # seconds to hold the tab before the fold starts
_back_retracting = False
_back_retract_at = 0.0  # monotonic time the fold may begin


def retract_back_tab() -> None:
    """Arm the back tab's fold-away — called when the back button (or Esc) is
    pressed, so the retract belongs to the press rather than the next screen's
    entrance. The fold itself starts after ``_BACK_RETRACT_DELAY``."""
    global _back_retracting, _back_retract_at
    _back_retracting = True
    _back_retract_at = time.monotonic() + _BACK_RETRACT_DELAY


def build_back_text(theme: Theme = PLANNING_THEME) -> Text:
    """The compact 'go back' line for the left pocket, styled like the music bar."""
    line = Text(justify="left")
    line.append("‹ ", style=theme.accent)
    line.append("back", style=theme.value)
    line.append("  esc ", style=theme.dim)
    return line


def build_copy_text(theme: Theme = PLANNING_THEME) -> Text:
    """The 'copy' line for the tab that sits beside the back tab on pages that
    support copy-to-clipboard (Usage, Changelog, …)."""
    line = Text(justify="left")
    line.append("c ", style=theme.accent)
    line.append("copy", style=theme.value)
    return line


# Clickable rects of the sibling tabs drawn beside the back tab this frame:
# (x0, y0, x1, y1, key) — a click inside one is reported as that key press.
_tab_regions: list[tuple[int, int, int, int, str]] = []


def chrome_tab_regions() -> list[tuple[int, int, int, int, str]]:
    """Clickable (x0, y0, x1, y1, key) rects of the chrome's sibling tabs."""
    return _tab_regions


def draw_back_pocket(console, options, lines: list, target: float = 1.0, *, extra_tabs: list | None = None) -> None:
    """Draw a rounded 'go back' tab in the bottom-LEFT, mirroring the music pocket.

    ``target`` is 1.0 on back-capable screens (glide in) and 0.0 on screens that
    aren't (glide out). An eased ``_back_presence`` drives the slide position both
    ways, so the tab animates AWAY when you leave, not just in when you arrive.
    Runs AFTER :func:`draw_music_pocket`, splicing a left alcove into the bottom
    three rows. Publishes its clickable rect in ``_back_region`` once mostly in.
    """
    from rich.segment import Segment

    global _back_region, _back_presence, _back_retracting, _tab_regions
    _back_region = None
    _tab_regions = []
    if not lines or len(lines) < 3 or not lines[-1]:
        return

    # A pressed back button folds the tab away immediately, overriding the
    # screen's own target until it's gone (then the latch releases). The latched
    # fold is much faster than the glide-in so it reads as happening ON the press,
    # not trailing into the next screen's entrance animation.
    ease = 0.22
    if _back_retracting:
        if time.monotonic() < _back_retract_at:
            target = 1.0  # hold it up for a beat so the fold doesn't snap on keydown
        else:
            target = 0.0
            ease = 0.55
    # Ease toward the target every frame — glides in (→1) and out (→0).
    _back_presence += (target - _back_presence) * ease
    if target <= 0.0 and _back_presence < 0.02:
        _back_presence = 0.0
        _back_retracting = False
        return  # fully retracted — nothing to draw

    width = sum(seg.cell_length for seg in lines[-1]) or options.max_width
    bstyle = lines[-1][0].style
    bg_style = Style(bgcolor=bstyle.bgcolor) if bstyle and bstyle.bgcolor else None
    back = build_back_text()
    bw = back.cell_len + 4  # ╭ + space + text + space + ╮
    if bw + 8 > width:  # leave room so it never collides with the music pocket
        return

    # Corner peel: anchored at the corner, a tab unfolds diagonally OUT of it — the
    # bottom border row opens first, then the text, then the roof — so it reads as
    # the bottom-left corner peeling up. Each row reveals its leftmost `rw` columns;
    # the staggered lead makes the peel front a diagonal. Retract (presence→0)
    # reverses it, folding the tab back down into the corner.
    lead = {-1: 0.0, -2: 0.22, -3: 0.44}  # bottom row peels first, roof last
    span = 1.0 - 0.44  # so the last (roof) row still completes at presence 1

    def _peel(x0: int, label: Text) -> int | None:
        """Splice one peeling alcove starting at column ``x0``; return its right
        edge once mostly peeled in (for the click rect), else None."""
        aw = label.cell_len + 4  # ╭ + space + text + space + ╮
        if x0 + aw > width - 2:
            return None
        roof = Text()
        roof.append("╭" + "─" * (aw - 2) + "╮", style=bstyle)
        mid = Text()
        mid.append("│ ", style=bstyle)
        mid.append_text(label)
        mid.append(" │", style=bstyle)
        # Open bottom (no floor) — corners rise at each wall (╯ … ╰) with an open
        # gap, like the music pocket, so the page shows through beneath the text.
        bot = Text()
        bot.append("╯" + " " * (aw - 2) + "╰", style=bstyle)
        sized = options.update_width(aw)
        right = x0 - 1
        for idx, alcove in ((-1, bot), (-2, mid), (-3, roof)):
            lp = max(0.0, min(1.0, (_back_presence - lead[idx]) / span))
            rw = int(round(aw * lp))  # revealed width of this row
            if rw <= 0:
                continue
            vr = min(width - 2, x0 + rw - 1)
            if vr < x0:
                continue
            full = console.render_lines(alcove, sized, pad=True, style=bg_style)[0]
            _al, seg, _ar = Segment.divide(full, [0, vr - x0 + 1, aw])
            lft, _mid, rgt = Segment.divide(lines[idx], [x0, vr + 1, width])
            lines[idx] = list(lft) + list(seg) + list(rgt)
            right = max(right, vr)
        return right if right >= x0 else None

    bleft = 2  # ╭ column, anchored at the bottom-left corner (music-pocket inset)
    term_h = len(lines)
    back_right = _peel(bleft, back)
    # Only clickable once it's mostly peeled in — not mid-animation.
    if _back_presence > 0.6 and back_right is not None:
        # Rows -3,-2,-1 are 1-based rows term_h-2 .. term_h; cols x0+1..right+1.
        _back_region = (bleft + 1, term_h - 2, back_right + 1, term_h)

    # Sibling tabs sit to the right of the back tab, peeling out of the same
    # corner with it — e.g. a 'c copy' tab, or a page's control hints. Each may
    # carry a key that a click on it reports (None = informational only).
    nxt = bleft + (back.cell_len + 4) + 1  # 1-col gap after the back tab
    for label, key in extra_tabs or []:
        right = _peel(nxt, label)
        if right is None:
            break  # ran out of width — drop this tab and any after it
        if key and _back_presence > 0.6:
            _tab_regions.append((nxt + 1, term_h - 2, right + 1, term_h, key))
        nxt = right + 2  # 1-col gap before the next tab


# The duck's status bubble ("Anthropic Key updated", "Copied to clipboard"):
# it dissolves in, holds, then dissolves out and clears itself — the same
# fade-in/hold/fade-out shape the rotating menu tips use (see tip_brightness),
# lerping its colours up from the page background instead of snapping on.
_SAY_FADE_IN = 0.22
_SAY_HOLD = 2.0
_SAY_FADE_OUT = 0.55
_SAY_BG = (28, 28, 34)  # matches the tips' background anchor
_SAY_TEXT = (198, 198, 208)  # soft grey-white, as the tip body
_SAY_BORDER = (110, 110, 124)
_say_text = ""  # the message currently being shown
_say_start = 0.0  # when it appeared (monotonic)


def _say_brightness(now_t: float) -> float:
    """0..1 brightness for the duck's bubble: fade in, hold, fade out, then 0
    (which stops it being drawn at all, so the message clears itself)."""
    e = now_t - _say_start
    if e < 0:
        return 0.0
    if e < _SAY_FADE_IN:
        return e / _SAY_FADE_IN
    if e < _SAY_FADE_IN + _SAY_HOLD:
        return 1.0
    out = e - _SAY_FADE_IN - _SAY_HOLD
    return max(0.0, 1.0 - out / _SAY_FADE_OUT)


# ── Controls drawer (persistent tab beside the music pocket) ───────────────
# A always-present "⌃C controls" tab sits just left of the music alcove; Ctrl+C
# expands it UPWARD into a panel listing every control available on the current
# screen (the global chords plus whatever the page handed over as _hint_tab).
_CONTROLS_LABEL = "⌃C controls"
_controls_open = False
_controls_presence = 0.0  # eased 0→1 expansion, so it grows/collapses smoothly
_controls_tab_presence = 0.0  # eased 0→1 entrance of the collapsed tab itself
_controls_region: tuple[int, int, int, int] | None = None


def toggle_controls() -> None:
    """Open/close the controls drawer (bound to Ctrl+C app-wide)."""
    global _controls_open
    _controls_open = not _controls_open


def controls_open() -> bool:
    return _controls_open


def close_controls() -> None:
    """Close the controls drawer (Esc dismisses it instead of navigating back)."""
    global _controls_open
    _controls_open = False


def controls_region() -> tuple[int, int, int, int] | None:
    """Clickable rect of the controls tab this frame (1-based inclusive)."""
    return _controls_region


def draw_controls_pocket(console, options, lines: list, page_hint: Text | None = None, target: float = 1.0) -> None:
    """Draw the persistent controls tab left of the music pocket, expanding upward
    when open. In place, like the other chrome routines; a no-op when too narrow.

    ``target`` is 1.0 on pages that qualify for the tab (it eases in) and 0.0 on
    those that don't (it eases back out), so the tab arrives and leaves rather than
    popping. Its own entrance is separate from the drawer's expansion.
    """
    from rich.segment import Segment

    global _controls_presence, _controls_tab_presence, _controls_region
    _controls_region = None
    if not lines or len(lines) < 4 or not lines[-1]:
        return
    # Ease the tab's own arrival/departure.
    _controls_tab_presence += (target - _controls_tab_presence) * 0.25
    if target <= 0.0 and _controls_tab_presence < 0.02:
        _controls_tab_presence = 0.0
        return  # fully gone — nothing to draw
    if _controls_tab_presence < 0.02:
        return
    width = sum(seg.cell_length for seg in lines[-1]) or options.max_width
    bstyle = lines[-1][0].style
    bg_style = Style(bgcolor=bstyle.bgcolor) if bstyle and bstyle.bgcolor else None
    theme = PLANNING_THEME

    # Sit immediately left of the music alcove (same geometry it uses).
    mw = build_music_subtitle().cell_len + 4
    music_left = (width - 3) - mw + 1
    label = Text()
    label.append("⌃C", style=theme.accent)
    label.append(" controls", style=theme.muted)
    aw = label.cell_len + 4
    t_right = music_left - 2
    t_left = t_right - aw + 1
    if t_left < 4:
        return  # not enough room between the back tab and the music bar

    term_h = len(lines)
    _controls_presence += ((1.0 if _controls_open else 0.0) - _controls_presence) * 0.3
    if _controls_presence < 0.02:
        _controls_presence = 0.0

    rows: list[Text] = []

    def _ctl(keys: str, what: str) -> None:
        # Left-aligned: the key starts flush at the left edge, descriptions share a
        # column so they still line up.
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(keys.ljust(8), style=theme.accent)
        t.append("  " + what, style=theme.muted)
        rows.append(t)

    # Page-specific controls first (what's actionable here), then the globals.
    # A page hint reads like "←/→  switch tab  ·  click a row  edit"; only split a
    # part into key/description when it actually starts with a key token, so
    # phrases like "click a row edit" aren't mangled into the key column.
    _keyish = {"←/→", "↑/↓", "Enter", "enter", "esc", "Esc", "Tab", "Space", "[", "]", "q", "c", "g", "t", "a", "f"}
    if page_hint is not None and page_hint.plain.strip():
        for part in [p.strip() for p in page_hint.plain.split("·") if p.strip()]:
            bits = part.split(None, 1)
            if len(bits) > 1 and (bits[0] in _keyish or bits[0].lower().startswith("ctrl+")):
                _ctl(bits[0], bits[1])
            else:
                _ctl("", part)  # a phrase, not a keypress — leave the key column empty
    # The music chords are already spelled out in the music pocket next door, so
    # they'd only be duplicated here.
    _ctl("esc", "go back")
    _ctl("ctrl+C", "close this  ·  twice to quit")

    # ── The tab IS the panel: it grows upward out of the bottom border ────────
    # Anchored bottom-right at the tab's corner, the box's roof rises as it opens
    # and the control rows fill in beneath it, revealed bottom-up. Width eases at
    # the same time so it unfolds rather than snapping to its open size.
    open_w = min(max((r.cell_len for r in rows), default=10) + 4, width - 8)
    cur_w = max(aw, int(round(aw + (open_w - aw) * _controls_presence)))
    c_right = t_right
    c_left = max(2, c_right - cur_w + 1)
    cur_w = c_right - c_left + 1
    inner = cur_w - 4  # text width between "│ " and " │"
    extra = int(round(len(rows) * _controls_presence))  # content rows revealed
    extra = max(0, min(extra, term_h - 5))  # never push the roof off the top

    def _bordered(body: Text | None) -> Text:
        t = Text()
        t.append("│ ", style=bstyle)
        if body is None:
            t.append(" " * inner)
        else:
            b = body.copy()
            b.truncate(inner, overflow="ellipsis", pad=True)
            t.append_text(b)
        t.append(" │", style=bstyle)
        return t

    roof = Text()
    roof.append("╭" + "─" * (cur_w - 2) + "╮", style=bstyle)
    bot = Text()
    bot.append("╯" + " " * (cur_w - 2) + "╰", style=bstyle)  # open bottom, like the pocket
    label_row = _bordered(label)

    # Row plan, bottom-up: open border, label, revealed content (last `extra`), roof.
    plan: list[tuple[int, Text]] = [(term_h - 1, bot), (term_h - 2, label_row)]
    shown_rows = rows[len(rows) - extra :] if extra else []
    for i, body in enumerate(reversed(shown_rows)):
        plan.append((term_h - 3 - i, _bordered(body)))
    plan.append((term_h - 3 - extra, roof))

    # Entrance: the tab unrolls leftward out of its right edge (mirroring the back
    # tab, which unrolls rightward out of the bottom-left corner). Only the
    # rightmost `vis` columns are spliced while it eases in or out.
    vis = max(1, int(round(cur_w * _controls_tab_presence)))
    v_left = c_right - vis + 1
    sized = options.update_width(cur_w)
    for r, piece in plan:
        if r < 0 or r >= term_h:
            continue
        full = console.render_lines(piece, sized, pad=True, style=bg_style)[0]
        _al, seg, _ar = Segment.divide(full, [cur_w - vis, cur_w, cur_w])
        lft, _m, rgt = Segment.divide(lines[r], [v_left, v_left + vis, width])
        lines[r] = list(lft) + list(seg) + list(rgt)
    if _controls_tab_presence > 0.6:  # only clickable once it has settled in
        _controls_region = (v_left + 1, term_h - 2, c_right + 1, term_h)


_DUCK_W = 13  # tight render width of the companion head (7 rows at this width)
_DUCK_RIGHT_MARGIN = 3  # cols kept clear on the right (border + breathing room)
_DUCK_SLIDE_SECONDS = 0.45  # duration of the slide-into-corner on screen entry
_DUCK_SLIDE_GAP = 0.25  # a render gap longer than this counts as a fresh screen entry
_DUCK_SLIDE_DISTANCE = 16  # how far he nudges right into the corner (a little bit)

# Slide-in animation clock (module-level; there is only ever one live screen).
_duck_slide_start = 0.0
_duck_last_draw = 0.0


def draw_companion_duck(console, options, lines: list, say: str = "") -> None:
    """Overlay the mascot duck in the bottom-right corner of ``lines``, in place.

    The duck sits just above the music pocket (which owns the bottom three rows),
    right-aligned inside the border. Composited over whatever is there — on the
    sub-pages that region is empty — so the mascot rides along on every screen the
    way the music bar does. A no-op when the panel is too short or too narrow.

    ``say`` gives him a speech bubble to the LEFT of his head (he's at the right
    edge, so the tail points right, back at him) — used for transient page status
    like "Anthropic Key updated" instead of spending a body row on it.
    """
    from rich.segment import Segment

    from yeaboi.ui.shared._animations import ease_out_cubic
    from yeaboi.ui.shared._mascot import render_head

    if not lines or not lines[-1]:
        return
    width = sum(seg.cell_length for seg in lines[-1]) or options.max_width
    # Render the sprite over the page's own background tint (main #104): the head's
    # empty cells carry no colour, so without a base bg they'd punch a black box
    # through the tinted page around the duck.
    bstyle = lines[-1][0].style
    bg_style = Style(bgcolor=bstyle.bgcolor) if bstyle and bstyle.bgcolor else None
    duck_rows = console.render_lines(render_head(0, flip=True), options.update_width(_DUCK_W), pad=True, style=bg_style)
    dh = len(duck_rows)
    # Need room for the duck + a gap + the 3-row pocket; skip on tiny panels.
    if len(lines) < dh + 5 or width < _DUCK_W + _DUCK_RIGHT_MARGIN + 4:
        return

    # Slide-in: a gap since the last draw means we just entered a screen that shows
    # the duck (the welcome draws its own, so it never calls this) — restart the
    # slide so the mascot glides in from the right edge into its corner. Continuous
    # sub-page re-renders keep the gap tiny, so it settles and stays put.
    global _duck_slide_start, _duck_last_draw
    now = time.monotonic()
    if now - _duck_last_draw > _DUCK_SLIDE_GAP:
        _duck_slide_start = now
    _duck_last_draw = now
    progress = min(1.0, (now - _duck_slide_start) / _DUCK_SLIDE_SECONDS)

    # He starts at roughly his menu-lane column and glides the little bit RIGHT into
    # his in-page corner, mirroring the menu->page transition (and the reverse when
    # going back). Only a small nudge, not a swing in from screen-centre.
    rest_dl = width - _DUCK_W - _DUCK_RIGHT_MARGIN  # resting left column (the corner)
    start_dl = max(1, rest_dl - _DUCK_SLIDE_DISTANCE)  # a little left, ~the menu position
    dl = int(start_dl + (rest_dl - start_dl) * ease_out_cubic(progress))
    right_edge = width - 1  # never overwrite the panel's right border
    if dl >= right_edge:
        return
    dr = min(dl + _DUCK_W, right_edge)
    bottom = len(lines) - 4  # one blank row above the pocket roof (rows -3,-2,-1)
    top = bottom - dh + 1
    for i, drow in enumerate(duck_rows):
        r = top + i
        if r < 0:
            continue
        visible = drow if (dr - dl) >= _DUCK_W else list(Segment.divide(drow, [dr - dl]))[0]
        left, _mid, right = Segment.divide(lines[r], [dl, dr, width])
        lines[r] = list(left) + list(visible) + list(right)

    # ── Speech bubble, to the left of his head ────────────────────────────────
    # A fresh message restarts the fade; once it has faded back out the bubble
    # stops drawing, so the status clears itself after a couple of seconds.
    global _say_text, _say_start
    if say and say != _say_text:
        _say_text, _say_start = say, time.monotonic()
    if not say or say != _say_text:
        return
    bright = _say_brightness(time.monotonic())
    if bright <= 0.0:
        return
    from yeaboi.ui.shared._animations import lerp_color

    text_style = lerp_color(bright, _SAY_BG, _SAY_TEXT)
    border_style = lerp_color(bright, _SAY_BG, _SAY_BORDER)
    label = Text(say, style=text_style, no_wrap=True, overflow="ellipsis")
    bw = label.cell_len + 4  # │ + space + text + space + │
    bx1 = dl - 2  # a 1-col gap before his head (the tail column sits at dl-1)
    bx0 = bx1 - bw + 1
    if bx0 < 2 or dh < 3:
        return  # not enough room to the left — skip rather than clip the page
    mid_r = top + dh // 2  # level with the middle of his head
    if mid_r - 1 < 0 or mid_r + 1 >= len(lines):
        return
    bstyle = border_style
    roof = Text()
    roof.append("╭" + "─" * (bw - 2) + "╮", style=bstyle)
    body = Text()
    body.append("│ ", style=bstyle)
    body.append_text(label)
    body.append(" │", style=bstyle)
    floor = Text()
    floor.append("╰" + "─" * (bw - 2) + "╯", style=bstyle)
    sized = options.update_width(bw)
    for r, piece in ((mid_r - 1, roof), (mid_r, body), (mid_r + 1, floor)):
        seg = console.render_lines(piece, sized, pad=True, style=bg_style)[0]
        lft, _m, rgt = Segment.divide(lines[r], [bx0, bx1 + 1, width])
        lines[r] = list(lft) + list(seg) + list(rgt)
    # Tail: a small pointer on the bubble's right edge, aimed at his head.
    tail = Text("›", style=bstyle)
    tseg = console.render_lines(tail, options.update_width(1), pad=True, style=bg_style)[0]
    lft, _m, rgt = Segment.divide(lines[mid_r], [bx1 + 1, bx1 + 2, width])
    lines[mid_r] = list(lft) + list(tseg) + list(rgt)


class _MusicPocketFrame:
    """Wraps a bare screen Panel and draws the app-wide chrome over its bottom rows:
    the music pocket (see :func:`draw_music_pocket`) plus the mascot duck (see
    :func:`draw_companion_duck`). Applied app-wide by :meth:`MusicLive.get_renderable`;
    the welcome screen draws its own richer companion + shares the pocket routine via
    ``_WelcomeFrame``. Falls back to a flat subtitle when too narrow.
    """

    def __init__(
        self,
        panel: Panel,
        *,
        with_duck: bool = True,
        preserve_content: bool = False,
        with_back: bool = False,
        with_copy: bool = False,
        hint_tab: Text | None = None,
        duck_say: str = "",
    ) -> None:
        self.panel = panel
        self.with_duck = with_duck  # screensaver already has the big duck → pocket only
        self.preserve_content = preserve_content  # keep row content behind the pocket band
        self.with_back = with_back  # draw the bottom-left "go back" tab (back-capable screens)
        self.with_copy = with_copy  # also draw a 'c copy' tab beside it
        self.hint_tab = hint_tab  # a page's control hints, as one more tab
        self.duck_say = duck_say  # transient status the companion speaks

    def __rich_console__(self, console, options):
        from rich.segment import Segment

        lines = console.render_lines(self.panel, options, pad=False)
        draw_music_pocket(console, options, lines, preserve_content=self.preserve_content)
        # Always drive the back tab so it can glide OUT (target 0) when leaving a
        # back-capable screen, not only glide in (target 1) when arriving.
        _extra: list = []
        if self.with_back and self.with_copy:
            _extra.append((build_copy_text(), "c"))
        # The page's hints are NOT drawn as their own tab any more — they'd be
        # redundant with (and cut off behind) the controls drawer, which lists them.
        draw_back_pocket(console, options, lines, target=1.0 if self.with_back else 0.0, extra_tabs=_extra)
        # The controls tab is persistent on every screen, and its drawer expands
        # upward over the page when opened with Ctrl+C.
        draw_controls_pocket(console, options, lines, page_hint=self.hint_tab)
        if self.with_duck:
            draw_companion_duck(console, options, lines, say=self.duck_say)
        # Newlines go BETWEEN rows, never after the last one. A trailing
        # Segment.line() on a full-height frame pushes the cursor past the final
        # row and scrolls the whole frame up by one — the "bottom border moves up
        # when entering a screen" glitch. Emit line separators only.
        for i, line in enumerate(lines):
            if i:
                yield Segment.line()
            yield from line


class MusicLive(Live):
    """App-wide ``Live`` with music chrome and idle-screensaver rendering."""

    def __init__(self, *args, **kwargs) -> None:
        # Default vertical_overflow to "crop". The TUI is a hand-rolled scroller:
        # every screen builds a Panel sized to the terminal height and does its
        # own line-slicing. If a Panel ever renders even one row too tall (small
        # terminal, an ASCII title whose height differs from the assumed
        # constant, or line-wrapping), Rich's default "ellipsis" overflow pushes
        # the real terminal into scrolling and corrupts the frame — the "breaks
        # the view" symptom. "crop" silently trims the overflow instead of
        # scrolling; unlike "ellipsis" it also avoids a stray "…" on the last row.
        kwargs.setdefault("vertical_overflow", "crop")
        super().__init__(*args, **kwargs)
        self._last_renderable = None

    def start(self, refresh: bool = False) -> None:
        """Enter the alt-screen only if we're not already in it.

        Rich's ``Live.start`` unconditionally re-emits the enter-alt escape, which
        clears the alternate buffer — a blank flash. When a screen takes over from
        one already in the alt-screen (e.g. the splash handing off to the menu, or
        one full-screen page to the next), that re-clear is a visible flicker. Skip
        the enter here when already in alt, but still mark ``_alt_screen`` so the
        final ``stop()`` restores the normal screen on app exit.
        """
        if self._screen and self.console.is_terminal and self.console.is_alt_screen:
            self._screen = False  # don't let Live re-enter (and clear) the alt-screen
            try:
                super().start(refresh)
            finally:
                self._screen = True
            self._alt_screen = True  # keep the exit-alt on the eventual stop()
        else:
            super().start(refresh)

    def update(self, renderable, *, refresh: bool = False) -> None:
        global _active
        _active = self
        self._last_renderable = renderable
        super().update(renderable, refresh=refresh)

    def get_renderable(self):
        """Return the saver while idle; otherwise the app screen with the music bar.

        Bare screen Panels are wrapped in :class:`_MusicPocketFrame` so the bottom
        border curves up over the music pocket app-wide. When the terminal is too
        narrow to box the bar it falls back to the flat right-aligned subtitle.
        Popups (a Panel that set its own subtitle) and non-Panel frames (e.g. the
        welcome screen, which draws its own richer pocket) are left untouched.
        """
        from yeaboi.ui.shared._screensaver import build_screensaver, idle_controller

        # Reset the back-tab rect every frame; only a frame that actually draws the
        # tab republishes it, so a click maps to Esc solely where the tab is shown.
        _clear_back_region()

        if idle_controller.should_show():
            width, height = self.console.size
            saver = build_screensaver(width=width, height=height)
            # Keep the music tab on the saver too, but pocket-only (it already has
            # the big chilling duck) and preserving the row content so the walking
            # duck can go right down to the border behind the pocket band.
            if isinstance(saver, Panel) and build_music_subtitle().cell_len + 10 <= width:
                return _MusicPocketFrame(saver, with_duck=False, preserve_content=True)
            return saver

        # App-wide minimum-size guard: below the welcome screen's floor, EVERY
        # screen (settings, hubs, wizards…) shows the "resize me" duck instead of a
        # cramped/clipped layout — not just the main menu. Lazy import: these live
        # in mode_select, which imports this module (avoid an import-time cycle).
        width, height = self.console.size
        from yeaboi.ui.mode_select.screens._screens import _MIN_HEIGHT, _MIN_WIDTH, _build_too_small_screen

        if width < _MIN_WIDTH or height < _MIN_HEIGHT:
            return _build_too_small_screen(width, height)

        renderable = super().get_renderable()
        if not isinstance(renderable, Panel):
            return renderable
        # Safety net (main #104): no screen may ever show the terminal's own
        # background. A Panel whose builder didn't set a style (i.e. bypassed
        # build_page_panel) gets the neutral dark base; styled Panels are left
        # untouched. Rich defaults Panel ``style`` to the string "none".
        if not renderable.style or str(renderable.style) == "none":
            renderable.style = f"on {NEUTRAL_BG}"
        # A subtitle we didn't set (a popup's own status) is meaningful — don't
        # clobber it. Our own previous stamp is tagged so we can refresh it.
        if getattr(renderable, "subtitle", None) and not getattr(renderable, "_music_stamped", False):
            return renderable  # a popup's own subtitle — leave it be

        width, _ = self.console.size
        if build_music_subtitle().cell_len + 10 <= width:
            # A screen that already features the mascot (e.g. the too-small guard
            # with its own centred duck) marks itself so we don't stamp a second,
            # redundant duck in the corner — it still gets the music pocket.
            with_duck = not getattr(renderable, "_no_companion_duck", False)
            # The "go back" tab shows on every screen except those that opt out
            # (the main menu marks itself with _no_back_hint — its Esc isn't "back").
            with_back = not getattr(renderable, "_no_back_hint", False)
            # Pages that support copy-to-clipboard flag themselves so a 'c copy'
            # tab appears beside the back tab (Usage, Changelog, …).
            with_copy = bool(getattr(renderable, "_copy_tab", False))
            # A page can hand its control hints to the chrome (_hint_tab) so they
            # ride in the bottom pocket instead of taking a body row.
            hint_tab = getattr(renderable, "_hint_tab", None)
            # A page can also hand the duck a line to speak (transient status).
            duck_say = str(getattr(renderable, "_duck_say", "") or "")
            return _MusicPocketFrame(
                renderable,
                with_duck=with_duck,
                with_back=with_back,
                with_copy=with_copy,
                hint_tab=hint_tab,
                duck_say=duck_say,
            )
        # Too narrow to box → keep the flat status line on the border.
        renderable.subtitle = build_music_subtitle()
        renderable.subtitle_align = "right"
        renderable._music_stamped = True
        return renderable

    def restamp(self) -> None:
        """Force an immediate redraw after a music state change (get_renderable
        rebuilds the bar each render, so a plain refresh is enough)."""
        try:
            self.refresh()
        except Exception:  # noqa: BLE001 - refreshing a stopped Live is harmless to skip
            logger.debug("Music bar refresh skipped", exc_info=True)


def make_live(*args, **kwargs) -> MusicLive:
    """Construct the app's Live so every screen gets the persistent music bar."""
    return MusicLive(*args, **kwargs)


def nudge_music_bar() -> None:
    """Redraw the status bar immediately after a music state change."""
    if _active is not None and getattr(_active, "is_started", False):
        _active.restamp()
