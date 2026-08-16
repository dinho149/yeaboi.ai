"""Tests for story writer node and related helpers.

Extracted from test_nodes.py during the test reorganisation (Phase 12).
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from tests._node_helpers import (
    VALID_STORIES_JSON,
    make_dummy_analysis,
    make_sample_features,
    make_story_for_inference,
    make_valid_story,
)
from yeaboi.agent.nodes import (
    _build_fallback_stories,
    _format_features_for_prompt,
    _format_stories,
    _infer_discipline,
    _parse_stories_response,
    _snap_to_fibonacci,
    _validate_stories,
    story_writer,
)
from yeaboi.agent.state import (
    AcceptanceCriterion,
    Discipline,
    Feature,
    Priority,
    QuestionnaireState,
    StoryPointValue,
    UserStory,
)


class TestSnapToFibonacci:
    """Tests for _snap_to_fibonacci() helper."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1, StoryPointValue.ONE),
            (2, StoryPointValue.TWO),
            (3, StoryPointValue.THREE),
            (5, StoryPointValue.FIVE),
            (8, StoryPointValue.EIGHT),
        ],
    )
    def test_exact_fibonacci_values(self, value, expected):
        """Exact Fibonacci values should return as-is."""
        assert _snap_to_fibonacci(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (4, StoryPointValue.THREE),  # 4 is closer to 3 than to 5
            (6, StoryPointValue.FIVE),  # 6 is closer to 5 than to 8
            (7, StoryPointValue.EIGHT),  # 7 is closer to 8 than to 5
        ],
    )
    def test_rounds_to_nearest_fibonacci(self, value, expected):
        """Non-Fibonacci values should snap to the nearest Fibonacci."""
        assert _snap_to_fibonacci(value) == expected

    def test_caps_above_eight(self):
        """Values above 8 should clamp to 8."""
        assert _snap_to_fibonacci(10) == StoryPointValue.EIGHT
        assert _snap_to_fibonacci(13) == StoryPointValue.EIGHT
        assert _snap_to_fibonacci(100) == StoryPointValue.EIGHT

    def test_clamps_below_one(self):
        """Values below 1 should clamp to 1."""
        assert _snap_to_fibonacci(0) == StoryPointValue.ONE
        assert _snap_to_fibonacci(-5) == StoryPointValue.ONE

    def test_return_type(self):
        """Return value should be a StoryPointValue instance."""
        result = _snap_to_fibonacci(3)
        assert isinstance(result, StoryPointValue)


class TestFormatFeaturesForPrompt:
    """Tests for _format_features_for_prompt() helper."""

    def test_returns_string(self):
        """Should return a non-empty string."""
        features = make_sample_features()
        result = _format_features_for_prompt(features)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_feature_ids(self):
        """All feature IDs should appear in the output."""
        features = make_sample_features()
        result = _format_features_for_prompt(features)
        assert "F1" in result
        assert "F2" in result
        assert "F3" in result

    def test_includes_titles(self):
        """All feature titles should appear in the output."""
        features = make_sample_features()
        result = _format_features_for_prompt(features)
        assert "User Authentication" in result
        assert "Task Management" in result

    def test_includes_priorities(self):
        """Feature priorities should appear in the output."""
        features = make_sample_features()
        result = _format_features_for_prompt(features)
        assert "high" in result
        assert "medium" in result

    def test_includes_descriptions(self):
        """Feature descriptions should appear in the output."""
        features = make_sample_features()
        result = _format_features_for_prompt(features)
        assert "Registration, login, JWT" in result

    def test_empty_features_returns_empty(self):
        """Empty feature list should return empty string."""
        result = _format_features_for_prompt([])
        assert result == ""


