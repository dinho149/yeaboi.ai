"""The /api/ceremonies, /api/slack and /api/agents routes.

Socketless, over ``AppServer.handle()``. The subject is the wire — what a page
payload carries, which requests are refused and why, and the NDJSON line order.
The decisions underneath belong to ``ceremonies/setup.py`` and
``agentwatch/setup.py`` and are tested there.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.agent.state import AgentUsageReport, CeremonyRun
from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.ceremonies.store import CeremonyStore

TOKEN = "test-token"


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A throwaway store, a fixed session and a scheduler that only records."""
    from yeaboi.ceremonies import setup

    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    monkeypatch.setattr(setup, "current_session", lambda: "s1")
    installed: set[str] = set()
    monkeypatch.setattr(
        setup.scheduler, "install_ceremony", lambda sid, name, at, wd: (installed.add(name), "installed")[1]
    )
    monkeypatch.setattr(setup.scheduler, "remove_ceremony", lambda sid, name: (installed.discard(name), "removed")[1])
    monkeypatch.setattr(setup.scheduler, "installed_ceremonies", lambda sid: sorted(installed))
    return {"db": db, "installed": installed}


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    body_bytes = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, headers, body_bytes))


def body(response) -> dict:
    assert response.code == 200, response.body
    return json.loads(response.body)


def drain(response) -> list[dict]:
    assert response.code == 200, response.body
    assert response.content_type == "application/x-ndjson"
    return [json.loads(line) for line in b"".join(response.stream).decode().splitlines()]


def _declare(app, **overrides):
    payload = {"name": "morning", "mode": "standup", "at": "09:00", "channels": ["terminal"], **overrides}
    return request(app, "POST", "/api/ceremonies", payload)


# ---------------------------------------------------------------------------
# Ceremonies
# ---------------------------------------------------------------------------


class TestCeremoniesPage:
    def test_an_empty_session_carries_the_catalog_and_the_hint(self, app, env):
        payload = body(request(app, "GET", "/api/ceremonies"))
        assert payload["ceremonies"] == []
        assert payload["modes"] and {"key", "label", "params"} <= set(payload["modes"][0])
        assert "terminal" in payload["channels"]
        assert "yeaboi ceremonies add" in payload["add_hint"]

    def test_a_declared_ceremony_carries_its_cadence_and_last_run(self, app, env):
        _declare(app)
        with CeremonyStore(env["db"]) as store:
            store.record_run(CeremonyRun(ceremony="morning", session_id="s1", outcome="ok", cost_usd=0.25))
        row = body(request(app, "GET", "/api/ceremonies"))["ceremonies"][0]
        assert row["name"] == "morning"
        assert row["cadence"] == "Mon–Fri at 09:00"
        assert row["next_fire"] == "Mon–Fri at 09:00"
        assert row["last_run"]["outcome"] == "ok"
        assert row["month_spend_usd"] == pytest.approx(0.25)

    def test_a_paused_row_reports_the_pause_not_a_schedule(self, app, env):
        _declare(app)
        request(app, "POST", "/api/ceremonies/morning/enabled", {"enabled": False})
        row = body(request(app, "GET", "/api/ceremonies"))["ceremonies"][0]
        assert row["next_fire"] == "paused"

    def test_a_job_with_no_declaration_shows_as_drift(self, app, env):
        env["installed"].add("ghost")
        assert "not declared here" in body(request(app, "GET", "/api/ceremonies"))["drift"][0]


