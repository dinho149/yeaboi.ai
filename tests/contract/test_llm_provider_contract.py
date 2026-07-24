"""Contract tests for LLM provider response parsing and error handling.

These tests verify that the JSON parsing pipeline correctly handles realistic
response formats from each supported LLM provider (Claude, GPT-4o, Gemini).
Each provider wraps JSON differently — Claude uses ```json fences, GPT-4o
sometimes returns bare JSON, Gemini may add preamble text — and our parsers
must handle all variants.

Also tests the off-topic classifier with each provider's cheap model and
provider-specific error types (401, 429, 529).

# See docs: "Testing — Contract Tests" for background.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from yeaboi.agent.nodes import (
    _extract_answers_from_description,
    _parse_analysis_response,
    _parse_features_response,
    _parse_sprints_response,
    _parse_stories_response,
    _parse_tasks_response,
)
from yeaboi.agent.state import (
    AcceptanceCriterion,
    Discipline,
    Feature,
    Priority,
    ProjectAnalysis,
    QuestionnaireState,
    StoryPointValue,
    UserStory,
)
from yeaboi.input_guardrails import check_off_topic

# ---------------------------------------------------------------------------
# Shared test data — realistic LLM JSON payloads matching actual schemas
# ---------------------------------------------------------------------------

_ANALYSIS_DICT = {
    "project_name": "TaskFlow",
    "project_description": "A collaborative task management platform",
    "project_type": "web_application",
    "goals": ["Real-time collaboration", "Kanban boards", "Sprint tracking"],
    "end_users": ["Development teams", "Project managers"],
    "target_state": "Production-ready SaaS platform",
    "tech_stack": ["React", "TypeScript", "Python", "FastAPI", "PostgreSQL"],
    "integrations": ["Slack", "GitHub", "Jira"],
    "constraints": ["SOC 2 compliance", "GDPR data residency"],
    "sprint_length_weeks": 2,
    "target_sprints": 4,
    "risks": ["Third-party API rate limits", "Real-time sync complexity"],
    "out_of_scope": ["Mobile app", "Self-hosted version"],
    "assumptions": ["Team has React experience", "PostgreSQL is already provisioned"],
    "scrum_md_contributions": [],
}

# Features use: id, title, description, priority
_FEATURES_LIST = [
    {"id": "F1", "title": "User Auth & Authorization", "description": "OAuth2, RBAC, SSO", "priority": "critical"},
    {"id": "F2", "title": "Task Management Core", "description": "CRUD, Kanban, drag-drop", "priority": "high"},
    {"id": "F3", "title": "Real-time Collaboration", "description": "WebSocket sync", "priority": "medium"},
]

# Stories use: id, feature_id, persona, goal, benefit, story_points, priority,
# acceptance_criteria (list of {given, when, then})
_STORIES_LIST = [
    {
        "id": "US-1",
        "feature_id": "F1",
        "persona": "new user",
        "goal": "register with my email",
        "benefit": "I can access the platform",
        "story_points": 3,
        "priority": "critical",
        "acceptance_criteria": [
            {"given": "a valid email", "when": "I submit registration", "then": "I receive a confirmation email"},
            {"given": "an existing email", "when": "I submit registration", "then": "I see an error message"},
        ],
    },
    {
        "id": "US-2",
        "feature_id": "F2",
        "persona": "team member",
        "goal": "create and edit tasks",
        "benefit": "I can track my work",
        "story_points": 5,
        "priority": "high",
        "acceptance_criteria": [
            {"given": "I am logged in", "when": "I create a task", "then": "it appears on my board"},
        ],
    },
]

# Tasks use: id, story_id, title, description
_TASKS_LIST = [
    {
        "id": "T-1",
        "story_id": "US-1",
        "title": "Set up FastAPI project structure",
        "description": "Scaffold FastAPI app with auth module",
    },
    {
        "id": "T-2",
        "story_id": "US-1",
        "title": "Implement email validation endpoint",
        "description": "POST /register with email verification",
    },
    {
        "id": "T-3",
        "story_id": "US-2",
        "title": "Build task CRUD API",
        "description": "REST endpoints for task CRUD",
    },
]

# Sprints use: id, name, goal, capacity_points, story_ids
_SPRINTS_LIST = [
    {
        "id": "SP-1",
        "name": "Sprint 1",
        "goal": "Authentication foundation",
        "capacity_points": 3,
        "story_ids": ["US-1"],
    },
    {
        "id": "SP-2",
        "name": "Sprint 2",
        "goal": "Task management core",
        "capacity_points": 5,
        "story_ids": ["US-2"],
    },
]


# ---------------------------------------------------------------------------
# Helpers — build minimal real dataclass instances
# ---------------------------------------------------------------------------


def _make_analysis() -> ProjectAnalysis:
    return ProjectAnalysis(
        project_name="TaskFlow",
        project_description="A task management platform",
        project_type="web_application",
        goals=("Real-time collaboration",),
        end_users=("Development teams",),
        target_state="Production SaaS",
        tech_stack=("React", "Python"),
        integrations=("Slack",),
        constraints=(),
        sprint_length_weeks=2,
        target_sprints=4,
        risks=(),
        out_of_scope=(),
        assumptions=(),
        scrum_md_contributions=(),
    )


def _make_questionnaire() -> QuestionnaireState:
    return QuestionnaireState(
        current_question=1,
        answers={1: "A task management platform"},
    )


def _make_features() -> list[Feature]:
    return [
        Feature(id="F1", title="Auth", description="Authentication", priority=Priority.CRITICAL),
        Feature(id="F2", title="Tasks", description="Task management", priority=Priority.HIGH),
    ]


def _make_stories() -> list[UserStory]:
    return [
        UserStory(
            id="US-1",
            feature_id="F1",
            persona="new user",
            goal="register with email",
            benefit="access the platform",
            acceptance_criteria=(AcceptanceCriterion(given="valid email", when="submit", then="registered"),),
            story_points=StoryPointValue.THREE,
            priority=Priority.CRITICAL,
            discipline=Discipline.BACKEND,
        ),
        UserStory(
            id="US-2",
            feature_id="F2",
            persona="team member",
            goal="create tasks",
            benefit="track work",
            acceptance_criteria=(AcceptanceCriterion(given="logged in", when="create task", then="task appears"),),
            story_points=StoryPointValue.FIVE,
            priority=Priority.HIGH,
            discipline=Discipline.FULLSTACK,
        ),
    ]


# ---------------------------------------------------------------------------
# Provider-specific response wrappers
# ---------------------------------------------------------------------------


def _claude_json(data: object) -> str:
    """Claude-style: wraps JSON in ```json fences."""
    return f"```json\n{json.dumps(data, indent=2)}\n```"


def _gpt4o_json(data: object) -> str:
    """GPT-4o-style: bare JSON, no fences."""
    return json.dumps(data, indent=2)


def _gemini_bare_json(data: object) -> str:
    """Gemini-style: bare JSON with trailing newlines."""
    return json.dumps(data) + "\n\n"


# ---------------------------------------------------------------------------
# Claude (Anthropic) — JSON response parsing
# ---------------------------------------------------------------------------


class TestClaudeResponseParsing:
    """Contract: Claude's ```json fenced responses parse correctly at every pipeline stage."""

    def test_analysis_json_with_fences(self):
        raw = _claude_json(_ANALYSIS_DICT)
        result = _parse_analysis_response(raw, _make_questionnaire(), team_size=5, velocity=20)

        assert result.project_name == "TaskFlow"
        assert "React" in result.tech_stack
        assert result.sprint_length_weeks == 2
        assert result.target_sprints == 4
        assert len(result.goals) == 3

    def test_features_json_with_fences(self):
        raw = _claude_json(_FEATURES_LIST)
        result = _parse_features_response(raw, _make_analysis())

        assert len(result) == 3
        assert result[0].id == "F1"
        assert result[0].priority == Priority.CRITICAL

    def test_stories_json_with_fences(self):
        raw = _claude_json(_STORIES_LIST)
        result = _parse_stories_response(raw, _make_features(), _make_analysis())

        assert len(result) == 2
        assert result[0].id == "US-1"
        assert result[0].story_points == StoryPointValue.THREE
        assert len(result[0].acceptance_criteria) == 2

    def test_tasks_json_with_fences(self):
        raw = _claude_json(_TASKS_LIST)
        result = _parse_tasks_response(raw, _make_stories())

        assert len(result) == 3
        assert result[0].story_id == "US-1"
        assert result[0].title == "Set up FastAPI project structure"

    def test_sprints_json_with_fences(self):
        raw = _claude_json(_SPRINTS_LIST)
        result = _parse_sprints_response(raw, _make_stories(), velocity=20)

        assert len(result) >= 1
        assert result[0].name == "Sprint 1"


# ---------------------------------------------------------------------------
# GPT-4o (OpenAI) — JSON response parsing
# ---------------------------------------------------------------------------


class TestOpenAIResponseParsing:
    """Contract: GPT-4o's bare JSON responses parse correctly at every pipeline stage."""

    def test_analysis_bare_json(self):
        raw = _gpt4o_json(_ANALYSIS_DICT)
        result = _parse_analysis_response(raw, _make_questionnaire(), team_size=5, velocity=20)

        assert result.project_name == "TaskFlow"
        assert "Python" in result.tech_stack

    def test_features_bare_json(self):
        raw = _gpt4o_json(_FEATURES_LIST)
        result = _parse_features_response(raw, _make_analysis())

        assert len(result) == 3
        assert result[1].title == "Task Management Core"

    def test_stories_bare_json(self):
        raw = _gpt4o_json(_STORIES_LIST)
        result = _parse_stories_response(raw, _make_features(), _make_analysis())

        assert len(result) == 2
        assert result[1].feature_id == "F2"

    def test_tasks_bare_json(self):
        raw = _gpt4o_json(_TASKS_LIST)
        result = _parse_tasks_response(raw, _make_stories())

        assert len(result) == 3

    def test_sprints_bare_json(self):
        raw = _gpt4o_json(_SPRINTS_LIST)
        result = _parse_sprints_response(raw, _make_stories(), velocity=20)

        assert len(result) >= 1


