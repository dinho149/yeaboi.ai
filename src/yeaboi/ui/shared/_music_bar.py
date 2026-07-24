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
    ``♪ Lofi · playing   ^P pause · ^O channel``. When ffplay isn't installed it
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
        toggle_hint = "^P play"
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
            toggle_hint = "^P pause"
        else:
            line.append("paused", style=theme.value)
            toggle_hint = "^P play"
        line.append("  ", style=theme.muted)
    line.append(f"  {toggle_hint} · ^O channel ", style=theme.dim)
    return line


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
        self._stamped = False

    def update(self, renderable, *, refresh: bool = False) -> None:
        global _active
        _active = self
        self._last_renderable = renderable
        self._stamp(renderable)
        super().update(renderable, refresh=refresh)

    def get_renderable(self):
        """Return the saver while idle, otherwise the preserved app screen."""
        from yeaboi.ui.shared._screensaver import build_screensaver, idle_controller

        if idle_controller.should_show():
            width, height = self.console.size
            return build_screensaver(width=width, height=height)
        return super().get_renderable()

    def refresh(self) -> None:
        # Re-stamp before every auto-refresh tick so the equalizer keeps animating
        # even on screens whose input loop only redraws on a keypress. Live already
        # runs a background refresh thread at refresh_per_second, so this is what
        # drives the animation app-wide with no timer of our own.
        if self._last_renderable is not None:
            self._stamp(self._last_renderable)
        super().refresh()

    def _stamp(self, renderable) -> None:
        """Set the music subtitle on a bare Panel; leave popups and non-Panels alone."""
        if not isinstance(renderable, Panel):
            self._stamped = False
            return
        # A subtitle we didn't set (a popup's own status) is meaningful — don't
        # clobber it. Our own previous stamp is tagged so we can refresh it.
        if getattr(renderable, "subtitle", None) and not getattr(renderable, "_music_stamped", False):
            self._stamped = False
            return
        # Always stamp — build_music_subtitle() renders a dim install hint when
        # ffplay is unavailable, so the bar stays present (and discoverable).
        renderable.subtitle = build_music_subtitle()
        renderable.subtitle_align = "right"
        renderable._music_stamped = True
        self._stamped = True

    def restamp(self) -> None:
        """Recompute the subtitle for the current screen and push a refresh."""
        if self._last_renderable is None:
            return
        self._stamp(self._last_renderable)
        if not self._stamped:
            return
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
