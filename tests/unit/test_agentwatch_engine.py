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
            _seed(store, sid, source=source, project=project, ended=ended, usage=usage)
    return db_path


def _seed(store, sid, *, source="claude_code", project="/p", ended="2026-08-07T10:00:00+00:00", usage, days=None):
    """One session row plus its per-day rows (the whole usage on ``ended``'s day unless ``days`` splits it)."""
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
    store.replace_session_days(f"/x/{sid}.jsonl", days or {ended[:10]: usage})


@pytest.fixture(autouse=True)
def no_ingest_no_export(monkeypatch, tmp_path):
    """Keep the engine off the real ~/.claude and ~/.yeaboi trees."""
    from yeaboi.agentwatch import billing
    from yeaboi.agentwatch.collector import IngestStats

    monkeypatch.setattr(engine.collector, "refresh", lambda store, **kw: IngestStats())
    monkeypatch.setattr(engine, "_claude_stats_path", lambda: tmp_path / "no-stats.json")
    monkeypatch.setattr(billing, "_claude_config_path", lambda: tmp_path / "no-claude.json")
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
            _seed(store, "s9", usage={"mystery-9000": {"input": 1_000_000, "output": 0, "calls": 1}})
        report = engine.run_agent_usage(window_days=30, db_path=db_path, today=TODAY)
        assert report.unknown_model_cost_share == 1.0
        assert report.by_model[0].known_pricing is False

    def test_a_session_across_the_window_edge_contributes_only_its_inside_days(self, db_path):
        with AgentWatchStore(db_path) as store:
            _seed(
                store,
                "span",
                ended="2026-08-07T01:00:00+00:00",
                usage={"claude-opus-5": {"input": 3_000_000, "output": 0, "calls": 3}},
                days={
                    "2026-08-06": {"claude-opus-5": {"input": 2_000_000, "output": 0, "calls": 2}},
                    "2026-08-07": {"claude-opus-5": {"input": 1_000_000, "output": 0, "calls": 1}},
                },
            )
        one_day = engine.run_agent_usage(window_days=1, db_path=db_path, today=date(2026, 8, 7))
        assert one_day.total_cost_usd == pytest.approx(5.0)  # 1M in at $5
        assert [p.date for p in one_day.daily_trend] == ["2026-08-07"]
        both = engine.run_agent_usage(window_days=2, db_path=db_path, today=date(2026, 8, 7))
        assert both.total_cost_usd == pytest.approx(15.0)
        assert [(p.date, p.cost_usd) for p in both.daily_trend] == [("2026-08-06", 10.0), ("2026-08-07", 5.0)]

    def test_rows_stamped_after_today_stay_out_with_a_warning(self, db_path):
        with AgentWatchStore(db_path) as store:
            _seed(store, "future", ended="2026-09-01T10:00:00+00:00", usage={"claude-opus-5": {"input": 1_000_000}})
        report = engine.run_agent_usage(window_days=30, db_path=db_path, today=TODAY)
        assert report.total_cost_usd == 0
        assert any("after today" in w for w in report.warnings)

    def test_cache_cost_share_and_window_travel_on_the_artifact(self, seeded):
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        # haiku's 1M cache read at $0.10 out of $31.10.
        assert report.cache_cost_share == pytest.approx(0.1 / 31.1, abs=0.001)
        assert report.window_days == 30

    def test_a_recorded_cost_wins_over_the_rate_table(self, db_path):
        with AgentWatchStore(db_path) as store:
            _seed(store, "rec", usage={"claude-opus-5": {"input": 1_000_000, "recorded_cost_usd": 1.5}})
        report = engine.run_agent_usage(window_days=30, db_path=db_path, today=TODAY)
        assert report.total_cost_usd == pytest.approx(1.5)
        assert any("recorded by the CLI" in w for w in report.warnings)


