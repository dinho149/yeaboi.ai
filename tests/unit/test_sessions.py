"""Tests for sessions.py — SessionStore file hardening and schema bookkeeping.

(The store's behaviour is covered indirectly across the mode suites; this file
holds the direct SessionStore unit tests, starting with the security bits.)
"""

import os
import sqlite3
import stat

import pytest

from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore


class TestSessionStoreFilePermissions:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_db_file_restricted_on_connect(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        store = SessionStore(db_path)
        try:
            assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
        finally:
            store._conn.close()

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    def test_existing_lax_db_repaired(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        db_path.touch(mode=0o644)
        db_path.chmod(0o644)
        store = SessionStore(db_path)
        try:
            assert stat.S_IMODE(db_path.stat().st_mode) == 0o600
        finally:
            store._conn.close()


class TestSchemaInfoSingleRow:
    """schema_info is a single-row table only by convention — opens must enforce it.

    Concurrent first-opens (TUI + MCP server + scheduler on the shared DB) race
    the stamp INSERT, leaving duplicate rows and making the version read
    arbitrary — one observed DB held 37 rows. Every open now dedupes to the
    single highest-version row.
    """

    def _rows(self, db_path):
        conn = sqlite3.connect(str(db_path))
        try:
            return [r[0] for r in conn.execute("SELECT schema_version FROM schema_info")]
        finally:
            conn.close()

    def _insert_rows(self, db_path, versions):
        conn = sqlite3.connect(str(db_path))
        for v in versions:
            conn.execute("INSERT INTO schema_info (schema_version) VALUES (?)", (v,))
        conn.commit()
        conn.close()

    def test_duplicate_rows_are_deduped_on_open(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        SessionStore(db_path).close()
        self._insert_rows(db_path, [CURRENT_SCHEMA_VERSION] * 30 + [1, 20, 25])

        store = SessionStore(db_path)
        try:
            assert not store.schema_mismatch
        finally:
            store.close()
        assert self._rows(db_path) == [CURRENT_SCHEMA_VERSION]

    def test_dedupe_keeps_the_newest_stamp(self, tmp_path):
        # A row stamped by a newer build must survive the dedupe, so the
        # newer-DB-older-code warning still fires and nothing downgrades it.
        db_path = tmp_path / "sessions.db"
        SessionStore(db_path).close()
        self._insert_rows(db_path, [CURRENT_SCHEMA_VERSION + 1, 3])

        store = SessionStore(db_path)
        try:
            assert store.schema_mismatch
        finally:
            store.close()
        assert self._rows(db_path) == [CURRENT_SCHEMA_VERSION + 1]

    def test_single_row_open_is_untouched(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        SessionStore(db_path).close()
        SessionStore(db_path).close()
        assert self._rows(db_path) == [CURRENT_SCHEMA_VERSION]


class TestArchitectureRoundTrip:
    """ArchitectureDecision and full Task fields survive save/load."""

    def _analysis_with_architecture(self):
        from yeaboi.agent.state import ArchitectureDecision, ArchitectureOption

        return ArchitectureDecision(
            options=(
                ArchitectureOption(name="Monolith", summary="s", pros=("a",), cons=("b",)),
                ArchitectureOption(name="Serverless", summary="s2"),
            ),
            chosen="Monolith",
            confidence="medium",
            rationale="why",
        )

    def test_architecture_round_trips(self, tmp_path):
        from tests._node_helpers import make_dummy_analysis
        from yeaboi.sessions import SessionStore

        analysis = make_dummy_analysis(architecture=self._analysis_with_architecture())
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("s1", "Test")
            store.save_state("s1", {"messages": [], "project_analysis": analysis})
            loaded = store.load_state("s1")
        arch = loaded["project_analysis"].architecture
        assert arch is not None
        assert arch.chosen == "Monolith"
        assert arch.options[0].pros == ("a",)

    def test_old_analysis_without_architecture_loads(self, tmp_path):
        from tests._node_helpers import make_dummy_analysis
        from yeaboi.sessions import SessionStore

        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("s1", "Test")
            store.save_state("s1", {"messages": [], "project_analysis": make_dummy_analysis()})
            loaded = store.load_state("s1")
        assert loaded["project_analysis"].architecture is None

    def test_task_label_and_plans_survive_resume(self, tmp_path):
        # Regression: _dict_to_task used to drop label/test_plan/ai_prompt.
        from yeaboi.agent.state import Task, TaskLabel
        from yeaboi.sessions import SessionStore

        task = Task(
            id="T-1",
            story_id="US-1",
            title="[Spike] Validate architecture: X",
            description="d",
            label=TaskLabel.SPIKE,
            test_plan="",
            ai_prompt="research prompt",
        )
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("s1", "Test")
            store.save_state("s1", {"messages": [], "tasks": [task]})
            loaded = store.load_state("s1")
        restored = loaded["tasks"][0]
        assert restored.label is TaskLabel.SPIKE
        assert restored.ai_prompt == "research prompt"

    def test_old_task_dict_without_new_keys_loads(self):
        from yeaboi.agent.state import TaskLabel
        from yeaboi.sessions import _dict_to_task

        task = _dict_to_task({"id": "T-1", "story_id": "US-1", "title": "t", "description": "d"})
        assert task.label is TaskLabel.CODE
        assert task.test_plan == ""


class TestProjectsMigration:
    """Migration v31 — the projects table and sessions_meta.project_id."""

    def _v30_db(self, tmp_path):
        db = tmp_path / "sessions.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """CREATE TABLE sessions_meta (
                   session_id          TEXT PRIMARY KEY,
                   project_name        TEXT NOT NULL DEFAULT '',
                   created_at          TEXT NOT NULL,
                   last_modified       TEXT NOT NULL,
                   last_node_completed TEXT NOT NULL DEFAULT '',
                   session_state       TEXT NOT NULL DEFAULT '',
                   session_mode        TEXT NOT NULL DEFAULT 'planning'
               );
               CREATE TABLE schema_info (schema_version INT NOT NULL);"""
        )
        conn.execute("INSERT INTO schema_info VALUES (30)")
        conn.execute("INSERT INTO sessions_meta (session_id, created_at, last_modified) VALUES ('old-1', 't', 't')")
        conn.commit()
        conn.close()
        return db

    def _columns(self, db, table):
        conn = sqlite3.connect(str(db))
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()

    def test_v30_db_gains_table_column_and_index(self, tmp_path):
        db = self._v30_db(tmp_path)
        with SessionStore(db) as store:
            assert store.schema_mismatch is False
        assert "project_id" in self._columns(db, "sessions_meta")
        conn = sqlite3.connect(str(db))
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            assert "projects" in names
            assert "idx_sessions_meta_project" in names
            # Pre-existing rows read as unscoped, not NULL.
            (pid,) = conn.execute("SELECT project_id FROM sessions_meta WHERE session_id = 'old-1'").fetchone()
            assert pid == ""
        finally:
            conn.close()

    def test_reopen_is_idempotent(self, tmp_path):
        db = self._v30_db(tmp_path)
        SessionStore(db).close()
        with SessionStore(db) as store:
            assert store.schema_mismatch is False


class TestWeeklyReviewMigration:
    """Migration v32 — the weekly_review_history table."""

    def _v31_db(self, tmp_path):
        db = tmp_path / "sessions.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(
            """CREATE TABLE sessions_meta (
                   session_id          TEXT PRIMARY KEY,
                   project_name        TEXT NOT NULL DEFAULT '',
                   created_at          TEXT NOT NULL,
                   last_modified       TEXT NOT NULL,
                   last_node_completed TEXT NOT NULL DEFAULT '',
                   session_state       TEXT NOT NULL DEFAULT '',
                   session_mode        TEXT NOT NULL DEFAULT 'planning',
                   project_id          TEXT NOT NULL DEFAULT ''
               );
               CREATE TABLE schema_info (schema_version INT NOT NULL);"""
        )
        conn.execute("INSERT INTO schema_info VALUES (31)")
        conn.commit()
        conn.close()
        return db

    def test_v31_db_gains_the_table(self, tmp_path):
        db = self._v31_db(tmp_path)
        with SessionStore(db) as store:
            assert store.schema_mismatch is False
        conn = sqlite3.connect(str(db))
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            assert "weekly_review_history" in names
            (version,) = conn.execute("SELECT schema_version FROM schema_info").fetchone()
            assert version == CURRENT_SCHEMA_VERSION
        finally:
            conn.close()

    def test_reopen_is_idempotent(self, tmp_path):
        db = self._v31_db(tmp_path)
        SessionStore(db).close()
        with SessionStore(db) as store:
            assert store.schema_mismatch is False


class TestSessionProjectLink:
    def test_create_session_defaults_to_unscoped(self, tmp_path):
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("s1", "Test")
            assert store.session_project_id("s1") == ""

    def test_create_session_persists_project_id(self, tmp_path):
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("s1", "Test", project_id="proj-11112222")
            assert store.session_project_id("s1") == "proj-11112222"

    def test_set_session_project_links_and_unlinks(self, tmp_path):
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("s1", "Test")
            store.set_session_project("s1", "proj-11112222")
            assert store.session_project_id("s1") == "proj-11112222"
            store.set_session_project("s1", "")
            assert store.session_project_id("s1") == ""

    def test_unknown_session_reads_as_unscoped(self, tmp_path):
        with SessionStore(tmp_path / "sessions.db") as store:
            assert store.session_project_id("nope") == ""

    def test_session_ids_for_project_filters_and_orders(self, tmp_path):
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("plan-a", project_id="proj-11112222")
            store.create_session("analysis-a", mode="analysis", project_id="proj-11112222")
            store.create_session("plan-other", project_id="proj-33334444")
            store.create_session("plan-unscoped")
            # Distinct timestamps so the newest-first ordering is decidable.
            for i, sid in enumerate(("plan-a", "analysis-a")):
                store._conn.execute(
                    "UPDATE sessions_meta SET last_modified = ? WHERE session_id = ?",
                    (f"2026-08-0{i + 1}T00:00:00+00:00", sid),
                )
            assert store.session_ids_for_project("proj-11112222") == ["analysis-a", "plan-a"]
            assert store.session_ids_for_project("proj-11112222", mode="planning") == ["plan-a"]
            assert store.session_ids_for_project("proj-99990000") == []


class TestPruneSparesProjects:
    def test_only_unscoped_stale_sessions_are_pruned(self, tmp_path):
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("stale-unscoped")
            store.create_session("stale-linked", project_id="proj-11112222")
            store.create_session("fresh-unscoped")
            for sid in ("stale-unscoped", "stale-linked"):
                store._conn.execute(
                    "UPDATE sessions_meta SET last_modified = ? WHERE session_id = ?",
                    ("2020-01-01T00:00:00+00:00", sid),
                )
            assert store.prune_old_sessions(30) == 1
            remaining = {row["session_id"] for row in store.list_sessions()}
            assert remaining == {"stale-linked", "fresh-unscoped"}


class TestProjectIdStateRoundTrip:
    def test_project_id_survives_save_and_load(self, tmp_path):
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("s1", "Test", project_id="proj-11112222")
            store.save_state("s1", {"messages": [], "project_id": "proj-11112222"})
            loaded = store.load_state("s1")
        assert loaded["project_id"] == "proj-11112222"


class TestContextDepsStateRoundTrip:
    def test_solo_survives_save_and_load(self, tmp_path):
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("s1", "Test")
            store.save_state("s1", {"messages": [], "solo": True})
            loaded = store.load_state("s1")
        assert loaded["solo"] is True

    def test_context_deps_survives_save_and_load(self, tmp_path):
        with SessionStore(tmp_path / "sessions.db") as store:
            store.create_session("s1", "Test")
            store.save_state("s1", {"messages": [], "context_deps": '["retro", "plan"]'})
            loaded = store.load_state("s1")
        assert loaded["context_deps"] == '["retro", "plan"]'


class TestListSessionsFilters:
    """The additive kwargs the cross-mode recent list reads through."""

    def _seed(self, path):
        with SessionStore(path) as store:
            store.create_session("p1", "Apollo", project_id="proj-11112222")
            store.create_session("a1", "Apollo", mode="analysis")
            store.create_session("p2", "Borealis")
        return path

    def test_rows_carry_mode_and_project(self, tmp_path):
        with SessionStore(self._seed(tmp_path / "s.db")) as store:
            rows = {r["session_id"]: r for r in store.list_sessions()}
        assert rows["p1"]["session_mode"] == "planning" and rows["p1"]["project_id"] == "proj-11112222"
        assert rows["a1"]["session_mode"] == "analysis" and rows["a1"]["project_id"] == ""

    def test_project_and_mode_filters(self, tmp_path):
        with SessionStore(self._seed(tmp_path / "s.db")) as store:
            assert [r["session_id"] for r in store.list_sessions(project_id="proj-11112222")] == ["p1"]
            assert [r["session_id"] for r in store.list_sessions(mode="analysis")] == ["a1"]
            assert store.list_sessions(project_id="proj-11112222", mode="analysis") == []

    def test_limit_caps_and_zero_means_all(self, tmp_path):
        with SessionStore(self._seed(tmp_path / "s.db")) as store:
            assert len(store.list_sessions(limit=2)) == 2
            assert len(store.list_sessions(limit=0)) == 3
