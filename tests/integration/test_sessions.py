"""Tests for yeaboi.sessions: SessionStore, make_session_id, make_display_name, state serialization."""

import json
import re
from pathlib import Path

import pytest

from yeaboi.agent.state import (
    AcceptanceCriterion,
    Discipline,
    Feature,
    OutputFormat,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    ReviewDecision,
    Sprint,
    StoryPointValue,
    Task,
    UserStory,
)
from yeaboi.sessions import (
    CURRENT_SCHEMA_VERSION,
    SessionStore,
    _deserialize_state,
    _serialize_state,
    make_display_name,
    make_session_id,
    make_unique_display_names,
)

# ---------------------------------------------------------------------------
# make_session_id
# ---------------------------------------------------------------------------


class TestMakeSessionId:
    def test_format(self):
        sid = make_session_id()
        # new-<8 hex chars>-<YYYY-MM-DD>
        assert re.fullmatch(r"new-[0-9a-f]{8}-\d{4}-\d{2}-\d{2}", sid), f"bad format: {sid}"

    def test_unique(self):
        ids = {make_session_id() for _ in range(20)}
        # UUID prefix makes collisions astronomically unlikely
        assert len(ids) == 20


# ---------------------------------------------------------------------------
# make_display_name
# ---------------------------------------------------------------------------


class TestMakeDisplayName:
    def test_with_project_name_and_date(self):
        meta = {
            "session_id": "new-abc12345-2026-03-06",
            "project_name": "LendFlow",
            "created_at": "2026-03-06T12:00:00+00:00",
        }
        assert make_display_name(meta) == "lendflow-2026-03-06"

    def test_slugifies_spaces_and_special_chars(self):
        meta = {
            "session_id": "new-abc12345-2026-03-06",
            "project_name": "My Cool Project!",
            "created_at": "2026-03-06T12:00:00+00:00",
        }
        name = make_display_name(meta)
        assert name == "my-cool-project-2026-03-06"

    def test_fallback_to_session_id_when_no_project(self):
        meta = {"session_id": "new-abc12345-2026-03-06", "project_name": "", "created_at": "2026-03-06T12:00:00+00:00"}
        assert make_display_name(meta) == "new-abc12345-2026-03-06"

    def test_fallback_when_missing_keys(self):
        assert make_display_name({}) == "unknown"

    def test_slug_truncated_at_40_chars(self):
        long_name = "A" * 50
        meta = {
            "session_id": "new-abc12345-2026-03-06",
            "project_name": long_name,
            "created_at": "2026-03-06T12:00:00+00:00",
        }
        name = make_display_name(meta)
        # slug part before the date
        slug_part = name.rsplit("-", 3)[0]
        assert len(slug_part) <= 40


# ---------------------------------------------------------------------------
# SessionStore — round-trip
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> SessionStore:
    with SessionStore(tmp_path / "sessions.db") as s:
        yield s


