"""Unit tests for roadmap/store.RoadmapStore + schema v10/v11 migrations."""

import json

import pytest

from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject
from yeaboi.roadmap.ingest import RoadmapSource
from yeaboi.roadmap.store import RoadmapStore, _analysis_to_json, _dict_to_analysis, friendly_label


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


def _analysis():
    return RoadmapAnalysis(
        source_type="confluence",
        source_locator="12345",
        source_label="Q3 Roadmap",
        summary="Two initiatives.",
        projects=(
            RoadmapProject(
                name="SSO",
                description="Add single sign-on across products.",
                size="large",
                rationale="Multi-sprint epic, start first.",
                priority=1,
                themes=("Security",),
                quarter="Q3 2026",
            ),
            RoadmapProject(name="Fix onboarding email", size="small", priority=2),
        ),
        warnings=("truncated",),
        generated_at="2026-07-18T09:00:00",
    )


class TestRoundTrip:
    def test_json_round_trip_preserves_tuples(self):
        original = _analysis()
        restored = _dict_to_analysis(json.loads(_analysis_to_json(original)))
        assert restored == original
        assert isinstance(restored.projects, tuple)
        assert isinstance(restored.projects[0].themes, tuple)

    def test_missing_keys_default(self):
        """Legacy JSON (older version, missing keys) still deserializes."""
        restored = _dict_to_analysis({"source_type": "local", "projects": [{"name": "X"}]})
        assert restored.source_type == "local"
        assert restored.projects[0].name == "X"
        assert restored.projects[0].size == "small"
        assert restored.projects[0].themes == ()
        assert restored.warnings == ()

    def test_non_dict_projects_skipped(self):
        restored = _dict_to_analysis({"projects": ["garbage", {"name": "ok"}]})
        assert len(restored.projects) == 1
        assert restored.projects[0].name == "ok"


class TestConfig:
    def test_save_and_load_round_trip(self, db_path):
        with RoadmapStore(db_path) as store:
            assert store.load_config() is None
            source = RoadmapSource(source_type="notion", locator="abc123", label="Roadmap")
            store.save_config(source)
            assert store.load_config() == source

    def test_singleton_overwrite(self, db_path):
        with RoadmapStore(db_path) as store:
            store.save_config(RoadmapSource(source_type="local", locator="/a.md", label="a.md"))
            store.save_config(RoadmapSource(source_type="confluence", locator="42", label="Q3"))
            loaded = store.load_config()
            assert loaded is not None and loaded.source_type == "confluence"
            count = store._conn.execute("SELECT COUNT(*) FROM roadmap_config").fetchone()[0]
            assert count == 1


class TestHistory:
    def test_record_and_get_latest(self, db_path):
        with RoadmapStore(db_path) as store:
            assert store.get_latest_analysis() is None
            store.record_run(_analysis())
            latest = store.get_latest_analysis()
            assert latest is not None and latest.summary == "Two initiatives."
            assert len(latest.projects) == 2

    def test_history_metadata(self, db_path):
        with RoadmapStore(db_path) as store:
            store.record_run(_analysis())
            hist = store.get_history()
            assert len(hist) == 1
            assert hist[0]["project_count"] == 2
            assert hist[0]["source_type"] == "confluence"


