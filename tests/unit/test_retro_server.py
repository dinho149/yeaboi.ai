"""Unit tests for the Retro LAN server: share codes, token auth, lifecycle."""

import json
import urllib.error
import urllib.request

import pytest

from yeaboi.retro.board import RetroBoard
from yeaboi.retro.server import (
    JoinLimiter,
    RetroServer,
    decode_share_code,
    encode_share_code,
    get_lan_ip,
    make_token,
)


class TestJoinLimiter:
    """The join-code brute-force throttle (F4)."""

    def test_allows_up_to_the_cap(self):
        lim = JoinLimiter()
        ip = "10.0.0.5"
        for _ in range(JoinLimiter._MAX_FAILS - 1):
            lim.record_failure(ip)
        assert lim.blocked(ip) is False  # still under the cap

    def test_blocks_after_cap(self):
        lim = JoinLimiter()
        ip = "10.0.0.5"
        for _ in range(JoinLimiter._MAX_FAILS):
            lim.record_failure(ip)
        assert lim.blocked(ip) is True

    def test_success_resets_counter(self):
        lim = JoinLimiter()
        ip = "10.0.0.5"
        for _ in range(JoinLimiter._MAX_FAILS):
            lim.record_failure(ip)
        lim.record_success(ip)
        assert lim.blocked(ip) is False

    def test_lockout_is_per_ip(self):
        lim = JoinLimiter()
        for _ in range(JoinLimiter._MAX_FAILS):
            lim.record_failure("1.1.1.1")
        assert lim.blocked("1.1.1.1") is True
        assert lim.blocked("2.2.2.2") is False

    def test_lockout_expires_after_window(self, monkeypatch):
        import yeaboi.retro.server as server_mod

        clock = {"t": 1000.0}
        monkeypatch.setattr(server_mod.time, "monotonic", lambda: clock["t"])
        lim = JoinLimiter()
        for _ in range(JoinLimiter._MAX_FAILS):
            lim.record_failure("9.9.9.9")
        assert lim.blocked("9.9.9.9") is True
        clock["t"] += JoinLimiter._LOCKOUT_S + 1  # window elapses
        assert lim.blocked("9.9.9.9") is False


class TestShareCode:
    def test_roundtrip(self):
        ip, port, tok = "192.168.1.24", 5173, make_token()
        code = encode_share_code(ip, port, tok)
        assert decode_share_code(code) == (ip, port, tok)

    def test_roundtrip_tolerates_spacing_and_case(self):
        ip, port, tok = "10.0.0.5", 5199, "abc-DEF_123"
        code = encode_share_code(ip, port, tok)
        assert decode_share_code(code.lower().replace("-", " ")) == (ip, port, tok)

    def test_token_is_unguessable_length(self):
        assert len(make_token()) >= 16
        assert make_token() != make_token()


class TestShareVsHostUrl:
    def test_share_url_is_token_free(self):
        # The shareable URL must NOT carry the token — recipients type the code.
        srv = RetroServer(RetroBoard("s"), port=5288)
        assert "token" not in srv.share_url
        assert srv.share_url == f"http://{srv.ip}:{srv.port}/"

    def test_host_url_still_carries_token(self):
        # The host's private direct link keeps the token for one-click access.
        srv = RetroServer(RetroBoard("s"), port=5289)
        assert f"?token={srv.token}" in srv.url


class TestLanIp:
    def test_returns_ipv4_string(self):
        ip = get_lan_ip()
        assert isinstance(ip, str)
        assert ip.count(".") == 3


@pytest.fixture
def running_server():
    b = RetroBoard("s", "Proj")
    srv = RetroServer(b, port=5210)
    srv.start()
    try:
        yield srv, b
    finally:
        srv.stop()


def _get(url):
    return urllib.request.urlopen(url, timeout=5)


