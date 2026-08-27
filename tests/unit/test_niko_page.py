"""The Niko page's pure halves (ui/mode_select/_niko.py).

The loop itself needs a terminal, so what is tested here is what does not: the
stored-conversation → rows mapping the hub's snapshot also uses, and the event
accumulator the worker thread writes and the render loop reads.
"""

from __future__ import annotations

from yeaboi.agent.state import NikoMessage, NikoToolCall
from yeaboi.niko import engine
from yeaboi.ui.mode_select._niko import _Turn, _turns_from


class TestTurnsFrom:
    def test_a_plain_exchange_becomes_two_rows(self):
        rows = _turns_from(
            [
                NikoMessage(role="user", content="hi"),
                NikoMessage(role="assistant", content="hey"),
            ]
        )
        assert [row["role"] for row in rows] == ["user", "assistant"]
        assert rows[1]["text"] == "hey"

    def test_tool_calls_ride_along_with_their_verdict(self):
        rows = _turns_from(
            [
                NikoMessage(
                    role="assistant",
                    content="$4.50",
                    tool_calls=(
                        NikoToolCall(name="llm_usage", ok=True),
                        NikoToolCall(name="ship_status", ok=False, error="no runs"),
                    ),
                )
            ]
        )
        assert rows[0]["tools"] == [
            {"name": "llm_usage", "ok": True},
            {"name": "ship_status", "ok": False},
        ]

    def test_a_navigate_call_surfaces_its_route(self):
        rows = _turns_from(
            [
                NikoMessage(
                    role="assistant",
                    content="there",
                    tool_calls=(NikoToolCall(name="navigate", ok=True, result={"route": "/humans/retro"}),),
                )
            ]
        )
        assert rows[0]["route"] == "/humans/retro"

    def test_a_refused_navigate_offers_no_route(self):
        rows = _turns_from(
            [
                NikoMessage(
                    role="assistant",
                    content="can't",
                    tool_calls=(NikoToolCall(name="navigate", ok=False, error="not a route"),),
                )
            ]
        )
        assert rows[0]["route"] == ""

    def test_a_non_dict_result_does_not_raise(self):
        rows = _turns_from(
            [NikoMessage(role="assistant", tool_calls=(NikoToolCall(name="navigate", ok=True, result="oops"),))]
        )
        assert rows[0]["route"] == ""

    def test_an_empty_conversation_is_no_rows(self):
        assert _turns_from([]) == []


class TestTurnAccumulator:
    def test_tokens_accumulate_in_order(self):
        turn = _Turn()
        turn.on_event(engine.Token("$4"))
        turn.on_event(engine.Token(".50"))
        assert "".join(turn.text) == "$4.50"

    def test_the_finished_answer_replaces_the_streamed_pieces(self):
        turn = _Turn()
        turn.on_event(engine.Token("par"))
        turn.on_event(engine.Token("tial"))
        turn.on_event(engine.Assistant("The whole answer."))
        assert turn.text == ["The whole answer."]

    def test_a_tool_starts_optimistic_and_is_closed_by_its_result(self):
        turn = _Turn()
        turn.on_event(engine.ToolStarted("ship_status", {}))
        assert turn.tools == [{"name": "ship_status", "ok": True}]
        turn.on_event(engine.ToolFinished(NikoToolCall(name="ship_status", ok=False, error="no runs")))
        assert turn.tools == [{"name": "ship_status", "ok": False}]

    def test_the_newest_open_call_is_the_one_closed(self):
        turn = _Turn()
        turn.on_event(engine.ToolStarted("get_session", {}))
        turn.on_event(engine.ToolStarted("get_session", {}))
        turn.on_event(engine.ToolFinished(NikoToolCall(name="get_session", ok=False)))
        assert [row["ok"] for row in turn.tools] == [True, False]

    def test_a_result_with_no_matching_call_is_ignored(self):
        turn = _Turn()
        turn.on_event(engine.ToolFinished(NikoToolCall(name="llm_usage", ok=True)))
        assert turn.tools == []

    def test_the_route_is_kept(self):
        turn = _Turn()
        turn.on_event(engine.Navigate("/usage"))
        assert turn.route == "/usage"

    def test_an_unknown_event_is_ignored_rather_than_fatal(self):
        turn = _Turn()
        turn.on_event(engine.Done("c1"))
        assert turn.text == [] and turn.tools == []
