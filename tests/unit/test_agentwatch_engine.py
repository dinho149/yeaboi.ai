"""Tests for src/yeaboi/agentwatch/engine.py — run_agent_usage."""

from datetime import date

import pytest

from yeaboi.agentwatch import engine
from yeaboi.agentwatch.store import AgentWatchStore

TODAY = date(2026, 8, 8)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def seeded(db_path):
    """Two sessions in-window (different projects/sources), one out-of-window."""
    rows = [
        (
            "s1",
            "claude_code",
            "/home/dev/webapp",
            "2026-08-07T10:00:00+00:00",
            {
                # opus-5: $5/$25 → 1M in + 1M out = $30
                "claude-opus-5": {"input": 1_000_000, "output": 1_000_000, "calls": 3},
            },
        ),
        (
            "s2",
            # A second source written straight into the store. _source_roots()
            # yields only Claude Code today, but the store, the by_source
            # breakdown and the --source filter are all keyed on this label —
            # so this is what proves none of them are hardcoded to one tool.
            "codex_cli",
            "/home/dev/api",
            "2026-08-06T09:00:00+00:00",
            {
                # haiku: $1/$5 → 1M in = $1; cache read 1M at 0.1*$1 = $0.10
                "claude-haiku-4-5": {"input": 1_000_000, "output": 0, "cache_read": 1_000_000, "calls": 1},
            },
        ),
        (
            "old",
            "claude_code",
            "/home/dev/webapp",
            "2026-01-01T09:00:00+00:00",
            {
                "claude-opus-5": {"input": 9_000_000, "output": 9_000_000, "calls": 9},
            },
        ),
    ]
    with AgentWatchStore(db_path) as store:
        for sid, source, project, ended, usage in rows:
            store.upsert_session(
                sid,
                source=source,
                source_path=f"/x/{sid}.jsonl",
                project_path=project,
                git_branch="main",
                cli_version="2.1.0",
                started_at=ended,
                ended_at=ended,
                turns=1,
                model_usage=usage,
                tool_counts={},
            )
    return db_path


