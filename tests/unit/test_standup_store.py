"""Unit tests for the Daily Standup SQLite store."""

from dataclasses import replace

import pytest

from yeaboi.agent.state import (
    MemberUpdate,
    StandupGap,
    StandupReport,
    TranscriptClaim,
    TranscriptReview,
    TranscriptSource,
)
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

    def test_evidence_round_trips_as_dataclasses(self, db_path):
        from yeaboi.agent.state import ActivityEvidence

        report = _make_report(
            member_updates=(
                MemberUpdate(
                    name="Me",
                    summary="x",
                    code_evidence=(
                        ActivityEvidence(
                            kind="commit",
                            key="78e4201d",
                            title="Fix login redirect",
                            url="https://g/c1",
                            repository="yeaboi/web",
                            timestamp="2026-07-30T09:15:00",
                        ),
                    ),
                ),
            )
        )
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        (row,) = latest.member_updates[0].code_evidence
        assert isinstance(row, ActivityEvidence)
        assert (row.key, row.title, row.repository) == ("78e4201d", "Fix login redirect", "yeaboi/web")
        assert latest.member_updates[0].ticketing_evidence == ()

    def test_pr_children_round_trip_nested(self, db_path):
        from yeaboi.agent.state import ActivityEvidence

        report = _make_report(
            member_updates=(
                MemberUpdate(
                    name="Me",
                    summary="x",
                    code_evidence=(
                        ActivityEvidence(
                            kind="pr",
                            key="!91",
                            title="Enable SSO",
                            url="https://a/pr/91",
                            status="merged",
                            children=(ActivityEvidence(kind="commit", key="aaa1", title="Fix", url="https://a/c1"),),
                        ),
                    ),
                ),
            )
        )
        with StandupStore(db_path) as store:
            store.record_run(report)
            latest = store.get_latest_report("s1")
        (pr,) = latest.member_updates[0].code_evidence
        (child,) = pr.children
        assert isinstance(child, ActivityEvidence)
        assert (child.kind, child.key, child.children) == ("commit", "aaa1", ())

    def test_old_report_json_without_evidence_deserializes(self, db_path):
        """Reports persisted before the *_evidence fields existed still load."""
        import json

        with StandupStore(db_path) as store:
            store.record_run(_make_report())
            (raw,) = store._conn.execute("SELECT report_json FROM standup_history").fetchone()
            d = json.loads(raw)
            for m in d["member_updates"]:
                for field in ("ticketing_evidence", "code_evidence", "documentation_evidence"):
                    m.pop(field, None)
            store._conn.execute("UPDATE standup_history SET report_json = ?", (json.dumps(d),))
            latest = store.get_latest_report("s1")
        assert latest is not None
        assert latest.member_updates[0].code_evidence == ()

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


# ---------------------------------------------------------------------------
# Transcript review — reviews, transcript bookkeeping, and the gap→issue ledger
# ---------------------------------------------------------------------------


def _make_review(**overrides) -> TranscriptReview:
    base = dict(
        session_id="s1",
        standup_date="2026-07-10",
        run_id=0,
        reviewed_at="2026-07-10T11:00:00+00:00",
        sources=(
            TranscriptSource(
                path="/tmp/t.vtt",
                filename="t.vtt",
                fmt="vtt",
                covered_date="2026-07-10",
                char_count=120,
                speakers=("Alice",),
            ),
        ),
        claims=(
            TranscriptClaim(
                member="Alice",
                claim="also commented on the design doc",
                quote="I also commented on the design doc",
                status="missing",
                system_hint="confluence",
                artifact_hint="comment on a page",
            ),
        ),
        gaps=(
            StandupGap(
                fingerprint="abc123",
                category="capability_gap_in_supported_source",
                scope="product",
                title="Standup misses Confluence page comments",
                members=("Alice",),
                affected_systems=("confluence",),
                next_steps=("Fetch page comments in collector.py",),
            ),
        ),
        config_suggestions=(
            StandupGap(
                fingerprint="def456",
                category="scope_gap_repository",
                scope="config",
                title="acme/infra is not in your code scope",
                remedy="Add acme/infra via Standup -> Configure -> Code",
            ),
        ),
        claims_matched=3,
        claims_missing=1,
        llm_mode="llm",
        warnings=("one warning",),
    )
    base.update(overrides)
    return TranscriptReview(**base)


