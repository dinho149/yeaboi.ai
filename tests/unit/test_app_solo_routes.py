"""The Solo world's native routes: /api/solo/today and the Weekly Review wire."""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.solo.today import TodaySnapshot

TOKEN = "test-token"


def request(app: AppServer, method: str, path: str, *, authed: bool = True, body: dict | None = None):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    raw = json.dumps(body).encode() if body is not None else b""
    if body is not None:
        headers["Content-Type"] = "application/json"
    return app.handle(parse_request(method, path, headers, raw))


def _review(**kw):
    from yeaboi.agent.state import ReviewAction, WeeklyReview

    base = dict(
        week_label="2026-W35",
        week_start="2026-08-24",
        week_end="2026-08-28",
        session_id="sid",
        summary="A steady week.",
        plan_line="Day 4/10 · On track",
        actions=(ReviewAction(id="a1b2c3d4e5f6", text="Write the ADR", week_label="2026-W35"),),
    )
    base.update(kw)
    return WeeklyReview(**base)


def _ndjson(resp) -> list[dict]:
    raw = b"".join(resp.stream) if getattr(resp, "stream", None) is not None else resp.body
    return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


class TestToday:
    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/solo/today", authed=False).code == 401

    def test_serves_the_snapshot_fields_verbatim(self, app, monkeypatch):
        seen = {}

        def fake(*, project_id=""):
            seen["project_id"] = project_id
            return TodaySnapshot(standup_date="2026-09-01", next_story_id="S-1", spend_usd=1.5, warnings=("x",))

        monkeypatch.setattr("yeaboi.solo.today.build_today_snapshot", fake)
        resp = request(app, "GET", "/api/solo/today?project_id=proj-12345678")
        payload = json.loads(resp.body)
        assert resp.code == 200
        assert seen == {"project_id": "proj-12345678"}
        assert set(payload) == {f.name for f in fields(TodaySnapshot)}
        assert payload["standup_date"] == "2026-09-01" and payload["next_story_id"] == "S-1"
        assert payload["spend_usd"] == 1.5 and payload["warnings"] == ["x"]

    def test_defaults_to_the_unscoped_snapshot(self, app, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            "yeaboi.solo.today.build_today_snapshot",
            lambda *, project_id="": seen.setdefault("project_id", project_id) or TodaySnapshot(),
        )
        payload = json.loads(request(app, "GET", "/api/solo/today").body)
        assert seen == {"project_id": ""}
        assert payload["standup_date"] == "" and payload["warnings"] == []

    def test_route_is_chrome_not_a_capability(self):
        from yeaboi.app.registry import ROUTES

        row = next(r for r in ROUTES if r.path == "/api/solo/today")
        assert row.method == "GET" and row.capability is None


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: path)
    return path


class TestReviewPage:
    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/solo/review", authed=False).code == 401

    def test_empty_before_any_review(self, app, db):
        payload = json.loads(request(app, "GET", "/api/solo/review").body)
        assert set(payload) == {"latest", "history", "carried", "beta_notice"}
        assert payload["latest"] is None and payload["history"] == [] and payload["carried"] == []
        assert isinstance(payload["beta_notice"], str)

    def test_serves_the_latest_the_history_and_the_carried_actions(self, app, db):
        from yeaboi.solo.store import WeeklyReviewStore

        with WeeklyReviewStore(db) as store:
            store.record_run(_review(week_label="2026-W34"))
            newest = store.record_run(_review())
        payload = json.loads(request(app, "GET", "/api/solo/review").body)
        assert payload["latest"]["run_id"] == newest
        assert payload["latest"]["review"]["summary"] == "A steady week."
        assert [row["week_label"] for row in payload["history"]] == ["2026-W35", "2026-W34"]
        assert set(payload["history"][0]) == {
            "id",
            "session_id",
            "project_id",
            "run_at",
            "week_label",
            "week_start",
            "week_end",
            "project_name",
            "action_count",
        }
        assert payload["carried"] == [
            {
                "id": "a1b2c3d4e5f6",
                "text": "Write the ADR",
                "status": "pending",
                "origin": "carryover",
                "week_label": "2026-W35",
            }
        ]

    def test_routes_belong_to_the_capability(self):
        from yeaboi.app.registry import ROUTES

        owned = {(r.method, r.path) for r in ROUTES if r.capability == "weekly-review"}
        assert owned == {
            ("GET", "/api/solo/review"),
            ("POST", "/api/solo/review/run"),
            ("GET", "/api/solo/review/runs/{run_id}"),
            ("POST", "/api/solo/review/runs/{run_id}/delete"),
        }


