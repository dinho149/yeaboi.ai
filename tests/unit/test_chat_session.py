"""The chat session's decision layer.

These are the answers every surface driving the planning graph needs — which
stage the conversation is in, what the newest reply becomes, how a review
verdict reads — so they are tested here once rather than through a renderer.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from yeaboi.agent.chat_session import (
    ACCEPT_WORDS,
    CONFIRM_VERDICT_PROMPT,
    PIPELINE_NODES,
    PRIOR_ART_VERDICT_PROMPT,
    Accept,
    AskQuestion,
    Assistant,
    AwaitConfirm,
    ChatSession,
    Done,
    EditFeedback,
    SwitchSize,
    Token,
    TrackerSync,
    at_intake_summary,
    at_prior_art,
    clear_review_state,
    next_node,
    replay_plan,
    reply_event,
    review_gate,
    review_verdict,
    stage_of,
)
from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState


def _done_qs(**kwargs) -> QuestionnaireState:
    """A questionnaire parked on the confirmation gate."""
    qs = QuestionnaireState(current_question=TOTAL_QUESTIONS + 1)
    qs.answers = {i: f"a{i}" for i in range(1, TOTAL_QUESTIONS + 1)}
    qs.awaiting_confirmation = True
    for key, value in kwargs.items():
        setattr(qs, key, value)
    return qs


# The stage machine only asks whether an analysis exists (and whether it wants
# features skipped), so a stand-in keeps these tests off ProjectAnalysis's
# 20-field constructor.
_ANALYSIS = object()


class TestNextNode:
    def test_an_unfinished_questionnaire_routes_to_intake(self):
        assert next_node({"questionnaire": None}) == "project_intake"

    def test_dry_run_reads_the_artifact_keys_instead_of_the_graph(self):
        # No graph to predict against — the artifacts already in state carry
        # the order, so a dry run walks the same node sequence.
        state: dict = {}
        assert next_node(state, dry_run=True) == "project_analyzer"
        state["project_analysis"] = _ANALYSIS
        assert next_node(state, dry_run=True) == "feature_generator"
        state.update(features=[1], stories=[1], tasks=[1], sprints=[1])
        assert next_node(state, dry_run=True) == "agent"


class TestStage:
    def test_capacity_overflow_wins_over_everything(self):
        state = {"capacity_override_target": -4, "pending_review": "story_writer"}
        assert stage_of(state) == "capacity"

    def test_capacity_is_never_raised_in_dry_run(self):
        assert stage_of({"capacity_override_target": -4}, dry_run=True) != "capacity"

    def test_an_open_spike_question_parks_the_pipeline(self):
        assert stage_of({"_spike_prompt": {"confidence": "low"}}) == "spike"

    def test_an_answered_spike_question_does_not(self):
        state = {"_spike_prompt": {"confidence": "low"}, "spike_choice": "skip"}
        assert stage_of(state) != "spike"

    def test_the_intake_gate_is_intake_not_review(self):
        # project_intake consumes its own verdict, so it must not be routed to
        # the review-card branch that would consume it first.
        assert stage_of({"pending_review": "project_intake"}) == "intake"

    def test_a_parked_generation_node_is_a_review(self):
        for node in PIPELINE_NODES:
            assert stage_of({"pending_review": node}) == "review"

    def test_the_epic_step_precedes_the_first_feature_stage(self):
        state = {"questionnaire": _done_qs(completed=True), "project_analysis": _ANALYSIS}
        assert stage_of(state) == "epic"
        state["_epic_reviewed"] = True
        assert stage_of(state) == "pipeline"

    def test_a_finished_plan_is_free_chat(self):
        state = {
            "questionnaire": _done_qs(completed=True),
            "project_analysis": _ANALYSIS,
            "_epic_reviewed": True,
            "features": [1],
            "stories": [1],
            "tasks": [1],
            "sprints": [1],
        }
        assert stage_of(state) == "chat"


class TestGatePredicates:
    def test_the_summary_gate_is_claimed_once_the_questions_are_done(self):
        assert at_intake_summary({"questionnaire": _done_qs()}) is True

    def test_the_prior_art_subloop_withholds_the_summary(self):
        # Both run with awaiting_confirmation set; without this the markdown
        # wall the card replaced comes back on top of the prior-art card.
        for stage in ("ask", "reason", "empty"):
            state = {"questionnaire": _done_qs(_prior_art_stage=stage)}
            assert at_prior_art(state) is True
            assert at_intake_summary(state) is False

    def test_a_sub_prompt_withholds_the_summary(self):
        for field in ("_awaiting_leave_input", "_awaiting_velocity_input"):
            assert at_intake_summary({"questionnaire": _done_qs(**{field: True})}) is False

    def test_an_answer_being_edited_withholds_the_summary(self):
        assert at_intake_summary({"questionnaire": _done_qs(editing_question=3)}) is False


class TestReplyEvent:
    def test_no_reply_is_nothing_to_show(self):
        assert reply_event({"messages": [HumanMessage(content="hi")]}) is None

    def test_the_summary_becomes_a_card_and_a_verdict_prompt(self):
        state = {"questionnaire": _done_qs(), "messages": [AIMessage(content="# A wall of markdown")]}
        event = reply_event(state)
        assert event == AwaitConfirm(kind="intake_summary", prompt=CONFIRM_VERDICT_PROMPT)

    def test_the_prior_art_batch_becomes_a_card_and_one_line(self):
        state = {
            "questionnaire": _done_qs(_prior_art_stage="ask"),
            "messages": [AIMessage(content="1. acme/auth\n2. acme/pay")],
        }
        assert reply_event(state) == AwaitConfirm(kind="prior_art", prompt=PRIOR_ART_VERDICT_PROMPT)

    def test_a_rejected_prior_art_answer_goes_out_as_prose(self):
        # Re-posting the same card over a rejected answer would read as a no-op.
        from yeaboi.agent.nodes import _PRIOR_ART_GRAMMAR_HINT

        state = {
            "questionnaire": _done_qs(_prior_art_stage="ask"),
            "messages": [AIMessage(content=_PRIOR_ART_GRAMMAR_HINT)],
        }
        assert reply_event(state) == Assistant(_PRIOR_ART_GRAMMAR_HINT)

    def test_an_empty_prior_art_result_goes_out_as_prose(self):
        state = {
            "questionnaire": _done_qs(_prior_art_stage="empty"),
            "messages": [AIMessage(content="Nothing of yours looks related.")],
        }
        assert reply_event(state) == Assistant("Nothing of yours looks related.")

    def test_a_mid_intake_reply_is_a_decorated_question(self):
        qs = QuestionnaireState(intake_mode="smart", current_question=6)
        state = {"questionnaire": qs, "messages": [AIMessage(content="How many engineers?")]}
        event = reply_event(state)
        assert isinstance(event, AskQuestion)
        assert event.number == 6
        assert "How many engineers?" in event.text

    def test_free_chat_replies_pass_through_untouched(self):
        state = {
            "questionnaire": _done_qs(completed=True, awaiting_confirmation=False),
            "messages": [AIMessage(content="Sure — updated.")],
        }
        assert reply_event(state) == Assistant("Sure — updated.")


class TestReviewGate:
    def test_every_pipeline_node_has_a_card(self):
        for node in PIPELINE_NODES:
            assert review_gate({}, node).kind

    def test_the_grammar_names_accept_edit_and_export(self):
        prompt = review_gate({}, "story_writer").prompt
        assert "**accept**" in prompt and "**edit**" in prompt and "/export" in prompt

    def test_an_oversized_small_project_is_offered_the_switch(self):
        gate = review_gate({"_small_project_oversized": True}, "project_analyzer")
        assert "switch to large" in gate.prompt

    def test_the_switch_is_only_offered_at_the_analysis_gate(self):
        assert "switch to large" not in review_gate({"_small_project_oversized": True}, "story_writer").prompt


class TestReviewVerdict:
    def test_every_accept_word_accepts(self):
        for word in ACCEPT_WORDS:
            assert isinstance(review_verdict(word.upper(), "story_writer"), Accept)

    def test_the_size_switch_is_only_a_switch_at_the_analysis_gate(self):
        assert review_verdict("switch to large", "project_analyzer") == SwitchSize(target="smart")
        assert isinstance(review_verdict("switch to large", "story_writer"), EditFeedback)

    def test_sync_picks_the_named_tracker(self):
        assert review_verdict("sync jira", "sprint_planner") == TrackerSync(tracker="jira")
        assert review_verdict("sync azure devops", "sprint_planner") == TrackerSync(tracker="azdevops")
        # Bare "sync" names nothing — the caller uses whatever is configured.
        assert review_verdict("sync", "sprint_planner") == TrackerSync(tracker="")

    def test_anything_else_is_edit_feedback_with_the_verb_stripped(self):
        assert review_verdict("edit make it smaller", "story_writer") == EditFeedback(text="make it smaller")
        assert review_verdict("regenerate  with fewer stories", "story_writer") == EditFeedback(
            text="with fewer stories"
        )

    def test_a_bare_verb_keeps_the_original_text(self):
        # Stripping "edit" off "edit" would send an empty refinement request.
        assert review_verdict("edit", "story_writer") == EditFeedback(text="edit")


class TestClearReviewState:
    def test_the_whole_review_bookkeeping_goes(self):
        state = {
            "pending_review": "story_writer",
            "last_review_decision": "edit",
            "last_review_feedback": "more",
            "review_feedback_images": ["a.png"],
            "_small_project_oversized": True,
            "stories": [1],
        }
        clear_review_state(state)
        assert state == {"stories": [1]}


class TestReplayPlan:
    def test_a_resumed_summary_gate_cards_the_newest_reply(self):
        messages = [
            HumanMessage(content="build a todo app"),
            AIMessage(content="Q1?"),
            HumanMessage(content="two"),
            AIMessage(content="# The summary wall"),
        ]
        plan = replay_plan({"questionnaire": _done_qs(), "messages": messages})
        assert plan.summary_at == 3
        assert plan.prior_art_at == -1

    def test_nothing_is_carded_mid_intake(self):
        qs = QuestionnaireState(intake_mode="smart", current_question=4)
        plan = replay_plan({"questionnaire": qs, "messages": [AIMessage(content="Q4?")]})
        assert (plan.summary_at, plan.prior_art_at) == (-1, -1)

    def test_the_prior_art_card_skips_a_trailing_grammar_hint(self):
        # Pinning to the newest reply outright would card the one-liner while
        # the batch-prompt wall above it replayed raw.
        from yeaboi.agent.nodes import _PRIOR_ART_GRAMMAR_HINT

        qs = _done_qs(_prior_art_stage="ask")
        qs._prior_art_candidates = [{"key": "github:acme/auth", "name": "acme/auth"}]
        messages = [
            AIMessage(content="1. acme/auth"),
            HumanMessage(content="the first one"),
            AIMessage(content=_PRIOR_ART_GRAMMAR_HINT),
        ]
        plan = replay_plan({"questionnaire": qs, "messages": messages})
        assert plan.prior_art_at == 0
        assert plan.summary_at == -1

    def test_no_candidates_means_no_card_to_rebuild(self):
        qs = _done_qs(_prior_art_stage="ask")
        plan = replay_plan({"questionnaire": qs, "messages": [AIMessage(content="1. acme/auth")]})
        assert plan.prior_art_at == -1


class FakeGraph:
    """Returns a scripted state per invoke, recording what it was invoked with."""

    def __init__(self, results: list[dict] | None = None):
        self.results = list(results or [])
        self.invocations: list[dict] = []

    def invoke(self, state: dict) -> dict:
        self.invocations.append(state)
        return {**state, **(self.results.pop(0) if self.results else {})}

    def stream(self, state: dict, *, stream_mode=None):
        """The agent node's real-streaming path — one values frame, no chunks."""
        yield "values", self.invoke(state)