class TestServerRouting:
    def test_get_root_serves_html(self, running_server):
        srv, _ = running_server
        html = _get(f"http://127.0.0.1:{srv.port}/").read().decode()
        assert "<title>Sprint Retro</title>" in html

    def test_api_without_token_forbidden(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/api/cards")
        assert exc.value.code == 403

    def test_api_with_token_returns_cards(self, running_server):
        srv, b = running_server
        b.add_card(grid="went_well", text="hello", author="Sam")
        data = json.load(_get(f"http://127.0.0.1:{srv.port}/api/cards?token={srv.token}"))
        assert data["revision"] >= 1
        assert data["cards"][0]["text"] == "hello"

    def test_post_adds_card(self, running_server):
        srv, b = running_server
        body = json.dumps({"grid": "demos", "text": "new UI", "author": "Rae"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/cards?token={srv.token}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = json.load(urllib.request.urlopen(req, timeout=5))
        assert resp["ok"] and resp["card"]["grid"] == "demos"
        assert b.total() == 1

    def test_post_without_token_forbidden(self, running_server):
        srv, _ = running_server
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/cards",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 403

    def test_unknown_path_404(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/nope")
        assert exc.value.code == 404


def _post(srv, path, body, *, token=None):
    tok = srv.token if token is None else token
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.port}{path}?token={tok}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.load(urllib.request.urlopen(req, timeout=5))


class TestStateEndpoint:
    def test_state_shape(self, running_server):
        srv, b = running_server
        b.add_card(grid="went_well", text="ci", author="Sam")
        data = json.load(_get(f"http://127.0.0.1:{srv.port}/api/state?token={srv.token}"))
        assert set(data) == {
            "revision",
            "cards",
            "carried",
            "presence",
            "typing",
            "timer",
            "reaction_events",
            "broadcast",
            "locked",
        }

    def test_state_forbidden_without_token(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/api/state")
        assert exc.value.code == 403


class TestJoinEndpoint:
    def test_correct_code_returns_token(self, running_server):
        srv, _ = running_server
        # /api/join is unauthenticated (it hands out the token), so post without one.
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/join",
            data=json.dumps({"code": srv.join_code}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = json.load(urllib.request.urlopen(req, timeout=5))
        assert resp["ok"] and resp["token"] == srv.token

    def test_brute_force_is_rate_limited(self, running_server):
        srv, _ = running_server

        def _attempt(code):
            req = urllib.request.Request(
                f"http://127.0.0.1:{srv.port}/api/join",
                data=json.dumps({"code": code}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
                return 200
            except urllib.error.HTTPError as e:
                return e.code

        codes = [403] * JoinLimiter._MAX_FAILS
        assert [_attempt("WRONG-COD") for _ in codes] == codes
        # Once the cap is hit, further attempts — even the correct code — are throttled.
        assert _attempt("WRONG-COD") == 429
        assert _attempt(srv.join_code) == 429


class TestReactEndpoint:
    def test_toggle(self, running_server):
        srv, b = running_server
        c = b.add_card(grid="went_well", text="ci", author="Sam")
        r = _post(srv, "/api/react", {"card_id": c.id, "emoji": "👍", "pid": "p1"})
        assert r["reacted"] is True and r["state"]["cards"][0]["reactions"] == {"👍": 1}
        r = _post(srv, "/api/react", {"card_id": c.id, "emoji": "👍", "pid": "p1"})
        assert r["reacted"] is False and r["state"]["cards"][0]["reactions"] == {}

    def test_forbidden_without_token(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/react", {"card_id": "x", "emoji": "👍", "pid": "p"}, token="")
        assert exc.value.code == 403


class TestPresenceEndpoint:
    def test_records_and_returns_state(self, running_server):
        srv, _ = running_server
        state = _post(srv, "/api/presence", {"pid": "p1", "name": "Sam", "avatar": "🤠", "typing_grid": "demos"})
        assert any(p["name"] == "Sam" for p in state["presence"])
        assert any(t["grid"] == "demos" for t in state["typing"])


class TestTimerEndpoint:
    def test_start_and_stop(self, running_server):
        srv, _ = running_server
        # The shared timer is admin-only — pass the host admin secret.
        r = _post(srv, "/api/timer", {"action": "start", "duration": 120, "pid": "p1", "admin": srv.admin_token})
        assert r["state"]["timer"]["running"] is True
        r = _post(srv, "/api/timer", {"action": "stop", "pid": "p1", "admin": srv.admin_token})
        assert r["state"]["timer"]["running"] is False

    def test_non_admin_forbidden(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/timer", {"action": "start", "duration": 60, "pid": "p1"})
        assert exc.value.code == 403


class TestCardsReturnsState:
    def test_post_card_returns_state(self, running_server):
        srv, _ = running_server
        r = _post(srv, "/api/cards", {"grid": "demos", "text": "new UI", "author": "Rae"})
        assert r["ok"] and "state" in r and r["state"]["cards"][0]["text"] == "new UI"


class TestTokenFreePage:
    def test_served_page_has_no_token(self, running_server):
        srv, _ = running_server
        page = _get(f"http://127.0.0.1:{srv.port}/").read().decode()
        assert srv.token not in page  # GET / is unauthenticated — must not leak the token


class TestJoinCode:
    def test_right_code_returns_token(self, running_server):
        srv, _ = running_server
        # /api/join is unauthenticated (no token in the URL).
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/join",
            data=json.dumps({"code": srv.join_code}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        assert json.load(urllib.request.urlopen(req, timeout=5))["token"] == srv.token

    def test_wrong_code_forbidden(self, running_server):
        srv, _ = running_server
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/join",
            data=json.dumps({"code": "WRONG-XXX"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 403


class TestQrEndpoint:
    def test_token_gated(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/api/qr")
        assert exc.value.code == 403

    def test_returns_svg(self, running_server):
        srv, _ = running_server
        body = _get(f"http://127.0.0.1:{srv.port}/api/qr?token={srv.token}").read()
        assert b"<svg" in body  # segno inline SVG


class TestInviteEndpoint:
    """What the browser's invite panel copies to a teammate's clipboard."""

    def test_token_gated(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/api/invite")
        assert exc.value.code == 403

    def test_returns_the_join_code_and_a_token_free_url(self, running_server):
        srv, _ = running_server
        body = json.load(_get(f"http://127.0.0.1:{srv.port}/api/invite?token={srv.token}"))
        assert body["joinCode"] == srv.join_code
        assert body["shareUrl"].endswith("/")
        assert "token=" not in body["shareUrl"]  # it is the *participant* link

    def test_never_returns_the_host_link_or_the_admin_secret(self, running_server):
        # The host link skips the gate and carries the admin secret, and every
        # participant can read this response. Handing it out here would promote
        # the whole room to host.
        srv, _ = running_server
        raw = _get(f"http://127.0.0.1:{srv.port}/api/invite?token={srv.token}").read().decode()
        assert srv.admin_token not in raw
        assert srv.token not in raw

    def test_url_follows_the_host_header_so_a_tunnel_link_is_the_tunnel_link(self, running_server):
        # The server answers on a LAN IP and on a trycloudflare hostname at once;
        # only the request knows which one this visitor arrived on. Copying the
        # LAN URL to a remote teammate is the bug this prevents.
        srv, _ = running_server
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/invite?token={srv.token}",
            headers={"Host": "abc-def.trycloudflare.com", "X-Forwarded-Proto": "https"},
        )
        body = json.load(urllib.request.urlopen(req, timeout=5))
        assert body["shareUrl"] == "https://abc-def.trycloudflare.com/"

    def test_falls_back_to_http_without_a_forwarded_proto(self, running_server):
        srv, _ = running_server
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/invite?token={srv.token}",
            headers={"Host": "192.168.1.20:8712"},
        )
        body = json.load(urllib.request.urlopen(req, timeout=5))
        assert body["shareUrl"] == "http://192.168.1.20:8712/"


class TestCardMutations:
    def test_edit_author_only(self, running_server):
        srv, _ = running_server
        r = _post(srv, "/api/cards", {"grid": "went_well", "text": "x", "author": "Sam", "pid": "p1"})
        cid = r["card"]["id"]
        with pytest.raises(urllib.error.HTTPError) as exc:  # wrong pid → 403
            _post(srv, "/api/card/edit", {"card_id": cid, "text": "y", "pid": "p2"})
        assert exc.value.code == 403
        ok = _post(srv, "/api/card/edit", {"card_id": cid, "text": "y", "pid": "p1"})
        assert ok["ok"] and ok["state"]["cards"][0]["text"] == "y"

    def test_delete_author_only(self, running_server):
        srv, _ = running_server
        cid = _post(srv, "/api/cards", {"grid": "demos", "text": "x", "author": "a", "pid": "p1"})["card"]["id"]
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/card/delete", {"card_id": cid, "pid": "p2"})
        assert exc.value.code == 403
        assert _post(srv, "/api/card/delete", {"card_id": cid, "pid": "p1"})["ok"] is True

    def test_move_open_to_anyone(self, running_server):
        srv, _ = running_server
        cid = _post(srv, "/api/cards", {"grid": "went_well", "text": "x", "author": "a", "pid": "p1"})["card"]["id"]
        r = _post(srv, "/api/card/move", {"card_id": cid, "grid": "demos", "index": 0, "pid": "someone-else"})
        assert r["ok"] and r["state"]["cards"][0]["grid"] == "demos"


class TestLifecycle:
    def test_properties_expose_join_info(self):
        srv = RetroServer(RetroBoard("s"), port=5211)
        assert srv.url.startswith("http://")
        assert "?token=" in srv.url
        assert len(srv.display_code) == 9  # "XXXX-XXXX"

    def test_start_stop_idempotent_stop(self):
        srv = RetroServer(RetroBoard("s"), port=5212)
        srv.start()
        srv.stop()
        srv.stop()  # second stop is a no-op, must not raise


class TestAdminEndpoints:
    def test_url_carries_admin_share_url_does_not(self, running_server):
        srv, _ = running_server
        assert f"admin={srv.admin_token}" in srv.url
        assert "admin=" not in srv.share_url and srv.admin_token not in srv.share_url

    def test_broadcast_requires_admin(self, running_server):
        srv, _ = running_server
        # Authed teammate (token) but no admin secret → 403.
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/admin/broadcast", {"theme": "synthwave"})
        assert exc.value.code == 403

    def test_broadcast_theme_and_music_with_admin(self, running_server):
        srv, _ = running_server
        r = _post(srv, "/api/admin/broadcast", {"theme": "forest", "admin": srv.admin_token})
        assert r["ok"] and r["state"]["broadcast"]["theme"] == "forest"
        r = _post(srv, "/api/admin/broadcast", {"music": {"playing": True, "channel": 0}, "admin": srv.admin_token})
        assert r["state"]["broadcast"]["music"]["playing"] is True

    def test_lock_requires_admin_and_freezes_board(self, running_server):
        srv, b = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/admin/lock", {"locked": True})
        assert exc.value.code == 403
        r = _post(srv, "/api/admin/lock", {"locked": True, "admin": srv.admin_token})
        assert r["ok"] and r["state"]["locked"] is True
        # A normal card POST is now rejected while locked.
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/cards", {"grid": "demos", "text": "blocked", "author": "Sam"})
        assert exc.value.code == 400


class TestAdminBroadcastValidation:
    def test_empty_broadcast_is_400(self, running_server):
        srv, _ = running_server
        # Neither theme nor music → client error, not a silent 200 no-op.
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/admin/broadcast", {"admin": srv.admin_token})
        assert exc.value.code == 400


class TestNoSecretLogging:
    def test_query_string_secrets_not_logged(self, running_server, caplog):
        import logging

        srv, _ = running_server
        # The host link carries token + admin in the query string; the access log
        # must never write either to disk (regression: log_request logged the full
        # request line, leaking the token/admin secret at DEBUG).
        with caplog.at_level(logging.DEBUG, logger="yeaboi.retro.server"):
            _get(f"http://127.0.0.1:{srv.port}/api/state?token={srv.token}&admin={srv.admin_token}")
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert srv.token not in blob
        assert srv.admin_token not in blob
