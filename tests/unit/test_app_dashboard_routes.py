"""The /api/standup and /api/analysis routes — socketless, over AppServer.handle().

The card vocabulary and the schedule action are tested in test_standup_dashboard.py,
test_standup_schedule.py and test_analysis_dashboard.py; here the subject is the
wire: what a dashboard read carries, the NDJSON run's line order and terminators,
and which requests are refused.
"""

from __future__ import annotations

import json

import pytest

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer

TOKEN = "test-token"


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    body = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, headers, body))


def drain(response) -> list[dict]:
    assert response.code == 200, response.body
    assert response.content_type == "application/x-ndjson"
    return [json.loads(line) for line in b"".join(response.stream).decode().splitlines()]


def _report(**kw) -> StandupReport:
    defaults = {
        "date": "2026-07-10",
        "team_summary": "steady progress",
        "member_updates": (
            MemberUpdate(name="Ana", summary="login page", activity_count=3),
            MemberUpdate(name="Bo", summary="No activity detected.", activity_count=0),
        ),
    }
    return StandupReport(**{**defaults, **kw})


def _collected(**kw) -> dict:
    base = {
        "message": "",
        "session_id": "s1",
        "session_name": "Barbers",
        "my_name": "Ana",
        "config": {"enabled": True, "time": "10:00"},
        "report": _report(),
        "schedule": {"installed": True},
        "review": None,
        "nudge": None,
        "gap_issues": [],
        "history": [{"id": 4, "standup_date": "2026-07-10"}],
        "run_id": 0,
    }
    return {**base, **kw}


