"""Tests for derive_question_view — behavior-identity with the old phase loop."""

from langchain_core.messages import AIMessage

from yeaboi.agent.state import QuestionnaireState
from yeaboi.prompts.intake import QUESTION_METADATA, SMALL_PROJECT_ESSENTIALS, SMART_ESSENTIALS, AnswerSource
from yeaboi.ui.session.chat._question_view import (
    CONFIRM_ACCEPT,
    CONFIRM_EDIT,
    CONFIRM_FREETEXT,
    CONFIRM_OVERRIDE_VELOCITY,
    derive_question_view,
    planned_question_progress,
)


def _state(qs: QuestionnaireState, ai_text: str = "What is your team size?") -> dict:
    return {"messages": [AIMessage(content=ai_text)], "questionnaire": qs}


class TestBasics:
    def test_no_questionnaire_returns_text_only(self):
        view = derive_question_view({"messages": [AIMessage(content="Tell me more")]})
        assert view.question_text == "Tell me more"
        assert view.choices is None
        assert view.progress == ""

    def test_completed_questionnaire_no_choices(self):
        qs = QuestionnaireState(completed=True)
        view = derive_question_view(_state(qs))
        assert view.choices is None

    def test_progress_and_phase(self):
        # Fresh questionnaire, standard/smart mode: nothing answered by the
        # user yet, so the honest count is position 1 over the essential set —
        # not "Q6 of 30", which counts the whole bank.
        qs = QuestionnaireState()
        qs.current_question = 6
        view = derive_question_view(_state(qs))
        assert view.progress == f"Question 1 of {len(SMART_ESSENTIALS)}"
        assert view.current_question == 6


class TestStaticChoices:
    def test_single_choice_lists_options(self):
        qs = QuestionnaireState()
        qs.current_question = 2  # Q2 is single-choice (no static default)
        view = derive_question_view(_state(qs))
        meta = QUESTION_METADATA[2]
        assert view.choices is not None
        assert [label for label, _ in view.choices] == list(meta.options)
        assert view.multi_select is False
        selected = [i for i, (_l, sel) in enumerate(view.choices) if sel]
        expected = [] if meta.default_index is None else [meta.default_index]
        assert selected == expected

    def test_suggestion_overrides_default_preselection(self):
        qs = QuestionnaireState()
        qs.current_question = 2
        option = QUESTION_METADATA[2].options[1]
        qs.suggested_answers = {2: option}
        view = derive_question_view(_state(qs))
        selected = [i for i, (_l, sel) in enumerate(view.choices) if sel]
        assert selected == [1]
        assert view.suggestion is None  # merged into the pre-selection

    def test_probed_question_gets_no_static_choices(self):
        qs = QuestionnaireState()
        qs.current_question = 2
        qs.probed_questions = {2}
        view = derive_question_view(_state(qs))
        assert view.choices is None

    def test_mode_hidden_row_dropped_in_smart_mode(self):
        # Q10's "1–2 sprints" re-litigates the size answer — hidden in chat
        # when the user already chose Large (intake_mode="smart").
        qs = QuestionnaireState()
        qs.current_question = 10
        qs.intake_mode = "smart"
        view = derive_question_view(_state(qs))
        labels = [label for label, _ in view.choices]
        assert "1–2 sprints" not in labels
        assert labels == [opt for opt in QUESTION_METADATA[10].options if opt != "1–2 sprints"]

    def test_mode_hidden_row_keeps_default_preselection(self):
        qs = QuestionnaireState()
        qs.current_question = 10
        qs.intake_mode = "smart"
        view = derive_question_view(_state(qs))
        meta = QUESTION_METADATA[10]
        # Precondition, not a guard — a silent pass if Q10 loses its default
        # would make this test vacuous.
        assert meta.default_index is not None
        selected = [label for label, sel in view.choices if sel]
        assert selected == [meta.options[meta.default_index]]

    def test_mode_hidden_row_shown_outside_smart_mode(self):
        for mode in ("small_project", ""):
            qs = QuestionnaireState()
            qs.current_question = 10
            qs.intake_mode = mode
            view = derive_question_view(_state(qs))
            assert [label for label, _ in view.choices] == list(QUESTION_METADATA[10].options)

    def test_mode_hidden_row_survives_when_suggested(self):
        # If the extractor pulled "1–2 sprints" out of the description, the
        # pre-selected row must stay visible — hiding it would drop the
        # suggestion silently.
        qs = QuestionnaireState()
        qs.current_question = 10
        qs.intake_mode = "smart"
        qs.suggested_answers = {10: "1–2 sprints"}
        view = derive_question_view(_state(qs))
        assert ("1–2 sprints", True) in view.choices

    def test_mode_filter_leaves_other_questions_alone(self):
        qs = QuestionnaireState()
        qs.current_question = 2
        qs.intake_mode = "smart"
        view = derive_question_view(_state(qs))
        assert [label for label, _ in view.choices] == list(QUESTION_METADATA[2].options)

    def test_multi_choice_preselects_from_comma_suggestion(self):
        multi_q = next(q for q, m in QUESTION_METADATA.items() if m.question_type == "multi_choice")
        meta = QUESTION_METADATA[multi_q]
        qs = QuestionnaireState()
        qs.current_question = multi_q
        qs.suggested_answers = {multi_q: f"{meta.options[0]}, {meta.options[2]}"}
        view = derive_question_view(_state(qs))
        assert view.multi_select is True
        selected = {i for i, (_l, sel) in enumerate(view.choices) if sel}
        assert selected == {0, 2}


