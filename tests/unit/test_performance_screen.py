"""Render tests for the Performance TUI screen builder."""

import io

from rich.console import Console
from rich.panel import Panel

from yeaboi.beta import BETA_LABEL
from yeaboi.ui.mode_select.screens._screens import _MIN_WIDTH
from yeaboi.ui.mode_select.screens._screens_secondary import _build_performance_screen
from yeaboi.ui.shared._components import TITLE_ROWS


def _render(panel: Panel) -> str:
    console = Console(file=io.StringIO(), width=100)
    console.print(panel)
    return console.file.getvalue()


class TestBuildPerformanceScreen:
    def test_roster_view_renders_ascii_and_hint(self):
        # Engineer names render as big ASCII art (like the intake mode picker), so
        # the literal name text is NOT present — assert the panel builds and the
        # selected engineer's hint (rendered as plain text) shows.
        data = {
            "session_name": "Demo",
            "view": "roster",
            "roster": ["Ada Lovelace", "Alan Turing"],
            "roster_hints": ["2 open 1:1 actions", "no open 1:1 actions"],
            "selected_idx": 0,
            "actions": ["1:1 Prep", "1:1 Complete", "6mo Review", "Notes", "Export", "Back"],
        }
        # desc_reveal > 0 reveals the selected engineer's description (typewriter).
        panel = _build_performance_screen(data, width=120, height=40, action_sel=0, desc_reveal=100.0)
        assert isinstance(panel, Panel)
        out = _render(panel)
        assert "2 open 1:1 actions" in out  # selected engineer's description
        assert "Enter open" in out  # key hints, the roster's only guidance

    def test_roster_view_has_no_action_buttons(self):
        # Choosing a person is the whole job here. Buttons would give the view a
        # second focus and a second axis of movement, which is what the actions
        # view exists to take over.
        data = {
            "view": "roster",
            "roster": ["Ada Lovelace"],
            "roster_hints": ["2 open 1:1 actions"],
            "selected_idx": 0,
            "actions": ["1:1 Prep", "1:1 Complete", "6mo Review", "Notes", "History", "Export"],
        }
        out = _render(_build_performance_screen(data, width=120, height=40))
        assert "1:1 Prep" not in out
        # A button row carries several "\u256d\u2500\u2500\u256e" runs; the page's own frame has
        # exactly one corner per line.
        assert not [line for line in out.splitlines() if line.count("\u256d") > 1]

    def test_roster_windows_large_roster(self):
        # A long roster must not crash; ▼ marker shows there are more below.
        roster = [f"Person {i}" for i in range(20)]
        data = {"view": "roster", "roster": roster, "selected_idx": 0}
        # Build at the same width _render() uses (100) so the header's width-aware
        # shadow/compact choice matches how it's rendered.
        panel = _build_performance_screen(data, width=100, height=30)
        assert isinstance(panel, Panel)
        out = _render(panel)
        assert "more" in out  # ▲/▼ overflow indicator

    def test_empty_roster_shows_hint(self):
        data = {"session_name": "Demo", "view": "roster", "roster": [], "selected_idx": 0}
        panel = _build_performance_screen(data, width=100, height=32)
        assert isinstance(panel, Panel)
        out = _render(panel)
        assert "No engineers" in out

    def test_detail_view_renders_the_artifact(self):
        from yeaboi.agent.state import OneOnOnePrep

        data = {
            "view": "detail",
            "detail_title": "1:1 Prep — Ada",
            "artifact": OneOnOnePrep(engineer="Ada", date="2026-08-23", talking_points=("one", "two")),
            "kind": "prep",
            "actions": ["Export", "Back"],
        }
        panel = _build_performance_screen(data, width=100, height=32, action_sel=1)
        assert isinstance(panel, Panel)
        out = _render(panel)
        assert "Talking points" in out and "one" in out

    def test_detail_scrolls_without_error(self):
        from yeaboi.agent.state import OneOnOnePrep

        prep = OneOnOnePrep(engineer="Ada", talking_points=tuple(f"point {i}" for i in range(100)))
        data = {
            "view": "detail",
            "detail_title": "x",
            "artifact": prep,
            "kind": "prep",
            "actions": ["Export", "Back"],
        }
        panel = _build_performance_screen(data, width=100, height=20, scroll_offset=40)
        assert isinstance(panel, Panel)

    def test_a_detail_view_with_no_artifact_says_so(self):
        data = {"view": "detail", "detail_title": "x", "artifact": None, "kind": "", "actions": ["Back"]}
        assert "Nothing to show" in _render(_build_performance_screen(data, width=100, height=32))


