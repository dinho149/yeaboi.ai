"""Invariance gate: the chat protocol produces the same plan as the headless one.

The refactor's hard constraint is "planning results are unaffected". This test
drives the SAME FakeGraph state machine two ways —

  1. the established auto-driver (agent/headless.run_planning_pipeline), and
  2. a chat-style driver: stream_chat_turn() per turn with the exact
     accept-bookkeeping the chat driver performs between review stages —

and asserts the final artifacts are identical. If the chat path ever invokes
differently (extra messages, missing review-field clears, reordered stages),
this fails before any user sees a drifted plan.
"""

from langchain_core.messages import HumanMessage

from tests.unit.test_headless_pipeline import FakeGraph
from yeaboi.agent.streaming import predict_next_node, stream_chat_turn
from yeaboi.questionnaire_io import build_questionnaire_from_answers

_ANSWERS = {
    1: "Build a customer portal",
    2: "Greenfield",
    3: "Customers cannot self-serve",
    4: "Portal live with billing",
    6: "4",
    11: "Python, React",
    15: "No repository yet",
}


def _chat_style_run(graph: FakeGraph) -> dict:
    """Drive the pipeline the way the chat driver does (accept every review)."""
    state: dict = {
        "messages": [],
        "questionnaire": build_questionnaire_from_answers(dict(_ANSWERS)),
        "_intake_mode": "quick",
    }
    for _ in range(40):
        if state.get("pending_review"):
            # The chat driver's accept path (also headless's): clear the
            # review bookkeeping, then continue.
            if state["pending_review"] != "project_intake":
                for key in ("last_review_decision", "last_review_feedback", "review_feedback_images"):
                    state.pop(key, None)
                state.pop("pending_review", None)
                if predict_next_node(state) == "agent":
                    break
                text = "accept"
            else:
                state.pop("pending_review", None)
                text = "accept"
        elif state.get("questionnaire").completed:
            if predict_next_node(state) == "agent":
                break
            text = "continue"
        else:
            text = "confirm"
        invoke_state = {**state, "messages": [*state.get("messages", []), HumanMessage(content=text)]}
        state = stream_chat_turn(graph, invoke_state, lambda _t: None, typewriter_cps=0)
    return state


class TestChatInvariance:
    def test_same_artifacts_as_headless(self, monkeypatch):
        monkeypatch.setattr("yeaboi.logging_setup.attach_session_log", lambda _sid: None)
        monkeypatch.setattr("yeaboi.logging_setup.detach_session_log", lambda: None)

        headless_graph = FakeGraph()
        monkeypatch.setattr("yeaboi.agent.graph.create_graph", lambda *a, **k: headless_graph)
        from yeaboi.agent.headless import run_planning_pipeline

        headless_state = run_planning_pipeline(build_questionnaire_from_answers(dict(_ANSWERS)), save_session=False)

        chat_state = _chat_style_run(FakeGraph())

        for key in ("project_analysis", "features", "stories", "tasks", "sprints"):
            assert chat_state.get(key) == headless_state.get(key), key

        qs_headless = headless_state["questionnaire"]
        qs_chat = chat_state["questionnaire"]
        assert qs_chat.answers == qs_headless.answers
        assert qs_chat.intake_mode == qs_headless.intake_mode
        assert qs_chat.completed and qs_headless.completed

    def test_capacity_warning_path_matches(self, monkeypatch):
        monkeypatch.setattr("yeaboi.logging_setup.attach_session_log", lambda _sid: None)
        monkeypatch.setattr("yeaboi.logging_setup.detach_session_log", lambda: None)

        headless_graph = FakeGraph(capacity_warning=True)
        monkeypatch.setattr("yeaboi.agent.graph.create_graph", lambda *a, **k: headless_graph)
        from yeaboi.agent.headless import run_planning_pipeline

        headless_state = run_planning_pipeline(build_questionnaire_from_answers(dict(_ANSWERS)), save_session=False)

        # Chat path: accept the recommended sprint count, as the popup default does.
        graph = FakeGraph(capacity_warning=True)
        state: dict = {
            "messages": [],
            "questionnaire": build_questionnaire_from_answers(dict(_ANSWERS)),
            "_intake_mode": "quick",
        }
        for _ in range(40):
            cap = state.get("capacity_override_target", 0)
            if cap < -1:
                state["capacity_override_target"] = abs(cap)
                text = "accept recommended sprints"
            elif state.get("pending_review"):
                if state["pending_review"] != "project_intake":
                    for key in ("last_review_decision", "last_review_feedback", "review_feedback_images"):
                        state.pop(key, None)
                state.pop("pending_review", None)
                if predict_next_node(state) == "agent":
                    break
                text = "accept"
            elif state.get("questionnaire").completed:
                if predict_next_node(state) == "agent":
                    break
                text = "continue"
            else:
                text = "confirm"
            invoke_state = {**state, "messages": [*state.get("messages", []), HumanMessage(content=text)]}
            state = stream_chat_turn(graph, invoke_state, lambda _t: None, typewriter_cps=0)

        assert state.get("sprints") == headless_state.get("sprints")
        assert state.get("capacity_override_target") == headless_state.get("capacity_override_target")
