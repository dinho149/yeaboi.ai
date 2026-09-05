"""Tests for the Projects TUI page loops (ui/mode_select/_projects.py).

Driven with a scripted ``read_key`` and a fake Live, the same shape the other
page-loop tests use — no terminal, no threads of our own (the AI rewrite's
worker is monkeypatched to answer inline).
"""

from __future__ import annotations

import pytest

from yeaboi.projects import active
from yeaboi.projects.engine import create_project, get_project
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
    # The project page's Plan fact reads projects.json; point it at nothing.
    monkeypatch.setattr("yeaboi.persistence.load_projects", lambda: [])
    active.set_active_project("")
    active.set_context_deps(None)
    active.set_solo_mode(False)
    yield {"db": db}
    active.set_active_project("")
    active.set_context_deps(None)
    active.set_solo_mode(False)


def _run(keys, **kwargs) -> _Live:
    live = _Live()
    _projects.run_projects_page(_Console(), live, keys, 0.05, True, **kwargs)
    return live


def _render(panel) -> str:
    import io

    from rich.console import Console

    console = Console(file=io.StringIO(), width=84, height=40)
    console.print(panel)
    return console.file.getvalue()


def _frames(live: _Live) -> str:
    return "\n".join(_render(f) for f in live.frames)


# The list's buttons: Open · New · Done/Reopen · Archive · Back.
OPEN, NEW, DONE, ARCHIVE, BACK = 0, 1, 2, 3, 4
# The project page's: Start · Plan · Runs · Context · Back.
START, PLAN, RUNS, CONTEXT, PBACK = 0, 1, 2, 3, 4


def _press(index: int, *rest):
    return _keys(*(["right"] * index), "enter", *rest)


class TestClose:
    def test_esc_closes_the_page(self, env):
        assert _run(_keys("esc")).frames

    def test_the_back_button_closes_it(self, env):
        create_project("Apollo", db_path=env["db"])
        live = _run(_press(BACK))
        assert "Apollo" in _render(live.frames[-1])

    def test_esc_returns_none(self, env):
        create_project("Apollo", db_path=env["db"])
        assert _projects.run_projects_page(_Console(), _Live(), _keys("esc"), 0.05, True, pick=True) is None
        assert active.get_active_project() == ""


class TestOpenAndStart:
    def test_open_shows_the_project_page_and_start_returns_its_id(self, env):
        created = create_project("Apollo", "A moon shot", db_path=env["db"])
        live = _Live()
        chosen = _projects.run_projects_page(_Console(), live, _press(OPEN, "enter"), 0.05, True, pick=True)
        assert chosen == created["project_id"]
        assert active.get_active_project() == created["project_id"]
        assert "A moon shot" in _frames(live) and "Inside this project" in _frames(live)

    def test_plan_returns_the_card_and_the_id(self, env):
        created = create_project("Apollo", db_path=env["db"])
        chosen = _projects.run_projects_page(_Console(), _Live(), _press(OPEN, "right", "enter"), 0.05, True)
        assert chosen == (_projects.PLAN_CARD, created["project_id"])
        assert active.get_active_project() == created["project_id"]

    def test_back_from_the_page_returns_to_the_list(self, env):
        created = create_project("Apollo", db_path=env["db"])
        live = _run(_press(OPEN, "esc", "esc"))
        assert active.get_active_project() == ""
        assert created["name"] in _render(live.frames[-1])

    def test_down_moves_the_selection_so_the_second_project_can_be_opened(self, env):
        create_project("Apollo", db_path=env["db"])
        second = create_project("Borealis", db_path=env["db"])  # newest first: Borealis, Apollo
        chosen = _projects.run_projects_page(_Console(), _Live(), _keys("down", "enter", "enter"), 0.05, True)
        assert chosen != second["project_id"] and chosen.startswith("proj-")

    def test_empty_page_points_at_new(self, env):
        live = _run(_keys("enter", "esc"))
        assert "New starts one" in _render(live.frames[-1])
        assert active.get_active_project() == ""


