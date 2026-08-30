"""Tests for the rotating welcome-screen tips (ui/shared/_tips.py)."""

import pytest

from yeaboi.surfaces import ALL_SURFACES
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
    tips_for_surface,
)
from yeaboi.voice import voice_install_command


def _clear_cache():
    # get_tips is lru_cached; reset so a monkeypatched availability is re-read.
    get_tips.cache_clear()


def test_get_tips_non_empty(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    tips = get_tips()
    assert len(tips) > 1
    assert all(isinstance(t, FeatureTip) and t.text for t in tips)
    _clear_cache()


def test_voice_tip_when_available(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    voice_tip = get_tips()[0]
    assert voice_tip.key == "voice"
    assert "double-tap Space" in voice_tip.text
    _clear_cache()


def test_voice_tip_when_installable(monkeypatch):
    """The gesture *is* the install now, so the tip points at it, not at a shell."""
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "installable")
    voice_tip = get_tips()[0]
    assert "double-tap Space" in voice_tip.text
    assert "one keystroke" in voice_tip.text
    assert voice_install_command() not in voice_tip.text
    _clear_cache()


def test_voice_tip_when_declined_falls_back_to_the_manual_command(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "declined")
    voice_tip = get_tips()[0]
    # The install-method-aware command, not a hardcoded `uv sync`.
    assert "enable dictation" in voice_tip.text
    assert voice_install_command() in voice_tip.text
    _clear_cache()


def test_voice_tip_when_unsupported_never_invites_a_doomed_install(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "unsupported")
    monkeypatch.setattr("yeaboi.voice.unsupported_blocker", lambda: "musl libc has no wheel")
    voice_tip = get_tips()[0]
    assert "musl libc has no wheel" in voice_tip.text
    assert voice_install_command() not in voice_tip.text
    _clear_cache()


def test_voice_tip_when_unsupported_names_the_actual_blocker(monkeypatch):
    """A Linux host missing libportaudio2 is 'unsupported' too, and the tip has
    to carry the apt command rather than a sentence about the wheel matrix —
    restating the platform gate there would be actively misleading."""
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "unsupported")
    monkeypatch.setattr(
        "yeaboi.voice.unsupported_blocker",
        lambda: "Audio backend unavailable — sudo apt install libportaudio2",
    )
    assert "libportaudio2" in get_tips()[0].text
    _clear_cache()


