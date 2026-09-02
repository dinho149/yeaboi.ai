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


# ---------------------------------------------------------------------------
# The Weekly Review page (_screens_solo.py)
# ---------------------------------------------------------------------------


def _review(**overrides):
    from yeaboi.agent.state import DeliveredItem, ReviewAction, WeeklyReview

    base = dict(
        week_label="2026-W36",
        week_start="2026-08-31",
        week_end="2026-09-04",
        project_name="Demo",
        my_name="Dinho",
        standup_lines=("Mon 2026-08-31: Closed S-0 — blocked: keys",),
        confidence_start=60,
        confidence_end=72,
        confidence_label="On track",
        sprint_name="Sprint 1",
        sprint_day=4,
        sprint_total_days=10,
        delivered_items=(DeliveredItem(key="S-0", title="Login form"),),
        planned_story_count=6,
        plan_status="on_track",
        plan_line="Day 4/10 of Sprint 1 · On track (72%) · 1 ticket closed against 6 stories planned",
        summary="A steady week.",
        went_well=("Shipped the login form",),
        to_change=("Stop starting Fridays",),
        actions=(ReviewAction(id="a1", text="Write the OAuth spike", week_label="2026-W36"),),
        carried_actions=(
            ReviewAction(id="c1", text="Fix the flaky test", status="done", origin="carryover", week_label="2026-W35"),
            ReviewAction(id="c2", text="Trim the backlog", status="dropped", origin="carryover", week_label="2026-W35"),
        ),
        warnings=("AI review unavailable — fallback prose.",),
    )
    base.update(overrides)
    return WeeklyReview(**base)


def _page(width: int, height: int, data: dict, *, plain: bool = True, **kw) -> str:
    from yeaboi.ui.mode_select.screens._screens_solo import _build_solo_review_screen

    console = Console(width=width, height=height, force_terminal=not plain, color_system=None if plain else "truecolor")
    with console.capture() as cap:
        console.print(_build_solo_review_screen(data, width=width, height=height, **kw))
    return cap.get()


class TestReviewCard:
    def test_the_solo_menu_carries_review_after_standup(self):
        keys = [c["key"] for c in _SOLO_CARDS]
        assert keys.index("weekly-review") == keys.index("daily-standup") + 1
        assert "weekly-review" not in {c["key"] for c in _MODE_CARDS}

    def test_eight_cards_and_the_full_strip_fit_the_floor(self):
        # 8 cards (26 rows) + the 7-row strip must still leave the hints on screen at 84×40.
        out = _render(84, 40, cards=_SOLO_CARDS, today=FULL, world="solo", shimmer_tick=30.0, plain=True)
        assert "Yesterday" in out
        assert "REVIEW" in out.replace(" ", "").replace("░", "").upper() or "BETA" in out
        assert _today_rows(FULL, body_area=34, cards_h=26) == _TODAY_FULL_ROWS

    def test_solo_rows_stay_contiguous_with_eight_cards(self):
        w, h = 120, 40
        hit_order = []
        for row in range(1, h + 1):
            idx = mode_at_row(3, width=w, height=h, row=row, col=10, cards=_SOLO_CARDS, today=FULL)
            if idx is not None and (not hit_order or hit_order[-1] != idx):
                hit_order.append(idx)
        assert hit_order == list(range(len(_SOLO_CARDS)))


