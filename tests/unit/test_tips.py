"""Tests for the rotating welcome-screen tips (ui/shared/_tips.py)."""

from yeaboi.ui.shared import _tips
from yeaboi.ui.shared._tips import (
    TIP_ROTATE_SECONDS,
    FeatureTip,
    build_tips_text,
    current_tip,
    get_tips,
    resolve_index,
    tip_at,
    tip_brightness,
    tip_count,
)
from yeaboi.voice import voice_install_command


def _clear_cache():
    # get_tips is lru_cached; reset so a monkeypatched availability is re-read.
    get_tips.cache_clear()


def test_get_tips_non_empty(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    tips = get_tips()
    assert len(tips) > 1
    assert all(isinstance(t, FeatureTip) and t.text for t in tips)
    _clear_cache()


def test_voice_tip_when_available(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    voice_tip = get_tips()[0]
    assert voice_tip.key == "voice"
    assert "double-tap Space" in voice_tip.text
    _clear_cache()


def test_voice_tip_when_unavailable(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (False, "reason"))
    voice_tip = get_tips()[0]
    # Tip shows the install-method-aware command (not a hardcoded `uv sync`).
    assert "enable dictation" in voice_tip.text
    assert voice_install_command() in voice_tip.text
    _clear_cache()


def test_music_tip_when_available(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    monkeypatch.setattr("yeaboi.music.is_music_available", lambda: (True, ""))
    assert any("Ctrl+P" in t.text for t in get_tips())
    _clear_cache()


def test_music_tip_when_unavailable(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    monkeypatch.setattr("yeaboi.music.is_music_available", lambda: (False, "no ffplay"))
    assert any("brew install" in t.text and "ffmpeg" in t.text for t in get_tips())
    _clear_cache()


def test_current_tip_advances_with_tick(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    idx0, _ = current_tip(0.0)
    idx1, _ = current_tip(TIP_ROTATE_SECONDS + 0.1)
    assert idx0 == 0
    assert idx1 == 1
    _clear_cache()


def test_current_tip_stable_within_window(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    idx_a, tip_a = current_tip(0.0)
    idx_b, tip_b = current_tip(TIP_ROTATE_SECONDS - 0.01)
    assert idx_a == idx_b
    assert tip_a == tip_b
    _clear_cache()


def test_current_tip_wraps_around(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    n = len(get_tips())
    # After a full cycle we return to the first tip.
    idx_first, _ = current_tip(0.0)
    idx_wrapped, _ = current_tip(n * TIP_ROTATE_SECONDS + 0.1)
    assert idx_first == idx_wrapped == 0
    _clear_cache()


def test_current_tip_handles_negative_tick(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    idx, tip = current_tip(-5.0)
    assert idx == 0
    assert tip.text
    _clear_cache()


def test_rotate_seconds_override(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    # With a 1s window, tick=1.5 lands on the second tip.
    idx, _ = current_tip(1.5, rotate_seconds=1.0)
    assert idx == 1
    _clear_cache()


def test_resolve_index_applies_browse_offset(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    n = tip_count()
    # The offset shifts the auto index; auto-rotation still advances with tick.
    assert resolve_index(0.0, 0) == 0
    assert resolve_index(0.0, 3) == 3
    assert resolve_index(0.0, n) == 0  # wraps modulo
    assert resolve_index(0.0, -1) == n - 1  # negative wraps too
    # Offset is additive on top of the tick-driven index, so rotation continues:
    # at one full window the auto index is 1, plus offset 2 → 3.
    assert resolve_index(TIP_ROTATE_SECONDS + 0.1, 2) == 3
    _clear_cache()


def test_build_tips_text_lists_every_tip(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    text = build_tips_text()
    assert text.startswith("# yeaboi — Tips")
    # Every tip's text appears as a bullet.
    for tip in get_tips():
        assert tip.text in text
    assert text.endswith("\n")
    _clear_cache()


def test_build_tips_text_marks_new_and_opens(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    text = build_tips_text()
    # The flagged feature is marked NEW and notes the mode it opens.
    assert "(NEW)" in text
    assert "→ opens Analysis" in text  # team-analysis tip → "Analysis" card
    # An ambient tip (no mode_key) has no opens-note.
    assert "→ opens" not in "\n".join(
        line for line in text.splitlines() if "double-tap Space" in line or "focus music" in line
    )
    _clear_cache()


def test_tip_at_wraps(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    assert tip_at(0) == get_tips()[0]
    assert tip_at(tip_count()) == get_tips()[0]  # wraps around
    _clear_cache()


def test_at_least_one_new_feature_tip(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    # The NEW badge only renders when some tip is flagged fresh.
    assert any(t.is_new for t in _tips._FEATURE_TIPS)
    _clear_cache()


def test_at_least_one_beta_feature_tip(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    # The BETA badge branch would otherwise go dead without anything noticing.
    assert any(t.is_beta for t in _tips._FEATURE_TIPS)
    _clear_cache()


def test_performance_tip_is_beta_not_new(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    perf = next(t for t in _tips._FEATURE_TIPS if t.key == "performance")
    # Unverified, not recent — the two badges make different promises.
    assert perf.is_beta is True
    assert perf.is_new is False
    _clear_cache()


def test_build_tips_text_marks_beta(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    lines = _tips.build_tips_text().splitlines()
    perf_line = next(line for line in lines if "Performance preps 1:1s" in line)
    planning_line = next(line for line in lines if "Planning starts as a chat" in line)
    assert "(BETA)" in perf_line
    assert "(BETA)" not in planning_line
    _clear_cache()


def test_carded_tips_have_mode_keys(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    # A representative feature tip carries a jump target; ambient tips do not.
    planning = next(t for t in _tips._FEATURE_TIPS if t.key == "planning")
    assert planning.mode_key == "project-planning"
    voice = get_tips()[0]
    assert voice.mode_key is None
    _clear_cache()


def test_module_constant_present():
    assert _tips.TIP_ROTATE_SECONDS > 0


def test_tip_count_matches_get_tips(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.is_voice_available", lambda: (True, ""))
    assert tip_count() == len(get_tips())
    _clear_cache()


def test_tip_brightness_full_mid_window():
    # Mid-window (well away from either edge) is fully visible.
    assert tip_brightness(TIP_ROTATE_SECONDS / 2) == 1.0


def test_tip_brightness_fades_in_at_start():
    # Just after a tip appears it is dimmer than mid-window.
    assert 0.0 <= tip_brightness(0.05) < 1.0


def test_tip_brightness_fades_out_before_switch():
    # Just before the next tip it is dimming back toward the background.
    assert 0.0 <= tip_brightness(TIP_ROTATE_SECONDS - 0.05) < 1.0


def test_tip_brightness_in_unit_range():
    for t in (0.0, 0.5, 2.9, 3.0, 5.9, 6.1, 42.0):
        b = tip_brightness(t)
        assert 0.0 <= b <= 1.0
