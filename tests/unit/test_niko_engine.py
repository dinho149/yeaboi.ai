"""Tests for the Niko engine (niko/engine.py).

The engine is a tool loop with one hard promise: it never raises. Every LLM
failure, every tool failure, every confused model has to end as an answer plus a
warning, because the caller is a chat panel and a traceback is not a reply.

The other property under test is the loop's shape — tools run, their results go
back as observations, and the round budget is a ceiling rather than a target.
"""

from __future__ import annotations

import threading

import pytest
from langchain_core.messages import AIMessage

from yeaboi.niko import engine
from yeaboi.niko.store import NikoStore


class FakeModel:
    """A chat model that replays a scripted list of replies."""

    def __init__(self, replies, *, streams: bool = False):
        self.replies = list(replies)
        self.streams = streams
        self.calls: list[list] = []

    def bind_tools(self, tools):
        self.bound = tools
        return self

    def invoke(self, messages):
        self.calls.append(list(messages))
        return self.replies.pop(0) if self.replies else AIMessage(content="done")

    def stream(self, messages):
        if not self.streams:
            raise NotImplementedError
        self.calls.append(list(messages))
        reply = self.replies.pop(0) if self.replies else AIMessage(content="done")
        from langchain_core.messages import AIMessageChunk

        text = reply.content if isinstance(reply.content, str) else ""
        for i in range(0, max(len(text), 1), 4):
            yield AIMessageChunk(content=text[i : i + 4], tool_calls=[])
        if reply.tool_calls:
            yield AIMessageChunk(content="", tool_calls=reply.tool_calls)


@pytest.fixture()
def wired(monkeypatch, tmp_path):
    """Wire the engine to a fake model and a scratch database."""
    from yeaboi.agent import llm as llm_module

    monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, "ok"))
    monkeypatch.setattr(llm_module, "track_usage", lambda *a, **k: None)
    # The auto-titler is a second, independent model call. Stubbed here so a
    # scripted reply list means "what the turn said", not "the turn plus a title".
    # TestTitling drives the real one.
    monkeypatch.setattr(engine, "_title_for", lambda question: question[:60])

    def _install(model):
        monkeypatch.setattr(llm_module, "get_llm", lambda *a, **k: model)
        return model

    return type("Wired", (), {"install": staticmethod(_install), "db": tmp_path / "sessions.db"})


def _tool_reply(name, args, call_id="t1", text=""):
    return AIMessage(content=text, tool_calls=[{"name": name, "args": args, "id": call_id}])


class TestAnswering:
    def test_a_plain_answer_comes_back_whole(self, wired):
        wired.install(FakeModel([AIMessage(content="yeaboi plans sprints.")]))
        answer = engine.ask("what is yeaboi?", db_path=wired.db)
        assert answer.text == "yeaboi plans sprints."
        assert answer.tool_calls == ()
        assert answer.warnings == ()

    def test_an_empty_question_is_the_callers_bug(self, wired):
        wired.install(FakeModel([]))
        with pytest.raises(ValueError, match="empty"):
            engine.ask("   ", db_path=wired.db)

    def test_an_empty_answer_still_says_something(self, wired):
        wired.install(FakeModel([AIMessage(content="   ")]))
        assert engine.ask("hm?", db_path=wired.db).text.strip()

    def test_events_arrive_in_render_order(self, wired):
        wired.install(FakeModel([AIMessage(content="hello")]))
        seen = []
        engine.ask("hi", db_path=wired.db, on_event=seen.append)
        assert [type(e).__name__ for e in seen] == ["Token", "Assistant", "Done"]

    def test_a_broken_renderer_does_not_kill_the_turn(self, wired):
        wired.install(FakeModel([AIMessage(content="hello")]))

        def explode(_event):
            raise RuntimeError("render died")

        assert engine.ask("hi", db_path=wired.db, on_event=explode).text == "hello"


class TestStreaming:
    def test_tokens_stream_when_the_provider_can(self, wired):
        wired.install(FakeModel([AIMessage(content="abcdefgh")], streams=True))
        seen = []
        answer = engine.ask("hi", db_path=wired.db, on_event=seen.append)
        tokens = [e.text for e in seen if isinstance(e, engine.Token)]
        assert len(tokens) > 1
        assert "".join(tokens) == "abcdefgh" == answer.text

    def test_a_provider_that_cannot_stream_still_answers_once(self, wired):
        wired.install(FakeModel([AIMessage(content="abcdefgh")], streams=False))
        seen = []
        engine.ask("hi", db_path=wired.db, on_event=seen.append)
        assert [e.text for e in seen if isinstance(e, engine.Token)] == ["abcdefgh"]


