"""Tests for Small-project intake mode: essentials, capacity gating, and the
Small → Large switch (advisory + re-entry).

See README: "Project Intake Questionnaire" — intake modes and
"Guardrails" — human-in-the-loop (advisory).
"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from tests._node_helpers import VALID_ANALYSIS_JSON, make_completed_questionnaire
from yeaboi.agent.nodes import (
    _essentials_for_mode,
    _extract_capacity_deductions,
    _fetch_sprint_targets,
    _is_small_project_mode,
    _prepare_bank_holiday_choices,
    _reopen_intake_for_epic,
    apply_epic_switch,
    project_analyzer,
)
from yeaboi.agent.state import ProjectAnalysis, QuestionnaireState
from yeaboi.prompts.intake import (
    QUICK_ESSENTIALS,
    SMALL_PROJECT_ESSENTIALS,
    SMART_ESSENTIALS,
)


class TestModeConstants:
    """The TUI intake cards and the Small essential set."""

    def test_tui_cards_are_chat_roadmap_offline(self):
        # The live chat replaced the Small/Large cards: "chat" asks the size
        # in conversation (small_project/smart remain the state vocabulary);
        # "roadmap" analyzes the quarterly roadmap and hands off, Offline last.
        from yeaboi.ui.mode_select.screens._screens import _INTAKE_CARDS

        keys = [c["key"] for c in _INTAKE_CARDS]
        assert keys == ["chat", "roadmap", "offline"]

    def test_small_essentials_include_sprint_length(self):
        # Small essentials = project type, problem, DoD, team size, sprint
        # length, stack, and sprint targeting (existing sprint vs new).
        assert SMALL_PROJECT_ESSENTIALS == frozenset({2, 3, 4, 6, 8, 11, 27})

    def test_small_essentials_drop_capacity_questions(self):
        # No target sprints (Q10) and none of the capacity questions (Q28-Q30)
        # — Small does no capacity work. Q27 IS asked (with a tracker) but as
        # "add to an existing sprint or create new?", not capacity planning.
        assert 10 not in SMALL_PROJECT_ESSENTIALS
        for q in (28, 29, 30):
            assert q not in SMALL_PROJECT_ESSENTIALS


class TestEssentialsForMode:
    """_essentials_for_mode() picks the right set per intake mode."""

    def test_quick(self):
        assert _essentials_for_mode("quick") is QUICK_ESSENTIALS

    def test_small_project(self):
        assert _essentials_for_mode("small_project") is SMALL_PROJECT_ESSENTIALS

    def test_smart_default(self):
        assert _essentials_for_mode("smart") is SMART_ESSENTIALS

    def test_unknown_falls_back_to_smart(self):
        assert _essentials_for_mode("standard") is SMART_ESSENTIALS


class TestIsSmallProjectMode:
    def test_true_only_for_small_project(self):
        assert _is_small_project_mode("small_project") is True
        assert _is_small_project_mode("smart") is False
        assert _is_small_project_mode("quick") is False
        assert _is_small_project_mode(None) is False


class TestCapacityGating:
    """Small mode zeroes out all capacity deductions and bank-holiday detection."""

    def test_extract_capacity_returns_zeros_for_small(self):
        qs = QuestionnaireState(intake_mode="small_project")
        qs._detected_bank_holiday_days = 5  # would normally count
        qs.answers[29] = "20%"
        cap = _extract_capacity_deductions(qs)
        assert cap == {
            "capacity_bank_holiday_days": 0,
            "capacity_planned_leave_days": 0,
            "capacity_unplanned_leave_pct": 0,
            "capacity_onboarding_engineer_sprints": 0,
            "capacity_ktlo_engineers": 0,
            "capacity_discovery_pct": 0,
        }

    def test_extract_capacity_still_counts_for_smart(self):
        qs = QuestionnaireState(intake_mode="smart")
        qs._detected_bank_holiday_days = 3
        cap = _extract_capacity_deductions(qs)
        assert cap["capacity_bank_holiday_days"] == 3

    def test_prepare_bank_holidays_noop_for_small(self):
        qs = QuestionnaireState(intake_mode="small_project")
        qs._detected_bank_holiday_days = 4
        qs._detected_bank_holidays = [{"name": "X"}]
        _prepare_bank_holiday_choices(qs)
        assert qs._detected_bank_holiday_days == 0
        assert qs._detected_bank_holidays == []


class TestSmallProjectAdvisory:
    """project_analyzer flags oversized Small projects and coerces the plan flat."""

    def _small_state(self) -> dict:
        qs = make_completed_questionnaire()
        return {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": qs,
            "team_size": 3,
            "velocity_per_sprint": 15,
            "_intake_mode": "small_project",
        }

    def _mock_llm(self, monkeypatch):
        fake = MagicMock()
        fake.content = VALID_ANALYSIS_JSON  # target_sprints=4, skip_features absent
        llm = MagicMock()
        llm.invoke.return_value = fake
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: llm)

    def test_oversized_flag_set_when_analyzer_says_bigger(self, monkeypatch):
        self._mock_llm(monkeypatch)
        result = project_analyzer(self._small_state())
        # 4 target sprints + no skip_features → looks bigger than a small project.
        assert result["_small_project_oversized"] is True

    def test_analysis_coerced_flat_in_small_mode(self, monkeypatch):
        self._mock_llm(monkeypatch)
        result = project_analyzer(self._small_state())
        analysis = result["project_analysis"]
        assert analysis.skip_features is True
        assert analysis.target_sprints <= 2
        assert result["target_sprints"] <= 2

    def test_advisory_text_in_message(self, monkeypatch):
        self._mock_llm(monkeypatch)
        result = project_analyzer(self._small_state())
        text = result["messages"][0].content
        assert "bigger than a small project" in text
        assert "switch to large" in text.lower()

    def test_not_flagged_in_smart_mode(self, monkeypatch):
        self._mock_llm(monkeypatch)
        state = self._small_state()
        state["_intake_mode"] = "smart"
        result = project_analyzer(state)
        assert result["_small_project_oversized"] is False
        # Smart mode keeps the analyzer's own values (not coerced to a flat plan).
        assert result["project_analysis"].target_sprints == 4


class TestSprintTargetQuestion:
    """Small-mode Q27: add to an existing sprint, or create a new one."""

    def _targets(self):
        return [
            {
                "name": "PSOT Sprint 104",
                "external_id": "88",
                "state": "active",
                "start_date": "2026-03-02",
                "number": 104,
            },
            {
                "name": "PSOT Sprint 105",
                "external_id": "89",
                "state": "future",
                "start_date": "2026-03-16",
                "number": 105,
            },
        ]

    def test_setup_parks_choices_and_options(self, monkeypatch):
        from yeaboi.agent.nodes import _setup_small_sprint_target_question

        monkeypatch.setattr("yeaboi.agent.nodes._fetch_sprint_targets", lambda pref="": (self._targets(), "ok"))
        monkeypatch.setattr("yeaboi.agent.nodes._is_jira_configured", lambda: True)
        qs = QuestionnaireState(intake_mode="small_project")
        prompt = _setup_small_sprint_target_question(qs)

        assert prompt is not None
        assert "existing sprint" in prompt
        assert qs._follow_up_choices[27] == (
            "Add to PSOT Sprint 104 (active)",
            "Add to PSOT Sprint 105",
            "Create a new sprint",
            "Backlog (no sprint)",
        )
        assert qs._sprint_target_options == {"PSOT Sprint 104": "88", "PSOT Sprint 105": "89"}
        # The active sprint feeds the start-date offset machinery.
        assert qs._active_sprint_number == 104
        assert qs._active_sprint_start_date == "2026-03-02"
        assert qs.answers[27] == "_active:104"

    def test_setup_returns_none_on_fetch_failure(self, monkeypatch):
        from yeaboi.agent.nodes import _setup_small_sprint_target_question

        monkeypatch.setattr("yeaboi.agent.nodes._fetch_sprint_targets", lambda pref="": ([], "connection failed"))
        qs = QuestionnaireState(intake_mode="small_project")
        assert _setup_small_sprint_target_question(qs) is None
        assert 27 not in qs._follow_up_choices

    def test_resolve_add_to_strips_active_suffix(self):
        from yeaboi.agent.nodes import _resolve_small_sprint_target_answer

        qs = QuestionnaireState(intake_mode="small_project")
        qs._sprint_target_options = {"PSOT Sprint 104": "88"}
        qs.answers[27] = "Add to PSOT Sprint 104 (active)"
        qs._follow_up_choices[27] = ("Add to PSOT Sprint 104 (active)",)
        _resolve_small_sprint_target_answer(qs)
        assert qs.answers[27] == "Add to PSOT Sprint 104"
        assert 27 not in qs._follow_up_choices

    def test_resolve_create_new_uses_max_plus_one(self):
        from yeaboi.agent.nodes import _resolve_small_sprint_target_answer

        qs = QuestionnaireState(intake_mode="small_project")
        qs._sprint_target_options = {"PSOT Sprint 104": "88", "PSOT Sprint 105": "89"}
        qs._active_sprint_number = 104
        qs.answers[27] = "Create a new sprint"
        _resolve_small_sprint_target_answer(qs)
        assert qs.answers[27] == "Sprint 106"

    def test_resolve_create_new_without_numbers_falls_back(self):
        from yeaboi.agent.nodes import _resolve_small_sprint_target_answer

        qs = QuestionnaireState(intake_mode="small_project")
        qs._sprint_target_options = {"Hardening": "12"}
        qs.answers[27] = "Create a new sprint"
        _resolve_small_sprint_target_answer(qs)
        assert qs.answers[27] == "Fresh start (today)"

    def test_resolve_backlog_keeps_the_sentinel(self):
        from yeaboi.agent.nodes import _resolve_small_sprint_target_answer

        qs = QuestionnaireState(intake_mode="small_project")
        qs.answers[27] = "Backlog (no sprint)"
        qs._follow_up_choices[27] = ("Create a new sprint", "Backlog (no sprint)")
        _resolve_small_sprint_target_answer(qs)
        assert qs.answers[27] == "Backlog (no sprint)"
        assert 27 not in qs._follow_up_choices

    def test_resolve_backlog_from_free_text(self):
        from yeaboi.agent.nodes import _resolve_small_sprint_target_answer

        qs = QuestionnaireState(intake_mode="small_project")
        qs.answers[27] = "just put it in the backlog"
        _resolve_small_sprint_target_answer(qs)
        assert qs.answers[27] == "Backlog (no sprint)"

    def test_fallback_question_with_known_number(self):
        from yeaboi.agent.nodes import _setup_small_sprint_fallback_question

        qs = QuestionnaireState(intake_mode="small_project")
        prompt = _setup_small_sprint_fallback_question(qs, 24, "Last analysed sprint: **Sprint 24**.\n\n")
        assert "backlog" in prompt
        assert qs._follow_up_choices[27] == ("Create Sprint 25 (next)", "Backlog (no sprint)")
        assert qs.answers[27] == "_active:24"
        assert qs._active_sprint_number == 24

    def test_fallback_question_without_number(self):
        from yeaboi.agent.nodes import _setup_small_sprint_fallback_question

        qs = QuestionnaireState(intake_mode="small_project")
        prompt = _setup_small_sprint_fallback_question(qs, None, "")
        assert "backlog" in prompt
        assert qs._follow_up_choices[27] == ("Create a new sprint", "Backlog (no sprint)")
        assert qs.answers[27] == "_targets"

    def test_analyzer_clamps_to_one_sprint_when_targeting_existing(self, monkeypatch):
        fake = MagicMock()
        fake.content = VALID_ANALYSIS_JSON
        llm = MagicMock()
        llm.invoke.return_value = fake
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: llm)

        qs = make_completed_questionnaire()
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": qs,
            "team_size": 3,
            "velocity_per_sprint": 15,
            "_intake_mode": "small_project",
            "sprint_target_mode": "existing",
            "target_sprint_name": "PSOT Sprint 104",
        }
        result = project_analyzer(state)
        assert result["project_analysis"].target_sprints == 1

    def test_analyzer_clamps_to_one_sprint_for_backlog(self, monkeypatch):
        fake = MagicMock()
        fake.content = VALID_ANALYSIS_JSON
        llm = MagicMock()
        llm.invoke.return_value = fake
        monkeypatch.setattr("yeaboi.agent.nodes.get_llm", lambda **kw: llm)

        qs = make_completed_questionnaire()
        state = {
            "messages": [HumanMessage(content="continue")],
            "questionnaire": qs,
            "team_size": 3,
            "velocity_per_sprint": 15,
            "_intake_mode": "small_project",
            "sprint_target_mode": "backlog",
        }
        result = project_analyzer(state)
        assert result["project_analysis"].target_sprints == 1


class TestFetchSprintTargets:
    """Board-sprint targets for the Q27 "add to an existing sprint" menu."""

    def _patch_trackers(self, monkeypatch, jira: bool = False, azdo: bool = False) -> None:
        monkeypatch.setattr("yeaboi.agent.nodes._is_jira_configured", lambda: jira)
        monkeypatch.setattr("yeaboi.agent.nodes._is_azdevops_configured", lambda: azdo)

    def test_jira_targets_carry_fields_and_parse_trailing_numbers(self, monkeypatch):
        self._patch_trackers(monkeypatch, jira=True)
        fake_client = object()
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: fake_client)
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "PROJ")
        monkeypatch.setattr("yeaboi.tools.jira.find_scrum_board_id", lambda jira, key: 7)
        calls: dict = {}

        def fake_fetch(jira, board_id, states):
            calls["jira"], calls["board_id"], calls["states"] = jira, board_id, states
            return [
                {"id": 101, "name": "Sprint 12", "state": "active", "start_date": "2026-08-10", "end_date": ""},
                {"id": None, "name": "Hardening", "state": "future", "start_date": "", "end_date": ""},
            ]

        monkeypatch.setattr("yeaboi.tools.jira.fetch_board_sprints", fake_fetch)

        targets, status = _fetch_sprint_targets()
        # The same scrum-filtered board and only the open states.
        assert calls["jira"] is fake_client
        assert calls["board_id"] == 7
        assert calls["states"] == ("active", "future")
        assert targets[0] == {
            "name": "Sprint 12",
            "external_id": "101",
            "state": "active",
            "start_date": "2026-08-10",
            "number": 12,
        }
        # No trailing digits → number None; a None id → empty external_id.
        assert targets[1]["number"] is None
        assert targets[1]["external_id"] == ""
        assert status == "2 open sprint(s) on the board"

    def test_no_jira_board_returns_empty_with_reason(self, monkeypatch):
        self._patch_trackers(monkeypatch, jira=True)
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: object())
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "PROJ")
        monkeypatch.setattr("yeaboi.tools.jira.find_scrum_board_id", lambda jira, key: None)

        targets, status = _fetch_sprint_targets()
        assert targets == []
        assert status == "No Jira board found for project PROJ"

    def test_jira_fetch_error_degrades_to_empty_with_message(self, monkeypatch):
        self._patch_trackers(monkeypatch, jira=True)
        monkeypatch.setattr("yeaboi.tools.jira._make_jira_client", lambda: object())
        monkeypatch.setattr("yeaboi.config.get_jira_project_key", lambda: "PROJ")

        def _boom(jira, key):
            raise RuntimeError("boom")

        monkeypatch.setattr("yeaboi.tools.jira.find_scrum_board_id", _boom)

        targets, status = _fetch_sprint_targets()
        assert targets == []
        assert status == "Jira connection failed: boom"

    def test_azdo_targets_drop_past_and_sort_active_first(self, monkeypatch):
        self._patch_trackers(monkeypatch, azdo=True)
        iterations = [
            {"name": "Sprint 3", "path": "\\P\\Sprint 3", "time_frame": "future", "start_date": "2026-09-01"},
            {"name": "Sprint 1", "path": "\\P\\Sprint 1", "time_frame": "past", "start_date": "2026-07-01"},
            {"name": "Sprint 2", "path": "\\P\\Sprint 2", "time_frame": "current", "start_date": "2026-08-10"},
        ]
        monkeypatch.setattr("yeaboi.tools.azure_devops.fetch_team_iterations_meta", lambda: iterations)

        targets, status = _fetch_sprint_targets()
        assert [t["name"] for t in targets] == ["Sprint 2", "Sprint 3"]  # past dropped, active first
        assert targets[0]["state"] == "active"
        assert targets[0]["external_id"] == "\\P\\Sprint 2"
        assert targets[1]["state"] == "future"
        assert status == "2 open iteration(s) for the team"

    def test_no_tracker_configured(self, monkeypatch):
        self._patch_trackers(monkeypatch)
        targets, status = _fetch_sprint_targets()
        assert targets == []
        assert status == "No tracker configured"


class TestApplyEpicSwitch:
    """apply_epic_switch() preserves answers and clears artifacts for the switch."""

    def _switched_state(self) -> dict:
        qs = QuestionnaireState(intake_mode="small_project", completed=True)
        qs.answers = {2: "Greenfield", 3: "solve X", 6: "3 engineers"}
        return {
            "_intake_mode": "small_project",
            "questionnaire": qs,
            "project_analysis": ProjectAnalysis(
                project_name="P",
                project_description="d",
                project_type="greenfield",
                goals=("g",),
                end_users=("u",),
                target_state="t",
                tech_stack=("py",),
                integrations=(),
                constraints=(),
                sprint_length_weeks=2,
                target_sprints=1,
                risks=(),
                out_of_scope=(),
                assumptions=(),
            ),
            "features": ["f"],
            "stories": ["s"],
            "tasks": ["t"],
            "sprints": ["sp"],
            "pending_review": "project_analyzer",
            "_small_project_oversized": True,
        }

    def test_preserves_answers_and_switches_mode(self):
        state = self._switched_state()
        apply_epic_switch(state)
        qs = state["questionnaire"]
        assert qs.intake_mode == "smart"
        assert state["_intake_mode"] == "smart"
        assert qs.completed is False
        assert qs._reopen_for_epic is True
        # Answers untouched — the whole point: no re-typing.
        assert qs.answers == {2: "Greenfield", 3: "solve X", 6: "3 engineers"}

    def test_clears_artifacts(self):
        state = self._switched_state()
        apply_epic_switch(state)
        for key in ("project_analysis", "features", "stories", "tasks", "sprints", "_small_project_oversized"):
            assert key not in state


class TestReopenIntakeForEpic:
    """_reopen_intake_for_epic() asks the remaining Epic essentials (or the summary)."""

    def test_asks_gap_when_essentials_missing(self):
        # Only Small essentials answered — Epic still needs Q10/Q27, so a gap remains.
        qs = QuestionnaireState(intake_mode="smart")
        qs._reopen_for_epic = True
        for q in SMALL_PROJECT_ESSENTIALS:
            qs.answers[q] = "answered"
        result = _reopen_intake_for_epic({"_intake_mode": "smart"}, qs)
        assert qs._reopen_for_epic is False
        assert isinstance(result["messages"][0], AIMessage)
        assert "Large" in result["messages"][0].content
        # Not yet at the confirmation summary — a real question was asked.
        assert result.get("pending_review") != "project_intake"

    def test_shows_summary_when_no_gaps(self):
        # Every question answered (incl. conditional essentials Q7/Q12/Q13) →
        # no gaps remain, so we fall through to the summary/PTO gate rather than
        # asking another essential. (Smart mode asks PTO before the summary.)
        qs = make_completed_questionnaire()
        qs.intake_mode = "smart"
        qs.completed = False
        qs.awaiting_confirmation = False
        qs._reopen_for_epic = True
        result = _reopen_intake_for_epic({"_intake_mode": "smart"}, qs)
        # Either the confirmation summary (pending_review) or the PTO gate — both
        # are the "no more essentials to ask" outcome, not a fresh gap question.
        reached_summary_gate = result.get("pending_review") == "project_intake" or qs._awaiting_leave_input
        assert reached_summary_gate
