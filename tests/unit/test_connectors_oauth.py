"""The music sign-in: PKCE, the authorize URL, the callback, the exchange, the cache."""

from __future__ import annotations

import base64
import hashlib
import threading
import urllib.request

import pytest

from yeaboi.connectors import oauth, oauth_clients


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}
        self.content = b"{}"
        self.headers: dict[str, str] = {}

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    oauth.reset_cache()
    for env in ("SPOTIFY_CLIENT_ID", "SPOTIFY_REFRESH_TOKEN", "SPOTIFY_ACCOUNT", "YEABOI_OAUTH_PORT"):
        monkeypatch.delenv(env, raising=False)
    yield
    oauth.reset_cache()


class TestPkce:
    def test_the_challenge_is_the_s256_of_the_verifier(self):
        verifier, challenge = oauth.pkce_pair()
        assert 43 <= len(verifier) <= 128
        assert set(verifier) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        assert challenge == expected
        assert "=" not in challenge

    def test_pairs_and_states_are_fresh(self):
        assert oauth.pkce_pair()[0] != oauth.pkce_pair()[0]
        assert oauth.new_state() != oauth.new_state()


class TestAuthorizeUrl:
    def test_spotify_names_the_loopback_ip_and_pkce(self):
        from urllib.parse import parse_qs, urlparse

        url = oauth.authorize_url(oauth.PROVIDERS["spotify"], "cid", oauth.redirect_uri("spotify", 8643), "st", "ch")
        parsed = urlparse(url)
        assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://accounts.spotify.com/authorize"
        query = parse_qs(parsed.query)
        assert query["redirect_uri"] == ["http://127.0.0.1:8643/callback/spotify"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"] == ["ch"] and query["state"] == ["st"] and query["client_id"] == ["cid"]
        assert "user-library-read" in query["scope"][0]
        assert "access_type" not in query

    def test_google_asks_for_offline_consent_and_only_reads(self):
        from urllib.parse import parse_qs, urlparse

        url = oauth.authorize_url(
            oauth.PROVIDERS["youtube_music"], "cid", oauth.redirect_uri("youtube_music"), "s", "c"
        )
        query = parse_qs(urlparse(url).query)
        assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
        assert query["scope"] == ["https://www.googleapis.com/auth/youtube.readonly"]

    def test_the_port_is_fixed_and_overridable(self, monkeypatch):
        assert oauth.oauth_port() == 8643
        monkeypatch.setenv("YEABOI_OAUTH_PORT", "9911")
        assert oauth.redirect_uri("spotify") == "http://127.0.0.1:9911/callback/spotify"
        monkeypatch.setenv("YEABOI_OAUTH_PORT", "not-a-port")
        assert oauth.oauth_port() == 8643
        monkeypatch.setenv("YEABOI_OAUTH_PORT", "70000")
        assert oauth.oauth_port() == 8643


class TestCallback:
    def test_a_matching_state_yields_the_code(self):
        assert oauth.parse_callback("/callback/spotify?code=abc&state=st", "st") == "abc"

    def test_anything_else_is_a_named_refusal_that_echoes_nothing(self):
        with pytest.raises(oauth.CallbackError, match="did not match"):
            oauth.parse_callback("/callback/spotify?code=abc&state=other", "st")
        with pytest.raises(oauth.CallbackError, match="refused"):
            oauth.parse_callback("/callback/spotify?error=access_denied&state=st", "st")
        with pytest.raises(oauth.CallbackError, match="without a code"):
            oauth.parse_callback("/callback/spotify?state=st", "st")
        with pytest.raises(oauth.CallbackError) as info:
            oauth.parse_callback("/callback/spotify?error=server_error&error_description=SECRET-DETAIL&state=st", "st")
        assert "SECRET-DETAIL" not in str(info.value)


class TestListener:
    def test_it_catches_one_redirect_and_answers_a_page_without_the_query(self):
        listener = oauth._CallbackServer("spotify", "st", 0)
        port = listener._server.server_address[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/callback/spotify?code=the-code&state=st") as resp:
                body = resp.read().decode()
                assert resp.status == 200
            assert "close this tab" in body and "the-code" not in body
            assert listener.code == "the-code" and listener.done
            listener._thread.join(timeout=3)
            assert not listener._thread.is_alive()
        finally:
            listener.close()

    def test_a_wrong_state_is_a_400_and_a_message(self):
        listener = oauth._CallbackServer("spotify", "st", 0)
        port = listener._server.server_address[1]
        try:
            with pytest.raises(urllib.error.HTTPError) as info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/callback/spotify?code=x&state=nope")
            assert info.value.code == 400
            assert "x" not in info.value.read().decode().split("<h1>")[1]
            assert listener.error and not listener.code
        finally:
            listener.close()

    def test_close_releases_the_port_for_the_next_sign_in(self):
        first = oauth._CallbackServer("spotify", "st", 0)
        port = first._server.server_address[1]
        first.close()
        second = oauth._CallbackServer("spotify", "st", port)
        second.close()

    def test_a_non_ascii_state_is_a_400_page_not_a_traceback(self):
        listener = oauth._CallbackServer("spotify", "st", 0)
        port = listener._server.server_address[1]
        try:
            with pytest.raises(urllib.error.HTTPError) as info:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/callback/spotify?code=x&state=%C3%A9")
            assert info.value.code == 400
        finally:
            listener.close()

    def test_a_busy_port_is_a_hard_error_never_a_walk(self):
        first = oauth._CallbackServer("spotify", "st", 0)
        port = first._server.server_address[1]
        try:
            with pytest.raises(OSError):
                oauth._CallbackServer("spotify", "st", port)
        finally:
            first.close()


class TestSession:
    def test_no_client_fails_cleanly_and_names_the_field(self):
        session = oauth.OAuthSignIn("spotify")
        assert session.start() is False
        assert "SPOTIFY_CLIENT_ID" in session.error and session.done and not session.ok

    def test_apple_has_no_sign_in(self):
        session = oauth.OAuthSignIn("apple_music")
        assert session.start() is False and "no sign-in" in session.error

    def test_a_busy_port_names_the_override(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "own-client")
        holder = oauth._CallbackServer("spotify", "st", 0)
        monkeypatch.setenv("YEABOI_OAUTH_PORT", str(holder._server.server_address[1]))
        try:
            session = oauth.OAuthSignIn("spotify")
            assert session.start() is False
            assert "YEABOI_OAUTH_PORT" in session.error
        finally:
            holder.close()

    def test_the_whole_flow_persists_the_token_and_the_name_and_never_echoes(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "own-client")
        monkeypatch.setenv("YEABOI_OAUTH_PORT", "0")
        applied: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: applied.__setitem__(k, v))
        posted: list[dict] = []

        def fake_post(url, *, data, timeout=10):
            posted.append({"url": url, **data})
            return FakeResponse(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600})

        monkeypatch.setattr(oauth.http, "post_form", fake_post)
        monkeypatch.setattr(oauth.http, "get_json", lambda url, headers: FakeResponse(200, {"display_name": "dinho"}))

        session = oauth.OAuthSignIn("spotify")
        assert session.start() is True
        assert session.url.startswith("https://accounts.spotify.com/authorize?")
        port = session._listener._server.server_address[1]
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback/spotify?code=the-code&state={session._state}").read()
        for _ in range(100):
            session.poll()
            if session.done:
                break
            session._worker and session._worker.join(timeout=0.1)
        assert session.ok and session.persisted
        assert applied == {"SPOTIFY_REFRESH_TOKEN": "RT", "SPOTIFY_ACCOUNT": "dinho"}
        assert posted[0]["grant_type"] == "authorization_code" and posted[0]["code"] == "the-code"
        assert posted[0]["code_verifier"] == session._verifier and "client_secret" not in posted[0]
        assert session.message == "Signed in as dinho"
        assert "RT" not in session.message and "AT" not in session.url

    def test_a_refused_exchange_is_a_message(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "own-client")
        monkeypatch.setenv("YEABOI_OAUTH_PORT", "0")
        monkeypatch.setattr(
            oauth.http, "post_form", lambda url, *, data, timeout=10: FakeResponse(400, {"error": "invalid_grant"})
        )
        session = oauth.OAuthSignIn("spotify")
        assert session.start()
        port = session._listener._server.server_address[1]
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback/spotify?code=c&state={session._state}").read()
        for _ in range(100):
            session.poll()
            if session.done:
                break
            session._worker and session._worker.join(timeout=0.1)
        assert not session.ok and "client ID" in session.error

    def test_a_token_body_that_is_not_one_ends_the_sign_in(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "own-client")
        monkeypatch.setenv("YEABOI_OAUTH_PORT", "0")
        monkeypatch.setattr(oauth.http, "post_form", lambda url, *, data, timeout=10: FakeResponse(200, None))
        session = oauth.OAuthSignIn("spotify")
        assert session.start()
        port = session._listener._server.server_address[1]
        urllib.request.urlopen(f"http://127.0.0.1:{port}/callback/spotify?code=c&state={session._state}").read()
        for _ in range(100):
            session.poll()
            if session.done:
                break
            session._worker and session._worker.join(timeout=0.1)
        assert session.done and not session.ok and "token" in session.error

    def test_cancel_is_safe_in_any_state(self):
        session = oauth.OAuthSignIn("spotify")
        session.cancel()
        session.cancel()
        assert session.message == "Sign-in cancelled"


