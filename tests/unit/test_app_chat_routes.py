"""The /api/chat routes — socketless, over AppServer.handle().

The conversation's own behaviour lives in test_chat_session.py; here the
subject is the wire: the session view, the NDJSON turn (its line order, its
op id, its terminators), and the locks that keep two turns off one state.
"""

from __future__ import annotations

import json
import pathlib
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

    def test_the_description_is_owed_as_the_first_turn(self, app):
        # It has to reach the graph as messages[0]; until it does, the view
        # carries it so the client knows to send it.
        assert open_chat(app)["opening"] == "a booking app for barbers"

    def test_the_opening_is_spent_once_it_has_been_sent(self, app):
        open_chat(app)
        turn(app, text="a booking app for barbers")
        assert json.loads(request(app, "GET", "/api/chat/sessions/proj-1").body)["opening"] == ""

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


class TestQuestionPlan:
    def test_a_pre_graph_conversation_has_no_plan_yet(self, app):
        # The questionnaire only exists after the first invoke, so there is
        # nothing to list — an empty plan, not a 404.
        open_chat(app)
        plan = json.loads(request(app, "GET", "/api/chat/sessions/proj-1/questions").body)
        assert plan == {"questions": [], "total": 30, "completed": False, "derived": False}

    def test_it_lists_what_the_run_answered_and_still_asks(self, app, monkeypatch):
        open_chat(app)
        turn(app)
        chat = app.chats.open("proj-1")
        chat.session.state["questionnaire"].answers = {1: "a booking app", 6: "four"}
        monkeypatch.setattr(
            "yeaboi.ui.session.chat._question_view.planned_question_sets",
            lambda qs: ([7], {1, 6}),
        )
        plan = json.loads(request(app, "GET", "/api/chat/sessions/proj-1/questions").body)
        assert [row["number"] for row in plan["questions"]] == [1, 6, 7]
        assert plan["questions"][0]["answer"] == "a booking app"
        # 7 is a gap: planned, not yet answered.
        assert plan["questions"][2]["remaining"] and plan["questions"][2]["answer"] == ""
        assert plan["questions"][0]["label"] and plan["derived"]

    def test_a_failed_derivation_says_so_rather_than_shrinking_the_plan(self, app, monkeypatch):
        # Falling back silently would present the answers as the whole plan.
        open_chat(app)
        turn(app)
        app.chats.open("proj-1").session.state["questionnaire"].answers = {1: "a booking app"}
        monkeypatch.setattr(
            "yeaboi.ui.session.chat._question_view.planned_question_sets",
            lambda qs: None,
        )
        plan = json.loads(request(app, "GET", "/api/chat/sessions/proj-1/questions").body)
        assert plan["derived"] is False
        assert [row["number"] for row in plan["questions"]] == [1]

    def test_an_unknown_conversation_is_a_404(self, app):
        assert request(app, "GET", "/api/chat/sessions/nope/questions").code == 404


class TestSizeSwitch:
    def test_an_unknown_size_is_refused(self, app):
        open_chat(app)
        assert request(app, "POST", "/api/chat/sessions/proj-1/size", {"mode": "huge"}).code == 400

    def test_switching_to_the_size_it_already_is_changes_nothing(self, app):
        open_chat(app)
        body = json.loads(request(app, "POST", "/api/chat/sessions/proj-1/size", {"mode": "smart"}).body)
        assert body == {"changed": False, "mode": "smart"}

    def test_before_the_intake_it_only_records_the_preference(self, app):
        open_chat(app)
        body = json.loads(request(app, "POST", "/api/chat/sessions/proj-1/size", {"mode": "small_project"}).body)
        assert body["changed"] and body["reopened"] is False
        assert app.chats.open("proj-1").session.state["_intake_mode"] == "small_project"

    def test_a_real_switch_keeps_the_answers(self, app):
        open_chat(app)
        turn(app)
        state = app.chats.open("proj-1").session.state
        state["questionnaire"].answers = {1: "a booking app"}
        body = json.loads(request(app, "POST", "/api/chat/sessions/proj-1/size", {"mode": "small_project"}).body)
        assert body["changed"] and body["reopened"]
        assert state["questionnaire"].answers == {1: "a booking app"}
        assert state["_intake_mode"] == "small_project"

    def test_the_switch_is_persisted(self, app):
        open_chat(app)
        request(app, "POST", "/api/chat/sessions/proj-1/size", {"mode": "small_project"})
        assert "proj-1" in app.saved


