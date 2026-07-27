"""Tests for questionnaire import/export as Markdown."""

from pathlib import Path

import pytest

from yeaboi.agent.state import TOTAL_QUESTIONS, QuestionnaireState
from yeaboi.prompts.intake import QUESTION_DEFAULTS
from yeaboi.questionnaire_io import (
    ESSENTIAL_QUESTIONS,
    build_questionnaire_from_answers,
    export_questionnaire_md,
    parse_questionnaire_md,
)

# ── Helpers ────────────────────────────────────────────────────────


def _all_answers() -> dict[int, str]:
    """Return a complete set of answers for all 26 questions."""
    return {q: f"Answer for Q{q}" for q in range(1, TOTAL_QUESTIONS + 1)}


def _partial_answers() -> dict[int, str]:
    """Return answers for essential questions only (Q1-Q4, Q6, Q11, Q15)."""
    return {q: f"Answer for Q{q}" for q in sorted(ESSENTIAL_QUESTIONS)}


# ── Export tests ───────────────────────────────────────────────────


class TestExportQuestionnaireMd:
    def test_blank_template_has_all_questions(self, tmp_path):
        """Blank export should contain all 26 questions."""
        path = tmp_path / "blank.md"
        result = export_questionnaire_md(None, path)
        content = result.read_text()
        for q_num in range(1, TOTAL_QUESTIONS + 1):
            assert f"**Q{q_num}.**" in content

    def test_blank_template_has_empty_blockquotes(self, tmp_path):
        """Blank export should have empty `> ` lines for answers."""
        path = tmp_path / "blank.md"
        export_questionnaire_md(None, path)
        content = path.read_text()
        # Every question should have a `> ` line following it
        lines = content.splitlines()
        q_lines = [i for i, line in enumerate(lines) if line.startswith("**Q")]
        for idx in q_lines:
            # The next line should be an empty blockquote
            assert lines[idx + 1] == "> "

    def test_partial_answers_filled(self, tmp_path):
        """Export with partial answers should fill in provided answers."""
        qs = QuestionnaireState(answers={1: "My project", 6: "3 engineers"})
        path = tmp_path / "partial.md"
        export_questionnaire_md(qs, path)
        content = path.read_text()
        assert "> My project" in content
        assert "> 3 engineers" in content

    def test_full_answers_filled(self, tmp_path):
        """Export with all answers should fill in every question."""
        answers = _all_answers()
        qs = QuestionnaireState(answers=answers)
        path = tmp_path / "full.md"
        export_questionnaire_md(qs, path)
        content = path.read_text()
        for q_num, answer in answers.items():
            assert f"> {answer}" in content

    def test_phase_headers_present(self, tmp_path):
        """Export should include phase headers."""
        path = tmp_path / "phases.md"
        export_questionnaire_md(None, path)
        content = path.read_text()
        assert "## Phase 1: Project Context" in content
        assert "## Phase 2: Team & Capacity" in content
        assert "## Phase 3: Technical Context" in content
        assert "## Phase 3a: Codebase Context" in content
        assert "## Phase 4: Risks & Unknowns" in content
        assert "## Phase 5: Preferences & Process" in content

    def test_instructions_present(self, tmp_path):
        """Export should include filling instructions at the top."""
        path = tmp_path / "instructions.md"
        export_questionnaire_md(None, path)
        content = path.read_text()
        assert "Fill in your answers" in content
        assert "> " in content
        assert "skip" in content

    def test_essential_questions_listed(self, tmp_path):
        """Export should list essential questions in the instructions."""
        path = tmp_path / "essential.md"
        export_questionnaire_md(None, path)
        content = path.read_text()
        assert "Q1" in content
        assert "Essential" in content

    def test_returns_resolved_path(self, tmp_path):
        """Export should return the resolved path."""
        path = tmp_path / "result.md"
        result = export_questionnaire_md(None, path)
        assert result == path.resolve()
        assert result.exists()

    def test_multiline_answer_exported(self, tmp_path):
        """Multi-line answers should be exported with `> ` on each line."""
        qs = QuestionnaireState(answers={1: "Line one\nLine two\nLine three"})
        path = tmp_path / "multiline.md"
        export_questionnaire_md(qs, path)
        content = path.read_text()
        assert "> Line one\n> Line two\n> Line three" in content


