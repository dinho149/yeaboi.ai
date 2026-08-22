"""End-to-end: what an armed Access gate changes about a live retro board.

The unit tests in ``test_sharing_identity.py`` prove the verifier refuses the
right tokens. These prove the *server* actually asks it — over a real socket,
through the real handler, on the real routes — because a verifier nothing calls
is a verifier that protects nothing.

The verifier itself is stubbed here on purpose. The crypto has its own file; what
is under test is the wiring: which requests are made to verify, which are not,
and what happens to a client that lies about who it is.

Requests are shaped the way the two paths really arrive:

* **tunnel-borne** — ``Host: board.example.com``, because that is what the
  generated ingress pins ``httpHostHeader`` to;
* **the host's own browser** — ``Host: 127.0.0.1:<port>``, which is also what
  cloudflared's socket looks like, and is exactly why the ``Host`` header rather
  than ``client_address`` is what decides.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from yeaboi.retro.board import RetroBoard
from yeaboi.retro.server import RetroServer
from yeaboi.sharing.identity import AccessGate, VerifiedUser

HOSTNAME = "board.example.com"
ADA = VerifiedUser(email="ada@example.com", subject="sub-ada")
BOB = VerifiedUser(email="bob@example.com", subject="sub-bob")

#: The stub accepts this string and nothing else. Real tokens are exercised in
#: test_sharing_identity.py; here the question is only who gets asked.
GOOD = "a-token-the-verifier-likes"


class _StubVerifier:
    """Answers from a fixed table instead of from Cloudflare's signing keys."""

    def __init__(self, tokens: dict[str, VerifiedUser]) -> None:
        self.tokens = tokens
        self.calls = 0

    def verify(self, headers):
        self.calls += 1
        return self.tokens.get(headers.get("Cf-Access-Jwt-Assertion", ""))


@pytest.fixture
def board_with_access():
    board = RetroBoard("Sprint 9", "Proj")
    server = RetroServer(board, port=5230)
    server.start()
    verifier = _StubVerifier({GOOD: ADA, "bobs-token": BOB})
    server.set_access_gate(AccessGate(HOSTNAME, verifier, frozenset({"ada@example.com"})))
    try:
        yield server, board, verifier
    finally:
        server.stop()


def _call(server, path, *, host, token=None, jwt=None, body=None):
    """One request, with the Host (and optionally the JWT) a real one would carry."""
    url = f"http://127.0.0.1:{server.port}{path}"
    if token is not None:
        url += ("&" if "?" in url else "?") + f"token={token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        method="POST" if body is not None else "GET",
    )
    request.add_header("Host", host)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if jwt is not None:
        request.add_header("Cf-Access-Jwt-Assertion", jwt)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class TestTheBoardTokenStopsBeingAWayIn:
    def test_a_tunnel_request_with_the_right_token_and_no_identity_is_refused(self, board_with_access):
        """The single most important assertion in this file.

        In the quick tier this exact request is served — the token *is* the
        boundary. Here the token is not consulted at all, which is what makes a
        leaked link stop being a way in.
        """
        server, _, _ = board_with_access
        status, _ = _call(server, "/api/cards", host=HOSTNAME, token=server.token)
        assert status == 403

    def test_a_tunnel_request_with_identity_and_no_token_is_served(self, board_with_access):
        server, _, _ = board_with_access
        status, data = _call(server, "/api/cards", host=HOSTNAME, jwt=GOOD)
        assert status == 200
        assert "cards" in data

    def test_a_token_the_verifier_rejects_is_refused(self, board_with_access):
        server, _, _ = board_with_access
        status, _ = _call(server, "/api/cards", host=HOSTNAME, jwt="forged")
        assert status == 403

    def test_an_unknown_host_must_also_verify(self, board_with_access):
        # Fail closed: a request we cannot place is one we do not trust.
        server, _, _ = board_with_access
        assert _call(server, "/api/cards", host="somewhere.else", token=server.token)[0] == 403

    def test_the_host_is_not_locked_out_of_their_own_board(self, board_with_access):
        """cloudflared connects from 127.0.0.1, so the socket cannot tell these
        apart — the ``Host`` header is the only thing that can.

        Requiring a JWT here would mean arming the tier locks the host out of
        the board they are running.
        """
        server, _, _ = board_with_access
        status, _ = _call(server, "/api/cards", host=f"127.0.0.1:{server.port}", token=server.token)
        assert status == 200

    def test_the_hosts_own_request_still_needs_the_token(self, board_with_access):
        server, _, _ = board_with_access
        assert _call(server, "/api/cards", host=f"127.0.0.1:{server.port}")[0] == 403


