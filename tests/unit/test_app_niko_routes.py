"""The /api/niko routes — socketless, over AppServer.handle().

The engine's own behaviour lives in test_niko_engine.py; here the subject is
the wire: the conversation view, the NDJSON turn (its line order, its op id,
its terminators), and the per-conversation lock that keeps two windows off one
thread.
"""

from __future__ import annotations

import json
import threading

import pytest

from yeaboi.agent.state import NikoAnswer, NikoToolCall
from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer
from yeaboi.niko import engine
from yeaboi.niko.store import NikoStore

TOKEN = "test-token"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: path)
    return path


@pytest.fixture
def app(db):
    return AppServer(token=TOKEN)


@pytest.fixture
def scripted(monkeypatch):
    """Replace the engine with one that replays events, and record its kwargs."""
    seen: dict = {}

    def install(events, answer=None, *, block: threading.Event | None = None, boom: Exception | None = None):
        def fake_ask(question, **kwargs):
            seen.update(kwargs, question=question)
            for event in events:
                kwargs["on_event"](event)
            if block is not None:
                block.wait(timeout=5)
            if boom is not None:
                raise boom
            return answer or NikoAnswer(conversation_id=kwargs.get("conversation_id", ""), text="ok")

        monkeypatch.setattr(engine, "ask", fake_ask)
        return seen

    return install


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    body = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, headers, body))


def open_conversation(app) -> str:
    resp = request(app, "POST", "/api/niko/conversations")
    assert resp.code == 201, resp.body
    return json.loads(resp.body)["id"]


def turn(app, conversation_id, question="what did my agents cost?", **payload):
    resp = request(app, "POST", f"/api/niko/conversations/{conversation_id}/send", {"question": question, **payload})
    assert resp.code == 200, resp.body
    assert resp.content_type == "application/x-ndjson"
    return [json.loads(line) for line in b"".join(resp.stream).decode().splitlines()]


class TestAuth:
    def test_every_niko_route_needs_the_token(self, app):
        for method, path in [
            ("POST", "/api/niko/conversations"),
            ("GET", "/api/niko/conversations"),
            ("GET", "/api/niko/conversations/x"),
            ("POST", "/api/niko/conversations/x/send"),
            ("POST", "/api/niko/conversations/x/delete"),
            ("GET", "/api/niko/suggestions"),
        ]:
            assert request(app, method, path, {} if method == "POST" else None, authed=False).code == 401


class TestConversations:
    def test_create_returns_an_empty_view(self, app):
        body = json.loads(request(app, "POST", "/api/niko/conversations").body)
        assert body["messages"] == []
        assert body["id"] and body["created_at"]

    def test_get_replays_the_thread_with_its_tool_calls(self, app, db):
        conversation_id = open_conversation(app)
        with NikoStore(db) as store:
            store.add_message(conversation_id, role="user", content="hi", route="/home")
            store.add_message(
                conversation_id,
                role="assistant",
                content="hey",
                tool_calls=(NikoToolCall(name="llm_usage", ok=True, result={"a": 1}),),
            )
        body = json.loads(request(app, "GET", f"/api/niko/conversations/{conversation_id}").body)
        assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
        assert body["messages"][0]["route"] == "/home"
        assert body["messages"][1]["tool_calls"] == [{"tool_name": "llm_usage", "ok": True, "error": ""}]

    def test_get_unknown_is_404(self, app):
        assert request(app, "GET", "/api/niko/conversations/nope").code == 404

    def test_list_is_newest_used_first(self, app, db):
        first, second = open_conversation(app), open_conversation(app)
        with NikoStore(db) as store:
            store.add_message(first, role="user", content="later")
        rows = json.loads(request(app, "GET", "/api/niko/conversations").body)["conversations"]
        assert [r["id"] for r in rows] == [first, second]

    def test_delete_archives_rather_than_purges(self, app, db):
        conversation_id = open_conversation(app)
        assert json.loads(request(app, "POST", f"/api/niko/conversations/{conversation_id}/delete").body)["archived"]
        assert json.loads(request(app, "GET", "/api/niko/conversations").body)["conversations"] == []
        # Archived, not gone: the record of what the user was told survives.
        assert request(app, "GET", f"/api/niko/conversations/{conversation_id}").code == 200

    def test_delete_unknown_is_404(self, app):
        assert request(app, "POST", "/api/niko/conversations/nope/delete").code == 404


class TestSuggestions:
    def test_a_screen_gets_its_own_chips(self, app):
        body = json.loads(request(app, "GET", "/api/niko/suggestions?route=/agents/usage").body)
        assert body["route"] == "/agents/usage"
        assert any("agents cost" in c["label"].lower() for c in body["suggestions"])

    def test_no_route_still_answers(self, app):
        assert json.loads(request(app, "GET", "/api/niko/suggestions").body)["suggestions"]