class TestDynamicChoices:
    def test_follow_up_choices_override_static(self):
        qs = QuestionnaireState()
        qs.current_question = 6
        qs._follow_up_choices = {6: ("Alice", "Bob", "Cara")}
        view = derive_question_view(_state(qs))
        assert [label for label, _ in view.choices] == ["Alice", "Bob", "Cara"]
        assert view.multi_select is True  # Q6 member selection is multi

    def test_q27_sprint_selection_is_single_select(self):
        qs = QuestionnaireState()
        qs.current_question = 27
        qs._follow_up_choices = {27: ("Sprint 4", "Sprint 5")}
        view = derive_question_view(_state(qs))
        assert view.multi_select is False

    def test_tracker_choice_is_single_select(self):
        qs = QuestionnaireState()
        qs.current_question = 1
        qs._awaiting_tracker_choice = True
        qs._follow_up_choices = {1: ("Jira", "Azure DevOps")}
        view = derive_question_view(_state(qs))
        assert view.multi_select is False


class TestPtoSubLoop:
    def test_pto_prompt_suppresses_choices(self):
        # PTO sets current_question to a choice question but shows its own
        # Yes/No prompt — static choices must not render.
        qs = QuestionnaireState()
        qs.current_question = 28
        qs._awaiting_leave_input = True
        view = derive_question_view(_state(qs, "Does anyone have planned leave?\n\n[1] Yes\n[2] No"))
        assert view.choices is None


class TestConfirmationChoices:
    """The summary's verdict is a pick: Accept / Edit / (Override velocity) /
    Tell-me rows appear exactly when _append_reply shows the card — and never
    during the PTO sub-loop, velocity number entry, or an edit re-ask."""

    def _confirm_qs(self, intake_mode: str = "standard") -> QuestionnaireState:
        qs = QuestionnaireState(intake_mode=intake_mode)
        qs.awaiting_confirmation = True
        qs.current_question = 31
        return qs

    def test_confirmation_offers_the_verdict_rows(self):
        view = derive_question_view(_state(self._confirm_qs(), "Here is the summary."))
        labels = [label for label, _sel in view.choices]
        assert labels == [CONFIRM_ACCEPT, CONFIRM_EDIT, CONFIRM_OVERRIDE_VELOCITY, CONFIRM_FREETEXT]
        assert view.choices[0][1] is True  # Accept pre-highlighted
        assert view.auto_submit is True
        assert view.multi_select is False

    def test_small_mode_omits_the_velocity_row(self):
        view = derive_question_view(_state(self._confirm_qs("small_project"), "Summary."))
        labels = [label for label, _sel in view.choices]
        assert labels == [CONFIRM_ACCEPT, CONFIRM_EDIT, CONFIRM_FREETEXT]

    def test_velocity_number_entry_suppresses_choices(self):
        qs = self._confirm_qs()
        qs._awaiting_velocity_input = True
        view = derive_question_view(_state(qs, "Enter your velocity (pts/sprint):"))
        assert view.choices is None

    def test_edit_reask_suppresses_choices(self):
        qs = self._confirm_qs()
        qs.editing_question = 6
        view = derive_question_view(_state(qs, "Enter your new answer:"))
        assert view.choices is None

    def test_pto_subloop_suppresses_choices(self):
        qs = self._confirm_qs()
        qs._awaiting_leave_input = True
        view = derive_question_view(_state(qs, "Does anyone have planned leave?"))
        assert view.choices is None


class TestPlannedProgress:
    """planned_question_progress counts the questions actually planned for
    THIS run (user-answered + essential gaps), not the 30-question bank —
    the same gap function the node paces the flow with."""

    def test_direct_answer_advances_position_and_holds_total(self):
        # Q3 has no CONDITIONAL_ESSENTIALS dependent, so answering it moves
        # the position without growing the plan.
        qs = QuestionnaireState()
        qs.answers[3] = "solve scheduling chaos"
        qs.answer_sources[3] = AnswerSource.DIRECT
        assert planned_question_progress(qs) == (2, len(SMART_ESSENTIALS))

    def test_conditional_promotion_grows_the_total(self):
        # A real (non-defaulted) Q6 answer promotes Q7 (team roles) into the
        # plan: position advances AND the total grows by one — honesty over
        # a frozen count.
        qs = QuestionnaireState()
        qs.answers[6] = "4 engineers"
        qs.answer_sources[6] = AnswerSource.DIRECT
        assert planned_question_progress(qs) == (2, len(SMART_ESSENTIALS) + 1)

    def test_extracted_answers_do_not_count_as_asked(self):
        # Extraction filling Q3 removes it from the gaps but it was never
        # asked — position must stay 1 and the total shrink.
        qs = QuestionnaireState()
        qs.answers[3] = "from the description"
        qs.answer_sources[3] = AnswerSource.EXTRACTED
        assert planned_question_progress(qs) == (1, len(SMART_ESSENTIALS) - 1)

    def test_small_mode_uses_its_leaner_essential_set(self):
        qs = QuestionnaireState(intake_mode="small_project")
        assert planned_question_progress(qs) == (1, len(SMALL_PROJECT_ESSENTIALS))
