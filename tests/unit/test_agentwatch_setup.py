"""The Agents family's mode table (agentwatch/setup.py).

The table is the contract two surfaces read: a mode whose engine target,
artifact type or store kind is wrong fails here rather than at the moment
somebody presses Re-run.
"""

from __future__ import annotations

import pytest

from yeaboi.agent.state import AgentUsageReport
from yeaboi.agentwatch import setup
from yeaboi.agentwatch.store import AgentWatchStore


class TestTable:
    def test_the_three_modes_are_the_agents_family(self):
        assert [m.key for m in setup.MODES] == ["agent-usage", "agent-advisor", "agent-security"]

    def test_every_engine_target_resolves(self):
        for mode in setup.MODES:
            assert callable(setup._resolve(mode.engine)), mode.key

    def test_every_artifact_target_resolves_and_takes_warnings(self):
        for mode in setup.MODES:
            artifact = setup.failure_artifact(mode, RuntimeError("boom"))
            assert artifact.warnings and "boom" in artifact.warnings[0]

    def test_lookup_answers_to_both_the_key_and_the_kind(self):
        assert setup.lookup("agent-usage") is setup.lookup("usage")

    def test_an_unknown_key_is_none(self):
        assert setup.lookup("agent-nonsense") is None

    def test_require_names_the_valid_keys(self):
        with pytest.raises(ValueError, match="agent-usage"):
            setup.require("agent-nonsense")

    def test_the_kinds_match_the_export_builders(self):
        from yeaboi.agentwatch.export import _BUILDERS

        assert {m.kind for m in setup.MODES} == set(_BUILDERS)


class TestRun:
    def test_run_calls_the_engine_with_the_progress_callback(self, monkeypatch):
        seen = {}

        def _fake(*, on_progress):
            seen["cb"] = on_progress
            return "artifact"

        monkeypatch.setattr("yeaboi.agentwatch.engine.run_agent_usage", _fake)

        def sink(event):
            pass

        assert setup.run(setup.require("usage"), sink) == "artifact"
        assert seen["cb"] is sink

    def test_a_repo_path_reaches_only_the_scoped_modes(self, monkeypatch):
        seen: dict = {}

        def _usage(*, on_progress, project_path=""):
            seen["usage"] = project_path
            return "u"

        def _security(*, on_progress):
            seen["security"] = "called without a scope"
            return "s"

        monkeypatch.setattr("yeaboi.agentwatch.engine.run_agent_usage", _usage)
        monkeypatch.setattr("yeaboi.agentwatch.engine.run_agent_security", _security)
        assert setup.run(setup.require("usage"), None, project_path="/srv/app") == "u"
        assert setup.run(setup.require("security"), None, project_path="/srv/app") == "s"
        assert seen == {"usage": "/srv/app", "security": "called without a scope"}

    def test_the_scoped_flag_is_pinned(self):
        assert {m.kind: m.scoped for m in setup.MODES} == {
            "usage": True,
            "advisor": True,
            "security": False,
        }


class TestMarkdown:
    def test_a_usage_report_renders(self):
        text = setup.markdown(setup.require("usage"), AgentUsageReport(period_start="2026-07-01"))
        assert text.strip()


class TestLatestArtifact:
    def test_no_history_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        assert setup.latest_artifact("usage") is None

    def test_a_saved_report_comes_back_with_its_stamp(self, tmp_path, monkeypatch):
        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        with AgentWatchStore(db) as store:
            store.record_report("usage", AgentUsageReport(period_start="2026-07-01", total_cost_usd=9.99))
        loaded = setup.latest_artifact("usage")
        assert loaded is not None
        artifact, created_at = loaded
        assert artifact.total_cost_usd == pytest.approx(9.99)
        assert created_at

    def test_an_unreadable_store_is_none_not_an_exception(self, tmp_path, monkeypatch):
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")

        def _boom(_path):
            raise RuntimeError("db locked")

        monkeypatch.setattr("yeaboi.agentwatch.store.AgentWatchStore", _boom)
        assert setup.latest_artifact("usage") is None


class TestModeOptions:
    def test_every_mode_is_offered_with_its_last_report(self, tmp_path, monkeypatch):
        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        with AgentWatchStore(db) as store:
            store.record_report("usage", AgentUsageReport(period_start="2026-07-01"))
        options = setup.mode_options()
        assert [o["key"] for o in options] == [m.key for m in setup.MODES]
        assert next(o for o in options if o["kind"] == "usage")["last_report_at"]
        assert next(o for o in options if o["kind"] == "advisor")["last_report_at"] == ""


class TestFreshness:
    def test_recent_reports_are_fresh_and_old_ones_are_not(self, monkeypatch):
        from datetime import datetime, timedelta, timezone

        monkeypatch.setenv("YEABOI_AGENTWATCH_FRESH_MINUTES", "60")
        now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
        assert setup.is_fresh((now - timedelta(minutes=10)).isoformat(), now=now)
        assert not setup.is_fresh((now - timedelta(minutes=90)).isoformat(), now=now)
        # A naive stamp reads as UTC; junk and blanks are stale.
        assert setup.is_fresh("2026-08-08T11:55:00", now=now)
        assert not setup.is_fresh("", now=now)
        assert not setup.is_fresh("not a date", now=now)

    def test_zero_minutes_means_always_re_run(self, monkeypatch):
        from datetime import datetime, timezone

        monkeypatch.setenv("YEABOI_AGENTWATCH_FRESH_MINUTES", "0")
        assert not setup.is_fresh(datetime.now(timezone.utc).isoformat())

    def test_the_config_accessor_clamps_and_defaults(self, monkeypatch):
        from yeaboi.config import get_agentwatch_fresh_minutes

        monkeypatch.delenv("YEABOI_AGENTWATCH_FRESH_MINUTES", raising=False)
        assert get_agentwatch_fresh_minutes() == 60
        monkeypatch.setenv("YEABOI_AGENTWATCH_FRESH_MINUTES", "lots")
        assert get_agentwatch_fresh_minutes() == 60
        monkeypatch.setenv("YEABOI_AGENTWATCH_FRESH_MINUTES", "99999")
        assert get_agentwatch_fresh_minutes() == 24 * 60
        monkeypatch.setenv("YEABOI_AGENTWATCH_FRESH_MINUTES", "-5")
        assert get_agentwatch_fresh_minutes() == 0