class TestParseStoriesResponse:
    """Tests for _parse_stories_response() helper."""

    def _features(self) -> list[Feature]:
        return make_sample_features()

    def _analysis(self):
        return make_dummy_analysis()

    def test_valid_json_returns_story_list(self):
        """Valid JSON array should produce a list of UserStory dataclasses."""
        result = _parse_stories_response(VALID_STORIES_JSON, self._features(), self._analysis())
        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(s, UserStory) for s in result)

    def test_story_fields_parsed_correctly(self):
        """Story fields should match the JSON values."""
        result = _parse_stories_response(VALID_STORIES_JSON, self._features(), self._analysis())
        story = result[0]
        assert story.id == "US-F1-001"
        assert story.feature_id == "F1"
        assert story.persona == "end user"
        assert story.goal == "register an account"
        assert story.benefit == "I can access the application"
        assert story.priority == Priority.HIGH
        assert story.story_points == StoryPointValue.FIVE

    def test_nested_acs_parsed(self):
        """Nested acceptance criteria should be parsed into AcceptanceCriterion tuples."""
        result = _parse_stories_response(VALID_STORIES_JSON, self._features(), self._analysis())
        story = result[0]
        assert len(story.acceptance_criteria) == 3
        ac = story.acceptance_criteria[0]
        assert isinstance(ac, AcceptanceCriterion)
        assert ac.given == "I am on the registration page"
        assert ac.when == "I submit valid credentials"
        assert ac.then == "my account is created"

    def test_code_fence_stripping(self):
        """JSON wrapped in markdown code fences should be handled."""
        fenced = f"```json\n{VALID_STORIES_JSON}\n```"
        result = _parse_stories_response(fenced, self._features(), self._analysis())
        assert len(result) == 3
        assert result[0].id == "US-F1-001"

    def test_bad_json_returns_fallback(self):
        """Invalid JSON should fall back to deterministic stories."""
        result = _parse_stories_response("this is not json", self._features(), self._analysis())
        assert isinstance(result, list)
        assert len(result) == 6  # fallback: 2 per feature x 3 features

    def test_invalid_priority_defaults_to_medium(self):
        """Invalid priority value should default to MEDIUM."""
        json_str = (
            '[{"id": "US-F1-001", "feature_id": "F1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "urgent"}]'
        )
        result = _parse_stories_response(json_str, self._features(), self._analysis())
        assert result[0].priority == Priority.MEDIUM

    def test_invalid_points_snap_to_fibonacci(self):
        """Non-Fibonacci story points should snap to nearest Fibonacci."""
        json_str = (
            '[{"id": "US-F1-001", "feature_id": "F1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 4, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._features(), self._analysis())
        assert result[0].story_points == StoryPointValue.THREE  # 4 snaps to 3

    def test_missing_ac_adds_fallback(self):
        """Stories with no valid acceptance criteria should get a generic fallback AC."""
        json_str = (
            '[{"id": "US-F1-001", "feature_id": "F1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [], '
            '"story_points": 3, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._features(), self._analysis())
        assert len(result[0].acceptance_criteria) == 1
        assert "successfully" in result[0].acceptance_criteria[0].then

    def test_auto_id_generation(self):
        """Stories with missing IDs should get auto-generated IDs."""
        json_str = (
            '[{"feature_id": "F1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._features(), self._analysis())
        assert result[0].id == "US-F1-001"

    def test_invalid_feature_id_skipped(self):
        """Stories with invalid feature_ids should be skipped."""
        json_str = (
            '[{"id": "US-X1-001", "feature_id": "X1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._features(), self._analysis())
        # Story with invalid feature_id "X1" is skipped, fallback produces 6 stories
        assert len(result) == 6  # fallback: 2 per feature x 3 features

    def test_empty_array_returns_fallback(self):
        """Empty JSON array should fall back."""
        result = _parse_stories_response("[]", self._features(), self._analysis())
        assert len(result) == 6

    def test_non_list_json_returns_fallback(self):
        """JSON that's not a list should fall back."""
        result = _parse_stories_response('{"story": "not a list"}', self._features(), self._analysis())
        assert len(result) == 6


class TestBuildFallbackStories:
    """Tests for _build_fallback_stories() helper."""

    def test_two_per_feature(self):
        """Fallback should produce exactly 2 stories per feature."""
        features = make_sample_features()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(features, analysis)
        assert len(result) == 6  # 2 x 3 features

    def test_all_are_user_story_instances(self):
        """All returned items should be UserStory dataclasses."""
        features = make_sample_features()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(features, analysis)
        assert all(isinstance(s, UserStory) for s in result)

    def test_story_ids_follow_format(self):
        """Story IDs should follow US-{feature_id}-{NNN} format."""
        features = make_sample_features()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(features, analysis)
        assert result[0].id == "US-F1-001"
        assert result[1].id == "US-F1-002"
        assert result[2].id == "US-F2-001"
        assert result[3].id == "US-F2-002"

    def test_feature_id_linkage(self):
        """Each story's feature_id should match the parent feature."""
        features = make_sample_features()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(features, analysis)
        assert result[0].feature_id == "F1"
        assert result[1].feature_id == "F1"
        assert result[2].feature_id == "F2"
        assert result[3].feature_id == "F2"

    def test_default_points_and_priority(self):
        """Fallback stories should have reasonable default points and inherit priority."""
        features = make_sample_features()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(features, analysis)
        # First story (core) has 5 points, second (setup) has 3 points
        assert result[0].story_points == StoryPointValue.FIVE
        assert result[1].story_points == StoryPointValue.THREE
        # Priority inherited from feature
        assert result[0].priority == Priority.HIGH  # F1 is HIGH

    def test_ac_presence(self):
        """Every fallback story should have at least one acceptance criterion."""
        features = make_sample_features()
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(features, analysis)
        for story in result:
            assert len(story.acceptance_criteria) >= 1
            ac = story.acceptance_criteria[0]
            assert isinstance(ac, AcceptanceCriterion)
            assert ac.given
            assert ac.when
            assert ac.then

    def test_empty_features_returns_empty(self):
        """Empty feature list should produce no stories."""
        analysis = make_dummy_analysis()
        result = _build_fallback_stories([], analysis)
        assert result == []

    def test_uses_first_end_user(self):
        """Core stories should use the first end_user from analysis as persona."""
        features = [Feature(id="F1", title="Auth", description="Auth features", priority=Priority.HIGH)]
        analysis = make_dummy_analysis(end_users=("admin", "customer"))
        result = _build_fallback_stories(features, analysis)
        assert result[0].persona == "admin"

    def test_single_feature_produces_2_stories(self):
        """Single feature should produce 2 fallback stories (same as any other feature)."""
        single = [Feature(id="F1", title="My Project", description="desc", priority=Priority.HIGH)]
        analysis = make_dummy_analysis(skip_features=True, goals=("Build API",))
        result = _build_fallback_stories(single, analysis)
        assert len(result) == 2

    def test_single_feature_all_link_to_feature(self):
        """All fallback stories for a single feature should reference that feature's ID."""
        single = [Feature(id="F1", title="My Project", description="desc", priority=Priority.HIGH)]
        analysis = make_dummy_analysis(skip_features=True)
        result = _build_fallback_stories(single, analysis)
        assert all(s.feature_id == "F1" for s in result)

    def test_single_feature_ids_sequential(self):
        """Fallback stories for a single feature should have sequential IDs."""
        single = [Feature(id="F1", title="My Project", description="desc", priority=Priority.HIGH)]
        analysis = make_dummy_analysis(skip_features=True)
        result = _build_fallback_stories(single, analysis)
        expected_ids = ["US-F1-001", "US-F1-002"]
        assert [s.id for s in result] == expected_ids


class TestFormatStories:
    """Tests for _format_stories() helper."""

    def _sample_stories(self) -> list[UserStory]:
        return [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="end user",
                goal="register an account",
                benefit="I can access the app",
                acceptance_criteria=(
                    AcceptanceCriterion(given="on registration page", when="submit valid data", then="account created"),
                ),
                story_points=StoryPointValue.FIVE,
                priority=Priority.HIGH,
            ),
            UserStory(
                id="US-F2-001",
                feature_id="F2",
                persona="developer",
                goal="create a task",
                benefit="I can track work",
                acceptance_criteria=(AcceptanceCriterion(given="logged in", when="fill form", then="task saved"),),
                story_points=StoryPointValue.THREE,
                priority=Priority.MEDIUM,
            ),
        ]

    def _sample_features(self) -> list[Feature]:
        return [
            Feature(id="F1", title="Authentication", description="Auth features", priority=Priority.HIGH),
            Feature(id="F2", title="Task Management", description="Task CRUD", priority=Priority.MEDIUM),
        ]

    def test_returns_string(self):
        """Should return a non-empty markdown string."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test Project")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_project_name(self):
        """The project name should appear in the header."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Widget Builder")
        assert "Widget Builder" in result

    def test_includes_count(self):
        """The story count should be mentioned."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        assert "2 user story(ies)" in result

    def test_includes_story_ids(self):
        """All story IDs should appear in the output."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        assert "US-F1-001" in result
        assert "US-F2-001" in result

    def test_includes_persona_goal_benefit(self):
        """Story text elements should appear in the output."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        assert "end user" in result
        assert "register an account" in result
        assert "I can access the app" in result

    def test_includes_priorities(self):
        """Priority values should appear in the output."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        assert "high" in result
        assert "medium" in result

    def test_includes_points(self):
        """Story point values should appear in the output."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        assert "5" in result
        assert "3" in result

    def test_includes_acceptance_criteria(self):
        """Acceptance criteria should appear with Given/When/Then format."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        assert "Given" in result
        assert "When" in result
        assert "Then" in result

    def test_includes_review_footer(self):
        """The review prompt footer should be present."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        assert "[Accept / Edit / Reject]" in result

    def test_grouped_by_feature(self):
        """Stories should be grouped under their parent feature headers."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        # Feature headers should appear
        assert "F1: Authentication" in result
        assert "F2: Task Management" in result
        # F1 header should come before F2 header
        assert result.index("F1: Authentication") < result.index("F2: Task Management")


class TestStoryWriter:
    """Tests for the story_writer() node function."""

    def _make_state(self, **extras: object) -> dict:
        """Build a minimal state with project_analysis and features for story writer tests."""
        analysis = make_dummy_analysis()
        features = make_sample_features()
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "features": features,
        }
        state.update(extras)
        return state

    def test_returns_stories_list(self, monkeypatch):
        """story_writer should return a list of UserStory instances."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        assert "stories" in result
        assert isinstance(result["stories"], list)
        assert all(isinstance(s, UserStory) for s in result["stories"])

    def test_returns_ai_message(self, monkeypatch):
        """story_writer should return an AIMessage with the formatted stories."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        assert "messages" in result
        assert len(result["messages"]) == 1
        assert isinstance(result["messages"][0], AIMessage)

    def test_ai_message_contains_story_info(self, monkeypatch):
        """The AIMessage should contain story IDs and content."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        content = result["messages"][0].content
        assert "US-F1-001" in content
        assert "register an account" in content

    def test_bad_json_uses_fallback(self, monkeypatch):
        """When LLM returns bad JSON, the fallback should produce valid stories."""
        fake_response = MagicMock()
        fake_response.content = "not valid json at all"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        assert isinstance(result["stories"], list)
        assert len(result["stories"]) == 6  # fallback: 2 per feature x 3 features

    def test_llm_exception_uses_fallback(self, monkeypatch):
        """When the LLM call raises an exception, the fallback should be used."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("API down")
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        assert isinstance(result["stories"], list)
        assert len(result["stories"]) == 6
        assert "messages" in result

    def test_small_mode_hard_caps_stories_at_two(self, monkeypatch):
        """Small projects keep only the first SMALL_PROJECT_MAX_STORIES stories
        (document order = LLM importance order) and warn about the rest —
        the ticket's hard limit, enforced regardless of what the LLM returns."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON  # 3 stories
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state(_intake_mode="small_project"))
        assert [s.id for s in result["stories"]] == ["US-F1-001", "US-F1-002"]
        # The dropped story is named in the visible warning.
        assert "US-F2-001" in result["messages"][0].content

    def test_small_mode_prompt_carries_the_cap(self, monkeypatch):
        """The prompt itself must instruct the ceiling, not just the trim."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        story_writer(self._make_state(_intake_mode="small_project"))
        prompt_sent = str(mock_llm.invoke.call_args)
        assert "HARD LIMIT: at most 2 stories in total" in prompt_sent

    def test_non_small_mode_is_not_trimmed(self, monkeypatch):
        """Large/standard projects keep every parsed story."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state(_intake_mode="smart"))
        assert len(result["stories"]) == 3
        prompt_sent = str(mock_llm.invoke.call_args)
        assert "HARD LIMIT" not in prompt_sent

    def test_small_mode_caps_the_fallback_path_too(self, monkeypatch):
        """Even the deterministic fallback (2 per feature x 3 features = 6)
        must come out at the cap in small mode."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("API down")
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state(_intake_mode="small_project"))
        assert len(result["stories"]) == 2

    def test_calls_llm_with_temperature_zero(self, monkeypatch):
        """story_writer should use temperature=0.0 for deterministic output."""
        fake_response = MagicMock()
        fake_response.content = VALID_STORIES_JSON
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response

        captured_kwargs = {}

        def capture_get_llm(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_llm

        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", capture_get_llm)

        story_writer(self._make_state())
        assert captured_kwargs.get("temperature") == 0.0


# -- Discipline inference helpers ------------------------------------------


class TestInferDiscipline:
    """Tests for _infer_discipline() helper.

    Keyword-based discipline inference from story text fields.
    """

    def test_frontend_keywords(self):
        """Goal with frontend keywords -> FRONTEND."""
        story = make_story_for_inference(goal="build a UI component with responsive layout")
        assert _infer_discipline(story) == Discipline.FRONTEND

    def test_backend_keywords(self):
        """Goal with backend keywords -> BACKEND."""
        story = make_story_for_inference(goal="create an API endpoint for user data")
        assert _infer_discipline(story) == Discipline.BACKEND

    def test_infrastructure_keywords(self):
        """Goal with infrastructure keywords -> INFRASTRUCTURE."""
        story = make_story_for_inference(goal="set up CI pipeline and deploy to staging")
        assert _infer_discipline(story) == Discipline.INFRASTRUCTURE

    def test_design_keywords(self):
        """Goal with design keywords -> DESIGN."""
        story = make_story_for_inference(goal="create wireframe for the dashboard")
        assert _infer_discipline(story) == Discipline.DESIGN

    def test_testing_keywords(self):
        """Goal with testing keywords -> TESTING."""
        story = make_story_for_inference(goal="improve test coverage for authentication")
        assert _infer_discipline(story) == Discipline.TESTING

    def test_mixed_frontend_backend_returns_fullstack(self):
        """Both frontend and backend keywords -> FULLSTACK."""
        story = make_story_for_inference(goal="build a form UI component that calls the API endpoint")
        assert _infer_discipline(story) == Discipline.FULLSTACK

    def test_no_keywords_returns_fullstack(self):
        """Generic goal with no discipline keywords -> FULLSTACK (default)."""
        story = make_story_for_inference(goal="implement the main feature")
        assert _infer_discipline(story) == Discipline.FULLSTACK

    def test_keywords_in_acceptance_criteria(self):
        """Keywords in AC text should also be detected."""
        acs = (
            AcceptanceCriterion(given="the database is seeded", when="querying the endpoint", then="data is returned"),
        )
        story = make_story_for_inference(goal="implement data retrieval", acs=acs)
        assert _infer_discipline(story) == Discipline.BACKEND

    def test_keywords_in_persona(self):
        """Keywords in persona should be detected."""
        story = make_story_for_inference(persona="frontend developer", goal="add new feature")
        assert _infer_discipline(story) == Discipline.FRONTEND


# -- Story validation helpers ----------------------------------------------


class TestValidateStories:
    """Tests for _validate_stories() helper.

    Deterministic validation + auto-fix of story fields.
    """

    def _sample_features(self) -> list[Feature]:
        return [
            Feature(id="F1", title="Auth", description="Auth features", priority=Priority.HIGH),
            Feature(id="F2", title="Tasks", description="Task features", priority=Priority.MEDIUM),
        ]

    def test_valid_stories_pass_through(self):
        """Stories meeting all checks should pass through unchanged with no warnings."""
        stories = [
            make_valid_story("US-F1-001", "F1"),
            make_valid_story("US-F1-002", "F1"),
            make_valid_story("US-F2-001", "F2"),
            make_valid_story("US-F2-002", "F2"),
        ]
        features = self._sample_features()
        validated, warnings = _validate_stories(stories, features)
        assert len(validated) == 4
        assert len(warnings) == 0

    def test_adds_acs_when_fewer_than_3(self):
        """A story with fewer than 3 ACs should be padded to 3."""
        story = make_valid_story(num_acs=1)
        features = self._sample_features()
        validated, warnings = _validate_stories([story], features)
        assert len(validated[0].acceptance_criteria) == 3

    def test_warning_when_acs_added(self):
        """Adding generic ACs should produce a warning."""
        story = make_valid_story(num_acs=1)
        features = self._sample_features()
        _, warnings = _validate_stories([story], features)
        assert any("AC" in w for w in warnings)

    def test_empty_persona_gets_default(self):
        """Story with empty persona should default to 'user'."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-F1-001",
            feature_id="F1",
            persona="",
            goal="do something",
            benefit="value",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )
        features = self._sample_features()
        validated, warnings = _validate_stories([story], features)
        assert validated[0].persona == "user"
        assert any("persona" in w for w in warnings)

    def test_empty_goal_gets_default(self):
        """Story with empty goal should get a default."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-F1-001",
            feature_id="F1",
            persona="dev",
            goal="",
            benefit="value",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )
        features = self._sample_features()
        validated, warnings = _validate_stories([story], features)
        assert validated[0].goal != ""
        assert any("goal" in w for w in warnings)

    def test_empty_benefit_gets_default(self):
        """Story with empty benefit should get a default."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-F1-001",
            feature_id="F1",
            persona="dev",
            goal="do something",
            benefit="  ",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )
        features = self._sample_features()
        validated, warnings = _validate_stories([story], features)
        assert validated[0].benefit.strip() != ""
        assert any("benefit" in w for w in warnings)

    def test_feature_count_warning_below_min(self):
        """Feature with 0 stories (below MIN_STORIES_PER_FEATURE=1) should produce a warning."""
        # F1 has 1 story (valid), F2 has 0 (below minimum)
        stories = [make_valid_story("US-F1-001", "F1")]
        features = self._sample_features()
        _, warnings = _validate_stories(stories, features)
        feature_warnings = [w for w in warnings if "minimum" in w.lower()]
        # Only F2 (0 stories) should warn — F1 with 1 story is at the minimum
        assert len(feature_warnings) >= 1

    def test_feature_count_warning_above_max(self):
        """Feature with more than MAX_STORIES_PER_FEATURE stories should produce a warning."""
        stories = [make_valid_story(f"US-F1-{i:03d}", "F1") for i in range(1, 8)]
        features = self._sample_features()
        _, warnings = _validate_stories(stories, features)
        feature_warnings = [w for w in warnings if "maximum" in w.lower()]
        assert len(feature_warnings) >= 1

    def test_returns_new_instances_when_fixed(self):
        """Validated stories with fixes should be new objects (frozen rebuild)."""
        story = make_valid_story(num_acs=1)
        features = self._sample_features()
        validated, _ = _validate_stories([story], features)
        assert validated[0] is not story

    def test_preserves_discipline(self):
        """Discipline field should be preserved through validation."""
        story = make_valid_story(discipline=Discipline.FRONTEND)
        features = self._sample_features()
        validated, _ = _validate_stories([story], features)
        assert validated[0].discipline == Discipline.FRONTEND

    def test_single_feature_uses_standard_bounds(self):
        """Single feature should use the same MIN/MAX bounds as any other feature."""
        single = [Feature(id="F1", title="My Project", description="desc", priority=Priority.HIGH)]
        stories = [make_valid_story(f"US-F1-{i:03d}", "F1") for i in range(1, 4)]
        _, warnings = _validate_stories(stories, single)
        feature_warnings = [w for w in warnings if "minimum" in w.lower() or "maximum" in w.lower()]
        assert len(feature_warnings) == 0

    def test_single_feature_no_warning_at_min(self):
        """Single feature with exactly MIN_STORIES_PER_FEATURE (1) should not produce a warning."""
        single = [Feature(id="F1", title="My Project", description="desc", priority=Priority.HIGH)]
        stories = [make_valid_story("US-F1-001", "F1")]
        _, warnings = _validate_stories(stories, single)
        feature_warnings = [w for w in warnings if "minimum" in w.lower()]
        assert len(feature_warnings) == 0

    def test_single_feature_warns_above_max(self):
        """Single feature with more than MAX stories should produce a warning."""
        single = [Feature(id="F1", title="My Project", description="desc", priority=Priority.HIGH)]
        stories = [make_valid_story(f"US-F1-{i:03d}", "F1") for i in range(1, 8)]
        _, warnings = _validate_stories(stories, single)
        feature_warnings = [w for w in warnings if "maximum" in w.lower()]
        assert len(feature_warnings) >= 1

    def test_empty_title_generated_from_goal(self):
        """Story with empty title should get a title derived from goal."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-F1-001",
            feature_id="F1",
            persona="developer",
            goal="create a bookmark endpoint for the API",
            benefit="value",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
            title="",
        )
        features = self._sample_features()
        validated, _ = _validate_stories([story], features)
        assert validated[0].title != ""
        # Title should be title-cased and derived from goal words
        assert validated[0].title == "Create A Bookmark Endpoint For The Api"

    def test_existing_title_preserved(self):
        """Story with a title should keep it unchanged."""
        story = make_valid_story()
        features = self._sample_features()
        validated, _ = _validate_stories([story], features)
        # make_valid_story doesn't set title, so it's "" — but let's test with an explicit title
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story_with_title = UserStory(
            id="US-F1-001",
            feature_id="F1",
            persona="developer",
            goal="do something",
            benefit="value",
            acceptance_criteria=(ac, ac, ac),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
            title="My Custom Title",
        )
        validated, _ = _validate_stories([story_with_title], features)
        assert validated[0].title == "My Custom Title"