class TestActionsView:
    """Picking a person opens their own page — one focus, one axis of movement."""

    ACTIONS = ["1:1 Prep", "1:1 Complete", "6mo Review", "Notes", "History", "Export"]

    def _data(self, **over):
        data = {
            "session_name": "Demo",
            "view": "actions",
            "roster": ["Ada Lovelace", "Alan Turing"],
            "roster_hints": ["2 open 1:1 actions", "no open 1:1 actions"],
            "selected_idx": 1,
            "actions": self.ACTIONS,
        }
        data.update(over)
        return data

    def test_shows_the_chosen_engineers_hint_and_the_action_buttons(self):
        out = _render(_build_performance_screen(self._data(), width=120, height=40, action_sel=0, desc_reveal=100.0))
        assert "no open 1:1 actions" in out  # the engineer at selected_idx, not the first
        for label in self.ACTIONS:
            assert label in out

    def test_describes_the_focused_action(self):
        prep = _render(_build_performance_screen(self._data(), width=120, height=40, action_sel=0))
        review = _render(_build_performance_screen(self._data(), width=120, height=40, action_sel=2))
        assert "next 1:1" in prep
        assert "six-month review" in review
        assert "next 1:1" not in review

    def test_message_renders_over_the_engineer(self):
        data = self._data(message="Generating 1:1 prep for Alan Turing\u2026")
        out = _render(_build_performance_screen(data, width=120, height=40))
        assert "Generating 1:1 prep" in out

    def test_empty_roster_does_not_crash(self):
        # Unreachable through the page loop, but the builder must never raise.
        panel = _build_performance_screen(self._data(roster=[], roster_hints=[]), width=100, height=30)
        assert isinstance(panel, Panel)

    def test_header_carries_the_beta_chip(self):
        assert BETA_LABEL in _render(_build_performance_screen(self._data(), width=100, height=30))


class TestBetaChip:
    """The mode is usable but unverified — the header says so on every view."""

    def test_roster_header_carries_the_beta_chip(self):
        data = {"session_name": "Demo", "view": "roster", "roster": ["Ada Lovelace"], "selected_idx": 0}
        out = _render(_build_performance_screen(data, width=100, height=30))
        assert BETA_LABEL in out

    def test_detail_header_carries_the_beta_chip(self):
        data = {
            "view": "detail",
            "detail_title": "1:1 Prep — Ada",
            "detail_lines": ["Talking points:", "  • one"],
            "actions": ["Export", "Back"],
        }
        out = _render(_build_performance_screen(data, width=100, height=32))
        assert BETA_LABEL in out

    def test_header_stays_two_rows_and_keeps_the_chip_at_min_width(self):
        # The chip is appended to the wordmark, so the narrowest supported
        # terminal is where it would wrap and silently push every viewport row
        # down by one. Asserted at _MIN_WIDTH rather than something arbitrarily
        # narrow: below ~64 columns the chip is cropped off entirely, so a
        # narrower assertion would still pass if the chip were never appended.
        # An empty roster isolates the header — engineer names use block font too.
        data = {"view": "roster", "roster": [], "selected_idx": 0}
        panel = _build_performance_screen(data, width=_MIN_WIDTH, height=30)
        console = Console(file=io.StringIO(), width=_MIN_WIDTH)
        console.print(panel)
        out = console.file.getvalue()

        glyph_rows = [line for line in out.splitlines() if any(ch in line for ch in "█▀▄")]
        assert len(glyph_rows) == TITLE_ROWS
        assert BETA_LABEL in out