class TestTranscriptReviews:
    def test_round_trips(self, db_path):
        review = _make_review()
        with StandupStore(db_path) as store:
            review_id = store.record_review(review)
            loaded = store.get_review(review_id)
        assert loaded is not None
        # review_id is assigned on insert, so compare the rest field-for-field.
        assert replace(loaded, review_id=0) == review
        assert loaded.review_id == review_id

    def test_nested_gaps_and_claims_rebuild_as_dataclasses(self, db_path):
        with StandupStore(db_path) as store:
            review_id = store.record_review(_make_review())
            loaded = store.get_review(review_id)
        assert isinstance(loaded.gaps[0], StandupGap)
        assert isinstance(loaded.claims[0], TranscriptClaim)
        assert isinstance(loaded.sources[0], TranscriptSource)
        assert loaded.gaps[0].affected_systems == ("confluence",)
        assert loaded.config_suggestions[0].scope == "config"

    def test_old_review_json_without_new_keys_deserializes(self, db_path):
        import json

        with StandupStore(db_path) as store:
            review_id = store.record_review(_make_review())
            (raw,) = store._conn.execute("SELECT review_json FROM standup_reviews").fetchone()
            stripped = json.loads(raw)
            for key in ("gaps", "config_suggestions", "sources", "claims", "llm_mode", "untracked_count"):
                stripped.pop(key, None)
            store._conn.execute("UPDATE standup_reviews SET review_json = ?", (json.dumps(stripped),))
            loaded = store.get_review(review_id)
        assert loaded is not None
        assert loaded.gaps == ()
        assert loaded.config_suggestions == ()
        assert loaded.sources == ()
        assert loaded.llm_mode == ""

    def test_corrupt_json_returns_none(self, db_path):
        with StandupStore(db_path) as store:
            review_id = store.record_review(_make_review())
            store._conn.execute("UPDATE standup_reviews SET review_json = 'garbage'")
            assert store.get_review(review_id) is None

    def test_get_review_missing_returns_none(self, db_path):
        with StandupStore(db_path) as store:
            assert store.get_review(999) is None

    def test_latest_and_list(self, db_path):
        with StandupStore(db_path) as store:
            store.record_review(_make_review(standup_date="2026-07-09", reviewed_at="2026-07-09T11:00:00+00:00"))
            store.record_review(_make_review(standup_date="2026-07-10", reviewed_at="2026-07-10T11:00:00+00:00"))
            latest = store.get_latest_review("s1")
            rows = store.get_reviews("s1")
        assert latest.standup_date == "2026-07-10"
        assert [r["standup_date"] for r in rows] == ["2026-07-10", "2026-07-09"]
        assert rows[0]["status"] == "drafted"

    def test_status_update(self, db_path):
        with StandupStore(db_path) as store:
            review_id = store.record_review(_make_review())
            store.set_review_status(review_id, "filed")
            assert store.get_reviews("s1")[0]["status"] == "filed"

    def test_other_session_ignored(self, db_path):
        with StandupStore(db_path) as store:
            store.record_review(_make_review(session_id="other"))
            assert store.get_latest_review("s1") is None


