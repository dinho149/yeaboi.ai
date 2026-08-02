"""Tests for the scrum agent state schema."""

import json
from dataclasses import FrozenInstanceError, asdict

import pytest
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph

from yeaboi.agent.state import (
    PHASE_QUESTION_RANGES,
    TOTAL_QUESTIONS,
    AcceptanceCriterion,
    Discipline,
    Feature,
    MemberUpdate,
    OutputFormat,
    Priority,
    ProjectAnalysis,
    PromptQualityRating,
    QuestionnairePhase,
    QuestionnaireState,
    ReviewDecision,
    ScrumState,
    Sprint,
    StandupReport,
    StoryPointValue,
    Task,
    TaskLabel,
    UserStory,
    _merge_dicts,
)

# ── Enum tests ─────────────────────────────────────────────────────────


class TestPriority:
    def test_values(self):
        assert set(Priority) == {Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM, Priority.LOW}

    def test_is_str(self):
        assert isinstance(Priority.HIGH, str)
        assert Priority.HIGH == "high"


class TestStoryPointValue:
    def test_fibonacci_values(self):
        assert [v.value for v in StoryPointValue] == [1, 2, 3, 5, 8]

    def test_is_int(self):
        assert isinstance(StoryPointValue.FIVE, int)
        assert StoryPointValue.FIVE == 5


class TestQuestionnairePhase:
    def test_has_seven_phases(self):
        assert len(QuestionnairePhase) == 7

    def test_is_str(self):
        assert isinstance(QuestionnairePhase.PROJECT_CONTEXT, str)


class TestReviewDecision:
    def test_values(self):
        assert set(ReviewDecision) == {ReviewDecision.ACCEPT, ReviewDecision.EDIT, ReviewDecision.REJECT}


class TestOutputFormat:
    def test_values(self):
        assert set(OutputFormat) == {OutputFormat.JIRA, OutputFormat.MARKDOWN, OutputFormat.BOTH}