class TestSessionStore:
    def test_create_and_get(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid)
        meta = store.get_session(sid)
        assert meta is not None
        assert meta["session_id"] == sid
        assert meta["project_name"] == ""
        assert meta["last_node_completed"] == ""

    def test_create_with_project_name(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="LendFlow")
        meta = store.get_session(sid)
        assert meta["project_name"] == "LendFlow"

    def test_get_returns_none_for_missing(self, store: SessionStore):
        assert store.get_session("nonexistent-id") is None

    def test_duplicate_create_ignored(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="First")
        store.create_session(sid, project_name="Second")  # should be silently ignored
        meta = store.get_session(sid)
        assert meta["project_name"] == "First"

    def test_update_project_name(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid)
        store.update_project_name(sid, "LendFlow")
        meta = store.get_session(sid)
        assert meta["project_name"] == "LendFlow"

    def test_update_last_node(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid)
        store.update_last_node(sid, "feature_generator")
        meta = store.get_session(sid)
        assert meta["last_node_completed"] == "feature_generator"

    def test_list_sessions_ordered_by_last_modified(self, store: SessionStore):
        sid1 = make_session_id()
        sid2 = make_session_id()
        store.create_session(sid1, project_name="Alpha")
        store.create_session(sid2, project_name="Beta")
        store.update_last_node(sid1, "feature_generator")  # makes sid1 most recently modified
        sessions = store.list_sessions()
        assert sessions[0]["session_id"] == sid1

    def test_list_sessions_empty(self, store: SessionStore):
        assert store.list_sessions() == []

    def test_context_manager(self, tmp_path: Path):
        sid = make_session_id()
        with SessionStore(tmp_path / "ctx.db") as s:
            s.create_session(sid, project_name="CtxTest")
        # After __exit__, connection is closed — re-open to verify persistence
        with SessionStore(tmp_path / "ctx.db") as s2:
            meta = s2.get_session(sid)
        assert meta is not None
        assert meta["project_name"] == "CtxTest"

    def test_data_persists_across_instances(self, tmp_path: Path):
        db = tmp_path / "persist.db"
        sid = make_session_id()
        with SessionStore(db) as s:
            s.create_session(sid, project_name="Persist")
            s.update_last_node(sid, "sprint_planner")
        with SessionStore(db) as s2:
            meta = s2.get_session(sid)
        assert meta["last_node_completed"] == "sprint_planner"

    def test_get_session_includes_session_state_raw(self, store: SessionStore):
        """get_session returns session_state_raw key (Phase 8B)."""
        sid = make_session_id()
        store.create_session(sid)
        meta = store.get_session(sid)
        assert "session_state_raw" in meta
        assert meta["session_state_raw"] == ""

    def test_get_latest_session_id(self, store: SessionStore):
        sid1 = make_session_id()
        sid2 = make_session_id()
        store.create_session(sid1)
        store.create_session(sid2)
        store.update_last_node(sid2, "feature_generator")
        assert store.get_latest_session_id() == sid2

    def test_get_latest_session_id_empty(self, store: SessionStore):
        assert store.get_latest_session_id() is None


# ---------------------------------------------------------------------------
# Schema migration (Phase 8B — backward compat with Phase 8A databases)
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    def test_old_db_gets_session_state_column(self, tmp_path: Path):
        """Opening a Phase 8A database (no session_state column) should migrate it."""
        import sqlite3

        db_path = tmp_path / "old.db"
        # Create a Phase 8A schema (no session_state column)
        conn = sqlite3.connect(str(db_path))
        conn.isolation_level = None
        conn.execute(
            """CREATE TABLE sessions_meta (
                session_id TEXT PRIMARY KEY,
                project_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_modified TEXT NOT NULL,
                last_node_completed TEXT NOT NULL DEFAULT ''
            )"""
        )
        conn.execute(
            "INSERT INTO sessions_meta VALUES (?, ?, ?, ?, ?)",
            ("old-session", "OldProject", "2026-01-01T00:00:00", "2026-01-01T00:00:00", "feature_generator"),
        )
        conn.close()

        # Open with SessionStore — should migrate and not crash
        with SessionStore(db_path) as store:
            meta = store.get_session("old-session")
        assert meta is not None
        assert meta["project_name"] == "OldProject"
        assert meta["session_state_raw"] == ""


# ---------------------------------------------------------------------------
# State serialization — _serialize_state / _deserialize_state
# ---------------------------------------------------------------------------


