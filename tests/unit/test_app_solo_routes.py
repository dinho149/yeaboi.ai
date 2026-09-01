"""GET /api/solo/today — the desktop twin of the Solo welcome's Today strip."""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.solo.today import TodaySnapshot

TOKEN = "test-token"


def request(app: AppServer, method: str, path: str, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    return app.handle(parse_request(method, path, headers, b""))


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
