"""Tests for run_agent_standup (agentwatch/engine.py) and its builders."""

from datetime import date

import pytest

from yeaboi.agentwatch import engine
from yeaboi.agentwatch.store import AgentWatchStore

MONDAY = date(2026, 8, 10)
FRIDAY_SESSION_TS = "2026-08-07T15:00:00+00:00"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture(autouse=True)
def no_ingest_no_export(monkeypatch):
    from yeaboi.agentwatch.collector import IngestStats

    monkeypatch.setattr(engine.collector, "refresh", lambda store, **kw: IngestStats())
    import yeaboi.agentwatch.export as export_mod

    monkeypatch.setattr(export_mod, "export_artifact", lambda artifact, *, kind: {})


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    import yeaboi.config

    monkeypatch.setattr(yeaboi.config, "is_llm_configured", lambda: (False, "no API key set"))


@pytest.fixture(autouse=True)
def no_trackers(monkeypatch):
    """Default: the tracker fan-out is stubbed empty (tests opt into items)."""
    import yeaboi.analysis.ai_usage as ai_usage

    monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([], [], ["GitHub: no token"], []))


def _seed_session(db_path, session_id="s1", ended_at=FRIDAY_SESSION_TS, project="/home/dev/webapp"):
    with AgentWatchStore(db_path) as store:
        store.upsert_session(
            session_id,
            source="claude_code",
            source_path=f"/x/{session_id}.jsonl",
            project_path=project,
            git_branch="feature/x",
            cli_version="2.1.0",
            started_at=ended_at,
            ended_at=ended_at,
            turns=4,
            model_usage={"claude-opus-5": {"input": 1_000_000, "output": 1_000_000, "calls": 2}},
            tool_counts={"Bash": 7, "Edit": 3, "Read": 12, "Write": 1},
        )


AGENT_PR = {
    "kind": "pr",
    "title": "add retry logic",
    "body": "Co-authored-by: Claude <noreply@anthropic.com>",
    "author": "dev",
    "author_email": "dev@x.com",
    "branch": "",
    "status": "merged",
    "repository": "org/webapp",
    "source": "github",
    "url": "https://github.com/org/webapp/pull/1",
    "timestamp": "2026-08-07T16:00:00+00:00",
    "key": "#1",
}

HUMAN_COMMIT = {
    "kind": "commit",
    "title": "fix typo",
    "body": "just me",
    "author": "dev",
    "author_email": "dev@x.com",
    "repository": "org/webapp",
    "source": "github",
    "url": "",
    "timestamp": "2026-08-07T16:00:00+00:00",
    "key": "abc1234",
}


class TestWindow:
    def test_monday_reaches_back_to_friday(self, db_path):
        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.window_start == "2026-08-07"  # Friday
        assert digest.sessions_worked == 1

    def test_explicit_days(self, db_path):
        _seed_session(db_path, ended_at="2026-08-01T10:00:00+00:00")
        digest = engine.run_agent_standup(days=3, db_path=db_path, today=MONDAY)
        assert digest.sessions_worked == 0  # Aug 1 is outside a 3-day window from Aug 10


class TestSessions:
    def test_session_summary_shape(self, db_path):
        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        (summary,) = digest.session_summaries
        assert summary.project == "webapp"
        assert summary.models == ("claude-opus-5",)
        assert summary.cost_usd == pytest.approx(30.0)
        assert summary.top_tools[0] == ("Read", "12")
        assert digest.total_cost_usd == pytest.approx(30.0)
        assert "claude_code" in digest.agents_seen


