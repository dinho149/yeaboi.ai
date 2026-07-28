"""Unit tests for reporting/store.ReportingStore + schema migration."""

import pytest

from yeaboi.agent.state import DeliveredItem, DeliveryReport
from yeaboi.reporting.store import ReportingStore, _dict_to_report, _report_to_json


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


def _report():
    return DeliveryReport(
        period_label="Last sprint",
        period_start="2026-06-29",
        period_end="2026-07-13",
        project_name="Acme",
        sprint_names=("Sprint 12",),
        headline="Shipped.",
        executive_summary="Summary.",
        themes=(("Security", ("SSO", "MFA")),),
        highlights=("SSO live",),
        metrics=(("Items delivered", "7"),),
        delivered_items=(DeliveredItem(key="A-1", title="t", status="Done", source="jira", assignee="Ada"),),
        emoji_theme=(("headline", "🚀"),),
        warnings=("w",),
        generated_at="2026-07-13",
    )


class TestRoundTrip:
    def test_json_round_trip_preserves_tuples(self):
        import json

        original = _report()
        restored = _dict_to_report(json.loads(_report_to_json(original)))
        assert restored == original

    def test_missing_keys_default(self):
        restored = _dict_to_report({"period_label": "Last sprint"})
        assert restored.period_label == "Last sprint"
        assert restored.themes == ()
        assert restored.delivered_items == ()


class TestStore:
    def test_record_and_get_latest(self, db_path):
        with ReportingStore(db_path) as store:
            store.record_run(_report(), session_id="s1")
            latest = store.get_latest_report()
            assert latest is not None and latest.headline == "Shipped."
            assert store.get_latest_report("s1") is not None
            assert store.get_latest_report("nope") is None

    def test_history_metadata(self, db_path):
        with ReportingStore(db_path) as store:
            store.record_run(_report(), session_id="s1")
            hist = store.get_history()
            assert len(hist) == 1
            assert hist[0]["item_count"] == 1
            assert hist[0]["period"] == "Last sprint"
            assert "id" in hist[0]  # saved-runs hub needs the row id to open/delete


class TestSavedRunsHub:
    """get_all_history / get_run_by_id / delete_run — power the TUI saved-runs hub."""

    def test_get_all_history_spans_sessions(self, db_path):
        with ReportingStore(db_path) as store:
            store.record_run(_report(), session_id="s1")
            store.record_run(_report(), session_id="s2")
            rows = store.get_all_history()
            assert len(rows) == 2
            assert {r["session_id"] for r in rows} == {"s1", "s2"}
            assert all("id" in r for r in rows)

    def test_get_run_by_id_round_trips(self, db_path):
        with ReportingStore(db_path) as store:
            rid = store.record_run(_report(), session_id="s1")
            got = store.get_run_by_id(rid)
            assert got is not None and got.headline == "Shipped."
            assert store.get_run_by_id(999) is None  # missing id

    def test_get_run_by_id_corrupt_returns_none(self, db_path):
        with ReportingStore(db_path) as store:
            rid = store.record_run(_report(), session_id="s1")
            store._conn.execute("UPDATE reporting_history SET report_json='{bad' WHERE id=?", (rid,))
            assert store.get_run_by_id(rid) is None  # corrupt json → None, no raise

    def test_delete_run_removes_only_that_row(self, db_path):
        with ReportingStore(db_path) as store:
            keep = store.record_run(_report(), session_id="s1")
            drop = store.record_run(_report(), session_id="s2")
            assert store.delete_run(drop) is True
            assert store.delete_run(drop) is False  # already gone → no-op
            remaining = {r["id"] for r in store.get_all_history()}
            assert remaining == {keep}


class TestSchemaMigration:
    def test_current_version_covers_reporting(self):
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION

        assert CURRENT_SCHEMA_VERSION >= 9

    def test_session_store_creates_reporting_table(self, db_path):
        import sqlite3

        from yeaboi.sessions import SessionStore

        with SessionStore(db_path):
            pass  # opening runs migrations up to v9
        conn = sqlite3.connect(str(db_path))
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        assert "reporting_history" in names


class TestSupportingSignalsRoundTrip:
    def test_round_trip_preserves_signals(self):
        import json
        from dataclasses import replace

        from yeaboi.agent.state import SupportingSignal

        original = replace(
            _report(),
            supporting_signals=(
                SupportingSignal(kind="pull_requests", source="github", count=12, samples=("Fix auth (#41)",)),
                SupportingSignal(kind="doc_updates", source="notion", count=2, samples=()),
            ),
        )
        restored = _dict_to_report(json.loads(_report_to_json(original)))
        assert restored == original
        assert restored.supporting_signals[0].samples == ("Fix auth (#41)",)

    def test_legacy_json_without_signals_defaults_empty(self):
        restored = _dict_to_report({"period_label": "Last sprint"})
        assert restored.supporting_signals == ()

    def test_malformed_count_coerced(self):
        restored = _dict_to_report(
            {"supporting_signals": [{"kind": "commits", "source": "github", "count": None, "samples": ["a"]}]}
        )
        assert restored.supporting_signals[0].count == 0
        assert restored.supporting_signals[0].samples == ("a",)