class TestDeclare:
    def test_it_saves_installs_and_names_the_equivalent_command(self, app, env):
        payload = body(_declare(app))
        assert payload["ceremony"]["name"] == "morning"
        assert payload["scheduler"] == "installed"
        assert payload["command"] == "yeaboi ceremonies add morning --mode standup --at 09:00"
        assert env["installed"] == {"morning"}

    def test_engine_args_reach_the_stored_ceremony(self, app, env):
        payload = body(_declare(app, args={"days": "3"}))
        assert payload["ceremony"]["args"] == [["days", "3"]]

    def test_a_refused_name_is_a_400_naming_the_rule(self, app, env):
        response = _declare(app, name="Morning Standup!")
        assert response.code == 400
        assert b"lowercase" in response.body
        assert env["installed"] == set()

    def test_a_ceremony_with_no_channel_is_refused(self, app, env):
        response = _declare(app, channels=[])
        assert response.code == 400
        assert b"tell nobody" in response.body

    def test_an_unknown_mode_is_a_400(self, app, env):
        assert _declare(app, mode="nonsense").code == 400

    def test_the_default_channel_is_desktop_not_terminal(self, app, env):
        # This process reserves stdout for the handshake and drops the terminal
        # channel from every fan-out, so defaulting to it would declare a
        # ceremony that runs and reaches nobody.
        payload = {"name": "morning", "mode": "standup"}
        stored = body(request(app, "POST", "/api/ceremonies", payload))["ceremony"]
        assert stored["channels"] == ["desktop"]


class TestPauseResumeRemove:
    def test_pause_takes_the_job_down_and_keeps_the_declaration(self, app, env):
        _declare(app)
        payload = body(request(app, "POST", "/api/ceremonies/morning/enabled", {"enabled": False}))
        assert payload["ceremony"]["enabled"] is False
        assert env["installed"] == set()
        with CeremonyStore(env["db"]) as store:
            assert store.get("s1", "morning") is not None

    def test_resume_puts_the_job_back(self, app, env):
        _declare(app)
        request(app, "POST", "/api/ceremonies/morning/enabled", {"enabled": False})
        body(request(app, "POST", "/api/ceremonies/morning/enabled", {"enabled": True}))
        assert env["installed"] == {"morning"}

    def test_pausing_something_unknown_is_a_404(self, app, env):
        assert request(app, "POST", "/api/ceremonies/nope/enabled", {"enabled": False}).code == 404

    def test_remove_drops_the_declaration_and_the_job(self, app, env):
        _declare(app)
        assert body(request(app, "POST", "/api/ceremonies/morning/remove", {}))["removed"] is True
        assert env["installed"] == set()
        assert body(request(app, "GET", "/api/ceremonies"))["ceremonies"] == []

    def test_removing_something_unknown_is_a_404(self, app, env):
        assert request(app, "POST", "/api/ceremonies/nope/remove", {}).code == 404


class TestCeremonyRun:
    def test_progress_then_done_with_a_summary(self, app, env, monkeypatch):
        _declare(app)

        def _fake(name, *, session_id="", dry_run=False, suppress_terminal=False, on_progress=None, **kw):
            on_progress("gathering")
            on_progress("delivering")
            return CeremonyRun(
                ceremony=name, session_id=session_id, outcome="ok", cost_usd=0.5, delivery=(("slack", True),)
            )

        monkeypatch.setattr("yeaboi.ceremonies.engine.run_ceremony", _fake)
        lines = drain(request(app, "POST", "/api/ceremonies/morning/run", {}))
        assert [line["type"] for line in lines] == ["progress", "progress", "done"]
        assert [line["phase"] for line in lines[:2]] == ["gathering", "delivering"]
        assert lines[-1]["summary"] == "morning ran ($0.50) → slack"

    def test_a_run_carries_no_op_line_because_nothing_can_cancel_it(self, app, env, monkeypatch):
        _declare(app)
        monkeypatch.setattr(
            "yeaboi.ceremonies.engine.run_ceremony",
            lambda name, **kw: CeremonyRun(ceremony=name, outcome="ok"),
        )
        assert all(line["type"] != "op" for line in drain(request(app, "POST", "/api/ceremonies/morning/run", {})))

    def test_the_terminal_channel_is_suppressed(self, app, env, monkeypatch):
        _declare(app)
        seen = {}

        def _fake(name, **kwargs):
            seen.update(kwargs)
            return CeremonyRun(ceremony=name, outcome="ok")

        monkeypatch.setattr("yeaboi.ceremonies.engine.run_ceremony", _fake)
        drain(request(app, "POST", "/api/ceremonies/morning/run", {"dry_run": True}))
        assert seen["suppress_terminal"] is True
        assert seen["dry_run"] is True

    def test_an_engine_that_raises_becomes_an_error_line(self, app, env, monkeypatch):
        _declare(app)

        def _boom(name, **kwargs):
            raise RuntimeError("the store is locked")

        monkeypatch.setattr("yeaboi.ceremonies.engine.run_ceremony", _boom)
        lines = drain(request(app, "POST", "/api/ceremonies/morning/run", {}))
        assert lines[-1]["type"] == "error"
        assert "the store is locked" in lines[-1]["message"]

    def test_running_something_unknown_is_a_404_before_the_stream(self, app, env):
        assert request(app, "POST", "/api/ceremonies/nope/run", {}).code == 404