class TestTrackerActivity:
    def test_agent_items_kept_and_humans_dropped(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        monkeypatch.setattr(
            ai_usage, "collect_ai_activity", lambda *a, **kw: ([AGENT_PR, HUMAN_COMMIT], ["github"], [], ["org/webapp"])
        )
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert len(digest.repo_activity) == 1
        row = digest.repo_activity[0]
        assert row.kind == "pr"
        assert row.repo == "webapp"
        assert row.agent_marker == "claude"
        assert row.status == "merged"

    def test_local_only_skips_trackers(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        def _boom(*a, **kw):
            raise AssertionError("tracker_sources=[] must not scan")

        monkeypatch.setattr(ai_usage, "collect_ai_activity", _boom)
        digest = engine.run_agent_standup(tracker_sources=[], db_path=db_path, today=MONDAY)
        assert digest.repo_activity == ()
        assert any("skipped" in note for note in digest.coverage_notes)

    def test_tracker_failure_is_a_note_not_a_crash(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        def _boom(*a, **kw):
            raise RuntimeError("github down")

        monkeypatch.setattr(ai_usage, "collect_ai_activity", _boom)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert any("github down" in note for note in digest.coverage_notes)

    def test_coverage_notes_surface(self, db_path):
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert any("no token" in note for note in digest.coverage_notes)

    def test_open_agent_pr_lands_in_flight(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        open_pr = {**AGENT_PR, "status": "open", "title": "wip: migrate config"}
        monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([open_pr], [], [], []))
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.in_flight == ("wip: migrate config (webapp)",)


class TestProse:
    def test_fallback_narrative_and_highlights(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage

        monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([AGENT_PR], [], [], []))
        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert "1 local agent session(s)" in digest.narrative
        assert any("Merged: add retry logic" in h for h in digest.highlights)

    def test_llm_prose_used_when_available(self, db_path, monkeypatch):
        _seed_session(db_path)
        monkeypatch.setattr(
            engine,
            "_invoke_llm",
            lambda prompt, *, what: (
                {"narrative": "the agents shipped things", "highlights": ["h1"], "attention_items": ["a1"]},
                [],
            ),
        )
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.narrative == "the agents shipped things"
        assert digest.highlights == ("h1",)
        assert digest.attention_items == ("a1",)

    def test_empty_window_warns(self, db_path):
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        assert digest.sessions_worked == 0
        assert any("No agent activity" in w for w in digest.warnings)


class TestDelivery:
    def test_deliver_failure_becomes_a_warning(self, db_path, monkeypatch):
        _seed_session(db_path)
        monkeypatch.setattr(engine, "_deliver_digest", lambda digest: {"slack": False, "desktop": True})
        digest = engine.run_agent_standup(deliver=True, db_path=db_path, today=MONDAY)
        assert any("Delivery failed: slack" in w for w in digest.warnings)

    def test_no_webhook_reports_false(self, monkeypatch, db_path):
        from yeaboi import config as config_mod

        monkeypatch.setattr(config_mod, "get_slack_webhook_url", lambda: "", raising=False)
        monkeypatch.setattr("yeaboi.standup.delivery.notify_desktop", lambda title, body: False)
        _seed_session(db_path)
        digest = engine.run_agent_standup(deliver=True, db_path=db_path, today=MONDAY)
        assert any("Delivery failed" in w for w in digest.warnings)

    def test_history_recorded(self, db_path):
        _seed_session(db_path)
        engine.run_agent_standup(db_path=db_path, today=MONDAY)
        with AgentWatchStore(db_path) as store:
            rows = store.list_reports("standup")
        assert rows[0]["report"]["sessions_worked"] == 1


class TestBuilders:
    def test_markdown_and_plaintext(self, db_path, monkeypatch):
        import yeaboi.analysis.ai_usage as ai_usage
        from yeaboi.agentwatch.export import build_standup_markdown, build_standup_plaintext

        monkeypatch.setattr(ai_usage, "collect_ai_activity", lambda *a, **kw: ([AGENT_PR], [], [], []))
        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        md = build_standup_markdown(digest)
        assert md.startswith("# Agent Standup — 2026-08-10")
        assert "## Local sessions" in md
        assert "add retry logic" in md
        text = build_standup_plaintext(digest)
        assert "*Agent Standup — 2026-08-10*" in text
        assert "$30.00" in text

    def test_rich_render(self, db_path):
        from rich.console import Console

        from yeaboi.agentwatch.render import format_standup_rich

        _seed_session(db_path)
        digest = engine.run_agent_standup(db_path=db_path, today=MONDAY)
        console = Console(width=100)
        with console.capture() as cap:
            console.print(format_standup_rich(digest))
        out = cap.get()
        assert "Agent Standup" in out
        assert "webapp" in out
