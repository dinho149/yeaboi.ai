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
    def __init__(self, paper: Paper, *, refreshing: bool = False, enabled: bool = True, rows=None):
        self.paper = paper
        self.refreshing = refreshing
        self.on = enabled
        self.asked: list[bool] = []
        self.invalidated: list[bool] = []
        self.rows = rows if rows is not None else [{"id": "a", "name": "A", "enabled": True}]

    def enabled(self) -> bool:
        return self.on

    def youtube_channel(self) -> str:
        return ""

    def get_paper(self, *, refresh: bool = False):
        self.asked.append(refresh)
        return self.paper, self.refreshing

    def invalidate(self, *, refresh: bool = True) -> bool:
        self.invalidated.append(refresh)
        return self.refreshing

    def source_rows(self) -> list[dict]:
        return self.rows


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


def request(app: AppServer, method: str, path: str, *, authed: bool = True, body: dict | None = None):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    raw = json.dumps(body).encode() if body is not None else b""
    if body is not None:
        headers["Content-Type"] = "application/json"
    return app.handle(parse_request(method, path, headers, raw))


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


FEED = "https://lobste.rs/rss"
PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]


@pytest.fixture
def store(tmp_path, monkeypatch):
    from yeaboi.news import roster

    path = tmp_path / "news_roster.json"
    monkeypatch.setattr("yeaboi.news.roster._store_path", lambda: path)
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: PUBLIC)
    monkeypatch.setattr("yeaboi.paths.LOGS_DIR", tmp_path / "logs")
    roster.invalidate()
    yield path
    roster.invalidate()


def _good_probe(url: str):
    from yeaboi.news.probe import Probe

    return Probe(ok=True, url=url.strip(), kind="rss", name="Lobsters", home_url="https://lobste.rs/", item_count=3)


class TestSourceRoutes:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/news/sources"),
            ("POST", "/api/news/sources/probe"),
            ("POST", "/api/news/sources"),
            ("POST", "/api/news/sources/a/enabled"),
            ("POST", "/api/news/sources/a/delete"),
        ],
    )
    def test_every_route_requires_auth(self, app, method, path):
        assert request(app, method, path, authed=False).code == 401

    def test_the_list_shape(self, app):
        payload = json.loads(request(app, "GET", "/api/news/sources").body)
        assert set(payload) == {"sources", "max_custom", "columns"}
        assert payload["sources"] == [{"id": "a", "name": "A", "enabled": True}]
        assert payload["max_custom"] == 20 and payload["columns"] == ["yeaboi", "ai", "engineering"]

    def test_probe_needs_a_url_and_passes_the_verdict_through(self, app, store, monkeypatch):
        assert request(app, "POST", "/api/news/sources/probe", body={}).code == 400
        monkeypatch.setattr("yeaboi.news.probe.probe", _good_probe)
        payload = json.loads(request(app, "POST", "/api/news/sources/probe", body={"url": FEED}).body)
        assert payload["ok"] is True and payload["kind"] == "rss" and payload["name"] == "Lobsters"
        assert set(payload) == {
            "ok",
            "url",
            "feed_url",
            "kind",
            "name",
            "home_url",
            "item_count",
            "sample_titles",
            "error",
        }

    def test_add_reports_a_failed_probe(self, app, store, monkeypatch):
        from yeaboi.news.probe import Probe

        monkeypatch.setattr("yeaboi.news.probe.probe", lambda url: Probe(url=url, error="http 404"))
        resp = request(app, "POST", "/api/news/sources", body={"url": FEED, "column": "ai"})
        assert resp.code == 400 and json.loads(resp.body)["error"] == "http 404"
        assert not store.exists()

    def test_add_reports_the_validators_problems(self, app, store, monkeypatch):
        monkeypatch.setattr("yeaboi.news.probe.probe", _good_probe)
        resp = request(app, "POST", "/api/news/sources", body={"url": FEED, "column": "research"})
        assert resp.code == 400 and "column must be one of" in json.loads(resp.body)["error"]

    def test_add_saves_names_after_the_feed_and_refreshes(self, app, desk, store, monkeypatch):
        from yeaboi.news import roster

        monkeypatch.setattr("yeaboi.news.probe.probe", _good_probe)
        desk.rows = [{"id": roster.custom_id(FEED), "name": "Lobsters", "enabled": True}]
        resp = request(app, "POST", "/api/news/sources", body={"url": FEED, "column": "engineering"})
        assert resp.code == 200
        payload = json.loads(resp.body)
        assert payload == {"source": desk.rows[0], "refreshing": False}
        saved = roster.load_roster().custom[0]
        assert saved.name == "Lobsters" and saved.kind == "rss" and saved.home_url == "https://lobste.rs/"
        assert desk.invalidated == [True]

    def test_add_keeps_a_given_name(self, app, store, monkeypatch):
        from yeaboi.news import roster

        monkeypatch.setattr("yeaboi.news.probe.probe", _good_probe)
        request(app, "POST", "/api/news/sources", body={"url": FEED, "column": "ai", "name": "Crustaceans"})
        assert roster.load_roster().custom[0].name == "Crustaceans"

    def test_enabled_rejects_a_non_bool_and_an_unknown_id(self, app, store):
        assert request(app, "POST", "/api/news/sources/techmeme/enabled", body={"enabled": "no"}).code == 400
        assert request(app, "POST", "/api/news/sources/nope/enabled", body={"enabled": False}).code == 404

    def test_off_hides_without_a_fetch_and_on_refreshes(self, app, desk, store):
        from yeaboi.news import roster

        desk.rows = [{"id": "techmeme", "name": "Techmeme", "enabled": False}]
        resp = request(app, "POST", "/api/news/sources/techmeme/enabled", body={"enabled": False})
        assert resp.code == 200 and json.loads(resp.body)["source"]["id"] == "techmeme"
        assert roster.load_roster().disabled == frozenset({"techmeme"})
        request(app, "POST", "/api/news/sources/techmeme/enabled", body={"enabled": True})
        assert roster.load_roster().disabled == frozenset()
        assert desk.invalidated == [False, True]

    def test_delete_refuses_a_builtin_and_an_unknown_id(self, app, store):
        assert request(app, "POST", "/api/news/sources/techmeme/delete").code == 400
        assert request(app, "POST", "/api/news/sources/custom-00000000/delete").code == 404

    def test_delete_removes_an_added_outlet(self, app, desk, store, monkeypatch):
        from yeaboi.news import roster

        monkeypatch.setattr("yeaboi.news.probe.probe", _good_probe)
        request(app, "POST", "/api/news/sources", body={"url": FEED, "column": "ai"})
        source_id = roster.custom_id(FEED)
        payload = json.loads(request(app, "POST", f"/api/news/sources/{source_id}/delete").body)
        assert payload == {"deleted": source_id, "refreshing": False}
        assert roster.load_roster().custom == ()
        assert desk.invalidated == [True, True]