class TestTheTurn:
    def test_line_order_op_first_then_done(self, app, scripted):
        scripted([engine.Token("$4"), engine.Token(".50"), engine.Assistant("$4.50.")])
        conversation_id = open_conversation(app)
        lines = turn(app, conversation_id)
        assert [line["type"] for line in lines] == ["op", "token", "token", "assistant", "done"]
        assert lines[0]["op_id"]

    def test_tool_lines_carry_their_outcome(self, app, scripted):
        scripted(
            [
                engine.ToolStarted("ship_status", {}),
                engine.ToolFinished(NikoToolCall(name="ship_status", ok=False, error="no runs yet")),
                engine.Assistant("Nothing yet."),
            ]
        )
        lines = turn(app, open_conversation(app))
        call, result = lines[1], lines[2]
        assert call == {"type": "tool_call", "tool_name": "ship_status", "tool_input": {}}
        assert result == {"type": "tool_result", "tool_name": "ship_status", "ok": False, "error": "no runs yet"}

    def test_navigate_reaches_the_wire(self, app, scripted):
        scripted([engine.Navigate("/humans/retro"), engine.Assistant("There.")])
        lines = turn(app, open_conversation(app))
        assert {"type": "navigate", "route": "/humans/retro"} in lines

    def test_the_engines_own_done_is_not_a_second_terminator(self, app, scripted):
        scripted([engine.Assistant("ok"), engine.Done("c1")])
        lines = turn(app, open_conversation(app))
        assert [line["type"] for line in lines].count("done") == 1

    def test_done_carries_the_route_and_warnings(self, app, scripted):
        conversation_id = open_conversation(app)
        scripted(
            [engine.Assistant("ok")],
            NikoAnswer(conversation_id=conversation_id, text="ok", route="/usage", warnings=("partial",)),
        )
        done = turn(app, conversation_id)[-1]
        assert done == {
            "type": "done",
            "conversation_id": conversation_id,
            "route": "/usage",
            "warnings": ["partial"],
        }

    def test_the_context_reaches_the_engine(self, app, scripted):
        seen = scripted([engine.Assistant("ok")])
        conversation_id = open_conversation(app)
        turn(app, conversation_id, "where?", route="/agents/usage", user_name="Omar")
        assert seen["question"] == "where?"
        assert seen["route"] == "/agents/usage"
        assert seen["user_name"] == "Omar"
        assert seen["surface"] == "desktop"
        assert seen["conversation_id"] == conversation_id

    def test_an_empty_question_is_400(self, app):
        conversation_id = open_conversation(app)
        assert request(app, "POST", f"/api/niko/conversations/{conversation_id}/send", {"question": "  "}).code == 400

    def test_an_unknown_conversation_is_404(self, app):
        assert request(app, "POST", "/api/niko/conversations/nope/send", {"question": "hi"}).code == 404

    def test_a_failed_turn_becomes_an_error_line_not_a_500(self, app, scripted):
        scripted([], boom=RuntimeError("connection reset"))
        lines = turn(app, open_conversation(app))
        assert lines[-1]["type"] == "error"
        assert lines[-1]["message"]

    def test_an_unknown_event_type_is_the_backends_bug(self, app):
        from yeaboi.app.routes_niko import _wire

        with pytest.raises(TypeError):
            _wire(object())


class TestTurnLock:
    def test_a_second_turn_on_the_same_thread_is_409(self, app, scripted):
        gate = threading.Event()
        scripted([engine.Assistant("ok")], block=gate)
        conversation_id = open_conversation(app)
        first = request(app, "POST", f"/api/niko/conversations/{conversation_id}/send", {"question": "a"})
        stream = iter(first.stream)
        next(stream)  # start the generator, which takes the lock's worker
        try:
            second = request(app, "POST", f"/api/niko/conversations/{conversation_id}/send", {"question": "b"})
            assert second.code == 409
        finally:
            gate.set()
            list(stream)

    def test_the_lock_is_released_when_the_stream_finishes(self, app, scripted):
        scripted([engine.Assistant("ok")])
        conversation_id = open_conversation(app)
        turn(app, conversation_id, "a")
        turn(app, conversation_id, "b")  # would 409 if the first never released

    def test_a_failed_turn_still_releases_the_lock(self, app, scripted):
        scripted([], boom=RuntimeError("boom"))
        conversation_id = open_conversation(app)
        assert turn(app, conversation_id, "a")[-1]["type"] == "error"
        scripted([engine.Assistant("ok")])
        # Would 409 if the failing turn's `finally` had not released it.
        assert turn(app, conversation_id, "b")[-1]["type"] == "done"

    def test_two_conversations_do_not_block_each_other(self, app, scripted):
        gate = threading.Event()
        scripted([engine.Assistant("ok")], block=gate)
        first, second = open_conversation(app), open_conversation(app)
        held = request(app, "POST", f"/api/niko/conversations/{first}/send", {"question": "a"})
        stream = iter(held.stream)
        next(stream)
        try:
            assert request(app, "POST", f"/api/niko/conversations/{second}/send", {"question": "b"}).code == 200
        finally:
            gate.set()
            list(stream)


class TestOps:
    def test_the_op_is_removed_when_the_turn_ends(self, app, scripted):
        scripted([engine.Assistant("ok")])
        lines = turn(app, open_conversation(app))
        assert app.ops.get(lines[0]["op_id"]) is None
