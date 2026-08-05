"""The planning chat's duck voice — one arbiter for the corner duck's bubble.

The chrome duck (ui/shared/_music_bar.py) can speak one line at a time via
``panel._duck_say``. Several chat features want that bubble — stage-done quips,
intake coaching, rotating tips — so nothing in the chat writes ``_duck_say``
directly: everything goes through a single :class:`ChatDuck`, whose priority
ladder decides what he says each frame. Durable information always goes to the
transcript (`_note`/`_say`); the bubble is additive and ephemeral only.

Pure and clock-parameterised so it unit-tests without Rich: the driver calls
``tick()`` once per frame and stamps the result onto the screen panel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from yeaboi.ui.shared._music_bar import _SAY_FADE_IN, _SAY_FADE_OUT, _SAY_HOLD

# Priority ladder: LOWER numbers win. Events (an ack, a stage quip, the
# celebration) beat coaching, which beats the idle tip rotation — so a tip can
# never talk over "Stories done!", and coaching owns the bubble during intake.
PRIORITY_EVENT = 1
PRIORITY_COACH = 2
PRIORITY_TIP = 3

COACH_HOLD = 4.0  # coaching lines dwell a little longer than the default 2s


@dataclass
class _Line:
    text: str
    priority: int
    hold: float
    seq: int
    at: float  # when it was said (monotonic)


class ChatDuck:
    """Decides what the corner duck says, one line at a time."""

    def __init__(self) -> None:
        self._seq = 0
        self._line: _Line | None = None

    def _expired(self, line: _Line, now: float) -> bool:
        return now - line.at > _SAY_FADE_IN + line.hold + _SAY_FADE_OUT

    def say(
        self, text: str, priority: int = PRIORITY_EVENT, hold: float | None = None, now: float | None = None
    ) -> bool:
        """Offer the duck a line. Returns True when it took the bubble.

        A line still showing at a HIGHER priority keeps the bubble (a tip never
        interrupts a quip); an equal-or-lower one is replaced. Offering the text
        already showing at the same priority is a no-op — repeats don't restart
        the fade unless the caller re-offers after it expired.
        """
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

    def tick(self, now: float | None = None) -> tuple[str, float, int] | None:
        """(text, hold, seq) to stamp on the panel this frame, or None."""
        now = time.monotonic() if now is None else now
        if self._line is None or self._expired(self._line, now):
            return None
        return (self._line.text, self._line.hold, self._line.seq)
