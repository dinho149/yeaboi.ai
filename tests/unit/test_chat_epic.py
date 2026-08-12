"""Tests for the chat epic reformat helper — profile load, LLM reformat, fallbacks."""

from unittest.mock import MagicMock, patch

from tests._node_helpers import make_dummy_analysis
from yeaboi.ui.session.chat._epic import _quarter_label_for, load_epic_profile, reformat_epic_to_team_style

_EXAMPLES = {
    "naming_conventions": {
        "epic_naming_style": "plain",
        "template_sections": [("Goal", 1), ("Scope", 1)],
    }
}

_REPLY = (
    '{"title": "Team Epic Title", "description": "Team-style description", '
    '"stories_estimate": 3, "points_estimate": 8, "rationale": "matches house style"}'
)


def _reformat(state, reply=_REPLY, side_effect=None):
    llm = MagicMock(return_value=MagicMock(content=reply), side_effect=side_effect)
    with (
        patch("yeaboi.agent.nodes._load_profile_by_id", return_value=(object(), _EXAMPLES)),
        patch("yeaboi.agent.nodes._format_team_calibration", return_value="CALIBRATION"),
        patch("yeaboi.tools.team_learning._llm_invoke", llm),
    ):
        return reformat_epic_to_team_style(state)


class TestReformat:
    def test_success_rebuilds_analysis_with_team_naming(self):
        analysis = make_dummy_analysis()
        state = {"project_analysis": analysis, "analysis_profile_id": "team-1"}
        profile_id, examples = _reformat(state)
        assert profile_id == "team-1"
        assert examples is _EXAMPLES
        rebuilt = state["project_analysis"]
        assert rebuilt.project_name == "Team Epic Title"
        assert rebuilt.project_description == "Team-style description"
        # Every other field survives the rebuild untouched.
        assert rebuilt.tech_stack == analysis.tech_stack
        assert rebuilt.target_sprints == analysis.target_sprints

    def test_code_fenced_reply_still_parses(self):
        state = {"project_analysis": make_dummy_analysis(), "analysis_profile_id": "team-1"}
        _reformat(state, reply=f"```json\n{_REPLY}\n```")
        assert state["project_analysis"].project_name == "Team Epic Title"

    def test_llm_error_keeps_original_analysis(self):
        analysis = make_dummy_analysis()
        state = {"project_analysis": analysis, "analysis_profile_id": "team-1"}
        profile_id, _examples = _reformat(state, side_effect=RuntimeError("api down"))
        assert profile_id == "team-1"
        assert state["project_analysis"] is analysis  # failure is non-fatal

    def test_unparseable_reply_keeps_original_analysis(self):
        analysis = make_dummy_analysis()
        state = {"project_analysis": analysis, "analysis_profile_id": "team-1"}
        _reformat(state, reply="Sorry, here is the epic: Better Title")
        assert state["project_analysis"] is analysis

    def test_dry_run_makes_no_profile_or_llm_calls(self):
        state = {"project_analysis": make_dummy_analysis(), "analysis_profile_id": "team-1"}
        with (
            patch("yeaboi.agent.nodes._load_profile_by_id", side_effect=AssertionError("profile load hit the network")),
            patch("yeaboi.tools.team_learning._llm_invoke", side_effect=AssertionError("LLM ran in dry-run")),
        ):
            profile_id, examples = reformat_epic_to_team_style(state, dry_run=True)
        assert profile_id == "team-1"
        assert examples is None

    def test_missing_analysis_is_a_noop(self):
        assert reformat_epic_to_team_style({}) == ("", None)

    def test_no_profile_skips_the_llm(self):
        state = {"project_analysis": make_dummy_analysis()}
        with (
            patch("yeaboi.agent.nodes._load_team_profile", return_value=None),
            patch("yeaboi.agent.nodes._load_team_examples", return_value=None),
            patch("yeaboi.tools.team_learning._llm_invoke", side_effect=AssertionError("LLM ran without a profile")),
        ):
            profile_id, examples = reformat_epic_to_team_style(state)
        assert profile_id == ""
        assert examples is None


class TestLoadEpicProfile:
    def test_explicit_profile_id_wins(self):
        state = {"analysis_profile_id": "team-9"}
        with patch("yeaboi.agent.nodes._load_profile_by_id", return_value=("profile", _EXAMPLES)) as load:
            profile_id, examples = load_epic_profile(state)
        load.assert_called_once_with("team-9")
        assert profile_id == "team-9"
        assert examples is _EXAMPLES
        assert state["_epic_profile"] == "profile"

    def test_auto_detect_uses_team_id(self):
        profile = MagicMock(team_id="detected-team")
        state = {}
        with (
            patch("yeaboi.agent.nodes._load_team_profile", return_value=profile),
            patch("yeaboi.agent.nodes._load_team_examples", return_value=_EXAMPLES),
        ):
            profile_id, examples = load_epic_profile(state)
        assert profile_id == "detected-team"
        assert examples is _EXAMPLES

    def test_load_failure_is_non_fatal(self):
        state = {"analysis_profile_id": "team-9"}
        with patch("yeaboi.agent.nodes._load_profile_by_id", side_effect=RuntimeError("db gone")):
            profile_id, examples = load_epic_profile(state)
        assert profile_id == "team-9"
        assert examples is None


class TestQuarterLabel:
    def test_single_quarter(self):
        analysis = make_dummy_analysis(target_sprints=2, sprint_length_weeks=2)
        label = _quarter_label_for({"sprint_start_date": "2026-01-15"}, analysis)
        assert label == "Q1|2026"

    def test_cross_quarter_range(self):
        analysis = make_dummy_analysis(target_sprints=4, sprint_length_weeks=2)
        label = _quarter_label_for({"sprint_start_date": "2026-03-20"}, analysis)
        assert label == "Q1|2026-Q2|2026"

    def test_bad_date_falls_back_without_raising(self):
        analysis = make_dummy_analysis(target_sprints=1, sprint_length_weeks=2)
        assert _quarter_label_for({"sprint_start_date": "not-a-date"}, analysis).startswith("Q")