# -- Discipline parsing in _parse_stories_response -------------------------


class TestParseStoriesDiscipline:
    """Tests for discipline parsing in _parse_stories_response()."""

    def _features(self) -> list[Feature]:
        return make_sample_features()

    def _analysis(self):
        return make_dummy_analysis()

    def test_parses_discipline_field(self):
        """Valid discipline in JSON should be parsed correctly."""
        json_str = (
            '[{"id": "US-F1-001", "feature_id": "F1", "persona": "user", "goal": "build a form", '
            '"benefit": "better UX", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high", "discipline": "frontend"}]'
        )
        result = _parse_stories_response(json_str, self._features(), self._analysis())
        assert result[0].discipline == Discipline.FRONTEND

    def test_invalid_discipline_infers(self):
        """Invalid discipline value should trigger inference."""
        json_str = (
            '[{"id": "US-F1-001", "feature_id": "F1", "persona": "user", '
            '"goal": "create an API endpoint", '
            '"benefit": "data access", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high", "discipline": "invalid_value"}]'
        )
        result = _parse_stories_response(json_str, self._features(), self._analysis())
        # Should infer BACKEND from "API endpoint" keywords
        assert result[0].discipline == Discipline.BACKEND

    def test_missing_discipline_infers(self):
        """Missing discipline field should trigger inference."""
        json_str = (
            '[{"id": "US-F1-001", "feature_id": "F1", "persona": "user", '
            '"goal": "build a responsive page layout", '
            '"benefit": "better UX", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high"}]'
        )
        result = _parse_stories_response(json_str, self._features(), self._analysis())
        # Should infer FRONTEND from "page layout responsive" keywords
        assert result[0].discipline == Discipline.FRONTEND


