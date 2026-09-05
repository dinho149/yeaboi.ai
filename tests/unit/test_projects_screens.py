"""Render tests for the Projects pages (_screens_projects.py).

Rendered at the app's enforced minimum terminal size (84x40), never the
builder's own default — that hides exactly the crowding a real user would hit.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from yeaboi.projects.flow import AGENTS_FLOW_LINE, FLOW
from yeaboi.sessions_recent import RecentSession
from yeaboi.ui.mode_select.screens._screens_projects import (
    ACTIONS,
    DRAFT_ACTIONS,
    EMPTY_LINE,
    PROJECT_ACTIONS,
    _build_context_screen,
    _build_draft_screen,
    _build_project_screen,
    _build_project_sessions_screen,
    _build_projects_screen,
    _flow_strip,
    list_actions,
    ordered_projects,
)

_W, _H = 84, 40


def _to_text(panel, *, width: int = _W, height: int = _H) -> str:
    console = Console(file=io.StringIO(), width=width, height=height)
    console.print(panel)
    return console.file.getvalue()


def _render(*, width: int = _W, height: int = _H, **kwargs) -> str:
    return _to_text(_build_projects_screen(width=width, height=height, **kwargs), width=width, height=height)


def _project(**overrides) -> dict:
    base = {
        "project_id": "proj-11112222",
        "name": "Apollo",
        "description": "A moon shot with a small crew",
        "settings": {},
        "created_at": "2026-08-01T00:00:00+00:00",
        "last_active": "2026-08-30T00:00:00+00:00",
        "archived": False,
        "status": "active",
        "session_count": 3,
    }
    return {**base, **overrides}


def _run(mode="standup", title="Standup — 2026-09-01") -> RecentSession:
    return RecentSession("s1", "7", mode, title, "2026-09-01T00:00:00", "2026-09-01T00:00:00", "proj-11112222")


class TestEmptyState:
    def test_invites_a_description_rather_than_a_terminal_command(self):
        out = _render(projects=[])
        assert EMPTY_LINE in out
        assert "yeaboi project create" not in out

    def test_still_draws_its_buttons(self):
        out = _render(projects=[])
        assert "New" in out and "Back" in out


class TestTheFlowStrip:
    def test_every_step_and_its_fragment_are_on_the_list_page(self):
        out = _render(projects=[])
        for step in FLOW:
            assert step.label in out
            assert step.leaves in out

    def test_the_labels_sit_on_one_rule(self):
        out = _render(projects=[])
        line = next(ln for ln in out.splitlines() if "● Plan" in ln)
        assert "● Report" in line and "─" in line

    def test_solo_has_no_retro_or_poker(self):
        out = _render(projects=[], world="solo")
        assert "● Retro" not in out and "● Poker" not in out and "● Plan" in out

    def test_agents_states_its_own_fact(self):
        out = _render(projects=[], world="agents")
        assert AGENTS_FLOW_LINE in out and "● Plan" not in out

    def test_never_wider_than_the_frame(self):
        for width in (84, 100, 140):
            for line in _flow_strip(world="team", width=width):
                assert len(line.plain) <= width - 6, (width, line.plain)

    def test_short_facts_sit_under_their_labels(self):
        inside = {step.key: "done" for step in FLOW}
        lines = _flow_strip(world="team", width=_W, inside=inside)
        assert len(lines) == 2 and lines[1].plain.count("done") == len(FLOW)

    def test_long_fragments_get_a_line_each(self):
        assert len(_flow_strip(world="team", width=_W)) == 1 + len(FLOW)


class TestTheRow:
    def test_shows_name_description_and_date(self):
        out = _render(projects=[_project()])
        assert "Apollo" in out and "A moon shot" in out and "2026-08-30" in out

    def test_the_active_project_wears_the_marker(self):
        out = _render(projects=[_project()], active_project_id="proj-11112222")
        assert "● Apollo" in out

    def test_in_progress_and_completed_are_two_sections(self):
        projects = ordered_projects(
            [_project(name="Done one", status="done"), _project(project_id="proj-2", name="Live")]
        )
        out = _render(projects=projects)
        assert out.index("In progress") < out.index("Live") < out.index("Completed") < out.index("Done one")

    def test_the_completed_heading_hides_when_nothing_is_done(self):
        assert "Completed" not in _render(projects=[_project()])

    def test_the_subtitle_is_the_doors_promise(self):
        assert "reads what the others left behind" in _render(projects=[_project()], sub_reveal=999)


class TestActions:
    def test_a_done_project_offers_reopen(self):
        assert list_actions(_project(status="done")) == ["Open", "New", "Reopen", "Archive", "Back"]
        assert list_actions(_project()) == ACTIONS
        assert list_actions(None) == ACTIONS

    def test_ordered_projects_puts_done_last(self):
        rows = ordered_projects([_project(name="B", status="done"), _project(name="A"), _project(name="C")])
        assert [r["name"] for r in rows] == ["A", "C", "B"]


class TestTheListDoesNotCropTheButtons:
    """The row block is flattened to one Text per line, so the viewport math holds."""

    @staticmethod
    def _many(n: int) -> list[dict]:
        return [
            _project(project_id=f"proj-{i:08d}", name=f"Project {i}", status="done" if i % 3 else "active")
            for i in range(n)
        ]

    @pytest.mark.parametrize("count", [1, 3, 6, 12, 40])
    def test_actions_survive_any_project_count(self, count):
        out = _render(projects=ordered_projects(self._many(count)))
        assert "Open" in out and "New" in out
        assert "Back" in out

    def test_a_long_list_can_actually_scroll(self):
        meta: dict = {}
        _render(projects=ordered_projects(self._many(40)), scroll_meta=meta, height=24)
        assert meta["max_offset"] > 0, "the page publishes no scroll room, so rows past the fold are unreachable"

    def test_rows_never_wrap_past_the_frame(self):
        out = _render(projects=[_project(name="A" * 60, description="B" * 120)])
        assert all(len(line) <= _W for line in out.splitlines())
        assert "Open" in out and "Back" in out


class TestProjectPage:
    def test_name_status_description_and_runs(self):
        out = _to_text(_build_project_screen(_project(), [_run()], width=_W, height=_H))
        assert "Apollo" in out and "In progress" in out and "A moon shot" in out
        assert "Standup — 2026-09-01" in out
        for label in PROJECT_ACTIONS:
            assert label in out

    def test_a_done_project_says_completed(self):
        assert "Completed" in _to_text(_build_project_screen(_project(status="done"), [], width=_W, height=_H))

    def test_inside_facts_replace_the_fragments(self):
        inside = {"project-planning": "done", "daily-standup": "2 runs", "retro": "not yet"}
        out = _to_text(_build_project_screen(_project(), [], inside=inside, width=_W, height=_H))
        assert "done" in out and "2 runs" in out and "not yet" in out

    def test_an_empty_project_invites_a_start(self):
        out = _to_text(_build_project_screen(_project(description=""), [], width=_W, height=_H))
        assert "Nothing has run inside it yet" in out and "No description yet" in out

    def test_the_key_hints_are_there(self):
        out = _to_text(_build_project_screen(_project(), [], width=_W, height=_H))
        assert "done / reopen" in out and "archive" in out

    def test_a_long_description_is_cut_to_three_lines(self):
        out = _to_text(_build_project_screen(_project(description="word " * 200), [], width=_W, height=_H))
        assert "…" in out and "Start" in out


class TestDraftPage:
    def test_shows_the_name_the_pitch_and_the_note(self):
        draft = {"name": "duck pond", "description": "A pond for ducks.", "source": "ai", "note": "AI rewrote it."}
        out = _to_text(_build_draft_screen(draft, width=_W, height=_H, sub_reveal=999))
        assert "New project" in out and "duck pond" in out and "A pond for ducks." in out and "AI rewrote it." in out
        for label in DRAFT_ACTIONS:
            assert label in out

    def test_nothing_wraps_past_the_frame(self):
        draft = {"name": "x", "description": "y " * 300, "source": "original", "note": "Named from your first words."}
        out = _to_text(_build_draft_screen(draft, width=_W, height=_H))
        assert all(len(line) <= _W for line in out.splitlines()) and "Create" in out


class TestContextPage:
    def test_inherit_marks_every_source_on(self):
        out = _to_text(_build_context_screen(None, width=_W, height=_H))
        assert out.count("●") == 5 and "Inheriting" in out

    def test_incognito_marks_every_source_off(self):
        out = _to_text(_build_context_screen((), width=_W, height=_H))
        assert out.count("○") == 5 and "Incognito" in out


class TestSessionsPage:
    def test_lists_rows_and_the_hint(self):
        out = _to_text(_build_project_sessions_screen([_run()], project_name="Apollo", width=_W, height=_H))
        assert "Standup — 2026-09-01" in out and "Enter opens the run's hub" in out

    def test_empty_says_so(self):
        out = _to_text(_build_project_sessions_screen([], width=_W, height=_H))
        assert "Nothing has run inside this project yet" in out
