"""Wire-level tests for long-polling /api/state, its ETag, and ?quiet=1.

These run real :class:`RetroServer` / :class:`PokerServer` instances on
loopback, because the properties worth pinning are HTTP properties — what a
conditional GET returns, whether a held request really blocks, whether
keep-alive survives a bodyless 304 — and none of that survives being mocked.

Long-polling replaced Server-Sent Events after SSE was measured failing through
a Cloudflare quick tunnel; :mod:`yeaboi.sharing.live` records that experiment.
"""

import http.client
import json
import logging
import threading
import time

import pytest

from yeaboi.poker.board import PokerBoard
from yeaboi.poker.server import PokerServer
from yeaboi.retro.board import RetroBoard
from yeaboi.retro.server import RetroServer
from yeaboi.sharing.events import EventHub
from yeaboi.sharing.live import MAX_WAIT_SECONDS, parse_wait

# Shrunk from 250 ms so a change lands within a test's patience. Patched on the
# module (not passed as an argument) because that is how a real server picks its
# interval up — ChangeWatcher resolves the constant in __init__.
_FAST_WATCH = 0.02


def _get(port: int, path: str, headers: dict | None = None, timeout: float = 10.0) -> tuple[int, dict, bytes]:
    """GET returning ``(status, lowercased_headers, body)`` — urllib raises on 304."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
    finally:
        conn.close()


def _post(port: int, path: str, payload: dict) -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", path, body=json.dumps(payload), headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


@pytest.fixture
def retro(monkeypatch):
    monkeypatch.setattr("yeaboi.sharing.events.WATCH_INTERVAL", _FAST_WATCH)
    board = RetroBoard("s", "Proj")
    srv = RetroServer(board, port=5320)
    srv.start()
    try:
        yield srv, board
    finally:
        srv.stop()


@pytest.fixture
def poker(monkeypatch):
    monkeypatch.setattr("yeaboi.sharing.events.WATCH_INTERVAL", _FAST_WATCH)
    board = PokerBoard("s", tickets=[{"key": "ABC-1", "summary": "Login"}])
    srv = PokerServer(board, port=5340)
    srv.start()
    try:
        yield srv, board
    finally:
        srv.stop()


class TestParseWait:
    def test_absent_means_no_wait(self):
        assert parse_wait("") == 0.0

    def test_parses_seconds(self):
        assert parse_wait("5") == 5.0

    def test_clamps_to_the_ceiling(self):
        # The hold must stay under Cloudflare's ~100 s origin-response limit, so
        # a client cannot ask for an arbitrarily long park.
        assert parse_wait("9999") == MAX_WAIT_SECONDS

    def test_negative_becomes_zero(self):
        assert parse_wait("-5") == 0.0

    def test_junk_becomes_zero(self):
        assert parse_wait("banana") == 0.0


class TestConditionalGet:
    def test_state_carries_an_etag(self, retro):
        srv, _ = retro
        status, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        assert status == 200
        assert headers["etag"].startswith('W/"')

    def test_matching_etag_returns_304_with_no_body(self, retro):
        srv, _ = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        status, headers2, body = _get(
            srv.port, f"/api/state?token={srv.token}&pid=p1", {"If-None-Match": headers["etag"]}
        )
        assert status == 304
        assert body == b""
        assert headers2["etag"] == headers["etag"]

    def test_etag_survives_the_ticking_server_clock(self, retro):
        # Two polls a moment apart carry different timer.now_epoch values but
        # must still collapse to a 304, or the header buys nothing.
        srv, _ = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        time.sleep(0.05)
        status, _, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1", {"If-None-Match": headers["etag"]})
        assert status == 304

    def test_a_change_invalidates_the_etag(self, retro):
        srv, board = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        board.add_card(grid="went_well", text="new", author="Alex", pid="p2")
        status, headers2, body = _get(
            srv.port, f"/api/state?token={srv.token}&pid=p1", {"If-None-Match": headers["etag"]}
        )
        assert status == 200
        assert headers2["etag"] != headers["etag"]
        assert json.loads(body)["cards"][0]["text"] == "new"

    def test_a_stale_etag_returns_the_full_body(self, retro):
        srv, _ = retro
        status, _, body = _get(srv.port, f"/api/state?token={srv.token}&pid=p1", {"If-None-Match": 'W/"deadbeef"'})
        assert status == 200
        assert "revision" in json.loads(body)

    def test_keep_alive_survives_a_304(self, retro):
        # A 304 has no Content-Length; if its framing were wrong the next
        # request on the same connection would hang or desync.
        srv, _ = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
        try:
            conn.request("GET", f"/api/state?token={srv.token}&pid=p1", headers={"If-None-Match": headers["etag"]})
            assert conn.getresponse().read() == b""
            conn.request("GET", f"/api/state?token={srv.token}&pid=p1")
            second = conn.getresponse()
            assert second.status == 200
            assert "revision" in json.loads(second.read())
        finally:
            conn.close()

    def test_viewer_pid_drives_the_mine_flag(self, retro):
        srv, board = retro
        board.add_card(grid="went_well", text="mine", author="Alex", pid="owner")
        _, _, own = _get(srv.port, f"/api/state?token={srv.token}&pid=owner")
        _, _, other = _get(srv.port, f"/api/state?token={srv.token}&pid=other")
        assert json.loads(own)["cards"][0]["mine"] is True
        assert json.loads(other)["cards"][0]["mine"] is False

    def test_no_token_is_forbidden(self, retro):
        srv, _ = retro
        status, _, _ = _get(srv.port, "/api/state?pid=p1")
        assert status == 403


class TestLongPoll:
    def test_a_change_releases_the_held_request(self, retro):
        srv, board = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        result: list[tuple[int, bytes, float]] = []

        def _hold() -> None:
            began = time.monotonic()
            status, _, body = _get(
                srv.port,
                f"/api/state?token={srv.token}&pid=p1&wait=10",
                {"If-None-Match": headers["etag"]},
                timeout=20,
            )
            result.append((status, body, time.monotonic() - began))

        t = threading.Thread(target=_hold, daemon=True)
        t.start()
        time.sleep(0.4)  # let the request park
        assert not result, "the request should still be held"
        board.add_card(grid="went_well", text="released", author="Alex", pid="p2")
        t.join(timeout=10)

        assert result, "the held request never returned"
        status, body, elapsed = result[0]
        assert status == 200
        assert json.loads(body)["cards"][0]["text"] == "released"
        assert elapsed < 5, "should return on the change, not on the deadline"

    def test_a_quiet_board_returns_304_at_the_deadline(self, retro):
        srv, _ = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        began = time.monotonic()
        status, _, body = _get(
            srv.port, f"/api/state?token={srv.token}&pid=p1&wait=1", {"If-None-Match": headers["etag"]}
        )
        elapsed = time.monotonic() - began
        assert status == 304
        assert body == b""
        assert 0.8 < elapsed < 4.0, f"should hold for ~1s, held {elapsed:.2f}s"

    def test_a_behind_client_is_answered_immediately(self, retro):
        # The safety property: a client whose ETag does not match current state
        # must never be parked, or a reconnecting peer waits for a change it has
        # already missed.
        srv, board = retro
        board.add_card(grid="went_well", text="already here", author="Alex", pid="p2")
        began = time.monotonic()
        status, _, body = _get(
            srv.port, f"/api/state?token={srv.token}&pid=p1&wait=20", {"If-None-Match": 'W/"stale"'}, timeout=30
        )
        elapsed = time.monotonic() - began
        assert status == 200
        assert json.loads(body)["cards"][0]["text"] == "already here"
        assert elapsed < 2.0, f"a behind client must not be held; waited {elapsed:.2f}s"

    def test_no_if_none_match_is_answered_immediately(self, retro):
        # A first-contact client has no cursor, so there is nothing to wait for.
        srv, _ = retro
        began = time.monotonic()
        status, _, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1&wait=20", timeout=30)
        assert status == 200
        assert time.monotonic() - began < 2.0

    def test_presence_releases_a_held_request(self, retro):
        # Presence deliberately does not bump revision, which is why the change
        # watcher probes it explicitly. Without that, the who's-here row would
        # only refresh when something else happened to change.
        srv, board = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        result: list[dict] = []

        def _hold() -> None:
            _, _, body = _get(
                srv.port,
                f"/api/state?token={srv.token}&pid=p1&wait=10",
                {"If-None-Match": headers["etag"]},
                timeout=20,
            )
            result.append(json.loads(body))

        t = threading.Thread(target=_hold, daemon=True)
        t.start()
        time.sleep(0.4)
        board.heartbeat("p2", name="Sam", avatar="")
        t.join(timeout=10)

        assert result, "presence did not release the held request"
        assert [p["name"] for p in result[0]["presence"]] == ["Sam"]

    def test_two_held_clients_are_both_released(self, retro):
        srv, board = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        done: list[int] = []

        def _hold() -> None:
            status, _, _ = _get(
                srv.port,
                f"/api/state?token={srv.token}&pid=p1&wait=10",
                {"If-None-Match": headers["etag"]},
                timeout=20,
            )
            done.append(status)

        threads = [threading.Thread(target=_hold, daemon=True) for _ in range(2)]
        for t in threads:
            t.start()
        time.sleep(0.4)
        board.add_card(grid="went_well", text="broadcast", author="Alex", pid="p9")
        for t in threads:
            t.join(timeout=10)
        assert done == [200, 200]

    def test_the_hold_slot_is_released_afterwards(self, retro):
        srv, board = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")

        def _hold() -> None:
            _get(
                srv.port,
                f"/api/state?token={srv.token}&pid=p1&wait=10",
                {"If-None-Match": headers["etag"]},
                timeout=20,
            )

        t = threading.Thread(target=_hold, daemon=True)
        t.start()
        time.sleep(0.4)
        assert srv.event_hub.subscriber_count == 1
        board.add_card(grid="went_well", text="x", author="A", pid="p2")
        t.join(timeout=10)
        assert srv.event_hub.subscriber_count == 0

    def test_over_the_cap_answers_immediately_instead_of_holding(self, retro, monkeypatch):
        # Refusing to hold must degrade to a fast 304, never to an error: the
        # client keeps working, just without the instant wake-up.
        srv, _ = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        monkeypatch.setattr(EventHub, "subscribe", lambda self, ip="": None)
        began = time.monotonic()
        status, _, _ = _get(
            srv.port, f"/api/state?token={srv.token}&pid=p1&wait=20", {"If-None-Match": headers["etag"]}, timeout=30
        )
        assert status == 304
        assert time.monotonic() - began < 2.0

    def test_wait_is_clamped_server_side(self, retro):
        # A client asking for an hour must not get one — the ceiling has to be
        # enforced here, not trusted to the caller.
        srv, _ = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        began = time.monotonic()
        _get(
            srv.port,
            f"/api/state?token={srv.token}&pid=p1&wait=3600",
            {"If-None-Match": headers["etag"]},
            timeout=MAX_WAIT_SECONDS + 15,
        )
        assert time.monotonic() - began <= MAX_WAIT_SECONDS + 5

    def test_stop_releases_a_held_request(self, retro):
        srv, _ = retro
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        done = threading.Event()

        def _hold() -> None:
            try:
                _get(
                    srv.port,
                    f"/api/state?token={srv.token}&pid=p1&wait=20",
                    {"If-None-Match": headers["etag"]},
                    timeout=30,
                )
            except OSError:
                pass  # the socket dies with the server — that counts as released
            finally:
                done.set()

        threading.Thread(target=_hold, daemon=True).start()
        time.sleep(0.4)
        srv.stop()  # the fixture's second stop() is a no-op
        assert done.wait(timeout=5), "shutdown left a request parked"

    def test_the_token_never_reaches_the_log(self, retro, caplog):
        # log_request logs only urlparse(path).path, so the token in the query
        # string never lands in the log file. Pinned because it is a
        # security-relevant property of an otherwise unrelated method.
        srv, _ = retro
        with caplog.at_level(logging.DEBUG, logger="yeaboi.retro.server"):
            _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        assert caplog.text  # the request really was logged
        assert srv.token not in caplog.text


class TestQuietPresence:
    def test_quiet_returns_an_ack_only(self, retro):
        srv, board = retro
        status, body = _post(srv.port, f"/api/presence?token={srv.token}&quiet=1", {"pid": "p1", "name": "Alex"})
        assert status == 200
        assert json.loads(body) == {"ok": True}
        assert [p["name"] for p in board.presence_list()] == ["Alex"]  # still recorded

    def test_without_quiet_the_snapshot_still_comes_back(self, retro):
        srv, _ = retro
        status, body = _post(srv.port, f"/api/presence?token={srv.token}", {"pid": "p1", "name": "Alex"})
        assert status == 200
        assert "revision" in json.loads(body)

    def test_quiet_needs_the_token(self, retro):
        srv, _ = retro
        status, _ = _post(srv.port, "/api/presence?quiet=1", {"pid": "p1", "name": "Alex"})
        assert status == 403


class TestPokerParity:
    def test_state_carries_an_etag_and_304s(self, poker):
        srv, _ = poker
        _, headers, body = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        assert json.loads(body)["ticket"]["key"] == "ABC-1"
        status, _, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1", {"If-None-Match": headers["etag"]})
        assert status == 304

    def test_a_vote_releases_the_held_request(self, poker):
        srv, board = poker
        board.heartbeat("p2", name="Sam")
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        result: list[dict] = []

        def _hold() -> None:
            _, _, body = _get(
                srv.port,
                f"/api/state?token={srv.token}&pid=p1&wait=10",
                {"If-None-Match": headers["etag"]},
                timeout=20,
            )
            result.append(json.loads(body))

        t = threading.Thread(target=_hold, daemon=True)
        t.start()
        time.sleep(0.4)
        board.cast_vote("p2", "5")
        t.join(timeout=10)

        assert result, "the vote did not release the held request"
        assert result[0]["votes"][0]["name"] == "Sam"

    def test_vote_secrecy_holds_on_a_long_poll(self, poker):
        # Responses are built by the same state_snapshot(pid) the plain poll
        # uses, so a waiting client can never see more than a polling one.
        srv, board = poker
        board.heartbeat("p2", name="Sam")
        board.cast_vote("p2", "8")
        _, _, body = _get(srv.port, f"/api/state?token={srv.token}&pid=p1&wait=1")
        payload = json.loads(body)
        assert payload["phase"] == "voting"
        assert [v.get("value") for v in payload["votes"]] == [None]  # "voted", not the value
        assert payload["votes"][0]["voted"] is True

    def test_quiet_presence_returns_an_ack_only(self, poker):
        srv, _ = poker
        status, body = _post(srv.port, f"/api/presence?token={srv.token}&quiet=1", {"pid": "p1", "name": "Alex"})
        assert status == 200
        assert json.loads(body) == {"ok": True}

    def test_no_token_is_forbidden(self, poker):
        srv, _ = poker
        status, _, _ = _get(srv.port, "/api/state?pid=p1")
        assert status == 403

    def test_stop_releases_a_held_request(self, poker):
        srv, _ = poker
        _, headers, _ = _get(srv.port, f"/api/state?token={srv.token}&pid=p1")
        done = threading.Event()

        def _hold() -> None:
            try:
                _get(
                    srv.port,
                    f"/api/state?token={srv.token}&pid=p1&wait=20",
                    {"If-None-Match": headers["etag"]},
                    timeout=30,
                )
            except OSError:
                pass
            finally:
                done.set()

        threading.Thread(target=_hold, daemon=True).start()
        time.sleep(0.4)
        srv.stop()
        assert done.wait(timeout=5), "shutdown left a request parked"


class TestRefusedHoldThrottle:
    """A refused hold slot is throttled server-side, whatever the client does."""

    def test_the_sleep_is_long_enough_to_stop_a_spin(self):
        from yeaboi.sharing.live import REFUSED_HOLD_SLEEP

        assert REFUSED_HOLD_SLEEP >= 1.0

    def test_the_client_threshold_sits_above_the_server_sleep(self):
        """Otherwise the client half of the throttle is dead code: the server
        already delays a refusal past the point the client tests for."""
        import re
        from pathlib import Path

        from yeaboi.sharing.live import REFUSED_HOLD_SLEEP

        src = Path("frontend/src/hooks/useBoardStream.ts").read_text()
        match = re.search(r"const MIN_PARKED_MS = (\d+);", src)
        assert match, "MIN_PARKED_MS not found"
        assert int(match.group(1)) > REFUSED_HOLD_SLEEP * 1000
