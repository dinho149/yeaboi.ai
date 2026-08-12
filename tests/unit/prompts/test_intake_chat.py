"""Tests for the chat presentation layer in prompts/intake.py."""

from yeaboi.prompts.intake import (
    CHAT_MODE_HIDDEN_CHOICES,
    CHAT_QUESTION_PREAMBLES,
    CHAT_QUESTION_PREAMBLES_BY_MODE,
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

    def test_every_question_has_a_preamble(self):
        # Full coverage: the interview should read conversationally end to end,
        # and a question added without a lead-in should be a visible decision.
        assert set(CHAT_QUESTION_PREAMBLES) == set(INTAKE_QUESTIONS)

    def test_preambles_are_distinct_and_short(self):
        texts = list(CHAT_QUESTION_PREAMBLES.values())
        assert len(set(texts)) == len(texts)  # 30 identical "Now…"s is a form again
        assert all(len(t) <= 60 for t in texts)


class TestModeAwarePreambles:
    def test_q10_smart_mode_acknowledges_size(self):
        text = INTAKE_QUESTIONS[10]
        decorated = decorate_question_for_chat(10, text, intake_mode="smart")
        assert decorated.startswith(CHAT_QUESTION_PREAMBLES_BY_MODE[(10, "smart")])
        assert decorated.endswith(text)

    def test_q8_small_mode_acknowledges_size(self):
        text = INTAKE_QUESTIONS[8]
        decorated = decorate_question_for_chat(8, text, intake_mode="small_project")
        assert decorated.startswith(CHAT_QUESTION_PREAMBLES_BY_MODE[(8, "small_project")])
        assert decorated.endswith(text)

    def test_wrong_mode_falls_back_to_base_preamble(self):
        # Q10 has no small_project override, Q8 no smart override — base wins.
        assert decorate_question_for_chat(10, INTAKE_QUESTIONS[10], intake_mode="small_project").startswith(
            CHAT_QUESTION_PREAMBLES[10]
        )
        assert decorate_question_for_chat(8, INTAKE_QUESTIONS[8], intake_mode="smart").startswith(
            CHAT_QUESTION_PREAMBLES[8]
        )

    def test_no_mode_is_unchanged(self):
        text = INTAKE_QUESTIONS[10]
        assert decorate_question_for_chat(10, text) == decorate_question_for_chat(10, text, intake_mode=None)

    def test_mode_preambles_are_distinct_and_short(self):
        texts = list(CHAT_QUESTION_PREAMBLES_BY_MODE.values())
        assert len(set(texts)) == len(texts)
        assert all(len(t) <= 60 for t in texts)
        # Must differ from the base preamble they replace — otherwise the
        # override is dead weight.
        for (q_num, _mode), text in CHAT_QUESTION_PREAMBLES_BY_MODE.items():
            assert text != CHAT_QUESTION_PREAMBLES[q_num]

    def test_mode_keys_use_the_real_vocabulary(self):
        # A typo'd mode ("large", "small") would produce an entry that
        # silently never fires; and an override for a question outside the
        # mode's essential set can never be reached in that mode.
        from yeaboi.prompts.intake import QUICK_ESSENTIALS, SMALL_PROJECT_ESSENTIALS, SMART_ESSENTIALS

        essentials = {
            "smart": SMART_ESSENTIALS,
            "quick": QUICK_ESSENTIALS,
            "small_project": SMALL_PROJECT_ESSENTIALS,
        }
        for q_num, mode in list(CHAT_QUESTION_PREAMBLES_BY_MODE) + list(CHAT_MODE_HIDDEN_CHOICES):
            assert mode in essentials, f"unknown intake mode {mode!r}"
            assert q_num in essentials[mode], f"Q{q_num} is not essential in {mode!r} — the override never fires"

    def test_hidden_choices_reference_real_canonical_options(self):
        # A hidden label that drifts from meta.options would silently hide
        # nothing — pin every entry to the canonical tuple.
        for (q_num, _mode), labels in CHAT_MODE_HIDDEN_CHOICES.items():
            meta = QUESTION_METADATA[q_num]
            for label in labels:
                assert label in meta.options
            # Hiding must never empty the menu.
            assert len(labels) < len(meta.options)