class TestImpersonationStops:
    """The tier's whole point, on the route where it bites."""

    def _add(self, server, *, jwt, pid, text="hello"):
        return _call(
            server,
            "/api/cards",
            host=HOSTNAME,
            jwt=jwt,
            body={"grid": "went_well", "text": text, "author": "Not Me", "pid": pid},
        )

    def test_a_card_is_owned_by_the_verified_pid_not_the_claimed_one(self, board_with_access):
        server, board, _ = board_with_access
        self._add(server, jwt=GOOD, pid="i-am-bob")
        card = board.state_snapshot("cf:sub-ada")["cards"][0]
        assert card["mine"] is True
        # And it is not owned by what the client asked for.
        assert board.state_snapshot("i-am-bob")["cards"][0]["mine"] is False

    def test_the_byline_is_the_verified_identity(self, board_with_access):
        """A board where the pid is accountable but the *name on the card* is
        freely chosen still cannot answer "who wrote this".
        """
        server, board, _ = board_with_access
        self._add(server, jwt=GOOD, pid="whatever")
        assert board.state_snapshot("cf:sub-ada")["cards"][0]["author"] == "ada"

    def test_another_verified_person_cannot_edit_it(self, board_with_access):
        server, board, _ = board_with_access
        self._add(server, jwt=GOOD, pid="x", text="ada's card")
        card_id = board.state_snapshot("cf:sub-ada")["cards"][0]["id"]

        # Bob is verified — he is allowed on the board — and claims Ada's pid.
        status, _ = _call(
            server,
            "/api/card/edit",
            host=HOSTNAME,
            jwt="bobs-token",
            body={"card_id": card_id, "text": "rewritten", "pid": "cf:sub-ada"},
        )
        assert status == 403
        assert board.state_snapshot("cf:sub-ada")["cards"][0]["text"] == "ada's card"

    def test_another_verified_person_cannot_delete_it(self, board_with_access):
        server, board, _ = board_with_access
        self._add(server, jwt=GOOD, pid="x")
        card_id = board.state_snapshot("cf:sub-ada")["cards"][0]["id"]
        status, _ = _call(
            server,
            "/api/card/delete",
            host=HOSTNAME,
            jwt="bobs-token",
            body={"card_id": card_id, "pid": "cf:sub-ada"},
        )
        assert status == 403
        assert len(board.state_snapshot("cf:sub-ada")["cards"]) == 1

    def test_the_owner_can_still_edit_their_own_card(self, board_with_access):
        # The control must not be so strict that it breaks the feature.
        server, board, _ = board_with_access
        self._add(server, jwt=GOOD, pid="x")
        card_id = board.state_snapshot("cf:sub-ada")["cards"][0]["id"]
        status, _ = _call(
            server,
            "/api/card/edit",
            host=HOSTNAME,
            jwt=GOOD,
            body={"card_id": card_id, "text": "second thoughts", "pid": "anything at all"},
        )
        assert status == 200
        assert board.state_snapshot("cf:sub-ada")["cards"][0]["text"] == "second thoughts"


