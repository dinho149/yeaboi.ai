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


class _Live:
    """Stand-in for the Rich Live the page drives; records nothing it need not."""

    def update(self, renderable, refresh: bool = False) -> None:  # noqa: ARG002
        self.last = renderable


class _Store:
    """A NikoStore that holds no database."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def messages(self, conversation_id: str):  # noqa: ARG002
        return []


def _drive(monkeypatch, keys: list[str], **kwargs) -> tuple[str, str]:
    """Run the page loop against a scripted keyboard and no database."""
    import io

    from rich.console import Console

    from yeaboi.niko import store as store_module
    from yeaboi.niko import suggestions
    from yeaboi.ui.mode_select._niko import run_niko_page

    monkeypatch.setattr(store_module, "NikoStore", _Store)
    monkeypatch.setattr(suggestions, "for_route", lambda route: [])
    pending = list(keys)
    return run_niko_page(
        Console(file=io.StringIO(), width=100, height=40),
        _Live(),
        lambda *a, **k: pending.pop(0) if pending else "escape",
        0.05,
        False,
        **kwargs,
    )


class TestSavedHandoff:
    """The page asks for the hub; it never opens one itself.

    That is what bounds the two: the hub opens the page with ``from_hub``, which
    drops the action that would open a hub again.
    """

    def test_saved_returns_the_handoff(self, monkeypatch):
        # Ask · New · Saved · Back — two rights lands on Saved.
        assert _drive(monkeypatch, ["right", "right", "enter"]) == ("", "saved")

    def test_from_hub_has_no_saved_action(self, monkeypatch):
        # Same two rights, but the row is Ask · New · Back, so this is Back —
        # which leaves with no handoff rather than asking for a second hub.
        assert _drive(monkeypatch, ["right", "right", "enter"], from_hub=True) == ("", "")

    def test_escape_asks_for_nothing(self, monkeypatch):
        assert _drive(monkeypatch, ["escape"]) == ("", "")


class TestClicks:
    """A click on the action row is hit-tested against the frame it landed on.

    The buttons are the only mouse target on the page, and getting the call
    wrong crashes the whole TUI rather than missing a button — the page renders
    the panel it clicks into, so the two cannot disagree.
    """

    def test_clicking_saved_asks_for_the_hub(self, monkeypatch):
        # Row 37 is the label row of the four action buttons at 100x40; column 41
        # is inside the third ("Saved").
        assert _drive(monkeypatch, ["click:41:37"]) == ("", "saved")

    def test_a_click_on_nothing_leaves_the_page_running(self, monkeypatch):
        # A miss must neither raise nor act — the following hit still lands.
        assert _drive(monkeypatch, ["click:2:2", "click:41:37"]) == ("", "saved")


class TestOpenNiko:
    """The duck's door: open the chat, and hand off to the hub only when asked."""

    @staticmethod
    def _patch(monkeypatch, next_action: str) -> list[str]:
        """Record the order the page and the hub are opened in."""
        import yeaboi.ui.mode_select as mode_select
        from yeaboi.ui.mode_select import _niko as niko_page

        seen: list[str] = []

        def _page(*args, **kwargs):
            seen.append("page")
            return "c1", next_action

        def _hub(*args, **kwargs):
            seen.append("hub")

        monkeypatch.setattr(niko_page, "run_niko_page", _page)
        monkeypatch.setattr(mode_select, "_run_niko_hub", _hub)
        return seen

    def test_opens_the_chat_and_stops_there(self, monkeypatch):
        import yeaboi.ui.mode_select as mode_select

        seen = self._patch(monkeypatch, "")
        mode_select._open_niko(None, None, None, 0.05, False)
        assert seen == ["page"]

    def test_saved_hands_off_to_the_hub_once(self, monkeypatch):
        import yeaboi.ui.mode_select as mode_select

        seen = self._patch(monkeypatch, "saved")
        mode_select._open_niko(None, None, None, 0.05, False)
        assert seen == ["page", "hub"]
