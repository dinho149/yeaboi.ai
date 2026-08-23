"""The /api/chat routes — socketless, over AppServer.handle().

The conversation's own behaviour lives in test_chat_session.py; here the
subject is the wire: the session view, the NDJSON turn (its line order, its
op id, its terminators), and the locks that keep two turns off one state.
"""

from __future__ import annotations

import json
import threading

import pytest
from langchain_core.messages import AIMessage

from yeaboi.agent.state import QuestionnaireState
from yeaboi.app.chats import ChatSupervisor
from yeaboi.app.router import parse_request
from yeaboi.app.server import AppServer

TOKEN = "test-token"


class FakeGraph:
    """Answers every turn with one scripted reply, recording the invokes."""

    def __init__(self):
        self.invocations: list[dict] = []
        self.gate = threading.Event()
        self.gate.set()

    def invoke(self, state: dict) -> dict:
        self.invocations.append(state)
        self.gate.wait(timeout=5)
        qs = QuestionnaireState(intake_mode="smart", current_question=2)
        return {**state, "questionnaire": qs, "messages": [*state["messages"], AIMessage(content="How many of you?")]}


@pytest.fixture
def graph():
    return FakeGraph()


@pytest.fixture
def app(graph):
    saved: dict[str, dict] = {}
    chats = ChatSupervisor(
        graph_factory=lambda: graph,
        loader=saved.get,
        saver=saved.__setitem__,
        id_factory=lambda: "proj-1",
    )
    server = AppServer(token=TOKEN, chats=chats)
    server.saved = saved  # the tests assert on what was persisted
    return server


def request(app: AppServer, method: str, path: str, payload: dict | None = None, *, authed: bool = True):
    headers = {"Authorization": f"Bearer {TOKEN}"} if authed else {}
    body = json.dumps(payload).encode() if payload is not None else b""
    return app.handle(parse_request(method, path, headers, body))


def open_chat(app, description="a booking app for barbers", **kw):
    resp = request(app, "POST", "/api/chat/sessions", {"description": description, "intake_mode": "smart", **kw})
    assert resp.code == 201, resp.body
    return json.loads(resp.body)


def turn(app, project_id="proj-1", text="four engineers"):
    resp = request(app, "POST", f"/api/chat/sessions/{project_id}/send", {"text": text})
    assert resp.code == 200, resp.body
    assert resp.content_type == "application/x-ndjson"
    return [json.loads(line) for line in b"".join(resp.stream).decode().splitlines()]


class TestCreate:
    def test_requires_auth(self, app):
        assert request(app, "POST", "/api/chat/sessions", {"description": "x"}, authed=False).code == 401

    def test_a_description_is_required(self, app):
        assert request(app, "POST", "/api/chat/sessions", {"description": "   "}).code == 400

    def test_an_unknown_size_is_refused(self, app):
        assert request(app, "POST", "/api/chat/sessions", {"description": "x", "intake_mode": "huge"}).code == 400

    def test_the_view_opens_on_the_greeting(self, app):
        view = open_chat(app)
        assert view["project_id"] == "proj-1"
        assert view["stage"] == "intake"
        assert [item["type"] for item in view["transcript"]] == ["assistant", "assistant"]
        # The description is messages[0], never a preamble echo — it would
        # otherwise appear twice the moment the first turn lands.
        assert not any("barbers" in item["text"] for item in view["transcript"])

    def test_a_new_conversation_is_persisted_immediately(self, app):
        open_chat(app)
        assert "proj-1" in app.saved


class TestSessionView:
    def test_an_unknown_conversation_is_a_404(self, app):
        assert request(app, "GET", "/api/chat/sessions/nope").code == 404

    def test_the_view_replays_the_turn(self, app):
        open_chat(app)
        turn(app)
        view = json.loads(request(app, "GET", "/api/chat/sessions/proj-1").body)
        kinds = [item["type"] for item in view["transcript"]]
        assert kinds == ["assistant", "assistant", "user", "assistant"]
        assert view["transcript"][-1]["text"] == "How many of you?"
        assert view["question"]["current_question"] == 2


class TestTurnStream:
    def test_the_op_id_leads_and_done_terminates(self, app):
        open_chat(app)
        lines = turn(app)
        assert lines[0]["type"] == "op" and lines[0]["op_id"]
        assert lines[-1] == {"stage": "intake", "type": "done"}

    def test_the_reply_streams_as_tokens_then_a_question(self, app):
        open_chat(app)
        lines = turn(app)
        assert "".join(line["text"] for line in lines if line["type"] == "token") == "How many of you?"
        question = next(line for line in lines if line["type"] == "question")
        assert question["number"] == 2

    def test_the_text_reaches_the_graph_and_the_state_is_saved(self, app, graph):
        open_chat(app)
        turn(app, text="four engineers")
        assert graph.invocations[-1]["messages"][-1].content == "four engineers"
        assert app.saved["proj-1"]["messages"]

    def test_a_provider_failure_lands_as_one_classified_line(self, app, graph, monkeypatch):
        open_chat(app)
        monkeypatch.setattr(graph, "invoke", lambda _state: (_ for _ in ()).throw(RuntimeError("boom")))
        lines = turn(app)
        assert lines[-1]["type"] == "error"
        # One classified human line, not a traceback and not a raw SDK dump.
        assert lines[-1]["message"].startswith("Unexpected error")
        assert len([line for line in lines if line["type"] == "error"]) == 1

    def test_a_second_turn_is_refused_while_one_is_running(self, app, graph):
        open_chat(app)
        graph.gate.clear()  # park the first turn inside the graph
        first = request(app, "POST", "/api/chat/sessions/proj-1/send", {"text": "one"})
        lines = iter(first.stream)
        assert json.loads(next(lines))["type"] == "op"  # the turn is in flight
        assert request(app, "POST", "/api/chat/sessions/proj-1/send", {"text": "two"}).code == 409
        graph.gate.set()
        b"".join(lines)  # drain, releasing the turn lock

    def test_the_turn_lock_and_op_are_released_after_the_stream(self, app):
        open_chat(app)
        lines = turn(app)
        op_id = lines[0]["op_id"]
        assert app.ops.get(op_id) is None
        assert request(app, "POST", "/api/chat/sessions/proj-1/send", {"text": "again"}).code == 200


class TestSupervisor:
    def test_one_live_session_per_conversation(self, app):
        open_chat(app)
        assert app.chats.open("proj-1") is app.chats.open("proj-1")

    def test_a_closed_conversation_resumes_from_disk(self, app):
        open_chat(app)
        turn(app)
        app.chats.close("proj-1")
        resumed = app.chats.open("proj-1")
        assert resumed.session.state["messages"]
