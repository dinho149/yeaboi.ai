"""Adversarial LLM-output coverage for the parser and validation stack.

# See docs: "Architecture" — four layers, three design principles
# See docs: "Scrum Standards" — story points, priority, feature decomposition

Extension of the per-node parser tests focused on scenarios not already covered
in test_analyzer.py / test_feature_generator.py / test_story_writer.py /
test_task_decomposer.py.

What this adds over the individual node parser tests
-----------------------------------------------------
* **Vague / empty descriptions** — preamble+fence combination that prevents
  clean stripping, parametrized sweep of 8 bad-input forms, partial JSON.
* **Contradictory field values** — case-insensitive priority normalisation for
  all four values; extreme story points (0 → 1, 99 → 8); wrong type for a
  numeric field; 100% invalid feature_ids producing a fallback; non-dict scalars
  in the array.
* **Absurdly large scope** — 50 feature response, 30 stories/feature, 50 stories
  across multiple features, 30-story-id sprint, ``_validate_stories`` per-feature
  count warning.

Tests already in the individual node files are NOT repeated here:
  ``test_bad_json_returns_fallback``, ``test_empty_response_returns_fallback``,
  ``test_invalid_priority_defaults_to_medium``, ``test_code_fence_stripping``,
  ``test_invalid_feature_id_skipped``, ``test_skips_items_with_unknown_story_id``,
  ``test_rounds_to_nearest_fibonacci``.
"""

from __future__ import annotations

import json

from tests._node_helpers import (
    make_completed_questionnaire,
    make_dummy_analysis,
    make_sample_features,
    make_sample_stories,
)
from yeaboi.agent.nodes import (
    _parse_analysis_response,
    _parse_features_response,
    _parse_sprints_response,
    _parse_stories_response,
    _validate_stories,
)
from yeaboi.agent.state import Priority, StoryPointValue
from yeaboi.prompts.feature_generator import MAX_FEATURES
from yeaboi.prompts.story_writer import MAX_STORIES_PER_FEATURE

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _qs():
    qs = make_completed_questionnaire()
    qs.answers[1] = "Task management SaaS for engineering teams"
    qs.answers[2] = "greenfield"
    return qs


# ---------------------------------------------------------------------------
# Vague / empty project descriptions
# ---------------------------------------------------------------------------


class TestVagueProjectDescriptions:
    """Unique fallback paths not covered by test_analyzer.py.

    # See docs: "Architecture" — project_analyzer node, fallback path
    """

    def test_preamble_plus_fence_parses_gracefully(self):
        """LLM preamble before the JSON fence prevents clean stripping → fallback.

        Some providers output: 'Sure! Here's the analysis:\n```json\n{...}\n```'
        The fence stripper only handles fences at the start of the string, so
        preamble text causes a parse failure. This documents that the fallback
        fires (no crash) — not a bug report.
        """
        preamble = "Sure, here's the analysis you requested:\n"
        json_body = (
            '{"project_name": "TodoApp", "project_type": "greenfield", "sprint_length_weeks": 2, "target_sprints": 3}'
        )
        with_fence = f"{preamble}```json\n{json_body}\n```"
        result = _parse_analysis_response(with_fence, _qs(), 5, 20)
        assert result is not None  # no crash; fallback used

    def test_minimal_json_uses_defaults_for_missing_fields(self):
        """A one-field JSON dict returns ProjectAnalysis with sane defaults."""
        from yeaboi.agent.state import ProjectAnalysis

        result = _parse_analysis_response('{"project_name": "MinimalApp"}', _qs(), 5, 20)
        assert isinstance(result, ProjectAnalysis)
        assert result.project_name == "MinimalApp"
        assert result.goals == ()
        assert result.sprint_length_weeks == 2

    def test_fallback_always_returns_project_analysis(self):
        """Every known malformed-input form returns a ProjectAnalysis, never None."""
        from yeaboi.agent.state import ProjectAnalysis

        bad_inputs = [
            "",
            "not json at all",
            "null",
            "{}",
            "[]",
            '{"bad": "structure"}',
            "true",
            "42",
        ]
        for raw in bad_inputs:
            result = _parse_analysis_response(raw, _qs(), 5, 20)
            assert isinstance(result, ProjectAnalysis), (
                f"Expected ProjectAnalysis for {raw!r}, got {type(result).__name__}"
            )