class TestReviewDetailView:
    def test_renders_every_section(self):
        out = _page(100, 70, {"view": "detail", "review": _review()})
        for needle in (
            "Against the plan",
            "On track",
            "Summary",
            "What went well",
            "What to change",
            "Actions for next week",
            "Carried from last week",
            "This week's standups",
            "Delivered (1)",
            "Notices",
            "Export",
            "Anonymize",
        ):
            assert needle in out, needle

    def test_status_glyphs_mark_carried_actions(self):
        out = _page(100, 44, {"view": "detail", "review": _review()})
        assert "● Fix the flaky test" in out
        assert "✕ Trim the backlog" in out
        assert "○ Write the OAuth spike" in out

    def test_no_review_is_an_honest_empty_state(self):
        out = _page(80, 24, {"view": "detail", "review": None})
        assert "No review yet" in out

    def test_scrolls_and_publishes_geometry(self):
        meta: dict = {}
        _page(80, 20, {"view": "detail", "review": _review()}, scroll_offset=3, scroll_meta=meta)
        assert meta.get("max_offset", 0) > 0
        assert meta.get("viewport_h", 0) > 0

    def test_message_and_anon_note_show(self):
        out = _page(100, 30, {"view": "detail", "review": _review(), "message": "Exported."}, anon_note="masked 2")
        assert "Exported." in out
        assert "masked 2" in out

    def test_returns_a_page_panel(self):
        from rich.panel import Panel

        from yeaboi.ui.mode_select.screens._screens_solo import _build_solo_review_screen

        assert isinstance(_build_solo_review_screen({"view": "detail", "review": None}), Panel)


class TestReviewCarriedView:
    def test_carried_rows_show_status_and_origin(self):
        review = _review()
        out = _page(100, 30, {"view": "carried", "carried": list(review.carried_actions), "cursor": 0})
        assert "Fix the flaky test" in out
        assert "done" in out
        assert "from 2026-W35" in out
        assert "Generate" in out

    def test_empty_carried_names_the_next_step(self):
        out = _page(80, 24, {"view": "carried", "carried": [], "cursor": 0})
        assert "Nothing carried from last week" in out

    def test_phases_cover_the_engine(self):
        from yeaboi.solo.engine import PHASES
        from yeaboi.ui.mode_select.screens._screens_solo import SOLO_REVIEW_PHASES

        assert tuple(p for p, _label in SOLO_REVIEW_PHASES) == PHASES

    def _progress_render(self, progress: list) -> str:
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen
        from yeaboi.ui.mode_select.screens._screens_solo import SOLO_REVIEW_PHASES
        from yeaboi.ui.shared._components import SOLO_THEME, solo_review_title

        panel = _build_standup_progress_screen(
            progress,
            width=100,
            height=30,
            theme=SOLO_THEME,
            title=solo_review_title(width=100),
            label="Reviewing your week",
            phases=SOLO_REVIEW_PHASES,
        )
        console = Console(width=100, height=30, force_terminal=False, color_system=None)
        with console.capture() as cap:
            console.print(panel)
        return cap.get()

    def test_progress_screen_lists_every_phase_pending_before_the_run(self):
        out = self._progress_render(["Starting"])
        assert "Reviewing your week" in out
        for _pid, label in __import__("yeaboi.ui.mode_select.screens._screens_solo", fromlist=["x"]).SOLO_REVIEW_PHASES:
            assert f"○ {label}" in out

    def test_progress_screen_advances_on_the_engine_events(self):
        # The engine emits one structured running/completed event per phase id;
        # a declared row must settle to ✓ (not sit pending with a bare-string
        # footnote), and the active one must not read as pending.
        from yeaboi.analysis.progress import append_component_progress

        progress: list = ["Starting"]
        append_component_progress(progress, component_id="scope", label="Resolving scope", status="running")
        append_component_progress(progress, component_id="scope", label="Resolving scope", status="completed")
        append_component_progress(progress, component_id="standups", label="Reading your standups", status="running")
        out = self._progress_render(progress)
        assert "✓ Resolving scope" in out
        assert "○ Reading your standups" not in out
        assert "○ Reading your sprint plan" in out
        assert "↳ scope" not in out


class TestMarkCycle:
    def test_space_walks_pending_done_dropped(self):
        from yeaboi.ui.mode_select._solo import _cycle_status

        assert _cycle_status("pending") == "done"
        assert _cycle_status("done") == "dropped"
        assert _cycle_status("dropped") == "pending"
        assert _cycle_status("carried") == "pending"
