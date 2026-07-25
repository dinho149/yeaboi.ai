"""Render tests for the saved-runs hub.

The hub is the standup/retro/reporting/performance landing that lists past runs with
Open/Delete/Export — the list render (`_build_run_hub_screen`) must not crash at any size
or state (populated list, empty state, focused row with buttons, delete popup). Opening a
saved run renders it through the mode's OWN rich builder (not a flat text view), so
`TestSnapshotRendering` checks each mode's snapshot data shape drives its themed screen.
"""

import io

from rich.console import Console
from rich.panel import Panel

from yeaboi.ui.mode_select.screens._project_cards import RunSummary
from yeaboi.ui.mode_select.screens._run_hub_screen import _build_run_hub_screen
from yeaboi.ui.shared._components import reporting_title, standup_title


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

    def test_delete_popup_renders(self):
        panel = _build_run_hub_screen(
            _runs(), 2, title_fn=standup_title, delete_popup_name="Standup — 2026-07-03", delete_popup_t=1.0
        )
        out = _text(panel)
        assert "Delete" in out and "Enter to confirm" in out

    def test_small_terminal_does_not_crash(self):
        # Just needs to render without raising at a cramped size.
        _text(_build_run_hub_screen(_runs(8), 5, title_fn=reporting_title), width=60, height=16)


_SNAP_ACTIONS = ["Export", "Delete", "Run again", "Back"]


class TestSnapshotRendering:
    """Opening a saved run feeds the report into the mode's real rich screen builder.

    These mirror the data shape each ``make_detail`` / ``open_snapshot`` in
    ``mode_select/__init__`` passes, so the snapshot looks like the live screen
    (themed, meters/grids/cards) rather than flat grey markdown.
    """

    def test_reporting_detail_renders_rich(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_reporting_screen

        panel = _build_reporting_screen(
            {
                "view": "detail",
                "detail_lines": ["Executive summary:", "• shipped auth"],
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

    def test_performance_detail_renders_rich(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_performance_screen

        panel = _build_performance_screen(
            {
                "view": "detail",
                "detail_lines": ["Strengths:", "• ownership"],
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
        assert "Join this retro" not in out  # live-only join block suppressed for a saved run

    def test_retro_live_still_shows_join(self):
        # Guard sanity: without the snapshot flag the live board still renders the join block.
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_retro_screen

        panel = _build_retro_screen(
            {"grids": {}, "display_code": "ABC123", "actions": ["Generate Action Items", "Export", "Close"]},
            action_sel=0,
            width=100,
            height=40,
        )
        assert "Join this retro" in _text(panel)

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
        assert "Join this session" not in out  # live-only join block suppressed for a saved run

    def test_poker_live_still_shows_join(self):
        from yeaboi.poker.board import PokerBoard
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_poker_screen

        board = PokerBoard("s", tickets=[{"key": "T-1", "summary": "X"}])
        panel = _build_poker_screen(
            {
                "state": board.state_snapshot(),
                "display_code": "ABC123",
                "actions": ["Share Remotely", "Export", "Close"],
            },
            action_sel=0,
            width=100,
            height=40,
        )
        assert "Join this session" in _text(panel)

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
