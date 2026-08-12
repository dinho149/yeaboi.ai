"""Tests for src/yeaboi/agentwatch/store.py — the Agents-family SQLite store."""

from dataclasses import dataclass, field

import pytest

from yeaboi.agentwatch.store import AgentWatchStore


@pytest.fixture
def store(tmp_path):
    with AgentWatchStore(tmp_path / "sessions.db") as s:
        yield s


@dataclass(frozen=True)
class _FakeReport:
    period_start: str = "2026-08-01"
    period_end: str = "2026-08-08"
    total_cost_usd: float = 1.25
    warnings: tuple = field(default_factory=tuple)


class TestCursor:
    def test_missing_cursor_is_none(self, store):
        assert store.get_cursor("/nope.jsonl") is None

    def test_set_then_get(self, store):
        store.set_cursor("/a.jsonl", source="claude_code", size=10, mtime=1.5, first_line_sha="abc")
        cur = store.get_cursor("/a.jsonl")
        assert cur == {"source": "claude_code", "size": 10, "mtime": 1.5, "first_line_sha": "abc"}

    def test_upsert_replaces(self, store):
        store.set_cursor("/a.jsonl", source="claude_code", size=10, mtime=1.5, first_line_sha="abc")
        store.set_cursor("/a.jsonl", source="claude_code", size=20, mtime=2.5, first_line_sha="def")
        assert store.get_cursor("/a.jsonl")["size"] == 20

    def test_reset_cursors(self, store):
        store.set_cursor("/a.jsonl", source="claude_code", size=10, mtime=1.5, first_line_sha="abc")
        store.reset_cursors()
        assert store.get_cursor("/a.jsonl") is None


class TestTransaction:
    def test_writes_are_visible_after_the_block(self, store):
        with store.transaction():
            store.set_cursor("/a.jsonl", source="claude_code", size=10, mtime=1.5, first_line_sha="abc")
            store.set_cursor("/b.jsonl", source="claude_code", size=20, mtime=2.5, first_line_sha="def")
        assert store.get_cursor("/a.jsonl")["size"] == 10
        assert store.get_cursor("/b.jsonl")["size"] == 20

    def test_exception_rolls_the_batch_back(self, store):
        with pytest.raises(RuntimeError), store.transaction():
            store.set_cursor("/a.jsonl", source="claude_code", size=10, mtime=1.5, first_line_sha="abc")
            raise RuntimeError("boom")
        assert store.get_cursor("/a.jsonl") is None

    def test_autocommit_still_works_afterwards(self, store, tmp_path):
        with store.transaction():
            store.set_cursor("/a.jsonl", source="claude_code", size=10, mtime=1.5, first_line_sha="abc")
        # Post-batch writes are autocommit again: a SECOND connection to the
        # same DB must see them immediately, which it would not if a
        # transaction were still open on the first connection.
        store.set_cursor("/c.jsonl", source="claude_code", size=30, mtime=3.5, first_line_sha="ghi")
        with AgentWatchStore(tmp_path / "sessions.db") as other:
            assert other.get_cursor("/c.jsonl")["size"] == 30