class TestAdminByEmail:
    """Host powers stop being a bearer secret in a URL."""

    def _lock(self, server, *, jwt, admin=""):
        return _call(
            server,
            "/api/admin/lock",
            host=HOSTNAME,
            jwt=jwt,
            body={"locked": True, "admin": admin, "pid": "p"},
        )

    def test_an_allowlisted_email_needs_no_admin_secret(self, board_with_access):
        server, board, _ = board_with_access
        assert self._lock(server, jwt=GOOD)[0] == 200

    def test_a_verified_non_admin_is_refused(self, board_with_access):
        server, _, _ = board_with_access
        assert self._lock(server, jwt="bobs-token")[0] == 403

    def test_the_admin_secret_is_ignored_outright_over_the_tunnel(self, board_with_access):
        """Not "also checked" — ignored.

        The secret rides in the host link's query string, so it reaches
        Cloudflare's edge access log and stays a static bearer for the life of
        the screen. Continuing to honour it over the tunnel would leave the
        weaker credential in place beside the stronger one.
        """
        server, _, _ = board_with_access
        assert self._lock(server, jwt="bobs-token", admin=server.admin_token)[0] == 403

    def test_the_host_on_loopback_still_uses_the_secret(self, board_with_access):
        server, _, _ = board_with_access
        status, _ = _call(
            server,
            "/api/admin/lock",
            host=f"127.0.0.1:{server.port}",
            token=server.token,
            body={"locked": True, "admin": server.admin_token, "pid": "p"},
        )
        assert status == 200


class TestTheQuickTierIsUnchanged:
    """Nothing above may have leaked into the default path."""

    def test_no_gate_means_the_token_is_still_the_boundary(self):
        board = RetroBoard("s", "P")
        server = RetroServer(board, port=5231)
        server.start()
        try:
            assert server.access_gate is None
            # A "tunnel-borne" Host is irrelevant without a gate.
            assert _call(server, "/api/cards", host=HOSTNAME, token=server.token)[0] == 200
            assert _call(server, "/api/cards", host=HOSTNAME)[0] == 403
        finally:
            server.stop()

    def test_a_client_minted_pid_still_owns_its_own_card(self):
        board = RetroBoard("s", "P")
        server = RetroServer(board, port=5232)
        server.start()
        try:
            _call(
                server,
                "/api/cards",
                host=f"127.0.0.1:{server.port}",
                token=server.token,
                body={"grid": "went_well", "text": "hi", "author": "Sam", "pid": "browser-uuid"},
            )
            snapshot = board.state_snapshot("browser-uuid")
            assert snapshot["cards"][0]["mine"] is True
            assert snapshot["cards"][0]["author"] == "Sam"
        finally:
            server.stop()


