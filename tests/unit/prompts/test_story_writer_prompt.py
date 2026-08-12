"""Tests for the story writer prompt template."""

from yeaboi.prompts.story_writer import (
    _ALLOWED_DISCIPLINES,
    _ALLOWED_PRIORITIES,
    _ALLOWED_STORY_POINTS,
    MAX_STORIES_PER_FEATURE,
    MAX_STORY_POINTS,
    MIN_STORIES_PER_FEATURE,
    SMALL_PROJECT_MAX_STORIES,
    get_story_writer_prompt,
)


def _make_prompt(**overrides: str) -> str:
    """Build a prompt with sensible defaults, overriding any kwargs."""
    defaults = {
        "project_name": "Todo App",
        "project_description": "A full-stack todo application",
        "project_type": "greenfield",
        "goals": "- Task management\n- User authentication",
        "end_users": "- developers\n- project managers",
        "tech_stack": "- React\n- FastAPI\n- PostgreSQL",
        "constraints": "- Must use AWS",
        "features_block": (
            "**F1: User Authentication** (Priority: high)\n"
            "  Registration, login, JWT\n\n"
            "**F2: Task Management** (Priority: high)\n"
            "  CRUD operations for tasks\n"
        ),
    }
    defaults.update(overrides)
    return get_story_writer_prompt(**defaults)


class TestGetStoryWriterPrompt:
    """Tests for get_story_writer_prompt()."""

    def test_returns_string(self):
        """get_story_writer_prompt should return a non-empty string."""
        result = _make_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_project_name(self):
        """The project name should appear in the prompt."""
        result = _make_prompt(project_name="Widget Builder")
        assert "Widget Builder" in result

    def test_includes_project_description(self):
        """The project description should appear in the prompt."""
        result = _make_prompt(project_description="Build amazing widgets")
        assert "Build amazing widgets" in result

    def test_includes_project_type(self):
        """The project type should appear in the prompt."""
        result = _make_prompt(project_type="existing codebase")
        assert "existing codebase" in result

    def test_includes_goals(self):
        """The goals should appear in the prompt."""
        result = _make_prompt(goals="- Build a REST API\n- Add auth")
        assert "Build a REST API" in result
        assert "Add auth" in result

    def test_includes_end_users(self):
        """The end users should appear in the prompt."""
        result = _make_prompt(end_users="- admins\n- customers")
        assert "admins" in result
        assert "customers" in result

    def test_includes_tech_stack(self):
        """The tech stack should appear in the prompt."""
        result = _make_prompt(tech_stack="- Python\n- Django")
        assert "Python" in result
        assert "Django" in result

    def test_includes_constraints(self):
        """The constraints should appear in the prompt."""
        result = _make_prompt(constraints="- Budget limit\n- Deadline Q2")
        assert "Budget limit" in result

    def test_includes_features_block(self):
        """The features block should appear in the prompt."""
        result = _make_prompt(features_block="**F1: Auth** (Priority: high)\n  Login and registration")
        assert "F1: Auth" in result
        assert "Login and registration" in result

    def test_includes_json_schema_with_all_fields(self):
        """The JSON schema should include all UserStory fields."""
        result = _make_prompt()
        assert '"id"' in result
        assert '"feature_id"' in result
        assert '"persona"' in result
        assert '"goal"' in result
        assert '"benefit"' in result
        assert '"acceptance_criteria"' in result
        assert '"story_points"' in result
        assert '"priority"' in result

    def test_includes_nested_ac_schema(self):
        """The JSON schema should include nested acceptance criteria fields."""
        result = _make_prompt()
        assert '"given"' in result
        assert '"when"' in result
        assert '"then"' in result

    def test_includes_story_count_guidance(self):
        """The prompt should specify the min and max story count per feature."""
        result = _make_prompt()
        assert str(MIN_STORIES_PER_FEATURE) in result
        assert str(MAX_STORIES_PER_FEATURE) in result

    def test_includes_fibonacci_values(self):
        """The prompt should list allowed Fibonacci story point values."""
        result = _make_prompt()
        for value in _ALLOWED_STORY_POINTS:
            assert str(value) in result

    def test_includes_priority_values(self):
        """The prompt should list allowed priority values."""
        result = _make_prompt()
        for priority in _ALLOWED_PRIORITIES:
            assert priority in result

    def test_includes_max_story_points(self):
        """The prompt should mention the 8-point cap."""
        result = _make_prompt()
        assert str(MAX_STORY_POINTS) in result

    def test_includes_ac_coverage_guidance(self):
        """The prompt should instruct on AC coverage (happy path, negative, edge case)."""
        result = _make_prompt()
        assert "happy" in result.lower()
        assert "negative" in result.lower() or "error" in result.lower()
        assert "edge case" in result.lower()

    def test_includes_splitting_strategies(self):
        """The prompt should include story splitting strategies."""
        result = _make_prompt()
        assert "Workflow step" in result or "workflow step" in result.lower()
        assert "Business rule" in result or "business rule" in result.lower()

    def test_includes_chain_of_thought(self):
        """The prompt should include chain-of-thought instructions."""
        result = _make_prompt()
        assert "Think step by step" in result

    def test_includes_json_only_instruction(self):
        """The prompt should instruct the LLM to return only JSON."""
        result = _make_prompt()
        assert "Return ONLY the JSON array" in result


