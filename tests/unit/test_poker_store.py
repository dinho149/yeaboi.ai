"""Tests for PokerStore (poker/store.py) — schema, round-trip, history."""

from yeaboi.agent.state import PokerReport, PokerTicketResult, PokerVote
from yeaboi.poker.store import PokerStore, _dict_to_poker_report, _poker_report_to_json


def _report(session_id: str = "sess-1", date: str = "2026-07-25") -> PokerReport:
    return PokerReport(
        date=date,
        session_id=session_id,
        project_name="Proj",
        source="jira",
        scope_label="Sprint 42",
        tickets=(
            PokerTicketResult(
                key="PROJ-1",
                url="https://x.atlassian.net/browse/PROJ-1",
                summary="Do the thing",
                description="Details",
                state="To Do",
                assignee="Alex",
                initial_points=None,
                final_points=5.0,
                estimated=True,
                votes=(PokerVote("Alex", "🦊", "5"), PokerVote("Sam", "🐙", "8")),
                ai_note="Talk through the 8",
                duel_transcript="Alex (voted 5) — turn 1: it is a config change.",
                duel_low="Alex (5)",
                duel_high="Sam (8)",
            ),
            PokerTicketResult(key="PROJ-2", summary="Skipped one"),
        ),
        participants=("Alex", "Sam"),
        generated_at="2026-07-25T10:00:00+00:00",
    )


class TestSerialization:
    def test_roundtrip(self):
        report = _report()
        back = _dict_to_poker_report(__import__("json").loads(_poker_report_to_json(report)))
        assert back == report

    def test_empty_dict_all_defaults(self):
        report = _dict_to_poker_report({})
        assert report == PokerReport()

    def test_missing_ticket_keys_default(self):
        report = _dict_to_poker_report({"tickets": [{"key": "X-1"}]})
        t = report.tickets[0]
        assert t.key == "X-1"
        assert t.final_points is None
        assert t.estimated is False
        assert t.votes == ()
        # Legacy (pre-duel) report JSON keeps deserializing.
        assert t.duel_transcript == ""
        assert t.duel_low == "" and t.duel_high == ""

    def test_bad_points_values_become_none(self):
        report = _dict_to_poker_report({"tickets": [{"key": "X-1", "final_points": "junk"}]})
        assert report.tickets[0].final_points is None


class TestStore:
    def test_record_and_read_back(self, tmp_path):
        with PokerStore(tmp_path / "sessions.db") as store:
            run_id = store.record_run(_report())
            assert run_id > 0
            latest = store.get_latest_report("sess-1")
            assert latest is not None
            assert latest.tickets[0].final_points == 5.0
            by_id = store.get_run_by_id(run_id)
            assert by_id == latest

    def test_history_rows(self, tmp_path):
        with PokerStore(tmp_path / "sessions.db") as store:
            store.record_run(_report(date="2026-07-24"))
            store.record_run(_report(date="2026-07-25"))
            rows = store.get_history("sess-1")
            assert len(rows) == 2
            assert rows[0]["poker_date"] == "2026-07-25"  # newest first
            assert rows[0]["ticket_count"] == 2
            assert rows[0]["estimated_count"] == 1
            assert rows[0]["source"] == "jira"
            assert rows[0]["scope_label"] == "Sprint 42"

    def test_get_all_history_crosses_sessions(self, tmp_path):
        with PokerStore(tmp_path / "sessions.db") as store:
            store.record_run(_report(session_id="a"))
            store.record_run(_report(session_id="b"))
            rows = store.get_all_history()
            assert {r["session_id"] for r in rows} == {"a", "b"}

    def test_delete_run(self, tmp_path):
        with PokerStore(tmp_path / "sessions.db") as store:
            run_id = store.record_run(_report())
            assert store.delete_run(run_id)
            assert not store.delete_run(run_id)
            assert store.get_run_by_id(run_id) is None

    def test_missing_session_returns_none(self, tmp_path):
        with PokerStore(tmp_path / "sessions.db") as store:
            assert store.get_latest_report("nope") is None
            assert store.get_history("nope") == []


class TestMigration:
    def test_v18_creates_poker_table(self, tmp_path):
        import sqlite3

        from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore

        assert CURRENT_SCHEMA_VERSION >= 18
        db = tmp_path / "sessions.db"
        # Simulate an older DB: create the store, then wind schema_info back to 17
        # and drop the poker table so the migration has real work to do.
        store = SessionStore(db)
        store.close()
        conn = sqlite3.connect(db)
        conn.execute("DROP TABLE IF EXISTS poker_history")
        conn.execute("UPDATE schema_info SET schema_version = 17")
        conn.commit()
        conn.close()
        store = SessionStore(db)  # reopening runs migrations
        store.close()
        conn = sqlite3.connect(db)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = conn.execute("SELECT schema_version FROM schema_info").fetchone()[0]
        conn.close()
        assert "poker_history" in tables
        assert version == CURRENT_SCHEMA_VERSION
