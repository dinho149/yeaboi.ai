"""Render tests for the Projects page (_screens_projects.py).

Rendered at the app's enforced minimum terminal size (84x40), never the
builder's own default — that hides exactly the crowding a real user would hit.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from yeaboi.ui.mode_select.screens._screens_projects import _build_context_screen, _build_projects_screen

_W, _H = 84, 40


def _render(*, width: int = _W, height: int = _H, **kwargs) -> str:
    panel = _build_projects_screen(width=width, height=height, **kwargs)
    console = Console(file=io.StringIO(), width=width, height=height)
    console.print(panel)
    return console.file.getvalue()


def _project(**overrides) -> dict:
    base = {
        "project_id": "proj-11112222",
        "name": "Apollo",
        "description": "",
        "settings": {},
        "created_at": "2026-08-01T00:00:00+00:00",
        "last_active": "2026-08-30T00:00:00+00:00",
        "archived": False,
        "session_count": 3,
    }
    return {**base, **overrides}


class TestEmptyState:
    def test_points_at_the_create_command_rather_than_saying_none(self):
        out = _render(projects=[])
        assert "No projects yet" in out
        assert "yeaboi project create" in out

    def test_still_draws_its_buttons(self):
        assert "Back" in _render(projects=[])


class TestTheListDoesNotCropTheButtons:
    """The row block is flattened to one Text per line, so the viewport math holds.

    A single multi-row Table counted as one body entry while drawing N+1 lines,
    which pushed the action block off the bottom of the fixed-height Panel at
    three projects or more — on any terminal height.
    """

    @staticmethod
    def _many(n: int) -> list[dict]:
        return [_project(project_id=f"proj-{i:08d}", name=f"Project {i}") for i in range(n)]

    @pytest.mark.parametrize("count", [1, 3, 6, 12, 40])
    def test_actions_survive_any_project_count(self, count):
        out = _render(projects=self._many(count))
        assert "Set active" in out
        assert "Back" in out

    def test_a_long_list_can_actually_scroll(self):
        meta: dict = {}
        _render(projects=self._many(40), scroll_meta=meta, height=24)
        assert meta["max_offset"] > 0, "the page publishes no scroll room, so rows past the fold are unreachable"


class TestTheRow:
    def test_shows_name_id_and_session_count(self):
        out = _render(projects=[_project()])
        assert "Apollo" in out
        assert "proj-11112222" in out
        assert "3" in out

    def test_the_active_project_wears_the_marker(self):
        out = _render(projects=[_project()], active_project_id="proj-11112222")
        assert "●" in out
        assert "Scoped runs read context through" in out

    def test_no_active_project_says_runs_stay_teamwide(self):
        out = _render(projects=[_project()])
        assert "team-wide" in out

    def test_an_archived_row_says_so(self):
        out = _render(projects=[_project(archived=True)])
        assert "(archived)" in out


class TestMessage:
    def test_a_message_reaches_the_page(self):
        assert "Archived Apollo." in _render(projects=[_project()], message="Archived Apollo.")


def _render_context(deps, **kwargs) -> str:
    panel = _build_context_screen(deps, width=_W, height=_H, **kwargs)
    console = Console(file=io.StringIO(), width=_W, height=_H)
    console.print(panel)
    return console.file.getvalue()


class TestContextScreen:
    def test_inherit_shows_every_source_on(self):
        out = _render_context(None)
        assert "Retro history" in out
        assert "Analysis profile" in out
        assert "Inheriting" in out
        assert "○" not in out

    def test_a_disabled_source_wears_the_hollow_glyph(self):
        out = _render_context(("standup", "plan", "performance", "analysis"))
        assert "○" in out
        assert "Only the ● sources" in out

    def test_incognito_names_itself_and_keeps_sessions(self):
        out = _render_context(())
        assert "Incognito" in out
        assert "Sessions still persist" in out

    def test_draws_its_buttons(self):
        out = _render_context(None)
        assert "All on" in out
        assert "Back" in out

    def test_a_message_reaches_the_page(self):
        assert "saved" in _render_context(None, message="saved")