# ---------------------------------------------------------------------------
# Gemini (Google) — JSON response parsing
# ---------------------------------------------------------------------------


class TestGeminiResponseParsing:
    """Contract: Gemini's bare JSON with trailing whitespace parses correctly."""

    def test_analysis_bare_json(self):
        raw = _gemini_bare_json(_ANALYSIS_DICT)
        result = _parse_analysis_response(raw, _make_questionnaire(), team_size=5, velocity=20)

        assert result.project_name == "TaskFlow"
        assert len(result.integrations) == 3

    def test_features_bare_json(self):
        raw = _gemini_bare_json(_FEATURES_LIST)
        result = _parse_features_response(raw, _make_analysis())

        assert len(result) == 3
        assert result[2].priority == Priority.MEDIUM

    def test_stories_bare_json(self):
        raw = _gemini_bare_json(_STORIES_LIST)
        result = _parse_stories_response(raw, _make_features(), _make_analysis())

        assert len(result) == 2

    def test_tasks_bare_json(self):
        raw = _gemini_bare_json(_TASKS_LIST)
        result = _parse_tasks_response(raw, _make_stories())

        assert len(result) == 3

    def test_sprints_bare_json(self):
        raw = _gemini_bare_json(_SPRINTS_LIST)
        result = _parse_sprints_response(raw, _make_stories(), velocity=20)

        assert len(result) >= 1