class TestBearer:
    def test_a_missing_token_is_signed_out(self):
        with pytest.raises(oauth.SignedOutError, match="Sign in"):
            oauth.bearer_for("spotify")

    def test_a_refresh_is_cached_and_a_rotated_token_persisted(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "own-client")
        monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "RT1")
        applied: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: applied.__setitem__(k, v))
        calls: list[dict] = []

        def fake_post(url, *, data, timeout=10):
            calls.append(data)
            return FakeResponse(200, {"access_token": "AT1", "refresh_token": "RT2", "expires_in": 3600})

        monkeypatch.setattr(oauth.http, "post_form", fake_post)
        assert oauth.bearer_for("spotify") == "AT1"
        assert oauth.bearer_for("spotify") == "AT1"
        assert len(calls) == 1 and calls[0]["grant_type"] == "refresh_token"
        assert applied == {"SPOTIFY_REFRESH_TOKEN": "RT2"}

    def test_a_refused_refresh_signs_out(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "own-client")
        monkeypatch.setenv("SPOTIFY_REFRESH_TOKEN", "RT-expired")
        monkeypatch.setenv("SPOTIFY_ACCOUNT", "dinho")
        applied: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: applied.__setitem__(k, v))
        monkeypatch.setattr(
            oauth.http, "post_form", lambda url, *, data, timeout=10: FakeResponse(400, {"error": "invalid_grant"})
        )
        with pytest.raises(oauth.SignedOutError, match="sign in again"):
            oauth.bearer_for("spotify")
        assert applied == {"SPOTIFY_REFRESH_TOKEN": "", "SPOTIFY_ACCOUNT": ""}

    def test_sign_out_clears_both_the_cache_and_the_pages(self, monkeypatch):
        from yeaboi.connectors import library

        applied: dict[str, str] = {}
        monkeypatch.setattr("yeaboi.config.apply_config_value", lambda k, v: applied.__setitem__(k, v))
        oauth._cache_put("spotify", "AT", 3600)
        library.cached(("spotify", "library", "x"), 60, lambda: library.Page())
        oauth.sign_out("spotify")
        assert applied == {"SPOTIFY_REFRESH_TOKEN": "", "SPOTIFY_ACCOUNT": ""}
        assert "spotify" not in oauth._cache
        assert not [k for k in library._cache if k[0] == "spotify"]
        oauth.sign_out("apple_music")  # nothing to do, nothing raised


