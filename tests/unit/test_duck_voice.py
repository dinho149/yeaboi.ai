"""Tests for the app-wide duck voice (ui/shared/_duck_voice.py).

The ladder/mute/lifecycle behaviour shared with the chat is pinned in
tests/unit/test_chat_duck.py (which must keep passing against the shim);
this file covers what the shared voice adds: the sticky tier, the module
singleton, the global mute, the quip vocabulary and the bubble fence.
"""

import pytest

from yeaboi.ui.shared import _duck_voice as dv
from yeaboi.ui.shared._duck_voice import (
    DUCK_QUIPS,
    PRIORITY_EVENT,
    DuckVoice,
    default_bubble_room,
    duck_muted,
    duck_voice,
    set_duck_muted,
)
from yeaboi.ui.shared._music_bar import _SAY_FADE_IN, _SAY_FADE_OUT, _SAY_HOLD


@pytest.fixture(autouse=True)
def _fresh_voice():
    dv._reset()
    yield
    dv._reset()


class TestSticky:
    def test_sticky_never_expires(self):
        voice = DuckVoice()
        voice.say_sticky('Delete "sprint 3"?  Enter to confirm', now=0.0)
        line = voice.tick(now=1e6)  # a week of frames later, still up
        assert line is not None and line[0].startswith("Delete")
        assert voice.sticky

    def test_sticky_beats_events(self):
        voice = DuckVoice()
        voice.say_sticky("Sure?", now=0.0)
        assert not voice.say("Exported!", priority=PRIORITY_EVENT, now=0.1)
        assert voice.tick(now=0.2)[0] == "Sure?"

    def test_clear_sticky_releases_the_bubble(self):
        voice = DuckVoice()
        voice.say_sticky("Sure?", now=0.0)
        voice.clear_sticky()
        assert voice.tick(now=0.1) is None
        assert not voice.sticky
        assert voice.say("Deleted.", now=0.2)  # the follow-up toast lands

    def test_clear_sticky_leaves_a_normal_line_alone(self):
        voice = DuckVoice()
        voice.say("Exported!", now=0.0)
        voice.clear_sticky()
        assert voice.tick(now=0.1)[0] == "Exported!"

    def test_new_sticky_replaces_old_sticky(self):
        voice = DuckVoice()
        voice.say_sticky("Delete A?", now=0.0)
        voice.say_sticky("Delete B?", now=0.1)
        assert voice.tick(now=0.2)[0] == "Delete B?"

    def test_normal_lines_are_not_sticky(self):
        voice = DuckVoice()
        voice.say("Exported!", now=0.0)
        assert not voice.sticky
        after = _SAY_FADE_IN + _SAY_HOLD + _SAY_FADE_OUT + 0.1
        assert voice.tick(now=after) is None  # normal expiry untouched


class TestSingleton:
    def test_duck_voice_returns_one_shared_instance(self):
        assert duck_voice() is duck_voice()

    def test_reset_gives_a_fresh_instance(self):
        first = duck_voice()
        dv._reset()
        assert duck_voice() is not first


class TestGlobalMute:
    def test_mute_reads_config_lazily(self, monkeypatch):
        monkeypatch.setenv("DUCK_ENABLED", "false")
        assert duck_muted() is True
        dv._reset()
        monkeypatch.setenv("DUCK_ENABLED", "true")
        assert duck_muted() is False

    def test_set_duck_muted_overrides_config(self, monkeypatch):
        monkeypatch.setenv("DUCK_ENABLED", "true")
        set_duck_muted(True)
        assert duck_muted() is True

    def test_muting_drops_the_live_line(self):
        duck_voice().say("Exported!", now=0.0)
        set_duck_muted(True)
        assert duck_voice().tick(now=0.1) is None

    def test_unmuting_does_not_resurrect_it(self):
        duck_voice().say("Exported!", now=0.0)
        set_duck_muted(True)
        set_duck_muted(False)
        assert duck_voice().tick(now=0.1) is None


class TestQuips:
    def test_quips_fit_the_bubble(self):
        for key, quip in DUCK_QUIPS.items():
            assert 0 < len(quip) <= 40, key

    def test_quips_are_distinct(self):
        assert len(set(DUCK_QUIPS.values())) == len(DUCK_QUIPS)


class TestDuckReact:
    """_duck_react — the one helper every mode completion moment calls."""

    def test_react_quacks_and_speaks_the_quip(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        quacks = []
        monkeypatch.setattr("yeaboi.ui.shared._music_bar.quack_duck", lambda *a, **k: quacks.append(1))
        ms._duck_react("standup_done")
        assert quacks == [1]
        assert duck_voice().tick()[0] == DUCK_QUIPS["standup_done"]

    def test_react_dynamic_text_overrides_the_table(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        monkeypatch.setattr("yeaboi.ui.shared._music_bar.quack_duck", lambda *a, **k: None)
        ms._duck_react("roadmap_done", "3 projects recommended.")
        assert duck_voice().tick()[0] == "3 projects recommended."

    def test_unknown_key_without_text_is_silent(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        quacks = []
        monkeypatch.setattr("yeaboi.ui.shared._music_bar.quack_duck", lambda *a, **k: quacks.append(1))
        ms._duck_react("no-such-event")
        assert quacks == []
        assert duck_voice().tick() is None

    def test_files_export_via_picker_reacts(self, monkeypatch):
        import yeaboi.ui.mode_select as ms

        monkeypatch.setattr("yeaboi.ui.shared._music_bar.quack_duck", lambda *a, **k: None)
        monkeypatch.setattr(ms, "_pick_dest", lambda *a, **k: "files")
        msg = ms._export_via_picker(
            None,
            None,
            lambda *a, **k: "q",
            0.05,
            True,
            mode="standup",
            files_export=lambda: "Exported to ~/exports",
            get_document=lambda: ("t", "md"),
        )
        assert msg == "Exported to ~/exports"
        assert duck_voice().tick()[0] == DUCK_QUIPS["export_done"]


class TestBubbleFence:
    def test_default_room_leaves_the_content_edge_alone(self):
        # width 160, content to col 64: 160 - 16 (duck) - 64 - 7 (chrome) = 73
        assert default_bubble_room(160) == 73

    def test_narrow_terminal_has_no_room(self):
        assert default_bubble_room(90) < dv._BUBBLE_MIN_COLS

    def test_page_supplied_edge_shrinks_the_room(self):
        assert default_bubble_room(160, content_edge=120) < default_bubble_room(160)