class TestDiscipline:
    """Tests for the Discipline StrEnum."""

    def test_values(self):
        """Discipline should have exactly 6 members."""
        assert len(Discipline) == 6
        assert set(Discipline) == {
            Discipline.FRONTEND,
            Discipline.BACKEND,
            Discipline.FULLSTACK,
            Discipline.INFRASTRUCTURE,
            Discipline.DESIGN,
            Discipline.TESTING,
        }

    def test_is_str(self):
        """Discipline members should be strings (StrEnum)."""
        assert isinstance(Discipline.BACKEND, str)
        assert Discipline.BACKEND == "backend"

    def test_default_discipline_on_user_story(self):
        """UserStory with no discipline kwarg should default to FULLSTACK."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-1",
            feature_id="F1",
            persona="dev",
            goal="do something",
            benefit="value",
            acceptance_criteria=(ac,),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )
        assert story.discipline == Discipline.FULLSTACK

    def test_explicit_discipline_on_user_story(self):
        """UserStory with explicit discipline should use the provided value."""
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        story = UserStory(
            id="US-1",
            feature_id="F1",
            persona="dev",
            goal="build a UI component",
            benefit="better UX",
            acceptance_criteria=(ac,),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
            discipline=Discipline.FRONTEND,
        )
        assert story.discipline == Discipline.FRONTEND


# ── Artifact dataclass tests ──────────────────────────────────────────


class TestAcceptanceCriterion:
    def test_creation(self):
        ac = AcceptanceCriterion(given="a user", when="they log in", then="they see the dashboard")
        assert ac.given == "a user"

    def test_frozen(self):
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        with pytest.raises(FrozenInstanceError):
            ac.given = "mutated"  # type: ignore[misc]

    def test_asdict(self):
        ac = AcceptanceCriterion(given="a", when="b", then="c")
        d = asdict(ac)
        assert d == {"given": "a", "when": "b", "then": "c"}


class TestFeature:
    def test_creation(self):
        e = Feature(id="F-1", title="Auth", description="Authentication system", priority=Priority.HIGH)
        assert e.id == "F-1"
        assert e.priority == Priority.HIGH

    def test_frozen(self):
        e = Feature(id="F-1", title="Auth", description="desc", priority=Priority.HIGH)
        with pytest.raises(FrozenInstanceError):
            e.title = "mutated"  # type: ignore[misc]


class TestUserStory:
    @pytest.fixture()
    def story(self):
        ac = AcceptanceCriterion(given="a user", when="they log in", then="they see the dashboard")
        return UserStory(
            id="US-1",
            feature_id="F-1",
            persona="developer",
            goal="log in with SSO",
            benefit="I save time",
            acceptance_criteria=(ac,),
            story_points=StoryPointValue.THREE,
            priority=Priority.MEDIUM,
        )

    def test_text_property(self, story: UserStory):
        assert story.text == "As a developer, I want to log in with SSO, so that I save time."

    def test_frozen(self, story: UserStory):
        with pytest.raises(FrozenInstanceError):
            story.persona = "mutated"  # type: ignore[misc]

    def test_asdict_serializable(self, story: UserStory):
        d = asdict(story)
        assert d["persona"] == "developer"
        assert isinstance(d["acceptance_criteria"], (list, tuple))


class TestTask:
    def test_creation(self):
        t = Task(id="T-1", story_id="US-1", title="Implement login", description="Wire up SSO")
        assert t.story_id == "US-1"

    def test_frozen(self):
        t = Task(id="T-1", story_id="US-1", title="t", description="d")
        with pytest.raises(FrozenInstanceError):
            t.title = "mutated"  # type: ignore[misc]

    def test_default_label_is_code(self):
        t = Task(id="T-1", story_id="US-1", title="t", description="d")
        assert t.label == TaskLabel.CODE

    def test_explicit_label(self):
        t = Task(id="T-1", story_id="US-1", title="t", description="d", label=TaskLabel.TESTING)
        assert t.label == TaskLabel.TESTING

    def test_ai_prompt_default(self):
        t = Task(id="T-1", story_id="US-1", title="t", description="d")
        assert t.ai_prompt == ""

    def test_ai_prompt_set(self):
        prompt = "You are a backend engineer. Implement the login endpoint using FastAPI."
        t = Task(id="T-1", story_id="US-1", title="t", description="d", ai_prompt=prompt)
        assert t.ai_prompt == prompt


class TestTaskLabel:
    def test_all_values(self):
        assert set(TaskLabel) == {TaskLabel.CODE, TaskLabel.DOCUMENTATION, TaskLabel.INFRASTRUCTURE, TaskLabel.TESTING}

    def test_string_values(self):
        assert TaskLabel.CODE.value == "Code"
        assert TaskLabel.DOCUMENTATION.value == "Documentation"
        assert TaskLabel.INFRASTRUCTURE.value == "Infrastructure"
        assert TaskLabel.TESTING.value == "Testing"

    def test_from_string(self):
        assert TaskLabel("Code") == TaskLabel.CODE
        assert TaskLabel("Testing") == TaskLabel.TESTING


class TestSprint:
    def test_creation(self):
        s = Sprint(id="S-1", name="Sprint 1", goal="Auth MVP", capacity_points=20, story_ids=("US-1", "US-2"))
        assert s.capacity_points == 20
        assert len(s.story_ids) == 2

    def test_frozen(self):
        s = Sprint(id="S-1", name="Sprint 1", goal="g", capacity_points=20, story_ids=())
        with pytest.raises(FrozenInstanceError):
            s.name = "mutated"  # type: ignore[misc]


class TestProjectAnalysis:
    """Tests for the ProjectAnalysis frozen dataclass."""

    @pytest.fixture()
    def analysis(self):
        return ProjectAnalysis(
            project_name="Todo App",
            project_description="A full-stack todo application",
            project_type="greenfield",
            goals=("Task management", "User auth"),
            end_users=("developers",),
            target_state="Deployed to production",
            tech_stack=("React", "FastAPI"),
            integrations=("GitHub API",),
            constraints=("Must use AWS",),
            sprint_length_weeks=2,
            target_sprints=4,
            risks=("Tight timeline",),
            out_of_scope=("Mobile app",),
            assumptions=("Default velocity",),
        )

    def test_creation(self, analysis: ProjectAnalysis):
        assert analysis.project_name == "Todo App"
        assert analysis.sprint_length_weeks == 2
        assert analysis.target_sprints == 4
        assert len(analysis.goals) == 2

    def test_frozen(self, analysis: ProjectAnalysis):
        with pytest.raises(FrozenInstanceError):
            analysis.project_name = "mutated"  # type: ignore[misc]

    def test_asdict(self, analysis: ProjectAnalysis):
        d = asdict(analysis)
        assert d["project_name"] == "Todo App"
        assert isinstance(d["goals"], (list, tuple))
        assert d["sprint_length_weeks"] == 2

    def test_tuple_fields_immutable(self, analysis: ProjectAnalysis):
        """Tuple fields should be immutable (no append)."""
        assert isinstance(analysis.goals, tuple)
        assert isinstance(analysis.tech_stack, tuple)

    def test_skip_features_defaults_false(self):
        """skip_features should default to False when not specified."""
        analysis = ProjectAnalysis(
            project_name="Test",
            project_description="desc",
            project_type="greenfield",
            goals=(),
            end_users=(),
            target_state="",
            tech_stack=(),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=3,
            risks=(),
            out_of_scope=(),
            assumptions=(),
        )
        assert analysis.skip_features is False

    def test_skip_features_true(self):
        """skip_features=True should be stored correctly."""
        analysis = ProjectAnalysis(
            project_name="Tiny API",
            project_description="A small REST API",
            project_type="greenfield",
            goals=("Build API",),
            end_users=("developers",),
            target_state="Deployed",
            tech_stack=("Python",),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=1,
            risks=(),
            out_of_scope=(),
            assumptions=(),
            skip_features=True,
        )
        assert analysis.skip_features is True

    def test_prompt_quality_defaults_none(self, analysis: ProjectAnalysis):
        """prompt_quality should default to None when not specified."""
        assert analysis.prompt_quality is None

    def test_prompt_quality_attached(self):
        """ProjectAnalysis should accept a PromptQualityRating."""
        quality = PromptQualityRating(
            score_pct=74,
            grade="B",
            answered_count=14,
            extracted_count=6,
            defaulted_count=4,
            skipped_count=2,
            probed_count=3,
            suggestions=("Specify your tech stack (Q11)",),
        )
        analysis = ProjectAnalysis(
            project_name="Test",
            project_description="desc",
            project_type="greenfield",
            goals=(),
            end_users=(),
            target_state="",
            tech_stack=(),
            integrations=(),
            constraints=(),
            sprint_length_weeks=2,
            target_sprints=3,
            risks=(),
            out_of_scope=(),
            assumptions=(),
            prompt_quality=quality,
        )
        assert analysis.prompt_quality is not None
        assert analysis.prompt_quality.grade == "B"
        assert analysis.prompt_quality.score_pct == 74


class TestPromptQualityRating:
    """Tests for the PromptQualityRating frozen dataclass."""

    def test_creation(self):
        rating = PromptQualityRating(
            score_pct=85,
            grade="A",
            answered_count=20,
            extracted_count=4,
            defaulted_count=2,
            skipped_count=0,
            probed_count=1,
            suggestions=(),
        )
        assert rating.score_pct == 85
        assert rating.grade == "A"
        assert rating.answered_count == 20

    def test_frozen(self):
        rating = PromptQualityRating(
            score_pct=50,
            grade="C",
            answered_count=10,
            extracted_count=5,
            defaulted_count=5,
            skipped_count=6,
            probed_count=0,
            suggestions=("Add more detail",),
        )
        with pytest.raises(FrozenInstanceError):
            rating.score_pct = 100  # type: ignore[misc]

    def test_asdict(self):
        rating = PromptQualityRating(
            score_pct=70,
            grade="B",
            answered_count=15,
            extracted_count=3,
            defaulted_count=4,
            skipped_count=4,
            probed_count=2,
            suggestions=("Suggestion 1",),
        )
        d = asdict(rating)
        assert d["score_pct"] == 70
        assert d["grade"] == "B"
        assert isinstance(d["suggestions"], (list, tuple))


# ── QuestionnaireState tests ──────────────────────────────────────────


class TestQuestionnaireState:
    def test_defaults(self):
        qs = QuestionnaireState()
        assert qs.current_question == 1
        assert qs.answers == {}
        assert qs.skipped_questions == set()
        assert qs.completed is False
        # PTO sub-loop fields default correctly
        assert qs._planned_leave_entries == []
        assert qs._awaiting_leave_input is False
        assert qs._leave_input_stage == ""
        assert qs._leave_input_buffer == {}

    def test_progress_empty(self):
        qs = QuestionnaireState()
        assert qs.progress == 0.0

    def test_progress_partial(self):
        # 5 answers out of 30 questions ≈ 0.1667
        qs = QuestionnaireState(answers={1: "a", 2: "b", 3: "c", 4: "d", 5: "e"})
        assert qs.progress == pytest.approx(5 / TOTAL_QUESTIONS)

    def test_progress_complete(self):
        qs = QuestionnaireState(answers={i: f"a{i}" for i in range(1, TOTAL_QUESTIONS + 1)})
        assert qs.progress == pytest.approx(1.0)

    def test_progress_with_skipped(self):
        # 3 answered + 2 skipped = 5 out of 30
        qs = QuestionnaireState(answers={1: "a", 2: "b", 3: "c"}, skipped_questions={4, 5})
        assert qs.progress == pytest.approx(5 / TOTAL_QUESTIONS)

    def test_current_phase_first(self):
        qs = QuestionnaireState(current_question=1)
        assert qs.current_phase == QuestionnairePhase.PROJECT_CONTEXT

    def test_current_phase_mid(self):
        qs = QuestionnaireState(current_question=10)
        assert qs.current_phase == QuestionnairePhase.TEAM_AND_CAPACITY

    def test_current_phase_codebase(self):
        qs = QuestionnaireState(current_question=15)
        assert qs.current_phase == QuestionnairePhase.CODEBASE_CONTEXT

    def test_current_phase_last(self):
        qs = QuestionnaireState(current_question=26)
        assert qs.current_phase == QuestionnairePhase.PREFERENCES

    def test_mutable(self):
        qs = QuestionnaireState()
        qs.current_question = 5
        qs.answers[1] = "answer"
        assert qs.current_question == 5
        assert qs.answers[1] == "answer"

    def test_skipped_questions_default(self):
        qs = QuestionnaireState()
        assert qs.skipped_questions == set()

    def test_skipped_questions_mutable(self):
        qs = QuestionnaireState()
        qs.skipped_questions.add(3)
        qs.skipped_questions.add(7)
        assert qs.skipped_questions == {3, 7}

    def test_probed_questions_default_empty(self):
        """probed_questions should default to an empty set."""
        qs = QuestionnaireState()
        assert qs.probed_questions == set()

    def test_probed_questions_mutable(self):
        """probed_questions should be mutable (add/remove elements)."""
        qs = QuestionnaireState()
        qs.probed_questions.add(1)
        qs.probed_questions.add(5)
        assert qs.probed_questions == {1, 5}

    def test_probed_questions_does_not_affect_progress(self):
        """Probing a question should NOT change progress — only answers and skips count."""
        qs = QuestionnaireState(answers={1: "a", 2: "b"}, probed_questions={1, 2})
        assert qs.progress == pytest.approx(2 / TOTAL_QUESTIONS)

    def test_defaulted_questions_default_empty(self):
        """defaulted_questions should default to an empty set."""
        qs = QuestionnaireState()
        assert qs.defaulted_questions == set()

    def test_defaulted_questions_mutable(self):
        """defaulted_questions should be mutable (add/remove elements)."""
        qs = QuestionnaireState()
        qs.defaulted_questions.add(5)
        qs.defaulted_questions.add(8)
        assert qs.defaulted_questions == {5, 8}

    def test_defaulted_questions_dont_double_count_progress(self):
        """Defaulted questions store their default in answers — progress counts the answer, not the default marker."""
        # Q5 defaulted (answer stored), Q1 answered normally → progress = 2/30
        qs = QuestionnaireState(answers={1: "a", 5: "No hard deadlines"}, defaulted_questions={5})
        assert qs.progress == pytest.approx(2 / TOTAL_QUESTIONS)

    def test_editing_question_defaults_none(self):
        """editing_question should default to None."""
        qs = QuestionnaireState()
        assert qs.editing_question is None

    def test_editing_question_mutable(self):
        """editing_question should be mutable (set and clear)."""
        qs = QuestionnaireState()
        qs.editing_question = 6
        assert qs.editing_question == 6
        qs.editing_question = None
        assert qs.editing_question is None

    def test_awaiting_confirmation_defaults_false(self):
        """awaiting_confirmation should default to False."""
        qs = QuestionnaireState()
        assert qs.awaiting_confirmation is False

    def test_awaiting_confirmation_mutable(self):
        """awaiting_confirmation should be mutable."""
        qs = QuestionnaireState()
        qs.awaiting_confirmation = True
        assert qs.awaiting_confirmation is True

    def test_phase_ranges_cover_all_questions(self):
        covered = set()
        for start, end in PHASE_QUESTION_RANGES.values():
            covered.update(range(start, end + 1))
        assert covered == set(range(1, TOTAL_QUESTIONS + 1))


# ── ScrumState TypedDict tests ────────────────────────────────────────


class TestScrumState:
    def test_required_keys(self):
        assert "messages" in ScrumState.__required_keys__

    def test_optional_keys_present(self):
        optional = ScrumState.__optional_keys__
        for key in (
            "project_name",
            "features",
            "stories",
            "tasks",
            "sprints",
            "questionnaire",
            "output_format",
            "project_analysis",
            "repo_context",
            "user_context",
        ):
            assert key in optional

    def test_repo_context_accepts_string(self):
        """repo_context should accept a raw repo scan string."""
        state: ScrumState = {
            "messages": [],
            "repo_context": "## File Tree\n- src/\n- README.md",
        }
        assert state["repo_context"] == "## File Tree\n- src/\n- README.md"

    def test_user_context_accepts_string(self):
        """user_context should accept free-form SCRUM.md content."""
        state: ScrumState = {
            "messages": [],
            "user_context": "# My Project\nWe use React + FastAPI. See https://docs.example.com",
        }
        assert "React" in state["user_context"]

    def test_minimal_creation(self):
        state: ScrumState = {"messages": []}
        assert state["messages"] == []

    def test_full_creation(self):
        state: ScrumState = {
            "messages": [HumanMessage(content="hi")],
            "project_name": "My Project",
            "features": [],
            "stories": [],
            "tasks": [],
            "sprints": [],
            "team_size": 5,
            "sprint_length_weeks": 2,
        }
        assert state["project_name"] == "My Project"
        assert state["team_size"] == 5


class TestPendingReview:
    """Tests for the pending_review field on ScrumState."""

    def test_pending_review_accepts_string(self):
        """pending_review should accept a node name string."""
        state: ScrumState = {
            "messages": [HumanMessage(content="hi")],
            "pending_review": "feature_generator",
        }
        assert state["pending_review"] == "feature_generator"

    def test_pending_review_with_review_decision(self):
        """pending_review should work alongside review decision fields."""
        state: ScrumState = {
            "messages": [HumanMessage(content="hi")],
            "pending_review": "story_writer",
            "last_review_decision": ReviewDecision.REJECT,
            "last_review_feedback": "need more detail",
        }
        assert state["pending_review"] == "story_writer"
        assert state["last_review_decision"] == ReviewDecision.REJECT


class TestQuestionnaireIntakeMode:
    """Tests for new intake_mode, extracted_questions, and _pending_merged_questions fields."""

    def test_intake_mode_default(self):
        qs = QuestionnaireState()
        assert qs.intake_mode == "standard"

    def test_extracted_questions_default(self):
        qs = QuestionnaireState()
        assert qs.extracted_questions == set()

    def test_pending_merged_questions_default(self):
        qs = QuestionnaireState()
        assert qs._pending_merged_questions == []

    def test_follow_up_choices_default(self):
        qs = QuestionnaireState()
        assert qs._follow_up_choices == {}

    def test_intake_mode_can_be_set(self):
        qs = QuestionnaireState(intake_mode="smart")
        assert qs.intake_mode == "smart"

    def test_small_project_mode_can_be_set(self):
        qs = QuestionnaireState(intake_mode="small_project")
        assert qs.intake_mode == "small_project"

    def test_reopen_for_epic_default_false(self):
        qs = QuestionnaireState()
        assert qs._reopen_for_epic is False

    def test_intake_mode_survives_round_trip(self):
        """small_project intake_mode persists across session serialization."""
        from yeaboi.sessions import _dict_to_questionnaire, _questionnaire_to_dict

        qs = QuestionnaireState(intake_mode="small_project")
        restored = _dict_to_questionnaire(_questionnaire_to_dict(qs))
        assert restored.intake_mode == "small_project"


class TestSmallProjectOversizedField:
    """The _small_project_oversized advisory flag round-trips through session state."""

    def test_round_trip(self):
        from yeaboi.sessions import _deserialize_state, _serialize_state

        state = {"messages": [], "_small_project_oversized": True, "_intake_mode": "small_project"}
        restored = _deserialize_state(_serialize_state(state))
        assert restored["_small_project_oversized"] is True
        assert restored["_intake_mode"] == "small_project"


class TestPastedImagesFields:
    """Pasted-screenshot path lists round-trip through session state (--resume)."""

    def test_round_trip(self):
        from yeaboi.sessions import _deserialize_state, _serialize_state

        state = {
            "messages": [],
            "pasted_images": ["/home/u/.yeaboi/attachments/p/img-a1b2c3d4.png"],
            "review_feedback_images": ["/home/u/.yeaboi/attachments/p/img-e5f6.jpg"],
            "chat_images": [],
        }
        restored = _deserialize_state(_serialize_state(state))
        assert restored["pasted_images"] == state["pasted_images"]
        assert restored["review_feedback_images"] == state["review_feedback_images"]
        assert restored["chat_images"] == []

    def test_legacy_state_without_fields_deserializes(self):
        # Sessions saved before this feature have no image keys — must not raise.
        from yeaboi.sessions import _deserialize_state, _serialize_state

        restored = _deserialize_state(_serialize_state({"messages": [], "project_name": "old"}))
        assert "pasted_images" not in restored
        assert restored.get("pasted_images", []) == []

    def test_fields_are_optional_keys(self):
        for key in ("pasted_images", "review_feedback_images", "chat_images"):
            assert key in ScrumState.__optional_keys__


class TestStateGraphCompatibility:
    def test_stategraph_accepts_scrum_state(self):
        """LangGraph's StateGraph must accept ScrumState without errors."""
        graph = StateGraph(ScrumState)
        assert graph is not None


