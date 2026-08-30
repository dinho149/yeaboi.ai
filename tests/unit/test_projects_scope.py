"""Tests for projects/scope.py — scope resolution and the planning-state pull."""

from yeaboi.agent.state import Sprint
from yeaboi.projects.scope import ProjectScope, latest_planning_state, resolve_scope
from yeaboi.projects.store import ProjectStore
from yeaboi.sessions import SessionStore


def _sprint(name: str) -> Sprint:
    return Sprint(id="SP-1", name=name, goal="", capacity_points=10, story_ids=())


def _linked_db(tmp_path):
    """A DB with one project and one linked planning session."""
    db = tmp_path / "sessions.db"
    with ProjectStore(db) as projects:
        project = projects.create("Apollo")
    with SessionStore(db) as sessions:
        sessions.create_session("plan-1", "Apollo", project_id=project["project_id"])
        sessions.create_session("loose-1")
    return db, project["project_id"]


class TestResolveScope:
    def test_explicit_project_id_wins(self, tmp_path):
        db, pid = _linked_db(tmp_path)
        scope = resolve_scope(pid, "loose-1", db_path=db)
        assert scope is not None
        assert scope.project_id == pid
        assert scope.session_ids == ("plan-1",)
        assert scope.context_deps is None

    def test_inherits_from_a_linked_session(self, tmp_path):
        db, pid = _linked_db(tmp_path)
        scope = resolve_scope(session_id="plan-1", db_path=db)
        assert scope is not None and scope.project_id == pid

    def test_unlinked_session_resolves_to_none(self, tmp_path):
        db, _ = _linked_db(tmp_path)
        assert resolve_scope(session_id="loose-1", db_path=db) is None

    def test_no_arguments_resolves_to_none(self, tmp_path):
        db, _ = _linked_db(tmp_path)
        assert resolve_scope(db_path=db) is None

    def test_missing_db_resolves_to_none(self, tmp_path):
        # Never creates the DB file on a read path, and never raises.
        db = tmp_path / "sessions.db"
        assert resolve_scope("proj-11112222", db_path=db) is None
        assert not db.exists()

    def test_broken_db_path_never_raises(self, tmp_path):
        assert resolve_scope("proj-11112222", db_path=tmp_path) is None  # a directory, not a file

    def test_unknown_project_still_scopes_to_empty(self, tmp_path):
        # A bad-but-set project id narrows to nothing rather than widening to
        # everything: the caller asked for a project, they get its (zero) rows.
        db, _ = _linked_db(tmp_path)
        scope = resolve_scope("proj-00000000", db_path=db)
        assert scope is not None and scope.session_ids == ()


class TestLatestPlanningState:
    def test_none_scope_is_none(self, tmp_path):
        assert latest_planning_state(None, db_path=tmp_path / "sessions.db") is None

    def test_picks_newest_session_with_sprints(self, tmp_path):
        db, pid = _linked_db(tmp_path)
        with SessionStore(db) as sessions:
            sessions.create_session("plan-2", "Apollo", project_id=pid)
            sessions.save_state("plan-1", {"messages": [], "sprints": [], "team_size": 3})
            sessions.save_state("plan-2", {"messages": [], "sprints": [_sprint("Sprint 1")], "team_size": 5})
        found = latest_planning_state(resolve_scope(pid, db_path=db), db_path=db)
        assert found is not None
        sid, state = found
        assert sid == "plan-2"
        assert state["team_size"] == 5

    def test_sprintless_project_yields_none(self, tmp_path):
        db, pid = _linked_db(tmp_path)
        with SessionStore(db) as sessions:
            sessions.save_state("plan-1", {"messages": [], "sprints": []})
        assert latest_planning_state(resolve_scope(pid, db_path=db), db_path=db) is None

    def test_non_planning_sessions_are_ignored(self, tmp_path):
        db, pid = _linked_db(tmp_path)
        with SessionStore(db) as sessions:
            sessions.create_session("analysis-1", mode="analysis", project_id=pid)
            sessions.save_state("analysis-1", {"messages": [], "sprints": [_sprint("not a plan")]})
        assert latest_planning_state(ProjectScope(pid, ("analysis-1",)), db_path=db) is None