# ---------------------------------------------------------------------------
# Contradictory / invalid field values
# ---------------------------------------------------------------------------


class TestContradictoryFieldValues:
    """Impossible/invalid field values are corrected, not crashed on.

    # See docs: "Scrum Standards" — priority levels, Fibonacci story points
    """

    def test_all_four_valid_priorities_preserved_case_insensitively(self):
        """CRITICAL / High / medium / low are all normalised to the Priority enum."""
        raw = json.dumps(
            [
                {"id": "F1", "title": "F1", "description": "d", "priority": "CRITICAL"},
                {"id": "F2", "title": "F2", "description": "d", "priority": "High"},
                {"id": "F3", "title": "F3", "description": "d", "priority": "medium"},
                {"id": "F4", "title": "F4", "description": "d", "priority": "low"},
            ]
        )
        features = _parse_features_response(raw, make_dummy_analysis())
        assert [f.priority for f in features] == [Priority.CRITICAL, Priority.HIGH, Priority.MEDIUM, Priority.LOW]

    def test_extreme_story_points_clamped_to_fibonacci(self):
        """story_points=99 clamps to 8; story_points=0 clamps to 1."""
        features = make_sample_features()
        stories_raw = json.dumps(
            [
                {
                    "id": "US-F1-001",
                    "feature_id": "F1",
                    "persona": "u",
                    "goal": "g",
                    "benefit": "b",
                    "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                    "story_points": 99,
                    "priority": "high",
                },
                {
                    "id": "US-F1-002",
                    "feature_id": "F1",
                    "persona": "u",
                    "goal": "g",
                    "benefit": "b",
                    "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                    "story_points": 0,
                    "priority": "medium",
                },
            ]
        )
        stories = _parse_stories_response(stories_raw, features, make_dummy_analysis())
        assert stories[0].story_points == StoryPointValue.EIGHT
        assert stories[1].story_points == StoryPointValue.ONE

    def test_sprint_length_weeks_wrong_type_defaults_to_two(self):
        """``sprint_length_weeks: "two"`` (string instead of int) defaults to 2."""
        raw = '{"project_name": "X", "sprint_length_weeks": "two", "target_sprints": "3"}'
        result = _parse_analysis_response(raw, _qs(), 5, 20)
        assert result.sprint_length_weeks == 2

    def test_all_stories_with_invalid_feature_id_trigger_fallback(self):
        """If every story references a non-existent feature, fallback stories are generated."""
        features = make_sample_features()  # F1, F2, F3
        stories_raw = json.dumps(
            [
                {
                    "id": "US-X-001",
                    "feature_id": "DOES_NOT_EXIST",
                    "persona": "u",
                    "goal": "g",
                    "benefit": "b",
                    "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                    "story_points": 3,
                    "priority": "high",
                },
            ]
        )
        # All skipped → empty list → _build_fallback_stories runs
        stories = _parse_stories_response(stories_raw, features, make_dummy_analysis())
        assert len(stories) >= len(features)

    def test_non_dict_items_in_features_array_skipped(self):
        """Hallucinated scalars (strings, numbers, null) in the features array are skipped."""
        raw = json.dumps(
            [
                "this is a string, not an object",
                42,
                None,
                {"id": "F1", "title": "Valid", "description": "desc", "priority": "high"},
            ]
        )
        features = _parse_features_response(raw, make_dummy_analysis())
        assert len(features) == 1
        assert features[0].title == "Valid"


# ---------------------------------------------------------------------------
# Absurdly large scope
# ---------------------------------------------------------------------------