# -- Fallback stories discipline -------------------------------------------


class TestFallbackStoriesDiscipline:
    """Tests for discipline tagging in _build_fallback_stories()."""

    def test_core_story_is_fullstack(self):
        """Fallback core-functionality story should be tagged FULLSTACK."""
        features = [Feature(id="F1", title="Auth", description="Auth features", priority=Priority.HIGH)]
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(features, analysis)
        assert result[0].discipline == Discipline.FULLSTACK

    def test_setup_story_is_testing(self):
        """Fallback setup/testing story should be tagged TESTING."""
        features = [Feature(id="F1", title="Auth", description="Auth features", priority=Priority.HIGH)]
        analysis = make_dummy_analysis()
        result = _build_fallback_stories(features, analysis)
        assert result[1].discipline == Discipline.TESTING


# -- Format stories with discipline and warnings --------------------------


class TestFormatStoriesDiscipline:
    """Tests for discipline display and warnings in _format_stories()."""

    def _sample_stories(self) -> list[UserStory]:
        return [
            UserStory(
                id="US-F1-001",
                feature_id="F1",
                persona="end user",
                goal="register an account",
                benefit="I can access the app",
                acceptance_criteria=(
                    AcceptanceCriterion(given="on reg page", when="submit data", then="account created"),
                ),
                story_points=StoryPointValue.FIVE,
                priority=Priority.HIGH,
                discipline=Discipline.BACKEND,
            ),
        ]

    def _sample_features(self) -> list[Feature]:
        return [Feature(id="F1", title="Auth", description="Auth features", priority=Priority.HIGH)]

    def test_shows_discipline_in_output(self):
        """Formatted output should include the discipline tag."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        assert "Discipline:** backend" in result

    def test_shows_warnings_when_provided(self):
        """Warnings should appear in a Validation Notes section."""
        warnings = ["Story US-F1-001 had only 1 AC — added 2 generic ACs."]
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test", warnings=warnings)
        assert "Validation Notes" in result
        assert "Story US-F1-001" in result

    def test_no_warnings_section_when_empty(self):
        """No warnings -> no Validation Notes section."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test", warnings=[])
        assert "Validation Notes" not in result

    def test_no_warnings_section_when_none(self):
        """Default (None warnings) -> no Validation Notes section."""
        result = _format_stories(self._sample_stories(), self._sample_features(), "Test")
        assert "Validation Notes" not in result