# ── _merge_dicts reducer ────────────────────────────────────────────────


class TestMergeDicts:
    def test_merges_two_dicts(self):
        assert _merge_dicts({"a": "1"}, {"b": "2"}) == {"a": "1", "b": "2"}

    def test_b_overwrites_on_collision(self):
        assert _merge_dicts({"a": "old"}, {"a": "new"}) == {"a": "new"}

    def test_empty_a(self):
        assert _merge_dicts({}, {"x": "y"}) == {"x": "y"}

    def test_empty_b(self):
        assert _merge_dicts({"x": "y"}, {}) == {"x": "y"}

    def test_both_empty(self):
        assert _merge_dicts({}, {}) == {}

    def test_does_not_mutate_inputs(self):
        a = {"k": "v"}
        b = {"k2": "v2"}
        result = _merge_dicts(a, b)
        assert a == {"k": "v"}
        assert b == {"k2": "v2"}
        assert result == {"k": "v", "k2": "v2"}


# ── Jira key mapping state fields ───────────────────────────────────────


class TestJiraKeyMappingFields:
    def test_jira_feature_keys_field_present_in_scrumstate(self):
        # ScrumState is a TypedDict — check the field is declared (via __annotations__).
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_feature_keys" in all_annotations

    def test_jira_story_keys_field_present_in_scrumstate(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_story_keys" in all_annotations

    def test_jira_task_keys_field_present_in_scrumstate(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_task_keys" in all_annotations

    def test_jira_sprint_keys_field_present_in_scrumstate(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_sprint_keys" in all_annotations

    def test_jira_epic_key_field_present_in_scrumstate(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "jira_epic_key" in all_annotations

    def test_jira_task_keys_uses_merge_dicts_reducer(self):
        """jira_task_keys should merge via _merge_dicts like the other Jira dict fields."""
        a = {"t1": "PROJ-10"}
        b = {"t2": "PROJ-11"}
        assert _merge_dicts(a, b) == {"t1": "PROJ-10", "t2": "PROJ-11"}

    def test_jira_sprint_keys_uses_merge_dicts_reducer(self):
        a = {"s1": "42"}
        b = {"s2": "43"}
        assert _merge_dicts(a, b) == {"s1": "42", "s2": "43"}

    def test_stategraph_accepts_jira_key_fields(self):
        """StateGraph must compile without errors when ScrumState has Jira dict fields."""
        graph = StateGraph(ScrumState)
        assert graph is not None


# ── Azure DevOps key mapping state fields ──────────────────────────────


class TestAzDevOpsKeyMappingFields:
    def test_azdevops_epic_id_field_present(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "azdevops_epic_id" in all_annotations

    def test_azdevops_story_keys_field_present(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "azdevops_story_keys" in all_annotations

    def test_azdevops_task_keys_field_present(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "azdevops_task_keys" in all_annotations

    def test_azdevops_iteration_keys_field_present(self):
        all_annotations = {}
        for cls in ScrumState.__mro__:
            all_annotations.update(getattr(cls, "__annotations__", {}))
        assert "azdevops_iteration_keys" in all_annotations

    def test_azdevops_dict_fields_use_merge_dicts_reducer(self):
        """azdevops dict fields should merge via _merge_dicts."""
        a = {"s1": "100"}
        b = {"s2": "101"}
        assert _merge_dicts(a, b) == {"s1": "100", "s2": "101"}

    def test_stategraph_accepts_azdevops_key_fields(self):
        """StateGraph must compile without errors when ScrumState has Azure DevOps dict fields."""
        graph = StateGraph(ScrumState)
        assert graph is not None


# ── Daily Standup artifact tests ──────────────────────────────────────


class TestMemberUpdate:
    def test_defaults(self):
        """All fields default so old serialized reports still deserialize."""
        m = MemberUpdate()
        assert m.name == ""
        assert m.summary == ""
        assert m.blockers == ""
        assert m.progress_note == ""
        assert m.outlook == ""
        assert m.source == "inferred"

    def test_frozen(self):
        m = MemberUpdate(name="Alice")
        with pytest.raises(FrozenInstanceError):
            m.name = "Bob"  # type: ignore[misc]

    def test_asdict(self):
        m = MemberUpdate(
            name="Alice", summary="Shipped login", blockers="none", source="combined", self_report="shipped it"
        )
        assert asdict(m) == {
            "name": "Alice",
            "summary": "Shipped login",
            "blockers": "none",
            "progress_note": "",
            "outlook": "",
            "source": "combined",
            "self_report": "shipped it",
            "links": (),
            "activity_count": 0,
            "code_summary": "",
            "code_links": (),
            "code_activity_count": 0,
            "documentation_summary": "",
            "documentation_links": (),
            "documentation_activity_count": 0,
            "ticketing_summary": "",
            "ticketing_links": (),
            "ticketing_activity_count": 0,
            "ticketing_evidence": (),
            "code_evidence": (),
            "documentation_evidence": (),
        }

    def test_self_report_defaults_empty(self):
        """Old serialized reports (no self_report key) deserialize with ''."""
        assert MemberUpdate().self_report == ""


class TestStandupReport:
    def test_defaults(self):
        r = StandupReport()
        assert r.date == ""
        assert r.sprint_day == 0
        assert r.confidence_pct == 0
        assert r.confidence_delta == 0
        assert r.confidence_trend == ""
        assert r.member_updates == ()
        assert r.activity_counts == ()

    def test_frozen(self):
        r = StandupReport(date="2026-07-10")
        with pytest.raises(FrozenInstanceError):
            r.date = "2026-07-11"  # type: ignore[misc]

    def test_asdict_serializable(self):
        r = StandupReport(
            date="2026-07-10",
            session_id="s1",
            sprint_name="Sprint 5",
            sprint_day=3,
            sprint_total_days=10,
            confidence_pct=82,
            confidence_label="At risk",
            member_updates=(MemberUpdate(name="Alice", summary="x"),),
            activity_counts=(("jira", 4), ("github", 2)),
        )
        d = asdict(r)
        assert d["confidence_pct"] == 82
        assert d["member_updates"][0]["name"] == "Alice"
        # asdict preserves tuples; only json.dumps converts them to lists
        assert d["activity_counts"] == (("jira", 4), ("github", 2))
        assert json.loads(json.dumps(d))["activity_counts"] == [["jira", 4], ["github", 2]]

    def test_round_trip_via_store_helpers(self):
        """StandupReport survives serialize -> JSON -> reconstruct with types intact."""
        from yeaboi.standup.store import _dict_to_standup_report, _standup_report_to_json

        original = StandupReport(
            date="2026-07-10",
            session_id="s1",
            sprint_name="Sprint 5",
            sprint_day=3,
            sprint_total_days=10,
            confidence_pct=82,
            confidence_label="At risk",
            confidence_rationale="behind ideal burn",
            team_summary="team did stuff",
            member_updates=(
                MemberUpdate(name="Alice", summary="login", blockers="", source="inferred"),
                MemberUpdate(name="Bob", summary="api", blockers="waiting on review", source="self-reported"),
            ),
            activity_counts=(("jira", 4), ("github", 2)),
            warnings=("Jira: authentication failed", "AI summary unavailable — key not set"),
        )
        restored = _dict_to_standup_report(json.loads(_standup_report_to_json(original)))
        assert restored == original
        # tuples must be reconstructed as tuples, not lists
        assert isinstance(restored.member_updates, tuple)
        assert isinstance(restored.activity_counts, tuple)
        assert isinstance(restored.activity_counts[0], tuple)
        assert isinstance(restored.warnings, tuple)
        assert restored.warnings[0] == "Jira: authentication failed"

    def test_reconstruct_backfills_missing_fields(self):
        """A report dict from an older version (missing keys) still deserializes."""
        from yeaboi.standup.store import _dict_to_standup_report

        restored = _dict_to_standup_report({"date": "2026-07-10", "session_id": "s1"})
        assert restored.date == "2026-07-10"
        assert restored.confidence_pct == 0
        assert restored.member_updates == ()


class TestTranscriptReviewArtifacts:
    """The standup transcript-review artifacts (state.py) and their round-trip."""

    def test_defaults(self):
        from yeaboi.agent.state import StandupGap, TranscriptClaim, TranscriptReview, TranscriptSource

        assert TranscriptReview().gaps == ()
        assert TranscriptReview().config_suggestions == ()
        assert TranscriptClaim().member == ""
        assert StandupGap().priority == "medium"
        assert StandupGap().feedback_kind == "Improvement"
        assert TranscriptSource().attribution == "labelled"
        assert TranscriptSource().external is False

    def test_frozen(self):
        from yeaboi.agent.state import StandupGap

        gap = StandupGap(title="x")
        with pytest.raises(FrozenInstanceError):
            gap.title = "y"  # type: ignore[misc]

    def test_round_trip_via_store_helpers(self):
        from yeaboi.agent.state import StandupGap, TranscriptClaim, TranscriptReview, TranscriptSource
        from yeaboi.standup.store import _dict_to_review, _review_to_json

        original = TranscriptReview(
            session_id="s1",
            standup_date="2026-07-10",
            run_id=7,
            reviewed_at="2026-07-10T11:00:00+00:00",
            sources=(TranscriptSource(path="/tmp/a.vtt", fmt="vtt", speakers=("Alice", "Bob"), truncated=True),),
            claims=(TranscriptClaim(member="Alice", quote="I also did X", status="missing"),),
            gaps=(
                StandupGap(
                    fingerprint="fp1",
                    scope="product",
                    members=("Alice",),
                    claims=(TranscriptClaim(member="Alice", quote="I also did X"),),
                    next_steps=("do a thing", "do another"),
                    affected_systems=("confluence",),
                ),
            ),
            config_suggestions=(StandupGap(fingerprint="fp2", scope="config", remedy="add the repo"),),
            claims_matched=3,
            llm_mode="llm",
            warnings=("w1",),
        )
        restored = _dict_to_review(json.loads(_review_to_json(original)))
        assert restored == original
        # Every collection must come back a tuple, not a JSON list.
        assert isinstance(restored.sources, tuple)
        assert isinstance(restored.gaps[0].next_steps, tuple)
        assert isinstance(restored.gaps[0].claims[0], TranscriptClaim)
        assert restored.sources[0].truncated is True

    def test_reconstruct_backfills_missing_fields(self):
        from yeaboi.standup.store import _dict_to_review

        restored = _dict_to_review({"session_id": "s1"})
        assert restored.session_id == "s1"
        assert restored.gaps == ()
        assert restored.claims == ()
        assert restored.run_id == 0


class TestDeliveryReport:
    """The Reporting mode's DeliveryReport frozen dataclass + serialization."""

    def test_frozen(self):
        from yeaboi.agent.state import DeliveryReport

        r = DeliveryReport(period_label="Last sprint")
        with pytest.raises(FrozenInstanceError):
            r.period_label = "Last month"  # type: ignore[misc]

    def test_round_trip_via_store_helpers(self):
        """DeliveryReport survives serialize -> JSON -> reconstruct with types intact."""
        from yeaboi.agent.state import DeliveredItem, DeliveryReport
        from yeaboi.reporting.store import _dict_to_report, _report_to_json

        original = DeliveryReport(
            period_label="Last month (~2 sprints)",
            period_start="2026-06-15",
            period_end="2026-07-13",
            project_name="Acme",
            sprint_names=("Sprint 11", "Sprint 12"),
            headline="Strong delivery.",
            executive_summary="We shipped a lot.",
            themes=(("Security", ("SSO", "MFA")), ("Performance", ("Faster checkout",))),
            highlights=("SSO live",),
            metrics=(("Items delivered", "12"), ("Contributors", "4")),
            delivered_items=(DeliveredItem(key="A-1", title="t", status="Done", source="jira", assignee="Ada"),),
            emoji_theme=(("headline", "🚀"), ("themes", "🧩")),
            warnings=("w1",),
            generated_at="2026-07-13",
        )
        restored = _dict_to_report(json.loads(_report_to_json(original)))
        assert restored == original
        # nested tuples must be reconstructed as tuples, not lists
        assert isinstance(restored.themes, tuple)
        assert isinstance(restored.themes[0], tuple)
        assert isinstance(restored.themes[0][1], tuple)
        assert isinstance(restored.metrics[0], tuple)
        assert isinstance(restored.delivered_items[0], DeliveredItem)

    def test_reconstruct_backfills_missing_fields(self):
        from yeaboi.reporting.store import _dict_to_report

        restored = _dict_to_report({"period_label": "Last sprint"})
        assert restored.period_label == "Last sprint"
        assert restored.themes == ()
        assert restored.delivered_items == ()
        assert restored.metrics == ()


class TestRoadmapArtifacts:
    """The Roadmap intake card's frozen dataclasses (RoadmapProject / RoadmapAnalysis)."""

    def test_defaults_construct_empty(self):
        from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject

        p = RoadmapProject()
        assert p.name == ""
        assert p.size == "small"
        assert p.priority == 0
        assert p.themes == ()
        a = RoadmapAnalysis()
        assert a.source_type == ""
        assert a.projects == ()
        assert a.warnings == ()

    def test_frozen(self):
        from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject

        p = RoadmapProject(name="Checkout revamp")
        with pytest.raises(FrozenInstanceError):
            p.name = "Other"  # type: ignore[misc]
        a = RoadmapAnalysis(source_type="local")
        with pytest.raises(FrozenInstanceError):
            a.source_type = "notion"  # type: ignore[misc]

    def test_asdict_round_trip(self):
        from dataclasses import asdict

        from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject

        a = RoadmapAnalysis(
            source_type="confluence",
            source_locator="12345",
            source_label="Q3 Roadmap",
            summary="Three initiatives this quarter.",
            projects=(
                RoadmapProject(
                    name="SSO",
                    description="Add single sign-on.",
                    size="large",
                    rationale="Multi-sprint epic.",
                    priority=1,
                    themes=("Security",),
                    quarter="Q3 2026",
                ),
            ),
            warnings=("truncated",),
            generated_at="2026-07-18T10:00:00",
        )
        d = json.loads(json.dumps(asdict(a)))
        assert d["projects"][0]["name"] == "SSO"
        assert d["projects"][0]["themes"] == ["Security"]
        assert d["warnings"] == ["truncated"]

    def test_round_trip_via_store_helpers(self):
        """RoadmapAnalysis survives serialize -> JSON -> reconstruct with types intact."""
        from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject
        from yeaboi.roadmap.store import _analysis_to_json, _dict_to_analysis

        original = RoadmapAnalysis(
            source_type="local",
            source_locator="/tmp/roadmap.md",
            source_label="roadmap.md",
            summary="One initiative.",
            projects=(RoadmapProject(name="SSO", size="large", priority=1, themes=("Security",)),),
            warnings=("w1",),
            generated_at="2026-07-18",
        )
        restored = _dict_to_analysis(json.loads(_analysis_to_json(original)))
        assert restored == original
        assert isinstance(restored.projects, tuple)
        assert isinstance(restored.projects[0], RoadmapProject)
        assert isinstance(restored.projects[0].themes, tuple)
