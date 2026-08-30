"""Render tests for the Projects page (_screens_projects.py).

Rendered at the app's enforced minimum terminal size (84x40), never the
builder's own default — that hides exactly the crowding a real user would hit.
"""

from __future__ import annotations

import io

from rich.console import Console

from yeaboi.ui.mode_select.screens._screens_projects import _build_projects_screen

_W, _H = 84, 40


def _render(**kwargs) -> str:
    panel = _build_projects_screen(width=_W, height=_H, **kwargs)
    console = Console(file=io.StringIO(), width=_W, height=_H)
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