class TestStatus:
    def test_done_moves_the_row_to_completed_and_reopen_brings_it_back(self, env):
        created = create_project("Apollo", db_path=env["db"])
        live = _run(_press(DONE, "esc"))
        assert get_project(created["project_id"], db_path=env["db"])["status"] == "done"
        out = _render(live.frames[-1])
        assert "Completed" in out and "Reopen" in out and "Apollo is done" in out
        _run(_press(DONE, "esc"))  # the same button now reads Reopen
        assert get_project(created["project_id"], db_path=env["db"])["status"] == "active"

    def test_d_on_the_project_page_toggles_it(self, env):
        created = create_project("Apollo", db_path=env["db"])
        live = _run(_press(OPEN, "d", "esc", "esc"))
        assert get_project(created["project_id"], db_path=env["db"])["status"] == "done"
        assert "Completed" in _frames(live)


class TestArchive:
    def test_archive_asks_first_then_hides_the_row_and_clears_active(self, env):
        created = create_project("Apollo", db_path=env["db"])
        active.set_active_project(created["project_id"])
        live = _run(_press(ARCHIVE, "enter", "esc"))
        assert active.get_active_project() == ""
        assert "Archive Apollo?" in _frames(live)
        assert _projects._load() == [] and "Archived Apollo." in _render(live.frames[-1])

    def test_esc_on_the_popup_keeps_it(self, env):
        created = create_project("Apollo", db_path=env["db"])
        _run(_press(ARCHIVE, "esc", "esc"))
        assert get_project(created["project_id"], db_path=env["db"])["archived"] is False

    def test_a_from_the_project_page_archives_and_closes_it(self, env):
        created = create_project("Apollo", db_path=env["db"])
        _run(_press(OPEN, "a", "enter", "esc"))
        assert get_project(created["project_id"], db_path=env["db"])["archived"] is True


class TestNewProject:
    @pytest.fixture()
    def typed(self, monkeypatch):
        """Stand in for the themed line editor: hand back the scripted text."""
        answers: list = []

        def _read_line(console, live, read_key, frame_time, supports_timeout, **kwargs):
            live.update(_projects._build_draft_screen({"name": "typing", "description": kwargs.get("initial", "")}))
            return answers.pop(0) if answers else None

        import yeaboi.ui.mode_select as ms

        monkeypatch.setattr(ms, "_standup_read_line", _read_line)
        return answers

    def test_describe_then_create_opens_the_new_project(self, env, typed):
        typed.append("A pond where ducks plan sprints")
        live = _Live()
        # New → (describe) → Create → the project page → Start.
        chosen = _projects.run_projects_page(_Console(), live, _press(NEW, "enter", "enter"), 0.05, True, pick=True)
        rows = _projects._load()
        assert [r["name"] for r in rows] == ["a pond where ducks"]
        assert rows[0]["description"] == "A pond where ducks plan sprints"
        assert chosen == rows[0]["project_id"] and active.get_active_project() == chosen
        assert "Named from your first words" in _frames(live)

    def test_cancel_at_the_description_creates_nothing(self, env, typed):
        _run(_press(NEW, "esc"))
        assert _projects._load() == []

    def test_cancel_on_the_preview_creates_nothing(self, env, typed):
        typed.append("something")
        _run(_press(NEW, "esc", "esc"))
        assert _projects._load() == []

    def test_ai_rewrite_replaces_the_draft(self, env, typed, monkeypatch):
        typed.append("a duck pond")
        monkeypatch.setattr(
            _projects,
            "_rewrite",
            lambda *a, **k: {
                "name": "duck pond",
                "description": "A pond for ducks.",
                "source": "ai",
                "note": "AI rewrote it.",
            },
        )
        live = _Live()
        # New → (describe) → AI rewrite → Create → page → Esc → list → Esc.
        _projects.run_projects_page(
            _Console(), live, _press(NEW, "right", "enter", "left", "enter", "esc", "esc"), 0.05, True
        )
        rows = _projects._load()
        assert [(r["name"], r["description"]) for r in rows] == [("duck pond", "A pond for ducks.")]
        assert "AI rewrote it." in _frames(live)

    def test_edit_reopens_the_description(self, env, typed):
        typed.extend(["first words", "second words here"])
        # New → (describe) → Edit → (describe again) → Create → page → Esc → Esc.
        _run(_press(NEW, "right", "right", "enter", "enter", "esc", "esc"))
        assert [r["description"] for r in _projects._load()] == ["second words here"]

    def test_the_rewrite_thread_answers_through_the_engine(self, env, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.projects.engine.draft_project_idea",
            lambda text: {"name": "x", "description": text.upper(), "source": "ai", "note": "ok"},
        )
        result = _projects._rewrite(_Console(), _Live(), _keys(), 0.0, True, "quiet")
        assert result["description"] == "QUIET"


