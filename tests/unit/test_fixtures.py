"""Integration tests against the sample fixture files in tests/fixtures/.

These tests validate both fixture files are well-formed and exercise the actual
code paths used during a real agent run:

  SCRUM.md                      → _load_user_context() → analyzer prompt injection
  scrum-questionnaire-answers.md → parse_questionnaire_md() → build_questionnaire_from_answers()

# See docs: "Project Intake Questionnaire" — offline workflow
"""

from pathlib import Path

import pytest

from yeaboi.agent.nodes import _load_user_context
from yeaboi.prompts.analyzer import get_analyzer_prompt
from yeaboi.questionnaire_io import build_questionnaire_from_answers, parse_questionnaire_md

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SCRUM_MD = FIXTURES / "SCRUM.md"
QUESTIONNAIRE_MD = FIXTURES / "scrum-questionnaire-answers.md"


# ---------------------------------------------------------------------------
# TestScrumMdFixture — tests for tests/fixtures/SCRUM.md
# ---------------------------------------------------------------------------


class TestScrumMdFixture:
    """Validate the SCRUM.md fixture file is well-formed and loads correctly."""

    def test_fixture_file_exists(self):
        assert SCRUM_MD.exists(), f"Fixture missing: {SCRUM_MD}"

    def test_file_is_not_empty(self):
        assert SCRUM_MD.read_text().strip(), "SCRUM.md fixture is empty"

    def test_load_user_context_reads_file(self):
        """_load_user_context() should return the full file content when given a path."""
        result, status = _load_user_context(path=str(SCRUM_MD))
        assert result is not None
        assert len(result) > 100  # non-trivial content
        assert status["status"] == "success"

    def test_content_contains_expected_sections(self):
        content = SCRUM_MD.read_text()
        assert "## Background" in content
        assert "## Tech Decisions Already Made" in content
        assert "## Constraints" in content
        assert "## Out of Scope" in content

    def test_content_contains_project_name(self):
        """The fixture project (LendFlow) should be named in the file."""
        content = SCRUM_MD.read_text()
        assert "LendFlow" in content

    def test_content_contains_tech_stack(self):
        """Tech stack details should be present for rich analyzer context."""
        content = SCRUM_MD.read_text()
        assert "FastAPI" in content
        assert "PostgreSQL" in content
        assert "React" in content

    def test_content_contains_constraints(self):
        """Constraints block should include compliance and deadline info."""
        content = SCRUM_MD.read_text()
        assert "GDPR" in content or "FCA" in content
        assert "Q2" in content  # deadline reference

    def test_injected_into_analyzer_prompt(self):
        """SCRUM.md content should appear inside the analyzer prompt."""
        user_context, _status = _load_user_context(path=str(SCRUM_MD))
        prompt = get_analyzer_prompt("placeholder answers", 3, 15, user_context=user_context)
        assert "User Context (SCRUM.md / scrum-docs)" in prompt
        assert "LendFlow" in prompt

    def test_not_injected_when_absent(self):
        """When _load_user_context returns None the prompt section is omitted."""
        prompt = get_analyzer_prompt("placeholder answers", 3, 15, user_context=None)
        assert "User Context" not in prompt


# ---------------------------------------------------------------------------
# TestQuestionnaireFixture — tests for tests/fixtures/scrum-questionnaire-answers.md
# ---------------------------------------------------------------------------


class TestQuestionnaireFixture:
    """Validate the questionnaire fixture parses correctly end-to-end."""

    @pytest.fixture(scope="class")
    def parsed(self):
        return parse_questionnaire_md(QUESTIONNAIRE_MD)

    @pytest.fixture(scope="class")
    def questionnaire(self, parsed):
        return build_questionnaire_from_answers(parsed)

    def test_fixture_file_exists(self):
        assert QUESTIONNAIRE_MD.exists(), f"Fixture missing: {QUESTIONNAIRE_MD}"

    def test_parses_without_error(self, parsed):
        assert isinstance(parsed, dict)

    def test_all_essential_questions_answered(self, parsed):
        """Q1, Q2, Q3, Q4, Q6, Q11, Q15 must all have non-empty answers."""
        essential = [1, 2, 3, 4, 6, 11, 15]
        for q in essential:
            assert q in parsed, f"Essential question Q{q} is missing"
            assert parsed[q].strip(), f"Essential question Q{q} is blank"

    def test_answer_count_is_complete(self, parsed):
        """All 26 questions should be answered (none left blank or 'skip')."""
        assert len(parsed) >= 20, f"Expected most questions answered, got {len(parsed)}"

    def test_q1_describes_project(self, parsed):
        assert "LendFlow" in parsed[1] or "loan" in parsed[1].lower()

    def test_q2_is_hybrid(self, parsed):
        assert "hybrid" in parsed[2].lower() or "existing" in parsed[2].lower()

    def test_q6_engineer_count_is_numeric(self, parsed):
        """Q6 should state a number of engineers."""
        answer = parsed[6]
        assert any(char.isdigit() for char in answer), f"Q6 answer has no digit: {answer!r}"

    def test_q8_sprint_length(self, parsed):
        assert "week" in parsed[8].lower()

    def test_q11_tech_stack_present(self, parsed):
        answer = parsed[11]
        assert "FastAPI" in answer or "Python" in answer

    def test_q24_story_points_fibonacci(self, parsed):
        assert "fibonacci" in parsed[24].lower() or "story point" in parsed[24].lower()

    def test_build_questionnaire_returns_state(self, questionnaire):
        from yeaboi.agent.state import QuestionnaireState

        assert isinstance(questionnaire, QuestionnaireState)

    def test_questionnaire_answers_populated(self, questionnaire):
        assert len(questionnaire.answers) >= 20

    def test_questionnaire_q1_answer_set(self, questionnaire):
        assert questionnaire.answers.get(1)

    def test_questionnaire_awaiting_confirmation(self, questionnaire):
        """build_questionnaire_from_answers() leaves the state at the confirmation gate."""
        assert questionnaire.awaiting_confirmation is True

    def test_questionnaire_current_question_past_end(self, questionnaire):
        """current_question should be past 26 (all questions answered)."""
        assert questionnaire.current_question > 26
