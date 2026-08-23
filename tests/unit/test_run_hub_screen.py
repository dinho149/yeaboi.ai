"""Render tests for the saved-runs hub.

The hub is the standup/retro/reporting/performance landing that lists past runs with
Open/Delete/Export — the list render (`_build_run_hub_screen`) must not crash at any size
or state (populated list, empty state, focused row with buttons, delete popup). Opening a
saved run renders it through the mode's OWN rich builder (not a flat text view), so
`TestSnapshotRendering` checks each mode's snapshot data shape drives its themed screen.
"""

import io

import pytest
from rich.console import Console
from rich.panel import Panel

from yeaboi.ui.mode_select.screens._project_cards import RunSummary
from yeaboi.ui.mode_select.screens._run_hub_screen import _build_run_hub_screen
from yeaboi.ui.shared import _duck_voice
from yeaboi.ui.shared._components import performance_title, reporting_title, standup_title


@pytest.fixture(autouse=True)
def _fresh_voice():
    # The hub speaks through the module-global shared voice — isolate per test.
    _duck_voice._reset()
    yield
    _duck_voice._reset()


def _text(panel: Panel, width: int = 100, height: int = 30) -> str:
    console = Console(file=io.StringIO(), width=width, height=height, legacy_windows=False)
    console.print(panel)
    return console.file.getvalue()


def _runs(n: int = 4) -> list[RunSummary]:
    return [
        RunSummary("standup", i, f"Standup — 2026-07-0{i}", f"Day {i} · 80% confident", "2 days ago")
        for i in range(1, n + 1)
    ]


class TestHubList:
    def test_populated_list_renders(self):
        out = _text(_build_run_hub_screen(_runs(), 0, title_fn=standup_title, subtitle="Saved standups"))
        assert "Saved standups" in out
        assert "Standup — 2026-07-01" in out

    def test_empty_state_uses_custom_text(self):
        panel = _build_run_hub_screen(
            [], 0, title_fn=standup_title, empty_title="No standups yet", empty_subtitle="Press Enter to run one"
        )
        out = _text(panel)
        assert "No standups yet" in out
        assert "+ New run" in out

    def test_selected_row_shows_action_buttons(self):
        panel = _build_run_hub_screen(
            _runs(), 1, title_fn=standup_title, focus=2, action_btns_visible=2.0, card_fade=1.0
        )
        out = _text(panel)
        assert "Delete" in out and "Export" in out

    def test_builder_never_stamps_the_duck_itself(self):
        # The confirmation and the toasts moved to the shared voice (the hub
        # LOOP feeds it; see TestHubDeleteConfirm) — the builder only declares
        # the bubble's free room and never writes _duck_say raw.
        panel = _build_run_hub_screen(
            _runs(),
            2,
            title_fn=standup_title,
            delete_popup_name="Standup — 2026-07-03",
            delete_popup_t=1.0,
            message="Deleted.",
        )
        assert getattr(panel, "_duck_say", "") == ""
        assert "Enter to confirm" not in _text(panel)  # not in the page body either

    def test_builder_declares_generous_bubble_room(self):
        # Cards are capped at ~56 cols, so on a wide terminal everything right
        # of them belongs to the duck's bubble.
        panel = _build_run_hub_screen(_runs(), 0, title_fn=standup_title, width=160)
        assert panel._bubble_room >= _duck_voice._BUBBLE_MIN_COLS

    def test_small_terminal_does_not_crash(self):
        # Just needs to render without raising at a cramped size.
        _text(_build_run_hub_screen(_runs(8), 5, title_fn=reporting_title), width=60, height=16)


