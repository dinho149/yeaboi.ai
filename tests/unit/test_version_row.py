"""Tests for the mode-select bottom-left version hint row (_build_version_row)."""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.mode_select.screens import _screens


def _status(**overrides) -> dict:
    base = {
        "current": "2.12.0",
        "latest": "",
        "update_available": False,
        "upgrade_command": "uv tool upgrade yeaboi",
        "is_dev": False,
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _not_restarted(monkeypatch):
    """Default every test to a normal launch — the ✓ updated chip is opt-in."""
    monkeypatch.setattr("yeaboi.update_check.is_fresh_restart", lambda: False)


@pytest.fixture
def _patch_status(monkeypatch):
    def _apply(**overrides):
        monkeypatch.setattr("yeaboi.update_check.get_update_status", lambda: _status(**overrides))

    return _apply


@pytest.fixture
def _patch_restarted(monkeypatch):
    def _apply(fresh: bool):
        monkeypatch.setattr("yeaboi.update_check.is_fresh_restart", lambda: fresh)

    return _apply


def _render(text: Text, width: int = 100) -> str:
    console = Console(file=io.StringIO(), width=width, legacy_windows=False)
    console.print(text)
    return console.file.getvalue()


class TestVersionRow:
    def test_shows_version_and_changelog_hint(self, _patch_status):
        _patch_status()
        out = _render(_screens._build_version_row(80))
        assert "v2.12.0" in out
        assert "c changelog" in out

    def test_shows_feedback_hint(self, _patch_status):
        _patch_status()
        out = _render(_screens._build_version_row(80))
        assert "f feedback" in out

    def test_shows_all_tips_hint(self, _patch_status):
        _patch_status()
        out = _render(_screens._build_version_row(80))
        assert "a all tips" in out

    def test_no_upgrade_segment_when_current(self, _patch_status):
        _patch_status()
        out = _render(_screens._build_version_row(80))
        assert "→" not in out
        assert "upgrade" not in out.replace("c changelog", "")

    def test_outdated_shows_new_version_and_command(self, _patch_status):
        _patch_status(latest="2.13.0", update_available=True)
        out = _render(_screens._build_version_row(80))
        assert "v2.12.0" in out
        assert "→" in out
        assert "v2.13.0" in out
        assert "uv tool upgrade yeaboi" in out

    def test_narrow_width_drops_command(self, _patch_status):
        _patch_status(latest="2.13.0", update_available=True)
        out = _render(_screens._build_version_row(60))
        assert "v2.13.0" in out  # new version still shown
        assert "uv tool upgrade yeaboi" not in out
        assert "c changelog" in out

    def test_dev_version_renders_plain(self, _patch_status):
        _patch_status(current="0.0.0+dev", is_dev=True)
        out = _render(_screens._build_version_row(80))
        assert "v0.0.0+dev" in out
        assert "→" not in out

    def test_left_justified(self, _patch_status):
        _patch_status()
        assert _screens._build_version_row(80).justify == "left"


class TestModeScreenWithVersionRow:
    def test_mode_screen_still_renders(self, _patch_status, monkeypatch):
        monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
        _patch_status()
        result = _screens._build_mode_screen(0, width=80, height=24, shimmer_tick=0.0)
        assert isinstance(result, Panel)

    def test_mode_screen_height_exact(self, _patch_status, monkeypatch):
        """The extra row must not push the panel past its fixed height."""
        monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
        _patch_status(latest="2.13.0", update_available=True)
        panel = _screens._build_mode_screen(0, width=80, height=24, shimmer_tick=0.0)
        console = Console(file=io.StringIO(), width=80, height=30, legacy_windows=False)
        console.print(panel)
        lines = console.file.getvalue().splitlines()
        assert len(lines) == 24

    def test_version_row_visible_in_mode_screen(self, _patch_status, monkeypatch):
        # The screen's own minimum, so the mode grid doesn't crop the bottom rows.
        # Pinned to the constant rather than a literal: eleven Humans cards moved
        # it once already, and the test should move with it.
        monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: False)
        _patch_status()
        height = _screens._MIN_HEIGHT
        panel = _screens._build_mode_screen(0, width=80, height=height, shimmer_tick=0.0)
        console = Console(file=io.StringIO(), width=80, height=height + 5, legacy_windows=False)
        console.print(panel)
        out = console.file.getvalue()
        assert "v2.12.0" in out
        assert "changelog" in out


class TestUpdateBox:
    """Bottom-right update box (companion lane) — the 'more pressing' advisory."""

    def test_none_when_up_to_date(self, _patch_status):
        _patch_status()
        assert _screens._build_update_box(cols=36) is None

    def test_none_on_dev_build(self, _patch_status):
        _patch_status(latest="2.13.0", update_available=True, is_dev=True)
        assert _screens._build_update_box(cols=36) is None

    def test_shows_version_and_ctrl_u(self, _patch_status):
        _patch_status(latest="2.13.0", update_available=True)
        box = _screens._build_update_box(cols=36)
        assert isinstance(box, Panel)
        out = _render(box, width=40)
        assert "v2.13.0" in out
        assert "ctrl+U" in out

    def test_version_row_suppresses_advisory_when_box_carries_it(self, _patch_status):
        _patch_status(latest="2.13.0", update_available=True)
        out = _render(_screens._build_version_row(120, suppress_upgrade=True))
        assert "→" not in out
        assert "uv tool upgrade yeaboi" not in out
        assert "v2.12.0" in out  # current version still shown
        assert "c changelog" in out


