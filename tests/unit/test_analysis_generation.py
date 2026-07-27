"""Unit tests for LLM-based sample generation functions in team_learning.py.

Covers: generate_sample_epic, generate_sample_stories, generate_sample_tasks,
generate_sample_sprint — both successful LLM calls (mocked) and fallback paths.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from yeaboi.tools.team_learning import (
    _build_revision_block,
    generate_sample_epic,
    generate_sample_sprint,
    generate_sample_stories,
    generate_sample_tasks,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_CALIBRATION = """\
## Team Calibration
- Velocity: 23.5 pts/sprint
- Sprint length: 2 weeks
- Story points: Fibonacci (1,2,3,5,8)
"""

_EXAMPLES = {
    "naming_conventions": {
        "epic_naming_style": "quarter-scoped",
        "epic_examples": ["Q4|2025|High Region Outage DR", "Q1|2026|Low Overmind improvement"],
        "template_sections": [("What is this about?", 0.8), ("Why does it matter?", 0.6)],
    },
    "ac_patterns": {"median_ac": 3},
    "task_decomposition": {
        "avg_tasks_per_story": 4.8,
        "type_distribution": {"Development": 64, "Testing": 13},
        "common_tasks": [("create rollback module", 2)],
    },
    "scope_changes": {
        "totals": {"avg_delivered_velocity": 25.9, "avg_committed_velocity": 19.1},
    },
}

_SAMPLE_EPIC = {
    "title": "Q1|2026|Medium Platform Resilience",
    "description": "Improve platform resilience.",
    "priority": "high",
    "stories_estimate": 5,
    "points_estimate": 18,
    "rationale": "Matches quarter-scoped naming.",
}

_SAMPLE_STORIES = [
    {
        "id": "S1",
        "title": "Implement failover",
        "persona": "developer",
        "goal": "automated failover",
        "benefit": "reduced downtime",
        "story_points": 5,
        "priority": "high",
        "discipline": "infrastructure",
        "acceptance_criteria": [
            {"given": "primary fails", "when": "failover triggers", "then": "traffic reroutes"},
        ],
        "rationale": "Matches infra pattern.",
    },
    {
        "id": "S2",
        "title": "Add monitoring",
        "persona": "SRE",
        "goal": "visibility",
        "benefit": "faster response",
        "story_points": 3,
        "priority": "medium",
        "discipline": "observability",
        "acceptance_criteria": [],
        "rationale": "Matches observability pattern.",
    },
]

_SAMPLE_TASKS = [
    {"id": "T-S1-01", "story_id": "S1", "title": "Build health endpoint", "label": "Code"},
    {"id": "T-S1-02", "story_id": "S1", "title": "Write integration tests", "label": "Testing"},
    {"id": "T-S2-01", "story_id": "S2", "title": "Create dashboard", "label": "Infrastructure"},
]


def _mock_llm_response(content: str) -> MagicMock:
    """Create a mock LLM response object."""
    resp = MagicMock()
    resp.content = content
    return resp


# ---------------------------------------------------------------------------
# generate_sample_epic
# ---------------------------------------------------------------------------


class TestGenerateSampleEpic:
    """Test sample epic generation with mocked LLM and fallback."""

    @patch("yeaboi.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        epic_json = json.dumps(_SAMPLE_EPIC)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(epic_json)

        result = generate_sample_epic(_CALIBRATION, _EXAMPLES)

        assert isinstance(result, dict)
        assert result["title"] == _SAMPLE_EPIC["title"]
        assert result["priority"] == "high"
        assert result["stories_estimate"] == 5

    @patch("yeaboi.agent.llm.get_llm")
    def test_json_with_code_fences(self, mock_get_llm):
        """LLM wraps JSON in markdown code fences — should still parse."""
        epic_json = f"```json\n{json.dumps(_SAMPLE_EPIC)}\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(epic_json)

        result = generate_sample_epic(_CALIBRATION)
        assert isinstance(result, dict)
        assert "title" in result

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_on_llm_error(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = generate_sample_epic(_CALIBRATION, _EXAMPLES)

        assert isinstance(result, dict)
        assert "title" in result
        assert "rationale" in result
        assert "Fallback" in result["rationale"]

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_on_invalid_json(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("not valid json {{{")

        result = generate_sample_epic(_CALIBRATION)
        assert isinstance(result, dict)
        assert "Fallback" in result["rationale"]

    def test_fallback_uses_epic_examples(self):
        """When LLM unavailable, fallback should use first epic example if available."""
        with patch("yeaboi.agent.llm.get_llm", side_effect=RuntimeError("unavailable")):
            result = generate_sample_epic(_CALIBRATION, _EXAMPLES)
            assert isinstance(result, dict)
            # Should use first example title from naming_conventions
            assert result["title"] == "Q4|2025|High Region Outage DR"

    @patch("yeaboi.agent.llm.get_llm")
    def test_no_examples(self, mock_get_llm):
        epic_json = json.dumps(_SAMPLE_EPIC)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(epic_json)

        result = generate_sample_epic(_CALIBRATION, None)
        assert isinstance(result, dict)

    @patch("yeaboi.agent.llm.get_llm")
    def test_empty_examples(self, mock_get_llm):
        epic_json = json.dumps(_SAMPLE_EPIC)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(epic_json)

        result = generate_sample_epic(_CALIBRATION, {})
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# generate_sample_stories
# ---------------------------------------------------------------------------


class TestGenerateSampleStories:
    """Test sample stories generation with mocked LLM and fallback."""

    @patch("yeaboi.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        stories_json = json.dumps(_SAMPLE_STORIES)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(stories_json)

        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC, _EXAMPLES)

        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["id"] == "S1"
        assert result[0]["story_points"] == 5

    @patch("yeaboi.agent.llm.get_llm")
    def test_json_with_code_fences(self, mock_get_llm):
        stories_json = f"```json\n{json.dumps(_SAMPLE_STORIES)}\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(stories_json)

        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC)
        assert isinstance(result, list)
        assert len(result) >= 1

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_on_llm_error(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC)

        assert isinstance(result, list)
        assert len(result) >= 1
        assert "Fallback" in result[0]["rationale"]

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_on_invalid_json(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("broken")

        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC)
        assert isinstance(result, list)
        assert len(result) >= 1

    @patch("yeaboi.agent.llm.get_llm")
    def test_stories_count_capped_at_3(self, mock_get_llm):
        """Even if epic estimates 10 stories, generate at most 3."""
        big_epic = {**_SAMPLE_EPIC, "stories_estimate": 10}
        stories = [{"id": f"S{i}", "title": f"Story {i}"} for i in range(3)]
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(stories))

        result = generate_sample_stories(_CALIBRATION, big_epic)
        assert len(result) <= 3

    @patch("yeaboi.agent.llm.get_llm")
    def test_no_examples(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_STORIES))
        result = generate_sample_stories(_CALIBRATION, _SAMPLE_EPIC, None)
        assert isinstance(result, list)

    @patch("yeaboi.agent.llm.get_llm")
    def test_epic_with_zero_stories_estimate(self, mock_get_llm):
        """Epic with stories_estimate=0 should still generate at least 2."""
        epic = {**_SAMPLE_EPIC, "stories_estimate": 0}
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_STORIES))
        result = generate_sample_stories(_CALIBRATION, epic)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# generate_sample_tasks
# ---------------------------------------------------------------------------


class TestGenerateSampleTasks:
    """Test sample tasks generation with mocked LLM and fallback."""

    @patch("yeaboi.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        tasks_json = json.dumps(_SAMPLE_TASKS)
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(tasks_json)

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES, _EXAMPLES)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0]["id"] == "T-S1-01"

    @patch("yeaboi.agent.llm.get_llm")
    def test_json_with_code_fences(self, mock_get_llm):
        tasks_json = f"```\n{json.dumps(_SAMPLE_TASKS)}\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(tasks_json)

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES)
        assert isinstance(result, list)

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_on_llm_error(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES)

        assert isinstance(result, list)
        assert len(result) >= 1
        # Fallback generates one task per story
        story_ids = {t["story_id"] for t in result}
        assert "S1" in story_ids

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_on_invalid_json(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("{invalid")

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES)
        assert isinstance(result, list)
        assert len(result) >= 1

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_creates_task_per_story(self, mock_get_llm):
        """Fallback should create at least one task per story."""
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("fail")

        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES)
        fallback_story_ids = {t["story_id"] for t in result}
        input_story_ids = {s["id"] for s in _SAMPLE_STORIES}
        assert fallback_story_ids == input_story_ids

    @patch("yeaboi.agent.llm.get_llm")
    def test_no_examples(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_TASKS))
        result = generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES, None)
        assert isinstance(result, list)

    @patch("yeaboi.agent.llm.get_llm")
    def test_empty_stories(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("[]")
        result = generate_sample_tasks(_CALIBRATION, [])
        assert isinstance(result, list)

    @patch("yeaboi.agent.llm.get_llm")
    def test_task_decomposition_context_injected(self, mock_get_llm):
        """When examples include task_decomposition, it should be in the prompt."""
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_TASKS))

        generate_sample_tasks(_CALIBRATION, _SAMPLE_STORIES, _EXAMPLES)

        # Verify prompt was called and contains task context
        call_args = mock_get_llm.return_value.invoke.call_args
        prompt_content = call_args[0][0][0].content
        assert "4.8" in prompt_content  # avg_tasks_per_story


# ---------------------------------------------------------------------------
# generate_sample_sprint
# ---------------------------------------------------------------------------


class TestGenerateSampleSprint:
    """Test sample sprint plan generation with mocked LLM and fallback."""

    _SPRINT_RESULT = {
        "sprint_name": "Sprint 1",
        "velocity_target": 20,
        "stories_included": ["S1", "S2"],
        "total_points": 8,
        "capacity_notes": "Based on team avg.",
        "risks": ["External dependency"],
        "rationale": "Conservative allocation.",
    }

    @patch("yeaboi.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(self._SPRINT_RESULT))

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS, _EXAMPLES)

        assert isinstance(result, dict)
        assert result["sprint_name"] == "Sprint 1"
        assert result["velocity_target"] == 20
        assert result["total_points"] == 8

    @patch("yeaboi.agent.llm.get_llm")
    def test_json_with_code_fences(self, mock_get_llm):
        sprint_json = f"```json\n{json.dumps(self._SPRINT_RESULT)}\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(sprint_json)

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS)
        assert isinstance(result, dict)
        assert "sprint_name" in result

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_on_llm_error(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS, _EXAMPLES)

        assert isinstance(result, dict)
        assert "Fallback" in result["rationale"]
        assert result["velocity_target"] == 25.9  # avg_delivered_velocity from examples

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_on_invalid_json(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("not json")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS)
        assert isinstance(result, dict)
        assert "Fallback" in result["rationale"]

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_includes_all_story_ids(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("fail")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS)
        assert set(result["stories_included"]) == {"S1", "S2"}

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_total_points_matches_stories(self, mock_get_llm):
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("fail")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS)
        expected_pts = sum(s.get("story_points", 0) for s in _SAMPLE_STORIES)
        assert result["total_points"] == expected_pts

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_without_examples(self, mock_get_llm):
        """Without examples, fallback velocity_target should be 20."""
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("fail")

        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS, None)
        assert result["velocity_target"] == 20  # default fallback

    @patch("yeaboi.agent.llm.get_llm")
    def test_no_examples(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(self._SPRINT_RESULT))
        result = generate_sample_sprint(_CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS, None)
        assert isinstance(result, dict)

    @patch("yeaboi.agent.llm.get_llm")
    def test_empty_stories_and_tasks(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(
            json.dumps({**self._SPRINT_RESULT, "stories_included": [], "total_points": 0})
        )
        result = generate_sample_sprint(_CALIBRATION, [], [])
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# Revision feedback (feedback-driven regeneration)
# ---------------------------------------------------------------------------


class TestBuildRevisionBlock:
    """Test the REVISION REQUEST prompt-section builder."""

    def test_no_feedback_returns_empty(self):
        assert _build_revision_block(None, _SAMPLE_EPIC) == ""
        assert _build_revision_block("", _SAMPLE_EPIC) == ""

    def test_feedback_includes_previous_and_feedback(self):
        block = _build_revision_block("make the title shorter", _SAMPLE_EPIC)
        assert "REVISION REQUEST" in block
        assert "make the title shorter" in block
        assert _SAMPLE_EPIC["title"] in block

    def test_non_serializable_previous_does_not_raise(self):
        block = _build_revision_block("tweak it", object())
        assert "REVISION REQUEST" in block
        assert "tweak it" in block


class TestRegenerationWithFeedback:
    """Each generator appends the revision block only when feedback is given."""

    @staticmethod
    def _sent_prompt(mock_get_llm) -> str:
        messages = mock_get_llm.return_value.invoke.call_args[0][0]
        return messages[0].content

    @patch("yeaboi.agent.llm.get_llm")
    def test_epic_prompt_includes_feedback(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_EPIC))

        generate_sample_epic(_CALIBRATION, _EXAMPLES, feedback="less infra jargon", previous=_SAMPLE_EPIC)

        prompt = self._sent_prompt(mock_get_llm)
        assert "REVISION REQUEST" in prompt
        assert "less infra jargon" in prompt
        assert _SAMPLE_EPIC["title"] in prompt

    @patch("yeaboi.agent.llm.get_llm")
    def test_epic_prompt_unchanged_without_feedback(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_EPIC))

        generate_sample_epic(_CALIBRATION, _EXAMPLES)

        assert "REVISION REQUEST" not in self._sent_prompt(mock_get_llm)

    @patch("yeaboi.agent.llm.get_llm")
    def test_stories_prompt_includes_feedback(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_STORIES))

        generate_sample_stories(
            _CALIBRATION, _SAMPLE_EPIC, _EXAMPLES, feedback="split S1 in two", previous=_SAMPLE_STORIES
        )

        prompt = self._sent_prompt(mock_get_llm)
        assert "REVISION REQUEST" in prompt
        assert "split S1 in two" in prompt
        assert "Implement failover" in prompt  # marker from previous stories

    @patch("yeaboi.agent.llm.get_llm")
    def test_tasks_prompt_includes_feedback(self, mock_get_llm):
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_SAMPLE_TASKS))

        generate_sample_tasks(
            _CALIBRATION, _SAMPLE_STORIES, _EXAMPLES, feedback="add docs tasks", previous=_SAMPLE_TASKS
        )

        prompt = self._sent_prompt(mock_get_llm)
        assert "REVISION REQUEST" in prompt
        assert "add docs tasks" in prompt
        assert "Build health endpoint" in prompt  # marker from previous tasks

    @patch("yeaboi.agent.llm.get_llm")
    def test_sprint_prompt_includes_feedback(self, mock_get_llm):
        sprint = {"sprint_name": "Sprint 1", "velocity_target": 20, "stories_included": ["S1"]}
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(sprint))

        generate_sample_sprint(
            _CALIBRATION, _SAMPLE_STORIES, _SAMPLE_TASKS, _EXAMPLES, feedback="lower the target", previous=sprint
        )

        prompt = self._sent_prompt(mock_get_llm)
        assert "REVISION REQUEST" in prompt
        assert "lower the target" in prompt
        assert "Sprint 1" in prompt  # marker from previous sprint

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_still_returned_with_feedback(self, mock_get_llm):
        """Feedback path must not break the deterministic fallback on LLM error."""
        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = generate_sample_epic(_CALIBRATION, _EXAMPLES, feedback="anything", previous=_SAMPLE_EPIC)

        assert isinstance(result, dict)
        assert "Fallback" in result["rationale"]


# ---------------------------------------------------------------------------
# Headline stats, recommendations and the analysis narrative LLM call
# ---------------------------------------------------------------------------


def _make_profile(**overrides):
    from yeaboi.team_profile import (
        DoDSignal,
        SpilloverStats,
        StoryPointCalibration,
        TeamProfile,
        WritingPatterns,
    )

    defaults = dict(
        team_id="jira-SCRUM",
        source="jira",
        project_key="SCRUM",
        sample_sprints=4,
        sample_stories=40,
        velocity_avg=20.0,
        velocity_stddev=9.0,
        point_calibrations=(StoryPointCalibration(point_value=3, avg_cycle_time_days=5.0, sample_count=12),),
        estimation_accuracy_pct=72.0,
        sprint_completion_rate=55.0,
        spillover=SpilloverStats(carried_over_pct=22.0),
        dod_signal=DoDSignal(stories_with_pr_link_pct=10.0),
        writing_patterns=WritingPatterns(uses_given_when_then=True, median_ac_count=3.0),
    )
    defaults.update(overrides)
    return TeamProfile(**defaults)


_STATS_EXAMPLES = {
    "team_size": 5,
    "sprint_details": [
        {"name": "S1", "points": 18, "planned": 10, "rate": 60},
        {"name": "S2", "points": 22, "planned": 12, "rate": 50},
    ],
    "scope_changes": {
        "totals": {
            "avg_committed_velocity": 30.0,
            "avg_delivered_velocity": 20.0,
            "total_stories": 40,
            "added_mid_sprint": 8,
            "re_estimated": 2,
        },
        "per_sprint": [
            {"name": "S1", "committed_pts": 30, "scope_churn": 0.4},
            {"name": "S2", "committed_pts": 28, "scope_churn": 0.35},
        ],
    },
    "contributor_stats": [
        {"name": "Ana", "per_sprint": 4.0, "delivery_pts": 30},
        {"name": "Bo", "per_sprint": 1.5, "delivery_pts": 10, "sprints_active": 3},
    ],
}

_NARRATIVE_JSON = {
    "executive_summary": "Health is mixed; scope volatility is the biggest risk.",
    "sections": {
        "velocity": "Velocity swings a lot.",
        "team": "Two people do most of the work.",
        "estimation": "Estimates are decent.",
        "workflow": "Little evidence of a DoD.",
        "writing": "Ticket writing is solid.",
        "trends": "No clear trend yet.",
        "recommendations": "Lock scope after planning.",
    },
}


class TestComputeHeadlineStats:
    """compute_headline_stats mirrors the screen's velocity/completion maths."""

    def test_prefers_sprint_details_over_profile(self):
        from yeaboi.tools.team_learning import compute_headline_stats

        stats = compute_headline_stats(_make_profile(), _STATS_EXAMPLES)
        assert stats["velocity"] == 20.0  # (18+22)/2, not profile's stale avg
        assert stats["stddev"] == 2.0
        assert stats["completion_rate"] == 55.0
        assert stats["delivery_accuracy"] == 67
        assert stats["team_size"] == 5

    def test_falls_back_to_profile_without_examples(self):
        from yeaboi.tools.team_learning import compute_headline_stats

        stats = compute_headline_stats(_make_profile(), None)
        assert stats["velocity"] == 20.0
        assert stats["stddev"] == 9.0
        assert stats["completion_rate"] == 55.0
        assert stats["delivery_accuracy"] == 0

    def test_empty_profile_no_crash(self):
        from yeaboi.tools.team_learning import compute_headline_stats

        empty = _make_profile(velocity_avg=0.0, velocity_stddev=0.0, sprint_completion_rate=0.0)
        stats = compute_headline_stats(empty, {})
        assert stats["var_pct"] == 0