class TestBillingAndCrossCheck:
    def test_subscription_is_named_on_the_artifact(self, seeded, tmp_path, monkeypatch):
        import json

        from yeaboi.agentwatch import billing

        cfg = tmp_path / "claude.json"
        cfg.write_text(json.dumps({"oauthAccount": {"billingType": "stripe_subscription"}}))
        monkeypatch.setattr(billing, "_claude_config_path", lambda: cfg)
        report = engine.run_agent_usage(window_days=30, db_path=seeded, today=TODAY)
        assert report.billing_kind == "subscription"

    def test_session_count_drift_against_claude_code_is_a_warning(self, db_path, tmp_path, monkeypatch):
        import json

        with AgentWatchStore(db_path) as store:
            for i in range(6):
                _seed(store, f"s{i}", usage={"claude-opus-5": {"input": 1000, "calls": 1}})
        stats = tmp_path / "stats.json"
        stats.write_text(json.dumps({"dailyActivity": [{"date": "2026-08-07", "sessionCount": 20}]}))
        monkeypatch.setattr(engine, "_claude_stats_path", lambda: stats)
        report = engine.run_agent_usage(window_days=30, db_path=db_path, today=TODAY)
        assert any("6 here vs 20" in w for w in report.warnings)

    def test_a_stale_stats_cache_is_compared_only_up_to_its_last_day(self, db_path, tmp_path, monkeypatch):
        import json

        with AgentWatchStore(db_path) as store:
            for i in range(6):
                _seed(store, f"s{i}", usage={"claude-opus-5": {"input": 1000, "calls": 1}})
        stats = tmp_path / "stats.json"
        # Computed before any of the seeded sessions: nothing to compare, no warning.
        stats.write_text(
            json.dumps(
                {"lastComputedDate": "2026-08-01", "dailyActivity": [{"date": "2026-08-01", "sessionCount": 40}]}
            )
        )
        monkeypatch.setattr(engine, "_claude_stats_path", lambda: stats)
        report = engine.run_agent_usage(window_days=30, db_path=db_path, today=TODAY)
        assert not any("stats cache" in w for w in report.warnings)


class TestProjectLabel:
    def test_a_worktree_labels_as_repo_slash_name(self):
        assert engine._project_label("/home/dev/yeaboi.ai/.claude/worktrees/agents/ai-native-sdlc") == (
            "yeaboi.ai/agents"
        )
        assert (
            engine._project_label("/home/dev/yeaboi-desktop/.claude/worktrees/feature-x") == "yeaboi-desktop/feature-x"
        )

    def test_a_plain_directory_labels_by_basename(self):
        assert engine._project_label("/home/dev/webapp") == "webapp"
        assert engine._project_label("") == "(unknown)"

    def test_a_subdirectory_of_a_git_repo_labels_by_the_toplevel(self, tmp_path, monkeypatch):
        import subprocess

        repo = tmp_path / "myrepo"
        (repo / "src" / "deep").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        engine._git_toplevel.cache_clear()
        assert engine._project_label(str(repo / "src" / "deep")) == "myrepo"

    def test_a_nested_worktree_name_keeps_both_segments_when_checked_out(self, tmp_path):
        import subprocess

        repo = tmp_path / "yeaboi.ai"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "root"], check=True)
        wt = repo / ".claude" / "worktrees" / "agents" / "ai-native-sdlc"
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", str(wt)], check=True)
        (wt / "src").mkdir()
        engine._git_toplevel.cache_clear()
        assert engine._project_label(str(wt / "src")) == "yeaboi.ai/agents/ai-native-sdlc"


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


class TestRepoScope:
    """project_path is an exact-or-prefix match on the session's directory, never a basename."""

    @pytest.mark.parametrize(
        ("session_path", "repo", "expected"),
        [
            ("/home/dev/webapp", "/home/dev/webapp", True),
            ("/home/dev/webapp/", "/home/dev/webapp", True),
            ("/home/dev/webapp/.claude/worktrees/feature", "/home/dev/webapp", True),
            ("/home/dev/webapp-v2", "/home/dev/webapp", False),
            ("/home/dev/other/webapp", "/home/dev/webapp", False),
            ("/srv/api", "/home/dev/api", False),
            ("/home/dev/webapp", "", True),
            ("/home/dev/./webapp", "/home/dev/webapp/", True),
            ("/home/dev/webapp/../webapp/src", "/home/dev/webapp", True),
            ("/home/dev/webapp/../other", "/home/dev/webapp", False),
        ],
    )
    def test_in_repo(self, session_path, repo, expected):
        assert engine._in_repo(session_path, repo) is expected

    def test_usage_keeps_only_sessions_under_the_repo(self, seeded):
        report = engine.run_agent_usage(window_days=30, project_path="/home/dev/api", db_path=seeded, today=TODAY)
        assert report.session_count == 1
        assert report.by_project[0].key == "api"

    def test_a_worktree_under_the_repo_counts(self, db_path):
        with AgentWatchStore(db_path) as store:
            _seed(
                store,
                "wt",
                project="/home/dev/webapp/.claude/worktrees/feature",
                usage={"claude-opus-5": {"input": 1_000, "output": 1_000, "calls": 1}},
            )
        report = engine.run_agent_usage(window_days=30, project_path="/home/dev/webapp", db_path=db_path, today=TODAY)
        assert report.session_count == 1
        assert report.by_project[0].key == "webapp/feature"

    def test_no_match_is_an_empty_report_not_a_crash(self, seeded):
        report = engine.run_agent_usage(window_days=30, project_path="/nowhere", db_path=seeded, today=TODAY)
        assert report.session_count == 0 and report.total_cost_usd == 0