class TestSchemaMigration:
    def test_current_version_covers_roadmap(self):
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION

        # Roadmap tables landed at v11; later migrations only add.
        assert CURRENT_SCHEMA_VERSION >= 11

    def test_session_store_creates_roadmap_tables(self, db_path):
        import sqlite3

        from yeaboi.sessions import SessionStore

        with SessionStore(db_path):
            pass  # opening runs migrations up to the current version
        conn = sqlite3.connect(str(db_path))
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        version = conn.execute("SELECT schema_version FROM schema_info").fetchone()[0]
        conn.close()
        assert "roadmap_config" in names
        assert "roadmap_history" in names
        assert "roadmaps" in names
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION

        assert version == CURRENT_SCHEMA_VERSION

    def test_v9_database_migrates_without_data_loss(self, db_path):
        """A pre-roadmap (v9) DB gains the roadmap tables and keeps existing rows."""
        import sqlite3

        from yeaboi.sessions import SessionStore

        # Build a v9-shaped DB: open with the current store, then rewind the
        # version stamp and drop the roadmap tables to simulate an old file.
        with SessionStore(db_path):
            pass
        conn = sqlite3.connect(str(db_path))
        conn.execute("DROP TABLE roadmap_config")
        conn.execute("DROP TABLE roadmap_history")
        conn.execute("DROP TABLE roadmaps")
        conn.execute("UPDATE schema_info SET schema_version = 9")
        conn.execute(
            "INSERT INTO reporting_history (run_at, period, report_json) VALUES ('2026-07-01', 'Last sprint', '{}')"
        )
        conn.commit()
        conn.close()

        with SessionStore(db_path):
            pass  # v9 → v10 → v11 → … migrations run

        conn = sqlite3.connect(str(db_path))
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        version = conn.execute("SELECT schema_version FROM schema_info").fetchone()[0]
        kept = conn.execute("SELECT COUNT(*) FROM reporting_history").fetchone()[0]
        conn.close()
        assert "roadmap_config" in names and "roadmap_history" in names and "roadmaps" in names
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION

        assert version == CURRENT_SCHEMA_VERSION
        assert kept == 1

    def test_v10_database_seeds_roadmaps_row(self, db_path):
        """A v10 DB with a saved singleton source + analysis seeds one roadmaps row."""
        import sqlite3

        from yeaboi.sessions import SessionStore

        with SessionStore(db_path):
            pass
        conn = sqlite3.connect(str(db_path))
        conn.execute("DROP TABLE roadmaps")
        conn.execute("UPDATE schema_info SET schema_version = 10")
        conn.execute(
            "INSERT INTO roadmap_config (id, source_type, source_locator, source_label, updated_at) "
            "VALUES (1, 'confluence', '12345', 'Q3 Roadmap', '2026-07-18T09:00:00')"
        )
        analysis_json = _analysis_to_json(_analysis())
        conn.execute(
            "INSERT INTO roadmap_history (run_at, source_type, source_locator, project_count, analysis_json) "
            "VALUES ('2026-07-18T09:00:00', 'confluence', '12345', 2, ?)",
            (analysis_json,),
        )
        conn.commit()
        conn.close()

        with SessionStore(db_path):
            pass  # v10 → v11 migration seeds the row

        with RoadmapStore(db_path) as store:
            rows = store.list_roadmaps()
            assert len(rows) == 1
            assert rows[0]["label"] == "Q3 Roadmap"
            assert rows[0]["source_type"] == "confluence"
            assert rows[0]["project_count"] == 2
            assert rows[0]["analyzed"] is True
            row = store.get_roadmap(rows[0]["id"])
            assert row is not None and row["analysis"] is not None
            assert row["analysis"].summary == "Two initiatives."

    def test_v10_empty_config_seeds_nothing(self, db_path):
        """A v10 DB that never configured a roadmap migrates to an empty list."""
        import sqlite3

        from yeaboi.sessions import SessionStore

        with SessionStore(db_path):
            pass
        conn = sqlite3.connect(str(db_path))
        conn.execute("DROP TABLE roadmaps")
        conn.execute("UPDATE schema_info SET schema_version = 10")
        conn.commit()
        conn.close()

        with SessionStore(db_path):
            pass

        with RoadmapStore(db_path) as store:
            assert store.list_roadmaps() == []


