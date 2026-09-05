"""projects.json remembers which engine project a planning run happened inside."""

from __future__ import annotations

import pytest

from yeaboi import persistence


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(persistence, "_PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr(persistence, "save_graph_state", lambda *a, **k: None)
    return tmp_path


class TestEngineProjectStamp:
    def test_a_scoped_run_carries_its_engine_project(self, home):
        persistence.save_project_snapshot("uuid-1", {"project_id": "proj-0000aaaa", "messages": []})
        (row,) = persistence.load_projects()
        assert row.id == "uuid-1" and row.engine_project_id == "proj-0000aaaa"

    def test_an_unscoped_run_is_blank(self, home):
        persistence.save_project_snapshot("uuid-2", {"messages": []})
        (row,) = persistence.load_projects()
        assert row.engine_project_id == ""

    def test_an_old_entry_without_the_key_still_loads(self, home):
        (home / "projects.json").write_text(
            '{"version": 1, "projects": [{"id": "old", "name": "Old", "updated_at": "2026-01-01"}]}'
        )
        (row,) = persistence.load_projects()
        assert row.name == "Old" and row.engine_project_id == ""