class TestPerformanceHubList:
    """Performance rows are per-engineer artifacts, not one run per date.

    The card's landing lists the whole team, so a row has to say who it is about —
    otherwise "1:1 Prep — 2026-07-01" appears four times with nothing to tell apart.
    """

    def _artifacts(self):
        return [
            RunSummary(
                "performance", 1, "1:1 Prep — 2026-07-05", "Ada · Prep", "2 days ago", kind="prep", engineer="Ada"
            ),
            RunSummary(
                "performance",
                2,
                "6-Month Review — 2026-01-01..2026-06-30",
                "Bob · Review",
                "a week ago",
                kind="review",
                engineer="Bob",
            ),
        ]

    def test_rows_name_the_engineer(self):
        out = _text(
            _build_run_hub_screen(
                self._artifacts(), 0, title_fn=performance_title, subtitle="Saved artifacts", new_label="+ New artifact"
            )
        )
        assert "Saved artifacts" in out
        assert "1:1 Prep — 2026-07-05" in out
        assert "Ada" in out and "Bob" in out
        assert "+ New artifact" in out

    def test_team_wide_empty_state(self):
        out = _text(
            _build_run_hub_screen(
                [],
                0,
                title_fn=performance_title,
                empty_title="No saved artifacts yet",
                empty_subtitle="Press Enter to pick an engineer and create one",
                new_label="+ New artifact",
            )
        )
        assert "No saved artifacts yet" in out
        assert "pick an engineer" in out


class TestHubExtraCard:
    """The optional fixed card below "+ New run" (standup's schedule entry)."""

    def test_extra_card_renders_in_populated_list(self):
        runs = _runs(2)
        panel = _build_run_hub_screen(
            runs, len(runs) + 1, title_fn=standup_title, new_label="+ New standup", extra_label="⏰ Set up a schedule"
        )
        out = _text(panel)
        assert "+ New standup" in out
        assert "Set up a schedule" in out

    def test_extra_card_renders_in_empty_state(self):
        panel = _build_run_hub_screen(
            [],
            1,
            title_fn=standup_title,
            empty_title="No standups yet",
            new_label="+ New standup",
            extra_label="⏰ Schedule · On · 09:50 · Mon–Fri · terminal",
            height=30,
        )
        out = _text(panel)
        assert "No standups yet" in out
        assert "+ New standup" in out
        assert "Schedule · On · 09:50" in out

    def test_without_extra_label_output_unchanged(self):
        # Retro/reporting pass nothing — the extra card must not appear.
        with_default = _text(_build_run_hub_screen(_runs(), 0, title_fn=reporting_title))
        explicit_empty = _text(_build_run_hub_screen(_runs(), 0, title_fn=reporting_title, extra_label=""))
        assert with_default == explicit_empty
        assert "schedule" not in with_default.lower()

    def test_delete_popup_with_extra_card_does_not_crash(self):
        panel = _build_run_hub_screen(
            _runs(6),
            2,
            title_fn=standup_title,
            delete_popup_name="Standup — 2026-07-03",
            delete_popup_t=1.0,
            extra_label="⏰ Set up a schedule",
        )
        _text(panel, height=20)  # must render at a cramped height without raising
        # The confirmation is spoken through the shared voice by the hub loop,
        # not drawn in the body (see TestHubDeleteConfirm).
        assert "Enter to confirm" not in _text(panel, height=20)

    def test_small_terminal_with_extra_card_does_not_crash(self):
        _text(
            _build_run_hub_screen(_runs(8), 9, title_fn=standup_title, extra_label="⏰ Set up a schedule"),
            width=60,
            height=16,
        )


_SNAP_ACTIONS = ["Export", "Delete", "Run again", "Back"]