def _make_full_state() -> dict:
    """Build a realistic graph state with all artifact types for round-trip tests."""
    qs = QuestionnaireState(
        current_question=26,
        answers={1: "LendFlow", 6: "5 engineers", 11: "Python, React"},
        skipped_questions={2, 3},
        suggested_answers={4: "B2B fintech"},
        probed_questions={6},
        defaulted_questions={21, 22},
        completed=True,
        awaiting_confirmation=False,
        intake_mode="smart",
        extracted_questions={1, 11},
        _pending_merged_questions=[],
        _follow_up_choices={6: ("Option A", "Option B")},
    )
    pa = ProjectAnalysis(
        project_name="LendFlow",
        project_description="A B2B lending platform",
        project_type="greenfield",
        goals=("Launch MVP", "Onboard 10 lenders"),
        end_users=("Lenders", "Borrowers"),
        target_state="Fully operational lending marketplace",
        tech_stack=("Python", "React", "PostgreSQL"),
        integrations=("Stripe", "Plaid"),
        constraints=("SOC2 compliance",),
        sprint_length_weeks=2,
        target_sprints=6,
        risks=("Regulatory approval delay",),
        out_of_scope=("Mobile app",),
        assumptions=("Team ramp-up complete",),
        scrum_md_contributions=("goals", "tech_stack"),
    )
    feature = Feature(id="feature-1", title="User Onboarding", description="Onboarding flow", priority=Priority.HIGH)
    ac = AcceptanceCriterion(given="a new user", when="they sign up", then="they see the dashboard")
    story = UserStory(
        id="story-1",
        feature_id="feature-1",
        persona="lender",
        goal="sign up",
        benefit="access the platform",
        acceptance_criteria=(ac,),
        story_points=StoryPointValue.THREE,
        priority=Priority.HIGH,
        discipline=Discipline.FRONTEND,
        dod_applicable=(True, True, True, False, True, False, True),
    )
    task = Task(id="task-1", story_id="story-1", title="Build signup form", description="React form component")
    sprint = Sprint(id="sprint-1", name="Sprint 1", goal="Onboarding MVP", capacity_points=15, story_ids=("story-1",))
    return {
        "messages": [],
        "questionnaire": qs,
        "project_analysis": pa,
        "features": [feature],
        "stories": [story],
        "tasks": [task],
        "sprints": [sprint],
        "team_size": 5,
        "sprint_length_weeks": 2,
        "velocity_per_sprint": 25,
        "target_sprints": 6,
        "repo_context": "Python project with 42 files",
        "confluence_context": "",
        "user_context": "Some SCRUM.md content",
        "_intake_mode": "smart",
        "pending_review": "sprint_planner",
        "last_review_decision": ReviewDecision.EDIT,
        "last_review_feedback": "Add more stories",
        "output_format": OutputFormat.BOTH,
        "context_sources": [{"name": "repo", "status": "ok", "detail": "42 files"}],
        "jira_feature_keys": {"feature-1": "PROJ-1"},
        "jira_story_keys": {"story-1": "PROJ-2"},
    }


class TestSerializeState:
    """Tests for _serialize_state."""

    def test_returns_valid_json(self):
        state = _make_full_state()
        result = _serialize_state(state)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_messages_excluded(self):
        state = {"messages": ["should be skipped"], "team_size": 5}
        result = json.loads(_serialize_state(state))
        assert "messages" not in result
        assert result["team_size"] == 5

    def test_none_values_excluded(self):
        state = {"messages": [], "project_analysis": None, "team_size": 3}
        result = json.loads(_serialize_state(state))
        assert "project_analysis" not in result

    def test_enum_serialized_as_value(self):
        state = {"messages": [], "last_review_decision": ReviewDecision.ACCEPT}
        result = json.loads(_serialize_state(state))
        assert result["last_review_decision"] == "accept"

    def test_questionnaire_sets_become_lists(self):
        qs = QuestionnaireState(skipped_questions={1, 2, 3})
        state = {"messages": [], "questionnaire": qs}
        result = json.loads(_serialize_state(state))
        assert isinstance(result["questionnaire"]["skipped_questions"], list)
        assert set(result["questionnaire"]["skipped_questions"]) == {1, 2, 3}


