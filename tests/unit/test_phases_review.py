"""Tests for the legacy answer accordion — the chat Edit-pick takeover contract.

Only the cancel semantics are covered here; the browse loop itself is exercised
end-to-end by the session integration tests. The accordion predates the chat and
returns None to mean "the user abandoned planning". The chat driver opens it as
a modal over a live transcript, where that must instead mean "back to the chat" —
so it passes return_state_on_esc=True and MUST get the (mutated) state back.
"""

from types import SimpleNamespace
from unittest.mock import patch

from yeaboi.agent.state import QuestionnaireState
from yeaboi.ui.session.phases._phases_review import _edit_accordion_browse

_MODULE = "yeaboi.ui.session.phases._phases_review"


def _gate_state() -> dict:
    qs = QuestionnaireState(current_question=6, intake_mode="smart")
    qs.answers = {2: "a", 6: "1"}
    qs.awaiting_confirmation = True
    return {"messages": [], "questionnaire": qs, "pending_review": "project_intake"}


def _run(state, keys: list[str], *, invoke_result=None, questions_result=None, **kwargs):
    """Drive the browse loop over a fixed key sequence with the screen stubbed."""
    remaining = list(keys)
    live = SimpleNamespace(update=lambda _renderable: None)
    console = SimpleNamespace(size=(100, 40))
    with (
        patch(f"{_MODULE}._build_accordion_question_screen", return_value=None),
        patch(f"{_MODULE}._invoke_with_animation", return_value=invoke_result),
        patch(
            "yeaboi.ui.session.phases._phases_intake._phase_intake_questions",
            return_value=questions_result,
        ) as questions,
    ):
        result = _edit_accordion_browse(
            live,
            console,
            object(),  # graph — any non-None takes the re-ask path
            state,
            lambda: remaining.pop(0) if remaining else "esc",
            False,
            **kwargs,
        )
    return result, questions


class TestCancelledReAsk:
    """The invoke that re-asks the question was cancelled (Esc during it)."""

    def test_returns_none_by_default(self):
        assert _run(_gate_state(), ["enter"])[0] is None

    def test_the_flag_hands_the_state_back(self):
        state = _gate_state()
        assert _run(state, ["enter"], return_state_on_esc=True)[0] is state

    def test_the_flag_restores_the_review_gate(self):
        # The loop pops pending_review in preparation for the invoke. Returning
        # to a chat without it would leave the summary card with no gate behind
        # it — the driver would route the next reply somewhere else entirely.
        state = _gate_state()
        _run(state, ["enter"], return_state_on_esc=True)
        assert state["pending_review"] == "project_intake"

    def test_the_flag_restores_the_browsed_question(self):
        state = _gate_state()
        _run(state, ["down", "enter"], return_state_on_esc=True)
        assert state["questionnaire"].current_question == 6


class TestEscDuringTheReAsk:
    """The invoke succeeded; the user pressed Esc while re-answering."""

    def test_the_flag_reaches_the_nested_question_loop(self):
        state = _gate_state()
        answered = _gate_state()
        _, questions = _run(
            state,
            ["enter"],
            invoke_result=answered,
            questions_result=answered,
            return_state_on_esc=True,
        )
        assert questions.call_args.kwargs["return_state_on_esc"] is True

    def test_legacy_callers_leave_it_off(self):
        state = _gate_state()
        answered = _gate_state()
        _, questions = _run(state, ["enter"], invoke_result=answered, questions_result=answered)
        assert questions.call_args.kwargs["return_state_on_esc"] is False


class TestEditHint:
    def test_the_hint_is_the_callers_to_choose(self):
        # "Esc exit" reads as exiting planning when there is a chat behind the
        # screen, so the chat passes its own wording.
        state = _gate_state()
        live = SimpleNamespace(update=lambda _renderable: None)
        console = SimpleNamespace(size=(100, 40))
        with patch(f"{_MODULE}._build_accordion_question_screen", return_value=None) as screen:
            _edit_accordion_browse(live, console, None, state, lambda: "esc", False, edit_hint="Esc back to chat")
        assert screen.call_args.kwargs["edit_hint"] == "Esc back to chat"
