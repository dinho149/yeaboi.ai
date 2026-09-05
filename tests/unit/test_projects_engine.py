"""Tests for projects/engine.py — the five headless entry points."""

import pytest

from yeaboi.projects import engine
from yeaboi.sessions import SessionStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


class TestCreateAndList:
    def test_create_returns_the_row(self, db_path):
        project = engine.create_project("Apollo", "the big one", db_path=db_path)
        assert project["name"] == "Apollo"
        assert project["project_id"].startswith("proj-")

    def test_blank_name_is_refused(self, db_path):
        with pytest.raises(ValueError, match="name is required"):
            engine.create_project("   ", db_path=db_path)

    def test_list_carries_session_counts(self, db_path):
        project = engine.create_project("Apollo", db_path=db_path)
        with SessionStore(db_path) as sessions:
            sessions.create_session("s1", project_id=project["project_id"])
            sessions.create_session("s2")
        rows = engine.list_projects(db_path=db_path)
        assert [(p["name"], p["session_count"]) for p in rows] == [("Apollo", 1)]


class TestGet:
    def test_returns_row_with_linked_sessions(self, db_path):
        project = engine.create_project("Apollo", db_path=db_path)
        with SessionStore(db_path) as sessions:
            sessions.create_session("s1", project_id=project["project_id"])
        got = engine.get_project(project["project_id"], db_path=db_path)
        assert got["name"] == "Apollo"
        assert got["session_ids"] == ["s1"]

    def test_unknown_project_raises(self, db_path):
        engine.create_project("Apollo", db_path=db_path)
        with pytest.raises(ValueError, match="unknown project"):
            engine.get_project("proj-00000000", db_path=db_path)


class TestLinkSession:
    def test_links_and_reads_back(self, db_path):
        project = engine.create_project("Apollo", db_path=db_path)
        with SessionStore(db_path) as sessions:
            sessions.create_session("s1")
        linked = engine.link_session(project["project_id"], "s1", db_path=db_path)
        assert linked == {"project_id": project["project_id"], "session_id": "s1"}
        with SessionStore(db_path) as sessions:
            assert sessions.session_project_id("s1") == project["project_id"]

    def test_unknown_project_raises(self, db_path):
        with SessionStore(db_path) as sessions:
            sessions.create_session("s1")
        with pytest.raises(ValueError, match="unknown project"):
            engine.link_session("proj-00000000", "s1", db_path=db_path)

    def test_unknown_session_raises(self, db_path):
        project = engine.create_project("Apollo", db_path=db_path)
        with pytest.raises(ValueError, match="unknown session"):
            engine.link_session(project["project_id"], "nope", db_path=db_path)


class TestSetDefaults:
    def test_merges_and_returns_settings(self, db_path):
        project = engine.create_project("Apollo", db_path=db_path)
        first = engine.set_project_defaults(
            project["project_id"], {"default_analysis_profile_id": "team-x"}, db_path=db_path
        )
        assert first["settings"] == {"default_analysis_profile_id": "team-x"}
        second = engine.set_project_defaults(project["project_id"], {"default_context_deps": []}, db_path=db_path)
        assert second["settings"] == {"default_analysis_profile_id": "team-x", "default_context_deps": []}

    def test_unknown_key_is_rejected(self, db_path):
        project = engine.create_project("Apollo", db_path=db_path)
        with pytest.raises(ValueError, match="unknown default"):
            engine.set_project_defaults(project["project_id"], {"default_analysis_profile": "typo"}, db_path=db_path)

    @pytest.mark.parametrize("bad", ["", "   ", "srv/app", "./app", "../app", "/", 42, None])
    def test_a_repo_path_that_is_not_absolute_is_rejected(self, db_path, bad):
        project = engine.create_project("Apollo", db_path=db_path)
        with pytest.raises(ValueError, match="repo_path"):
            engine.set_project_defaults(project["project_id"], {"repo_path": bad}, db_path=db_path)
        assert engine.get_project(project["project_id"], db_path=db_path)["settings"] == {}

    def test_a_repo_path_is_stored_normalised(self, db_path):
        project = engine.create_project("Apollo", db_path=db_path)
        result = engine.set_project_defaults(project["project_id"], {"repo_path": "/srv//app/../app/"}, db_path=db_path)
        assert result["settings"] == {"repo_path": "/srv/app"}

    def test_unknown_project_raises(self, db_path):
        with pytest.raises(ValueError, match="unknown project"):
            engine.set_project_defaults("proj-00000000", {}, db_path=db_path)