class TestDetailViewportGeometry:
    """The three things that used to go wrong silently on this view."""

    @staticmethod
    def _prep():
        from yeaboi.agent.state import OneOnOnePrep

        return OneOnOnePrep(
            engineer="Ada",
            date="2026-08-23",
            talking_points=tuple(
                f"A talking point long enough to wrap on a narrow terminal, number {i}." for i in range(40)
            ),
            warnings=("Only one sprint of history — treat trends as provisional",),
        )

    @staticmethod
    def _data(**extra):
        return {
            "view": "detail",
            "detail_title": "1:1 Prep — Ada",
            "kind": "prep",
            "actions": ["Export", "Share Online", "Anonymize"],
            **extra,
        }

    def _render_at(self, *, width, height, scroll=0, message="", actions=None):
        data = self._data(artifact=self._prep(), message=message)
        if actions is not None:
            data["actions"] = actions
        return _render(
            _build_performance_screen(data, width=width, height=height, scroll_offset=scroll),
        )

    def test_the_tail_of_a_long_artifact_is_reachable(self):
        # Rows wrapped at render time while the scroll math counted them as one,
        # so max_scroll under-counted and the last section could not be reached.
        out = self._render_at(width=100, height=30, scroll=999)
        assert "Notices" in out
        assert "treat trends as provisional" in out

    def test_the_status_banner_survives_a_scroll_to_the_bottom(self):
        # It used to be the first row *inside* the viewport, so scrolling lost it.
        out = self._render_at(width=100, height=30, scroll=999, message="Exported to ~/.yeaboi/exports.")
        assert "Exported to" in out

    @staticmethod
    def _panel_height(out: str) -> int:
        return len([ln for ln in out.splitlines() if ln.strip()])

    def test_a_short_terminal_drops_the_banner_and_keeps_the_buttons(self):
        # At 17 rows the banner and a full viewport cannot both fit. The banner
        # goes: the message repeats, the way out of the page does not. (Below 16
        # the button labels crop whatever we do — that floor predates this view.)
        out = self._render_at(width=100, height=17, message="Exported to ~/.yeaboi/exports.")
        assert "Export" in out
        assert "Exported to" not in out
        assert self._panel_height(out) == 17

    def test_one_more_row_is_enough_to_keep_both(self):
        out = self._render_at(width=100, height=18, message="Exported to ~/.yeaboi/exports.")
        assert "Export" in out
        assert "Exported to" in out

    def test_panel_height_is_exact_across_terminals_and_button_counts(self):
        # Five detail buttons wrap to a second row on a narrow terminal; the
        # hardcoded action height this replaces pushed the bottom border off the
        # panel when they did.
        for height in (15, 18, 20, 24, 28, 40):
            for width in (60, 80, 100, 120):
                for actions in (["Export", "Back"], ["Export", "Share Online", "Adjust", "Revert", "Back"]):
                    data = {**self._data(artifact=self._prep(), message="working…"), "actions": actions}
                    console = Console(file=io.StringIO(), width=width)
                    console.print(_build_performance_screen(data, width=width, height=height, scroll_offset=3))
                    lines = [ln for ln in console.file.getvalue().splitlines() if ln.strip()]
                    assert len(lines) == height, (width, height, len(actions))
                    assert lines[-1].lstrip().startswith("╰"), (width, height, len(actions))


