"""Tests for derive_question_view — behavior-identity with the old phase loop."""

from langchain_core.messages import AIMessage

from yeaboi.agent.state import QuestionnaireState
from yeaboi.prompts.intake import QUESTION_METADATA
from yeaboi.ui.session.chat._question_view import derive_question_view


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
        qs = QuestionnaireState()
        qs.current_question = 6
        view = derive_question_view(_state(qs))
        assert view.progress == "Q6 of 30"
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
