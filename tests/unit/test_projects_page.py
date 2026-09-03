"""Tests for the Projects TUI page loop (ui/mode_select/_projects.py).

Driven with a scripted ``read_key`` and a fake Live, the same shape the other
page-loop tests use — no terminal, no threads of our own.
"""

from __future__ import annotations

import pytest

from yeaboi.projects import active
from yeaboi.projects.engine import create_project
from yeaboi.ui.mode_select import _projects


class _Live:
    def __init__(self):
        self.frames: list = []

    def update(self, renderable):
        self.frames.append(renderable)


class _Console:
    size = (84, 40)


def _keys(*sequence):
    remaining = list(sequence)

    def _read(timeout=None):
        if timeout == 0.0:
            return ""
        return remaining.pop(0) if remaining else "esc"

    return _read


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    active.set_active_project("")
    active.set_context_deps(None)
    active.set_solo_mode(False)
    yield {"db": db}
    active.set_active_project("")
    active.set_context_deps(None)
    active.set_solo_mode(False)


def _run(keys) -> _Live:
    live = _Live()
    _projects.run_projects_page(_Console(), live, keys, 0.05, True)
    return live


def _render(panel) -> str:
    import io

    from rich.console import Console

    console = Console(file=io.StringIO(), width=84, height=40)
    console.print(panel)
    return console.file.getvalue()


class TestClose:
    def test_esc_closes_the_page(self, env):
        live = _run(_keys("esc"))
        assert live.frames

    def test_the_back_button_closes_it(self, env):
        create_project("Apollo", db_path=env["db"])
        # Open → Sessions → Context → Archive → Back, then Enter.
        live = _run(_keys("right", "right", "right", "right", "enter"))
        assert "Apollo" in _render(live.frames[-1])

    def test_esc_returns_none(self, env):
        create_project("Apollo", db_path=env["db"])
        assert _projects.run_projects_page(_Console(), _Live(), _keys("esc"), 0.05, True, pick=True) is None
        assert active.get_active_project() == ""


class TestOpen:
    def test_enter_on_open_sets_the_project_and_returns_its_id(self, env):
        created = create_project("Apollo", db_path=env["db"])
        chosen = _projects.run_projects_page(_Console(), _Live(), _keys("enter"), 0.05, True, pick=True)
        assert chosen == created["project_id"]
        assert active.get_active_project() == created["project_id"]

    def test_down_moves_the_selection_so_the_second_project_can_be_opened(self, env):
        create_project("Apollo", db_path=env["db"])
        second = create_project("Borealis", db_path=env["db"])  # newest first: Borealis, Apollo
        chosen = _projects.run_projects_page(_Console(), _Live(), _keys("down", "enter"), 0.05, True)
        assert chosen != second["project_id"] and chosen.startswith("proj-")

    def test_open_from_the_menu_shortcut_returns_the_id_too(self, env):
        created = create_project("Apollo", db_path=env["db"])
        assert _projects.run_projects_page(_Console(), _Live(), _keys("enter"), 0.05, True) == created["project_id"]

    def test_empty_page_offers_the_command_instead(self, env):
        live = _run(_keys("enter", "esc"))
        assert "yeaboi project create" in _render(live.frames[-1])
        assert active.get_active_project() == ""


class TestArchive:
    def test_archive_hides_the_row_and_clears_active(self, env):
        created = create_project("Apollo", db_path=env["db"])
        active.set_active_project(created["project_id"])
        _run(_keys("right", "right", "right", "enter", "esc"))  # Archive the active project
        assert active.get_active_project() == ""


class TestSessionsSubPage:
    @pytest.fixture()
    def project(self, env):
        from yeaboi.agent.state import StandupReport
        from yeaboi.sessions import SessionStore
        from yeaboi.standup.store import StandupStore

        created = create_project("Apollo", db_path=env["db"])
        with SessionStore(env["db"]) as store:
            store.create_session("p1", "Apollo", project_id=created["project_id"])
        with StandupStore(env["db"]) as store:
            store.record_run(StandupReport(session_id="p1", date="2026-09-01"))
        return created

    def test_lists_the_projects_runs(self, env, project):
        live = _run(_keys("right", "enter", "esc", "esc"))  # Sessions, then back out twice
        # The subtitle is a typewriter reveal, so the row and the hint are what a keyed walk sees.
        assert any(
            "Standup — 2026-09-01" in _render(f) and "Enter opens the run's hub" in _render(f) for f in live.frames
        )

    def test_enter_opens_the_runs_hub_with_the_project_active(self, env, project):
        opened: list = []

        def open_hub(key):
            opened.append((key, active.get_active_project()))

        _projects.run_projects_page(
            _Console(), _Live(), _keys("right", "enter", "enter", "esc", "esc"), 0.05, True, open_hub=open_hub
        )
        assert opened == [("daily-standup", project["project_id"])]
        assert active.get_active_project() == ""  # restored afterwards

    def test_a_planning_row_points_at_its_card(self, env, project):
        live = _run(_keys("right", "enter", "down", "enter", "esc", "esc"))  # the planning row is second
        assert any("Open it from the Planning card." in _render(f) for f in live.frames)

    def test_a_row_without_a_hub_callable_points_at_its_card(self, env, project):
        live = _run(_keys("right", "enter", "enter", "esc", "esc"))
        assert any("Open it from the Standup card." in _render(f) for f in live.frames)

    def test_an_empty_project_says_so(self, env):
        create_project("Bare", db_path=env["db"])
        live = _run(_keys("right", "enter", "enter", "esc", "esc"))
        assert any("Nothing has run inside this project yet" in _render(f) for f in live.frames)


class TestContextPage:
    def test_space_toggles_one_source_off(self, env):
        # Open Context (button 2), Space the first row (retro) off, back out.
        _run(_keys("right", "right", "enter", " ", "esc", "esc"))
        assert active.get_context_deps() == ("standup", "plan", "performance", "analysis")

    def test_incognito_button_switches_everything_off(self, env):
        _run(_keys("right", "right", "enter", "right", "enter", "esc", "esc"))
        assert active.get_context_deps() == ()

    def test_all_on_restores_inherit(self, env):
        active.set_context_deps(())
        _run(_keys("right", "right", "enter", "enter", "esc", "esc"))
        assert active.get_context_deps() is None


class TestSoloMode:
    """The Solo world's ambient session flag and its context-dep default."""

    def test_solo_defaults_drop_the_retro_feed(self, env):
        from yeaboi.projects.scope import CONTEXT_DEP_TOKENS

        active.set_solo_mode(True)
        deps = active.get_context_deps()
        assert deps is not None and "retro" not in deps
        assert set(deps) == set(CONTEXT_DEP_TOKENS) - {"retro"}

    def test_an_explicit_choice_still_wins(self, env):
        active.set_solo_mode(True)
        active.set_context_deps(("retro", "plan"))
        assert active.get_context_deps() == ("retro", "plan")
        active.set_context_deps(())  # incognito is explicit too
        assert active.get_context_deps() == ()

    def test_leaving_the_solo_world_restores_inherit(self, env):
        active.set_solo_mode(True)
        assert active.get_context_deps() is not None
        active.set_solo_mode(False)
        assert active.get_context_deps() is None
        assert active.is_solo_mode() is False

    def test_incognito_renders_its_own_state_line(self, env):
        live = _run(_keys("right", "right", "enter", "right", "enter", "esc", "esc"))
        assert any("Incognito" in _render(f) for f in live.frames)

    def test_context_works_with_no_projects(self, env):
        _run(_keys("right", "right", "enter", "right", "enter", "esc", "esc"))
        assert active.get_context_deps() == ()
