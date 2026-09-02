"""The /api/reporting, /api/performance, /api/roadmap and /api/ship routes.

Socketless, over ``AppServer.handle()``. The ship *lifecycle* is tested in
test_app_ships.py; here the subject is the wire — what an options payload
carries, which requests are refused and why, and the NDJSON line order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.reporting.sprints import SprintRef

TOKEN = "test-token"


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    body = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, headers, body))


def body(response) -> dict:
    assert response.code == 200, response.body
    return json.loads(response.body)


def drain(response) -> list[dict]:
    assert response.code == 200, response.body
    assert response.content_type == "application/x-ndjson"
    return [json.loads(line) for line in b"".join(response.stream).decode().splitlines()]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestReportingOptions:
    def test_carries_the_five_periods_and_the_deck_vocabulary(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.reporting.setup.available_report_sources", lambda: {"delivery": ["jira"]})
        payload = body(request(app, "GET", "/api/reporting/options"))
        assert [p["key"] for p in payload["periods"]] == [
            "last_week",
            "last_sprint",
            "last_month",
            "quarter",
            "window",
        ]
        assert payload["style_choices"]["layouts"]
        assert {"key", "label", "kind"} <= set(payload["style_fields"][0])

    def test_says_whether_the_sources_step_is_a_real_question(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.reporting.setup.available_report_sources", lambda: {"delivery": ["jira"]})
        assert body(request(app, "GET", "/api/reporting/options"))["sources"]["step_applies"] is False
        monkeypatch.setattr(
            "yeaboi.reporting.setup.available_report_sources",
            lambda: {"delivery": ["jira"], "code": ["github"]},
        )
        assert body(request(app, "GET", "/api/reporting/options"))["sources"]["step_applies"] is True

    def test_needs_a_token(self, app):
        assert request(app, "GET", "/api/reporting/options", authed=False).code == 401


class TestReportingSprints:
    def test_answers_the_quarter_and_a_fallback_window(self, app, monkeypatch):
        found = [SprintRef(name="S10", start_date="2026-07-01", end_date="2026-07-14", in_quarter=True)]
        monkeypatch.setattr("yeaboi.reporting.setup.sprint_options", lambda *a, **k: found)
        payload = body(request(app, "GET", "/api/reporting/sprints?session_id=s1"))
        assert payload["checked"] == [0]
        assert payload["fallback"]["window_start"]

    def test_no_sprints_is_an_answer_not_an_error(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.reporting.setup.sprint_options", lambda *a, **k: [])
        payload = body(request(app, "GET", "/api/reporting/sprints"))
        assert payload["sprints"] == [] and payload["checked"] == []


class TestReportingWindow:
    rows = [
        {"name": "S9", "start_date": "2026-06-01", "end_date": "2026-06-14", "in_quarter": False},
        {"name": "S10", "start_date": "2026-07-01", "end_date": "2026-07-14", "in_quarter": True},
    ]

    def test_the_detected_selection_keeps_the_plain_label(self, app):
        payload = body(request(app, "POST", "/api/reporting/window", {"sprints": self.rows, "checked": [1]}))
        assert payload["sprint_names"] == ["S10"]
        assert "(custom)" not in payload["period_label_override"]

    def test_any_other_selection_is_custom(self, app):
        payload = body(request(app, "POST", "/api/reporting/window", {"sprints": self.rows, "checked": [0, 1]}))
        assert "(custom)" in payload["period_label_override"]

    def test_nothing_checked_is_refused_with_the_remedy(self, app):
        response = request(app, "POST", "/api/reporting/window", {"sprints": self.rows, "checked": []})
        assert response.code == 400
        assert b"at least one sprint" in response.body


class TestReportingRun:
    def test_an_unknown_period_is_refused_by_name(self, app):
        response = request(app, "POST", "/api/reporting/run", {"period": "fortnight"})
        assert response.code == 400
        assert b"period must be one of" in response.body

    def test_a_reversed_custom_range_names_the_field(self, app):
        response = request(
            app,
            "POST",
            "/api/reporting/run",
            {"period": "window", "window_start": "2026-08-20", "window_end": "2026-08-01"},
        )
        assert response.code == 400
        assert b"before window_start" in response.body

    def test_a_custom_range_needs_both_dates(self, app):
        response = request(app, "POST", "/api/reporting/run", {"period": "window", "window_start": "2026-08-01"})
        assert response.code == 400
        assert b"both window_start and window_end" in response.body

    def test_a_run_streams_op_progress_then_done(self, app, monkeypatch):
        @dataclass
        class _Report:
            delivered_items: tuple = ()
            period_label: str = "Last month"
            warnings: tuple = ()

        def _fake(period, **kw):
            kw["on_progress"]("Gathering delivered work")
            return _Report()

        monkeypatch.setattr("yeaboi.reporting.engine.run_delivery_report", _fake)
        lines = drain(request(app, "POST", "/api/reporting/run", {"period": "last_month"}))
        assert lines[0]["type"] == "op"
        assert {"type": "progress", "phase": "Gathering delivered work"} in lines
        assert lines[-1]["type"] == "done"
        assert lines[-1]["delivered"] == 0

    def test_a_cancelled_run_ends_cancelled_not_error(self, app, monkeypatch):
        from yeaboi.reporting.engine import ReportCancelledError

        def _fake(period, **kw):
            raise ReportCancelledError("stopped")

        monkeypatch.setattr("yeaboi.reporting.engine.run_delivery_report", _fake)
        assert drain(request(app, "POST", "/api/reporting/run", {"period": "last_week"}))[-1] == {"type": "cancelled"}

    def test_the_op_is_removed_once_the_stream_ends(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.reporting.engine.run_delivery_report", lambda period, **kw: object())
        lines = drain(request(app, "POST", "/api/reporting/run", {"period": "last_week"}))
        assert app.ops.get(lines[0]["op_id"]) is None


class TestReportingStyle:
    def test_saving_answers_with_the_stored_style(self, app, monkeypatch):
        saved: list = []
        monkeypatch.setattr("yeaboi.reporting.style.save_deck_style", saved.append)
        payload = body(request(app, "POST", "/api/reporting/style", {"style": {"layout": "compact"}}))
        assert payload["style"]["layout"] == "compact"
        assert saved and saved[0].layout == "compact"

    def test_reset_restores_the_defaults(self, app, monkeypatch):
        from yeaboi.reporting.style import DEFAULT_STYLE

        monkeypatch.setattr("yeaboi.reporting.style.save_deck_style", lambda style: None)
        payload = body(request(app, "POST", "/api/reporting/style", {"reset": True}))
        assert payload["style"]["layout"] == DEFAULT_STYLE.layout


class TestReportingExport:
    def test_nothing_saved_is_a_404(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.sharing.resolve.load", lambda kind, **kw: None)
        assert request(app, "POST", "/api/reporting/export", {}).code == 404

    def test_pptx_without_the_extra_names_the_extra(self, app, monkeypatch):
        from yeaboi.sharing.resolve import Resolved

        monkeypatch.setattr(
            "yeaboi.sharing.resolve.load",
            lambda kind, **kw: Resolved(kind="reporting", artifact=object(), title="t", project_name="p"),
        )
        monkeypatch.setattr("yeaboi.reporting.export.export_pptx_only", lambda *a, **k: None)
        response = request(app, "POST", "/api/reporting/export", {"pptx_only": True})
        assert response.code == 503
        assert b"python-pptx" in response.body

    def test_the_answered_fit_reaches_the_deck_builder(self, app, monkeypatch, tmp_path):
        from yeaboi.sharing.resolve import Resolved

        seen: dict = {}
        monkeypatch.setattr(
            "yeaboi.sharing.resolve.load",
            lambda kind, **kw: Resolved(kind="reporting", artifact=object(), title="t", project_name="p"),
        )

        def _export(report, **kw):
            seen.update(kw)
            return {"markdown": tmp_path / "r.md"}

        monkeypatch.setattr("yeaboi.reporting.export.export_report", _export)
        body(request(app, "POST", "/api/reporting/export", {"expand": False}))
        assert seen["style"].content_fit == "tight"


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


class TestPerformanceRoster:
    def test_pairs_every_name_with_its_hint(self, app, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.performance.setup.collect_roster",
            lambda **kw: {
                "session_id": "s1",
                "session_name": "Apollo",
                "roster": ["Ada", "Bob"],
                "hints": ["2 open 1:1 actions", "no open 1:1 actions"],
            },
        )
        payload = body(request(app, "GET", "/api/performance/roster"))
        assert payload["engineers"] == [
            {"name": "Ada", "hint": "2 open 1:1 actions"},
            {"name": "Bob", "hint": "no open 1:1 actions"},
        ]
        assert [a["key"] for a in payload["actions"]] == ["prep", "complete", "review", "notes", "history"]

    def test_an_empty_roster_carries_the_reason(self, app, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.performance.setup.collect_roster",
            lambda **kw: {"session_id": "", "session_name": "", "roster": [], "hints": []},
        )
        payload = body(request(app, "GET", "/api/performance/roster"))
        assert payload["engineers"] == []
        assert payload["empty_message"]


class TestPerformanceEngineer:
    def test_a_nameless_request_is_refused(self, app):
        # The router will not match an empty segment, so this is a 404 route,
        # not a handler bug — the assertion pins that it never 500s.
        assert request(app, "GET", "/api/performance/engineer/").code in (400, 404)

    def test_no_database_is_a_404_naming_the_engineer(self, app, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "missing.db")
        response = request(app, "GET", "/api/performance/engineer/Ada")
        assert response.code == 404
        assert b"Ada" in response.body


# ---------------------------------------------------------------------------
# Roadmap
# ---------------------------------------------------------------------------


class TestRoadmapOptions:
    def test_offers_every_source_even_unconfigured(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "")
        monkeypatch.setattr("yeaboi.config.get_notion_token", lambda: "")
        sources = body(request(app, "GET", "/api/roadmap/options"))["sources"]
        assert [s["key"] for s in sources] == ["confluence", "notion", "local"]
        assert all(s["hint"] for s in sources)


class TestRoadmapAnalyze:
    def test_an_unknown_source_is_refused_by_name(self, app):
        response = request(app, "POST", "/api/roadmap/analyze", {"source_type": "sharepoint", "locator": "x"})
        assert response.code == 400
        assert b"sharepoint" in response.body

    def test_a_missing_file_is_refused_before_the_run(self, app, tmp_path):
        response = request(
            app, "POST", "/api/roadmap/analyze", {"source_type": "local", "locator": str(tmp_path / "nope.md")}
        )
        assert response.code == 400
        assert b"File not found" in response.body

    def test_a_file_outside_the_sandbox_is_refused_and_asked_about(self, app, monkeypatch, tmp_path):
        # Refusing without asking would leave the person with a 403 and no way
        # to say yes — the pre-flight queues the request the modal answers.
        from yeaboi import fs_policy

        path = tmp_path / "roadmap.md"
        path.write_text("# Q3\n", encoding="utf-8")
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda p, mode="read": False)
        fs_policy.set_interactive(True)
        try:
            response = request(app, "POST", "/api/roadmap/analyze", {"source_type": "local", "locator": str(path)})
            assert response.code == 403
            assert b"try again" in response.body
            assert [req.context for req in fs_policy.pop_pending_denials()] == ["roadmap intake"]
        finally:
            fs_policy.set_interactive(False)
            fs_policy.pop_pending_denials()

    def test_an_analysis_streams_op_progress_then_done(self, app, monkeypatch):
        @dataclass
        class _Analysis:
            projects: tuple = ()
            warnings: tuple = ()

        def _fake(source, **kw):
            kw["on_progress"]("Reading the roadmap source")
            return _Analysis()

        monkeypatch.setattr("yeaboi.roadmap.engine.run_roadmap_analysis", _fake)
        monkeypatch.setattr("yeaboi.app.routes_roadmap._save", lambda source, analysis, roadmap_id: 7)
        monkeypatch.setattr("yeaboi.config.get_confluence_base_url", lambda: "https://x.atlassian.net")
        lines = drain(
            request(app, "POST", "/api/roadmap/analyze", {"source_type": "confluence", "locator": "12345"}),
        )
        assert lines[0]["type"] == "op"
        assert {"type": "progress", "phase": "Reading the roadmap source"} in lines
        assert lines[-1]["type"] == "done"
        assert lines[-1]["roadmap_id"] == 7

    def test_a_broken_store_is_an_error_line_not_a_traceback(self, app, monkeypatch):
        def _boom(source, **kw):
            raise RuntimeError("disk full")

        monkeypatch.setattr("yeaboi.roadmap.engine.run_roadmap_analysis", _boom)
        lines = drain(request(app, "POST", "/api/roadmap/analyze", {"source_type": "notion", "locator": "abc"}))
        assert lines[-1]["type"] == "error"
        assert "disk full" not in lines[-1]["message"]


class TestRoadmapPlan:
    def test_nothing_analyzed_is_a_404(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.sharing.resolve.load", lambda kind, **kw: None)
        assert request(app, "POST", "/api/roadmap/plan", {}).code == 404

    def test_a_saved_roadmap_id_must_be_a_number(self, app):
        assert request(app, "GET", "/api/roadmap/saved/abc").code == 400


# ---------------------------------------------------------------------------
# Ship
# ---------------------------------------------------------------------------


class TestShipStories:
    def test_carries_the_default_repo_and_the_empty_message(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.plans.latest_plan_with_work", lambda: None)
        payload = body(request(app, "GET", "/api/ship/stories"))
        assert payload["stories"] == []
        assert payload["default_repo"]
        assert payload["empty_message"]

    def test_an_unreadable_store_reports_a_problem_not_a_500(self, app, monkeypatch):
        def _boom():
            raise RuntimeError("db locked")

        monkeypatch.setattr("yeaboi.ship.plans.latest_plan_with_work", _boom)
        assert "Could not read saved plans" in body(request(app, "GET", "/api/ship/stories"))["problem"]


class TestShipTarget:
    def test_reports_the_toplevel_and_whether_it_is_granted(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.setup.resolve_target", lambda repo: ("/repos/api", ""))
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda p, mode="read": False)
        payload = body(request(app, "POST", "/api/ship/target", {"repo": "/repos/api/src"}))
        assert payload["repo"] == "/repos/api"
        assert payload["allowed"] is False
        assert "/repos/api" in payload["consent_hint"]

    def test_a_path_outside_any_repo_has_no_hint_to_give(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.setup.resolve_target", lambda repo: ("", "not a git repository"))
        payload = body(request(app, "POST", "/api/ship/target", {"repo": "/tmp"}))
        assert payload["problem"] and payload["consent_hint"] == ""


class TestShipLaunch:
    def test_a_storyless_launch_is_refused(self, app):
        response = request(app, "POST", "/api/ship/runs", {"repo": "/repos/api"})
        assert response.code == 400
        assert b"story_id" in response.body

    def test_an_unresolvable_repo_is_refused_with_gits_words(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.ship.setup.resolve_target", lambda repo: ("", "not a git repository"))
        response = request(app, "POST", "/api/ship/runs", {"story_id": "US-1", "repo": "/tmp"})
        assert response.code == 400
        assert b"not a git repository" in response.body

    def test_an_ungranted_repo_is_refused_and_asked_about(self, app, monkeypatch):
        from yeaboi import fs_policy

        monkeypatch.setattr("yeaboi.ship.setup.resolve_target", lambda repo: ("/repos/api", ""))
        monkeypatch.setattr("yeaboi.fs_policy.is_allowed", lambda p, mode="read": False)
        fs_policy.set_interactive(True)
        try:
            response = request(app, "POST", "/api/ship/runs", {"story_id": "US-1", "repo": "/repos/api"})
            assert response.code == 403  # before any money is spent
            assert b"try again" in response.body
            pending = fs_policy.pop_pending_denials()
            assert [(req.mode, req.context) for req in pending] == [("write", "ship run")]
        finally:
            fs_policy.set_interactive(False)
            fs_policy.pop_pending_denials()


class TestShipRunReads:
    def test_an_unknown_run_is_a_404(self, app):
        assert request(app, "GET", "/api/ship/runs/nope").code == 404
        assert request(app, "POST", "/api/ship/runs/nope/cancel").code == 404
        assert request(app, "POST", "/api/ship/runs/nope/gate", {"resolution": "approved"}).code == 404

    def test_an_unknown_resolution_is_refused_before_the_lookup(self, app):
        response = request(app, "POST", "/api/ship/runs/nope/gate", {"resolution": "maybe"})
        assert response.code == 400
        assert b"resolution must be one of" in response.body

    def test_no_runs_yet_is_an_empty_list(self, app):
        assert body(request(app, "GET", "/api/ship/runs"))["runs"] == []


class TestReportingSolo:
    def test_solo_reaches_the_engine(self, app, monkeypatch):
        @dataclass
        class _Report:
            delivered_items: tuple = ()
            period_label: str = "Last month"
            warnings: tuple = ()

        seen: list = []
        monkeypatch.setattr(
            "yeaboi.reporting.engine.run_delivery_report", lambda period, **kw: seen.append(kw["solo"]) or _Report()
        )
        drain(request(app, "POST", "/api/reporting/run", {"period": "last_month"}))
        drain(request(app, "POST", "/api/reporting/run", {"period": "last_month", "solo": True}))
        assert seen == [False, True]