# ---------------------------------------------------------------------------
# The inbound Slack lane
# ---------------------------------------------------------------------------


class TestSlack:
    def test_a_write_only_lane_says_why_and_reads_no_ledger(self, app, env, monkeypatch):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (False, "no SLACK_BOT_TOKEN"))
        called = []
        monkeypatch.setattr("yeaboi.slack.engine.inbound_history", lambda **kw: called.append(kw) or {})
        payload = body(request(app, "GET", "/api/slack"))
        assert payload["two_way"] is False
        assert payload["why"] == "no SLACK_BOT_TOKEN"
        assert payload["events"] == []
        assert called == [], "a lane that cannot read must not query the ledger"

    def test_a_live_lane_carries_identities_and_history(self, app, env, monkeypatch):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (True, ""))
        monkeypatch.setattr(
            "yeaboi.slack.identity.listing", lambda sid, db_path=None: [{"slack_user": "U1", "member": "Ada"}]
        )
        monkeypatch.setattr("yeaboi.ceremonies.scheduler.slack_poll_status", lambda session_id="": {"interval_min": 15})
        monkeypatch.setattr(
            "yeaboi.slack.engine.inbound_history",
            lambda **kw: {"events": [{"verb": "approve"}], "recent_polls": [{"outcome": "ok"}]},
        )
        payload = body(request(app, "GET", "/api/slack"))
        assert payload["linked"] == 1
        assert payload["interval_min"] == 15
        assert payload["events"] == [{"verb": "approve"}]

    def test_an_unreadable_ledger_does_not_sink_the_page(self, app, env, monkeypatch):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.slack.identity.listing", lambda sid, db_path=None: [])
        monkeypatch.setattr("yeaboi.ceremonies.scheduler.slack_poll_status", lambda session_id="": {"interval_min": 0})

        def _boom(**kw):
            raise RuntimeError("db locked")

        monkeypatch.setattr("yeaboi.slack.engine.inbound_history", _boom)
        assert body(request(app, "GET", "/api/slack"))["events"] == []

    def test_link_binds_and_returns_the_new_listing(self, app, env, monkeypatch):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.slack.identity.link", lambda sid, user, member, db_path=None: "linked Ada")
        monkeypatch.setattr(
            "yeaboi.slack.identity.listing", lambda sid, db_path=None: [{"slack_user": "U1", "member": "Ada"}]
        )
        monkeypatch.setattr("yeaboi.ceremonies.scheduler.slack_poll_status", lambda session_id="": {"interval_min": 0})
        payload = body(request(app, "POST", "/api/slack/link", {"slack_user": "U1", "member": "Ada"}))
        assert payload["linked"] == "linked Ada"
        assert payload["identities"] == [{"slack_user": "U1", "member": "Ada"}]

    def test_unlink_drops_the_binding(self, app, env, monkeypatch):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (False, "no token"))
        monkeypatch.setattr("yeaboi.slack.identity.unlink", lambda sid, user, db_path=None: True)
        payload = body(request(app, "POST", "/api/slack/link", {"slack_user": "U1", "unlink": True}))
        assert payload["unlinked"] is True

    def test_a_link_with_no_slack_id_is_a_400(self, app, env):
        assert request(app, "POST", "/api/slack/link", {"member": "Ada"}).code == 400

    def test_a_declined_poll_is_answered_not_raised(self, app, env, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.slack.engine.apply_inbound_events",
            lambda **kw: {"outcome": "declined", "declined": True, "events_applied": 0, "events_seen": 0},
        )
        payload = body(request(app, "POST", "/api/slack/poll", {}))
        assert payload["declined"] is True


