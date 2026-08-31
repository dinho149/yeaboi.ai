"""Tests for projects/scope.py — scope resolution and the planning-state pull."""

import pytest

from yeaboi.agent.state import Sprint
from yeaboi.projects.scope import (
    CONTEXT_DEP_TOKENS,
    ProjectScope,
    latest_planning_state,
    normalize_context_deps,
    parse_context_spec,
    recent_standup_blockers,
    resolve_scope,
    wants,
)
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


class TestContextDepsVocabulary:
    def test_normalize_accepts_iterables_csv_and_json(self):
        assert normalize_context_deps(["retro", "plan"]) == frozenset({"retro", "plan"})
        assert normalize_context_deps("retro, plan") == frozenset({"retro", "plan"})
        assert normalize_context_deps('["standup"]') == frozenset({"standup"})
        assert normalize_context_deps(()) == frozenset()

    def test_normalize_none_and_blank_mean_all_on(self):
        assert normalize_context_deps(None) is None
        assert normalize_context_deps("") is None

    def test_normalize_drops_unknown_tokens_without_raising(self):
        assert normalize_context_deps(["retro", "bogus"]) == frozenset({"retro"})

    def test_parse_spec_grammar(self):
        assert parse_context_spec("all") == list(CONTEXT_DEP_TOKENS)
        assert parse_context_spec("none") == []
        assert parse_context_spec("") is None
        assert parse_context_spec("inherit") is None
        assert parse_context_spec("retro,plan") == ["retro", "plan"]

    def test_parse_spec_raises_on_a_typo(self):
        with pytest.raises(ValueError, match="unknown context source"):
            parse_context_spec("retro,bogus")

    def test_wants_helpers(self):
        scope = ProjectScope("proj-11112222", ("s1",), frozenset({"retro"}))
        assert scope.wants("retro") and not scope.wants("plan")
        assert wants(None, "plan")
        assert wants(ProjectScope("", None, None), "plan")
        assert not wants(ProjectScope("", None, frozenset()), "plan")


class TestResolveScopeContextDeps:
    def test_explicit_deps_win_over_the_project_default(self, tmp_path):
        db, pid = _linked_db(tmp_path)
        with ProjectStore(db) as projects:
            projects.set_settings(pid, {"default_context_deps": ["plan"]})
        scope = resolve_scope(pid, context_deps=["retro"], db_path=db)
        assert scope is not None and scope.context_deps == frozenset({"retro"})

    def test_project_default_applies_when_the_caller_passes_none(self, tmp_path):
        db, pid = _linked_db(tmp_path)
        with ProjectStore(db) as projects:
            projects.set_settings(pid, {"default_context_deps": ["plan", "retro"]})
        scope = resolve_scope(pid, db_path=db)
        assert scope is not None and scope.context_deps == frozenset({"plan", "retro"})

    def test_no_default_means_all_on(self, tmp_path):
        db, pid = _linked_db(tmp_path)
        scope = resolve_scope(pid, db_path=db)
        assert scope is not None and scope.context_deps is None

    def test_unscoped_incognito_gets_a_carrier_scope(self, tmp_path):
        # No project, but a deps restriction: the scope exists, narrows no
        # sessions (None, not ()), and carries the empty dep set.
        db, _ = _linked_db(tmp_path)
        scope = resolve_scope(session_id="loose-1", context_deps=[], db_path=db)
        assert scope == ProjectScope("", None, frozenset())

    def test_missing_db_still_carries_an_explicit_restriction(self, tmp_path):
        scope = resolve_scope(context_deps=["retro"], db_path=tmp_path / "sessions.db")
        assert scope == ProjectScope("", None, frozenset({"retro"}))

    def test_explicit_empty_differs_from_absent(self, tmp_path):
        db, _ = _linked_db(tmp_path)
        assert resolve_scope(db_path=db) is None
        assert resolve_scope(context_deps=[], db_path=db) is not None


class TestBlockersRespectTheStandupToggle:
    def test_standup_dep_off_returns_nothing(self, tmp_path):
        db, pid = _linked_db(tmp_path)
        scope = resolve_scope(pid, context_deps=["retro", "plan"], db_path=db)
        assert recent_standup_blockers(scope, db_path=db) == []