class TestComputeRecommendations:
    """The deterministic recommendation list (ported from the screen)."""

    def test_flags_expected_warnings(self):
        from yeaboi.tools.team_learning import compute_recommendations

        labels = [label for label, _ in compute_recommendations(_make_profile(), _STATS_EXAMPLES)]
        joined = " ".join(labels)
        assert "Low sprint completion" in joined
        assert "Frequent spillover" in joined
        assert "High scope churn" in joined

    def test_healthy_profile_flags_nothing(self):
        from yeaboi.team_profile import DoDSignal, SpilloverStats, WritingPatterns
        from yeaboi.tools.team_learning import compute_recommendations

        healthy = _make_profile(
            velocity_stddev=2.0,
            sprint_completion_rate=90.0,
            spillover=SpilloverStats(carried_over_pct=5.0),
            dod_signal=DoDSignal(),
            writing_patterns=WritingPatterns(),
        )
        assert compute_recommendations(healthy, None) == []


class TestGenerateAnalysisNarrative:
    """One LLM call producing the executive summary + per-section explanations."""

    @staticmethod
    def _sent_prompt(mock_get_llm) -> str:
        messages = mock_get_llm.return_value.invoke.call_args[0][0]
        return messages[0].content

    @patch("yeaboi.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_analysis_narrative

        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_NARRATIVE_JSON))
        result = _generate_analysis_narrative(_make_profile(), _STATS_EXAMPLES)

        assert result["executive_summary"].startswith("Health is mixed")
        assert result["sections"]["velocity"] == "Velocity swings a lot."
        assert set(result["sections"]) == {
            "velocity",
            "team",
            "estimation",
            "workflow",
            "writing",
            "trends",
            "recommendations",
        }

    @patch("yeaboi.agent.llm.get_llm")
    def test_prompt_contains_metrics_digest(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_analysis_narrative

        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_NARRATIVE_JSON))
        _generate_analysis_narrative(_make_profile(), _STATS_EXAMPLES)

        prompt = self._sent_prompt(mock_get_llm)
        assert "Metrics digest" in prompt
        assert "velocity 20.0±2.0" in prompt
        assert "5 contributors" in prompt
        assert "Scrum coach" in prompt
        assert "Return ONLY a JSON object" in prompt

    @patch("yeaboi.agent.llm.get_llm")
    def test_code_fences_stripped(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_analysis_narrative

        fenced = "```json\n" + json.dumps(_NARRATIVE_JSON) + "\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(fenced)
        result = _generate_analysis_narrative(_make_profile(), _STATS_EXAMPLES)
        assert result["sections"]["team"] == "Two people do most of the work."

    def test_llm_error_returns_fallback(self):
        from yeaboi.tools.team_learning import _generate_analysis_narrative

        with patch("yeaboi.agent.llm.get_llm", side_effect=RuntimeError("no key")):
            result = _generate_analysis_narrative(_make_profile(), _STATS_EXAMPLES)
        assert result["executive_summary"]
        assert set(result["sections"]) >= {"velocity", "recommendations"}

    @patch("yeaboi.agent.llm.get_llm")
    def test_invalid_json_returns_fallback(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_analysis_narrative

        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("not json at all")
        result = _generate_analysis_narrative(_make_profile(), _STATS_EXAMPLES)
        assert result["executive_summary"]
        assert len(result["sections"]) == 7

    @patch("yeaboi.agent.llm.get_llm")
    def test_missing_sections_backfilled(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_analysis_narrative

        partial = {"executive_summary": "Short.", "sections": {"velocity": "Only this one."}}
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(partial))
        result = _generate_analysis_narrative(_make_profile(), _STATS_EXAMPLES)
        assert result["sections"]["velocity"] == "Only this one."
        assert result["sections"]["workflow"]  # deterministic back-fill

    @patch("yeaboi.agent.llm.get_llm")
    def test_unknown_keys_dropped(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_analysis_narrative

        noisy = dict(_NARRATIVE_JSON)
        noisy["sections"] = dict(_NARRATIVE_JSON["sections"], bogus="drop me")
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(noisy))
        result = _generate_analysis_narrative(_make_profile(), _STATS_EXAMPLES)
        assert "bogus" not in result["sections"]

    def test_fallback_narrative_never_raises_on_empty(self):
        from yeaboi.tools.team_learning import _fallback_narrative

        empty = _make_profile(
            velocity_avg=0.0,
            velocity_stddev=0.0,
            sprint_completion_rate=0.0,
            point_calibrations=(),
        )
        result = _fallback_narrative(empty, None)
        assert len(result["sections"]) == 7


# ---------------------------------------------------------------------------
# Team insights (start / stop / keep / try coaching)
# ---------------------------------------------------------------------------

_INSIGHTS_JSON = {
    "start": [{"title": "Link PRs to tickets", "detail": "Add PR links to every story.", "evidence": "10% PR linkage"}],
    "stop": [{"title": "Overcommitting sprints", "detail": "Plan to actual capacity.", "evidence": "55% completion"}],
    "keep": [{"title": "Given/When/Then ACs", "detail": "Structured ACs work well.", "evidence": "GWT detected"}],
    "try": [{"title": "WIP limits", "detail": "Cap in-progress work.", "evidence": "22% spillover"}],
}

_INSIGHT_KEYS = ("start", "stop", "keep", "try")


class TestGenerateTeamInsights:
    """One LLM call producing start/stop/keep/try coaching insights."""

    @staticmethod
    def _sent_prompt(mock_get_llm) -> str:
        messages = mock_get_llm.return_value.invoke.call_args[0][0]
        return messages[0].content

    @patch("yeaboi.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_team_insights

        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_INSIGHTS_JSON))
        result = _generate_team_insights(_make_profile(), _STATS_EXAMPLES)

        assert set(result) == set(_INSIGHT_KEYS)
        assert result["start"][0]["title"] == "Link PRs to tickets"
        assert result["try"][0]["evidence"] == "22% spillover"

    @patch("yeaboi.agent.llm.get_llm")
    def test_prompt_contains_metrics_digest(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_team_insights

        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(_INSIGHTS_JSON))
        _generate_team_insights(_make_profile(), _STATS_EXAMPLES)

        prompt = self._sent_prompt(mock_get_llm)
        assert "Metrics digest" in prompt
        assert "velocity 20.0±2.0" in prompt
        assert "agile coach" in prompt
        assert "Return ONLY a JSON object" in prompt

    @patch("yeaboi.agent.llm.get_llm")
    def test_code_fences_stripped(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_team_insights

        fenced = "```json\n" + json.dumps(_INSIGHTS_JSON) + "\n```"
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(fenced)
        result = _generate_team_insights(_make_profile(), _STATS_EXAMPLES)
        assert result["stop"][0]["title"] == "Overcommitting sprints"

    def test_llm_error_returns_fallback(self):
        from yeaboi.tools.team_learning import _generate_team_insights

        with patch("yeaboi.agent.llm.get_llm", side_effect=RuntimeError("no key")):
            result = _generate_team_insights(_make_profile(), _STATS_EXAMPLES)
        assert all(result[k] for k in _INSIGHT_KEYS)

    @patch("yeaboi.agent.llm.get_llm")
    def test_invalid_json_returns_fallback(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_team_insights

        mock_get_llm.return_value.invoke.return_value = _mock_llm_response("not json at all")
        result = _generate_team_insights(_make_profile(), _STATS_EXAMPLES)
        assert all(result[k] for k in _INSIGHT_KEYS)

    @patch("yeaboi.agent.llm.get_llm")
    def test_missing_categories_backfilled(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_team_insights

        partial = {"start": _INSIGHTS_JSON["start"]}
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(partial))
        result = _generate_team_insights(_make_profile(), _STATS_EXAMPLES)
        assert result["start"][0]["title"] == "Link PRs to tickets"
        assert result["stop"]  # deterministic back-fill
        assert result["keep"]
        assert result["try"]

    @patch("yeaboi.agent.llm.get_llm")
    def test_malformed_items_filtered(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_team_insights

        noisy = dict(_INSIGHTS_JSON)
        noisy["keep"] = ["not a dict", {"title": ""}, {"detail": "no title"}]
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(noisy))
        result = _generate_team_insights(_make_profile(), _STATS_EXAMPLES)
        # All keep items were malformed — category falls back deterministically
        assert result["keep"]
        assert all(it["title"] for it in result["keep"])

    @patch("yeaboi.agent.llm.get_llm")
    def test_missing_detail_and_evidence_coerced(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_team_insights

        sparse = dict(_INSIGHTS_JSON)
        sparse["try"] = [{"title": "Just a title"}]
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(sparse))
        result = _generate_team_insights(_make_profile(), _STATS_EXAMPLES)
        assert result["try"][0] == {"title": "Just a title", "detail": "", "evidence": ""}

    @patch("yeaboi.agent.llm.get_llm")
    def test_items_capped_per_category(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_team_insights

        overfull = dict(_INSIGHTS_JSON)
        overfull["start"] = [{"title": f"Item {i}", "detail": "d", "evidence": "e"} for i in range(8)]
        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(overfull))
        result = _generate_team_insights(_make_profile(), _STATS_EXAMPLES)
        assert len(result["start"]) == 4


class TestFallbackTeamInsights:
    """Deterministic coaching insights derived from recommendations + stats."""

    def test_all_categories_non_empty(self):
        from yeaboi.tools.team_learning import _fallback_team_insights

        result = _fallback_team_insights(_make_profile(), _STATS_EXAMPLES)
        assert all(result[k] for k in _INSIGHT_KEYS)

    def test_warnings_map_to_stop(self):
        from yeaboi.tools.team_learning import _fallback_team_insights

        result = _fallback_team_insights(_make_profile(), _STATS_EXAMPLES)
        stop_titles = " ".join(it["title"] for it in result["stop"])
        assert "Low sprint completion" in stop_titles or "Frequent spillover" in stop_titles

    def test_notices_map_to_start(self):
        from yeaboi.tools.team_learning import _fallback_team_insights

        result = _fallback_team_insights(_make_profile(), _STATS_EXAMPLES)
        start_titles = " ".join(it["title"] for it in result["start"])
        assert "Low PR linkage" in start_titles

    def test_healthy_profile_still_fills_every_category(self):
        from yeaboi.team_profile import DoDSignal, SpilloverStats, WritingPatterns
        from yeaboi.tools.team_learning import _fallback_team_insights

        healthy = _make_profile(
            velocity_stddev=2.0,
            sprint_completion_rate=90.0,
            spillover=SpilloverStats(carried_over_pct=5.0),
            dod_signal=DoDSignal(),
            writing_patterns=WritingPatterns(uses_given_when_then=True),
        )
        result = _fallback_team_insights(healthy, None)
        assert all(result[k] for k in _INSIGHT_KEYS)
        keep_evidence = " ".join(it["evidence"] for it in result["keep"])
        assert "90% average sprint completion" in keep_evidence

    def test_spillover_triggers_wip_limit_experiment(self):
        from yeaboi.tools.team_learning import _fallback_team_insights

        result = _fallback_team_insights(_make_profile(), _STATS_EXAMPLES)
        try_titles = " ".join(it["title"] for it in result["try"])
        assert "WIP limits" in try_titles

    def test_empty_profile_no_crash(self):
        from yeaboi.tools.team_learning import _fallback_team_insights

        empty = _make_profile(
            velocity_avg=0.0,
            velocity_stddev=0.0,
            sprint_completion_rate=0.0,
            sample_sprints=0,
            point_calibrations=(),
        )
        result = _fallback_team_insights(empty, None)
        assert all(result[k] for k in _INSIGHT_KEYS)

    def test_items_capped(self):
        from yeaboi.tools.team_learning import _fallback_team_insights

        result = _fallback_team_insights(_make_profile(), _STATS_EXAMPLES)
        assert all(len(result[k]) <= 4 for k in _INSIGHT_KEYS)


class TestSynthesisEvidenceCap:
    """The synthesis prompt keeps only recent examples per point value — the
    digest's aggregates already include every story."""

    _SYNTHESIS_JSON = {
        "point_descriptions": {"3": "Routine story."},
        "narrative": {"executive_summary": "Fine.", "sections": {}},
        "insights": {},
    }

    @staticmethod
    def _point_inputs():
        stories = [
            {"issue_key": f"S-{index}", "summary": f"Story {index}", "points": 3.0, "discipline": "backend"}
            for index in range(20)
        ] + [
            {"issue_key": f"B-{index}", "summary": f"Big {index}", "points": 5.0, "discipline": "backend"}
            for index in range(2)
        ]
        return (stories, (), {}, {})

    @patch("yeaboi.agent.llm.get_llm")
    def test_prompt_keeps_last_six_per_value_and_discloses_omissions(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_analysis_synthesis

        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(self._SYNTHESIS_JSON))
        _generate_analysis_synthesis(_make_profile(), _STATS_EXAMPLES, self._point_inputs(), include_insights=False)

        messages = mock_get_llm.return_value.invoke.call_args[0][0]
        prompt = messages[0].content
        assert prompt.count('"key": "S-') == 6  # capped from 20
        assert prompt.count('"key": "B-') == 2  # small groups untouched
        assert '"S-19"' in prompt and '"S-0"' not in prompt  # newest kept
        assert '"evidence_omitted": {"3.0": 14}' in prompt

    @patch("yeaboi.agent.llm.get_llm")
    def test_capped_material_round_trips_through_cache(self, mock_get_llm, tmp_path):
        from yeaboi.tools.team_learning import _generate_analysis_synthesis

        mock_get_llm.return_value.invoke.return_value = _mock_llm_response(json.dumps(self._SYNTHESIS_JSON))
        db = tmp_path / "analysis.db"
        first = _generate_analysis_synthesis(
            _make_profile(), _STATS_EXAMPLES, self._point_inputs(), include_insights=False, db_path=db
        )
        second = _generate_analysis_synthesis(
            _make_profile(), _STATS_EXAMPLES, self._point_inputs(), include_insights=False, db_path=db
        )
        assert not first.get("_cache_hit")
        assert second.get("_cache_hit") is True