# ---------------------------------------------------------------------------
# The Agents family
# ---------------------------------------------------------------------------


class TestAgentModes:
    def test_the_three_modes_come_back_with_the_beta_caveat(self, app, env):
        from yeaboi.beta import AGENTWATCH_BETA_NOTICE

        payload = body(request(app, "GET", "/api/agents/modes"))
        assert [m["kind"] for m in payload["modes"]] == ["usage", "advisor", "security"]
        assert payload["beta_notice"] == AGENTWATCH_BETA_NOTICE
        assert payload["actions"] == ["Export", "Copy", "Re-run", "Back"]
        assert payload["fresh_minutes"] == 60


class TestAgentLatest:
    def test_no_history_is_a_null_report_not_a_404(self, app, env):
        payload = body(request(app, "GET", "/api/agents/usage/latest"))
        assert payload["report"] is None
        assert payload["as_of"] == ""

    def test_a_saved_report_comes_back_stamped(self, app, env):
        from yeaboi.agentwatch.store import AgentWatchStore

        with AgentWatchStore(env["db"]) as store:
            store.record_report("usage", AgentUsageReport(period_start="2026-07-01", total_cost_usd=9.99))
        payload = body(request(app, "GET", "/api/agents/usage/latest"))
        assert payload["report"]["total_cost_usd"] == pytest.approx(9.99)
        assert payload["as_of"]
        # Just recorded, so within the freshness window: the surface must not re-run.
        assert payload["fresh"] is True

    def test_a_stale_report_is_flagged_so_the_surface_re_runs(self, app, env, monkeypatch):
        from yeaboi.agentwatch.store import AgentWatchStore

        with AgentWatchStore(env["db"]) as store:
            store.record_report("usage", AgentUsageReport(period_start="2026-07-01"))
        monkeypatch.setenv("YEABOI_AGENTWATCH_FRESH_MINUTES", "0")
        assert body(request(app, "GET", "/api/agents/usage/latest"))["fresh"] is False

    def test_the_route_answers_to_the_mode_key_too(self, app, env):
        assert body(request(app, "GET", "/api/agents/agent-usage/latest"))["kind"] == "usage"

    def test_an_unknown_kind_is_a_404(self, app, env):
        assert request(app, "GET", "/api/agents/nonsense/latest").code == 404