class TestStandupDashboard:
    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/standup/dashboard", authed=False).code == 401

    def test_it_carries_the_cards_and_the_report(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.standup.dashboard.collect", lambda *a, **k: _collected())
        body = json.loads(request(app, "GET", "/api/standup/dashboard?session_id=s1").body)
        assert body["session_id"] == "s1"
        assert body["session_name"] == "Barbers"
        assert [c["key"] for c in body["cards"]] == ["summary", "my_update", "team", "activity", "schedule"]
        assert body["report"]["team_summary"] == "steady progress"
        assert body["config"] == {"enabled": True, "time": "10:00"}

    def test_the_active_list_uses_the_shared_rule(self, app, monkeypatch):
        # Bo's summary is the no-activity sentinel, so Bo is quiet.
        monkeypatch.setattr("yeaboi.standup.dashboard.collect", lambda *a, **k: _collected())
        assert json.loads(request(app, "GET", "/api/standup/dashboard").body)["active"] == ["Ana"]

    def test_a_session_with_no_report_still_answers(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.standup.dashboard.collect", lambda *a, **k: _collected(report=None))
        body = json.loads(request(app, "GET", "/api/standup/dashboard").body)
        assert body["report"] is None
        assert [c["key"] for c in body["cards"]] == ["schedule"]
        assert body["active"] == []

    def test_the_saved_runs_hub_rides_along(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.standup.dashboard.collect", lambda *a, **k: _collected())
        assert json.loads(request(app, "GET", "/api/standup/dashboard").body)["history"] == [
            {"id": 4, "standup_date": "2026-07-10"}
        ]

    def test_a_run_id_opens_that_past_run(self, app, monkeypatch):
        seen: list = []
        monkeypatch.setattr(
            "yeaboi.standup.dashboard.collect",
            lambda session_id="", run_id=0, **k: seen.append(run_id) or _collected(run_id=run_id),
        )
        body = json.loads(request(app, "GET", "/api/standup/dashboard?run_id=4").body)
        assert seen == [4] and body["run_id"] == 4

    def test_a_nonsense_run_id_is_refused(self, app):
        assert request(app, "GET", "/api/standup/dashboard?run_id=latest").code == 400

    def test_a_blank_session_id_means_the_latest(self, app, monkeypatch):
        seen: list = []
        monkeypatch.setattr(
            "yeaboi.standup.dashboard.collect", lambda session_id="", **k: seen.append(session_id) or _collected()
        )
        request(app, "GET", "/api/standup/dashboard")
        assert seen == [""]


class TestDeleteRun:
    def test_an_unknown_run_is_a_404(self, app, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "sessions.db")
        assert request(app, "POST", "/api/standup/runs/99/delete").code == 404

    def test_a_nonsense_run_id_is_refused(self, app):
        assert request(app, "POST", "/api/standup/runs/latest/delete").code == 400


class TestStandupSchedule:
    def test_reading_needs_a_session(self, app):
        assert request(app, "GET", "/api/standup/schedule").code == 400

    def test_it_returns_the_saved_schedule(self, app, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.standup.schedule.current_schedule", lambda sid, **k: {"session_id": sid, "enabled": True}
        )
        assert json.loads(request(app, "GET", "/api/standup/schedule?session_id=s1").body)["enabled"] is True

    def test_saving_needs_a_session(self, app):
        assert request(app, "POST", "/api/standup/schedule", {"enabled": True}).code == 400

    def test_an_unknown_channel_is_refused(self, app):
        payload = {"session_id": "s1", "enabled": True, "delivery_channels": ["carrier-duck"]}
        assert request(app, "POST", "/api/standup/schedule", payload).code == 400

    def test_an_offset_off_the_preset_grid_is_refused(self, app):
        # The pickers offer a shortlist; a value outside it would install a job
        # no wizard could read back.
        payload = {"session_id": "s1", "enabled": True, "remind_after": 47}
        assert request(app, "POST", "/api/standup/schedule", payload).code == 400

    def test_saving_installs_and_reports_back(self, app, monkeypatch):
        applied: list = []
        monkeypatch.setattr(
            "yeaboi.standup.schedule.apply_schedule",
            lambda sid, **kw: applied.append((sid, kw)) or "Scheduled via launchd",
        )
        monkeypatch.setattr("yeaboi.standup.schedule.current_schedule", lambda sid, **k: {"enabled": True})
        payload = {
            "session_id": "s1",
            "enabled": True,
            "time": "09:30",
            "weekdays": "1-5",
            "lead_minutes": 15,
            "delivery_channels": ["terminal"],
            "remind_after": 60,
        }
        body = json.loads(request(app, "POST", "/api/standup/schedule", payload).body)
        assert body["message"] == "Scheduled via launchd"
        assert body["schedule"] == {"enabled": True}
        assert applied[0][1]["remind_after"] == 60
        assert applied[0][1]["lead_minutes"] == 15


class TestStandupRun:
    def test_a_session_is_required(self, app):
        assert request(app, "POST", "/api/standup/run", {}).code == 400

    def test_the_lines_run_op_progress_done(self, app, monkeypatch):
        def fake_run(session_id, **kw):
            kw["on_run_id"](7)
            kw["on_progress"]("Collecting activity")
            return _report()

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", fake_run)
        lines = drain(request(app, "POST", "/api/standup/run", {"session_id": "s1"}))
        assert [line["type"] for line in lines] == ["op", "run_id", "progress", "done"]
        assert lines[1]["run_id"] == 7
        assert lines[2]["phase"] == "Collecting activity"
        assert lines[-1]["report"]["team_summary"] == "steady progress"

    def test_a_preview_never_delivers(self, app, monkeypatch):
        seen: list = []

        def fake_run(session_id, **kw):
            seen.append((kw["deliver"], kw["dry_run"]))
            return _report()

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", fake_run)
        drain(request(app, "POST", "/api/standup/run", {"session_id": "s1"}))
        assert seen == [(False, True)]
        drain(request(app, "POST", "/api/standup/run", {"session_id": "s1", "deliver": True}))
        assert seen[1] == (True, False)

    def test_a_failure_ends_the_stream_with_one_classified_line(self, app, monkeypatch):
        def boom(session_id, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr("yeaboi.standup.engine.run_standup", boom)
        lines = drain(request(app, "POST", "/api/standup/run", {"session_id": "s1"}))
        assert [line["type"] for line in lines] == ["op", "error"]
        assert lines[-1]["message"].startswith("Unexpected error")

    def test_the_operation_is_released_when_the_stream_ends(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.standup.engine.run_standup", lambda sid, **kw: _report())
        op_id = drain(request(app, "POST", "/api/standup/run", {"session_id": "s1"}))[0]["op_id"]
        assert app.ops.get(op_id) is None


class TestAnalysisOptions:
    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/analysis/options", authed=False).code == 401

    def test_it_reports_the_configured_grid_and_the_steps(self, app, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.analysis.setup.available_grid",
            lambda: {"delivery": ["jira"], "code": [], "docs": ["notion"], "ops": ["pagerduty"]},
        )
        body = json.loads(request(app, "GET", "/api/analysis/options").body)
        assert body["grid"]["delivery"] == ["jira"]
        assert body["features_available"] == {
            "delivery": True,
            "ai_footprint": False,
            "code_health": False,
            "documentation": True,
            "operational": True,
        }
        assert "features" in body and body["features"][0]["key"] == "delivery"
        assert body["default_depth"] == "deep"


class TestAnalysisSteps:
    def _plan(self, app, **answers):
        answers.setdefault("grid", {"delivery": ["jira"], "code": ["github"], "docs": ["notion"], "ops": []})
        answers.setdefault("roster_fallback", ["jira"])
        return json.loads(request(app, "POST", "/api/analysis/steps", answers).body)

    def test_a_docs_only_selection_skips_depth_and_members(self, app):
        plan = self._plan(app, features=["documentation"], components={"docs": ["notion"]})
        assert plan["steps"] == ["features", "sources", "window", "review"]

    def test_the_grid_narrows_to_what_the_features_read(self, app):
        plan = self._plan(app, features=["documentation"], components={"docs": ["notion"]})
        assert plan["grid"] == {"delivery": [], "code": [], "docs": ["notion"], "ops": []}

    def test_a_code_host_earns_its_scope_step(self, app):
        plan = self._plan(app, features=["ai_footprint"], components={"code": ["github"]})
        assert "github_owners" in plan["steps"] and "azdo_projects" not in plan["steps"]

    def test_the_model_step_needs_the_callers_probe(self, app):
        answers = {"features": ["delivery"], "components": {"delivery": ["jira"]}, "depth": "deep"}
        assert "model" not in self._plan(app, **answers)["steps"]
        assert "model" in self._plan(app, **answers, model_offered=True)["steps"]

    def test_it_returns_the_payload_the_answers_would_run(self, app):
        plan = self._plan(
            app,
            features=["delivery"],
            components={"delivery": ["jira"]},
            depth="deep",
            members=["Ana"],
        )
        assert plan["run"]["depth"] == "deep"
        assert plan["run"]["members_map"] == {"jira": ["Ana"]}

    def test_a_solo_wizard_never_asks_for_members(self, app):
        plan = self._plan(
            app,
            features=["delivery"],
            components={"delivery": ["jira"]},
            members=["Ana"],
            solo=True,
        )
        assert "members" not in plan["steps"]
        # And a stale pick coerces out of the payload the answers would run.
        assert plan["run"]["members"] is None and plan["run"]["members_map"] is None


class TestAnalysisResult:
    def test_an_unknown_analysis_is_a_404(self, app, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "missing.db")
        assert request(app, "GET", "/api/analysis/result/nope").code == 404

    def test_no_database_still_lists_nothing(self, app, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "missing.db")
        assert json.loads(request(app, "GET", "/api/analysis/profiles").body) == {"profiles": []}


class TestAnalysisRun:
    def test_an_unknown_feature_is_refused(self, app):
        assert request(app, "POST", "/api/analysis/run", {"features": ["astrology"]}).code == 400

    def test_an_unknown_source_is_refused(self, app):
        assert request(app, "POST", "/api/analysis/run", {"source": "trello"}).code == 400

    def test_an_unknown_depth_is_refused(self, app):
        assert request(app, "POST", "/api/analysis/run", {"depth": "profound"}).code == 400

    def test_the_lines_run_op_progress_done(self, app, monkeypatch):
        def fake_run(**kw):
            kw["progress"].append("Reading sprints")
            return {"delivery": {}, "warnings": []}

        monkeypatch.setattr("yeaboi.analysis.engine.run_team_analysis", fake_run)
        lines = drain(request(app, "POST", "/api/analysis/run", {"source": "jira"}))
        assert [line["type"] for line in lines] == ["op", "progress", "done"]
        assert lines[1]["phase"] == "Reading sprints"
        assert lines[-1]["result"] == {"delivery": {}, "warnings": []}

    def test_the_wizard_payload_reaches_the_engine(self, app, monkeypatch):
        seen: list = []
        monkeypatch.setattr(
            "yeaboi.analysis.engine.run_team_analysis", lambda **kw: seen.append(kw) or {"delivery": {}}
        )
        payload = {
            "source": "both",
            "features": ["delivery", "documentation"],
            "components": {"delivery": ["jira"], "docs": ["notion"]},
            "members_map": {"jira": ["Ana"]},
            "analysis_scope": {"github": ["acme"]},
            "window_days": 60,
            "depth": "quick",
            "model": "llama3",
        }
        drain(request(app, "POST", "/api/analysis/run", payload))
        call = seen[0]
        assert call["analysis_features"] == ["delivery", "documentation"]
        assert call["members"] == {"jira": ["Ana"]}
        assert call["analysis_scope"] == {"github": ["acme"]}
        assert call["analysis_window_days"] == 60
        assert call["analysis_depth"] == "quick"
        assert call["analysis_model"] == "llama3"

    def test_a_cancelled_run_says_so_rather_than_erroring(self, app, monkeypatch):
        from yeaboi.analysis.cancellation import AnalysisCancelledError

        def cancelled(**kw):
            raise AnalysisCancelledError("stopped")

        monkeypatch.setattr("yeaboi.analysis.engine.run_team_analysis", cancelled)
        lines = drain(request(app, "POST", "/api/analysis/run", {}))
        assert [line["type"] for line in lines] == ["op", "cancelled"]

    def test_the_run_is_cancellable_through_its_op(self, app, monkeypatch):
        # The op's event IS the engine's cancel seam — nothing else joins them.
        seen: list = []
        monkeypatch.setattr(
            "yeaboi.analysis.engine.run_team_analysis", lambda **kw: seen.append(kw["cancel_event"]) or {}
        )
        op_id = drain(request(app, "POST", "/api/analysis/run", {}))[0]["op_id"]
        assert seen[0] is not None
        assert app.ops.get(op_id) is None  # released when the stream ended


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: path)
    return path


class TestStandupProject:
    def test_a_project_id_reaches_the_engine(self, app, db, monkeypatch):
        from yeaboi.projects.engine import create_project

        pid = create_project("Apollo", db_path=db)["project_id"]
        seen: list = []
        monkeypatch.setattr(
            "yeaboi.standup.engine.run_standup", lambda sid, **kw: seen.append(kw["project_id"]) or _report()
        )
        drain(request(app, "POST", "/api/standup/run", {"session_id": "s1"}))
        drain(request(app, "POST", "/api/standup/run", {"session_id": "s1", "project_id": None}))
        drain(request(app, "POST", "/api/standup/run", {"session_id": "s1", "project_id": pid}))
        assert seen == ["", "", pid]

    def test_an_unknown_project_is_400(self, app, db, monkeypatch):
        monkeypatch.setattr("yeaboi.standup.engine.run_standup", lambda sid, **kw: _report())
        resp = request(app, "POST", "/api/standup/run", {"session_id": "s1", "project_id": "proj-00000000"})
        assert resp.code == 400
        assert b"unknown project" in resp.body


class TestAnalysisProject:
    def test_a_project_id_links_the_created_session(self, app, db, monkeypatch):
        from types import SimpleNamespace

        from yeaboi.projects.engine import create_project, get_project
        from yeaboi.sessions import SessionStore

        pid = create_project("Apollo", db_path=db)["project_id"]
        profile = SimpleNamespace(team_id="team-x", project_key="APOLLO")
        monkeypatch.setattr(
            "yeaboi.analysis.engine.run_team_analysis", lambda **kw: {"delivery": {"jira": {"profile": profile}}}
        )
        lines = drain(request(app, "POST", "/api/analysis/run", {"project_id": pid}))
        assert lines[-1]["type"] == "done"
        session_id = lines[-1]["session_id"]
        with SessionStore(db) as store:
            assert store.session_project_id(session_id) == pid
            assert store.list_sessions(mode="analysis")[0]["project_name"] == "APOLLO"
        project = get_project(pid, db_path=db)
        assert project["session_ids"] == [session_id]
        assert project["settings"] == {"default_analysis_profile_id": "team-x"}

    def test_a_blank_project_creates_no_session(self, app, db, monkeypatch):
        from yeaboi.sessions import SessionStore

        monkeypatch.setattr("yeaboi.analysis.engine.run_team_analysis", lambda **kw: {"delivery": {}})
        lines = drain(request(app, "POST", "/api/analysis/run", {}))
        assert "session_id" not in lines[-1]
        with SessionStore(db) as store:
            assert store.list_sessions() == []

    def test_a_result_without_a_profile_still_links_the_session(self, app, db, monkeypatch):
        from yeaboi.projects.engine import create_project, get_project

        pid = create_project("Apollo", db_path=db)["project_id"]
        monkeypatch.setattr("yeaboi.analysis.engine.run_team_analysis", lambda **kw: {"delivery": {}})
        lines = drain(request(app, "POST", "/api/analysis/run", {"project_id": pid}))
        project = get_project(pid, db_path=db)
        assert project["session_ids"] == [lines[-1]["session_id"]]
        assert project["settings"] == {}

    def test_an_unknown_project_is_400(self, app, db, monkeypatch):
        monkeypatch.setattr("yeaboi.analysis.engine.run_team_analysis", lambda **kw: {"delivery": {}})
        resp = request(app, "POST", "/api/analysis/run", {"project_id": "proj-00000000"})
        assert resp.code == 400
        assert b"unknown project" in resp.body


class TestStandupSolo:
    def test_solo_reaches_the_engine(self, app, monkeypatch):
        seen: list = []
        monkeypatch.setattr("yeaboi.standup.engine.run_standup", lambda sid, **kw: seen.append(kw["solo"]) or _report())
        drain(request(app, "POST", "/api/standup/run", {"session_id": "s1"}))
        drain(request(app, "POST", "/api/standup/run", {"session_id": "s1", "solo": True}))
        assert seen == [False, True]

    def test_solo_reaches_the_schedule(self, app, monkeypatch):
        applied: list = []
        monkeypatch.setattr(
            "yeaboi.standup.schedule.apply_schedule", lambda sid, **kw: applied.append(kw["solo"]) or "ok"
        )
        monkeypatch.setattr("yeaboi.standup.schedule.current_schedule", lambda sid, **k: {"enabled": True})
        payload = {"session_id": "s1", "enabled": True, "delivery_channels": ["terminal"], "remind_after": 0}
        request(app, "POST", "/api/standup/schedule", payload)
        request(app, "POST", "/api/standup/schedule", {**payload, "solo": True})
        assert applied == [False, True]