# ── Parse tests ────────────────────────────────────────────────────


class TestParseQuestionnaireMd:
    def test_all_30_answers(self, tmp_path):
        """Parsing a fully-filled file should return all 30 answers."""
        answers = _all_answers()
        qs = QuestionnaireState(answers=answers)
        path = tmp_path / "full.md"
        export_questionnaire_md(qs, path)
        parsed = parse_questionnaire_md(path)
        assert len(parsed) == TOTAL_QUESTIONS
        for q_num, answer in answers.items():
            assert parsed[q_num] == answer

    def test_empty_answers_skipped(self, tmp_path):
        """Empty blockquote lines should not produce answers."""
        path = tmp_path / "blank.md"
        export_questionnaire_md(None, path)
        # Blank template has only empty `> ` lines — should raise because no answers
        with pytest.raises(ValueError, match="No valid"):
            parse_questionnaire_md(path)

    def test_skip_keyword_ignored(self, tmp_path):
        """The literal 'skip' answer should be treated as unanswered."""
        content = "**Q1.** What is the project?\n> skip\n\n**Q2.** Greenfield?\n> Yes, new build\n"
        path = tmp_path / "skip.md"
        path.write_text(content)
        parsed = parse_questionnaire_md(path)
        assert 1 not in parsed
        assert parsed[2] == "Yes, new build"

    def test_multiline_answers(self, tmp_path):
        """Multi-line blockquote answers should be joined with newlines."""
        content = "**Q1.** What is the project?\n> Line one\n> Line two\n> Line three\n"
        path = tmp_path / "multi.md"
        path.write_text(content)
        parsed = parse_questionnaire_md(path)
        assert parsed[1] == "Line one\nLine two\nLine three"

    def test_raises_on_empty_file(self, tmp_path):
        """An empty file should raise ValueError."""
        path = tmp_path / "empty.md"
        path.write_text("")
        with pytest.raises(ValueError, match="No valid"):
            parse_questionnaire_md(path)

    def test_partial_file(self, tmp_path):
        """A file with only some questions should return just those."""
        content = "**Q1.** What is the project?\n> My cool app\n\n**Q6.** How many engineers?\n> 5\n"
        path = tmp_path / "partial.md"
        path.write_text(content)
        parsed = parse_questionnaire_md(path)
        assert len(parsed) == 2
        assert parsed[1] == "My cool app"
        assert parsed[6] == "5"

    def test_invalid_question_numbers_ignored(self, tmp_path):
        """Question numbers outside 1-30 should be ignored."""
        content = "**Q0.** Invalid\n> bad\n\n**Q31.** Also invalid\n> bad\n\n**Q1.** Valid\n> good\n"
        path = tmp_path / "invalid.md"
        path.write_text(content)
        parsed = parse_questionnaire_md(path)
        assert len(parsed) == 1
        assert parsed[1] == "good"

    def test_file_not_found(self, tmp_path):
        """Non-existent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            parse_questionnaire_md(tmp_path / "nope.md")

    def test_bare_blockquote_line(self, tmp_path):
        """A bare `>` line (no space after) should be treated as empty line in multi-line."""
        content = "**Q1.** What is the project?\n> First line\n>\n> Third line\n"
        path = tmp_path / "bare.md"
        path.write_text(content)
        parsed = parse_questionnaire_md(path)
        assert parsed[1] == "First line\n\nThird line"


# ── Build tests ────────────────────────────────────────────────────


class TestBuildQuestionnaireFromAnswers:
    def test_all_answers_stored(self):
        """All provided answers should be stored."""
        answers = _all_answers()
        qs = build_questionnaire_from_answers(answers)
        assert len(qs.answers) == TOTAL_QUESTIONS
        for q_num, answer in answers.items():
            assert qs.answers[q_num] == answer

    def test_defaults_applied_for_missing(self):
        """Missing questions with defaults should get the default value."""
        # Only provide essential answers — rest should get defaults
        answers = _partial_answers()
        qs = build_questionnaire_from_answers(answers)
        for q_num, default in QUESTION_DEFAULTS.items():
            if q_num not in answers:
                assert qs.answers[q_num] == default
                assert q_num in qs.defaulted_questions

    def test_essential_marked_skipped(self):
        """Essential questions with no answer should be in skipped_questions."""
        # Empty answers — all essential questions should be skipped
        qs = build_questionnaire_from_answers({})
        for q_num in ESSENTIAL_QUESTIONS:
            assert q_num in qs.skipped_questions

    def test_awaiting_confirmation(self):
        """Result should have awaiting_confirmation=True."""
        qs = build_questionnaire_from_answers(_all_answers())
        assert qs.awaiting_confirmation is True

    def test_not_completed(self):
        """Result should have completed=False (user must confirm first)."""
        qs = build_questionnaire_from_answers(_all_answers())
        assert qs.completed is False

    def test_current_question_past_end(self):
        """current_question should be TOTAL_QUESTIONS + 1."""
        qs = build_questionnaire_from_answers(_all_answers())
        assert qs.current_question == TOTAL_QUESTIONS + 1

    def test_no_defaulted_when_all_provided(self):
        """When all answers are provided, defaulted_questions should be empty."""
        qs = build_questionnaire_from_answers(_all_answers())
        assert len(qs.defaulted_questions) == 0

    def test_no_skipped_when_all_provided(self):
        """When all answers are provided, skipped_questions should be empty."""
        qs = build_questionnaire_from_answers(_all_answers())
        assert len(qs.skipped_questions) == 0


# ── Round-trip tests ───────────────────────────────────────────────


class TestRoundTrip:
    def test_export_then_import_preserves_answers(self, tmp_path):
        """Exporting and then importing should produce the same answers."""
        original_answers = _all_answers()
        qs = QuestionnaireState(answers=original_answers)
        path = tmp_path / "roundtrip.md"
        export_questionnaire_md(qs, path)
        parsed = parse_questionnaire_md(path)
        assert parsed == original_answers

    def test_partial_roundtrip(self, tmp_path):
        """Partial answers should round-trip correctly."""
        original = {1: "My project", 6: "3 engineers", 11: "Python + FastAPI"}
        qs = QuestionnaireState(answers=original)
        path = tmp_path / "partial_rt.md"
        export_questionnaire_md(qs, path)
        parsed = parse_questionnaire_md(path)
        assert parsed == original

    def test_multiline_roundtrip(self, tmp_path):
        """Multi-line answers should round-trip correctly."""
        original = {1: "Line one\nLine two", 6: "5 engineers"}
        qs = QuestionnaireState(answers=original)
        path = tmp_path / "multi_rt.md"
        export_questionnaire_md(qs, path)
        parsed = parse_questionnaire_md(path)
        assert parsed == original


class TestSandboxGating:
    """Questionnaire import/export respect the filesystem sandbox."""

    def test_export_outside_whitelist_denied(self):
        from yeaboi.fs_policy import SandboxViolationError

        with pytest.raises(SandboxViolationError):
            export_questionnaire_md(None, Path("/denied-sandbox-dir/q.md"))

    def test_parse_outside_whitelist_denied(self):
        from yeaboi.fs_policy import SandboxViolationError

        with pytest.raises(SandboxViolationError):
            parse_questionnaire_md(Path("/denied-sandbox-dir/q.md"))
