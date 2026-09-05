"""The music sign-in and library routes — socketless, over AppServer.handle()."""

from __future__ import annotations

import json

import pytest

from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.connectors import library, oauth
from yeaboi.connectors.library import Item, LibraryError, Page

TOKEN = "test-token"


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    body = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, headers, body))


@pytest.fixture
def app():
    return AppServer(token=TOKEN)


class FakeSignIn:
    def __init__(self, key: str, *, can_start: bool = True):
        self.key = key
        self.url = f"https://vendor.example/authorize?for={key}"
        self.account = ""
        self.error = (
            "" if can_start else "yeaboi's app is not configured — paste your own client ID (SPOTIFY_CLIENT_ID)"
        )
        self.persisted = False
        self.cancelled = False
        self._can_start = can_start
        self.polls = 0

    def start(self) -> bool:
        return self._can_start

    def poll(self) -> None:
        self.polls += 1
        if self.polls >= 2:
            self.persisted = True
            self.account = "dinho"

    @property
    def done(self) -> bool:
        return bool(self.persisted or self.error)

    @property
    def ok(self) -> bool:
        return self.persisted

    @property
    def message(self) -> str:
        return f"Signed in as {self.account}" if self.persisted else self.error

    def cancel(self) -> None:
        self.cancelled = True


class TestAuth:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/api/connections/spotify/signin"),
            ("GET", "/api/connections/spotify/signin"),
            ("POST", "/api/connections/spotify/signin/cancel"),
            ("POST", "/api/connections/spotify/signout"),
            ("GET", "/api/music/spotify/library"),
            ("GET", "/api/music/spotify/playlist/abc/items"),
            ("GET", "/api/music/spotify/search?q=x"),
            ("POST", "/api/music/spotify/play"),
            ("GET", "/api/music/spotify/player"),
            ("GET", "/api/music/spotify/devices"),
        ],
    )
    def test_every_route_needs_the_bearer(self, app, method, path):
        assert request(app, method, path, authed=False).code == 401