# -- Story writer validates stories ----------------------------------------


class TestStoryWriterValidation:
    """Tests for validation integration in the story_writer() node."""

    def _make_state(self) -> dict:
        analysis = make_dummy_analysis()
        features = make_sample_features()
        return {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": QuestionnaireState(completed=True),
            "project_analysis": analysis,
            "features": features,
        }

    def test_stories_are_validated(self, monkeypatch):
        """story_writer should validate stories (e.g. pad ACs to 3)."""
        # JSON with a story that has only 1 AC -- validation should pad to 3
        single_ac_json = (
            '[{"id": "US-F1-001", "feature_id": "F1", "persona": "user", "goal": "test", '
            '"benefit": "val", "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}], '
            '"story_points": 3, "priority": "high", "discipline": "backend"}]'
        )
        fake_response = MagicMock()
        fake_response.content = single_ac_json
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = fake_response
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: mock_llm)

        result = story_writer(self._make_state())
        # The single AC should have been padded to 3 by validation
        assert len(result["stories"][0].acceptance_criteria) == 3


class TestAcStyleParsing:
    """The parser accepts every AC shape regardless of the requested style."""

    def _features(self) -> list[Feature]:
        return make_sample_features()

    def _analysis(self):
        return make_dummy_analysis()

    def _story_json(self, acs) -> str:
        import json as _json

        return _json.dumps(
            [
                {
                    "id": "US-F1-001",
                    "feature_id": "F1",
                    "title": "Login",
                    "persona": "user",
                    "goal": "log in",
                    "benefit": "access my data",
                    "acceptance_criteria": acs,
                    "story_points": 3,
                    "priority": "high",
                }
            ]
        )

    def test_string_acs_parse_as_free_text(self):
        raw = self._story_json(["Login succeeds with valid credentials.", "Invalid password shows an error."])
        result = _parse_stories_response(raw, self._features(), self._analysis(), "bullets")
        acs = result[0].acceptance_criteria
        assert [ac.text for ac in acs] == [
            "Login succeeds with valid credentials.",
            "Invalid password shows an error.",
        ]
        assert all(not ac.given for ac in acs)

    def test_text_dict_acs_parse(self):
        raw = self._story_json([{"text": "Search returns within 200ms."}])
        result = _parse_stories_response(raw, self._features(), self._analysis(), "gwt")
        assert result[0].acceptance_criteria[0].text == "Search returns within 200ms."

    def test_mixed_shapes_all_survive(self):
        raw = self._story_json(
            [
                {"given": "a", "when": "b", "then": "c"},
                "Free text criterion.",
                {"text": "Another one."},
            ]
        )
        result = _parse_stories_response(raw, self._features(), self._analysis(), "gwt")
        acs = result[0].acceptance_criteria
        assert len(acs) == 3
        assert acs[0].given == "a" and not acs[0].text
        assert acs[1].text == "Free text criterion."

    def test_generic_fallback_matches_style(self):
        raw = self._story_json([])
        gwt = _parse_stories_response(raw, self._features(), self._analysis(), "gwt")
        assert gwt[0].acceptance_criteria[0].given  # GWT-shaped
        bullets = _parse_stories_response(raw, self._features(), self._analysis(), "bullets")
        assert bullets[0].acceptance_criteria[0].text  # free-text-shaped

    def test_validate_pads_with_style_matched_generics(self):
        story = make_valid_story()
        story = story.__class__(**{**story.__dict__, "acceptance_criteria": (AcceptanceCriterion(text="One."),)})
        validated, warnings = _validate_stories([story], self._features(), "bullets")
        acs = validated[0].acceptance_criteria
        assert len(acs) == 3
        assert all(ac.text for ac in acs)

    def test_validate_honours_low_team_median(self):
        story = make_valid_story()
        story = story.__class__(**{**story.__dict__, "acceptance_criteria": (AcceptanceCriterion(text="One."),)})
        validated, warnings = _validate_stories([story], self._features(), "bullets", min_acs=1)
        assert len(validated[0].acceptance_criteria) == 1
        assert not any("generic" in w for w in warnings)

    def test_fallback_stories_match_style(self):
        bullets = _build_fallback_stories(self._features(), self._analysis(), "bullets")
        assert all(ac.text for s in bullets for ac in s.acceptance_criteria)
        gwt = _build_fallback_stories(self._features(), self._analysis(), "gwt")
        assert all(ac.given for s in gwt for ac in s.acceptance_criteria)