class TestClients:
    def test_the_users_own_client_wins(self, monkeypatch):
        monkeypatch.setenv("SPOTIFY_CLIENT_ID", "own")
        assert oauth_clients.resolve("spotify") == oauth_clients.OAuthClient("own", "", own=True)

    def test_a_blank_builtin_is_none(self, monkeypatch):
        monkeypatch.delenv("SPOTIFY_CLIENT_ID", raising=False)
        monkeypatch.setattr(oauth_clients, "_BUILTIN", {"spotify": ("", "")})
        assert oauth_clients.resolve("spotify") is None
        monkeypatch.setattr(oauth_clients, "_BUILTIN", {"spotify": ("builtin", "")})
        assert oauth_clients.resolve("spotify") == oauth_clients.OAuthClient("builtin", "", own=False)

    def test_google_carries_its_non_confidential_secret(self, monkeypatch):
        monkeypatch.setenv("YOUTUBE_MUSIC_CLIENT_ID", "gid")
        monkeypatch.setenv("YOUTUBE_MUSIC_CLIENT_SECRET", "gsecret")
        assert oauth_clients.resolve("youtube_music") == oauth_clients.OAuthClient("gid", "gsecret", own=True)


def test_the_listener_thread_is_a_daemon():
    listener = oauth._CallbackServer("spotify", "st", 0)
    try:
        assert isinstance(listener._thread, threading.Thread) and listener._thread.daemon
    finally:
        listener.close()