# ---------------------------------------------------------------------------
# Off-topic classifier — each provider's cheap model
# ---------------------------------------------------------------------------


class TestOffTopicClassifierContract:
    """Contract: off-topic classifier handles responses from each provider's cheap model."""

    def _mock_llm_response(self, content: str) -> MagicMock:
        """Create a mock LLM that returns a fixed response."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = content
        mock_llm.invoke.return_value = mock_response
        return mock_llm

    @patch("yeaboi.agent.llm.get_llm")
    @patch("yeaboi.config.get_llm_provider")
    def test_anthropic_haiku_off_topic(self, mock_provider, mock_get_llm):
        """Haiku returns 'OFF_TOPIC' → input blocked with redirect message."""
        mock_provider.return_value = "anthropic"
        mock_get_llm.return_value = self._mock_llm_response("OFF_TOPIC")

        result = check_off_topic("tell me a joke")
        assert result is not None
        assert "project-related" in result.lower()

    @patch("yeaboi.agent.llm.get_llm")
    @patch("yeaboi.config.get_llm_provider")
    def test_openai_mini_off_topic(self, mock_provider, mock_get_llm):
        """gpt-4o-mini returns 'OFF_TOPIC' → blocked."""
        mock_provider.return_value = "openai"
        mock_get_llm.return_value = self._mock_llm_response("OFF_TOPIC")

        result = check_off_topic("what is love")
        assert result is not None

    @patch("yeaboi.agent.llm.get_llm")
    @patch("yeaboi.config.get_llm_provider")
    def test_google_flash_off_topic(self, mock_provider, mock_get_llm):
        """Gemini Flash returns 'OFF_TOPIC' → blocked."""
        mock_provider.return_value = "google"
        mock_get_llm.return_value = self._mock_llm_response("OFF_TOPIC")

        result = check_off_topic("sing me a song")
        assert result is not None

    @patch("yeaboi.agent.llm.get_llm")
    @patch("yeaboi.config.get_llm_provider")
    def test_relevant_input_allowed(self, mock_provider, mock_get_llm):
        """LLM returns 'RELEVANT' → input passes through (None returned)."""
        mock_provider.return_value = "anthropic"
        mock_get_llm.return_value = self._mock_llm_response("RELEVANT")

        result = check_off_topic("tell me a joke")
        assert result is None

    @patch("yeaboi.agent.llm.get_llm")
    @patch("yeaboi.config.get_llm_provider")
    def test_classifier_error_fails_open(self, mock_provider, mock_get_llm):
        """If the classifier LLM raises an exception, input is allowed (fail-open)."""
        mock_provider.return_value = "anthropic"
        mock_get_llm.side_effect = Exception("API overloaded")

        result = check_off_topic("random gibberish xyz")
        assert result is None  # Fail open — system prompt is safety net


# ---------------------------------------------------------------------------
# Error responses — 401, 429, 529
# ---------------------------------------------------------------------------


class TestLLMErrorResponsesContract:
    """Contract: LLM API error types are properly structured and catchable."""

    def test_401_authentication_error(self):
        """anthropic.AuthenticationError is a distinct, catchable exception type."""
        err = anthropic.AuthenticationError(
            message="Invalid API Key",
            response=MagicMock(status_code=401, headers={}),
            body={"error": {"message": "Invalid API Key"}},
        )
        assert "Invalid API Key" in str(err)

    def test_429_rate_limit_error(self):
        """anthropic.RateLimitError is a distinct, catchable exception type."""
        err = anthropic.RateLimitError(
            message="Rate limit exceeded",
            response=MagicMock(status_code=429, headers={}),
            body={"error": {"message": "Rate limit exceeded"}},
        )
        assert isinstance(err, anthropic.RateLimitError)

    def test_529_overloaded_error(self):
        """529 Overloaded is an APIStatusError with status_code=529."""
        resp = MagicMock(status_code=529, headers={})
        err = anthropic.APIStatusError(
            message="Overloaded",
            response=resp,
            body={"error": {"message": "Overloaded"}},
        )
        assert isinstance(err, anthropic.APIStatusError)
        assert err.response.status_code == 529

    def test_error_hierarchy(self):
        """All Anthropic API errors inherit from anthropic.APIError."""
        assert issubclass(anthropic.AuthenticationError, anthropic.APIStatusError)
        assert issubclass(anthropic.RateLimitError, anthropic.APIStatusError)
        assert issubclass(anthropic.APIStatusError, anthropic.APIError)
        assert issubclass(anthropic.APIConnectionError, anthropic.APIError)


# ---------------------------------------------------------------------------
# Extract answers — LLM returns JSON mapping question numbers to answers
# ---------------------------------------------------------------------------


class TestExtractAnswersContract:
    """Contract: _extract_answers_from_description parses each provider's JSON format."""

    @patch("yeaboi.agent.nodes.get_llm")
    def test_claude_fenced_json_extracted(self, mock_get_llm):
        """Claude returns fenced JSON with extracted answers."""
        mock_response = MagicMock()
        answers = {"1": "Task management platform", "6": "5 engineers", "11": "React, Python"}
        mock_response.content = _claude_json(answers)
        mock_get_llm.return_value.invoke.return_value = mock_response

        result = _extract_answers_from_description("Building a task management platform")

        assert result[1] == "Task management platform"
        assert result[6] == "5 engineers"
        assert result[11] == "React, Python"

    @patch("yeaboi.agent.nodes.get_llm")
    def test_gpt4o_bare_json_extracted(self, mock_get_llm):
        """GPT-4o returns bare JSON."""
        mock_response = MagicMock()
        mock_response.content = _gpt4o_json({"1": "E-commerce site", "8": "2 weeks"})
        mock_get_llm.return_value.invoke.return_value = mock_response

        result = _extract_answers_from_description("Building an e-commerce site with 2-week sprints")

        assert result[1] == "E-commerce site"
        assert result[8] == "2 weeks"

    @patch("yeaboi.agent.nodes.get_llm")
    def test_gemini_bare_json_extracted(self, mock_get_llm):
        """Gemini returns bare JSON with trailing whitespace."""
        mock_response = MagicMock()
        mock_response.content = _gemini_bare_json({"1": "Mobile banking app", "2": "greenfield"})
        mock_get_llm.return_value.invoke.return_value = mock_response

        result = _extract_answers_from_description("New mobile banking application from scratch")

        assert result[1] == "Mobile banking app"
        assert result[2] == "greenfield"

    @patch("yeaboi.agent.nodes.get_llm")
    def test_llm_failure_returns_empty_dict(self, mock_get_llm):
        """If the LLM raises, extraction returns {} gracefully."""
        mock_get_llm.return_value.invoke.side_effect = Exception("API down")

        result = _extract_answers_from_description("Some project description")

        assert result == {}


