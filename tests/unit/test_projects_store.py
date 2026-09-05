"""Tests for projects/store.py — the first-class project rows."""

import re
import sqlite3

import pytest

from yeaboi.projects.store import PROJECTS_SCHEMA, ProjectStore, new_project_id


@pytest.fixture
def store(tmp_path):
    with ProjectStore(tmp_path / "sessions.db") as s:
        yield s


class TestProjectId:
    def test_format(self):
        assert re.fullmatch(r"proj-[0-9a-f]{8}", new_project_id())

    def test_unique(self):
        assert len({new_project_id() for _ in range(50)}) == 50


class TestCrud:
    def test_create_and_get_round_trip(self, store):
        created = store.create("Apollo", "the big one")
        got = store.get(created["project_id"])
        assert got == created
        assert got["name"] == "Apollo"
        assert got["description"] == "the big one"
        assert got["settings"] == {}
        assert got["archived"] is False

    def test_get_missing_returns_none(self, store):
        assert store.get("proj-00000000") is None

    def test_list_orders_by_last_active(self, store):
        first = store.create("First")
        store.create("Second")
        store.touch(first["project_id"])
        # A touched project resurfaces ahead of a newer untouched one.
        listed = store.list_projects()
        assert [p["name"] for p in listed] == ["First", "Second"]
        assert listed[0]["last_active"] >= listed[1]["last_active"]

    def test_archive_hides_from_default_listing(self, store):
        keep = store.create("Keep")
        gone = store.create("Gone")
        assert store.archive(gone["project_id"]) is True
        assert [p["project_id"] for p in store.list_projects()] == [keep["project_id"]]
        everything = store.list_projects(include_archived=True)
        assert {p["project_id"] for p in everything} == {keep["project_id"], gone["project_id"]}

    def test_archive_missing_returns_false(self, store):
        assert store.archive("proj-00000000") is False


class TestSettings:
    def test_round_trip(self, store):
        project = store.create("Apollo")
        store.set_settings(project["project_id"], {"default_analysis_profile_id": "team-x"})
        assert store.get_settings(project["project_id"]) == {"default_analysis_profile_id": "team-x"}

    def test_unknown_keys_survive(self, store):
        # The store is a dumb JSON round-trip; key policy lives in the engine.
        project = store.create("Apollo")
        store.set_settings(project["project_id"], {"default_context_deps": ["retro"], "future_key": 1})
        assert store.get_settings(project["project_id"]) == {"default_context_deps": ["retro"], "future_key": 1}

    def test_missing_project_reads_empty(self, store):
        assert store.get_settings("proj-00000000") == {}

    def test_corrupt_settings_read_as_empty(self, store):
        project = store.create("Apollo")
        store._conn.execute(
            "UPDATE projects SET settings_json = 'not json' WHERE project_id = ?",
            (project["project_id"],),
        )
        assert store.get_settings(project["project_id"]) == {}


class TestLifecycle:
    def test_self_heals_on_a_pre_existing_db(self, tmp_path):
        # A DB that has never seen the projects table (opened by an older
        # SessionStore, say) gets it on store open.
        db = tmp_path / "sessions.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE other (x INT)")
        conn.commit()
        conn.close()
        with ProjectStore(db) as store:
            assert store.list_projects() == []

    def test_schema_constant_is_idempotent(self, tmp_path):
        db = tmp_path / "sessions.db"
        ProjectStore(db).close()
        conn = sqlite3.connect(str(db))
        conn.executescript(PROJECTS_SCHEMA)  # IF NOT EXISTS — must not raise
        conn.close()

    def test_double_close_is_safe(self, tmp_path):
        store = ProjectStore(tmp_path / "sessions.db")
        store.close()
        store.close()


class TestStatus:
    def test_new_projects_are_active(self, store):
        assert store.create("Apollo")["status"] == "active"
        assert store.list_projects()[0]["status"] == "active"

    def test_set_status_round_trips_and_bumps_last_active(self, store):
        created = store.create("Apollo")
        assert store.set_status(created["project_id"], "done") is True
        got = store.get(created["project_id"])
        assert got["status"] == "done"
        assert got["last_active"] >= created["last_active"]
        assert store.set_status(created["project_id"], "active") is True
        assert store.get(created["project_id"])["status"] == "active"

    def test_unknown_status_is_refused(self, store):
        created = store.create("Apollo")
        with pytest.raises(ValueError, match="status must be one of"):
            store.set_status(created["project_id"], "finished")

    def test_missing_project_returns_false(self, store):
        assert store.set_status("proj-00000000", "done") is False

    def test_a_pre_status_table_gains_the_column_on_open(self, tmp_path):
        db = tmp_path / "sessions.db"
        conn = sqlite3.connect(str(db))
        conn.executescript(PROJECTS_SCHEMA.replace(",\n    status        TEXT NOT NULL DEFAULT 'active'", ""))
        conn.execute("INSERT INTO projects VALUES ('proj-0000aaaa', 'Old', '', '{}', '2026-01-01', '2026-01-01', 0)")
        conn.commit()
        conn.close()
        with ProjectStore(db) as s:
            assert s.get("proj-0000aaaa")["status"] == "active"
            assert s.set_status("proj-0000aaaa", "done") is True
