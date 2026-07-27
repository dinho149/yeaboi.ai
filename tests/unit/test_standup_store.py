"""Unit tests for the Daily Standup SQLite store."""

import pytest

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.standup.store import StandupStore


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


def _make_report(**overrides) -> StandupReport:
    base = dict(
        date="2026-07-10",
        session_id="s1",
        sprint_name="Sprint 5",
        sprint_day=3,
        sprint_total_days=10,
        confidence_pct=82,
        confidence_label="At risk",
        member_updates=(MemberUpdate(name="Alice", summary="login"),),
        activity_counts=(("jira", 4),),
    )
    base.update(overrides)
    return StandupReport(**base)


class TestConfig:
    def test_save_and_load(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config(
                "s1",
                enabled=True,
                time="10:00",
                lead_minutes=15,
                weekdays="1-5",
                delivery_channels=["terminal", "slack"],
                repo_path="/tmp/repo",
            )
            cfg = store.load_config("s1")
        assert cfg is not None
        assert cfg["enabled"] is True
        assert cfg["time"] == "10:00"
        assert cfg["lead_minutes"] == 15
        assert cfg["delivery_channels"] == ["terminal", "slack"]
        assert cfg["repo_path"] == "/tmp/repo"

    def test_lead_minutes_defaults_to_10(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config("s1", enabled=True, time="10:00", weekdays="1-5", delivery_channels=["terminal"])
            cfg = store.load_config("s1")
        assert cfg["lead_minutes"] == 10

    def test_load_missing_returns_none(self, db_path):
        with StandupStore(db_path) as store:
            assert store.load_config("nope") is None

    def test_upsert_updates_existing(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config("s1", enabled=True, time="09:50", weekdays="1-5", delivery_channels=["terminal"])
            store.save_config("s1", enabled=False, time="10:00", weekdays="1-5", delivery_channels=["email"])
            cfg = store.load_config("s1")
        assert cfg["enabled"] is False
        assert cfg["time"] == "10:00"
        assert cfg["delivery_channels"] == ["email"]

    def test_corrupt_channels_falls_back(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config("s1", enabled=True, time="09:50", weekdays="1-5", delivery_channels=["terminal"])
            store._conn.execute("UPDATE standup_config SET delivery_channels = 'not json' WHERE session_id='s1'")
            cfg = store.load_config("s1")
        assert cfg["delivery_channels"] == ["terminal"]

    def test_my_aliases_round_trip(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config(
                "s1",
                enabled=True,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                my_aliases="omardin14, Omar N",
            )
            cfg = store.load_config("s1")
        assert cfg["my_aliases"] == "omardin14, Omar N"

    def test_my_aliases_defaults_empty(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config("s1", enabled=True, time="10:00", weekdays="1-5", delivery_channels=["terminal"])
            cfg = store.load_config("s1")
        assert cfg["my_aliases"] == ""

    def test_team_scope_round_trip(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config(
                "s1",
                enabled=True,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                tracker_sources=["jira", "azure_devops"],
                team_members=["Alice", "Bob"],
                roster_configured=True,
            )
            cfg = store.load_config("s1")
        assert cfg["tracker_sources"] == ["jira", "azure_devops"]
        assert cfg["team_members"] == ["Alice", "Bob"]
        assert cfg["roster_configured"] is True

    def test_team_scope_defaults_to_unconfigured_jira(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config("s1", enabled=True, time="10:00", weekdays="1-5", delivery_channels=["terminal"])
            cfg = store.load_config("s1")
        assert cfg["tracker_sources"] == ["jira"]
        assert cfg["team_members"] == []
        assert cfg["roster_configured"] is False

    def test_documentation_scope_round_trip(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config(
                "s1",
                enabled=True,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                documentation_sources=["confluence", "notion"],
                documentation_scope_configured=True,
            )
            cfg = store.load_config("s1")
        assert cfg["documentation_sources"] == ["confluence", "notion"]
        assert cfg["documentation_scope_configured"] is True

    def test_automation_fields_round_trip(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config(
                "s1",
                enabled=True,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                automation_markers="wiz, acme-scanner",
                automation_handling="off",
            )
            cfg = store.load_config("s1")
        assert cfg["automation_markers"] == "wiz, acme-scanner"
        assert cfg["automation_handling"] == "off"

    def test_automation_fields_default(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config("s1", enabled=True, time="10:00", weekdays="1-5", delivery_channels=["terminal"])
            cfg = store.load_config("s1")
        assert cfg["automation_markers"] == ""
        assert cfg["automation_handling"] == "exclude"

    def test_my_aliases_column_migrates_old_db(self, db_path):
        """A standup_config table created before my_aliases existed gains the column on open."""
        import sqlite3

        conn = sqlite3.connect(str(db_path))
        conn.executescript(
            """CREATE TABLE standup_config (
                   session_id TEXT PRIMARY KEY,
                   enabled INTEGER NOT NULL DEFAULT 0,
                   time TEXT NOT NULL DEFAULT '10:00',
                   timezone TEXT NOT NULL DEFAULT '',
                   weekdays TEXT NOT NULL DEFAULT '1-5',
                   delivery_channels TEXT NOT NULL DEFAULT '["terminal"]',
                   repo_path TEXT NOT NULL DEFAULT '',
                   created_at TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               );
               INSERT INTO standup_config (session_id, enabled, created_at, updated_at)
               VALUES ('s1', 1, 'now', 'now');"""
        )
        conn.close()
        with StandupStore(db_path) as store:
            cfg = store.load_config("s1")
        assert cfg is not None
        assert cfg["my_aliases"] == ""
        assert cfg["tracker_sources"] == ["jira"]
        assert cfg["team_members"] == []
        assert cfg["roster_configured"] is False
        # Automation-filter columns also arrive via migration with safe defaults.
        assert cfg["automation_markers"] == ""
        assert cfg["automation_handling"] == "exclude"


class TestSelfUpdates:
    def test_save_and_get(self, db_path):
        with StandupStore(db_path) as store:
            store.save_my_update("s1", "2026-07-10", "Alice", "shipped the login page")
            updates = store.get_my_updates("s1", "2026-07-10")
        assert updates == {"Alice": "shipped the login page"}

    def test_resubmit_overwrites(self, db_path):
        with StandupStore(db_path) as store:
            store.save_my_update("s1", "2026-07-10", "Alice", "first")
            store.save_my_update("s1", "2026-07-10", "Alice", "second")
            updates = store.get_my_updates("s1", "2026-07-10")
        assert updates == {"Alice": "second"}

    def test_scoped_by_date(self, db_path):
        with StandupStore(db_path) as store:
            store.save_my_update("s1", "2026-07-10", "Alice", "today")
            assert store.get_my_updates("s1", "2026-07-11") == {}

    def test_images_round_trip(self, db_path, tmp_path):
        img = tmp_path / "burndown.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        with StandupStore(db_path) as store:
            store.save_my_update("s1", "2026-07-10", "Alice", "see chart [image #1]", images=[str(img)])
            assert store.get_my_update_images("s1", "2026-07-10") == {"Alice": [str(img)]}

    def test_missing_image_files_pruned(self, db_path, tmp_path):
        with StandupStore(db_path) as store:
            store.save_my_update("s1", "2026-07-10", "Alice", "x", images=[str(tmp_path / "gone.png")])
            assert store.get_my_update_images("s1", "2026-07-10") == {}

    def test_update_without_images_has_none(self, db_path):
        with StandupStore(db_path) as store:
            store.save_my_update("s1", "2026-07-10", "Alice", "no pics")
            assert store.get_my_update_images("s1", "2026-07-10") == {}


class TestRunHistory:
    def test_record_and_get_latest(self, db_path):
        report = _make_report()
        with StandupStore(db_path) as store:
            row_id = store.record_run(report, delivery_status={"terminal": True}, status="success")
            latest = store.get_latest_report("s1")
        assert row_id > 0
        assert latest == report

    def test_get_latest_missing_returns_none(self, db_path):
        with StandupStore(db_path) as store:
            assert store.get_latest_report("s1") is None

    def test_latest_is_most_recent(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-09", confidence_pct=50))
            store.record_run(_make_report(date="2026-07-10", confidence_pct=90))
            latest = store.get_latest_report("s1")
        assert latest.date == "2026-07-10"
        assert latest.confidence_pct == 90

    def test_report_images_round_trip(self, db_path):
        # New tuple field must survive JSON serialization (list → tuple rebuild).
        report = _make_report(images=("/tmp/a.png", "/tmp/b.png"))
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        assert latest.images == ("/tmp/a.png", "/tmp/b.png")

    def test_old_report_without_images_deserializes(self, db_path):
        # Reports recorded before the images field existed must still load.
        report = _make_report()
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        assert latest.images == ()

    def test_get_history(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-09"))
            store.record_run(_make_report(date="2026-07-10"))
            history = store.get_history("s1")
        assert len(history) == 2
        assert history[0]["standup_date"] == "2026-07-10"  # newest first
        assert history[0]["confidence_pct"] == 82
        assert "id" in history[0]  # saved-runs hub needs the row id

    def test_corrupt_report_json_returns_none(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report())
            store._conn.execute("UPDATE standup_history SET report_json = 'garbage'")
            assert store.get_latest_report("s1") is None


class TestSavedRunsHub:
    """get_all_history / get_run_by_id / delete_run — power the TUI saved-runs hub."""

    def test_get_all_history_carries_id_and_session(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-09"))
            rows = store.get_all_history()
        assert rows and "id" in rows[0] and rows[0]["session_id"] == "s1"

    def test_get_run_by_id_round_trips_and_missing(self, db_path):
        report = _make_report(date="2026-07-10")
        with StandupStore(db_path) as store:
            rid = store.record_run(report)
            assert store.get_run_by_id(rid) == report
            assert store.get_run_by_id(999) is None

    def test_get_run_by_id_corrupt_returns_none(self, db_path):
        with StandupStore(db_path) as store:
            rid = store.record_run(_make_report())
            store._conn.execute("UPDATE standup_history SET report_json='{bad' WHERE id=?", (rid,))
            assert store.get_run_by_id(rid) is None

    def test_delete_run_removes_only_that_row(self, db_path):
        with StandupStore(db_path) as store:
            keep = store.record_run(_make_report(date="2026-07-09"))
            drop = store.record_run(_make_report(date="2026-07-10"))
            assert store.delete_run(drop) is True
            assert store.delete_run(drop) is False
            assert {r["id"] for r in store.get_all_history()} == {keep}

    def test_self_report_round_trips(self, db_path):
        report = _make_report(
            member_updates=(
                MemberUpdate(name="Me", summary="Merged auth PR", source="combined", self_report="paired\nall day"),
            )
        )
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        assert latest.member_updates[0].self_report == "paired\nall day"
        assert latest.member_updates[0].source == "combined"

    def test_old_report_json_without_self_report_deserializes(self, db_path):
        """Reports persisted before the self_report field existed still load."""
        import json

        with StandupStore(db_path) as store:
            store.record_run(_make_report())
            # Strip self_report from the stored JSON to simulate an old row.
            (raw,) = store._conn.execute("SELECT report_json FROM standup_history").fetchone()
            d = json.loads(raw)
            for m in d["member_updates"]:
                m.pop("self_report", None)
            store._conn.execute("UPDATE standup_history SET report_json = ?", (json.dumps(d),))
            latest = store.get_latest_report("s1")
        assert latest is not None
        assert latest.member_updates[0].self_report == ""

    def test_activity_window_round_trips(self, db_path):
        report = _make_report(activity_window="Fri 2026-07-17 00:00 → now")
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        assert latest.activity_window == "Fri 2026-07-17 00:00 → now"

    def test_my_name_round_trips(self, db_path):
        report = _make_report(my_name="Omar Din")
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        assert latest.my_name == "Omar Din"


class TestMigrationCreatesTables:
    def test_session_store_v6_creates_standup_tables(self, db_path):
        """Opening a SessionStore should run the v6 migration and create standup tables."""
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore

        assert CURRENT_SCHEMA_VERSION >= 6
        with SessionStore(db_path):
            pass
        # A fresh StandupStore on the same DB should find existing tables and work.
        with StandupStore(db_path) as store:
            store.save_config("s1", enabled=True, time="09:50", weekdays="1-5", delivery_channels=["terminal"])
            assert store.load_config("s1") is not None


class TestSkippedSourcesRoundTrip:
    def test_round_trips(self, db_path):
        report = _make_report(skipped_sources=(("github", "STANDUP_GITHUB_REPO not set"),))
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        assert latest.skipped_sources == (("github", "STANDUP_GITHUB_REPO not set"),)

    def test_old_report_without_field_deserializes(self, db_path):
        import json

        with StandupStore(db_path) as store:
            store.record_run(_make_report())
            (raw,) = store._conn.execute("SELECT report_json FROM standup_history").fetchone()
            d = json.loads(raw)
            d.pop("skipped_sources", None)
            store._conn.execute("UPDATE standup_history SET report_json = ?", (json.dumps(d),))
            latest = store.get_latest_report("s1")
        assert latest is not None
        assert latest.skipped_sources == ()


class TestMemberLinksRoundTrip:
    def test_round_trips(self, db_path):
        member = MemberUpdate(name="Alice", summary="login", links=(("PSOT-1", "https://j/browse/PSOT-1"),))
        with StandupStore(db_path) as store:
            store.record_run(_make_report(member_updates=(member,)))
            latest = store.get_latest_report("s1")
        assert latest.member_updates[0].links == (("PSOT-1", "https://j/browse/PSOT-1"),)

    def test_old_member_without_links_deserializes(self, db_path):
        import json

        with StandupStore(db_path) as store:
            store.record_run(_make_report())
            (raw,) = store._conn.execute("SELECT report_json FROM standup_history").fetchone()
            d = json.loads(raw)
            for m in d["member_updates"]:
                m.pop("links", None)
            store._conn.execute("UPDATE standup_history SET report_json = ?", (json.dumps(d),))
            latest = store.get_latest_report("s1")
        assert latest.member_updates[0].links == ()

    def test_structured_summaries_and_coverage_round_trip(self, db_path):
        member = MemberUpdate(
            name="Alice",
            summary="Delivered authentication and its runbook.",
            ticketing_summary="Moved PSOT-1 to Done.",
            ticketing_links=(("PSOT-1", "https://j/browse/PSOT-1"),),
            code_summary="Merged authentication.",
            code_links=(("#12", "https://github/pull/12"),),
            documentation_summary="Updated the authentication runbook.",
            documentation_links=(("Runbook", "https://wiki/runbook"),),
        )
        report = _make_report(
            member_updates=(member,),
            category_coverage=(
                ("ticketing", "covered"),
                ("code", "covered"),
                ("documentation", "partial"),
            ),
        )
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        assert latest.member_updates[0] == member
        assert latest.category_coverage == report.category_coverage


class TestActivityCountRoundTrip:
    def test_round_trips(self, db_path):
        member = MemberUpdate(name="Alice", summary="login", activity_count=3)
        with StandupStore(db_path) as store:
            store.record_run(_make_report(member_updates=(member,)))
            latest = store.get_latest_report("s1")
        assert latest.member_updates[0].activity_count == 3

    def test_old_member_without_count_deserializes(self, db_path):
        import json

        with StandupStore(db_path) as store:
            store.record_run(_make_report())
            (raw,) = store._conn.execute("SELECT report_json FROM standup_history").fetchone()
            d = json.loads(raw)
            for m in d["member_updates"]:
                m.pop("activity_count", None)
            store._conn.execute("UPDATE standup_history SET report_json = ?", (json.dumps(d),))
            latest = store.get_latest_report("s1")
        assert latest.member_updates[0].activity_count == 0


class TestEnabledScheduleSessions:
    @staticmethod
    def _save(store, session_id, enabled, time):
        store.save_config(session_id, enabled=enabled, time=time, weekdays="1-5", delivery_channels=["terminal"])

    def test_lists_enabled_sessions_most_recent_first(self, db_path):
        with StandupStore(db_path) as store:
            self._save(store, "old", True, "09:00")
            self._save(store, "off", False, "10:00")
            self._save(store, "new", True, "11:00")
            # Touch "old" again so it becomes the most recently updated.
            self._save(store, "old", True, "09:15")
            assert store.get_enabled_schedule_sessions() == ["old", "new"]

    def test_empty_when_no_enabled_config(self, db_path):
        with StandupStore(db_path) as store:
            self._save(store, "s1", False, "10:00")
            assert store.get_enabled_schedule_sessions() == []


class TestDayOverDayRoundTrip:
    def test_new_fields_round_trip(self, db_path):
        report = _make_report(
            confidence_delta=-8,
            confidence_trend="declining",
            member_updates=(
                MemberUpdate(
                    name="Alice",
                    summary="login",
                    progress_note="Still on PSOT-9 from yesterday.",
                    outlook="Likely to finish PSOT-9.",
                ),
            ),
        )
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        assert latest == report
        assert latest.confidence_delta == -8
        assert latest.confidence_trend == "declining"
        assert latest.member_updates[0].progress_note == "Still on PSOT-9 from yesterday."
        assert latest.member_updates[0].outlook == "Likely to finish PSOT-9."

    def test_old_report_json_defaults(self, db_path):
        # Reports recorded before the day-over-day fields existed must still load.
        report = _make_report()
        with StandupStore(db_path) as store:
            store.record_run(report)
            store._conn.execute(
                "UPDATE standup_history SET report_json = "
                '\'{"date": "2026-07-10", "member_updates": [{"name": "Alice"}]}\''
            )
            latest = store.get_latest_report("s1")
        assert latest.confidence_delta == 0
        assert latest.confidence_trend == ""
        assert latest.member_updates[0].progress_note == ""
        assert latest.member_updates[0].outlook == ""


class TestGetPreviousReport:
    def test_newest_before_date_wins(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-08", confidence_pct=70))
            store.record_run(_make_report(date="2026-07-09", confidence_pct=80))
            store.record_run(_make_report(date="2026-07-10", confidence_pct=90))
            prev = store.get_previous_report("s1", "2026-07-10")
        assert prev is not None
        assert prev.date == "2026-07-09"

    def test_same_day_rerun_excluded(self, db_path):
        # A rerun earlier TODAY is not "yesterday".
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-10", confidence_pct=50))
            assert store.get_previous_report("s1", "2026-07-10") is None

    def test_failed_runs_excluded(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-09"), status="failed")
            assert store.get_previous_report("s1", "2026-07-10") is None

    def test_partial_runs_included(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-09"), status="partial")
            prev = store.get_previous_report("s1", "2026-07-10")
        assert prev is not None

    def test_corrupt_json_returns_none(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-09"))
            store._conn.execute("UPDATE standup_history SET report_json = 'garbage'")
            assert store.get_previous_report("s1", "2026-07-10") is None

    def test_other_session_ignored(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-09", session_id="other"))
            assert store.get_previous_report("s1", "2026-07-10") is None