class TestReviewRunRead:
    def test_one_saved_review(self, app, db):
        from yeaboi.solo.store import WeeklyReviewStore

        with WeeklyReviewStore(db) as store:
            run_id = store.record_run(_review())
        payload = json.loads(request(app, "GET", f"/api/solo/review/runs/{run_id}").body)
        assert payload == {"run_id": run_id, "review": payload["review"]}
        assert payload["review"]["week_label"] == "2026-W35"

    def test_unknown_is_404(self, app, db):
        assert request(app, "GET", "/api/solo/review/runs/99").code == 404

    def test_a_non_number_is_400(self, app, db):
        assert request(app, "GET", "/api/solo/review/runs/abc").code == 400


class TestReviewDelete:
    def test_deletes_and_reports(self, app, db):
        from yeaboi.solo.store import WeeklyReviewStore

        with WeeklyReviewStore(db) as store:
            run_id = store.record_run(_review())
        resp = request(app, "POST", f"/api/solo/review/runs/{run_id}/delete")
        assert json.loads(resp.body) == {"deleted": True, "run_id": run_id}
        assert request(app, "GET", f"/api/solo/review/runs/{run_id}").code == 404

    def test_unknown_is_404(self, app, db):
        assert request(app, "POST", "/api/solo/review/runs/99/delete").code == 404


class TestReviewRun:
    def test_streams_op_progress_then_done_with_the_run_id(self, app, db, monkeypatch):
        from yeaboi.solo.store import WeeklyReviewStore

        seen: dict = {}

        def fake_run(*, on_progress=None, **kwargs):
            from yeaboi.analysis.progress import send_component_progress

            seen.update(kwargs)
            for phase in ("scope", "standups"):
                send_component_progress(on_progress, component_id=phase, label=phase, status="running")
                send_component_progress(on_progress, component_id=phase, label=phase, status="running", detail="x")
                send_component_progress(on_progress, component_id=phase, label=phase, status="completed")
            on_progress("Fetching completed work from Jira…")  # free text never reaches the wire
            review = _review()
            with WeeklyReviewStore(db) as store:
                seen["run_id"] = store.record_run(review)
            return review

        monkeypatch.setattr("yeaboi.solo.engine.run_weekly_review", fake_run)
        resp = request(
            app,
            "POST",
            "/api/solo/review/run",
            body={
                "session_id": "sid",
                "project_id": "proj-12345678",
                "context_deps": ["standup"],
                "week_end": "2026-08-28",
                "carried_statuses": {"a1b2c3d4e5f6": "done"},
            },
        )
        assert resp.code == 200
        lines = _ndjson(resp)
        assert [line["type"] for line in lines] == ["op", "progress", "progress", "done"]
        assert lines[1]["phase"] == "scope" and lines[2]["phase"] == "standups"
        assert lines[-1]["run_id"] == seen["run_id"]
        assert lines[-1]["review"]["week_label"] == "2026-W35"
        assert seen["session_id"] == "sid" and seen["project_id"] == "proj-12345678"
        assert seen["context_deps"] == ["standup"] and seen["week_end"] == "2026-08-28"
        assert seen["carried_statuses"] == {"a1b2c3d4e5f6": "done"}

    def test_a_malformed_week_end_is_400(self, app, db, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.solo.engine.run_weekly_review",
            lambda **kw: (_ for _ in ()).throw(AssertionError("must not run")),
        )
        resp = request(app, "POST", "/api/solo/review/run", body={"week_end": "next friday"})
        assert resp.code == 400

    def test_blank_body_runs_the_newest_of_everything(self, app, db, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(
            "yeaboi.solo.engine.run_weekly_review", lambda *, on_progress=None, **kw: seen.update(kw) or _review()
        )
        lines = _ndjson(request(app, "POST", "/api/solo/review/run", body={}))
        assert lines[-1]["type"] == "done" and lines[-1]["run_id"] == 0  # nothing stored by the fake
        assert seen == {
            "session_id": "",
            "project_id": "",
            "context_deps": None,
            "week_end": "",
            "carried_statuses": None,
        }

    def test_an_engine_failure_is_an_error_line(self, app, db, monkeypatch):
        def boom(*, on_progress=None, **kw):
            raise RuntimeError("tracker exploded")

        monkeypatch.setattr("yeaboi.solo.engine.run_weekly_review", boom)
        lines = _ndjson(request(app, "POST", "/api/solo/review/run", body={}))
        assert [line["type"] for line in lines] == ["op", "error"]
        assert lines[-1]["message"]

    @pytest.mark.parametrize("body", [{"context_deps": "standup"}, {"carried_statuses": ["a=done"]}])
    def test_malformed_scope_fields_are_400(self, app, db, body):
        assert request(app, "POST", "/api/solo/review/run", body=body).code == 400