class TestAbsurdlyLargeScope:
    """Oversized LLM outputs (50+ features, 30+ stories/feature) do not crash.

    # See docs: "Scrum Standards" — feature decomposition (3-6 features rule)
    #
    # The feature_generator prompt instructs the LLM to return 3-6 features.
    # The parser does NOT enforce this cap — it returns whatever the LLM sent.
    # Enforcement of the 3-6 rule lives in the prompt (MAX_FEATURES=6 constant).
    # ``_validate_stories`` DOES warn when per-feature story count exceeds
    # MAX_STORIES_PER_FEATURE (5).
    """

    def test_fifty_features_no_crash(self):
        """50-feature response is parsed without error.

        Documents that the parser is permissive — the prompt budget is the
        enforcement mechanism, not the parser.
        """
        raw = json.dumps(
            [
                {"id": f"F{i}", "title": f"Feature {i}", "description": f"Scope {i}", "priority": "medium"}
                for i in range(1, 51)
            ]
        )
        features = _parse_features_response(raw, make_dummy_analysis())
        assert len(features) == 50

    def test_parser_does_not_enforce_max_features_cap(self):
        """Parser accepts MAX_FEATURES+4 features — confirms enforcement is in the prompt."""
        raw = json.dumps(
            [
                {"id": f"F{i}", "title": f"Feature {i}", "description": "d", "priority": "high"}
                for i in range(1, MAX_FEATURES + 5)
            ]
        )
        features = _parse_features_response(raw, make_dummy_analysis())
        assert len(features) == MAX_FEATURES + 4

    def test_thirty_stories_per_feature_no_crash(self):
        """30 stories for a single feature are all parsed (no truncation)."""
        features = [f for f in make_sample_features() if f.id == "F1"]
        stories_data = [
            {
                "id": f"US-F1-{i:03d}",
                "feature_id": "F1",
                "persona": "user",
                "goal": f"feature {i}",
                "benefit": "value",
                "acceptance_criteria": [
                    {"given": "g", "when": "w", "then": "t"},
                    {"given": "g", "when": "w", "then": "t"},
                    {"given": "g", "when": "w", "then": "t"},
                ],
                "story_points": 3,
                "priority": "medium",
            }
            for i in range(1, 31)
        ]
        stories = _parse_stories_response(json.dumps(stories_data), features, make_dummy_analysis())
        assert len(stories) == 30

    def test_validate_stories_warns_for_overcrowded_feature(self):
        """``_validate_stories`` issues a warning when a single feature exceeds MAX_STORIES_PER_FEATURE.

        # See docs: "Scrum Standards" — story count constraints
        """
        features = [f for f in make_sample_features() if f.id == "F1"]
        stories_data = [
            {
                "id": f"US-F1-{i:03d}",
                "feature_id": "F1",
                "persona": "user",
                "goal": f"feature {i}",
                "benefit": "value",
                "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                "story_points": 2,
                "priority": "medium",
            }
            for i in range(1, MAX_STORIES_PER_FEATURE + 3)  # 3 over the recommended limit
        ]
        stories = _parse_stories_response(json.dumps(stories_data), features, make_dummy_analysis())
        _, warnings = _validate_stories(stories, features)
        over_limit = [w for w in warnings if "maximum" in w.lower() or "stories" in w.lower()]
        assert len(over_limit) > 0, f"Expected overcapacity warning, got: {warnings}"

    def test_fifty_stories_across_three_features_no_crash(self):
        """50 stories spread across 3 features are all parsed — no truncation."""
        features = make_sample_features()
        stories_data = [
            {
                "id": f"US-F{(i % 3) + 1}-{i:03d}",
                "feature_id": f"F{(i % 3) + 1}",
                "persona": "user",
                "goal": f"feature {i}",
                "benefit": "value",
                "acceptance_criteria": [{"given": "g", "when": "w", "then": "t"}],
                "story_points": 3,
                "priority": "medium",
            }
            for i in range(1, 51)
        ]
        stories = _parse_stories_response(json.dumps(stories_data), features, make_dummy_analysis())
        assert len(stories) == 50

    def test_sprint_with_duplicate_story_ids_no_crash(self):
        """Sprint plan containing duplicate/overcapacity story_ids is handled by the validator."""
        stories = make_sample_stories()
        story_ids = [s.id for s in stories]
        sprints_data = json.dumps(
            [
                {
                    "id": "SP-1",
                    "name": "Mega Sprint",
                    "goal": "Do everything",
                    "capacity_points": 300,
                    "story_ids": story_ids * 10,  # duplicates
                }
            ]
        )
        result = _parse_sprints_response(sprints_data, stories, velocity=10)
        assert result is not None
        assert isinstance(result, list)

    def test_empty_features_triggers_fallback(self):
        """Empty JSON array for features → _build_fallback_features generates 3 generic features."""
        features = _parse_features_response("[]", make_dummy_analysis())
        assert len(features) == 3

    def test_empty_stories_triggers_fallback(self):
        """Empty JSON array for stories → _build_fallback_stories generates at least one per feature."""
        features = make_sample_features()
        stories = _parse_stories_response("[]", features, make_dummy_analysis())
        assert len(stories) >= len(features)
