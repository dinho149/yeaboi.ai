"""Tests for src/yeaboi/agentwatch/advisor.py — run_agent_advisor."""

import json
from dataclasses import asdict
from datetime import date

import pytest

from yeaboi.agentwatch import advisor
from yeaboi.agentwatch.store import AgentWatchStore

TODAY = date(2026, 8, 8)

# Captured before the autouse no_prefix_files fixture patches the module attr,
# so TestPrefixScan can exercise the real function.
_REAL_PREFIX_FILES = advisor._prefix_files

# A fake API key big enough to clear the audit's MIN_SIZE floor when repeated.
PLANTED_SECRET = "sk-ant-api03-PLANTED-SECRET-VALUE-000000"
BIG = ("x" * 80 + "\n") * 10  # 810 bytes, above MIN_SIZE


def _assistant(blocks: list) -> str:
    return json.dumps({"message": {"role": "assistant", "content": blocks}})


def _tool_use(tool_id: str, name: str, **inp) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": inp}


def _result(tool_id: str, text: str) -> str:
    return json.dumps(
        {"message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]}}
    )


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture(autouse=True)
def no_ingest_no_export(monkeypatch):
    """Keep the engine off the real ~/.claude and ~/.yeaboi trees."""
    from yeaboi.agentwatch.collector import IngestStats

    monkeypatch.setattr(advisor.collector, "refresh", lambda store, **kw: IngestStats())
    import yeaboi.agentwatch.export as export_mod

    monkeypatch.setattr(export_mod, "export_artifact", lambda artifact, *, kind: {})


@pytest.fixture(autouse=True)
def no_prefix_files(monkeypatch):
    """Keep the cache-health scan off the developer's real CLAUDE.md files."""
    monkeypatch.setattr(advisor, "_prefix_files", lambda sessions: [])


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Default every test to the unconfigured-LLM path (deterministic fallback)."""
    import yeaboi.config

    monkeypatch.setattr(yeaboi.config, "is_llm_configured", lambda: (False, "no API key set"))


def _seed_session(db_path, transcript_path, *, model_usage=None, sid="s1", ended="2026-08-07T10:00:00+00:00"):
    with AgentWatchStore(db_path) as store:
        store.upsert_session(
            sid,
            source="claude_code",
            source_path=str(transcript_path),
            project_path="/home/dev/webapp",
            git_branch="main",
            cli_version="2.1.0",
            started_at=ended,
            ended_at=ended,
            turns=1,
            # opus-5 input rate is $5/Mtok — the blended rate for a single model.
            model_usage=model_usage or {"claude-opus-5": {"input": 1_000_000, "output": 0, "calls": 1}},
            tool_counts={},
        )


def _transcript_with_identical_repeat(tmp_path, extra_line: str = ""):
    lines = [
        _assistant([_tool_use("t1", "Read", file_path="/f.py")]),
        _result("t1", BIG),
        _assistant([_tool_use("t2", "Read", file_path="/f.py")]),
        _result("t2", BIG),
    ]
    if extra_line:
        lines.append(extra_line)
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestPipeline:
    def test_prices_waste_at_the_blended_input_rate(self, tmp_path, db_path):
        transcript = _transcript_with_identical_repeat(tmp_path)
        _seed_session(db_path, transcript)
        report = advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY, dry_run=True)

        assert report.files_audited == 1
        assert report.effective_input_rate_per_mtok == pytest.approx(5.0)
        assert report.total_cost_usd == pytest.approx(5.0)  # 1M opus-5 input tokens

        identical = next(i for i in report.line_items if i.mechanism == "identical-repeat")
        expected_bytes = len(BIG.encode())
        assert identical.content_bytes == expected_bytes
        assert identical.est_tokens == expected_bytes // 4
        assert identical.est_usd == round(expected_bytes // 4 * 5.0 / 1_000_000, 4)
        assert report.recoverable_usd == pytest.approx(
            sum(i.est_usd for i in report.line_items if i.recoverable), abs=1e-6
        )
        assert 0 < report.recoverable_share < 1

    def test_stale_rereads_are_context_not_savings(self, tmp_path, db_path):
        lines = [
            _assistant([_tool_use("t1", "Read", file_path="/f.py")]),
            _result("t1", BIG),
            _assistant([_tool_use("t2", "Edit", file_path="/f.py")]),
            _result("t2", "edited"),
        ]
        path = tmp_path / "session.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        _seed_session(db_path, path)
        report = advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY, dry_run=True)
        stale = next(i for i in report.line_items if i.mechanism == "stale-reread")
        assert stale.content_bytes > 0
        assert not stale.recoverable
        assert report.recoverable_usd == pytest.approx(
            sum(i.est_usd for i in report.line_items if i.mechanism != "stale-reread"), abs=1e-6
        )

    def test_empty_window_warns_and_stays_zero(self, db_path):
        report = advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY, dry_run=True)
        assert report.session_count == 0
        assert report.files_audited == 0
        assert report.recoverable_usd == 0.0
        assert report.alignment_score == 100
        assert any("No local agent sessions" in w for w in report.warnings)

    def test_history_is_recorded(self, tmp_path, db_path):
        transcript = _transcript_with_identical_repeat(tmp_path)
        _seed_session(db_path, transcript)
        advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY, dry_run=True)
        with AgentWatchStore(db_path) as store:
            rows = store.list_reports("advisor")
        assert len(rows) == 1
        assert rows[0]["report"]["files_audited"] == 1

    def test_llm_failure_falls_back_to_deterministic_prose(self, tmp_path, db_path):
        transcript = _transcript_with_identical_repeat(tmp_path)
        _seed_session(db_path, transcript)
        report = advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY)
        assert any("AI output unavailable" in w for w in report.warnings)
        assert report.insights  # deterministic fallback lines

    def test_blended_rate_weights_by_input_share(self, tmp_path, db_path):
        transcript = _transcript_with_identical_repeat(tmp_path)
        _seed_session(
            db_path,
            transcript,
            # 3M opus-5 input at $5 + 1M haiku input at $1 → (15+1)/4 = $4/Mtok.
            model_usage={
                "claude-opus-5": {"input": 3_000_000, "output": 0, "calls": 1},
                "claude-haiku-4-5": {"input": 1_000_000, "output": 0, "calls": 1},
            },
        )
        report = advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY, dry_run=True)
        assert report.effective_input_rate_per_mtok == pytest.approx(4.0)
        assert report.unknown_rate_share == 0.0

    def test_cache_only_window_flags_the_fallback_rate(self, tmp_path, db_path):
        # Regression: a window with sessions but no input tokens prices the
        # whole audit at the fallback tier — that must be flagged, not silent.
        transcript = _transcript_with_identical_repeat(tmp_path)
        _seed_session(
            db_path,
            transcript,
            model_usage={"claude-opus-5": {"input": 0, "output": 0, "cache_read": 2_000_000, "calls": 1}},
        )
        report = advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY, dry_run=True)
        assert report.unknown_rate_share == pytest.approx(1.0)

    def test_recoverable_headline_capped_at_window_spend(self, tmp_path, db_path):
        # Regression: waste priced at fresh-input rates can exceed a cheap
        # cache-heavy window's measured spend; "$9 recoverable of $4" must not
        # render. Tiny spend, big transcript → the cap fires with a warning.
        transcript = _transcript_with_identical_repeat(tmp_path)
        _seed_session(
            db_path,
            transcript,
            model_usage={"claude-opus-5": {"input": 10, "output": 0, "calls": 1}},
        )
        report = advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY, dry_run=True)
        assert report.total_cost_usd > 0
        assert report.recoverable_usd == report.total_cost_usd
        assert report.recoverable_share == pytest.approx(1.0)
        assert any("capped" in w.lower() for w in report.warnings)

    def test_unknown_model_flags_the_rate(self, tmp_path, db_path):
        transcript = _transcript_with_identical_repeat(tmp_path)
        _seed_session(
            db_path,
            transcript,
            model_usage={"totally-unknown-model": {"input": 1_000_000, "output": 0, "calls": 1}},
        )
        report = advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY, dry_run=True)
        assert report.unknown_rate_share == pytest.approx(1.0)


class TestPrivacy:
    def test_transcript_content_never_reaches_the_artifact(self, tmp_path, db_path):
        transcript = _transcript_with_identical_repeat(
            tmp_path,
            extra_line=_result("t9", f"env dump: ANTHROPIC_API_KEY={PLANTED_SECRET}\n" + BIG),
        )
        _seed_session(db_path, transcript)
        report = advisor.run_agent_advisor(window_days=30, db_path=db_path, today=TODAY, dry_run=True)
        serialized = json.dumps(asdict(report))
        assert PLANTED_SECRET not in serialized
        assert BIG[:40] not in serialized
        # Family convention (test_agentwatch_collector): the whole database is
        # scanned too, not just the artifact — the run also persisted history.
        import sqlite3

        conn = sqlite3.connect(db_path)
        try:
            for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                for row in conn.execute(f"SELECT * FROM {table}"):  # noqa: S608 — test over its own tmp DB
                    for value in row:
                        assert PLANTED_SECRET not in str(value), f"secret leaked into {table}"
        finally:
            conn.close()


class TestPrefixScan:
    def test_prefix_files_come_from_home_and_windowed_projects(self, tmp_path):
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        (home / ".claude" / "CLAUDE.md").write_text("home config", encoding="utf-8")
        project = tmp_path / "proj"
        project.mkdir()
        (project / "CLAUDE.md").write_text("project config", encoding="utf-8")
        sessions = [{"project_path": str(project)}, {"project_path": str(project)}]
        paths = _REAL_PREFIX_FILES(sessions, home=home)
        assert paths == [home / ".claude" / "CLAUDE.md", project / "CLAUDE.md"]

    def test_scan_reports_counts_and_score_never_content(self, tmp_path):
        volatile = tmp_path / "CLAUDE.md"
        volatile.write_text(
            "deploy id 3f2b8a9e-1c4d-4e6f-9a0b-2c3d4e5f6a7b at 2026-08-16T10:00:00Z",
            encoding="utf-8",
        )
        clean = tmp_path / "CLEAN.md"
        clean.write_text("nothing volatile here", encoding="utf-8")
        signals, score = advisor._scan_prefix_files([volatile, clean])
        assert len(signals) == 1  # the clean file is the normal case, not a row
        assert signals[0].location == str(volatile)
        assert signals[0].total == 2
        assert dict(signals[0].counts) == {"uuid": "1", "iso8601": "1"}
        assert score == 90  # per-file penalty: min(20, 2*5)
        assert "3f2b8a9e" not in json.dumps([asdict(s) for s in signals])


class TestRepoScope:
    def test_project_path_keeps_only_sessions_under_the_repo(self, db_path, tmp_path):
        _seed_session(db_path, tmp_path / "a.jsonl", sid="a")
        report = advisor.run_agent_advisor(
            window_days=30, project_path="/home/dev/webapp", db_path=db_path, today=TODAY, dry_run=True
        )
        assert report.session_count == 1
        none = advisor.run_agent_advisor(
            window_days=30, project_path="/nowhere", db_path=db_path, today=TODAY, dry_run=True
        )
        assert none.session_count == 0
