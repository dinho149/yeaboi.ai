"""Tests for agent/streaming.py — the chat-turn streaming transport."""

import threading

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from yeaboi.agent.state import QuestionnaireState
from yeaboi.agent.streaming import (
    _TYPEWRITER_MAX_CHARS,
    ChatStreamCancelledError,
    _text_of,
    predict_next_node,
    stream_chat_turn,
)


def _agent_ready_state() -> dict:
    """A state that predict_next_node routes to the 'agent' node (plan complete)."""
    qs = QuestionnaireState(completed=True)
    analysis = object.__new__(object)  # truthiness is all predict_next_node needs
    return {
        "messages": [HumanMessage(content="what changed in sprint 2?")],
        "questionnaire": qs,
        "project_analysis": analysis,
        "features": ["f"],
        "stories": ["s"],
        "tasks": ["t"],
        "sprints": ["sp"],
    }


def _intake_state() -> dict:
    """A state that predict_next_node routes to project_intake (no questionnaire)."""
    return {"messages": [HumanMessage(content="my project description")]}


class FakeStreamGraph:
    """Scripted graph: .stream() replays events, .invoke() returns a state."""

    def __init__(self, events=(), invoke_result=None):
        self.events = list(events)
        self.invoke_result = invoke_result
        self.closed = False
        self.stream_calls = 0
        self.invoke_calls = 0

    def stream(self, state, stream_mode=None):
        self.stream_calls += 1

        def _gen():
            try:
                yield from self.events
            finally:
                self.closed = True

        return _gen()

    def invoke(self, state):
        self.invoke_calls += 1
        return self.invoke_result


class TestTextOf:
    def test_plain_string(self):
        assert _text_of("hello") == "hello"

    def test_anthropic_block_list_keeps_only_text(self):
        content = [
            {"type": "text", "text": "Hi "},
            {"type": "tool_use", "name": "jira", "input": {}},
            {"type": "text", "text": "there"},
        ]
        assert _text_of(content) == "Hi there"

    def test_list_of_strings(self):
        assert _text_of(["a", "b"]) == "ab"

    def test_unknown_content_is_empty(self):
        assert _text_of(42) == ""


class TestAgentStreaming:
    """Path A — real token streaming for the agent node."""

    def _final(self):
        state = _agent_ready_state()
        state["messages"] = [*state["messages"], AIMessage(content="Hi there")]
        return state

    def test_streams_agent_tokens_in_order(self):
        final = self._final()
        graph = FakeStreamGraph(
            events=[
                ("messages", (AIMessageChunk(content="Hi "), {"langgraph_node": "agent"})),
                ("messages", (AIMessageChunk(content="there"), {"langgraph_node": "agent"})),
                ("values", final),
            ]
        )
        tokens: list[str] = []
        result = stream_chat_turn(graph, _agent_ready_state(), tokens.append)
        assert tokens == ["Hi ", "there"]
        assert result is final
        assert graph.stream_calls == 1
        assert graph.invoke_calls == 0
        assert graph.closed  # generator always closed, even on success

    def test_filters_non_agent_nodes(self):
        # A tool node (or any other node's LLM call) must never leak into chat.
        final = self._final()
        graph = FakeStreamGraph(
            events=[
                ("messages", (AIMessageChunk(content="secret json"), {"langgraph_node": "tools"})),
                ("messages", (AIMessageChunk(content="visible"), {"langgraph_node": "agent"})),
                ("values", final),
            ]
        )
        tokens: list[str] = []
        stream_chat_turn(graph, _agent_ready_state(), tokens.append)
        assert tokens == ["visible"]

    def test_block_list_content_emits_text_only(self):
        final = self._final()
        chunk = AIMessageChunk(content=[{"type": "text", "text": "Hi"}])
        graph = FakeStreamGraph(events=[("messages", (chunk, {"langgraph_node": "agent"})), ("values", final)])
        tokens: list[str] = []
        stream_chat_turn(graph, _agent_ready_state(), tokens.append)
        assert tokens == ["Hi"]

    def test_empty_tool_call_chunks_are_skipped(self):
        final = self._final()
        graph = FakeStreamGraph(
            events=[
                ("messages", (AIMessageChunk(content=""), {"langgraph_node": "agent"})),
                ("values", final),
            ]
        )
        tokens: list[str] = []
        stream_chat_turn(graph, _agent_ready_state(), tokens.append)
        assert tokens == []

    def test_cancel_aborts_and_closes_stream(self):
        cancel = threading.Event()
        cancel.set()
        graph = FakeStreamGraph(
            events=[
                ("messages", (AIMessageChunk(content="Hi"), {"langgraph_node": "agent"})),
                ("values", self._final()),
            ]
        )
        with pytest.raises(ChatStreamCancelledError):
            stream_chat_turn(graph, _agent_ready_state(), lambda t: None, cancel=cancel)
        assert graph.closed

    def test_missing_final_state_raises(self):
        graph = FakeStreamGraph(events=[("messages", (AIMessageChunk(content="x"), {"langgraph_node": "agent"}))])
        with pytest.raises(RuntimeError, match="final state"):
            stream_chat_turn(graph, _agent_ready_state(), lambda t: None)


