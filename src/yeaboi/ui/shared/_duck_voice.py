"""One shared voice for the corner duck — every page speaks through it.

The chrome duck (ui/shared/_music_bar.py) can show one speech-bubble line at a
time via ``panel._duck_say``. Any page that wants the bubble goes through a
single :class:`DuckVoice` arbiter, whose priority ladder decides what he says
each frame — direct ``_duck_say`` writes from page code fight each other and
are being retired. Durable information always belongs on the page itself
(status rows, transcript notes); the bubble is additive and ephemeral only.

Two ways to reach the duck:

- The planning chat owns a private :class:`DuckVoice` instance and stamps the
  panel attrs itself (it has its own reading-column fence).
- Every other page uses the module singleton :func:`duck_voice`; the chrome
  ticks it once per frame in ``MusicLive.get_renderable`` and stamps the line
  for them, fenced by :func:`default_bubble_room` (or the page's own
  ``panel._bubble_room``).

Priority ladder: LOWER numbers win. A sticky line (a confirmation waiting for
an answer) beats events; events (an ack, a completion quip) beat coaching.
There is deliberately NO ambient tier below coaching: rotating feature-tips
were tried in the bubble and read as noise (user feedback) — the duck only
speaks when something actually happened.

Pure and clock-parameterised so it unit-tests without Rich.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from yeaboi.ui.shared._music_bar import _SAY_FADE_IN, _SAY_FADE_OUT, _SAY_HOLD

PRIORITY_STICKY = 0  # a confirmation that must wait for an answer — never fades
PRIORITY_EVENT = 1
PRIORITY_COACH = 2

COACH_HOLD = 4.0  # coaching lines dwell a little longer than the default 2s

# The reaction vocabulary — one short line per completion event, used by the
# mode pages so the tone stays consistent app-wide. Dynamic lines (counts,
# filenames) go through say() directly. Kept ≤ 40 chars by a unit test so a
# quip always fits the bubble on a normal terminal.
DUCK_QUIPS: dict[str, str] = {
    "standup_done": "Standup's up!",
    "report_done": "Report's ready!",
    "roadmap_done": "Plan's plotted!",
    "export_done": "Saved it!",
    "link_ready": "Link's live!",
    "sync_done": "Synced!",
    "actions_done": "Actions drafted!",
    "analysis_done": "Team mapped!",
    "poker_done": "Points dealt!",
    "artifact_done": "Done and dusted!",
    "anonymize_done": "Scrubbed clean!",
}

# ─── Bubble fence ────────────────────────────────────────────────────────────
# The bubble is drawn leftward from the duck's corner and the chrome will skip
# (never clip) one that doesn't fit — but "fit" only accounts for the terminal
# edge, not page content. These mirror the chat's fence (chat/_screen.py):
# a line is truncated to the free columns right of the content and dropped
# entirely below _BUBBLE_MIN_COLS, so a bubble can never overlap page content.
_BUBBLE_MIN_COLS = 12
_DUCK_LANE = 16  # the duck sprite's columns at the right edge
_BUBBLE_CHROME = 7  # bubble borders + tail + breathing gap
# Conservative default content edge for pages that don't declare their own:
# most mode pages are left-gutter line lists well inside 64 columns, and on a
# narrow terminal the bubble silently skips — the quack still lands.
_DEFAULT_CONTENT_EDGE = 64


def default_bubble_room(width: int, content_edge: int = _DEFAULT_CONTENT_EDGE) -> int:
    """Columns a bubble may use between ``content_edge`` and the duck's lane."""
    return (width - _DUCK_LANE) - content_edge - _BUBBLE_CHROME


@dataclass
class _Line:
    text: str
    priority: int
    hold: float
    seq: int
    at: float  # when it was said (monotonic)


class DuckVoice:
    """Decides what the corner duck says, one line at a time."""

    def __init__(self) -> None:
        self._seq = 0
        self._line: _Line | None = None
        self.muted = False  # the user asked for quiet

    def mute(self, muted: bool) -> None:
        """Silence (or restore) the bubble; muting drops the current line too."""
        self.muted = muted
        if muted:
            self._line = None

    def _expired(self, line: _Line, now: float) -> bool:
        if line.priority == PRIORITY_STICKY:
            return False  # sticky waits for clear_sticky(), never for the clock
        return now - line.at > _SAY_FADE_IN + line.hold + _SAY_FADE_OUT

    def say(
        self, text: str, priority: int = PRIORITY_EVENT, hold: float | None = None, now: float | None = None
    ) -> bool:
        """Offer the duck a line. Returns True when it took the bubble.

        A line still showing at a HIGHER priority keeps the bubble (a coaching
        nudge never interrupts a quip); an equal-or-lower one is replaced.
        Offering the text already showing at the same priority is a no-op —
        repeats don't restart the fade unless the caller re-offers after it
        expired.
        """
        if self.muted or not text:
            return False  # nothing to say (an empty status must not hold the bubble)
        now = time.monotonic() if now is None else now
        hold = _SAY_HOLD if hold is None else hold
        live = self._line is not None and not self._expired(self._line, now)
        if live and priority > self._line.priority:
            return False  # something more important is still on screen
        if live and self._line.text == text and self._line.priority == priority:
            return True  # already saying exactly this — let it play out
        self._seq += 1
        self._line = _Line(text, priority, hold, self._seq, now)
        return True

    def say_sticky(self, text: str, now: float | None = None) -> bool:
        """A line that waits for an answer — full brightness until cleared."""
        return self.say(text, priority=PRIORITY_STICKY, hold=float("inf"), now=now)

    def clear_sticky(self) -> None:
        """Release a sticky line (a no-op when the live line isn't sticky)."""
        if self._line is not None and self._line.priority == PRIORITY_STICKY:
            self._line = None

    @property
    def sticky(self) -> bool:
        """True while the live line is a sticky one (chrome skips the fade)."""
        return self._line is not None and self._line.priority == PRIORITY_STICKY

    def tick(self, now: float | None = None) -> tuple[str, float, int] | None:
        """(text, hold, seq) to stamp on the panel this frame, or None."""
        now = time.monotonic() if now is None else now
        if self._line is None or self._expired(self._line, now):
            return None
        return (self._line.text, self._line.hold, self._line.seq)


# ─── Module singleton + global mute ──────────────────────────────────────────
# One page renders at a time, so one shared voice serves every non-chat page.
# The mute flag is module state (checked by the chrome's stamping site, not
# inside DuckVoice — the class stays pure/clock-testable) and lazily seeded
# from the persisted DUCK_ENABLED preference.

_voice: DuckVoice | None = None
_muted: bool | None = None  # None = not yet read from config


def duck_voice() -> DuckVoice:
    """The app-wide duck voice every non-chat page speaks through."""
    global _voice
    if _voice is None:
        _voice = DuckVoice()
    return _voice


def duck_muted() -> bool:
    """Whether the duck's bubble is muted app-wide (lazy-read from config)."""
    global _muted
    if _muted is None:
        from yeaboi.config import is_duck_enabled

        _muted = not is_duck_enabled()
    return _muted


def set_duck_muted(muted: bool) -> None:
    """Flip the app-wide mute for this session (persistence is the caller's
    job via config.set_duck_enabled — Settings and /duck both do)."""
    global _muted
    _muted = muted
    if muted and _voice is not None:
        _voice._line = None  # drop any live line immediately; the voice stays usable


def _reset() -> None:
    """Test helper: fresh voice, mute back to unread."""
    global _voice, _muted
    _voice = None
    _muted = None