class TestDeserializeState:
    """Tests for _deserialize_state."""

    def test_injects_messages_list(self):
        state = _deserialize_state('{"team_size": 5}')
        assert state["messages"] == []

    def test_reconstructs_questionnaire(self):
        qs = QuestionnaireState(current_question=10, answers={1: "test"}, skipped_questions={2})
        serialized = _serialize_state({"messages": [], "questionnaire": qs})
        state = _deserialize_state(serialized)
        assert isinstance(state["questionnaire"], QuestionnaireState)
        assert state["questionnaire"].current_question == 10
        assert state["questionnaire"].answers == {1: "test"}
        assert state["questionnaire"].skipped_questions == {2}

    def test_reconstructs_project_analysis(self):
        pa = ProjectAnalysis(
            project_name="X",
            project_description="Desc",
            project_type="greenfield",
            goals=("g1",),
            end_users=("u1",),
            target_state="done",
            tech_stack=("Python",),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=4,
            risks=(),
            out_of_scope=(),
            assumptions=(),
        )
        serialized = _serialize_state({"messages": [], "project_analysis": pa})
        state = _deserialize_state(serialized)
        assert isinstance(state["project_analysis"], ProjectAnalysis)
        assert state["project_analysis"].project_name == "X"
        assert state["project_analysis"].goals == ("g1",)

    def test_reconstructs_feature(self):
        feature = Feature(id="f-1", title="T", description="D", priority=Priority.CRITICAL)
        serialized = _serialize_state({"messages": [], "features": [feature]})
        state = _deserialize_state(serialized)
        assert isinstance(state["features"][0], Feature)
        assert state["features"][0].priority == Priority.CRITICAL

    def test_reconstructs_story_with_nested_ac(self):
        ac = AcceptanceCriterion(given="g", when="w", then="t")
        story = UserStory(
            id="s-1",
            feature_id="f-1",
            persona="user",
            goal="do",
            benefit="gain",
            acceptance_criteria=(ac,),
            story_points=StoryPointValue.FIVE,
            priority=Priority.MEDIUM,
            title="Create Bookmark Endpoint",
            discipline=Discipline.BACKEND,
        )
        serialized = _serialize_state({"messages": [], "stories": [story]})
        state = _deserialize_state(serialized)
        s = state["stories"][0]
        assert isinstance(s, UserStory)
        assert s.story_points == StoryPointValue.FIVE
        assert s.discipline == Discipline.BACKEND
        assert s.title == "Create Bookmark Endpoint"  # regression: title used to be dropped on load
        assert isinstance(s.acceptance_criteria[0], AcceptanceCriterion)

    def test_reconstructs_task(self):
        task = Task(id="t-1", story_id="s-1", title="Do", description="Details")
        serialized = _serialize_state({"messages": [], "tasks": [task]})
        state = _deserialize_state(serialized)
        assert isinstance(state["tasks"][0], Task)

    def test_reconstructs_sprint(self):
        sprint = Sprint(id="sp-1", name="S1", goal="Go", capacity_points=20, story_ids=("s-1", "s-2"))
        serialized = _serialize_state({"messages": [], "sprints": [sprint]})
        state = _deserialize_state(serialized)
        sp = state["sprints"][0]
        assert isinstance(sp, Sprint)
        assert sp.story_ids == ("s-1", "s-2")

    def test_reconstructs_enums(self):
        state_in = {
            "messages": [],
            "last_review_decision": ReviewDecision.EDIT,
            "output_format": OutputFormat.JIRA,
        }
        serialized = _serialize_state(state_in)
        state = _deserialize_state(serialized)
        assert state["last_review_decision"] == ReviewDecision.EDIT
        assert state["output_format"] == OutputFormat.JIRA

    def test_scalar_passthrough(self):
        state_in = {"messages": [], "team_size": 7, "repo_context": "data", "_intake_mode": "quick"}
        serialized = _serialize_state(state_in)
        state = _deserialize_state(serialized)
        assert state["team_size"] == 7
        assert state["repo_context"] == "data"
        assert state["_intake_mode"] == "quick"

    def test_jira_mappings_passthrough(self):
        state_in = {"messages": [], "jira_feature_keys": {"e-1": "PROJ-1"}}
        serialized = _serialize_state(state_in)
        state = _deserialize_state(serialized)
        assert state["jira_feature_keys"] == {"e-1": "PROJ-1"}

    def test_context_sources_passthrough(self):
        state_in = {"messages": [], "context_sources": [{"name": "repo", "status": "ok"}]}
        serialized = _serialize_state(state_in)
        state = _deserialize_state(serialized)
        assert state["context_sources"] == [{"name": "repo", "status": "ok"}]


class TestRoundTrip:
    """Full round-trip: serialize → deserialize → compare."""

    def test_full_state_round_trip(self):
        """All artifact types survive a serialize→deserialize round-trip."""
        original = _make_full_state()
        serialized = _serialize_state(original)
        restored = _deserialize_state(serialized)

        # Questionnaire
        assert isinstance(restored["questionnaire"], QuestionnaireState)
        assert restored["questionnaire"].answers == original["questionnaire"].answers
        assert restored["questionnaire"].skipped_questions == original["questionnaire"].skipped_questions
        assert restored["questionnaire"].completed is True

        # Project analysis
        assert isinstance(restored["project_analysis"], ProjectAnalysis)
        assert restored["project_analysis"].project_name == "LendFlow"
        assert restored["project_analysis"].goals == ("Launch MVP", "Onboard 10 lenders")

        # Artifacts
        assert len(restored["features"]) == 1
        assert restored["features"][0].priority == Priority.HIGH
        assert len(restored["stories"]) == 1
        assert restored["stories"][0].story_points == StoryPointValue.THREE
        assert restored["stories"][0].discipline == Discipline.FRONTEND
        assert len(restored["tasks"]) == 1
        assert len(restored["sprints"]) == 1
        assert restored["sprints"][0].story_ids == ("story-1",)

        # Scalars
        assert restored["team_size"] == 5
        assert restored["velocity_per_sprint"] == 25

        # Enums
        assert restored["last_review_decision"] == ReviewDecision.EDIT
        assert restored["output_format"] == OutputFormat.BOTH

        # Messages injected
        assert restored["messages"] == []