class TestChatSessionSend:
    def _session(self, results=None, state=None):
        graph = FakeGraph(results)
        qs = QuestionnaireState(intake_mode="smart", current_question=6)
        return ChatSession(graph, state if state is not None else {"questionnaire": qs}), graph

    def _events(self, session, text="two engineers", **kwargs):
        events: list = []
        assert session.send(text, events.append, **kwargs) is True
        return events

    def test_a_turn_streams_tokens_then_the_reply_then_done(self):
        session, _graph = self._session([{"messages": [AIMessage(content="What stack?")]}])
        events = self._events(session)
        assert all(isinstance(e, Token) for e in events[:-2])
        assert "".join(e.text for e in events if isinstance(e, Token)) == "What stack?"
        assert isinstance(events[-2], AskQuestion)
        assert isinstance(events[-1], Done)

    def test_the_text_is_appended_as_the_newest_human_message(self):
        session, graph = self._session()
        self._events(session, "two engineers")
        assert graph.invocations[0]["messages"][-1].content == "two engineers"

    def test_an_empty_send_invokes_without_adding_a_message(self):
        # The size switch re-enters the node with nothing to say.
        session, graph = self._session()
        self._events(session, "")
        assert graph.invocations[0]["messages"] == []

    def test_the_intake_gate_is_dropped_before_the_invoke(self):
        # pending_review is a LastValue channel: left set, it survives the
        # invoke, the gate never closes and the stage stays "intake" forever.
        qs = _done_qs()
        session, graph = self._session(state={"questionnaire": qs, "pending_review": "project_intake"})
        self._events(session, "accept")
        assert "pending_review" not in graph.invocations[0]
        assert "pending_review" not in session.state

    def test_images_ride_the_intake_channel_during_intake(self):
        session, graph = self._session()
        self._events(session, "like this", images=["shot-1.png"])
        assert graph.invocations[0]["pasted_images"] == ["shot-1.png"]
        assert "chat_images" not in graph.invocations[0]

    def test_images_ride_the_chat_channel_after_intake(self):
        state = {
            "questionnaire": _done_qs(completed=True, awaiting_confirmation=False),
            "project_analysis": _ANALYSIS,
            "_epic_reviewed": True,
            "features": [1],
            "stories": [1],
            "tasks": [1],
            "sprints": [1],
        }
        session, graph = self._session(state=state)
        self._events(session, "match this", images=["shot-1.png"])
        assert graph.invocations[0]["chat_images"] == ["shot-1.png"]
        assert "pasted_images" not in graph.invocations[0]

    def test_the_returned_state_replaces_the_session_state(self):
        session, _graph = self._session([{"project_analysis": _ANALYSIS}])
        self._events(session)
        assert session.state["project_analysis"] is _ANALYSIS

    def test_a_summary_turn_ends_on_the_confirmation_gate(self):
        qs = _done_qs()
        session, _graph = self._session(
            [{"messages": [AIMessage(content="# Everything I've got")], "questionnaire": qs}],
            state={"questionnaire": QuestionnaireState(intake_mode="smart", current_question=30)},
        )
        events = self._events(session, "the last answer")
        assert events[-2] == AwaitConfirm(kind="intake_summary", prompt=CONFIRM_VERDICT_PROMPT)

    def test_a_provider_error_propagates_for_the_caller_to_classify(self):
        class Boom(FakeGraph):
            def invoke(self, state):
                raise RuntimeError("provider said no")

        session = ChatSession(Boom(), {"questionnaire": QuestionnaireState(current_question=6)})
        before = session.state
        with pytest.raises(RuntimeError):
            session.send("hi", lambda _event: None)
        assert session.state is before  # nothing was merged


class TestChatSessionAwaiting:
    def test_awaiting_names_the_stage(self):
        session = ChatSession(None, {"pending_review": "story_writer"})
        assert session.awaiting == "review"

    def test_dry_run_sessions_carry_the_flag(self):
        session = ChatSession(None, {"capacity_override_target": -4}, dry_run=True)
        assert session.awaiting != "capacity"