class TestRoadmaps:
    def _source(self):
        return RoadmapSource(source_type="local", locator="/tmp/q3.md", label="q3.md")

    def test_save_new_and_list(self, db_path):
        with RoadmapStore(db_path) as store:
            rid = store.save_roadmap(self._source(), _analysis())
            assert rid > 0
            rows = store.list_roadmaps()
            assert len(rows) == 1
            assert rows[0]["id"] == rid
            assert rows[0]["label"] == "Q3"  # friendly_label strips the extension + uppercases the quarter
            assert rows[0]["project_count"] == 2
            assert rows[0]["analyzed"] is True
            assert rows[0]["created_at"] and rows[0]["updated_at"]

    def test_update_in_place(self, db_path):
        """Re-analyze updates the same row: no new row, updated_at bumps, DESC order."""
        with RoadmapStore(db_path) as store:
            rid_old = store.save_roadmap(self._source(), _analysis())
            store._conn.execute("UPDATE roadmaps SET updated_at = '2000-01-01' WHERE id = ?", (rid_old,))
            rid_new = store.save_roadmap(RoadmapSource(source_type="notion", locator="abc", label="Notion Q3"), None)
            rid_upd = store.save_roadmap(self._source(), _analysis(), roadmap_id=rid_old)
            assert rid_upd == rid_old
            rows = store.list_roadmaps()
            assert len(rows) == 2
            assert rows[0]["id"] == rid_old  # freshest updated_at first
            assert rows[1]["id"] == rid_new

    def test_get_roadmap_round_trips_analysis(self, db_path):
        with RoadmapStore(db_path) as store:
            rid = store.save_roadmap(self._source(), _analysis())
            row = store.get_roadmap(rid)
            assert row is not None
            assert row["analysis"] == _analysis()
            assert isinstance(row["analysis"].projects, tuple)
            assert row["source"] == self._source()

    def test_get_roadmap_missing_id(self, db_path):
        with RoadmapStore(db_path) as store:
            assert store.get_roadmap(999) is None

    def test_never_analyzed_roadmap(self, db_path):
        """save_roadmap(source, None) stores the source with no analysis."""
        with RoadmapStore(db_path) as store:
            rid = store.save_roadmap(self._source(), None)
            rows = store.list_roadmaps()
            assert rows[0]["project_count"] == 0
            assert rows[0]["analyzed"] is False
            row = store.get_roadmap(rid)
            assert row is not None and row["analysis"] is None
            assert row["source"] == self._source()

    def test_corrupt_analysis_json(self, db_path):
        """A corrupt analysis payload returns the row with analysis=None, never raises."""
        with RoadmapStore(db_path) as store:
            rid = store.save_roadmap(self._source(), _analysis())
            store._conn.execute("UPDATE roadmaps SET analysis_json = '{not json' WHERE id = ?", (rid,))
            row = store.get_roadmap(rid)
            assert row is not None and row["analysis"] is None

    def test_delete_roadmap(self, db_path):
        with RoadmapStore(db_path) as store:
            rid = store.save_roadmap(self._source(), _analysis())
            store.delete_roadmap(rid)
            assert store.list_roadmaps() == []
            store.delete_roadmap(999)  # missing id is a no-op


class TestFriendlyLabel:
    """friendly_label humanizes file-ish labels but leaves real titles alone."""

    def test_filename_humanized(self):
        assert friendly_label("q3-2026-roadmap.md") == "Q3 2026 Roadmap"

    def test_path_reduced_to_name(self):
        assert friendly_label("/Users/me/docs/q1_2027_plan.pdf") == "Q1 2027 Plan"

    def test_human_title_passes_through(self):
        assert friendly_label("Q3 2026 Product Roadmap") == "Q3 2026 Product Roadmap"

    def test_underscores_and_mixed_case(self):
        assert friendly_label("platform_roadmap.docx") == "Platform Roadmap"

    def test_unknown_extension_kept(self):
        # .xlsx isn't a roadmap suffix — the dot segment is treated as part of the name
        assert friendly_label("roadmap.xlsx") != ""

    def test_empty_and_whitespace(self):
        assert friendly_label("") == ""
        assert friendly_label("   ") == ""

    def test_idempotent(self):
        once = friendly_label("q3-2026-roadmap.md")
        assert friendly_label(once) == once

    def test_stored_raw_label_displays_friendly(self, db_path):
        """A row saved before the humanizer existed still lists with a friendly label."""
        with RoadmapStore(db_path) as store:
            rid = store.save_roadmap(RoadmapSource(source_type="local", locator="/tmp/x.md", label="x.md"), _analysis())
            store._conn.execute("UPDATE roadmaps SET label = 'q3-2026-roadmap.md' WHERE id = ?", (rid,))
            assert store.list_roadmaps()[0]["label"] == "Q3 2026 Roadmap"
            row = store.get_roadmap(rid)
            assert row is not None and row["label"] == "Q3 2026 Roadmap"


class TestProvenanceSelfHeal:
    """A roadmap_history missing the v21 provenance columns heals on store open.

    The v21 schema-version collision could leave a shared DB stamped past 21
    without origin/edited_from_id, and the CLI and MCP tools open this store
    without ever constructing a SessionStore (whose v26 migration is the other
    repair path).
    """

    def test_pre_v21_table_heals_on_open(self, db_path):
        import sqlite3

        RoadmapStore(db_path).close()
        conn = sqlite3.connect(str(db_path))
        keep = ", ".join(
            r[1] for r in conn.execute("PRAGMA table_info(roadmap_history)") if r[1] not in ("origin", "edited_from_id")
        )
        conn.executescript(
            f"CREATE TABLE pre AS SELECT {keep} FROM roadmap_history;"
            "DROP TABLE roadmap_history;"
            "ALTER TABLE pre RENAME TO roadmap_history;"
        )
        conn.close()
        with RoadmapStore(db_path) as store:
            cols = {r[1] for r in store._conn.execute("PRAGMA table_info(roadmap_history)")}
        assert {"origin", "edited_from_id"} <= cols