class TestSnapshotRendering:
    """Opening a saved run feeds the report into the mode's real rich screen builder.

    These mirror the data shape each ``make_detail`` / ``open_snapshot`` in
    ``mode_select/__init__`` passes, so the snapshot looks like the live screen
    (themed, meters/grids/cards) rather than flat grey markdown.
    """

    def test_reporting_detail_renders_rich(self):
        from yeaboi.agent.state import DeliveryReport
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_reporting_screen

        report = DeliveryReport(
            period_label="Last month",
            headline="A good month.",
            executive_summary="shipped auth",
            metrics=(("Items delivered", "5"),),
        )
        panel = _build_reporting_screen(
            {
                "view": "detail",
                "report": report,
                "detail_title": "Delivery Report — Last month",
                "actions": _SNAP_ACTIONS,
            },
            action_sel=0,
            width=100,
            height=30,
        )
        out = _text(panel)
        assert "Delivery Report — Last month" in out
        assert "shipped auth" in out and "Run again" in out
        assert "By the numbers" in out  # rich metrics section, not flat lines

    def test_performance_detail_renders_rich(self):
        from yeaboi.agent.state import SixMonthReview
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_performance_screen

        panel = _build_performance_screen(
            {
                "view": "detail",
                "artifact": SixMonthReview(engineer="Ada", strengths=("ownership",)),
                "kind": "review",
                "detail_title": "6-month review — Ada",
                "actions": _SNAP_ACTIONS,
            },
            action_sel=0,
            width=100,
            height=30,
        )
        out = _text(panel)
        assert "6-month review — Ada" in out
        assert "ownership" in out

    def test_retro_snapshot_shows_grids_and_hides_join(self):
        from yeaboi.agent.state import RetroCard, RetroReport
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_retro_screen

        report = RetroReport(
            session_id="s",
            project_name="Demo",
            cards=(RetroCard(id="a", grid="went_well", text="ci is green", author="Sam", origin="web"),),
        )
        panel = _build_retro_screen(
            {
                "grids": report.by_grid(),
                "carried": list(report.carried_action_items),
                "session_name": report.project_name,
                "snapshot": True,
                "actions": _SNAP_ACTIONS,
            },
            action_sel=0,
            width=100,
            height=40,
        )
        out = _text(panel)
        assert "ci is green" in out
        assert "Send this to your team" not in out  # live-only join block suppressed for a saved run

    def test_retro_live_still_shows_join(self):
        # Guard sanity: without the snapshot flag the live board still renders the join block.
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_retro_screen

        panel = _build_retro_screen(
            {"grids": {}, "display_code": "ABC123", "actions": ["Generate Action Items", "Export", "Close"]},
            action_sel=0,
            width=100,
            height=40,
        )
        assert "Send this to your team" in _text(panel)

    def test_poker_snapshot_shows_results_and_hides_join(self):
        from yeaboi.agent.state import PokerReport, PokerTicketResult, PokerVote
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_poker_screen

        report = PokerReport(
            session_id="s",
            project_name="Demo",
            source="jira",
            scope_label="Sprint 42",
            tickets=(
                PokerTicketResult(
                    key="PROJ-1",
                    summary="Add login",
                    initial_points=None,
                    final_points=5.0,
                    estimated=True,
                    votes=(PokerVote("Sam", "🐙", "5"),),
                ),
            ),
            participants=("Sam",),
        )
        panel = _build_poker_screen(
            {
                "report": report,
                "session_name": report.project_name,
                "snapshot": True,
                "actions": _SNAP_ACTIONS,
            },
            action_sel=0,
            width=100,
            height=40,
        )
        out = _text(panel)
        assert "Add login" in out
        assert "5 points" in out
        assert "Send this to your team" not in out  # live-only join block suppressed for a saved run

    def test_poker_live_still_shows_join(self):
        from yeaboi.poker.board import PokerBoard
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_poker_screen

        board = PokerBoard("s", tickets=[{"key": "T-1", "summary": "X"}])
        panel = _build_poker_screen(
            {
                "state": board.state_snapshot(),
                "display_code": "ABC123",
                "actions": ["Copy Invite", "Copy Host Link", "Export", "Close"],
            },
            action_sel=0,
            width=100,
            height=40,
        )
        assert "Send this to your team" in _text(panel)

    def test_standup_overview_shows_meter_strip(self):
        from yeaboi.agent.state import StandupReport
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_screen

        report = StandupReport(
            date="2026-07-01",
            sprint_name="Sprint 5",
            sprint_day=3,
            sprint_total_days=10,
            confidence_label="On track",
            confidence_pct=80,
            team_summary="All green.",
        )
        # Build the dict exactly as open_standup_snapshot does (reading report attrs) so a
        # missing/renamed field — StandupReport has no project_name — fails here, not at runtime.
        data = {"report": report, "session_name": "", "my_name": report.my_name, "team_expanded": False}
        panel = _build_standup_screen(
            data,
            view="overview",
            selected_card=0,
            action_sel=0,
            actions=_SNAP_ACTIONS,
            width=100,
            height=30,
        )
        out = _text(panel)
        assert "Sprint 5" in out and "On track" in out  # pinned meter strip, not flat text

    def test_standup_section_detail_renders(self):
        from yeaboi.agent.state import StandupReport
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_screen

        report = StandupReport(date="2026-07-01", sprint_name="Sprint 5", team_summary="All systems green.")
        panel = _build_standup_screen(
            {"report": report, "session_name": "Demo"},
            view="summary",
            action_sel=0,
            actions=["← Overview"],
            width=100,
            height=30,
        )
        assert "green" in _text(panel).lower()