def test_music_tip_when_available(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    monkeypatch.setattr("yeaboi.music.is_music_available", lambda: (True, ""))
    assert any("Ctrl+P" in t.text for t in get_tips())
    _clear_cache()


def test_music_tip_when_unavailable(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    monkeypatch.setattr("yeaboi.music.is_music_available", lambda: (False, "no ffplay"))
    assert any("brew install" in t.text and "ffmpeg" in t.text for t in get_tips())
    _clear_cache()


def test_current_tip_advances_with_tick(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    idx0, _ = current_tip(0.0)
    idx1, _ = current_tip(TIP_ROTATE_SECONDS + 0.1)
    assert idx0 == 0
    assert idx1 == 1
    _clear_cache()


def test_current_tip_stable_within_window(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    idx_a, tip_a = current_tip(0.0)
    idx_b, tip_b = current_tip(TIP_ROTATE_SECONDS - 0.01)
    assert idx_a == idx_b
    assert tip_a == tip_b
    _clear_cache()


def test_current_tip_wraps_around(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    n = len(tips_for_surface("tui"))
    # After a full cycle we return to the first tip.
    idx_first, _ = current_tip(0.0)
    idx_wrapped, _ = current_tip(n * TIP_ROTATE_SECONDS + 0.1)
    assert idx_first == idx_wrapped == 0
    _clear_cache()


def test_current_tip_handles_negative_tick(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    idx, tip = current_tip(-5.0)
    assert idx == 0
    assert tip.text
    _clear_cache()


def test_rotate_seconds_override(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    # With a 1s window, tick=1.5 lands on the second tip.
    idx, _ = current_tip(1.5, rotate_seconds=1.0)
    assert idx == 1
    _clear_cache()


def test_resolve_index_applies_browse_offset(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
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
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    text = build_tips_text()
    assert text.startswith("# yeaboi — Tips")
    # Every terminal tip's text appears as a bullet — and no other surface's.
    for tip in tips_for_surface("tui"):
        assert tip.text in text
    for tip in get_tips():
        if "tui" not in tip.surfaces:
            assert tip.text not in text
    assert text.endswith("\n")
    _clear_cache()


def test_build_tips_text_marks_new_and_opens(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
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
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    assert tip_at(0) == get_tips()[0]
    assert tip_at(tip_count()) == get_tips()[0]  # wraps around
    _clear_cache()


def test_at_least_one_new_feature_tip(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    # The NEW badge only renders when some tip is flagged fresh.
    assert any(t.is_new for t in _tips._FEATURE_TIPS)
    _clear_cache()


def test_at_least_one_beta_feature_tip(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    # The BETA badge branch would otherwise go dead without anything noticing.
    assert any(t.is_beta for t in _tips._FEATURE_TIPS)
    _clear_cache()


def test_performance_tip_is_beta_not_new(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    perf = next(t for t in _tips._FEATURE_TIPS if t.key == "performance")
    # Unverified, not recent — the two badges make different promises.
    assert perf.is_beta is True
    assert perf.is_new is False
    _clear_cache()


def test_build_tips_text_marks_beta(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    lines = _tips.build_tips_text().splitlines()
    perf_line = next(line for line in lines if "Performance preps 1:1s" in line)
    planning_line = next(line for line in lines if "which of your own repos could help" in line)
    assert "(BETA)" in perf_line
    assert "(BETA)" not in planning_line
    _clear_cache()


def test_carded_tips_have_mode_keys(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    # A representative feature tip carries a jump target; ambient tips do not.
    planning = next(t for t in _tips._FEATURE_TIPS if t.key == "planning")
    assert planning.mode_key == "project-planning"
    voice = get_tips()[0]
    assert voice.mode_key is None
    _clear_cache()


def test_module_constant_present():
    assert _tips.TIP_ROTATE_SECONDS > 0


def test_tip_count_matches_the_tui_rotation(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    assert tip_count() == len(tips_for_surface("tui"))
    # The terminal never counts the whole registry: it holds both surfaces.
    assert tip_count() < len(get_tips())
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


# --- per-surface split -----------------------------------------------------


def test_untagged_tips_reach_every_surface():
    tip = FeatureTip("x", "some text")
    assert tip.surfaces == ALL_SURFACES


def test_tips_for_surface_keeps_rotation_order(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    everything = get_tips()
    tui = tips_for_surface("tui")
    assert list(tui) == [t for t in everything if "tui" in t.surfaces]
    _clear_cache()


@pytest.mark.parametrize("state", ["ready", "installable", "declined", "unsupported"])
def test_each_surface_opens_on_its_own_voice_tip(monkeypatch, state):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: state)
    monkeypatch.setattr("yeaboi.voice.unsupported_blocker", lambda: "no libportaudio2")
    tui, desktop = (tips_for_surface(s)[0] for s in ("tui", "desktop"))
    assert tui.key == desktop.key == "voice"
    if state == "unsupported":
        # The one state that is a fact about the machine rather than a gesture.
        assert tui.text == desktop.text
    else:
        assert "mic" in desktop.text and "mic" not in tui.text
    _clear_cache()


def test_the_two_surfaces_disagree(monkeypatch):
    # The whole point of the split: neither list is the other's.
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    tui = {t.text for t in tips_for_surface("tui")}
    desktop = {t.text for t in tips_for_surface("desktop")}
    assert tui - desktop and desktop - tui
    # …and they still share the tips that describe the product rather than a gesture.
    assert len(tui & desktop) > 10
    _clear_cache()


def test_terminal_only_tips_never_reach_the_desktop(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    keys = {t.key for t in tips_for_surface("desktop")}
    # Focus music has no desktop control, and a window has no headless mode.
    assert "music" not in keys
    assert "meta:headless" not in keys
    _clear_cache()


def test_desktop_only_tips_never_reach_the_terminal(monkeypatch):
    _clear_cache()
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")
    assert not [t for t in tips_for_surface("tui") if t.key.startswith("desktop:")]
    assert [t for t in tips_for_surface("desktop") if t.key.startswith("desktop:")]
    _clear_cache()


def test_tips_for_an_unknown_surface_raise():
    # Not an empty tuple: a typo would leave a screen with nothing to rotate,
    # and tip_at divides by the length.
    with pytest.raises(ValueError, match="unknown surface"):
        tips_for_surface("fax-machine")
