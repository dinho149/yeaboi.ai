"""Tests for the chat presentation layer in prompts/intake.py."""

from yeaboi.prompts.intake import (
    CHAT_QUESTION_PREAMBLES,
    INTAKE_QUESTIONS,
    QUESTION_METADATA,
    decorate_question_for_chat,
)


class TestDecorateQuestionForChat:
    def test_known_question_gets_preamble(self):
        text = INTAKE_QUESTIONS[6]
        decorated = decorate_question_for_chat(6, text)
        assert decorated.endswith(text)
        assert decorated.startswith(CHAT_QUESTION_PREAMBLES[6])

    def test_unknown_question_is_identity(self):
        text = "Some question text?"
        assert decorate_question_for_chat(99, text) == text

    def test_never_alters_choice_option_lines(self):
        # Choice pre-selection matches against the option lines — decoration
        # must leave every [n] line byte-identical for all decorated questions.
        for q_num in CHAT_QUESTION_PREAMBLES:
            meta = QUESTION_METADATA.get(q_num)
            if meta is None or not meta.options:
                continue
            text = "\n".join(f"[{i}] {opt}" for i, opt in enumerate(meta.options, 1))
            decorated = decorate_question_for_chat(q_num, text)
            assert decorated.endswith(text)

    def test_preambles_only_name_real_questions(self):
        assert set(CHAT_QUESTION_PREAMBLES) <= set(INTAKE_QUESTIONS)