class TestTypewriter:
    """Path B — deterministic text replayed through on_token."""

    def _result(self, text: str) -> dict:
        return {"messages": [HumanMessage(content="desc"), AIMessage(content=text)]}

    def test_replays_last_ai_message(self):
        text = "What is your team size? Pick a number."
        graph = FakeStreamGraph(invoke_result=self._result(text))
        tokens: list[str] = []
        result = stream_chat_turn(graph, _intake_state(), tokens.append, typewriter_cps=0)
        assert "".join(tokens) == text
        assert result is graph.invoke_result
        assert graph.invoke_calls == 1
        assert graph.stream_calls == 0  # intake never takes the streaming path

    def test_cancel_fast_forwards_but_returns_result(self):
        # The graph already ran — cancel must not lose the recorded answer.
        text = "A" * 100
        cancel = threading.Event()
        cancel.set()
        graph = FakeStreamGraph(invoke_result=self._result(text))
        tokens: list[str] = []
        result = stream_chat_turn(graph, _intake_state(), tokens.append, cancel=cancel, typewriter_cps=0)
        assert "".join(tokens) == text
        assert result is graph.invoke_result

    def test_no_ai_message_emits_nothing(self):
        graph = FakeStreamGraph(invoke_result={"messages": [HumanMessage(content="x")]})
        tokens: list[str] = []
        stream_chat_turn(graph, _intake_state(), tokens.append, typewriter_cps=0)
        assert tokens == []


class TestTypewriterCap:
    """Replies over typewriter_max_chars skip the replay: nothing is emitted
    and the finished state returns at once — the driver appends the reply from
    the returned state (usually as a card), so a wall of text never scrolls
    the chat first."""

    def _result(self, text: str) -> dict:
        return {"messages": [HumanMessage(content="desc"), AIMessage(content=text)]}

    def test_long_reply_emits_no_tokens_but_returns_state(self):
        text = "S" * (_TYPEWRITER_MAX_CHARS + 1)
        graph = FakeStreamGraph(invoke_result=self._result(text))
        tokens: list[str] = []
        result = stream_chat_turn(graph, _intake_state(), tokens.append, typewriter_cps=0)
        assert tokens == []
        assert result is graph.invoke_result

    def test_reply_at_cap_still_typewrites(self):
        text = "S" * _TYPEWRITER_MAX_CHARS
        graph = FakeStreamGraph(invoke_result=self._result(text))
        tokens: list[str] = []
        stream_chat_turn(graph, _intake_state(), tokens.append, typewriter_cps=0)
        assert "".join(tokens) == text

    def test_zero_cap_disables_the_limit(self):
        text = "S" * (_TYPEWRITER_MAX_CHARS + 1)
        graph = FakeStreamGraph(invoke_result=self._result(text))
        tokens: list[str] = []
        stream_chat_turn(graph, _intake_state(), tokens.append, typewriter_cps=0, typewriter_max_chars=0)
        assert "".join(tokens) == text

    def test_explicit_cap_overrides_default(self):
        text = "S" * 50
        graph = FakeStreamGraph(invoke_result=self._result(text))
        tokens: list[str] = []
        stream_chat_turn(graph, _intake_state(), tokens.append, typewriter_cps=0, typewriter_max_chars=10)
        assert tokens == []


class TestPredictNextNode:
    def test_reexports_headless_prediction(self):
        # One hand-synced copy of route_entry's logic lives in headless.py;
        # streaming must reuse it, not fork a third.
        from yeaboi.agent.headless import _predict_next_node

        assert predict_next_node is _predict_next_node

    def test_routes_agent_when_plan_complete(self):
        assert predict_next_node(_agent_ready_state()) == "agent"
        assert predict_next_node(_intake_state()) == "project_intake"
