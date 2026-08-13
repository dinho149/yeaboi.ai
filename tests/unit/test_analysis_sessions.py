"""Unit tests for analysis-mode session persistence.

Covers:
- create_session with mode="analysis"
- list_analysis_sessions filtering
- Schema v4 migration (session_mode column)
- Analysis session CRUD round-trips
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yeaboi.sessions import (
    CURRENT_SCHEMA_VERSION,
    SessionStore,
    make_session_id,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> SessionStore:
    with SessionStore(tmp_path / "sessions.db") as s:
        yield s


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_current_version(self):
        # v6 added the Daily Standup tables; v7 added the Retro tables (retro_history);
        # v8 added the Performance tables (1:1s, reviews, notes); v9 added the
        # Reporting table (reporting_history); v10 added the Roadmap tables
        # (roadmap_config, roadmap_history); v11 added the multi-row roadmaps
        # list; v12 added the token_usage performance columns (duration_ms /
        # eval_duration_ms / load_duration_ms / tokens_per_sec) for local metrics;
        # v13 added the ticket cache; v14-v17 add Standup scopes; v18 added the
        # Scrum Poker table (poker_history); v19 adds the Analysis enrichment
        # cache; v20 records selected Analysis features; v21 adds the artifact
        # edit log plus origin/edited_from_id provenance on every history table;
        # v22 added the Standup transcript-review tables (standup_reviews,
        # standup_transcripts, standup_gap_issues) plus the two standup_config
        # transcript columns; v23 adds the Standup practice-detection config
        # (habit_detection / habit_rules); v24 adds its LLM relatedness switch
        # (habit_ai_match); v25 adds the practice feedback ledger
        # (standup_practice_feedback); v26 re-applies v21 — a pre-rebase
        # lineage stamped shared DBs at 21 with a different meaning, so the
        # provenance columns could be missing from a DB already at v25;
        # v27 adds the agentwatch tables (agent_sessions, agent_ingest_files,
        # agent_security_findings, and the three report-history tables); v28
        # adds standup_config.github_owners, the GitHub org scope that mirrors
        # the Azure project picker; v29 adds
        # standup_config.github_excluded_repositories, the narrow opt-out from
        # an owner's expansion.
        # v30 adds planning_prior_art_feedback, the global ledger of which
        # existing repos the user ruled out as prior art.
        assert CURRENT_SCHEMA_VERSION == 30

    def test_new_db_has_session_mode_column(self, store: SessionStore):
        """A freshly created DB should have the session_mode column."""
        sid = make_session_id()
        store.create_session(sid, mode="analysis")
        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row is not None
        assert row[0] == "analysis"

    def test_new_db_has_analysis_ticket_cache(self, store: SessionStore):
        row = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_ticket_cache'"
        ).fetchone()
        assert row == ("analysis_ticket_cache",)

    def test_new_db_has_analysis_enrichment_cache(self, store: SessionStore):
        row = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_enrichment_cache'"
        ).fetchone()
        assert row == ("analysis_enrichment_cache",)

    def test_new_db_records_analysis_run_features(self, tmp_path: Path):
        from yeaboi.team_profile import TeamProfileStore

        with TeamProfileStore(tmp_path / "features.db") as profiles:
            cols = {row[1] for row in profiles._conn.execute("PRAGMA table_info(analysis_runs)").fetchall()}
        assert "features_json" in cols

    def test_v12_db_migrates_analysis_ticket_cache(self, tmp_path: Path):
        import sqlite3

        db_path = tmp_path / "v12.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_info (schema_version INT NOT NULL)")
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (12)")
        conn.commit()
        conn.close()

        with SessionStore(db_path) as migrated:
            row = migrated._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_ticket_cache'"
            ).fetchone()
            version = migrated._conn.execute("SELECT schema_version FROM schema_info").fetchone()[0]

        assert row == ("analysis_ticket_cache",)
        assert version == CURRENT_SCHEMA_VERSION

    def test_v15_db_migrates_documentation_scope(self, tmp_path: Path):
        import sqlite3

        db_path = tmp_path / "v15.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_info (schema_version INT NOT NULL)")
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (15)")
        conn.execute("CREATE TABLE standup_config (session_id TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        with SessionStore(db_path) as migrated:
            columns = {row[1] for row in migrated._conn.execute("PRAGMA table_info(standup_config)").fetchall()}
            version = migrated._conn.execute("SELECT schema_version FROM schema_info").fetchone()[0]

        assert {"documentation_sources", "documentation_scope_configured"} <= columns
        assert version == CURRENT_SCHEMA_VERSION

    def test_v16_db_migrates_azure_repository_scope_to_projects(self, tmp_path: Path):
        import sqlite3

        db_path = tmp_path / "v16.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_info (schema_version INT NOT NULL)")
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (16)")
        conn.execute(
            """CREATE TABLE standup_config (
                   session_id TEXT PRIMARY KEY,
                   azdo_repositories TEXT NOT NULL DEFAULT '[]'
               )"""
        )
        conn.execute(
            "INSERT INTO standup_config (session_id, azdo_repositories) VALUES (?, ?)",
            ("s1", '["Core/api", "Core/web", "Platform/service"]'),
        )
        conn.commit()
        conn.close()

        with SessionStore(db_path) as migrated:
            projects = migrated._conn.execute(
                "SELECT azdo_projects FROM standup_config WHERE session_id = 's1'"
            ).fetchone()[0]
            version = migrated._conn.execute("SELECT schema_version FROM schema_info").fetchone()[0]

        assert projects == '["Core", "Platform"]'
        assert version == CURRENT_SCHEMA_VERSION

    def test_v27_db_gains_github_owner_scope_without_widening_saved_repos(self, tmp_path: Path):
        """Column only: deriving owners from saved repos would silently widen scope."""
        import sqlite3

        db_path = tmp_path / "v27.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE schema_info (schema_version INT NOT NULL)")
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (27)")
        conn.execute(
            """CREATE TABLE standup_config (
                   session_id TEXT PRIMARY KEY,
                   github_repositories TEXT NOT NULL DEFAULT '[]'
               )"""
        )
        conn.execute(
            "INSERT INTO standup_config (session_id, github_repositories) VALUES (?, ?)",
            ("s1", '["acme/api", "acme/web"]'),
        )
        conn.commit()
        conn.close()

        with SessionStore(db_path) as migrated:
            columns = {row[1] for row in migrated._conn.execute("PRAGMA table_info(standup_config)").fetchall()}
            owners, repositories = migrated._conn.execute(
                "SELECT github_owners, github_repositories FROM standup_config WHERE session_id = 's1'"
            ).fetchone()
            version = migrated._conn.execute("SELECT schema_version FROM schema_info").fetchone()[0]

        assert "github_owners" in columns
        assert owners == "[]"
        assert repositories == '["acme/api", "acme/web"]'
        assert version == CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# create_session with mode
# ---------------------------------------------------------------------------


class TestCreateSessionWithMode:
    def test_default_mode_is_planning(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid)
        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row[0] == "planning"

    def test_analysis_mode(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="Team Analysis", mode="analysis")
        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row[0] == "analysis"

    def test_duplicate_session_id_ignored(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, mode="analysis")
        # Creating again with same ID should not raise
        store.create_session(sid, mode="planning")
        # Original mode should be preserved (INSERT OR IGNORE)
        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row[0] == "analysis"

    def test_create_with_project_name(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="Platform Team", mode="analysis")
        meta = store.get_session(sid)
        assert meta is not None
        assert meta["project_name"] == "Platform Team"


# ---------------------------------------------------------------------------
# list_analysis_sessions
# ---------------------------------------------------------------------------


class TestListAnalysisSessions:
    def test_empty_store(self, store: SessionStore):
        result = store.list_analysis_sessions()
        assert result == []

    def test_only_returns_analysis_sessions(self, store: SessionStore):
        sid_plan = make_session_id()
        sid_ana1 = make_session_id()
        sid_ana2 = make_session_id()

        store.create_session(sid_plan, project_name="Plan Project", mode="planning")
        store.create_session(sid_ana1, project_name="Analysis 1", mode="analysis")
        store.create_session(sid_ana2, project_name="Analysis 2", mode="analysis")

        result = store.list_analysis_sessions()
        assert len(result) == 2
        session_ids = {r["session_id"] for r in result}
        assert sid_ana1 in session_ids
        assert sid_ana2 in session_ids
        assert sid_plan not in session_ids

    def test_ordered_by_last_modified_desc(self, store: SessionStore):
        sid1 = make_session_id()
        sid2 = make_session_id()

        store.create_session(sid1, project_name="First", mode="analysis")
        store.create_session(sid2, project_name="Second", mode="analysis")

        # Update sid1 to make it more recent via save_state (requires messages key)
        store.save_state(sid1, {"messages": []})

        result = store.list_analysis_sessions()
        assert len(result) == 2
        # sid1 should be first (most recently modified)
        assert result[0]["session_id"] == sid1

    def test_returns_expected_keys(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="Test", mode="analysis")

        result = store.list_analysis_sessions()
        assert len(result) == 1
        row = result[0]
        assert "session_id" in row
        assert "project_name" in row
        assert "created_at" in row
        assert "last_modified" in row
        assert "last_node_completed" in row

    def test_no_planning_sessions_returned(self, store: SessionStore):
        """Even with many planning sessions, list_analysis_sessions returns empty."""
        for i in range(5):
            store.create_session(make_session_id(), project_name=f"Plan {i}", mode="planning")

        result = store.list_analysis_sessions()
        assert result == []


# ---------------------------------------------------------------------------
# Analysis session state round-trip
# ---------------------------------------------------------------------------


class TestAnalysisSessionStateRoundTrip:
    def test_save_and_load_analysis_state(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, project_name="Platform", mode="analysis")

        # save_state persists the full graph state
        store.save_state(sid, {"messages": [], "instructions": "Team velocity is 23.5"})

        meta = store.get_session(sid)
        assert meta is not None
        # State should be saved as JSON
        assert meta["session_state_raw"] != ""

    def test_update_preserves_mode(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, mode="analysis")
        store.save_state(sid, {"messages": []})

        row = store._conn.execute("SELECT session_mode FROM sessions_meta WHERE session_id = ?", (sid,)).fetchone()
        assert row[0] == "analysis"

    def test_delete_analysis_session(self, store: SessionStore):
        sid = make_session_id()
        store.create_session(sid, mode="analysis")

        store.delete_session(sid)
        meta = store.get_session(sid)
        assert meta is None

    def test_delete_does_not_affect_other_modes(self, store: SessionStore):
        sid_ana = make_session_id()
        sid_plan = make_session_id()
        store.create_session(sid_ana, mode="analysis")
        store.create_session(sid_plan, mode="planning")

        store.delete_session(sid_ana)

        assert store.get_session(sid_plan) is not None
        assert store.get_session(sid_ana) is None


# ---------------------------------------------------------------------------
# Migration from v3 to v4
# ---------------------------------------------------------------------------


class TestMigrationV3ToV4:
    def test_migration_adds_session_mode_column(self, tmp_path: Path):
        """Simulate a v3 database and verify migration adds session_mode."""
        import sqlite3

        db_path = tmp_path / "v3.db"
        conn = sqlite3.connect(str(db_path))
        # Create v3 schema (no session_mode column)
        conn.execute(
            """CREATE TABLE sessions_meta (
                session_id TEXT PRIMARY KEY,
                project_name TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                last_modified TEXT DEFAULT '',
                last_node_completed TEXT DEFAULT '',
                session_state TEXT DEFAULT ''
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_info (
                schema_version INT NOT NULL
            )"""
        )
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (3)")
        # Also create team_profiles table (v3 requirement)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS team_profiles (
                team_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                examples_json TEXT DEFAULT '{}',
                updated_at TEXT NOT NULL
            )"""
        )
        # Insert a pre-existing session (should get default mode='planning')
        conn.execute("INSERT INTO sessions_meta (session_id, project_name) VALUES ('old-session', 'OldProject')")
        conn.commit()
        conn.close()

        # Open with SessionStore — should trigger migration
        with SessionStore(db_path) as store:
            # Old session should have default mode
            row = store._conn.execute(
                "SELECT session_mode FROM sessions_meta WHERE session_id = 'old-session'"
            ).fetchone()
            assert row is not None
            assert row[0] == "planning"

            # Should be able to create analysis sessions
            store.create_session("new-ana", mode="analysis")
            result = store.list_analysis_sessions()
            assert len(result) == 1

    def test_existing_sessions_default_to_planning(self, tmp_path: Path):
        """Pre-existing sessions in a v3 DB should default to 'planning' mode."""
        import sqlite3

        db_path = tmp_path / "v3b.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE sessions_meta (
                session_id TEXT PRIMARY KEY,
                project_name TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                last_modified TEXT DEFAULT '',
                last_node_completed TEXT DEFAULT '',
                session_state TEXT DEFAULT ''
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_info (
                schema_version INT NOT NULL
            )"""
        )
        conn.execute("INSERT INTO schema_info (schema_version) VALUES (3)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS team_profiles (
                team_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                examples_json TEXT DEFAULT '{}',
                updated_at TEXT NOT NULL
            )"""
        )
        for i in range(3):
            conn.execute(f"INSERT INTO sessions_meta (session_id, project_name) VALUES ('sess-{i}', 'Proj{i}')")
        conn.commit()
        conn.close()

        with SessionStore(db_path) as store:
            # None should show up as analysis sessions
            analysis = store.list_analysis_sessions()
            assert len(analysis) == 0


