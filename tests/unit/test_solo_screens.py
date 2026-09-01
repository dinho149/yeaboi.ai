"""Render tests for the Solo welcome's Today strip (_screens.py)."""

from __future__ import annotations

import pytest
from rich.console import Console

from yeaboi.solo.today import TodaySnapshot
from yeaboi.ui.mode_select.screens._screens import (
    _MODE_CARDS,
    _SOLO_CARDS,
    _TODAY_COMPACT_ROWS,
    _TODAY_FULL_ROWS,
    _build_mode_screen,
    _today_lines,
    _today_rows,
    mode_at_row,
    selected_title_offset,
)

FULL = TodaySnapshot(
    standup_date="2026-09-01",
    standup_summary="Closed S-0; started S-1",
    standup_blockers="waiting on API keys",
    sprint_day=2,
    sprint_total_days=5,
    confidence_pct=72,
    confidence_label="On track",
    confidence_trend="improving",
    next_story_id="S-1",
    next_story_title="Wire the login form",
    next_sprint_name="Sprint 1",
    plan_session_id="plan-1",
    spend_usd=12.4,
    spend_sessions=3,
    spend_known=True,
)


@pytest.fixture(autouse=True)
def _quiet_chrome(monkeypatch):
    monkeypatch.setattr("yeaboi.config.is_tips_enabled", lambda: True)
    monkeypatch.setattr(
        "yeaboi.update_check.get_update_status",
        lambda: {"update_available": False, "current": "0.0.0", "latest": "", "upgrade_command": "", "is_dev": False},
    )
    monkeypatch.setattr("yeaboi.update_check.is_fresh_restart", lambda: False)
    monkeypatch.setattr("yeaboi.voice.voice_state", lambda: "ready")


def _render(width: int, height: int, *, plain: bool = False, **kw) -> str:
    console = Console(
        width=width,
        height=height,
        force_terminal=not plain,
        color_system=None if plain else "truecolor",
        record=True,
    )
    with console.capture() as cap:
        console.print(_build_mode_screen(0, width=width, height=height, **kw))
    return cap.get()


class TestRows:
    def test_none_takes_no_rows(self):
        assert _today_rows(None, body_area=34, cards_h=23) == 0

    def test_full_when_the_menu_leaves_room(self):
        assert _today_rows(FULL, body_area=34, cards_h=23) == _TODAY_FULL_ROWS

    def test_compact_when_it_leaves_a_little(self):
        assert _today_rows(FULL, body_area=26, cards_h=23) == _TODAY_COMPACT_ROWS

    def test_nothing_when_it_leaves_none(self):
        assert _today_rows(FULL, body_area=24, cards_h=23) == 0


class TestLines:
    def test_empty_snapshot_names_the_next_step(self):
        lines = _today_lines(TodaySnapshot())
        assert lines[0].endswith("press Enter on Standup")
        assert "no sprint context" in lines[1]
        assert lines[2].endswith("press Enter on Planning")
        assert "no agent sessions" in lines[3]

    def test_full_snapshot_reads_the_numbers(self):
        lines = _today_lines(FULL)
        assert lines[0] == "☀ Yesterday: Closed S-0; started S-1 — blocked: waiting on API keys"
        assert lines[1] == "◷ Sprint day 2/5 · On track (72%) ↑"
        assert lines[2] == "▶ Next: S-1 Wire the login form · Sprint 1"
        assert lines[3] == "⚙ Agents this week: $12.40 across 3 sessions"

    def test_unknown_model_prices_are_marked_approximate(self):
        lines = _today_lines(TodaySnapshot(spend_usd=1.0, spend_sessions=1, spend_known=False))
        assert lines[3] == "⚙ Agents this week: ~$1.00 across 1 session"


class TestRender:
    def test_solo_menu_renders_the_strip_at_the_floor(self):
        out = _render(84, 40, cards=_SOLO_CARDS, today=FULL)
        assert "today" in out and "Wire the login form" in out and "Sprint day 2/5" in out

    def test_solo_menu_renders_the_strip_in_the_companion_layout(self):
        out = _render(120, 45, cards=_SOLO_CARDS, today=FULL)
        assert "today" in out and "Wire the login form" in out

    def test_empty_states_render(self):
        out = _render(84, 40, cards=_SOLO_CARDS, today=TodaySnapshot())
        assert "press Enter on Standup" in out and "press Enter on Planning" in out

    def test_compact_strip_is_one_line(self):
        # 84x34 leaves five rows beside the seven cards — too few for the box,
        # enough for the one-line summary. The builder degrades rather than
        # pushing a card off.
        out = _render(84, 34, cards=_SOLO_CARDS, today=FULL, plain=True)
        assert "Sprint day 2/5" in out and "Next: S-1" in out
        assert "╭─ today" not in out
        assert "╭─ today" in _render(84, 40, cards=_SOLO_CARDS, today=FULL, plain=True)

    def test_no_snapshot_is_byte_identical_to_before(self):
        # The strip is strictly additive: every pinned welcome render elsewhere
        # passes no snapshot and must not move.
        assert _render(84, 40, cards=_SOLO_CARDS) == _render(84, 40, cards=_SOLO_CARDS, today=None)
        assert "today" not in _render(84, 40, cards=_MODE_CARDS)

    def test_the_strip_waits_for_the_sweep(self):
        out = _render(84, 40, cards=_SOLO_CARDS, today=FULL, sweep_front=0.5)
        assert "Wire the login form" not in out

    def test_world_narrows_the_tip_rotation(self):
        # Tick 0 is the voice tip in every world; the render just has to accept
        # the world and not blow up — the rotation itself is tested in test_tips.
        assert _render(84, 40, cards=_SOLO_CARDS, world="solo")


class TestHitTestAgreesWithTheBuilder:
    @pytest.mark.parametrize("size", [(84, 40), (120, 45), (84, 34)])
    def test_first_card_click_lands_below_the_strip(self, size):
        w, h = size
        offset = selected_title_offset(0, width=w, height=h, cards=_SOLO_CARDS, today=FULL)
        rows = _today_rows(
            FULL,
            body_area=(h - 3 - 2 - 1) if w >= 108 and h >= 39 else (h - 3 - 3),
            cards_h=23,
        )
        assert offset >= rows
        # The strip's rows are not a card; the first card's title row is.
        assert mode_at_row(0, width=w, height=h, row=3 + offset, col=10, cards=_SOLO_CARDS, today=FULL) == 0
        if rows:
            assert (
                mode_at_row(0, width=w, height=h, row=3 + offset - rows, col=10, cards=_SOLO_CARDS, today=FULL) is None
            )

    def test_rows_stay_contiguous_with_the_strip(self):
        w, h = 84, 40
        hit_order = []
        for row in range(1, h + 1):
            idx = mode_at_row(0, width=w, height=h, row=row, col=10, cards=_SOLO_CARDS, today=FULL)
            if idx is not None and (not hit_order or hit_order[-1] != idx):
                hit_order.append(idx)
        assert hit_order == list(range(len(_SOLO_CARDS)))

    def test_without_a_snapshot_the_maths_is_unchanged(self):
        assert selected_title_offset(2, width=84, height=40, cards=_SOLO_CARDS) == selected_title_offset(
            2, width=84, height=40, cards=_SOLO_CARDS, today=None
        )
