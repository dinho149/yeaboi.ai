"""Tests for the Poker LAN server (poker/server.py) — auth, routes, write-backs.

Mirrors test_retro_server.py: a real ThreadingHTTPServer on localhost, driven
with urllib. Tracker write-backs are monkeypatched at the poker.tickets seam so
no network is touched.
"""

import json
import time
import urllib.error
import urllib.request

import pytest

from yeaboi.poker.board import PokerBoard
from yeaboi.poker.server import JoinLimiter, PokerServer
from yeaboi.web.security import DOCUMENT_HEADERS


def _tickets(n: int = 3) -> list[dict]:
    return [
        {
            "source": "demo",
            "key": f"T-{i}",
            "summary": f"Ticket {i}",
            "description": f"Desc {i}",
            "description_text": f"Desc {i}",
            "story_points": None,
            "state": "To Do",
            "assignee": "",
            "url": "",
        }
        for i in range(n)
    ]


@pytest.fixture
def running_server():
    b = PokerBoard("s", "Proj", source="demo", scope_label="Backlog", tickets=_tickets())
    srv = PokerServer(b, port=5310)
    srv.start()
    try:
        yield srv, b
    finally:
        srv.stop()


def _get(url):
    return urllib.request.urlopen(url, timeout=5)


def _post(srv, path, body, *, token=None, raw=False):
    tok = srv.token if token is None else token
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.port}{path}?token={tok}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=5)
    return resp if raw else json.load(resp)


def _admin_post(srv, path, body):
    return _post(srv, path, {**body, "admin": srv.admin_token})