class TestRosterDensity:
    """The big-ASCII row is borrowed from a picker with eight fixed entries.

    At three lines each it fits two engineers on a 24-row terminal, so a
    ten-person team was five screens of paging.
    """

    NAMES = [
        "Ada Lovelace",
        "Bob Jones",
        "Carla Diaz",
        "Dan Okafor",
        "Eve Nakamura",
        "Frank Li",
        "Grace Hopper",
        "Hana Suzuki",
        "Ivan Petrov",
        "Jo Kim",
    ]

    def _render_roster(self, names, *, height=24, selected=0, width=100):
        hints = [f"{i} open 1:1 actions" for i in range(len(names))]
        data = {
            "view": "roster",
            "roster": list(names),
            "roster_hints": hints,
            "selected_idx": selected,
            "session_name": "Team",
            "actions": [],
        }
        return _render(_build_performance_screen(data, width=width, height=height, sub_reveal=999.0, desc_reveal=999.0))

    def test_a_ten_person_team_is_one_screen_not_five(self):
        out = self._render_roster(self.NAMES)
        visible = [name for name in self.NAMES if name in out]
        assert len(visible) >= 8

    def test_a_small_team_keeps_the_big_ascii_rows(self):
        # Its whole point is that a roster of four reads as four faces.
        out = self._render_roster(self.NAMES[:4], height=40)
        assert "█" in out

    def test_a_short_terminal_switches_to_the_compact_list_whatever_the_roster_size(self):
        out = self._render_roster(self.NAMES[:4], height=20)
        assert "█▀█ █▀▀ █▀█" in out  # the PERFORMANCE title itself still renders
        assert all(name in out for name in self.NAMES[:4])

    def test_the_selected_engineer_carries_the_caret(self):
        out = self._render_roster(self.NAMES, selected=3)
        assert "▸ Dan Okafor" in out

    def test_the_window_follows_the_selection_and_counts_what_it_hides(self):
        out = self._render_roster(self.NAMES, selected=9)
        assert "Jo Kim" in out
        assert "▲" in out

    def test_hints_line_up_in_one_column(self):
        # Ragged hints are what the eye cannot run down.
        out = self._render_roster(["Al", "Bartholomew Fitzgerald"])
        rows = [ln for ln in out.splitlines() if "open 1:1 actions" in ln]
        assert len({ln.index("open 1:1 actions") for ln in rows}) == 1

    def test_panel_height_stays_exact_for_a_long_roster(self):
        for height in (18, 24, 30, 40):
            out = self._render_roster(self.NAMES, height=height)
            lines = [ln for ln in out.splitlines() if ln.strip()]
            assert len(lines) == height, height