# ---------------------------------------------------------------------------
# SessionStore — save_state / load_state
# ---------------------------------------------------------------------------


class TestSaveLoadState:
    def test_save_and_load_round_trip(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid)
        original = _make_full_state()
        store.save_state(sid, original)
        loaded = store.load_state(sid)
        assert loaded is not None
        assert loaded["team_size"] == 5
        assert isinstance(loaded["questionnaire"], QuestionnaireState)
        assert isinstance(loaded["features"][0], Feature)

    def test_load_state_missing_session(self, store: SessionStore):
        assert store.load_state("nonexistent") is None

    def test_load_state_empty_state(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid)
        # session_state is empty string by default
        assert store.load_state(sid) is None

    def test_load_state_corrupt_json(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid)
        store._conn.execute(
            "UPDATE sessions_meta SET session_state = ? WHERE session_id = ?",
            ("{{{invalid", sid),
        )
        assert store.load_state(sid) is None

    def test_save_state_overwrites_previous(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid)
        store.save_state(sid, {"messages": [], "team_size": 3})
        store.save_state(sid, {"messages": [], "team_size": 7})
        loaded = store.load_state(sid)
        assert loaded["team_size"] == 7

    def test_save_state_persists_across_instances(self, tmp_path: Path):
        db = tmp_path / "persist.db"
        sid = make_session_id()
        with SessionStore(db) as s:
            s.create_session(sid)
            s.save_state(sid, _make_full_state())
        with SessionStore(db) as s2:
            loaded = s2.load_state(sid)
        assert loaded is not None
        assert loaded["team_size"] == 5
        assert isinstance(loaded["project_analysis"], ProjectAnalysis)


# ---------------------------------------------------------------------------
# Schema version tracking (Phase 8C)
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_same_version_no_mismatch(self, tmp_path: Path):
        """Opening a DB at the current schema version → no mismatch."""
        db = tmp_path / "v.db"
        with SessionStore(db) as s:
            assert s.schema_mismatch is False
        # Re-open — still no mismatch
        with SessionStore(db) as s2:
            assert s2.schema_mismatch is False

    def test_old_db_no_row_stamps_current(self, tmp_path: Path):
        """Pre-8C DB (no schema_info table) gets stamped with current version."""
        import sqlite3

        db = tmp_path / "old.db"
        conn = sqlite3.connect(str(db))
        conn.isolation_level = None
        conn.execute(
            """CREATE TABLE sessions_meta (
                session_id TEXT PRIMARY KEY,
                project_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_modified TEXT NOT NULL,
                last_node_completed TEXT NOT NULL DEFAULT '',
                session_state TEXT NOT NULL DEFAULT ''
            )"""
        )
        conn.close()

        with SessionStore(db) as s:
            assert s.schema_mismatch is False
            row = s._conn.execute("SELECT schema_version FROM schema_info").fetchone()
            assert row[0] == CURRENT_SCHEMA_VERSION

    def test_newer_db_sets_mismatch_true(self, tmp_path: Path):
        """DB written by a newer version → schema_mismatch=True."""
        import sqlite3

        db = tmp_path / "future.db"
        conn = sqlite3.connect(str(db))
        conn.isolation_level = None
        conn.execute(
            """CREATE TABLE sessions_meta (
                session_id TEXT PRIMARY KEY,
                project_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_modified TEXT NOT NULL,
                last_node_completed TEXT NOT NULL DEFAULT '',
                session_state TEXT NOT NULL DEFAULT ''
            )"""
        )
        conn.execute("CREATE TABLE schema_info (schema_version INT NOT NULL)")
        conn.execute("INSERT INTO schema_info VALUES (?)", (CURRENT_SCHEMA_VERSION + 5,))
        conn.close()

        with SessionStore(db) as s:
            assert s.schema_mismatch is True


# ---------------------------------------------------------------------------
# make_unique_display_names (Phase 8C)
# ---------------------------------------------------------------------------