class TestServerRouting:
    def test_get_root_serves_html_token_free(self, running_server):
        srv, _ = running_server
        html = _get(f"http://127.0.0.1:{srv.port}/").read().decode()
        assert "<title>Planning Poker</title>" in html
        assert srv.token not in html
        assert srv.admin_token not in html

    def test_state_requires_token(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/api/state")
        assert exc.value.code == 403

    def test_state_shape(self, running_server):
        srv, _ = running_server
        data = json.load(_get(f"http://127.0.0.1:{srv.port}/api/state?token={srv.token}"))
        assert {
            "revision",
            "phase",
            "ticket_index",
            "ticket_count",
            "ticket",
            "tickets_meta",
            "votes",
            "mine_value",
            "distribution",
            "median",
            "suggestion",
            "ai",
            "duel",
            "progress",
            "presence",
            "timer",
            "broadcast",
            "locked",
            "notice",
        } == set(data)
        assert data["ticket_count"] == 3

    def test_invite_is_token_gated(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/api/invite")
        assert exc.value.code == 403

    def test_invite_returns_the_code_and_a_token_free_url(self, running_server):
        srv, _ = running_server
        body = json.load(_get(f"http://127.0.0.1:{srv.port}/api/invite?token={srv.token}"))
        assert body["joinCode"] == srv.join_code
        assert "token=" not in body["shareUrl"]

    def test_invite_never_carries_the_admin_secret(self, running_server):
        # Every participant can read this response; the host link it would come
        # from grants reveal, save and edit.
        srv, _ = running_server
        raw = _get(f"http://127.0.0.1:{srv.port}/api/invite?token={srv.token}").read().decode()
        assert srv.admin_token not in raw
        assert srv.token not in raw

    def test_invite_url_follows_the_host_header(self, running_server):
        srv, _ = running_server
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/invite?token={srv.token}",
            headers={"Host": "abc-def.trycloudflare.com", "X-Forwarded-Proto": "https"},
        )
        assert json.load(urllib.request.urlopen(req, timeout=5))["shareUrl"] == "https://abc-def.trycloudflare.com/"

    def test_invite_prefers_the_public_url_over_the_requesters_host(self, running_server):
        # The host reaches a loopback-bound board at 127.0.0.1; the invite must
        # not hand that back. See the retro suite for the full note.
        srv, _ = running_server
        srv.set_public_url("https://abc-def.trycloudflare.com/")
        body = json.load(_get(f"http://127.0.0.1:{srv.port}/api/invite?token={srv.token}"))
        assert body["shareUrl"] == "https://abc-def.trycloudflare.com/"

    def test_unknown_path_404(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/nope")
        assert exc.value.code == 404

    def test_unknown_post_path_forbidden(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/hack", {})
        assert exc.value.code == 403

    def test_body_cap(self, running_server):
        srv, _ = running_server
        req = urllib.request.Request(
            f"http://127.0.0.1:{srv.port}/api/vote?token={srv.token}",
            data=b"x" * 9000,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 413


class TestTicketPeek:
    @pytest.mark.parametrize("query", ["i=1", "token=wrong&i=1"])
    def test_requires_token(self, running_server, query):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/api/ticket?{query}")
        assert exc.value.code == 403

    @pytest.mark.parametrize("i", ["99", "-1", "abc", ""])
    def test_bad_index_404(self, running_server, i):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{srv.port}/api/ticket?token={srv.token}&i={i}")
        assert exc.value.code == 404

    def test_returns_sanitized_ticket(self, running_server):
        srv, board = running_server
        board.cast_vote("p1", "5")  # round internals must never leak into the view
        data = json.load(_get(f"http://127.0.0.1:{srv.port}/api/ticket?token={srv.token}&i=1"))
        assert data["key"] == "T-1"
        assert data["summary"] == "Ticket 1"
        assert data["description_text"] == "Desc 1"
        assert set(data) == {
            "index",
            "rev",
            "key",
            "summary",
            "description_text",
            "acceptance_text",
            "type",
            "story_points",
            "state",
            "assignee",
            "url",
            "estimated",
            "final_points",
        }

    def test_meta_rev_in_state(self, running_server):
        srv, _ = running_server
        data = json.load(_get(f"http://127.0.0.1:{srv.port}/api/state?token={srv.token}"))
        assert all(m["rev"] == 0 for m in data["tickets_meta"])


class TestJoinEndpoint:
    def test_correct_code_returns_token(self, running_server):
        srv, _ = running_server
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
        assert _attempt("WRONG-COD") == 429
        assert _attempt(srv.join_code) == 429


class TestVoting:
    def test_vote_and_masked_state(self, running_server):
        srv, _ = running_server
        resp = _post(srv, "/api/vote", {"pid": "p1", "value": "5"})
        assert resp["ok"]
        assert resp["state"]["mine_value"] == "5"
        # Another viewer must not see the value pre-reveal.
        other = json.load(_get(f"http://127.0.0.1:{srv.port}/api/state?token={srv.token}&pid=p2"))
        assert other["mine_value"] == ""
        assert "5" not in json.dumps(other["votes"])

    def test_invalid_vote_rejected(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/vote", {"pid": "p1", "value": "4"})
        assert exc.value.code == 400

    def test_clear_vote(self, running_server):
        srv, _ = running_server
        _post(srv, "/api/vote", {"pid": "p1", "value": "5"})
        resp = _post(srv, "/api/vote/clear", {"pid": "p1"})
        assert resp["ok"] and resp["state"]["mine_value"] == ""

    def test_presence_heartbeat_returns_state(self, running_server):
        srv, _ = running_server
        resp = _post(srv, "/api/presence", {"pid": "p1", "name": "Alex", "avatar": "🦊"})
        assert resp["presence"] == [{"name": "Alex", "avatar": "🦊"}]


class TestAdminGate:
    @pytest.mark.parametrize(
        "path,body",
        [
            ("/api/admin/reveal", {}),
            ("/api/admin/revote", {}),
            ("/api/admin/goto", {"index": 1}),
            ("/api/admin/finalize", {"points": 5}),
            ("/api/admin/ticket/edit", {"key": "T-0", "points": 5}),
            ("/api/admin/ai", {}),
            ("/api/admin/duel/open", {"seconds": 60}),
            ("/api/admin/duel/next", {}),
            ("/api/admin/duel/close", {}),
            ("/api/admin/broadcast", {"theme": "light"}),
            ("/api/admin/lock", {"locked": True}),
            ("/api/timer", {"action": "start", "duration": 60}),
        ],
    )
    def test_admin_routes_reject_non_admin(self, running_server, path, body):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, path, {**body, "admin": "not-the-secret"})
        assert exc.value.code == 403

    def test_reveal_with_admin_secret(self, running_server):
        srv, _ = running_server
        _post(srv, "/api/vote", {"pid": "p1", "value": "5"})
        resp = _admin_post(srv, "/api/admin/reveal", {})
        assert resp["ok"] and resp["state"]["phase"] == "revealed"
        assert resp["state"]["distribution"] == {"5": 1}

    def test_revote_and_goto(self, running_server):
        srv, _ = running_server
        _post(srv, "/api/vote", {"pid": "p1", "value": "5"})
        _admin_post(srv, "/api/admin/reveal", {})
        resp = _admin_post(srv, "/api/admin/revote", {})
        assert resp["state"]["phase"] == "voting"
        resp = _admin_post(srv, "/api/admin/goto", {"index": 2})
        assert resp["state"]["ticket_index"] == 2


class TestFinalize:
    def test_success_advances(self, running_server, monkeypatch):
        srv, b = running_server
        calls = {}

        def _update(source, ticket, **kwargs):
            calls["source"] = source
            calls["key"] = ticket.get("key")
            calls.update(kwargs)
            return True, ""

        monkeypatch.setattr("yeaboi.poker.tickets.update_ticket", _update)
        _post(srv, "/api/vote", {"pid": "p1", "value": "8"})
        _admin_post(srv, "/api/admin/reveal", {})
        resp = _admin_post(srv, "/api/admin/finalize", {"points": 8})
        assert resp["ok"]
        assert calls == {"source": "demo", "key": "T-0", "story_points": 8.0}
        assert resp["state"]["ticket_index"] == 1
        assert b.tickets_snapshot()[0]["final_points"] == 8.0

    def test_tracker_failure_does_not_advance(self, running_server, monkeypatch):
        srv, b = running_server
        monkeypatch.setattr("yeaboi.poker.tickets.update_ticket", lambda *a, **k: (False, "Error: Jira said no"))
        _post(srv, "/api/vote", {"pid": "p1", "value": "8"})
        _admin_post(srv, "/api/admin/reveal", {})
        resp = _admin_post(srv, "/api/admin/finalize", {"points": 8})
        assert resp["ok"] is False
        assert "Jira said no" in resp["error"]
        assert resp["state"]["ticket_index"] == 0  # no advance
        assert resp["state"]["phase"] == "revealed"  # still on the reveal
        assert resp["state"]["notice"] == "Error: Jira said no"
        assert b.tickets_snapshot()[0]["estimated"] is False

    def test_bad_points_rejected(self, running_server):
        srv, _ = running_server
        _post(srv, "/api/vote", {"pid": "p1", "value": "8"})
        _admin_post(srv, "/api/admin/reveal", {})
        with pytest.raises(urllib.error.HTTPError) as exc:
            _admin_post(srv, "/api/admin/finalize", {"points": "junk"})
        assert exc.value.code == 400

    def test_finalize_before_reveal_never_touches_tracker(self, running_server, monkeypatch):
        # The phase pre-check must run BEFORE the tracker write — otherwise a
        # stale/raw finalize pushes points to the real board and the poker
        # board then rejects them (tracker and board out of sync).
        srv, _ = running_server
        monkeypatch.setattr(
            "yeaboi.poker.tickets.update_ticket",
            lambda *a, **k: pytest.fail("tracker write must not happen before the reveal"),
        )
        _post(srv, "/api/vote", {"pid": "p1", "value": "8"})  # still voting
        with pytest.raises(urllib.error.HTTPError) as exc:
            _admin_post(srv, "/api/admin/finalize", {"points": 8})
        assert exc.value.code == 400


class TestTicketEdit:
    def test_edit_pushes_then_mirrors(self, running_server, monkeypatch):
        srv, b = running_server
        calls = {}

        def _update(source, ticket, **kwargs):
            calls.update(kwargs)
            return True, ""

        monkeypatch.setattr("yeaboi.poker.tickets.update_ticket", _update)
        resp = _admin_post(
            srv,
            "/api/admin/ticket/edit",
            {"key": "T-1", "summary": "New title", "description": "New body", "points": 3},
        )
        assert resp["ok"]
        assert calls == {"summary": "New title", "description": "New body", "story_points": 3.0}
        t = b.tickets_snapshot()[1]
        assert t["summary"] == "New title"
        assert t["description_text"] == "New body"
        assert t["story_points"] == 3.0

    def test_edit_failure_leaves_board_untouched(self, running_server, monkeypatch):
        srv, b = running_server
        monkeypatch.setattr("yeaboi.poker.tickets.update_ticket", lambda *a, **k: (False, "Error: denied"))
        resp = _admin_post(srv, "/api/admin/ticket/edit", {"key": "T-1", "summary": "New title"})
        assert resp["ok"] is False
        assert b.tickets_snapshot()[1]["summary"] == "Ticket 1"

    def test_edit_unknown_ticket(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _admin_post(srv, "/api/admin/ticket/edit", {"key": "NOPE", "summary": "x"})
        assert exc.value.code == 400

    def test_edit_nothing_to_update(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _admin_post(srv, "/api/admin/ticket/edit", {"key": "T-0"})
        assert exc.value.code == 400


class TestAiEndpoint:
    def test_requires_reveal(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _admin_post(srv, "/api/admin/ai", {})
        assert exc.value.code == 400

    def test_spawns_worker_and_note_lands(self, running_server, monkeypatch):
        srv, b = running_server
        seen = {}

        def _fake(ticket, votes, **kwargs):
            seen.update(kwargs)
            return {
                "note": "Split the ticket.",
                "suggested_points": 5.0,
                "confidence": "high",
                "evidence": ["5-pt stories avg 4.2 days"],
                "llm_mode": "llm",
                "warnings": [],
            }

        monkeypatch.setattr("yeaboi.poker.engine.get_poker_perspective", _fake)
        _post(srv, "/api/vote", {"pid": "p1", "value": "5"})
        _admin_post(srv, "/api/admin/reveal", {})
        resp = _admin_post(srv, "/api/admin/ai", {})
        assert resp["ok"] and resp["pending"]
        # The worker thread lands the note; poll briefly.
        for _ in range(50):
            ai = b.state_snapshot()["ai"]
            if ai["note"]:
                break
            time.sleep(0.05)
        assert ai == {
            "pending": False,
            "note": "Split the ticket.",
            "suggested": 5.0,
            "confidence": "high",
            "evidence": ["5-pt stories avg 4.2 days"],
        }
        # The worker scopes the cross-mode history gather to this session's project.
        assert seen["project_name"] == b.project_name

    def test_double_click_guard(self, running_server, monkeypatch):
        srv, b = running_server

        def _slow(ticket, votes, **kwargs):
            time.sleep(0.3)
            return {"note": "ok", "suggested_points": None, "llm_mode": "llm", "warnings": []}

        monkeypatch.setattr("yeaboi.poker.engine.get_poker_perspective", _slow)
        _post(srv, "/api/vote", {"pid": "p1", "value": "5"})
        _admin_post(srv, "/api/admin/reveal", {})
        first = _admin_post(srv, "/api/admin/ai", {})
        second = _admin_post(srv, "/api/admin/ai", {})  # while in flight
        assert first["ok"] and second["ok"] and second["pending"]
        for _ in range(50):
            if b.state_snapshot()["ai"]["note"]:
                break
            time.sleep(0.05)
        assert b.state_snapshot()["ai"]["note"] == "ok"


def _split_reveal(srv):
    """Two present voters with a 2-vs-8 spread, revealed — duel-ready."""
    _post(srv, "/api/presence", {"pid": "p1", "name": "Alex", "avatar": "🦊"})
    _post(srv, "/api/presence", {"pid": "p2", "name": "Sam", "avatar": "🐙"})
    _post(srv, "/api/vote", {"pid": "p1", "value": "2"})
    _post(srv, "/api/vote", {"pid": "p2", "value": "8"})
    _admin_post(srv, "/api/admin/reveal", {})


def _upload_audio(srv, pid, data, *, turn=1):
    req = urllib.request.Request(
        f"http://127.0.0.1:{srv.port}/api/duel/audio?token={srv.token}&pid={pid}&turn={turn}",
        data=data,
        headers={"Content-Type": "audio/webm"},
        method="POST",
    )
    return json.load(urllib.request.urlopen(req, timeout=5))


def _wait_for(fn, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    return fn()


class TestDuelEndpoints:
    def test_open_requires_reveal(self, running_server):
        srv, _ = running_server
        with pytest.raises(urllib.error.HTTPError) as exc:
            _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        assert exc.value.code == 400

    def test_open_requires_disagreement(self, running_server):
        srv, _ = running_server
        _post(srv, "/api/vote", {"pid": "p1", "value": "5"})
        _post(srv, "/api/vote", {"pid": "p2", "value": "5"})
        _admin_post(srv, "/api/admin/reveal", {})
        with pytest.raises(urllib.error.HTTPError) as exc:
            _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        assert exc.value.code == 400

    def test_open_shape_and_missing_mic_notice(self, running_server):
        # The test env has no voice extra — the host mic is a notice, never an error.
        srv, _ = running_server
        _split_reveal(srv)
        resp = _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        assert resp["ok"]
        state = resp["state"]
        assert state["phase"] == "duel"
        duel = state["duel"]
        assert duel["status"] == "live"
        assert duel["low"]["name"] == "Alex" and duel["high"]["name"] == "Sam"
        assert duel["recording"] == {"host": False, "low": False, "high": False}
        assert state["notice"].startswith("Host mic unavailable")
        assert state["timer"]["running"] is True

    def test_mic_flag_for_duelists_only(self, running_server):
        srv, b = running_server
        _split_reveal(srv)
        _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        resp = _post(srv, "/api/duel/mic", {"pid": "p1", "on": True})
        assert resp["ok"]
        assert b.state_snapshot()["duel"]["recording"]["low"] is True
        with pytest.raises(urllib.error.HTTPError) as exc:
            _post(srv, "/api/duel/mic", {"pid": "p3", "on": True})
        assert exc.value.code == 400

    def test_next_turn(self, running_server):
        srv, _ = running_server
        _split_reveal(srv)
        _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        resp = _admin_post(srv, "/api/admin/duel/next", {})
        assert resp["ok"] and resp["state"]["duel"]["turn"] == "high"

    def test_audio_upload_requires_duelist_pid(self, running_server):
        srv, _ = running_server
        _split_reveal(srv)
        _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        with pytest.raises(urllib.error.HTTPError) as exc:
            _upload_audio(srv, "p3", b"fake-audio")
        assert exc.value.code == 403

    def test_audio_body_cap(self, running_server, monkeypatch):
        srv, _ = running_server
        monkeypatch.setattr("yeaboi.poker.server._MAX_AUDIO_BODY", 64)
        _split_reveal(srv)
        _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        with pytest.raises(urllib.error.HTTPError) as exc:
            _upload_audio(srv, "p1", b"x" * 200)
        assert exc.value.code == 413

    def test_audio_after_grace_rejected(self, running_server, monkeypatch):
        srv, b = running_server
        monkeypatch.setattr("yeaboi.poker.server._DUEL_UPLOAD_GRACE", 0.0)
        _split_reveal(srv)
        _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        _admin_post(srv, "/api/admin/duel/close", {})
        with pytest.raises(urllib.error.HTTPError) as exc:
            _upload_audio(srv, "p1", b"fake-audio")
        assert exc.value.code == 409

    def test_close_with_no_audio_reports_failure(self, running_server, monkeypatch):
        srv, b = running_server
        monkeypatch.setattr("yeaboi.poker.server._DUEL_UPLOAD_GRACE", 0.05)
        _split_reveal(srv)
        _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        resp = _admin_post(srv, "/api/admin/duel/close", {})
        assert resp["ok"] and resp["state"]["phase"] == "revealed"
        assert resp["state"]["duel"]["status"] == "transcribing"
        duel = _wait_for(lambda: (d := b.state_snapshot()["duel"]) and d["status"] != "transcribing" and d)
        assert duel["status"] == "failed"
        assert "Transcription produced nothing" in duel["error"]

    def test_stt_transcribes_segments_and_auto_ai(self, running_server, monkeypatch):
        srv, b = running_server
        monkeypatch.setattr("yeaboi.poker.server._DUEL_UPLOAD_GRACE", 0.05)
        monkeypatch.setattr("yeaboi.voice.transcribe_media", lambda data: "it is just a config change")
        seen = {}

        def _fake_ai(ticket, votes, **kwargs):
            seen.update(kwargs)
            return {
                "note": "Alex's argument holds.",
                "suggested_points": 3.0,
                "confidence": "medium",
                "evidence": [],
                "llm_mode": "llm",
                "warnings": [],
            }

        monkeypatch.setattr("yeaboi.poker.engine.get_poker_perspective", _fake_ai)
        _split_reveal(srv)
        _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        assert _upload_audio(srv, "p1", b"fake-webm", turn=1)["ok"]
        _admin_post(srv, "/api/admin/duel/close", {})
        duel = _wait_for(lambda: (d := b.state_snapshot()["duel"]) and d["status"] == "done" and d)
        assert duel["transcript"] == "Alex (voted 2) — turn 1:\nit is just a config change"
        # The AI auto-fires once transcription lands, with the debate attached.
        ai = _wait_for(lambda: (a := b.state_snapshot()["ai"]) and a["note"] and a)
        assert ai["note"] == "Alex's argument holds."
        assert seen["debate_transcript"] == duel["transcript"]

    def test_revote_aborts_capture_and_duel(self, running_server):
        srv, b = running_server
        _split_reveal(srv)
        _admin_post(srv, "/api/admin/duel/open", {"seconds": 60})
        assert _upload_audio(srv, "p1", b"fake-webm")["ok"]
        _admin_post(srv, "/api/admin/revote", {})
        assert b.state_snapshot()["duel"] is None
        assert srv.duel_capture.accepting() is False
        assert srv.duel_capture.take_segments() == {}


class TestUrls:
    def test_host_url_carries_admin_secret(self):
        srv = PokerServer(PokerBoard("s"), port=5388)
        assert f"token={srv.token}" in srv.url
        assert f"admin={srv.admin_token}" in srv.url

    def test_host_url_is_loopback_until_the_tunnel_is_up(self):
        srv = PokerServer(PokerBoard("s"), port=5388)
        assert srv.url.startswith("http://127.0.0.1:5388/?token=")
        srv.set_public_url("https://abc-def.trycloudflare.com/")
        assert srv.url.startswith("https://abc-def.trycloudflare.com/?token=")

    def test_share_url_is_the_tunnel_or_nothing(self):
        # No LAN fallback: the board binds loopback, so until the tunnel lands
        # there is no address a teammate could use.
        srv = PokerServer(PokerBoard("s"), port=5388)
        assert srv.share_url == ""
        srv.set_public_url("https://abc-def.trycloudflare.com/")
        assert srv.share_url == "https://abc-def.trycloudflare.com/"
        assert "token" not in srv.share_url

    def test_binds_loopback_only(self):
        srv = PokerServer(PokerBoard("s"), port=5393)
        srv.start()
        try:
            assert srv._httpd.server_address[0] == "127.0.0.1"
        finally:
            srv.stop()

    def test_lifecycle_start_stop(self):
        srv = PokerServer(PokerBoard("s", tickets=_tickets(1)), port=5311)
        srv.start()
        try:
            html = _get(f"http://127.0.0.1:{srv.port}/").read().decode()
            assert "Planning Poker" in html
        finally:
            srv.stop()
        srv.stop()  # idempotent


class TestSecurityHeaders:
    """Same shared header set as the retro board; see web/security.py."""

    def test_document_carries_every_shared_header(self, running_server):
        srv, _ = running_server
        headers = _get(f"http://127.0.0.1:{srv.port}/").headers
        for name, value in DOCUMENT_HEADERS:
            assert headers[name] == value, name

    def test_api_responses_carry_them_too(self, running_server):
        srv, _ = running_server
        headers = _get(f"http://127.0.0.1:{srv.port}/api/state?token={srv.token}").headers
        for name, value in DOCUMENT_HEADERS:
            assert headers[name] == value, name