class TestPerformanceLoadingChecklist:
    """The generate screen: declared phases visible from the first frame."""

    def _render_progress(self, progress, phases, **kw):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_standup_progress_screen
        from yeaboi.ui.shared._components import PERFORMANCE_THEME, performance_title

        panel = _build_standup_progress_screen(
            progress,
            width=100,
            height=34,
            elapsed=21.0,
            anim_tick=21.0,
            theme=PERFORMANCE_THEME,
            title=performance_title(width=100),
            label="1:1 Prep — Ada",
            phases=phases,
            **kw,
        )
        return _render(panel)

    def _event(self, component_id, status, *, label="X", detail=""):
        from yeaboi.analysis.progress import append_component_progress

        events: list = []
        append_component_progress(events, component_id=component_id, label=label, status=status, detail=detail)
        return events[0]

    def test_an_empty_run_still_shows_the_whole_shape(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import PERF_PREP_PHASES

        out = self._render_progress([], PERF_PREP_PHASES)
        for _pid, label in PERF_PREP_PHASES:
            assert f"○ {label}" in out, f"{label} was not shown as pending"

    def test_a_settled_phase_swaps_its_pending_dot_for_its_mark(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import PERF_PREP_PHASES

        out = self._render_progress(
            [self._event("tickets", "completed", detail="42 ticket(s) from jira")], PERF_PREP_PHASES
        )
        assert "✓ Gather tickets · 42 ticket(s) from jira" in out
        assert "○ Gather tickets" not in out
        assert "○ Ask the model" in out  # the rest still pending

    def test_a_row_keeps_the_declared_wording_before_and_after_it_runs(self):
        # The engine labels this event "Tickets"; the checklist says "Gather
        # tickets". One row must not rename itself the moment it starts.
        from yeaboi.ui.mode_select.screens._screens_secondary import PERF_PREP_PHASES

        out = self._render_progress([self._event("tickets", "running", label="Tickets")], PERF_PREP_PHASES)
        assert "Gather tickets" in out
        assert "Tickets ·" not in out

    def test_the_footer_counts_only_settled_phases(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import PERF_PREP_PHASES

        out = self._render_progress(
            [self._event("tickets", "completed"), self._event("standup", "no_data")], PERF_PREP_PHASES
        )
        assert "[2/8]" in out

    def test_an_undeclared_phase_still_renders(self):
        # The optional live scan is not on the checklist; it must not vanish.
        from yeaboi.ui.mode_select.screens._screens_secondary import PERF_PREP_PHASES

        out = self._render_progress(
            [self._event("gap_scan", "completed", label="Live scan", detail="7 item(s)")], PERF_PREP_PHASES
        )
        assert "Live scan" in out

    def test_no_phases_is_unchanged_behaviour(self):
        # Every existing caller passes nothing; only what happened shows.
        out = self._render_progress([self._event("tickets", "completed", label="Tickets")], ())
        assert "✓ Tickets" in out
        assert "○" not in out


class TestPhaseIdsMatchWhatTheEnginesEmit:
    """A renamed id would show a permanently pending row and a total never reached.

    The checklists spell their component ids as literals; these are the two-way
    equality that keeps them tied to the constants the emitters actually use.
    """

    def test_the_evidence_phases_are_exactly_the_evidence_sources_that_emit(self):
        from yeaboi.performance import evidence
        from yeaboi.ui.mode_select.screens._screens_secondary import _PERF_EVIDENCE_PHASES

        declared = {pid for pid, _ in _PERF_EVIDENCE_PHASES}
        # Code and documentation are sub-categories of standup; they carry a
        # coverage row but no phase of their own.
        emitting = set(evidence.EVIDENCE_SOURCES) - {evidence.SOURCE_CODE, evidence.SOURCE_DOCUMENTATION}
        assert declared == emitting

    def test_the_engine_phases_are_exactly_the_engine_phase_constants(self):
        from yeaboi.performance import engine
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            PERF_COMPLETE_PHASES,
            PERF_PREP_PHASES,
        )

        declared = {pid for pid, _ in PERF_PREP_PHASES + PERF_COMPLETE_PHASES}
        engine_ids = {engine.PHASE_MODEL, engine.PHASE_SAVE, engine.PHASE_PRIOR, engine.PHASE_EMAIL}
        assert engine_ids <= declared, "an engine phase nothing declares renders as an unknown row"

    def test_the_review_declares_the_context_phase_it_emits(self):
        from yeaboi.performance import engine
        from yeaboi.ui.mode_select.screens._screens_secondary import PERF_REVIEW_PHASES

        assert engine.PHASE_CONTEXT in {pid for pid, _ in PERF_REVIEW_PHASES}


class TestTheRunningRowSurvivesAShortTerminal:
    """A checklist taller than the viewport must not hide the row that is moving."""

    def _rows(self, progress, height):
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            PERF_PREP_PHASES,
            _build_standup_progress_screen,
        )
        from yeaboi.ui.shared._components import PERFORMANCE_THEME, performance_title

        panel = _build_standup_progress_screen(
            progress,
            width=100,
            height=height,
            elapsed=3.0,
            anim_tick=3.0,
            theme=PERFORMANCE_THEME,
            title=performance_title(width=100),
            label="1:1 Prep",
            phases=PERF_PREP_PHASES,
        )
        return _render(panel)

    def _started(self, done, running):
        from yeaboi.analysis.progress import append_component_progress

        events: list = []
        for cid in done:
            append_component_progress(events, component_id=cid, label=cid, status="completed")
        append_component_progress(events, component_id=running, label=running, status="running")
        return events

    def test_the_first_phase_is_visible_before_anything_finishes(self):
        # A 24-row terminal renders the page at height 23; eight phases plus a
        # footer do not fit, and a plain tail slice drops the one that is running.
        out = self._rows(self._started((), "tickets"), 23)
        assert "Gather tickets" in out

    def test_the_running_row_is_visible_at_every_height(self):
        progress = self._started(("tickets", "standup"), "analysis")
        for height in (16, 18, 20, 23, 30, 40):
            assert "Read team analysis" in self._rows(progress, height), f"hidden at height {height}"

    def test_a_late_phase_is_visible_once_it_is_the_one_running(self):
        done = ("tickets", "standup", "analysis", "retro", "poker", "delivery", "model")
        assert "Save & export" in self._rows(self._started(done, "save"), 23)

    def test_the_footer_counts_the_whole_checklist_not_the_window(self):
        out = self._rows(self._started(("tickets", "standup"), "analysis"), 23)
        assert "[2/8]" in out