class TestStandupSnapshotLoop:
    """Regression: opening a saved standup and pressing an action button must not crash.

    The standup snapshot uses an ``open_snapshot`` override that drives the shared
    [Export, Delete, Run again, Back] actions through a run-bound callback. A wiring bug
    once passed the two-arg ``_run_action`` and then called it with one arg, so every
    button raised ``TypeError`` inside the live loop. This drives the loop headlessly to
    the Back button — the exact dispatch that used to crash.
    """

    def test_open_snapshot_and_press_back_does_not_crash(self, tmp_path, monkeypatch):
        import yeaboi.ui.mode_select as ms
        from yeaboi.agent.state import StandupReport
        from yeaboi.standup.store import StandupStore

        db = tmp_path / "sessions.db"
        with StandupStore(db) as store:
            store.record_run(
                StandupReport(
                    date="2026-07-01",
                    session_id="s1",
                    sprint_name="Sprint 5",
                    sprint_day=3,
                    sprint_total_days=10,
                    team_summary="all good",
                )
            )
        monkeypatch.setattr(ms, "_ana_dbp", db)

        class _Console:
            size = (120, 40)

            def print(self, *a, **k):
                pass

        class _Live:
            def update(self, *a, **k):
                pass

        # Enter opens the run's snapshot; three Rights move focus to the Back button;
        # Enter presses Back (the dispatch that used to raise); q exits the hub.
        keys = iter(["enter", "right", "right", "right", "enter", "q"])

        def read_key(timeout=None):
            return next(keys, "q")

        ms._run_standup_hub(_Console(), _Live(), read_key, 0.05, True)  # must not raise


class TestHubScheduleCardLoop:
    """Driving the standup hub to the schedule card must invoke the wizard."""

    def test_enter_on_schedule_card_runs_wizard_and_shows_toast(self, tmp_path, monkeypatch):
        import yeaboi.ui.mode_select as ms
        from yeaboi.agent.state import StandupReport
        from yeaboi.sessions import SessionStore
        from yeaboi.standup.store import StandupStore

        db = tmp_path / "sessions.db"
        with SessionStore(db) as sstore:
            sstore.create_session("s1", "Proj")
        with StandupStore(db) as store:
            store.record_run(StandupReport(date="2026-07-01", session_id="s1", team_summary="ok"))
        monkeypatch.setattr(ms, "_ana_dbp", db)

        calls = []
        monkeypatch.setattr(
            ms,
            "_run_standup_schedule_wizard",
            lambda console, live, rk, ft, st, session_id: calls.append(session_id) or "Schedule saved.",
        )

        class _Console:
            size = (120, 40)

            def print(self, *a, **k):
                pass

        rendered = []

        class _Live:
            def update(self, panel):
                rendered.append(panel)

        # One run in the list → indices: 0 run, 1 "+ New standup", 2 schedule card.
        keys = iter(["down", "down", "enter", "q"])

        def read_key(timeout=None):
            return next(keys, "q")

        ms._run_standup_hub(_Console(), _Live(), read_key, 0.05, True)
        assert calls == ["s1"]  # wizard invoked with the latest session
        # The wizard's message surfaces as a duck toast — the loop feeds the
        # shared voice rather than stamping a body row or a panel attr.
        line = _duck_voice.duck_voice().tick()
        assert line is not None and line[0] == "Schedule saved."

    def test_no_session_shows_hint_instead_of_wizard(self, tmp_path, monkeypatch):
        import yeaboi.ui.mode_select as ms

        db = tmp_path / "sessions.db"
        monkeypatch.setattr(ms, "_ana_dbp", db)
        monkeypatch.setattr(
            ms,
            "_run_standup_schedule_wizard",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("wizard must not run without a session")),
        )

        class _Console:
            size = (120, 40)

            def print(self, *a, **k):
                pass

        rendered = []

        class _Live:
            def update(self, panel):
                rendered.append(panel)

        # Empty hub → indices: 0 "+ New standup", 1 schedule card.
        keys = iter(["down", "enter", "q"])

        def read_key(timeout=None):
            return next(keys, "q")

        ms._run_standup_hub(_Console(), _Live(), read_key, 0.05, True)
        line = _duck_voice.duck_voice().tick()
        assert line is not None and "No session yet" in line[0]  # the duck says it