class TestUpdateScreen:
    """The ctrl+U modal: spinner → success / failure result."""

    def test_running_shows_spinner_and_target(self):
        out = _render(_screens._build_update_screen(80, 24, latest="2.13.0", command="x", spinner="*"), width=80)
        assert "updating to v2.13.0" in out
        assert "*" in out

    def test_success_counts_down_to_the_restart(self):
        out = _render(
            _screens._build_update_screen(
                80, 24, latest="2.13.0", command="x", done=True, ok=True, restart_in=2, can_restart=True
            ),
            width=80,
        )
        assert "v2.13.0" in out
        assert "restarting in 2" in out
        assert "esc to stay" in out

    def test_success_without_a_relaunch_command_asks_for_a_manual_restart(self):
        out = _render(
            _screens._build_update_screen(80, 24, latest="2.13.0", command="x", done=True, ok=True, can_restart=False),
            width=80,
        )
        assert "restart yeaboi" in out
        assert "press any key" in out
        assert "restarting in" not in out

    def test_failure_shows_manual_command(self):
        out = _render(
            _screens._build_update_screen(
                80, 24, latest="2.13.0", command="uv tool upgrade yeaboi", done=True, ok=False, detail="boom"
            ),
            width=80,
        )
        assert "failed" in out
        assert "uv tool upgrade yeaboi" in out


class TestRestartedConfirmation:
    """After a ctrl+U restart the row confirms the version that actually took."""

    def test_confirms_the_new_version(self, _patch_status, _patch_restarted):
        _patch_status(current="2.13.0")
        _patch_restarted(True)
        out = _render(_screens._build_version_row(100))
        assert "✓ updated" in out
        assert "v2.13.0" in out

    def test_silent_on_a_normal_launch(self, _patch_status):
        _patch_status(current="2.13.0")
        out = _render(_screens._build_version_row(100))
        assert "✓ updated" not in out

    def test_silent_when_an_upgrade_is_still_pending(self, _patch_status, _patch_restarted):
        # Mid-restart the check can already know about a NEWER release; the pending
        # upgrade is the more useful thing to show, so it wins the slot.
        _patch_status(current="2.13.0", latest="2.14.0", update_available=True)
        _patch_restarted(True)
        out = _render(_screens._build_version_row(100))
        assert "✓ updated" not in out
        assert "v2.14.0" in out

    def test_silent_when_an_upgrade_is_pending_in_the_companion_layout(self, _patch_status, _patch_restarted):
        # suppress_upgrade means _build_update_box is carrying the pending upgrade;
        # the row must not answer it with "you're up to date" two columns away.
        _patch_status(current="2.13.0", latest="2.14.0", update_available=True)
        _patch_restarted(True)
        out = _render(_screens._build_version_row(100, suppress_upgrade=True))
        assert "✓ updated" not in out

    def test_dropped_on_a_narrow_terminal(self, _patch_status, _patch_restarted):
        _patch_status(current="2.13.0")
        _patch_restarted(True)
        out = _render(_screens._build_version_row(60), width=60)
        assert "✓ updated" not in out
        assert "c changelog" in out


class TestRowFitsItsColumn:
    """Chips are dropped whole, never cropped mid-word.

    Rich crops this row rather than wrapping it, so a chip that overruns the
    column simply disappears — and a chip that half-fits renders as a bare key
    with no label. The companion duck's lane is the budget the old width-only
    gate did not know about: ``s schedule`` used to vanish at 108 and render as
    ``s`` at 118.
    """

    @staticmethod
    def _chips(width: int, *, show_companion: bool) -> str:
        return _screens._build_version_row(width, show_companion=show_companion).plain

    def test_no_chip_is_half_drawn_in_the_companion_layout(self, _patch_status):
        _patch_status()
        for width in range(_screens._COMPANION_MIN_WIDTH, 200):
            row = self._chips(width, show_companion=True)
            budget = _screens._version_row_budget(width, show_companion=True)
            assert len(row) <= budget, f"row overruns its column at width {width}"
            chips = (("c", "changelog"), ("f", "feedback"), ("a", "all tips"), ("s", "schedule"), ("n", "niko"))
            for key, label in chips:
                if f" {key} " in f" {row} ":
                    assert f"{key} {label}" in row, f"chip {key!r} lost its label at width {width}"

    def test_schedule_survives_the_lane_it_used_to_lose_to(self, _patch_status):
        _patch_status()
        # 108 and 118 are where the width-only gate cropped it; either it fits
        # whole or it is dropped whole.
        for width in (108, 118):
            row = self._chips(width, show_companion=True)
            assert "s schedule" in row or "schedule" not in row

    def test_niko_is_the_first_chip_dropped(self, _patch_status):
        _patch_status()
        # It is last in the row, so no width shows Niko while hiding schedule.
        for width in range(72, 200):
            for companion in (False, True):
                row = self._chips(width, show_companion=companion)
                if "n niko" in row:
                    assert "s schedule" in row, f"niko outlived schedule at width {width}"

    def test_the_lane_costs_the_row_its_width(self, _patch_status):
        _patch_status()
        assert _screens._version_row_budget(140, show_companion=True) == (
            _screens._version_row_budget(140, show_companion=False) - _screens._COMPANION_COLS
        )


class TestNikoKeycap:
    """Niko is a keycap, not a mode card — the duck is its other door."""

    def test_offered_when_the_row_has_room(self, _patch_status):
        _patch_status()
        assert "n niko" in _screens._build_version_row(140, show_companion=True).plain

    def test_dropped_at_the_minimum_width(self, _patch_status):
        _patch_status()
        row = _screens._build_version_row(_screens._MIN_WIDTH, show_companion=False).plain
        assert "n niko" not in row
        assert "s schedule" in row  # the older keycap keeps its slot