class TestTheToolLoop:
    def test_prose_from_every_round_survives(self, wired, monkeypatch):
        # A turn that says "let me check", calls a tool, then answers has
        # written two paragraphs; keeping only the second stores an answer the
        # user never saw.
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"ok": 1})
        wired.install(
            FakeModel([_tool_reply("ship_status", {}, text="Let me check."), AIMessage(content="Nothing waiting.")])
        )
        assert engine.ask("waiting?", db_path=wired.db).text == "Let me check.\n\nNothing waiting."

    def test_a_tool_result_goes_back_as_an_observation(self, wired, monkeypatch):
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"total_usd": 4.5})
        model = wired.install(FakeModel([_tool_reply("llm_usage", {}), AIMessage(content="$4.50.")]))
        answer = engine.ask("what did yeaboi cost?", db_path=wired.db)
        assert answer.text == "$4.50."
        assert [c.name for c in answer.tool_calls] == ["llm_usage"]
        # The second call must have seen the tool's output.
        assert any("total_usd" in str(getattr(m, "content", "")) for m in model.calls[1])

    def test_tool_events_bracket_the_call(self, wired, monkeypatch):
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"ok": 1})
        wired.install(FakeModel([_tool_reply("ship_status", {}), AIMessage(content="Nothing waiting.")]))
        seen = []
        engine.ask("anything waiting?", db_path=wired.db, on_event=seen.append)
        names = [type(e).__name__ for e in seen]
        assert names.index("ToolStarted") < names.index("ToolFinished") < names.index("Assistant")

    def test_a_failing_tool_is_recorded_and_the_turn_continues(self, wired, monkeypatch):
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"error": "no runs yet"})
        wired.install(FakeModel([_tool_reply("ship_status", {}), AIMessage(content="You haven't shipped yet.")]))
        answer = engine.ask("anything waiting?", db_path=wired.db)
        assert answer.text == "You haven't shipped yet."
        assert answer.tool_calls[0].ok is False
        assert answer.tool_calls[0].error == "no runs yet"

    def test_several_tools_in_one_round_all_run(self, wired, monkeypatch):
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"name": name})
        reply = AIMessage(
            content="",
            tool_calls=[
                {"name": "ship_status", "args": {}, "id": "a"},
                {"name": "llm_usage", "args": {}, "id": "b"},
            ],
        )
        wired.install(FakeModel([reply, AIMessage(content="Both read.")]))
        answer = engine.ask("status?", db_path=wired.db)
        assert [c.name for c in answer.tool_calls] == ["ship_status", "llm_usage"]

    def test_the_round_budget_is_a_ceiling_not_a_hang(self, wired, monkeypatch):
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"ok": 1})
        forever = [_tool_reply("ship_status", {}, call_id=str(i), text="thinking") for i in range(10)]
        model = wired.install(FakeModel(forever))
        answer = engine.ask("loop?", db_path=wired.db, max_rounds=3)
        assert len(model.calls) == 3
        assert len(answer.tool_calls) == 3
        assert any("tool-round limit" in w for w in answer.warnings)

    def test_cancel_stops_the_turn_between_rounds(self, wired, monkeypatch):
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"ok": 1})
        cancel = threading.Event()
        cancel.set()
        model = wired.install(FakeModel([AIMessage(content="never asked")]))
        answer = engine.ask("hi", db_path=wired.db, cancel=cancel)
        assert model.calls == []
        assert answer.text


class TestNavigate:
    def test_the_route_reaches_the_answer_and_the_stream(self, wired, monkeypatch):
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"route": "/humans/retro"})
        wired.install(
            FakeModel([_tool_reply("navigate", {"route": "/humans/retro"}), AIMessage(content="Taking you there.")])
        )
        seen = []
        answer = engine.ask("run a retro", db_path=wired.db, on_event=seen.append)
        assert answer.route == "/humans/retro"
        assert [e.route for e in seen if isinstance(e, engine.Navigate)] == ["/humans/retro"]

    def test_a_refused_route_is_not_suggested(self, wired, monkeypatch):
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"error": "not a route", "route": ""})
        wired.install(FakeModel([_tool_reply("navigate", {"route": "/nope"}), AIMessage(content="Can't.")]))
        assert engine.ask("go", db_path=wired.db).route == ""


class TestFailuresBecomeWarnings:
    def test_no_llm_still_signposts(self, monkeypatch, tmp_path):
        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (False, "ANTHROPIC_API_KEY not set"))
        answer = engine.ask("what can you do?", route="/agents/usage", db_path=tmp_path / "s.db")
        assert "ANTHROPIC_API_KEY not set" in answer.warnings[0]
        assert "agents cost" in answer.text.lower()

    def test_an_llm_error_is_a_warning_not_a_raise(self, wired):
        class Broken(FakeModel):
            def invoke(self, messages):
                raise RuntimeError("connection reset")

        wired.install(Broken([]))
        answer = engine.ask("hi", db_path=wired.db)
        assert answer.warnings and "LLM request failed" in answer.warnings[0]
        assert answer.text

    def test_an_auth_error_says_so(self, wired):
        class Unauthorized(FakeModel):
            def invoke(self, messages):
                raise RuntimeError("401 unauthorized: invalid api key")

        wired.install(Unauthorized([]))
        assert "billing" in engine.ask("hi", db_path=wired.db).warnings[0]

    def test_an_unbuildable_model_is_a_warning(self, monkeypatch, tmp_path):
        from yeaboi.agent import llm as llm_module

        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, "ok"))

        def boom(*a, **k):
            raise ImportError("langchain_openai is not installed")

        monkeypatch.setattr(llm_module, "get_llm", boom)
        answer = engine.ask("hi", db_path=tmp_path / "s.db")
        assert "could not be created" in answer.warnings[0]