class TestSessions:
    def _upsert(self, store, session_id="s1", ended_at="2026-08-07T10:00:00+00:00", source_path=None):
        # One rollup per transcript FILE, so the default path tracks the id —
        # two sessions in one test are two files, as they are on disk.
        store.upsert_session(
            session_id,
            source="claude_code",
            source_path=source_path or f"/{session_id}.jsonl",
            project_path="/home/dev/proj",
            git_branch="main",
            cli_version="2.1.226",
            started_at="2026-08-07T09:00:00+00:00",
            ended_at=ended_at,
            turns=3,
            model_usage={"claude-opus-5": {"input": 10, "output": 20, "calls": 1}},
            tool_counts={"Bash": 4},
        )

    def test_round_trip(self, store):
        self._upsert(store)
        rows = store.list_sessions()
        assert len(rows) == 1
        row = rows[0]
        assert row["session_id"] == "s1"
        assert row["model_usage"]["claude-opus-5"]["output"] == 20
        assert row["tool_counts"] == {"Bash": 4}

    def test_upsert_replaces_not_duplicates(self, store):
        self._upsert(store)
        self._upsert(store)
        assert len(store.list_sessions()) == 1

    def test_same_session_id_in_two_files_keeps_both_rollups(self, store):
        # The bug this schema shape exists to prevent: a session resumed from a
        # different cwd (or a copied transcript) carries ONE sessionId across
        # TWO files. Keyed on session_id, the second upsert replaced the first
        # and its tokens vanished from every cost total.
        self._upsert(store, "dup", source_path="/one.jsonl")
        self._upsert(store, "dup", source_path="/two.jsonl")
        rows = store.list_sessions()
        assert len(rows) == 2
        assert {r["source_path"] for r in rows} == {"/one.jsonl", "/two.jsonl"}
        assert {r["session_id"] for r in rows} == {"dup"}

    def test_forget_source_path_drops_the_rollup(self, store):
        self._upsert(store, "gone", source_path="/gone.jsonl")
        store.forget_source_path("/gone.jsonl")
        assert store.list_sessions() == []

    def test_window_filter(self, store):
        self._upsert(store, "old", ended_at="2026-08-01T10:00:00+00:00")
        self._upsert(store, "new", ended_at="2026-08-07T10:00:00+00:00")
        rows = store.list_sessions(since="2026-08-05")
        assert [r["session_id"] for r in rows] == ["new"]
        rows = store.list_sessions(since="2026-08-01", until="2026-08-02")
        assert [r["session_id"] for r in rows] == ["old"]


class TestFindings:
    def test_add_and_list(self, store):
        store.add_finding(
            category="secret", severity="critical", pattern="secret-sk-ant", source_path="/a.jsonl", line_no=7
        )
        rows = store.list_findings()
        assert rows[0]["pattern"] == "secret-sk-ant"
        assert rows[0]["severity"] == "critical"

    def test_duplicate_finding_ignored(self, store):
        for _ in range(2):
            store.add_finding(
                category="risky_tool", severity="high", pattern="curl-pipe-shell", source_path="/a.jsonl", line_no=3
            )
        assert len(store.list_findings()) == 1

    def test_category_filter_and_delete_by_path(self, store):
        store.add_finding(category="secret", severity="critical", pattern="p", source_path="/a.jsonl", line_no=1)
        store.add_finding(category="risky_tool", severity="high", pattern="q", source_path="/b.jsonl", line_no=2)
        assert len(store.list_findings(category="secret")) == 1
        store.delete_findings_for_path("/a.jsonl")
        assert [r["source_path"] for r in store.list_findings()] == ["/b.jsonl"]


class TestReports:
    def test_record_and_list_each_kind(self, store):
        for kind in ("usage", "standup", "security"):
            row_id = store.record_report(kind, _FakeReport(), key_date="2026-08-08")
            assert row_id > 0
            rows = store.list_reports(kind)
            assert rows[0]["key_date"] == "2026-08-08"
            assert rows[0]["report"]["total_cost_usd"] == 1.25
            assert rows[0]["origin"] == "generated"

    def test_newest_first_and_limit(self, store):
        for day in ("01", "02", "03"):
            store.record_report("usage", _FakeReport(period_start=f"2026-08-{day}"), key_date=f"2026-08-{day}")
        rows = store.list_reports("usage", limit=2)
        assert [r["key_date"] for r in rows] == ["2026-08-03", "2026-08-02"]