class TestAttachments:
    def _post(self, app, **payload):
        return request(app, "POST", "/api/chat/sessions/proj-1/attachments", payload)

    def test_a_pasted_image_is_kept_and_chipped(self, app, tmp_path, monkeypatch):
        import base64

        monkeypatch.setattr("yeaboi.paths.get_attachments_dir", lambda scope: tmp_path)
        open_chat(app)
        body = json.loads(self._post(app, image=base64.b64encode(b"PNGDATA").decode(), index=2).body)
        assert body["chip"] == "[image #2]"
        assert pathlib.Path(body["path"]).read_bytes() == b"PNGDATA"
        assert pathlib.Path(body["path"]).suffix == ".png"

    def test_a_jpeg_keeps_its_own_extension(self, app, tmp_path, monkeypatch):
        import base64

        monkeypatch.setattr("yeaboi.paths.get_attachments_dir", lambda scope: tmp_path)
        open_chat(app)
        body = json.loads(self._post(app, image=base64.b64encode(b"JPG").decode(), mime="image/jpeg", index=1).body)
        assert pathlib.Path(body["path"]).suffix == ".jpg"

    def test_a_pdf_is_not_an_image(self, app):
        open_chat(app)
        assert self._post(app, image="AAAA", mime="application/pdf", index=1).code == 400

    def test_garbage_is_refused_rather_than_written(self, app):
        open_chat(app)
        assert self._post(app, image="not base64!!", index=1).code == 400
        assert self._post(app, image="", index=1).code == 400

    def test_an_oversized_image_is_refused_at_paste_time(self, app):
        import base64

        from yeaboi.ui.shared._attachments import MAX_IMAGE_BYTES

        open_chat(app)
        big = base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode()
        resp = self._post(app, image=big, index=1)
        assert resp.code == 413 and "4.5 MB" in resp.body.decode()


class TestImagesFollowTheirChips:
    """Deleting an ``[image #N]`` chip detaches its image — the terminal's rule."""

    @staticmethod
    def _sent(graph) -> list[str]:
        """The images the last invoke carried, whichever slot the stage puts them in."""
        state = graph.invocations[-1]
        return list(state.get("chat_images") or state.get("pasted_images") or [])

    def test_only_the_chipped_attachments_travel(self, app, graph):
        open_chat(app)
        turn(app)
        resp = request(
            app,
            "POST",
            "/api/chat/sessions/proj-1/send",
            {"text": "look at [image #2]", "images": ["/tmp/a.png", "/tmp/b.png"]},
        )
        b"".join(resp.stream)
        assert self._sent(graph) == ["/tmp/b.png"]

    def test_an_attachment_with_no_surviving_chip_is_dropped(self, app, graph):
        open_chat(app)
        turn(app)
        resp = request(
            app,
            "POST",
            "/api/chat/sessions/proj-1/send",
            {"text": "never mind", "images": ["/tmp/a.png"]},
        )
        b"".join(resp.stream)
        assert self._sent(graph) == []


class TestSoloConversations:
    def test_solo_true_seeds_the_state_key(self, app):
        open_chat(app, solo=True)
        assert app.saved["proj-1"]["solo"] is True

    def test_the_default_is_a_team_conversation(self, app):
        open_chat(app)
        assert "solo" not in app.saved["proj-1"]
