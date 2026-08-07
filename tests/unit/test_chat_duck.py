"""Tests for the chat duck's bubble arbiter (ui/session/chat/_duck.py)."""

from yeaboi.ui.session.chat._duck import (
    COACH_HOLD,
    PRIORITY_COACH,
    PRIORITY_EVENT,
    ChatDuck,
)
from yeaboi.ui.shared._music_bar import _SAY_FADE_IN, _SAY_FADE_OUT, _SAY_HOLD


class TestPriorityLadder:
    def test_event_beats_a_live_coaching_line(self):
        duck = ChatDuck()
        duck.say("Let's talk timing.", priority=PRIORITY_COACH, now=0.0)
        assert duck.say("Stories done!", priority=PRIORITY_EVENT, now=0.1)
        assert duck.tick(now=0.2)[0] == "Stories done!"

    def test_coach_never_interrupts_a_live_event(self):
        duck = ChatDuck()
        duck.say("Stories done!", priority=PRIORITY_EVENT, now=0.0)
        assert not duck.say("Let's talk timing.", priority=PRIORITY_COACH, now=0.1)
        assert duck.tick(now=0.2)[0] == "Stories done!"

    def test_coach_takes_over_after_the_event_expires(self):
        duck = ChatDuck()
        duck.say("Stories done!", priority=PRIORITY_EVENT, now=0.0)
        after = _SAY_FADE_IN + _SAY_HOLD + _SAY_FADE_OUT + 0.1
        assert duck.say("Let's talk timing.", priority=PRIORITY_COACH, now=after)
        assert duck.tick(now=after + 0.1)[0] == "Let's talk timing."


class TestMute:
    def test_muted_duck_refuses_lines_and_drops_the_current_one(self):
        duck = ChatDuck()
        duck.say("Stories done!", now=0.0)
        duck.mute(True)
        assert duck.tick(now=0.1) is None  # live line dropped immediately
        assert not duck.say("Tasks sliced!", now=0.2)

    def test_unmuting_restores_the_bubble(self):
        duck = ChatDuck()
        duck.mute(True)
        duck.mute(False)
        assert duck.say("Quack!", now=0.0)
        assert duck.tick(now=0.1)[0] == "Quack!"


class TestLifecycle:
    def test_tick_returns_none_when_silent(self):
        assert ChatDuck().tick(now=0.0) is None

    def test_line_expires_after_fade_plus_hold(self):
        duck = ChatDuck()
        duck.say("hi", now=0.0)
        assert duck.tick(now=_SAY_FADE_IN + _SAY_HOLD) is not None
        assert duck.tick(now=_SAY_FADE_IN + _SAY_HOLD + _SAY_FADE_OUT + 0.1) is None

    def test_custom_hold_extends_the_line(self):
        duck = ChatDuck()
        duck.say("coaching", priority=PRIORITY_COACH, hold=COACH_HOLD, now=0.0)
        text, hold, _seq = duck.tick(now=_SAY_FADE_IN + COACH_HOLD - 0.1)
        assert text == "coaching" and hold == COACH_HOLD

    def test_seq_increments_per_new_line_so_repeats_restart(self):
        # The chrome swallows identical text unless the seq changes; a re-offer
        # after expiry must therefore carry a fresh seq.
        duck = ChatDuck()
        duck.say("Export finished!", now=0.0)
        _, _, seq1 = duck.tick(now=0.1)
        later = _SAY_FADE_IN + _SAY_HOLD + _SAY_FADE_OUT + 1.0
        duck.say("Export finished!", now=later)
        _, _, seq2 = duck.tick(now=later + 0.1)
        assert seq2 > seq1

    def test_same_live_text_same_priority_does_not_restart(self):
        # Re-offering the line already showing lets it play out (no seq bump,
        # so the fade isn't restarted every frame by a repeating caller).
        duck = ChatDuck()
        duck.say("working", now=0.0)
        _, _, seq1 = duck.tick(now=0.1)
        assert duck.say("working", now=0.2)
        _, _, seq2 = duck.tick(now=0.3)
        assert seq1 == seq2

    def test_equal_priority_new_text_replaces(self):
        duck = ChatDuck()
        duck.say("Tasks sliced!", now=0.0)
        duck.say("Sprints packed!", now=0.1)
        assert duck.tick(now=0.2)[0] == "Sprints packed!"


class TestQuipTables:
    def test_quips_fit_the_bubble_budget(self):
        # Same ≤40-char rule the shared DUCK_QUIPS table is held to — a longer
        # line wraps out of the corner bubble.
        from yeaboi.ui.session.chat._duck import PHASE_QUIPS, WORKING_QUIPS

        for key, quip in PHASE_QUIPS.items():
            assert 0 < len(quip) <= 40, key
        for quip in WORKING_QUIPS:
            assert 0 < len(quip) <= 40, quip
