"""Tests for the Performance TUI page loop (ui/mode_select/__init__.py).

The page has three views and the loop's job is to keep exactly one thing
movable in each: the roster chooses a person, the actions view chooses what to
do to them, the detail view scrolls an artifact. These drive the loop with a
scripted ``read_key`` and a fake Live — no terminal, no tracker, no LLM.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from yeaboi.ui import mode_select
from yeaboi.ui.mode_select.screens import _screens_secondary


class _Live:
    def __init__(self):
        self.frames: list = []

    def update(self, renderable):
        self.frames.append(renderable)


class _Console:
    size = (100, 40)


def _keys(*sequence):
    """A read_key that plays a script, then answers ``esc`` forever."""
    remaining = list(sequence)

    def _read(timeout=None):
        if timeout == 0.0:
            return ""  # drain: nothing buffered
        return remaining.pop(0) if remaining else "esc"

    return _read


class _Frames(list):
    """Every rendered frame as (view, selected_idx, focused action).

    A list so the tests can say ``in`` and index it; ``messages`` rides along for
    the few assertions that care what the page told the user.
    """

    def __init__(self):
        super().__init__()
        self.messages: list[str] = []


@pytest.fixture()
def views(monkeypatch):
    """Record what every rendered frame was showing."""
    seen = _Frames()

    def _fake_screen(data, *, action_sel=0, **_kw):
        actions = data.get("actions") or []
        focused = actions[action_sel] if 0 <= action_sel < len(actions) else ""
        seen.append((data.get("view", ""), data.get("selected_idx", 0), focused))
        seen.messages.append(data.get("message", ""))
        return "panel"

    monkeypatch.setattr(_screens_secondary, "_build_performance_screen", _fake_screen)
    return seen


@pytest.fixture()
def roster(monkeypatch):
    """A two-person roster, with no session and no store behind it."""
    monkeypatch.setattr(
        mode_select,
        "_collect_performance_data",
        lambda message="": {
            "message": message,
            "session_id": "s-1",
            "session_name": "Demo",
            "roster": ["Ada Lovelace", "Alan Turing"],
            "roster_hints": ["2 open 1:1 actions", "no open 1:1 actions"],
        },
    )
    monkeypatch.setattr(mode_select, "_performance_roster_hints", lambda names: ["" for _ in names])


def _run(keys):
    mode_select._run_performance_page(_Console(), _Live(), keys, 0.01, True)


class TestRosterToActions:
    def test_enter_opens_the_chosen_engineer(self, views, roster):
        # Down moves to Alan, Enter opens his actions view, Esc goes back, Esc exits.
        _run(_keys("down", "enter", "esc"))
        assert ("actions", 1, "1:1 Prep") in views
        # The roster is where Esc lands, and the loop only exits from there.
        assert views[-1][0] == "roster"

    def test_the_roster_ignores_left_and_right(self, views, roster):
        # There are no buttons on the roster any more; the arrows that used to
        # move between them must not move anything else either.
        _run(_keys("right", "right", "esc"))
        assert {v for v, _idx, _act in views} == {"roster"}
        assert {idx for _v, idx, _act in views} == {0}

    def test_an_empty_roster_says_so_instead_of_opening(self, views, monkeypatch):
        monkeypatch.setattr(
            mode_select,
            "_collect_performance_data",
            lambda message="": {"session_id": "", "session_name": "", "roster": [], "roster_hints": []},
        )
        _run(_keys("enter", "esc"))
        assert {v for v, _idx, _act in views} == {"roster"}
        assert any("No engineers" in m for m in views.messages)


class TestActionsView:
    def test_left_and_right_move_between_actions(self, views, roster):
        _run(_keys("enter", "right", "right", "esc", "esc"))
        focused = [act for view, _idx, act in views if view == "actions"]
        assert focused[0] == "1:1 Prep"
        assert focused[-1] == "6mo Review"

    def test_esc_returns_to_the_roster_without_leaving_the_page(self, views, roster):
        _run(_keys("enter", "esc", "enter", "esc", "esc"))
        assert [v for v, _i, _a in views].count("roster") >= 3
        assert ("actions", 0, "1:1 Prep") in views


class TestDetailReturnsToTheEngineer:
    def test_esc_out_of_an_artifact_lands_back_on_that_engineers_actions(self, views, roster, monkeypatch):
        from yeaboi.performance import engine, render

        monkeypatch.setattr(engine, "run_one_on_one_prep", lambda *a, **kw: object())
        monkeypatch.setattr(render, "format_prep_lines", lambda prep: ["Talking points:", "  • one"])

        # Enter opens Alan, Enter runs 1:1 Prep, Esc leaves the artifact.
        _run(_keys("down", "enter", "enter", "esc", "esc"))
        detail_at = [i for i, (v, _idx, _a) in enumerate(views) if v == "detail"]
        assert detail_at, "the prep never rendered a detail view"
        after = [v for v, _idx, _a in views[detail_at[-1] + 1 :]]
        # Back goes to the person you were reading about, not the whole team.
        assert after and after[0] == "actions"


class TestTheActionRowOnlyCreates:
    """Export left the pre-generation row — there is nothing to export yet."""

    def test_the_row_offers_no_export(self, views, roster):
        _run(_keys("enter", "esc", "esc"))
        offered = {act for view, _idx, act in views if view == "actions"}
        assert "Export" not in offered

    def test_right_stops_at_history(self, views, roster):
        _run(_keys("enter", *(["right"] * 8), "esc", "esc"))
        focused = [act for view, _idx, act in views if view == "actions"]
        assert focused[-1] == "History"


class TestTheLoadingScreen:
    """A generate paints the phase checklist, not the actions screen."""

    def test_generating_paints_the_progress_screen_with_its_phases(self, views, roster, monkeypatch):
        from yeaboi.performance import engine, render

        painted: list[dict] = []

        def _fake_progress_screen(progress, *, phases=(), label="", **_kw):
            painted.append({"progress": list(progress), "phases": phases, "label": label})
            return "progress-panel"

        monkeypatch.setattr(_screens_secondary, "_build_standup_progress_screen", _fake_progress_screen)
        monkeypatch.setattr(render, "format_prep_lines", lambda prep: ["Talking points:"])

        def _slow_prep(*_a, on_progress=None, **_kw):
            # One event, then hand back — the worker must have painted by now.
            if on_progress is not None:
                on_progress(
                    {"kind": "analysis_component", "component_id": "tickets", "label": "T", "status": "running"}
                )
            time.sleep(0.05)
            return object()

        monkeypatch.setattr(engine, "run_one_on_one_prep", _slow_prep)
        _run(_keys("enter", "enter", "esc", "esc"))

        assert painted, "the loading screen never rendered"
        assert painted[-1]["phases"] == _screens_secondary.PERF_PREP_PHASES
        assert "1:1 Prep" in painted[-1]["label"]

    def test_the_complete_flow_declares_its_own_phases(self, views, roster, monkeypatch):
        from yeaboi.performance import engine, render

        painted: list = []
        monkeypatch.setattr(
            _screens_secondary,
            "_build_standup_progress_screen",
            lambda progress, *, phases=(), **_kw: painted.append(phases) or "progress-panel",
        )
        monkeypatch.setattr(render, "format_completion_lines", lambda rec: ["Summary:"])
        monkeypatch.setattr(mode_select, "_performance_get_transcript", lambda *a, **kw: ("We talked about scope.", []))

        def _slow_complete(*_a, **_kw):
            time.sleep(0.05)
            return SimpleNamespace(warnings=())

        monkeypatch.setattr(engine, "complete_one_on_one", _slow_complete)
        _run(_keys("enter", "right", "enter", "esc", "esc"))

        assert painted and painted[-1] == _screens_secondary.PERF_COMPLETE_PHASES


class TestOutputActionsUseWhatIsOnScreen:
    """Export and Share Online act on the artifact shown, not the newest saved one."""

    def test_export_writes_the_artifact_on_screen(self, views, roster, monkeypatch):
        from yeaboi.performance import engine, render

        shown = SimpleNamespace(engineer="Ada Lovelace", marker="the-fresh-prep")
        monkeypatch.setattr(engine, "run_one_on_one_prep", lambda *a, **kw: shown)
        monkeypatch.setattr(render, "format_prep_lines", lambda prep: ["Talking points:"])

        exported: list = []

        def _fake_picker(*_a, files_export=None, **_kw):
            return files_export()

        monkeypatch.setattr(mode_select, "_export_via_picker", _fake_picker)
        monkeypatch.setattr(
            mode_select,
            "_performance_export",
            lambda artifact, *, engineer, kind: exported.append((artifact, engineer, kind)) or "written",
        )

        # Open Ada, run 1:1 Prep, then Enter on the detail view's first button (Export).
        _run(_keys("enter", "enter", "enter", "esc", "esc"))

        assert exported == [(shown, "Ada Lovelace", "prep")]

    def test_share_online_publishes_the_artifact_on_screen(self, views, roster, monkeypatch):
        from yeaboi.performance import engine, render

        shown = SimpleNamespace(engineer="Ada Lovelace", marker="the-fresh-prep")
        monkeypatch.setattr(engine, "run_one_on_one_prep", lambda *a, **kw: shown)
        monkeypatch.setattr(render, "format_prep_lines", lambda prep: ["Talking points:"])

        shared: list = []
        monkeypatch.setattr(
            mode_select, "_run_output_share_flow", lambda *a, document=None, **kw: shared.append(document) or 0
        )
        from yeaboi.sharing import documents

        monkeypatch.setattr(documents, "performance_document", lambda art, *, kind, anon=None: (art, kind))

        # Open Ada, run 1:1 Prep, Right onto Share Online, Enter.
        _run(_keys("enter", "enter", "right", "enter", "esc", "esc"))

        assert shared == [(shown, "prep")]


class TestTheExportHelpersOnTheirOwn:
    """The two artifact-taking helpers, called for real rather than patched away."""

    def test_a_prep_exports_to_markdown_and_html(self, tmp_path, monkeypatch):
        from yeaboi.agent.state import OneOnOnePrep

        written: list = []
        monkeypatch.setattr(
            "yeaboi.performance.export.export_artifact",
            lambda art, *, engineer, kind: (
                written.append((art, engineer, kind)) or {"markdown": tmp_path / "prep-2026-08-24.md"}
            ),
        )
        prep = OneOnOnePrep(engineer="Ada", date="2026-08-24")
        msg = mode_select._performance_export(prep, engineer="Ada", kind="prep")
        assert written == [(prep, "Ada", "prep")]
        assert "Markdown + HTML" in msg

    def test_a_note_says_why_it_cannot_be_a_file(self, tmp_path):
        # The hub renders notes; they have no exporter of their own.
        msg = mode_select._performance_export(object(), engineer="Ada", kind="note")
        assert "Copy/Publish" in msg

    def test_an_export_failure_is_reported_not_raised(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr("yeaboi.performance.export.export_artifact", _boom)
        msg = mode_select._performance_export(object(), engineer="Ada", kind="prep")
        assert msg.startswith("Export failed:")

    def test_the_document_titles_each_kind(self):
        from yeaboi.agent.state import OneOnOnePrep, SixMonthReview

        title, markdown = mode_select._performance_document(
            OneOnOnePrep(engineer="Ada", date="2026-08-24"), engineer="Ada", kind="prep"
        )
        assert title == "1:1 Prep — Ada"
        assert markdown

        title, _ = mode_select._performance_document(SixMonthReview(engineer="Ada"), engineer="Ada", kind="review")
        assert title == "6-Month Review — Ada"

    def test_an_unpublishable_kind_says_so_instead_of_raising(self):
        msg = mode_select._performance_document(object(), engineer="Ada", kind="note")
        assert isinstance(msg, str)
        assert "cannot be published" in msg