class TestRunRowByDate:
    def test_returns_newest_run_on_the_date(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-10"))
            second = store.record_run(_make_report(date="2026-07-10"))
            assert store.get_run_row_by_date("s1", "2026-07-10") == second

    def test_failed_run_ignored(self, db_path):
        with StandupStore(db_path) as store:
            store.record_run(_make_report(date="2026-07-10"), status="failed")
            assert store.get_run_row_by_date("s1", "2026-07-10") == 0

    def test_no_run_returns_zero(self, db_path):
        with StandupStore(db_path) as store:
            assert store.get_run_row_by_date("s1", "2026-07-10") == 0


class TestTranscriptBookkeeping:
    def test_marks_and_lists_hashes(self, db_path):
        with StandupStore(db_path) as store:
            store.mark_transcript_reviewed(
                "s1", path="/tmp/a.vtt", content_hash="h1", covered_date="2026-07-10", review_id=1
            )
            assert store.reviewed_transcript_hashes("s1") == {"h1"}

    def test_same_content_at_a_new_path_is_not_re_reviewed(self, db_path):
        # Renaming a transcript must not re-spend an LLM call: the key is content.
        with StandupStore(db_path) as store:
            store.mark_transcript_reviewed(
                "s1", path="/tmp/a.vtt", content_hash="h1", covered_date="2026-07-10", review_id=1
            )
            store.mark_transcript_reviewed(
                "s1", path="/tmp/renamed.vtt", content_hash="h1", covered_date="2026-07-10", review_id=2
            )
            rows = store._conn.execute("SELECT path, review_id FROM standup_transcripts").fetchall()
        assert rows == [("/tmp/renamed.vtt", 2)]

    def test_hashes_are_session_scoped(self, db_path):
        with StandupStore(db_path) as store:
            store.mark_transcript_reviewed(
                "s1", path="/tmp/a.vtt", content_hash="h1", covered_date="2026-07-10", review_id=1
            )
            assert store.reviewed_transcript_hashes("other") == set()


class TestGapIssueLedger:
    def test_insert_then_read(self, db_path):
        with StandupStore(db_path) as store:
            store.upsert_gap_issue("fp1", category="integration_missing", title="Slack", review_id=3)
            entry = store.get_gap_issue("fp1")
        assert entry["category"] == "integration_missing"
        assert entry["state"] == "drafted"
        assert entry["occurrences"] == 1
        assert entry["last_review_id"] == 3

    def test_recurrence_bumps_occurrences(self, db_path):
        with StandupStore(db_path) as store:
            store.upsert_gap_issue("fp1", category="c", title="t")
            store.upsert_gap_issue("fp1", category="c", title="t")
            store.upsert_gap_issue("fp1", category="c", title="t")
            assert store.get_gap_issue("fp1")["occurrences"] == 3

    def test_recurrence_preserves_filed_state(self, db_path):
        """A later 'seen again' must never erase the issue number — that is how
        dedup would silently start filing duplicates onto a public repo."""
        with StandupStore(db_path) as store:
            store.upsert_gap_issue(
                "fp1",
                category="c",
                title="t",
                issue_number=42,
                issue_url="https://example/42",
                state="filed",
                via="api",
                filed_at="2026-07-10T00:00:00+00:00",
            )
            store.upsert_gap_issue("fp1", category="c", title="t")
            entry = store.get_gap_issue("fp1")
        assert entry["issue_number"] == 42
        assert entry["issue_url"] == "https://example/42"
        assert entry["state"] == "filed"
        assert entry["filed_at"] == "2026-07-10T00:00:00+00:00"
        assert entry["occurrences"] == 2

    def test_bump_occurrence_can_be_suppressed(self, db_path):
        with StandupStore(db_path) as store:
            store.upsert_gap_issue("fp1", category="c", title="t")
            store.upsert_gap_issue("fp1", category="c", title="t", state="filed", bump_occurrence=False)
            assert store.get_gap_issue("fp1")["occurrences"] == 1

    def test_missing_returns_none(self, db_path):
        with StandupStore(db_path) as store:
            assert store.get_gap_issue("nope") is None

    def test_ledger_is_not_session_scoped(self, db_path):
        """The loop improves yeaboi itself, so the same gap in two projects is one issue."""
        with StandupStore(db_path) as store:
            store.upsert_gap_issue("fp1", category="c", title="t")
            store.upsert_gap_issue("fp2", category="c2", title="t2")
            assert {e["fingerprint"] for e in store.get_gap_issues()} == {"fp1", "fp2"}


class TestTranscriptConfig:
    def test_round_trips(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config(
                "s1",
                enabled=True,
                time="10:00",
                weekdays="1-5",
                delivery_channels=["terminal"],
                transcript_dir="/tmp/meetings",
                transcript_review_enabled=False,
            )
            cfg = store.load_config("s1")
        assert cfg["transcript_dir"] == "/tmp/meetings"
        assert cfg["transcript_review_enabled"] is False

    def test_defaults_to_enabled(self, db_path):
        with StandupStore(db_path) as store:
            store.save_config("s1", enabled=True, time="10:00", weekdays="1-5", delivery_channels=["terminal"])
            cfg = store.load_config("s1")
        assert cfg["transcript_dir"] == ""
        assert cfg["transcript_review_enabled"] is True
