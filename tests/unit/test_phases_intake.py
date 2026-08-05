"""Tests for the legacy intake question loop — the chat /form takeover contract.

Only the Esc-return semantics are covered here: the loop itself is exercised
end-to-end by the session integration tests. The chat driver hands its state to
_phase_intake_questions and MUST get the loop's (mutated) state back on Esc —
a None return would leave the driver holding recorded answers with stale
messages.
"""

from unittest.mock import patch

from yeaboi.agent.state import QuestionnaireState
from yeaboi.ui.session.phases._phases_intake import _phase_intake_questions


def _mid_intake_state() -> dict:
    qs = QuestionnaireState(current_question=6, intake_mode="smart")
    qs.answers = {2: "a", 3: "b", 4: "c"}
    return {"messages": [], "questionnaire": qs}


def _run(state, **kwargs):
    with (
        patch("yeaboi.ui.session.phases._phases_intake._predict_next_node", return_value="project_intake"),
        patch("yeaboi.ui.session.phases._phases_intake._question_input_loop", return_value=None),  # Esc
    ):
        return _phase_intake_questions(None, None, None, state, lambda t=0.0: "", False, **kwargs)


class TestEscReturn:
    def test_esc_returns_none_by_default(self):
        # Legacy callers treat None as "user cancelled" — unchanged.
        assert _run(_mid_intake_state()) is None

    def test_esc_returns_state_when_opted_in(self):
        state = _mid_intake_state()
        assert _run(state, return_state_on_esc=True) is state

    def test_completed_questionnaire_returns_state_immediately(self):
        state = _mid_intake_state()
        state["questionnaire"].completed = True
        assert _run(state) is state