class TestSignIn:
    def test_apple_has_no_sign_in(self, app):
        assert request(app, "POST", "/api/connections/apple_music/signin").code == 404
        assert request(app, "POST", "/api/connections/apple_music/signout").code == 404
        assert request(app, "POST", "/api/connections/nope/signin").code == 404

    def test_the_lifecycle_never_carries_a_token(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.oauth.OAuthSignIn", FakeSignIn)
        assert json.loads(request(app, "GET", "/api/connections/spotify/signin").body) == {"active": False}
        started = json.loads(request(app, "POST", "/api/connections/spotify/signin").body)
        assert started == {"started": True, "url": "https://vendor.example/authorize?for=spotify", "message": ""}
        first = json.loads(request(app, "GET", "/api/connections/spotify/signin").body)
        assert first == {"active": True, "done": False, "ok": False, "saved": False, "account": "", "message": ""}
        second = json.loads(request(app, "GET", "/api/connections/spotify/signin").body)
        assert second == {
            "active": True,
            "done": True,
            "ok": True,
            "saved": True,
            "account": "dinho",
            "message": "Signed in as dinho",
        }
        # Another service's poll does not see this session.
        assert json.loads(request(app, "GET", "/api/connections/youtube_music/signin").body) == {"active": False}

    def test_a_start_that_cannot_names_the_field(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.oauth.OAuthSignIn", lambda key: FakeSignIn(key, can_start=False))
        body = json.loads(request(app, "POST", "/api/connections/spotify/signin").body)
        assert body["started"] is False and "SPOTIFY_CLIENT_ID" in body["message"] and body["url"] == ""

    def test_starting_again_cancels_the_old_session(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.oauth.OAuthSignIn", FakeSignIn)
        request(app, "POST", "/api/connections/spotify/signin")
        old = app.oauth_signin
        request(app, "POST", "/api/connections/youtube_music/signin")
        assert old.cancelled and app.oauth_signin.key == "youtube_music"

    def test_cancel_only_touches_its_own_service(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.oauth.OAuthSignIn", FakeSignIn)
        request(app, "POST", "/api/connections/spotify/signin")
        assert json.loads(request(app, "POST", "/api/connections/youtube_music/signin/cancel").body) == {"ok": True}
        assert app.oauth_signin is not None
        request(app, "POST", "/api/connections/spotify/signin/cancel")
        assert app.oauth_signin is None

    def test_signout_forgets_the_token_and_the_pages(self, app, monkeypatch):
        applied: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: applied.__setitem__(k, v))
        library.cached(("spotify", "library", "x"), 60, lambda: Page())
        body = json.loads(request(app, "POST", "/api/connections/spotify/signout").body)
        assert body == {"ok": True, "signed_in": False}
        assert applied == {"SPOTIFY_REFRESH_TOKEN": "", "SPOTIFY_ACCOUNT": ""}
        assert not [k for k in library._cache if k[0] == "spotify"]


ROW = Item(
    id="4uLU6hMCjMI75M1A2tKUQC", kind="track", title="Song", url="https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC"
)


class TestLibrary:
    def test_a_page_is_the_row_shape(self, app, monkeypatch):
        seen: list[tuple] = []
        monkeypatch.setattr(
            "yeaboi.connectors.library_spotify.library",
            lambda shelf, cursor, limit: seen.append((shelf, cursor, limit)) or Page((ROW,), "20"),
        )
        resp = request(app, "GET", "/api/music/spotify/library?shelf=liked&cursor=0&limit=20")
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["next_cursor"] == "20" and body["items"][0]["url"] == ROW.url
        assert set(body["items"][0]) == {
            "id",
            "kind",
            "title",
            "subtitle",
            "artwork_url",
            "duration_ms",
            "url",
            "uri",
            "preview_url",
            "count",
        }
        assert seen == [("liked", "0", "20")]

    def test_a_bad_shelf_is_a_400_and_apple_has_no_library_here(self, app):
        assert request(app, "GET", "/api/music/spotify/library?shelf=moods").code == 400
        assert request(app, "GET", "/api/music/apple_music/library").code == 404
        assert request(app, "GET", "/api/music/nope/library").code == 404

    def test_a_refusal_is_its_code_with_its_status(self, app, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.connectors.library_spotify.library",
            lambda shelf, cursor, limit: (_ for _ in ()).throw(LibraryError("signed_out", "Sign in to Spotify", 409)),
        )
        resp = request(app, "GET", "/api/music/spotify/library")
        assert resp.code == 409
        assert json.loads(resp.body) == {"error": "Sign in to Spotify", "code": "signed_out"}

    def test_a_playlist_id_is_checked_before_any_call(self, app):
        assert request(app, "GET", "/api/music/spotify/playlist/../items").code in (400, 404)
        assert request(app, "GET", "/api/music/spotify/playlist/a%20b/items").code == 400

    def test_search_needs_a_query_and_apple_needs_no_sign_in(self, app, monkeypatch):
        assert request(app, "GET", "/api/music/spotify/search").code == 400
        monkeypatch.setattr("yeaboi.connectors.library_apple.search", lambda q, limit, country: Page((ROW,), ""))
        resp = request(app, "GET", "/api/music/apple_music/search?q=daft")
        assert resp.code == 200 and json.loads(resp.body)["items"][0]["id"] == ROW.id

    def test_play_validates_and_hands_back_the_fallback_codes(self, app, monkeypatch):
        assert request(app, "POST", "/api/music/spotify/play", {}).code == 400
        assert request(app, "POST", "/api/music/spotify/play", {"uri": "x", "device_id": 3}).code == 400
        monkeypatch.setattr("yeaboi.connectors.library_spotify.play", lambda uri, device: None)
        assert json.loads(request(app, "POST", "/api/music/spotify/play", {"uri": "spotify:track:x"}).body) == {
            "ok": True
        }
        monkeypatch.setattr(
            "yeaboi.connectors.library_spotify.play",
            lambda uri, device: (_ for _ in ()).throw(LibraryError("premium_required", "Needs Premium", 403)),
        )
        resp = request(app, "POST", "/api/music/spotify/play", {"uri": "spotify:track:x"})
        assert resp.code == 403 and json.loads(resp.body)["code"] == "premium_required"

    def test_player_and_devices_pass_through(self, app, monkeypatch):
        monkeypatch.setattr("yeaboi.connectors.library_spotify.player", lambda: {"playing": False, "item": None})
        monkeypatch.setattr("yeaboi.connectors.library_spotify.devices", lambda: {"devices": []})
        assert json.loads(request(app, "GET", "/api/music/spotify/player").body) == {"playing": False, "item": None}
        assert json.loads(request(app, "GET", "/api/music/spotify/devices").body) == {"devices": []}


def test_no_music_route_is_open(app):
    from yeaboi.app.registry import ROUTES, UNAUTHENTICATED

    music = [r.path for r in ROUTES if r.path.startswith("/api/music") or "/signin" in r.path or "/signout" in r.path]
    assert music and not set(music) & UNAUTHENTICATED
    assert oauth.CALLBACK_PATH not in {r.path for r in ROUTES}
