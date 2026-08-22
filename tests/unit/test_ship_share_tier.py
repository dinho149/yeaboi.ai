"""The ship approval board is a share surface too.

It is loopback-bound and tunnel-fronted like the retro, poker and output-share
boards, and it was the one that reached ``CloudflareTunnel`` directly. Under
``YEABOI_SHARE_MODE=access`` that published a public ``trycloudflare.com`` URL —
the single thing the tier promises cannot happen.
"""

from __future__ import annotations

import pytest

from yeaboi.sharing.access import secret_equal


class TestItGoesThroughTheTierFactory:
    def test_no_surface_constructs_a_tunnel_directly(self):
        """Every share surface must reach the transport through open_tunnel.

        A direct constructor cannot see YEABOI_SHARE_MODE, so it silently opts
        that surface out of the tier.
        """
        from pathlib import Path

        surfaces = [
            Path("src/yeaboi/ship/live.py"),
            Path("src/yeaboi/ui/shared/_output_share.py"),
            Path("src/yeaboi/ui/mode_select/__init__.py"),
        ]
        offenders = []
        for path in surfaces:
            body = path.read_text()
            if "CloudflareTunnel(" in body:
                offenders.append(str(path))
        assert offenders == [], f"construct the transport via open_tunnel: {offenders}"

    def test_a_refused_tier_leaves_the_board_on_loopback(self, monkeypatch):
        """No URL is published and no gate is armed when the tier cannot start."""
        from yeaboi.sharing.tunnel import ShareTransport
        from yeaboi.ship import live as ship_live

        monkeypatch.setattr(
            "yeaboi.sharing.tunnel.open_tunnel",
            lambda port, **kw: ShareTransport(None, None, "Cloudflare Access is incomplete"),
        )

        class FakeServer:
            port = 5473
            published: list[str] = []
            gates: list[object] = []

            def set_public_url(self, url):
                self.published.append(url)

            def set_access_gate(self, gate):
                self.gates.append(gate)

        session = object.__new__(ship_live.ShipBoardSession)
        session.server = FakeServer()
        session._tunnel = None
        session._tunnel_factory = None
        session._bring_up_tunnel()

        assert session.server.published == []
        assert session._tunnel is None

    def test_the_gate_is_armed_before_the_tunnel_starts(self, monkeypatch):
        """Verification must be on before the door is."""
        from yeaboi.sharing.tunnel import ShareTransport
        from yeaboi.ship import live as ship_live

        order: list[str] = []
        gate = object()

        class FakeTunnel:
            def start(self, *a, **kw):
                order.append("tunnel-start")
                return "https://ship.example.com/"

        monkeypatch.setattr(
            "yeaboi.sharing.tunnel.open_tunnel",
            lambda port, **kw: ShareTransport(FakeTunnel(), gate),
        )

        class FakeServer:
            port = 5473
            display_code = "ABCD-2345"

            def set_public_url(self, url):
                order.append("publish")

            def set_access_gate(self, g):
                order.append("gate")
                assert g is gate

        session = object.__new__(ship_live.ShipBoardSession)
        session.server = FakeServer()
        session._tunnel = None
        session._tunnel_factory = None
        session._bring_up_tunnel()

        assert order == ["gate", "tunnel-start", "publish"]


class TestTheSixDefectsReachedShipToo:
    def test_a_non_ascii_credential_compares_false_rather_than_raising(self):
        # compare_digest raises TypeError on non-ASCII str; the ship server used
        # it raw on both the token and the join code.
        assert secret_equal("café", "cafe") is False
        assert secret_equal("café", "café") is True

    def test_ship_server_uses_the_shared_helpers(self):
        from pathlib import Path

        body = Path("src/yeaboi/ship/server.py").read_text()
        assert "secrets.compare_digest" not in body
        assert "self.client_address[0]" not in body
        assert "client_key(" in body


class TestShipIdentityEnforcement:
    """The tier's rules reach the ship board's two POST routes too.

    Ship was the surface the tier audit missed once already (a fifth tunnel
    surface constructing its transport directly); these pin the two rules the
    other boards enforce — join sits behind identity, presence cannot claim a
    pid — so it cannot drift again.
    """

    HOSTNAME = "boards.example.com"

    @pytest.fixture
    def gated(self, monkeypatch):
        import yeaboi.ship.server as server_mod
        from yeaboi.sharing.identity import AccessGate, VerifiedUser
        from yeaboi.ship.board import ShipBoard
        from yeaboi.ship.server import ShipServer

        monkeypatch.setattr(server_mod, "build_board_html", lambda *a, **k: "<!doctype html><title>S</title>")
        board = ShipBoard("run-1", db_path=None, story_title="Story", project_name="Proj")
        srv = ShipServer(board, port=5493)
        srv.start()

        class _Verifier:
            def verify(self, headers):
                if headers.get("Cf-Access-Jwt-Assertion") == "good-token":
                    return VerifiedUser(email="ada@example.com", subject="sub-ada")
                return None

        srv.set_access_gate(AccessGate(self.HOSTNAME, _Verifier(), frozenset()))
        try:
            yield srv, board
        finally:
            srv.stop()

    def _post(self, srv, path, body, *, host, jwt=None, token=None):
        import json as _json
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{srv.port}{path}"
        if token is not None:
            url += ("&" if "?" in url else "?") + f"token={token}"
        req = urllib.request.Request(url, data=_json.dumps(body).encode(), method="POST")
        req.add_header("Host", host)
        req.add_header("Content-Type", "application/json")
        if jwt is not None:
            req.add_header("Cf-Access-Jwt-Assertion", jwt)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, _json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, _json.loads(e.read() or b"{}")

    def test_a_tunnel_borne_join_needs_identity_before_a_code_attempt(self, gated):
        """An unverified stranger must not spend the limiter budget or brute the
        code — the same rule as every other board's /api/join."""
        srv, _ = gated
        status, _body = self._post(srv, "/api/join", {"code": srv.join_code}, host=self.HOSTNAME)
        assert status == 403

    def test_a_verified_join_still_exchanges_the_code(self, gated):
        srv, _ = gated
        status, body = self._post(srv, "/api/join", {"code": srv.join_code}, host=self.HOSTNAME, jwt="good-token")
        assert status == 200
        assert body["token"] == srv.token

    def test_presence_cannot_claim_someone_elses_pid(self, gated):
        """The claimed pid and name are replaced by the verified identity, so the
        presence list shows who is actually there."""
        srv, board = gated
        status, _body = self._post(
            srv,
            "/api/presence",
            {"pid": "someone-else", "name": "Mallory"},
            host=self.HOSTNAME,
            jwt="good-token",
            token=srv.token,
        )
        assert status == 200
        assert "someone-else" not in board._presence
        assert "cf:sub-ada" in board._presence
        assert board._presence["cf:sub-ada"]["name"] == "ada"  # the verified byline, not "Mallory"

    def test_the_hosts_loopback_presence_is_untouched(self, gated):
        srv, board = gated
        status, _body = self._post(
            srv,
            "/api/presence",
            {"pid": "local-pid", "name": "Host"},
            host=f"127.0.0.1:{srv.port}",
            token=srv.token,
        )
        assert status == 200
        assert "local-pid" in board._presence