class TestParseDodCount:
    """dod_applicable flags normalise to the plan's RESOLVED DoD length.

    A custom Definition of Done list is not 7 items; the renderers zip flags
    against the resolved list, so a mis-sized tuple used to drop the whole
    DoD section from every synced ticket.
    """

    def _raw(self, dod=None):
        import json as _json

        story = {
            "id": "US-F1-001",
            "feature_id": "F1",
            "title": "Login",
            "persona": "user",
            "goal": "log in",
            "benefit": "access my data",
            "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
            "story_points": 3,
            "priority": "high",
        }
        if dod is not None:
            story["dod_applicable"] = dod
        return _json.dumps([story])

    def test_custom_count_sizes_flags(self):
        raw = self._raw([True, False, True])
        result = _parse_stories_response(raw, make_sample_features(), make_dummy_analysis(), "gwt", dod_count=3)
        assert result[0].dod_applicable == (True, False, True)

    def test_long_reply_truncated(self):
        raw = self._raw([True] * 7)
        result = _parse_stories_response(raw, make_sample_features(), make_dummy_analysis(), "gwt", dod_count=3)
        assert result[0].dod_applicable == (True, True, True)

    def test_short_reply_padded_applicable(self):
        raw = self._raw([False])
        result = _parse_stories_response(raw, make_sample_features(), make_dummy_analysis(), "gwt", dod_count=3)
        assert result[0].dod_applicable == (False, True, True)

    def test_missing_defaults_to_resolved_length(self):
        result = _parse_stories_response(self._raw(), make_sample_features(), make_dummy_analysis(), "gwt", dod_count=3)
        assert result[0].dod_applicable == (True, True, True)

    def test_default_count_stays_seven(self):
        raw = self._raw([True, False, True, True, True, True, True])
        result = _parse_stories_response(raw, make_sample_features(), make_dummy_analysis())
        assert result[0].dod_applicable == (True, False, True, True, True, True, True)