class TestHubDeleteConfirm:
    """The delete confirmation is a sticky duck line owned by the hub loop."""

    def _run(self, tmp_path, monkeypatch, keys):
        import yeaboi.ui.mode_select as ms
        from yeaboi.agent.state import StandupReport
        from yeaboi.standup.store import StandupStore

        db = tmp_path / "sessions.db"
        with StandupStore(db) as store:
            store.record_run(StandupReport(date="2026-07-01", session_id="s1", team_summary="ok"))
        monkeypatch.setattr(ms, "_ana_dbp", db)

        class _Console:
            size = (120, 40)

            def print(self, *a, **k):
                pass

        class _Live:
            def update(self, *a, **k):
                pass

        it = iter(keys)

        def read_key(timeout=None):
            return next(it, "q")

        ms._run_standup_hub(_Console(), _Live(), read_key, 0.05, True)

    def test_delete_focus_enter_raises_a_sticky_confirmation(self, tmp_path, monkeypatch):
        voice = _duck_voice.duck_voice()
        seen = []
        real = voice.say_sticky
        monkeypatch.setattr(voice, "say_sticky", lambda text, **kw: seen.append(text) or real(text, **kw))
        # Focus the Delete button on the run row, raise the confirm, cancel it.
        self._run(tmp_path, monkeypatch, ["right", "enter", "esc", "q"])
        assert seen and seen[0].startswith('Delete "')
        assert "Enter to confirm" in seen[0]
        assert voice.tick() is None  # Esc cleared the sticky line

    def test_confirming_delete_speaks_the_toast(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch, ["right", "enter", "enter", "q"])
        voice = _duck_voice.duck_voice()
        line = voice.tick()
        assert line is not None and line[0] == "Run deleted."
        assert not voice.sticky


class TestHubSchedulePrefersEnabledSession:
    def test_wizard_targets_session_with_enabled_schedule(self, tmp_path, monkeypatch):
        # A schedule enabled for an OLDER session must stay visible/editable —
        # the hub wizard targets it instead of the bare latest session (which
        # would silently create a second schedule).
        import yeaboi.ui.mode_select as ms
        from yeaboi.sessions import SessionStore
        from yeaboi.standup.store import StandupStore

        db = tmp_path / "sessions.db"
        with SessionStore(db) as sstore:
            sstore.create_session("old-sess", "Old")
            sstore.create_session("new-sess", "New")  # latest
        with StandupStore(db) as store:
            store.save_config("old-sess", enabled=True, time="09:30", weekdays="1-5", delivery_channels=["terminal"])
        monkeypatch.setattr(ms, "_ana_dbp", db)

        calls = []
        monkeypatch.setattr(
            ms,
            "_run_standup_schedule_wizard",
            lambda console, live, rk, ft, st, session_id: calls.append(session_id) or "ok",
        )

        class _Console:
            size = (120, 40)

            def print(self, *a, **k):
                pass

        class _Live:
            def update(self, panel):
                pass

        # Empty run list → indices: 0 "+ New standup", 1 schedule card.
        keys = iter(["down", "enter", "q"])
        ms._run_standup_hub(_Console(), _Live(), lambda timeout=None: next(keys, "q"), 0.05, True)
        assert calls == ["old-sess"]
