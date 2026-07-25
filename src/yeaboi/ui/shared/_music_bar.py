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
from rich.text import Text

from yeaboi import music
from yeaboi.ui.shared._components import PLANNING_THEME, Theme

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
        blank_line = console.render_lines(blank, options.update_width(width), pad=True)[0]
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
    lines[-1] = console.render_lines(border, sized, pad=True)[0]
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
            seg = console.render_lines(alcove, asized, pad=True)[0]
            lft, _mid, rgt = Segment.divide(lines[idx], [left, right + 1, width])
            lines[idx] = list(lft) + list(seg) + list(rgt)
    else:
        lines[-3] = console.render_lines(roof, sized, pad=True)[0]
        lines[-2] = console.render_lines(textrow, sized, pad=True)[0]


_DUCK_W = 13  # tight render width of the companion head (7 rows at this width)
_DUCK_RIGHT_MARGIN = 3  # cols kept clear on the right (border + breathing room)
_DUCK_SLIDE_SECONDS = 0.45  # duration of the slide-into-corner on screen entry
_DUCK_SLIDE_GAP = 0.25  # a render gap longer than this counts as a fresh screen entry
_DUCK_SLIDE_DISTANCE = 16  # how far he nudges right into the corner (a little bit)

# Slide-in animation clock (module-level; there is only ever one live screen).
_duck_slide_start = 0.0
_duck_last_draw = 0.0


def draw_companion_duck(console, options, lines: list) -> None:
    """Overlay the mascot duck in the bottom-right corner of ``lines``, in place.

    The duck sits just above the music pocket (which owns the bottom three rows),
    right-aligned inside the border. Composited over whatever is there — on the
    sub-pages that region is empty — so the mascot rides along on every screen the
    way the music bar does. A no-op when the panel is too short or too narrow.
    """
    from rich.segment import Segment

    from yeaboi.ui.shared._animations import ease_out_cubic
    from yeaboi.ui.shared._mascot import render_head

    if not lines or not lines[-1]:
        return
    width = sum(seg.cell_length for seg in lines[-1]) or options.max_width
    duck_rows = console.render_lines(render_head(0, flip=True), options.update_width(_DUCK_W), pad=True)
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


class _MusicPocketFrame:
    """Wraps a bare screen Panel and draws the app-wide chrome over its bottom rows:
    the music pocket (see :func:`draw_music_pocket`) plus the mascot duck (see
    :func:`draw_companion_duck`). Applied app-wide by :meth:`MusicLive.get_renderable`;
    the welcome screen draws its own richer companion + shares the pocket routine via
    ``_WelcomeFrame``. Falls back to a flat subtitle when too narrow.
    """

    def __init__(self, panel: Panel, *, with_duck: bool = True, preserve_content: bool = False) -> None:
        self.panel = panel
        self.with_duck = with_duck  # screensaver already has the big duck → pocket only
        self.preserve_content = preserve_content  # keep row content behind the pocket band

    def __rich_console__(self, console, options):
        from rich.segment import Segment

        lines = console.render_lines(self.panel, options, pad=False)
        draw_music_pocket(console, options, lines, preserve_content=self.preserve_content)
        if self.with_duck:
            draw_companion_duck(console, options, lines)
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

        if idle_controller.should_show():
            width, height = self.console.size
            saver = build_screensaver(width=width, height=height)
            # Keep the music tab on the saver too, but pocket-only (it already has
            # the big chilling duck) and preserving the row content so the walking
            # duck can go right down to the border behind the pocket band.
            if isinstance(saver, Panel) and build_music_subtitle().cell_len + 10 <= width:
                return _MusicPocketFrame(saver, with_duck=False, preserve_content=True)
            return saver

        renderable = super().get_renderable()
        if not isinstance(renderable, Panel):
            return renderable
        if getattr(renderable, "subtitle", None) and not getattr(renderable, "_music_stamped", False):
            return renderable  # a popup's own subtitle — leave it be

        width, _ = self.console.size
        if build_music_subtitle().cell_len + 10 <= width:
            return _MusicPocketFrame(renderable)
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