class TestNoRouteTakesTheClientsWordForIt:
    """A source scan, because the guarantee is "every route", not "these routes".

    The three servers read a participant's ``pid`` / ``author`` / ``name`` out of
    the POST body in ten places. Every one must either go through
    ``enforce_identity`` or use the name it returned — a new route that reads the
    body directly would silently reintroduce impersonation on that route alone,
    and no behavioural test would notice until someone thought to write one.
    """

    CLAIMS = ('payload.get("pid"', 'payload.get("author"', 'payload.get("name"')
    FILES = (
        "src/yeaboi/retro/server.py",
        "src/yeaboi/poker/server.py",
        "src/yeaboi/sharing/server.py",
    )

    def test_every_identity_read_is_server_enforced(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        unguarded = []
        for name in self.FILES:
            for number, line in enumerate((root / name).read_text().splitlines(), 1):
                if not any(claim in line for claim in self.CLAIMS):
                    continue
                if "enforce_identity" in line or "verified_name" in line:
                    continue
                # The one shape that is allowed: assigning the raw claim to a
                # local that the very next lines hand to enforce_identity. Both
                # boards do this so the admin string can be read alongside it.
                unguarded.append(f"{name}:{number}: {line.strip()}")
        # Two known lines: retro and poker each bind `pid` before the
        # enforce_identity call two lines below. Anything else is a new route
        # that forgot.
        assert len(unguarded) == 2, "identity read without server enforcement:\n" + "\n".join(unguarded)
        for entry in unguarded:
            assert entry.endswith('pid = str(payload.get("pid", ""))'), entry

    def test_the_two_allowed_reads_are_enforced_within_three_lines(self):
        """The exemption above is only safe while the enforcement is right there."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2]
        for name in ("src/yeaboi/retro/server.py", "src/yeaboi/poker/server.py"):
            lines = (root / name).read_text().splitlines()
            index = next(i for i, line in enumerate(lines) if line.strip() == 'pid = str(payload.get("pid", ""))')
            window = "\n".join(lines[index : index + 12])
            assert "enforce_identity" in window, f"{name}: pid is read but never enforced nearby"


class TestTheJoinRouteIsBehindIdentityToo:
    """The one unauthenticated POST, and why it is not an asterisk on the claim.

    ``/api/join`` exchanges the short code for the board token, and by design it
    is the only route that does not require the token. In the Access tier that
    would leave exactly one tunnel-borne route unverified — harmless in itself
    (the token it returns is useless over the tunnel, where every other route
    wants a JWT), but "every tunnel-borne request is verified" is a claim
    SECURITY.md makes, and it should be true without a footnote.
    """

    def test_an_unverified_visitor_cannot_even_attempt_the_code(self, board_with_access):
        server, _, _ = board_with_access
        status, _ = _call(server, "/api/join", host=HOSTNAME, body={"code": server.join_code})
        assert status == 403

    def test_a_verified_visitor_still_exchanges_the_code(self, board_with_access):
        """The code stays a second factor — it is not bypassed, just gated."""
        server, _, _ = board_with_access
        status, data = _call(server, "/api/join", host=HOSTNAME, jwt=GOOD, body={"code": server.join_code})
        assert status == 200
        assert data["token"] == server.token

    def test_a_verified_visitor_with_the_wrong_code_is_still_refused(self, board_with_access):
        server, _, _ = board_with_access
        status, _ = _call(server, "/api/join", host=HOSTNAME, jwt=GOOD, body={"code": "WRON-GXXX"})
        assert status == 403

    def test_an_unverified_attempt_does_not_spend_the_rate_limit(self, board_with_access):
        """Otherwise a stranger burns a verified visitor's budget before they arrive.

        The limiter is keyed per visitor now (see ``access.client_key``), but a
        refusal that never reaches ``record_failure`` is strictly better than one
        that does.
        """
        server, _, _ = board_with_access
        for _ in range(12):
            _call(server, "/api/join", host=HOSTNAME, body={"code": "WRON-GXXX"})
        status, data = _call(server, "/api/join", host=HOSTNAME, jwt=GOOD, body={"code": server.join_code})
        assert status == 200
        assert data["token"] == server.token

    def test_the_quick_tier_join_is_untouched(self):
        board = RetroBoard("s", "P")
        server = RetroServer(board, port=5233)
        server.start()
        try:
            status, data = _call(server, "/api/join", host=HOSTNAME, body={"code": server.join_code})
            assert status == 200
            assert data["token"] == server.token
        finally:
            server.stop()


class TestTheReadPathsUseTheVerifiedIdentityToo:
    """Ownership has to agree between the write path and the read path.

    POSTs store cards under the verified pid, but the browser keeps sending its
    own generated id on every long poll. Reading state as the claimed pid made
    `mine` false on a participant's own cards, taking their edit and delete
    controls away — a bug only the Access tier could produce.
    """

    def test_state_is_read_as_the_verified_participant(self, board_with_access):
        server, board, _ = board_with_access
        _call(
            server,
            "/api/cards",
            host=HOSTNAME,
            jwt=GOOD,
            body={"grid": "went_well", "text": "mine", "author": "Not Me", "pid": "someone-else"},
        )
        # The browser keeps polling with its own generated id, not the verified one.
        _status, state = _call(server, "/api/state?pid=someone-else", host=HOSTNAME, jwt=GOOD)
        cards = state.get("cards", [])
        assert cards, "the card should be visible"
        assert all(c.get("mine") for c in cards), "a verified author must own their own card on the read path"

    def test_no_route_reads_a_client_supplied_pid_directly(self):
        """Two-way with the write-path guard above it."""
        from pathlib import Path

        for path in (
            "src/yeaboi/retro/server.py",
            "src/yeaboi/poker/server.py",
            "src/yeaboi/sharing/server.py",
            "src/yeaboi/ship/server.py",
        ):
            body = Path(path).read_text()
            assert '_query("pid")' not in body.replace('effective_pid(self, self._query("pid"))', ""), (
                f"{path} reads a client-supplied pid without effective_pid()"
            )