# ---------------------------------------------------------------------------
# Schema v12 — local-model performance columns on token_usage
# ---------------------------------------------------------------------------


def _make_v9_db(db_path: Path) -> None:
    """Build a minimal v9 DB with the pre-perf token_usage shape (no perf cols)."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS schema_info (schema_version INT NOT NULL)")
    conn.execute("INSERT INTO schema_info (schema_version) VALUES (9)")
    conn.execute(
        """CREATE TABLE token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            input_tokens INT NOT NULL DEFAULT 0,
            output_tokens INT NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT ''
        )"""
    )
    conn.execute(
        "INSERT INTO token_usage (timestamp, input_tokens, output_tokens, model, provider) "
        "VALUES ('2026-01-01T00:00:00', 100, 50, 'claude-sonnet-4-6', 'anthropic')"
    )
    conn.commit()
    conn.close()


class TestMigrationV12:
    def test_migration_adds_perf_columns(self, tmp_path: Path):
        db_path = tmp_path / "v9.db"
        _make_v9_db(db_path)
        with SessionStore(db_path) as store:
            cols = {row[1] for row in store._conn.execute("PRAGMA table_info(token_usage)").fetchall()}
            for expected in ("duration_ms", "eval_duration_ms", "load_duration_ms", "tokens_per_sec"):
                assert expected in cols
            # Version stamped to current.
            ver = store._conn.execute("SELECT schema_version FROM schema_info").fetchone()[0]
            assert ver == CURRENT_SCHEMA_VERSION

    def test_old_rows_still_readable(self, tmp_path: Path):
        db_path = tmp_path / "v9b.db"
        _make_v9_db(db_path)
        with SessionStore(db_path) as store:
            usage = store.get_lifetime_usage()
            assert usage["input_tokens"] == 100
            assert usage["output_tokens"] == 50
            # A pre-perf row has no timing → excluded from the perf summary.
            assert store.get_local_perf_summary() == {}


class TestLocalPerfSummary:
    def test_empty_when_no_local_rows(self, store: SessionStore):
        store.record_token_usage(100, 50, model="claude-sonnet-4-6", provider="anthropic")
        assert store.get_local_perf_summary() == {}

    def test_aggregates_ollama_rows(self, store: SessionStore):
        store.record_token_usage(
            200,
            100,
            model="qwen3:8b",
            provider="ollama",
            duration_ms=2000.0,
            load_duration_ms=500.0,
            tokens_per_sec=40.0,
        )
        store.record_token_usage(
            180,
            90,
            model="qwen3:8b",
            provider="ollama",
            duration_ms=1000.0,
            load_duration_ms=100.0,
            tokens_per_sec=60.0,
        )
        # A cloud row must not pollute the local aggregates.
        store.record_token_usage(50, 25, model="claude-sonnet-4-6", provider="anthropic")
        summary = store.get_local_perf_summary()
        assert summary["calls"] == 2
        assert summary["avg_tps"] == 50.0
        assert summary["max_tps"] == 60.0
        assert summary["avg_duration_ms"] == 1500.0
        # Last call is the most recently inserted ollama row.
        assert summary["last"]["model"] == "qwen3:8b"
        assert summary["last"]["tps"] == 60.0


# ---------------------------------------------------------------------------
# Resume contract — save-on-generation persists each step, resumable until
# the flow is marked complete. Backs the immediate-save behaviour in
# ui/mode_select/__init__.py (_save_ana / _load_ana_session).
# ---------------------------------------------------------------------------


class TestAnalysisResumeContract:
    def test_sample_artifacts_round_trip(self, store: SessionStore):
        """Every preview artifact (incl. the sprint) survives save/load verbatim."""
        sid = make_session_id()
        store.create_session(sid, project_name="Platform", mode="analysis")
        state = {
            "messages": [],
            "instructions": "Team velocity is 23.5",
            "sample_epic": {"title": "Checkout revamp"},
            "sample_stories": [{"id": "S1", "story_points": 3}],
            "sample_tasks": [{"id": "T1"}],
            "sample_sprint": {"sprint_name": "Sprint 1", "total_points": 3},
            "last_page": "sprint",
        }
        store.save_state(sid, state)

        loaded = store.load_state(sid)
        assert loaded is not None
        assert loaded["last_page"] == "sprint"
        assert loaded["sample_epic"] == {"title": "Checkout revamp"}
        assert loaded["sample_sprint"] == {"sprint_name": "Sprint 1", "total_points": 3}

    def test_load_ana_session_resumes_incomplete(self, tmp_path: Path, monkeypatch):
        """A session saved mid-flow (last_page != complete) is returned for resume."""
        from yeaboi.ui import mode_select

        db = tmp_path / "sessions.db"
        monkeypatch.setattr(mode_select, "_ana_dbp", db)
        monkeypatch.setattr(mode_select, "_ana_sid", "")

        sid = make_session_id()
        with SessionStore(db) as store:
            store.create_session(sid, project_name="Platform", mode="analysis")
            store.save_state(sid, {"messages": [], "sample_epic": {"title": "E"}, "last_page": "epic"})

        resumed = mode_select._load_ana_session("Platform")
        assert resumed is not None
        assert resumed["last_page"] == "epic"
        # The resume target is latched so subsequent _save_ana calls hit the same row.
        assert mode_select._ana_sid == sid

    def test_load_ana_session_skips_complete(self, tmp_path: Path, monkeypatch):
        """A finished session (last_page == complete) is not offered for resume."""
        from yeaboi.ui import mode_select

        db = tmp_path / "sessions.db"
        monkeypatch.setattr(mode_select, "_ana_dbp", db)
        monkeypatch.setattr(mode_select, "_ana_sid", "")

        sid = make_session_id()
        with SessionStore(db) as store:
            store.create_session(sid, project_name="Platform", mode="analysis")
            store.save_state(sid, {"messages": [], "last_page": "complete"})

        assert mode_select._load_ana_session("Platform") is None


class TestMigrationV23:
    """Standup practice config reaches a database migrated ahead of StandupStore.

    Covers v24 (habit_ai_match) too: both migrations run off the same v20 base.
    """

    def _v20_db(self, db_path: Path) -> None:
        # A v20-era standup_config: everything up to automation_handling, and
        # nothing after it.
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """
            CREATE TABLE schema_info (schema_version INT NOT NULL);
            INSERT INTO schema_info VALUES (20);
            CREATE TABLE standup_config (
                session_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                time TEXT NOT NULL DEFAULT '10:00',
                automation_handling TEXT NOT NULL DEFAULT 'exclude',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO standup_config (session_id) VALUES ('s1');
            """
        )
        conn.commit()
        conn.close()

    def test_migration_adds_the_habit_columns(self, tmp_path: Path):
        db_path = tmp_path / "v20.db"
        self._v20_db(db_path)
        with SessionStore(db_path) as store:
            cols = {row[1] for row in store._conn.execute("PRAGMA table_info(standup_config)").fetchall()}
            assert {"habit_detection", "habit_rules", "habit_ai_match"} <= cols
            ver = store._conn.execute("SELECT schema_version FROM schema_info").fetchone()[0]
            assert ver == CURRENT_SCHEMA_VERSION

    def test_an_existing_row_defaults_to_on(self, tmp_path: Path):
        # Practices are on by default, so an existing team must not silently
        # come out of the migration with detection disabled.
        import sqlite3

        db_path = tmp_path / "v20b.db"
        self._v20_db(db_path)
        with SessionStore(db_path):
            pass
        conn = sqlite3.connect(str(db_path))
        row = conn.execute(
            "SELECT habit_detection, habit_rules, habit_ai_match FROM standup_config WHERE session_id = 's1'"
        ).fetchone()
        conn.close()
        assert row == ("on", "", "on")