class TestInsideFacts:
    def test_counts_runs_by_step_and_reads_the_plan_from_projects_json(self, env, monkeypatch):
        from yeaboi.sessions_recent import RecentSession
        from yeaboi.ui.mode_select import ProjectSummary

        rows = [
            RecentSession("s1", "1", "standup", "Standup", "", "", "proj-1"),
            RecentSession("s1", "2", "standup", "Standup", "", "", "proj-1"),
            RecentSession("s1", "3", "retro", "Retro", "", "", "proj-1"),
        ]
        monkeypatch.setattr(
            "yeaboi.persistence.load_projects",
            lambda: [ProjectSummary("Apollo", status="Complete", engine_project_id="proj-1")],
        )
        facts = _projects._inside("proj-1", rows)
        assert facts["daily-standup"] == "2 runs" and facts["retro"] == "1 run"
        assert facts["reporting"] == "not yet" and facts["project-planning"] == "done"

    def test_an_unfinished_plan_is_in_progress(self, env, monkeypatch):
        from yeaboi.ui.mode_select import ProjectSummary

        monkeypatch.setattr(
            "yeaboi.persistence.load_projects",
            lambda: [ProjectSummary("Apollo", status="In Progress", engine_project_id="proj-1")],
        )
        assert _projects._inside("proj-1", [])["project-planning"] == "in progress"
        assert _projects._inside("proj-2", [])["project-planning"] == "not yet"


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

    def _runs(self, *rest):
        # Open the project, then its Runs button.
        return _press(OPEN, "right", "right", "enter", *rest)

    def test_lists_the_projects_runs(self, env, project):
        live = _run(self._runs("esc", "esc", "esc"))
        assert any(
            "Standup — 2026-09-01" in _render(f) and "Enter opens the run's hub" in _render(f) for f in live.frames
        )

    def test_enter_opens_the_runs_hub_with_the_project_active(self, env, project):
        opened: list = []

        def open_hub(key):
            opened.append((key, active.get_active_project()))

        _projects.run_projects_page(
            _Console(), _Live(), self._runs("enter", "esc", "esc", "esc"), 0.05, True, open_hub=open_hub
        )
        assert opened == [("daily-standup", project["project_id"])]
        assert active.get_active_project() == ""  # restored afterwards

    def test_a_planning_row_points_at_its_card(self, env, project):
        live = _run(self._runs("down", "enter", "esc", "esc", "esc"))  # the planning row is second
        assert "Open it from the Planning card." in _frames(live)

    def test_a_row_without_a_hub_callable_points_at_its_card(self, env, project):
        live = _run(self._runs("enter", "esc", "esc", "esc"))
        assert "Open it from the Standup card." in _frames(live)

    def test_an_empty_project_says_so(self, env):
        create_project("Bare", db_path=env["db"])
        live = _run(self._runs("enter", "esc", "esc", "esc"))
        assert "Nothing has run inside this project yet" in _frames(live)


class TestContextPage:
    def _context(self, *rest):
        # Open the project, then its Context button.
        return _press(OPEN, "right", "right", "right", "enter", *rest)

    def test_space_toggles_one_source_off(self, env):
        create_project("Apollo", db_path=env["db"])
        _run(self._context(" ", "esc", "esc", "esc"))
        assert active.get_context_deps() == ("standup", "plan", "performance", "analysis")

    def test_incognito_button_switches_everything_off(self, env):
        create_project("Apollo", db_path=env["db"])
        _run(self._context("right", "enter", "esc", "esc", "esc"))
        assert active.get_context_deps() == ()

    def test_all_on_restores_inherit(self, env):
        create_project("Apollo", db_path=env["db"])
        active.set_context_deps(())
        _run(self._context("enter", "esc", "esc", "esc"))
        assert active.get_context_deps() is None

    def test_the_page_runs_on_its_own_too(self, env):
        live = _Live()
        _projects.run_context_page(_Console(), live, _keys("right", "enter", "esc"), 0.05, True)
        assert active.get_context_deps() == () and "Incognito" in _frames(live)


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

    def test_the_solo_list_has_no_retro_step(self, env):
        live = _run(_keys("esc"), world="solo")
        out = _render(live.frames[-1])
        assert "● Retro" not in out and "● Plan" in out