class TestMakeUniqueDisplayNames:
    def test_no_collisions(self):
        sessions = [
            {"session_id": "s1", "project_name": "Alpha", "created_at": "2026-03-06T12:00:00"},
            {"session_id": "s2", "project_name": "Beta", "created_at": "2026-03-06T12:00:00"},
        ]
        names = make_unique_display_names(sessions)
        assert names["s1"] == "alpha-2026-03-06"
        assert names["s2"] == "beta-2026-03-06"

    def test_two_same_day(self):
        sessions = [
            {"session_id": "s1", "project_name": "LendFlow", "created_at": "2026-03-06T10:00:00"},
            {"session_id": "s2", "project_name": "LendFlow", "created_at": "2026-03-06T14:00:00"},
        ]
        names = make_unique_display_names(sessions)
        assert names["s1"] == "lendflow-2026-03-06"
        assert names["s2"] == "lendflow-2026-03-06-2"

    def test_three_same_day(self):
        sessions = [
            {"session_id": "s1", "project_name": "X", "created_at": "2026-03-06T08:00:00"},
            {"session_id": "s2", "project_name": "X", "created_at": "2026-03-06T12:00:00"},
            {"session_id": "s3", "project_name": "X", "created_at": "2026-03-06T16:00:00"},
        ]
        names = make_unique_display_names(sessions)
        assert names["s1"] == "x-2026-03-06"
        assert names["s2"] == "x-2026-03-06-2"
        assert names["s3"] == "x-2026-03-06-3"

    def test_mixed_named_and_unnamed(self):
        sessions = [
            {"session_id": "new-abc-2026-03-06", "project_name": "", "created_at": "2026-03-06T12:00:00"},
            {"session_id": "s2", "project_name": "Alpha", "created_at": "2026-03-06T12:00:00"},
        ]
        names = make_unique_display_names(sessions)
        # Unnamed falls back to session_id — no collision with "alpha-2026-03-06"
        assert names["new-abc-2026-03-06"] == "new-abc-2026-03-06"
        assert names["s2"] == "alpha-2026-03-06"

    def test_empty_list(self):
        assert make_unique_display_names([]) == {}


# ---------------------------------------------------------------------------
# Auto-prune old sessions (Phase 8C)
# ---------------------------------------------------------------------------


class TestPruneOldSessions:
    def test_prunes_old_keeps_recent(self, tmp_path: Path):
        """Sessions older than max_age_days are deleted; recent ones survive."""
        db = tmp_path / "prune.db"
        with SessionStore(db) as s:
            s.create_session("old-1", project_name="Old")
            s.create_session("new-1", project_name="New")
            # Manually backdate old-1 to 60 days ago
            s._conn.execute(
                "UPDATE sessions_meta SET last_modified = datetime('now', '-60 days') WHERE session_id = ?",
                ("old-1",),
            )
            pruned = s.prune_old_sessions(30)
            assert pruned == 1
            assert s.get_session("old-1") is None
            assert s.get_session("new-1") is not None

    def test_zero_max_age_returns_zero(self, tmp_path: Path):
        """max_age_days=0 means pruning is disabled — returns 0."""
        db = tmp_path / "noprune.db"
        with SessionStore(db) as s:
            s.create_session("s1")
            s._conn.execute(
                "UPDATE sessions_meta SET last_modified = datetime('now', '-999 days') WHERE session_id = ?",
                ("s1",),
            )
            assert s.prune_old_sessions(0) == 0
            assert s.get_session("s1") is not None

    def test_nothing_to_prune(self, store: SessionStore):
        """No old sessions → returns 0."""
        store.create_session("recent-1")
        assert store.prune_old_sessions(30) == 0


# ---------------------------------------------------------------------------
# Delete sessions (Phase 8C — /clear and --clear-sessions)
# ---------------------------------------------------------------------------


class TestDeleteSessions:
    def test_delete_session_returns_true(self, store: SessionStore):
        store.create_session("del-1")
        assert store.delete_session("del-1") is True
        assert store.get_session("del-1") is None

    def test_delete_session_nonexistent_returns_false(self, store: SessionStore):
        assert store.delete_session("nonexistent") is False

    def test_delete_all_sessions(self, store: SessionStore):
        store.create_session("a1")
        store.create_session("a2")
        store.create_session("a3")
        count = store.delete_all_sessions()
        assert count == 3
        assert store.list_sessions() == []

    def test_delete_all_empty_db(self, store: SessionStore):
        assert store.delete_all_sessions() == 0
