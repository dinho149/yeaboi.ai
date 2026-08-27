"""Tests for yeaboi.ambience — the shared duck/music/saver vocabulary."""

from __future__ import annotations

from pathlib import Path

import pytest

from yeaboi import ambience, config


@pytest.fixture
def env(monkeypatch):
    """Preferences that write to os.environ only — no ~/.env is touched."""
    monkeypatch.setattr(config, "set_config_value", lambda _k, _v: Path("/tmp/.env"))
    for key in ("DUCK_ENABLED", "MUSIC_ENABLED", "MUSIC_CHANNEL", "PET_ENABLED", "SAVER_STYLE"):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


class TestQuips:
    def test_every_quip_fits_the_bubble(self):
        # The bubble is drawn beside the duck; a longer line is clipped, not wrapped.
        for key, quip in ambience.DUCK_QUIPS.items():
            assert len(quip) <= 40, f"quip {key!r} is {len(quip)} chars"

    def test_no_two_events_say_the_same_thing(self):
        assert len(set(ambience.DUCK_QUIPS.values())) == len(ambience.DUCK_QUIPS)

    def test_the_tui_still_reaches_them_through_the_duck_voice(self):
        from yeaboi.ui.shared._duck_voice import DUCK_QUIPS

        assert DUCK_QUIPS is ambience.DUCK_QUIPS


class TestState:
    def test_defaults(self, env):
        state = ambience.state()
        assert state["duck"]["enabled"] is True
        assert state["music"]["enabled"] is False  # never surprise anyone with noise
        assert state["music"]["channel"] == 0
        assert state["pet"]["enabled"] is False
        assert state["saver"]["idle_seconds"] == ambience.IDLE_SECONDS
        assert state["saver"]["style"] == ambience.DEFAULT_SAVER_STYLE

    def test_channels_carry_a_name_and_a_stream(self, env):
        channels = ambience.state()["music"]["channels"]
        assert channels
        assert all(set(channel) == {"name", "url"} for channel in channels)

    def test_the_quips_travel_with_the_state(self, env):
        assert ambience.state()["duck"]["quips"] == ambience.DUCK_QUIPS

    def test_an_out_of_range_persisted_channel_reads_as_the_first(self, env):
        env.setenv("MUSIC_CHANNEL", "99")
        assert ambience.state()["music"]["channel"] == 0

    def test_an_unrecognised_persisted_style_reads_as_the_default(self, env):
        # A style written by a newer desktop must not blank the terminal's saver.
        env.setenv("SAVER_STYLE", "lava-lamp")
        assert ambience.state()["saver"]["style"] == ambience.DEFAULT_SAVER_STYLE

    def test_the_style_catalogue_travels_with_the_state(self, env):
        assert ambience.state()["saver"]["styles"] == ambience.SAVER_STYLES

    def test_the_state_is_a_copy_a_caller_cannot_corrupt(self, env):
        ambience.state()["duck"]["quips"]["standup_done"] = "nope"
        assert ambience.DUCK_QUIPS["standup_done"] != "nope"
        ambience.state()["saver"]["styles"]["off"] = "nope"
        assert ambience.SAVER_STYLES["off"] != "nope"


class TestApply:
    def test_writes_each_preference_and_answers_with_the_new_state(self, env):
        state = ambience.apply({"duck_enabled": False, "music_enabled": True, "music_channel": 2, "pet_enabled": True})
        assert state["duck"]["enabled"] is False
        assert state["music"] == {**state["music"], "enabled": True, "channel": 2}
        assert state["pet"]["enabled"] is True

    def test_an_empty_change_set_is_a_read(self, env):
        assert ambience.apply({}) == ambience.state()

    def test_an_unknown_key_is_a_caller_bug(self, env):
        with pytest.raises(ValueError, match="unknown ambience setting"):
            ambience.apply({"volume": 11})

    def test_a_channel_off_the_end_is_refused_rather_than_clamped(self, env):
        with pytest.raises(ValueError, match="out of range"):
            ambience.apply({"music_channel": 99})

    def test_a_flag_must_be_a_boolean(self, env):
        with pytest.raises(ValueError, match="must be true or false"):
            ambience.apply({"duck_enabled": "yes"})

    def test_a_boolean_is_not_a_channel_index(self, env):
        # bool is an int in Python; True must not select station 1.
        with pytest.raises(ValueError, match="must be an integer"):
            ambience.apply({"music_channel": True})

    def test_writes_the_saver_style(self, env):
        assert ambience.apply({"saver_style": "aurora"})["saver"]["style"] == "aurora"

    def test_an_unknown_style_is_refused_rather_than_defaulted(self, env):
        # Silently storing "duck-yard" would tell the caller its pick had landed.
        with pytest.raises(ValueError, match="unknown saver_style"):
            ambience.apply({"saver_style": "lava-lamp"})

    def test_a_style_must_be_a_string(self, env):
        with pytest.raises(ValueError, match="must be a string"):
            ambience.apply({"saver_style": 3})