# ---------------------------------------------------------------------------
# Verify all providers return parseable JSON for each pipeline stage
# ---------------------------------------------------------------------------


class TestAllProvidersParseableJSON:
    """Contract: realistic JSON from every provider parses into valid artifacts at every stage."""

    @pytest.mark.parametrize(
        "wrapper",
        [_claude_json, _gpt4o_json, _gemini_bare_json],
        ids=["claude", "gpt4o", "gemini"],
    )
    def test_analysis_parses_for_all_providers(self, wrapper):
        raw = wrapper(_ANALYSIS_DICT)
        result = _parse_analysis_response(raw, _make_questionnaire(), team_size=5, velocity=20)
        assert result.project_name == "TaskFlow"

    @pytest.mark.parametrize(
        "wrapper",
        [_claude_json, _gpt4o_json, _gemini_bare_json],
        ids=["claude", "gpt4o", "gemini"],
    )
    def test_features_parse_for_all_providers(self, wrapper):
        raw = wrapper(_FEATURES_LIST)
        result = _parse_features_response(raw, _make_analysis())
        assert len(result) == 3

    @pytest.mark.parametrize(
        "wrapper",
        [_claude_json, _gpt4o_json, _gemini_bare_json],
        ids=["claude", "gpt4o", "gemini"],
    )
    def test_stories_parse_for_all_providers(self, wrapper):
        raw = wrapper(_STORIES_LIST)
        result = _parse_stories_response(raw, _make_features(), _make_analysis())
        assert len(result) == 2

    @pytest.mark.parametrize(
        "wrapper",
        [_claude_json, _gpt4o_json, _gemini_bare_json],
        ids=["claude", "gpt4o", "gemini"],
    )
    def test_tasks_parse_for_all_providers(self, wrapper):
        raw = wrapper(_TASKS_LIST)
        result = _parse_tasks_response(raw, _make_stories())
        assert len(result) == 3

    @pytest.mark.parametrize(
        "wrapper",
        [_claude_json, _gpt4o_json, _gemini_bare_json],
        ids=["claude", "gpt4o", "gemini"],
    )
    def test_sprints_parse_for_all_providers(self, wrapper):
        raw = wrapper(_SPRINTS_LIST)
        result = _parse_sprints_response(raw, _make_stories(), velocity=20)
        assert len(result) >= 1
