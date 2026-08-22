"""Unit tests for the ship board server: token auth, join code, loopback, lifecycle.

The ``ship`` React bundle is built in a later step, so these tests stub
``build_board_html`` — the server's job is routing and access control, which is
independent of the page body.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import yeaboi.ship.server as server_mod
from yeaboi.ship.board import ShipBoard
from yeaboi.ship.server import JoinLimiter, ShipServer


@pytest.fixture
def running_server(monkeypatch):
    monkeypatch.setattr(server_mod, "build_board_html", lambda *a, **k: "<!doctype html><title>Ship</title>")
    board = ShipBoard("run-1", db_path=None, story_title="Story", project_name="Proj")
    srv = ShipServer(board, port=5490)
    srv.start()
    try:
        yield srv, board
    finally:
        srv.stop()


def _get(url):
    return urllib.request.urlopen(url, timeout=5)


def _post(srv, path, body, *, token=None):
    tok = srv.token if token is None else token
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.port}{path}{sep}token={tok}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=5))


class TestBind:
    def test_binds_loopback_only(self, running_server):
        srv, _ = running_server
        assert srv._httpd.server_address[0] == "127.0.0.1"

    def test_host_link_carries_token_and_admin(self, running_server):
        srv, _ = running_server
        assert "token=" in srv.url and "admin=" in srv.url

    def test_share_url_is_empty_until_the_tunnel(self, running_server):
        srv, _ = running_server
        assert srv.share_url == ""  # no LAN fallback; the tunnel is the only address


class TestRouting:
    def test_root_serves_the_page_with_document_headers(self, running_server):
        srv, _ = running_server
        resp = _get(f"http://127.0.0.1:{srv.port}/")
        body = resp.read().decode()
        assert "<title>Ship</title>" in body
        # Every board response carries the protective header set (web/security.py).
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_state_requires_the_token(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/api/state")
        assert exc.value.code == 403

    def test_state_with_token_returns_the_snapshot(self, running_server):
        srv, _ = running_server
        data = json.load(_get(f"http://127.0.0.1:{srv.port}/api/state?token={srv.token}"))
        assert data["run_id"] == "run-1"
        assert data["status"] == "starting"  # no store row for this board

    def test_unknown_path_404s(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/nope")
        assert exc.value.code == 404


class TestJoin:
    def test_correct_code_exchanges_for_the_token(self, running_server):
        srv, _ = running_server
        resp = _post(srv, "/api/join", {"code": srv.join_code}, token="")
        assert resp["token"] == srv.token

    def test_wrong_code_is_forbidden(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/join", {"code": "WRONG-COD"}, token="")
        assert exc.value.code == 403


class TestPresence:
    def test_presence_records_a_watcher(self, running_server):
        srv, board = running_server
        resp = _post(srv, "/api/presence", {"pid": "p1", "name": "Ada"})
        assert resp["run_id"] == "run-1"
        assert board.present_pids() == ("p1",)

    def test_presence_quiet_skips_the_snapshot(self, running_server):
        srv, _ = running_server
        resp = _post(srv, "/api/presence?quiet=1", {"pid": "p2"})
        assert resp == {"ok": True}

    def test_presence_requires_the_token(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/presence", {"pid": "p1"}, token="bad")
        assert exc.value.code == 403


class TestLimiter:
    def test_wrapper_uses_the_shared_cap(self):
        lim = JoinLimiter()
        for _ in range(JoinLimiter._MAX_FAILS):
            lim.record_failure("1.2.3.4")
        assert lim.blocked("1.2.3.4") is True
        assert lim.blocked("5.6.7.8") is False


class TestMalformedContentLengthDropsTheConnection:
    def test_bad_length_is_400_and_the_connection_dies(self, running_server):
        """The undeclared body stays queued on the socket, so a keep-alive reuse
        would parse mid-body — the 400 must take the connection with it."""
        import http.client

        srv = running_server[0]
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        try:
            conn.putrequest("POST", "/api/join")
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", "not-a-number")
            conn.endheaders()
            conn.send(b"{}")
            response = conn.getresponse()
            assert response.status == 400
            response.read()
            with pytest.raises((http.client.HTTPException, OSError)):
                conn.putrequest("POST", "/api/join")
                conn.putheader("Content-Type", "application/json")
                conn.putheader("Content-Length", "2")
                conn.endheaders()
                conn.send(b"{}")
                conn.getresponse()
        finally:
            conn.close()