class TestAgentRun:
    def test_bare_strings_and_component_dicts_are_separate_line_types(self, app, env, monkeypatch):
        from yeaboi.agentwatch import setup

        def _fake(mode, on_progress, **kw):
            on_progress("scanning")
            on_progress(
                {
                    "kind": "analysis_component",
                    "component_id": "scan",
                    "label": "Scanning",
                    "status": "completed",
                }
            )
            return AgentUsageReport(period_start="2026-07-01")

        monkeypatch.setattr(setup, "run", _fake)
        lines = drain(request(app, "POST", "/api/agents/usage/run", {}))
        assert [line["type"] for line in lines] == ["progress", "component", "done"]
        assert lines[0]["phase"] == "scanning"
        assert lines[1]["component"]["component_id"] == "scan"
        assert lines[2]["kind"] == "usage"

    def test_an_engine_that_raises_becomes_an_error_line(self, app, env, monkeypatch):
        from yeaboi.agentwatch import setup

        def _boom(mode, on_progress, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(setup, "run", _boom)
        lines = drain(request(app, "POST", "/api/agents/usage/run", {}))
        assert lines[-1]["type"] == "error"

    def test_running_an_unknown_kind_is_a_404_before_the_stream(self, app, env):
        assert request(app, "POST", "/api/agents/nonsense/run", {}).code == 404


class TestAgentScope:
    """`project_id` resolves to the project's repo_path; saved reports are machine-wide."""

    @pytest.fixture
    def project(self, env):
        from yeaboi.projects.engine import create_project, set_project_defaults

        pid = create_project("Apollo", db_path=env["db"])["project_id"]
        set_project_defaults(pid, {"repo_path": "/srv/apollo"}, db_path=env["db"])
        bare = create_project("Bare", db_path=env["db"])["project_id"]
        return {"pid": pid, "bare": bare}

    def test_unscoped_latest_carries_an_empty_scope(self, app, env):
        assert body(request(app, "GET", "/api/agents/usage/latest"))["scoped_to"] == ""

    def test_a_scoped_latest_is_null_with_the_repo_named(self, app, env, project):
        from yeaboi.agentwatch.store import AgentWatchStore

        with AgentWatchStore(env["db"]) as store:
            store.record_report("usage", AgentUsageReport(period_start="2026-07-01", total_cost_usd=9.99))
        payload = body(request(app, "GET", f"/api/agents/usage/latest?project_id={project['pid']}"))
        assert payload["report"] is None and payload["as_of"] == ""
        assert payload["scoped_to"] == "/srv/apollo"

    def test_security_ignores_the_project(self, app, env, project):
        payload = body(request(app, "GET", f"/api/agents/security/latest?project_id={project['pid']}"))
        assert payload["scoped_to"] == ""

    def test_run_passes_the_repo_to_the_engine_and_echoes_it(self, app, env, project, monkeypatch):
        from yeaboi.agentwatch import setup

        seen = {}

        def _fake(mode, on_progress, **kw):
            seen.update(kw)
            return AgentUsageReport(period_start="2026-07-01")

        monkeypatch.setattr(setup, "run", _fake)
        lines = drain(request(app, "POST", "/api/agents/usage/run", {"project_id": project["pid"]}))
        assert seen == {"project_path": "/srv/apollo", "options": {}}
        assert lines[-1]["type"] == "done" and lines[-1]["scoped_to"] == "/srv/apollo"

    def test_run_options_reach_the_engine(self, app, env, monkeypatch):
        from yeaboi.agentwatch import setup

        seen = {}

        def _fake(mode, on_progress, **kw):
            seen.update(kw)
            return AgentUsageReport(period_start="2026-07-01")

        monkeypatch.setattr(setup, "run", _fake)
        drain(request(app, "POST", "/api/agents/usage/run", {"window_days": 7, "include_info": True}))
        assert seen["options"] == {"window_days": 7, "include_info": True}
        assert request(app, "POST", "/api/agents/usage/run", {"window_days": "lots"}).code == 400

    def test_an_unknown_project_is_404(self, app, env, project):
        assert request(app, "GET", "/api/agents/usage/latest?project_id=proj-00000000").code == 404
        assert request(app, "POST", "/api/agents/usage/run", {"project_id": "proj-00000000"}).code == 404

    def test_a_project_without_a_repo_path_is_400_naming_the_command(self, app, env, project):
        resp = request(app, "GET", f"/api/agents/usage/latest?project_id={project['bare']}")
        assert resp.code == 400
        assert b"set-defaults" in resp.body


class TestAgentExport:
    def test_copy_is_answered_as_data(self, app, env):
        from yeaboi.agentwatch.store import AgentWatchStore

        with AgentWatchStore(env["db"]) as store:
            store.record_report("usage", AgentUsageReport(period_start="2026-07-01"))
        payload = body(request(app, "POST", "/api/agents/usage/export", {"destination": "copy"}))
        assert payload["markdown"].strip()

    def test_files_writes_and_names_the_directory(self, app, env, tmp_path, monkeypatch):
        from yeaboi.agentwatch.store import AgentWatchStore

        out = tmp_path / "exports"
        out.mkdir()
        monkeypatch.setattr("yeaboi.paths.get_agentwatch_export_dir", lambda kind: out)
        with AgentWatchStore(env["db"]) as store:
            store.record_report("usage", AgentUsageReport(period_start="2026-07-01"))
        payload = body(request(app, "POST", "/api/agents/usage/export", {"destination": "files"}))
        assert payload["ok"] is True
        assert list(out.glob("usage-*.md"))

    def test_exporting_with_nothing_saved_is_a_404(self, app, env):
        assert request(app, "POST", "/api/agents/usage/export", {"destination": "copy"}).code == 404

    def test_an_unknown_destination_is_a_400(self, app, env):
        assert request(app, "POST", "/api/agents/usage/export", {"destination": "notion"}).code == 400


class TestAuth:
    def test_every_m9_route_requires_the_token(self, app, env):
        for method, path in (
            ("GET", "/api/ceremonies"),
            ("POST", "/api/ceremonies"),
            ("GET", "/api/slack"),
            ("POST", "/api/slack/poll"),
            ("GET", "/api/agents/modes"),
            ("GET", "/api/agents/usage/latest"),
        ):
            assert request(app, method, path, {} if method == "POST" else None, authed=False).code == 401, path


class TestSecurityDismissals:
    def test_dismiss_needs_a_reason_then_lists_and_restores(self, app, env, monkeypatch, tmp_path):
        from yeaboi.agentwatch import dismissals

        monkeypatch.setattr(dismissals, "default_path", lambda: tmp_path / "allow.json")
        assert request(app, "POST", "/api/agents/security/dismiss", {"key": "secret:p:/a"}).code == 400
        assert request(app, "POST", "/api/agents/security/dismiss", {"reason": "x"}).code == 400
        payload = body(
            request(app, "POST", "/api/agents/security/dismiss", {"key": "secret:p:/a", "reason": "fixture"})
        )
        assert payload["entry"]["reason"] == "fixture"
        listed = body(request(app, "GET", "/api/agents/security/dismissed"))
        assert [d["key"] for d in listed["dismissed"]] == ["secret:p:/a"]
        restored = body(request(app, "POST", "/api/agents/security/dismiss", {"key": "secret:p:/a", "undo": True}))
        assert restored["restored"] == "secret:p:/a" and restored["dismissed"] == []
        assert request(app, "POST", "/api/agents/security/dismiss", {"key": "secret:p:/a", "undo": True}).code == 404

    def test_an_unscoped_run_echoes_an_empty_scope(self, app, env, monkeypatch):
        from yeaboi.agentwatch import setup

        monkeypatch.setattr(setup, "run", lambda mode, on_progress, **kw: AgentUsageReport(period_start="2026-07-01"))
        assert drain(request(app, "POST", "/api/agents/usage/run", {}))[-1]["scoped_to"] == ""


class TestSecurityActions:
    """The routes the redesigned page acts through: verdict, fix, replay, signals."""

    @pytest.fixture
    def seeded(self, env, tmp_path, monkeypatch):
        from yeaboi.agentwatch import dismissals, engine, security_checks
        from yeaboi.agentwatch.collector import IngestStats
        from yeaboi.agentwatch.store import AgentWatchStore

        monkeypatch.setattr(dismissals, "default_path", lambda: tmp_path / "allow.json")
        monkeypatch.setattr(security_checks, "_config_roots", lambda: (tmp_path / "dot-claude", tmp_path / "c.json"))
        monkeypatch.setattr(engine.collector, "refresh", lambda store, **kw: IngestStats())
        with AgentWatchStore(env["db"]) as store:
            store.add_finding(
                category="risky_tool",
                severity="high",
                pattern="curl-pipe-shell",
                source_path=str(tmp_path / "gone.jsonl"),
                line_no=5,
                session_id="s1",
                context="command",
                at="2026-08-23T10:00:00Z",
                snippet="[REDACTED curl-pipe-shell]",
            )
            store.add_finding(
                category="secret",
                severity="high",
                pattern="secret-anthropic-key",
                source_path=str(tmp_path / "b.jsonl"),
                line_no=2,
                session_id="s2",
                context="tool-result",
                target="/r/tests/test_x.py",
            )
        report = engine.run_agent_security(db_path=env["db"], dry_run=True)
        return {f.category: f for f in report.findings}

    def test_verdict_sets_many_aside_and_answers_with_the_report(self, app, seeded):
        assert request(app, "POST", "/api/agents/security/verdict", {"keys": []}).code == 400
        assert request(app, "POST", "/api/agents/security/verdict", {"keys": ["k"], "verdict": "nope"}).code == 400
        assert request(app, "POST", "/api/agents/security/verdict", {"keys": ["k"], "verdict": "dismiss"}).code == 400
        keys = [seeded["secret"].key]
        payload = body(request(app, "POST", "/api/agents/security/verdict", {"keys": keys, "verdict": "test-data"}))
        assert payload["handled"] == keys
        handled = [f for f in payload["report"]["findings"] if f["key"] == keys[0]]
        assert handled and handled[0]["verdict"] == "handled"
        assert payload["report"]["verdict_line"].startswith("One thing needs a decision.")
        undone = body(request(app, "POST", "/api/agents/security/verdict", {"keys": keys, "verdict": "undo"}))
        assert undone["restored"] == keys

    def test_dismiss_answers_with_the_rebuilt_report(self, app, seeded):
        key = seeded["risky_tool"].key
        payload = body(request(app, "POST", "/api/agents/security/dismiss", {"key": key, "reason": "known"}))
        assert payload["report"]["dismissed_count"] == 1
        assert payload["report"]["verdict_line"].startswith("Nothing needs a decision.")

    def test_fix_applies_and_reports(self, app, seeded):
        assert request(app, "POST", "/api/agents/security/fix", {"key": "k"}).code == 400
        key = seeded["risky_tool"].key
        payload = body(request(app, "POST", "/api/agents/security/fix", {"key": key, "fix_id": "mark-test-data"}))
        assert payload["ok"] and payload["handled"] == [key] and payload["report"]["dismissed_count"] == 1
        missing = body(request(app, "POST", "/api/agents/security/fix", {"key": "nope", "fix_id": "guard-hook"}))
        assert not missing["ok"] and "re-run" in missing["detail"]

    def test_fix_refused_by_the_sandbox_is_a_403(self, app, seeded, monkeypatch):
        monkeypatch.setattr("yeaboi.fs_policy.request_consent", lambda p, *, mode="read", context="": False)
        response = request(
            app, "POST", "/api/agents/security/fix", {"key": seeded["risky_tool"].key, "fix_id": "guard-hook"}
        )
        assert response.code == 403 and b"allow it and try again" in response.body

    def test_replay_and_signals(self, app, seeded):
        assert request(app, "GET", "/api/agents/security/replay?key=nope").code == 404
        key = seeded["risky_tool"].key
        gone = request(app, "GET", f"/api/agents/security/replay?key={key}")
        assert gone.code == 400 and b"no longer on disk" in gone.body
        signals = body(request(app, "GET", f"/api/agents/security/signals?key={key}"))
        assert signals["key"] == key
        assert [(s["line_no"], s["context"], s["snippet"]) for s in signals["signals"]] == [
            (5, "command", "[REDACTED curl-pipe-shell]")
        ]

    def test_latest_lists_info_on_request_without_a_scan(self, app, seeded, env):
        from yeaboi.agentwatch.store import AgentWatchStore

        with AgentWatchStore(env["db"]) as store:
            store.add_finding(
                category="secret",
                severity="info",
                pattern="tunnel-hostname",
                source_path="/t/c.jsonl",
                line_no=1,
                context="prose",
            )
        plain = body(request(app, "GET", "/api/agents/security/latest"))
        assert plain["report"]["hidden_info_count"] == 0  # the saved report predates the row
        shown = body(request(app, "GET", "/api/agents/security/latest?include_info=1"))
        assert shown["report"]["hidden_info_count"] == 0 and plain["report"]["hidden_info_count"] == 0