class TestPersistence:
    def test_a_turn_is_stored_as_two_messages(self, wired):
        wired.install(FakeModel([AIMessage(content="hello")]))
        answer = engine.ask("hi", route="/home", db_path=wired.db)
        with NikoStore(wired.db) as store:
            messages = store.messages(answer.conversation_id)
        assert [m.role for m in messages] == ["user", "assistant"]
        assert messages[0].route == "/home"

    def test_the_thread_continues_when_its_id_is_passed_back(self, wired):
        model = wired.install(FakeModel([AIMessage(content="one"), AIMessage(content="two")]))
        first = engine.ask("hi", db_path=wired.db)
        second = engine.ask("and?", conversation_id=first.conversation_id, db_path=wired.db)
        assert second.conversation_id == first.conversation_id
        # The second turn must have carried the first exchange.
        assert any("one" in str(getattr(m, "content", "")) for m in model.calls[-1])

    def test_an_unknown_conversation_id_opens_a_new_thread(self, wired):
        wired.install(FakeModel([AIMessage(content="hello")]))
        answer = engine.ask("hi", conversation_id="gone", db_path=wired.db)
        assert answer.conversation_id != "gone"

    def test_replay_tells_the_model_what_was_already_read(self, wired, monkeypatch):
        monkeypatch.setattr("yeaboi.niko.tools.call", lambda name, args: {"ok": 1})
        model = wired.install(
            FakeModel(
                [
                    _tool_reply("ship_status", {}),
                    AIMessage(content="Nothing waiting."),
                    AIMessage(content="Still nothing."),
                ]
            )
        )
        first = engine.ask("waiting?", db_path=wired.db)
        engine.ask("and now?", conversation_id=first.conversation_id, db_path=wired.db)
        replayed = " ".join(str(getattr(m, "content", "")) for m in model.calls[-1])
        assert "Earlier you read: ship_status" in replayed


class TestPromptContext:
    def test_the_system_prompt_names_the_screen(self, wired):
        model = wired.install(FakeModel([AIMessage(content="ok")]))
        engine.ask("hi", route="/agents/usage", user_name="Omar", db_path=wired.db)
        system = str(model.calls[0][0].content)
        assert "/agents/usage" in system
        assert "agent-usage" in system
        assert "Omar" in system

    def test_the_terminal_surface_describes_navigate_differently(self, wired):
        model = wired.install(FakeModel([AIMessage(content="ok")]))
        engine.ask("hi", surface="terminal", db_path=wired.db)
        assert "terminal" in str(model.calls[0][0].content)

    def test_an_unreadable_session_store_does_not_break_the_prompt(self, monkeypatch):
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: (_ for _ in ()).throw(OSError("gone")))
        assert engine._facts() == ()


class TestTitling:
    """The auto-titler is a separate, cheap model call, and never fatal."""

    def test_the_title_comes_from_the_opening_question(self, monkeypatch, tmp_path):
        from yeaboi.agent import llm as llm_module

        monkeypatch.setattr("yeaboi.config.is_llm_configured", lambda: (True, "ok"))
        monkeypatch.setattr(llm_module, "track_usage", lambda *a, **k: None)
        # One instance for both calls — the turn, then the titler.
        shared = FakeModel([AIMessage(content="hello"), AIMessage(content='"Agent Spend"')])
        monkeypatch.setattr(llm_module, "get_llm", lambda *a, **k: shared)
        answer = engine.ask("what did my agents cost?", db_path=tmp_path / "s.db")
        with NikoStore(tmp_path / "s.db") as store:
            assert store.get(answer.conversation_id).title == "Agent Spend"

    def test_a_failing_titler_falls_back_to_the_question(self, monkeypatch):
        from yeaboi.agent import llm as llm_module

        def boom(*a, **k):
            raise RuntimeError("no model")

        monkeypatch.setattr(llm_module, "get_llm", boom)
        assert engine._title_for("what did my agents cost?") == "what did my agents cost?"

    def test_a_long_question_is_truncated_with_an_ellipsis(self, monkeypatch):
        from yeaboi.agent import llm as llm_module

        monkeypatch.setattr(llm_module, "get_llm", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
        title = engine._title_for("x" * 200)
        assert title.endswith("\u2026") and len(title) == 61