class TestStoryWriterPromptOutOfScope:
    """Tests for the out_of_scope parameter."""

    def test_out_of_scope_section_present_when_provided(self):
        """'Out of Scope' section should contain the provided items."""
        result = _make_prompt(out_of_scope="- Creating the EKS cluster\n- CI/CD pipeline setup")
        assert "Out of Scope" in result
        assert "Creating the EKS cluster" in result
        assert "CI/CD pipeline setup" in result

    def test_out_of_scope_exclusion_rule_present(self):
        """The prompt should include a rule about not creating stories for out-of-scope items."""
        result = _make_prompt()
        assert "Do NOT create stories for items listed under Out of Scope" in result

    def test_out_of_scope_defaults_to_empty(self):
        """Out of scope should be present as a section even when empty."""
        result = _make_prompt()
        assert "### Out of Scope" in result


class TestStoryWriterPromptConstants:
    """Tests for prompt constants."""

    def test_min_stories_per_feature_is_1(self):
        assert MIN_STORIES_PER_FEATURE == 1

    def test_max_stories_per_feature_is_5(self):
        assert MAX_STORIES_PER_FEATURE == 5

    def test_max_story_points_is_8(self):
        assert MAX_STORY_POINTS == 8

    def test_allowed_story_points_are_fibonacci(self):
        """Allowed story points should be the Fibonacci sequence up to 8."""
        assert _ALLOWED_STORY_POINTS == (1, 2, 3, 5, 8)

    def test_allowed_priorities_match_enum(self):
        """Allowed priorities should match the Priority StrEnum values."""
        assert set(_ALLOWED_PRIORITIES) == {"critical", "high", "medium", "low"}


class TestStoryWriterDisciplinePrompt:
    """Tests for discipline-related prompt content."""

    def test_includes_discipline_in_schema(self):
        """The JSON schema should include a 'discipline' field."""
        result = _make_prompt()
        assert '"discipline"' in result

    def test_includes_discipline_values(self):
        """The prompt should list all 6 discipline values."""
        result = _make_prompt()
        for discipline in _ALLOWED_DISCIPLINES:
            assert discipline in result

    def test_includes_discipline_rule(self):
        """The prompt should include a rule about tagging stories by discipline."""
        result = _make_prompt()
        assert "Tag each story with a discipline" in result

    def test_discipline_fullstack_fallback_mentioned(self):
        """The prompt should mention using 'fullstack' when unclear."""
        result = _make_prompt()
        assert "fullstack" in result.lower()


class TestStoryWriterDisciplineConstants:
    """Tests for discipline-related constants."""

    def test_allowed_disciplines_has_six_values(self):
        """_ALLOWED_DISCIPLINES should have exactly 6 values."""
        assert len(_ALLOWED_DISCIPLINES) == 6

    def test_allowed_disciplines_values(self):
        """_ALLOWED_DISCIPLINES should match the expected set."""
        assert set(_ALLOWED_DISCIPLINES) == {
            "frontend",
            "backend",
            "fullstack",
            "infrastructure",
            "design",
            "testing",
        }


class TestStoryWriterPromptImports:
    """Verify imports from the expected locations."""

    def test_importable_from_story_writer_module(self):
        from yeaboi.prompts.story_writer import get_story_writer_prompt as imported_fn

        assert imported_fn is get_story_writer_prompt


class TestMaxTotalStories:
    """Small projects carry a hard total-story ceiling (YEA-57 item 7): the
    cap instruction must replace the per-feature range AND beat any
    team-calibration average — a one-sprint job must not inherit a large
    team's stories-per-epic habit."""

    def test_cap_instruction_present(self):
        result = _make_prompt(max_total_stories=SMALL_PROJECT_MAX_STORIES)
        assert f"at MOST {SMALL_PROJECT_MAX_STORIES} user stories" in result
        assert f"HARD LIMIT: at most {SMALL_PROJECT_MAX_STORIES} stories in total" in result

    def test_cap_suppresses_per_feature_range(self):
        result = _make_prompt(max_total_stories=2)
        assert f"{MIN_STORIES_PER_FEATURE}-{MAX_STORIES_PER_FEATURE} stories per feature" not in result

    def test_cap_beats_team_calibration_average(self):
        calibration = "Team history: avg 4.0 stories/epic across 12 epics."
        result = _make_prompt(max_total_stories=2, team_calibration=calibration)
        assert "HARD LIMIT: at most 2 stories in total" in result
        assert "Aim for ~4 stories per feature" not in result

    def test_no_cap_keeps_default_range(self):
        result = _make_prompt()
        assert f"{MIN_STORIES_PER_FEATURE}-{MAX_STORIES_PER_FEATURE} stories per feature" in result
        assert "HARD LIMIT" not in result

    def test_small_project_max_stories_is_2(self):
        assert SMALL_PROJECT_MAX_STORIES == 2
