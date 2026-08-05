"""Tests for the merged "Your projects" rows + roadmap export wiring.

_load_planning_rows() merges planning projects (persistence) with saved
roadmaps (RoadmapStore) into one ProjectSummary list; _export_roadmap_via_picker
routes roadmap export through the shared destination picker.
"""

import pytest

import yeaboi.ui.mode_select as mode_select
from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject
from yeaboi.roadmap.ingest import RoadmapSource
from yeaboi.roadmap.store import RoadmapStore
from yeaboi.ui.mode_select import ProjectSummary, _export_roadmap_via_picker, _load_planning_rows


def _analysis():
    return RoadmapAnalysis(
        source_type="local",
        source_locator="/tmp/q3.md",
        source_label="q3.md",
        summary="One initiative.",
        projects=(RoadmapProject(name="SSO", description="Add SSO.", size="large", priority=1),),
        generated_at="2026-07-18T09:00:00",
    )


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr(mode_select, "_ana_dbp", path)
    return path


def _seed_roadmap(db_path, analysis=None, label="Q3 2026 Roadmap"):
    source = RoadmapSource(source_type="local", locator="/tmp/q3.md", label=label)
    with RoadmapStore(db_path) as store:
        return store.save_roadmap(source, analysis if analysis is not None else _analysis())


class _FakeConsole:
    size = (100, 30)


class _FakeLive:
    def update(self, _panel):
        pass


def _read_key(timeout=None):
    return "enter"


class TestLoadPlanningRows:
    def test_merges_projects_and_roadmaps(self, db_path, monkeypatch):
        _seed_roadmap(db_path)
        monkeypatch.setattr(
            "yeaboi.persistence.load_projects",
            lambda: [ProjectSummary(name="Billing", id="p1", updated_at="2026-07-19T00:00:00")],
        )
        rows = _load_planning_rows()
        assert {r.kind for r in rows} == {"project", "roadmap"}
        assert len(rows) == 2
        roadmap = next(r for r in rows if r.kind == "roadmap")
        assert roadmap.roadmap_id > 0
        assert "candidate project" in roadmap.created
        assert "analyzed" in roadmap.created
        assert roadmap.name  # friendly label, non-empty

    def test_sorted_newest_first(self, db_path, monkeypatch):
        _seed_roadmap(db_path)  # saved "now" — newest
        monkeypatch.setattr(
            "yeaboi.persistence.load_projects",
            lambda: [ProjectSummary(name="Old project", id="p1", updated_at="2000-01-01T00:00:00")],
        )
        rows = _load_planning_rows()
        assert rows[0].kind == "roadmap"
        assert rows[-1].name == "Old project"

    def test_not_analyzed_meta(self, db_path, monkeypatch):
        # A roadmap row without an analysis (e.g. seeded from a v10 config DB).
        source = RoadmapSource(source_type="local", locator="/tmp/q3.md", label="q3.md")
        with RoadmapStore(db_path) as store:
            store.save_roadmap(source, None)
        monkeypatch.setattr("yeaboi.persistence.load_projects", lambda: [])
        rows = _load_planning_rows()
        assert len(rows) == 1
        assert "not analyzed yet" in rows[0].created

    def test_store_failure_degrades_to_projects_only(self, db_path, monkeypatch):
        monkeypatch.setattr("yeaboi.persistence.load_projects", lambda: [ProjectSummary(name="Only project", id="p1")])
        monkeypatch.setattr(
            "yeaboi.roadmap.store.RoadmapStore.__enter__", lambda self: (_ for _ in ()).throw(OSError("boom"))
        )
        rows = _load_planning_rows()
        assert [r.name for r in rows] == ["Only project"]

    def test_empty_everything(self, db_path, monkeypatch):
        monkeypatch.setattr("yeaboi.persistence.load_projects", lambda: [])
        assert _load_planning_rows() == []


class TestExportRoadmapViaPicker:
    def _export(self, roadmap_id):
        return _export_roadmap_via_picker(_FakeConsole(), _FakeLive(), _read_key, 0.01, True, roadmap_id=roadmap_id)

    def test_files_destination_exports(self, db_path, monkeypatch, tmp_path):
        import yeaboi.paths as paths

        monkeypatch.setattr(paths, "ROADMAP_EXPORTS_DIR", tmp_path / "roadmap")
        rid = _seed_roadmap(db_path)
        monkeypatch.setattr(mode_select, "_pick_dest", lambda *a, **k: "files")
        msg = self._export(rid)
        assert msg is not None
        assert "HTML" in msg and "MD" in msg

    def test_notion_destination_publishes(self, db_path, monkeypatch):
        rid = _seed_roadmap(db_path, label="Q3 2026 Roadmap")
        monkeypatch.setattr(mode_select, "_pick_dest", lambda *a, **k: "notion")
        captured = {}

        def _fake_publish(dest, *, title, markdown):
            captured.update(dest=dest, title=title, markdown=markdown)

            class _R:
                ok = True  # the real PublishResult carries ok — the picker reads it
                message = "Published."

            return _R()

        monkeypatch.setattr("yeaboi.export_targets.publish_markdown", _fake_publish)
        msg = self._export(rid)
        assert msg == "Published."
        assert captured["dest"] == "notion"
        assert captured["title"].startswith("Roadmap — ")
        assert "SSO" in captured["markdown"]

    def test_unanalyzed_roadmap_message(self, db_path, monkeypatch):
        source = RoadmapSource(source_type="local", locator="/tmp/q3.md", label="q3.md")
        with RoadmapStore(db_path) as store:
            rid = store.save_roadmap(source, None)
        monkeypatch.setattr(mode_select, "_pick_dest", lambda *a, **k: "files")
        assert self._export(rid) == "Analyze this roadmap before exporting."

    def test_missing_roadmap_message(self, db_path):
        assert self._export(9999) == "Roadmap not found."

    def test_picker_cancel_returns_none(self, db_path, monkeypatch):
        rid = _seed_roadmap(db_path)
        monkeypatch.setattr(mode_select, "_pick_dest", lambda *a, **k: None)
        assert self._export(rid) is None