@pytest.fixture(autouse=True)
def no_ingest_no_export(monkeypatch):
    """Keep the engine off the real ~/.claude and ~/.yeaboi trees."""
    from yeaboi.agentwatch.collector import IngestStats

    monkeypatch.setattr(engine.collector, "refresh", lambda store, **kw: IngestStats())
    import yeaboi.agentwatch.export as export_mod

    monkeypatch.setattr(export_mod, "export_artifact", lambda artifact, *, kind: {})


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Default every test to the unconfigured-LLM path (deterministic fallback)."""
    import yeaboi.config

    monkeypatch.setattr(yeaboi.config, "is_llm_configured", lambda: (False, "no API key set"))


class TestAggregation:
    def test_totals_and_cost_math(self, seeded):
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        assert report.session_count == 2  # the January session is out of window
        assert report.total_cost_usd == pytest.approx(30.0 + 1.1, abs=0.01)
        assert report.total_input_tokens == 2_000_000
        assert report.total_cache_read_tokens == 1_000_000
        assert report.pricing_as_of  # honesty stamp travels with the artifact

    def test_by_model_sorted_by_cost(self, seeded):
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        assert [r.model for r in report.by_model] == ["claude-opus-5", "claude-haiku-4-5"]
        assert report.by_model[0].cost_usd == pytest.approx(30.0)
        assert all(r.known_pricing for r in report.by_model)

    def test_breakdowns_and_trend(self, seeded):
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        assert [r.key for r in report.by_project] == ["webapp", "api"]
        assert {r.key for r in report.by_source} == {"claude_code", "codex_cli"}
        assert [p.date for p in report.daily_trend] == ["2026-08-06", "2026-08-07"]

    def test_window_filter(self, seeded):
        report = engine.run_agent_usage(window_days=1, db_path=seeded, today=date(2026, 8, 7))
        assert report.session_count == 1
        assert report.by_project[0].key == "webapp"

    def test_project_and_source_filters(self, seeded):
        by_project = engine.run_agent_usage(window_days=30, project="api", db_path=seeded, today=TODAY)
        assert by_project.session_count == 1
        assert by_project.by_source[0].key == "codex_cli"
        by_source = engine.run_agent_usage(window_days=30, source="claude_code", db_path=seeded, today=TODAY)
        assert by_source.session_count == 1
        assert by_source.by_project[0].key == "webapp"

    def test_unknown_model_share_flagged(self, db_path):
        with AgentWatchStore(db_path) as store:
            store.upsert_session(
                "s9",
                source="claude_code",
                source_path="/x/s9.jsonl",
                project_path="/p",
                git_branch="",
                cli_version="",
                started_at="2026-08-07T10:00:00+00:00",
                ended_at="2026-08-07T10:00:00+00:00",
                turns=1,
                model_usage={"mystery-9000": {"input": 1_000_000, "output": 0, "calls": 1}},
                tool_counts={},
            )
        report = engine.run_agent_usage(window_days=30, db_path=db_path, today=TODAY)
        assert report.unknown_model_cost_share == 1.0
        assert report.by_model[0].known_pricing is False


class TestFallbackAndLlm:
    def test_no_sessions_is_a_warning_not_a_crash(self, db_path):
        report = engine.run_agent_usage(window_days=30, db_path=db_path, today=TODAY)
        assert report.session_count == 0
        assert any("No local agent sessions" in w for w in report.warnings)

    def test_unconfigured_llm_falls_back_to_deterministic_insights(self, seeded):
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        assert report.insights  # deterministic evidence lines
        assert any("claude-opus-5" in line for line in report.insights)
        assert any("AI output unavailable" in w for w in report.warnings)

    def test_llm_prose_is_used_when_available(self, seeded, monkeypatch):
        monkeypatch.setattr(
            engine,
            "_invoke_llm",
            lambda prompt, *, what: (
                {"insights": ["spend is concentrated"], "recommendations": ["use haiku for drafts"]},
                [],
            ),
        )
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        assert report.insights == ("spend is concentrated",)
        assert report.recommendations == ("use haiku for drafts",)
        assert not any("AI output unavailable" in w for w in report.warnings)

    def test_partial_llm_reply_keeps_its_recommendations(self, seeded, monkeypatch):
        # Insights empty but recommendations usable: the fallback supplies the
        # insights and must NOT wipe the recommendations, which it always
        # returns empty by design.
        monkeypatch.setattr(
            engine,
            "_invoke_llm",
            lambda prompt, *, what: ({"insights": [], "recommendations": ["switch drafts to haiku"]}, []),
        )
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        assert report.recommendations == ("switch drafts to haiku",)
        assert report.insights  # deterministic fallback filled the empty half

    def test_dry_run_never_calls_the_llm(self, seeded, monkeypatch):
        def _boom(*a, **kw):
            raise AssertionError("dry_run must not invoke the LLM")

        monkeypatch.setattr(engine, "_invoke_llm", _boom)
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY, dry_run=True)
        assert report.session_count == 2
        assert report.insights  # deterministic fallback lines

    def test_llm_numbers_never_leak_into_totals(self, seeded, monkeypatch):
        # Even a hostile LLM reply can't change the artifact's numbers.
        monkeypatch.setattr(
            engine, "_invoke_llm", lambda prompt, *, what: ({"insights": ["x"], "total_cost_usd": 999999}, [])
        )
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        assert report.total_cost_usd == pytest.approx(31.1, abs=0.01)


class TestPersistence:
    def test_report_history_recorded(self, seeded):
        engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        with AgentWatchStore(seeded) as store:
            rows = store.list_reports("usage")
        assert len(rows) == 1
        assert rows[0]["report"]["session_count"] == 2

    def test_export_failure_never_sinks_the_run(self, seeded, monkeypatch):
        import yeaboi.agentwatch.export as export_mod

        def _boom(artifact, *, kind):
            raise OSError("disk full")

        monkeypatch.setattr(export_mod, "export_artifact", _boom)
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        assert report.session_count == 2


class TestProgressPhases:
    """The engine brackets each phase with structured lifecycle events."""

    def test_dry_run_phase_sequence(self, seeded):
        from yeaboi.analysis.progress import is_component_progress

        events: list = []
        engine.run_agent_usage(db_path=seeded, today=TODAY, dry_run=True, on_progress=events.append)
        assert all(is_component_progress(e) for e in events)
        assert [(e["component_id"], e["status"]) for e in events] == [
            ("scan", "running"),
            ("scan", "completed"),
            ("price", "running"),
            ("price", "completed"),
            ("insights", "no_data"),
        ]
        # The scan's terminal event carries parsed/cached counts, not filenames.
        assert events[1]["detail"] == "0 parsed · 0 cached"

    def test_llm_unavailable_marks_insights_fallback(self, seeded):
        events: list = []
        engine.run_agent_usage(db_path=seeded, today=TODAY, on_progress=events.append)
        seq = [(e["component_id"], e["status"]) for e in events]
        assert ("insights", "running") in seq
        assert ("insights", "fallback") in seq


class TestGoDispatch:
    """The YEABOI_GO pilot seam: Go results hydrate; any failure → Python."""

    def test_dispatch_error_returns_none_for_fallback(self, monkeypatch, db_path):
        from yeaboi.gocore import CoreError

        class BrokenClient:
            def request(self, *args, **kwargs):
                raise CoreError("sidecar exploded")

        monkeypatch.setattr(engine, "_go_client", lambda: BrokenClient())
        assert (
            engine._go_standup_digest(
                window_start="2026-08-07", digest_date="2026-08-08", db_path=db_path, on_progress=None
            )
            is None
        )
        assert engine._go_security_report(scan_date="2026-08-08", deep=False, db_path=db_path, on_progress=None) is None

    def test_no_client_means_python_path(self, monkeypatch, db_path):
        monkeypatch.setattr(engine, "_go_client", lambda: None)
        assert (
            engine._go_usage_report(
                window_days=1, project="", source="", db_path=db_path, today=TODAY, on_progress=None
            )
            is None
        )
        assert (
            engine._go_standup_digest(
                window_start="2026-08-07", digest_date="2026-08-08", db_path=db_path, on_progress=None
            )
            is None
        )
        assert engine._go_security_report(scan_date="2026-08-08", deep=False, db_path=db_path, on_progress=None) is None

    def test_run_agent_usage_builds_on_the_go_artifact(self, monkeypatch, seeded):
        canned_artifact = {
            "period_start": "2026-07-11",
            "period_end": "2026-08-09",
            "session_count": 2,
            "total_cost_usd": 1.23,
            "by_model": [
                {
                    "model": "claude-opus-5",
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "cache_write_tokens": 0,
                    "cache_read_tokens": 0,
                    "calls": 1,
                    "cost_usd": 1.23,
                    "known_pricing": True,
                }
            ],
            "by_project": [],
            "by_source": [],
            "daily_trend": [],
            "insights": [],
            "recommendations": [],
            "warnings": ["scan warning from go"],
            "generated_at": "",
        }

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                assert method == "agentwatch.usage"
                assert params["window_days"] == 30
                return {"contract_version": 1, "stats": {}, "artifact": canned_artifact}

        monkeypatch.setattr(engine, "_go_client", lambda: FakeClient())
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY, dry_run=True)
        assert report.total_cost_usd == 1.23  # Go's numbers, untouched
        assert report.session_count == 2
        assert "scan warning from go" in report.warnings
        assert report.generated_at  # Python stamps time
        assert report.insights  # Python fills prose (fallback under dry_run)

    def test_malformed_go_artifact_falls_back_to_python(self, monkeypatch, seeded):
        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                return {"contract_version": 1, "stats": {}, "artifact": "not a dict"}

        monkeypatch.setattr(engine, "_go_client", lambda: FakeClient())
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY, dry_run=True)
        # The seeded fixture has real sessions — the Python path priced them.
        assert report.session_count == 2

    def test_run_agent_standup_builds_on_the_go_digest(self, monkeypatch, db_path):
        canned_artifact = {
            "digest_date": "2026-08-08",
            "window_start": "2026-08-07",
            "window_end": "2026-08-08",
            "sessions_worked": 1,
            "total_cost_usd": 12.5,
            "agents_seen": ["claude_code"],
            "session_summaries": [
                {
                    "session_id": "s1",
                    "source": "claude_code",
                    "project": "webapp",
                    "branch": "main",
                    "models": ["claude-opus-5"],
                    "turns": 4,
                    "cost_usd": 12.5,
                    "top_tools": [["Bash", "7"]],
                    "started_at": "2026-08-07T10:00:00+00:00",
                    "ended_at": "2026-08-07T11:00:00+00:00",
                }
            ],
            "repo_activity": [],
            "highlights": [],
            "in_flight": [],
            "attention_items": [],
            "narrative": "",
            "coverage_notes": [],
            "warnings": ["scan warning from go"],
            "generated_at": "",
        }

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                assert method == "agentwatch.standup"
                assert params["window_start"] == "2026-08-07"
                return {"contract_version": 1, "stats": {}, "artifact": canned_artifact}

        monkeypatch.setattr(engine, "_go_client", lambda: FakeClient())
        digest = engine.run_agent_standup(tracker_sources=[], db_path=db_path, today=date(2026, 8, 8), dry_run=True)
        assert digest.total_cost_usd == 12.5  # Go's numbers, untouched
        assert digest.session_summaries[0].top_tools == (("Bash", "7"),)
        assert "scan warning from go" in digest.warnings
        assert any("skipped" in note for note in digest.coverage_notes)  # Python's tracker leg still ran
        assert digest.narrative  # Python fills prose (fallback under dry_run)
        assert digest.generated_at  # Python stamps time

    def test_run_agent_security_builds_on_the_go_report(self, monkeypatch, db_path):
        canned_artifact = {
            "scan_date": "2026-08-08",
            "posture": "needs-attention",
            "sessions_scanned": 5,
            "files_scanned": 6,
            "secrets_found": 1,
            "findings": [
                {
                    "severity": "high",
                    "category": "secret",
                    "title": "Credential-shaped text in a session transcript",
                    "location": "/x/s1.jsonl",
                    "line_no": 3,
                    "pattern": "secret-sk-ant",
                    "detail": "",
                    "remediation": "Rotate the credential; avoid pasting secrets into agent sessions.",
                }
            ],
            "mcp_servers": [],
            "settings_flags": [],
            "summary": "",
            "recommendations": [],
            "warnings": [],
            "generated_at": "",
        }

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                assert method == "agentwatch.security"
                assert params["reset_cursors"] is True
                assert params["claude_dir"] and params["claude_json"]
                return {"contract_version": 1, "stats": {}, "artifact": canned_artifact}

        monkeypatch.setattr(engine, "_go_client", lambda: FakeClient())
        report = engine.run_agent_security(deep=True, db_path=db_path, today=date(2026, 8, 8), dry_run=True)
        assert report.posture == "needs-attention"  # Go's ranking, untouched
        assert report.findings[0].pattern == "secret-sk-ant"
        assert report.summary  # Python fills prose (fallback under dry_run)
        assert report.recommendations == (canned_artifact["findings"][0]["remediation"],)
        assert report.generated_at

    def test_malformed_go_security_artifact_falls_back_to_python(self, monkeypatch, db_path):
        from yeaboi.agentwatch import security_checks

        class FakeClient:
            def request(self, method, params, on_progress=None, timeout=None):
                return {"contract_version": 1, "stats": {}, "artifact": None}

        monkeypatch.setattr(engine, "_go_client", lambda: FakeClient())
        # Keep the Python fallback's audit off the real ~/.claude.
        empty = db_path.parent / "claude-home"
        monkeypatch.setattr(security_checks, "_config_roots", lambda: (empty / ".claude", empty / ".claude.json"))
        report = engine.run_agent_security(db_path=db_path, today=date(2026, 8, 8), dry_run=True)
        # The Python path audited the (test-isolated) configs and produced a report.
        assert report.scan_date == "2026-08-08"
        assert report.summary
