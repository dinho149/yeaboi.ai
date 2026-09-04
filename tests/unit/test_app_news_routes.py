"""The front page route: /api/news over AppServer.handle(), socketless."""

from __future__ import annotations

import json

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.news.paper import Paper, Section, SourceStatus
from yeaboi.news.parse import NewsItem

TOKEN = "test-token"

ITEM_KEYS = {
    "id",
    "title",
    "url",
    "source_id",
    "source_name",
    "published",
    "summary",
    "image_url",
    "kind",
    "topic",
    "persona",
    "column",
}


class FakeDesk:
    def __init__(self, paper: Paper, *, refreshing: bool = False, enabled: bool = True):
        self.paper = paper
        self.refreshing = refreshing
        self.on = enabled
        self.asked: list[bool] = []

    def enabled(self) -> bool:
        return self.on

    def get_paper(self, *, refresh: bool = False):
        self.asked.append(refresh)
        return self.paper, self.refreshing


def _item(title: str, **kw) -> NewsItem:
    base = dict(id="abc", title=title, url="https://x.example/p", column="ai", persona="wizard", topic="models")
    base.update(kw)
    return NewsItem(**base)


def _paper() -> Paper:
    return Paper(
        generated_at="2026-09-04T12:00:00+00:00",
        lead=_item("Lead"),
        sections=(Section(column="ai", title="AI", items=(_item("Row", id="def"),)),),
        sources=(SourceStatus(id="a", name="A", home_url="https://a.example/", column="ai", ok=True),),
    )


def request(app: AppServer, method: str, path: str, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    return app.handle(parse_request(method, path, headers, b""))


@pytest.fixture
def desk():
    return FakeDesk(_paper())


@pytest.fixture
def app(desk):
    return AppServer(token=TOKEN, news=desk)


class TestNewsRoute:
    def test_requires_auth(self, app):
        assert request(app, "GET", "/api/news", authed=False).code == 401

    def test_serializes_the_desks_paper(self, app):
        resp = request(app, "GET", "/api/news")
        assert resp.code == 200
        payload = json.loads(resp.body)
        assert set(payload) == {
            "enabled",
            "refreshing",
            "schema",
            "generated_at",
            "stale",
            "lead",
            "sections",
            "sources",
        }
        assert payload["enabled"] is True
        assert payload["refreshing"] is False
        assert payload["stale"] is False
        assert set(payload["lead"]) == ITEM_KEYS
        assert payload["lead"]["title"] == "Lead"
        assert payload["lead"]["image_url"] is None
        assert payload["sections"] == [
            {"column": "ai", "title": "AI", "items": [json.loads(json.dumps(payload["sections"][0]["items"][0]))]}
        ]
        assert set(payload["sections"][0]["items"][0]) == ITEM_KEYS
        assert set(payload["sources"][0]) == {
            "id",
            "name",
            "home_url",
            "column",
            "ok",
            "fetched_at",
            "error",
            "item_count",
        }

    def test_refresh_is_forwarded(self, app, desk):
        request(app, "GET", "/api/news")
        request(app, "GET", "/api/news?refresh=1")
        request(app, "GET", "/api/news?refresh=true")
        request(app, "GET", "/api/news?refresh=no")
        assert desk.asked == [False, True, True, False]

    def test_a_stale_paper_says_so(self):
        desk = FakeDesk(Paper(generated_at="t", stale=True), refreshing=True)
        payload = json.loads(request(AppServer(token=TOKEN, news=desk), "GET", "/api/news").body)
        assert payload["stale"] is True
        assert payload["refreshing"] is True
        assert payload["lead"] is None
        assert payload["sections"] == []

    def test_the_off_switch_shape(self):
        paper = Paper(
            generated_at="t",
            sections=(Section(column="yeaboi", title="yeaboi", items=(_item("yeaboi 4.1.0", kind="release"),)),),
        )
        desk = FakeDesk(paper, enabled=False)
        payload = json.loads(request(AppServer(token=TOKEN, news=desk), "GET", "/api/news").body)
        assert payload["enabled"] is False
        assert [section["column"] for section in payload["sections"]] == ["yeaboi"]
        assert payload["sources"] == []

    def test_the_default_desk_is_real(self):
        app = AppServer(token=TOKEN)
        from yeaboi.news.desk import NewsDesk

        assert isinstance(app.news, NewsDesk)