class TestMigration:
    def test_fresh_session_store_creates_agentwatch_tables(self, tmp_path):
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore

        assert CURRENT_SCHEMA_VERSION == 28
        db = tmp_path / "sessions.db"
        with SessionStore(db) as s:
            assert s.schema_mismatch is False
        with AgentWatchStore(db) as aw:
            tables = {
                row[0] for row in aw._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        assert {"agent_sessions", "agent_ingest_files", "agent_security_findings"} <= tables


class TestRehydration:
    """record_report → latest_report → report_from_payload round-trips."""

    @staticmethod
    def _artifacts():
        from yeaboi.agent.state import (
            AgentRepoActivityRow,
            AgentSecurityReport,
            AgentSessionSummary,
            AgentStandupDigest,
            AgentUsageBreakdownRow,
            AgentUsageReport,
            DailyUsagePoint,
            McpServerRecord,
            ModelUsageRow,
            SecurityFinding,
        )

        usage = AgentUsageReport(
            period_start="2026-07-10",
            period_end="2026-08-08",
            session_count=3,
            total_cost_usd=31.1,
            unknown_model_cost_share=0.25,
            pricing_as_of="2026-06-24",
            by_model=(ModelUsageRow(model="claude-opus-5", input_tokens=1_000_000, cost_usd=30.0),),
            by_project=(AgentUsageBreakdownRow(key="webapp", sessions=2, cost_usd=20.5),),
            by_source=(AgentUsageBreakdownRow(key="claude_code", sessions=3, cost_usd=31.1),),
            daily_trend=(DailyUsagePoint(date="2026-08-07", cost_usd=31.1, sessions=3),),
            insights=("opus dominates",),
            recommendations=("try haiku",),
            warnings=("no key",),
            generated_at="2026-08-08T10:00:00+00:00",
        )
        standup = AgentStandupDigest(
            digest_date="2026-08-08",
            window_start="2026-08-07",
            window_end="2026-08-08",
            sessions_worked=1,
            total_cost_usd=2.5,
            agents_seen=("claude_code",),
            session_summaries=(
                AgentSessionSummary(
                    session_id="s1",
                    source="claude_code",
                    project="webapp",
                    models=("claude-opus-5",),
                    turns=4,
                    cost_usd=2.5,
                    top_tools=(("Bash", "7"), ("Edit", "3")),
                    started_at="2026-08-07T10:00:00+00:00",
                ),
            ),
            repo_activity=(
                AgentRepoActivityRow(source="github", repo="webapp", kind="pr", title="fix", status="merged"),
            ),
            highlights=("merged a fix",),
            narrative="one session ran.",
            coverage_notes=("trackers skipped",),
            generated_at="2026-08-08T10:00:00+00:00",
        )
        security = AgentSecurityReport(
            scan_date="2026-08-08",
            posture="needs-attention",
            sessions_scanned=3,
            files_scanned=5,
            secrets_found=1,
            findings=(
                SecurityFinding(severity="high", category="secret", title="Credential", location="/x.jsonl", line_no=2),
            ),
            mcp_servers=(McpServerRecord(name="tracker", transport="http", target="http://x", flags=("plain-http",)),),
            settings_flags=("bypass-permissions",),
            summary="1 finding.",
            recommendations=("rotate it",),
            generated_at="2026-08-08T10:00:00+00:00",
        )
        return {"usage": usage, "standup": standup, "security": security}

    def test_round_trip_each_kind(self, store):
        from yeaboi.agentwatch.store import report_from_payload

        for kind, artifact in self._artifacts().items():
            store.record_report(kind, artifact, key_date="2026-08-08")
            row = store.latest_report(kind)
            assert row is not None
            rebuilt = report_from_payload(kind, row["report"])
            # Frozen-dataclass equality: every nested row and tuple must survive
            # the JSON round trip (top_tools pairs come back as lists otherwise).
            assert rebuilt == artifact

    def test_latest_report_empty_history_is_none(self, store):
        assert store.latest_report("usage") is None

    def test_missing_keys_rehydrate_with_defaults(self):
        from yeaboi.agentwatch.store import report_from_payload

        rebuilt = report_from_payload("usage", {"period_start": "2026-08-01"})
        assert rebuilt is not None
        assert rebuilt.period_start == "2026-08-01"
        assert rebuilt.by_model == ()
        assert rebuilt.total_cost_usd == 0.0

    def test_corrupt_payloads_are_none(self):
        from yeaboi.agentwatch.store import report_from_payload

        assert report_from_payload("usage", {}) is None
        assert report_from_payload("usage", "not a dict") is None
        assert report_from_payload("unknown-kind", {"a": 1}) is None

    def test_corrupt_json_row_falls_back_to_none(self, store):
        from yeaboi.agentwatch.store import report_from_payload

        store._conn.execute(
            "INSERT INTO agent_usage_reports (period_start, report_json, created_at) VALUES (?, ?, ?)",
            ("2026-08-08", "{broken json", "2026-08-08T10:00:00+00:00"),
        )
        row = store.latest_report("usage")
        assert row is not None
        assert report_from_payload("usage", row["report"]) is None
