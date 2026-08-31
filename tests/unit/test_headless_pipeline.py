"""Tests for the headless planning-pipeline driver (agent/headless.py)."""

import pytest

from tests._node_helpers import (
    make_completed_questionnaire,
    make_dummy_analysis,
    make_sample_features,
    make_sample_sprints,
    make_sample_stories,
)
from yeaboi.agent.headless import (
    HeadlessPipelineError,
    _next_auto_input,
    _predict_next_node,
    run_planning_pipeline,
)
from yeaboi.agent.state import QuestionnaireState, Task
from yeaboi.questionnaire_io import build_questionnaire_from_answers


def _make_sample_tasks() -> list[Task]:
    return [Task(id="T-1", story_id="US-1", title="Do the thing", description="Implement it")]


class FakeGraph:
    """Stub CompiledStateGraph that advances one pipeline stage per invoke().

    Mirrors what the real graph does at the state level: each invoke looks at
    which artifacts exist and produces the next one, setting pending_review
    like the generation nodes do. Keeps the driver's routing/auto-accept
    logic under test without any LLM calls.
    """

    def __init__(self, capacity_warning: bool = False):
        self.capacity_warning = capacity_warning
        self.invocations: list[str] = []

    def invoke(self, state: dict) -> dict:
        state = dict(state)
        self.invocations.append(state["messages"][-1].content)
        qs = state.get("questionnaire")
        node = _predict_next_node(state)
        if node == "project_intake":
            # Intake confirm — complete the questionnaire.
            assert isinstance(qs, QuestionnaireState)
            qs.awaiting_confirmation = False
            qs.completed = True
        elif node == "project_analyzer":
            state["project_analysis"] = make_dummy_analysis()
            state["pending_review"] = "project_analyzer"
        elif node in ("feature_generator", "feature_skip"):
            state["features"] = make_sample_features()
            state["pending_review"] = "feature_generator"
        elif node == "story_writer":
            state["stories"] = make_sample_stories()
            state["pending_review"] = "story_writer"
        elif node == "task_decomposer":
            state["tasks"] = _make_sample_tasks()
            state["pending_review"] = "task_decomposer"
        elif node == "sprint_planner":
            if self.capacity_warning and state.get("capacity_override_target", 0) == 0:
                # First pass: flag the overflow, produce no sprints yet.
                state["capacity_override_target"] = -3
            else:
                state["sprints"] = make_sample_sprints()
                state["pending_review"] = "sprint_planner"
        return state


@pytest.fixture
def no_session_logs(monkeypatch):
    """Keep the per-session log handler away from ~/.yeaboi during tests."""
    monkeypatch.setattr("yeaboi.logging_setup.attach_session_log", lambda _sid: None)
    monkeypatch.setattr("yeaboi.logging_setup.detach_session_log", lambda: None)


@pytest.fixture
def fake_graph(monkeypatch, no_session_logs):
    graph = FakeGraph()
    monkeypatch.setattr("yeaboi.agent.graph.create_graph", lambda tools=(): graph)
    return graph


class TestPredictNextNode:
    """_predict_next_node moved here from repl/_ui.py — spot-check + re-export."""

    def test_no_questionnaire_is_intake(self):
        assert _predict_next_node({}) == "project_intake"

    def test_completed_pipeline_is_agent(self):
        state = {
            "questionnaire": make_completed_questionnaire(),
            "project_analysis": make_dummy_analysis(),
            "features": make_sample_features(),
            "stories": make_sample_stories(),
            "tasks": _make_sample_tasks(),
            "sprints": make_sample_sprints(),
        }
        assert _predict_next_node(state) == "agent"

    def test_repl_ui_reexport_still_works(self):
        from yeaboi.repl._ui import _predict_next_node as reexported

        assert reexported is _predict_next_node


class TestNextAutoInput:
    """The auto-drive decision table."""

    def test_awaiting_confirmation_confirms(self):
        qs = QuestionnaireState(awaiting_confirmation=True)
        assert _next_auto_input({"questionnaire": qs}) == "confirm"

    def test_completed_mid_pipeline_continues(self):
        state = {"questionnaire": make_completed_questionnaire()}
        assert _next_auto_input(state) == "continue"

    def test_pending_review_accepts_and_clears(self):
        state = {
            "questionnaire": make_completed_questionnaire(),
            "project_analysis": make_dummy_analysis(),
            "pending_review": "project_analyzer",
            "last_review_decision": "x",
            "last_review_feedback": "y",
        }
        assert _next_auto_input(state) == "accept"
        assert "pending_review" not in state
        assert "last_review_decision" not in state
        assert "last_review_feedback" not in state

    def test_intake_pending_review_keeps_feedback_fields(self):
        qs = QuestionnaireState(awaiting_confirmation=True)
        state = {"questionnaire": qs, "pending_review": "project_intake", "last_review_feedback": "keep"}
        assert _next_auto_input(state) == "accept"
        assert state["last_review_feedback"] == "keep"

    def test_final_accept_completes_without_reinvoke(self):
        state = {
            "questionnaire": make_completed_questionnaire(),
            "project_analysis": make_dummy_analysis(),
            "features": make_sample_features(),
            "stories": make_sample_stories(),
            "tasks": _make_sample_tasks(),
            "sprints": make_sample_sprints(),
            "pending_review": "sprint_planner",
        }
        assert _next_auto_input(state) is None

    def test_capacity_warning_auto_accepts_recommended(self):
        state = {"questionnaire": make_completed_questionnaire(), "capacity_override_target": -3}
        result = _next_auto_input(state)
        assert result is not None
        assert state["capacity_override_target"] == 3

    def test_mid_intake_raises(self):
        qs = QuestionnaireState(current_question=5)
        with pytest.raises(HeadlessPipelineError):
            _next_auto_input({"questionnaire": qs})

    def test_spike_prompt_auto_answered_by_confidence_rule(self):
        # medium/low confidence → include; high → skip. The sentinel clears
        # and spike_choice is written so the node never re-asks.
        state = {
            "questionnaire": make_completed_questionnaire(),
            "_spike_prompt": {"chosen": "Monolith", "confidence": "medium", "recommended": "include"},
        }
        assert _next_auto_input(state) == "include the architecture spike"
        assert state["spike_choice"] == "include"
        assert state["_spike_prompt"] == {}

        state = {
            "questionnaire": make_completed_questionnaire(),
            "_spike_prompt": {"chosen": "Monolith", "confidence": "high", "recommended": "skip"},
        }
        assert _next_auto_input(state) == "skip the architecture spike"
        assert state["spike_choice"] == "skip"


class TestRunPlanningPipeline:
    """End-to-end driver behaviour against the FakeGraph."""

    def test_full_pipeline_completes(self, fake_graph):
        qs = build_questionnaire_from_answers({1: "A test project", 6: "4", 8: "2"})
        state = run_planning_pipeline(qs, save_session=False)
        assert state["sprints"]
        assert state["stories"]
        assert "pending_review" not in state
        # confirm → continue(analyzer) → accept ×4 (features, stories, tasks, sprints)
        assert fake_graph.invocations[0] == "confirm"  # intake confirmation gate
        assert fake_graph.invocations[1] == "continue"
        assert fake_graph.invocations[2:] == ["accept"] * 4
        assert len(fake_graph.invocations) == 6

    def test_the_prior_art_step_is_closed_before_the_graph_starts(self, fake_graph):
        """Answering "3" once the sub-loop opens is too late — by then the node
        has already paid for five repository reads, five tree walks and an LLM
        call whose output is thrown away."""
        qs = build_questionnaire_from_answers({1: "A test project", 2: "Greenfield"})
        run_planning_pipeline(qs, save_session=False)
        assert qs._prior_art_stage == "done"

    def test_a_caller_who_already_answered_it_is_left_alone(self, fake_graph):
        qs = build_questionnaire_from_answers({1: "A test project", 2: "Greenfield"})
        qs._prior_art_stage = "ask"
        run_planning_pipeline(qs, save_session=False)
        assert qs._prior_art_stage == "ask"

    def test_capacity_warning_auto_accepted(self, monkeypatch, no_session_logs):
        graph = FakeGraph(capacity_warning=True)
        monkeypatch.setattr("yeaboi.agent.graph.create_graph", lambda tools=(): graph)
        qs = build_questionnaire_from_answers({1: "A test project"})
        state = run_planning_pipeline(qs, save_session=False)
        assert state["sprints"]
        assert state["capacity_override_target"] == 3
        assert "accept recommended sprints" in graph.invocations

    def test_progress_callback_reports_nodes(self, fake_graph):
        seen: list[tuple[str, int]] = []
        qs = build_questionnaire_from_answers({1: "A test project"})
        run_planning_pipeline(qs, save_session=False, on_progress=lambda node, step: seen.append((node, step)))
        nodes = [n for n, _ in seen]
        assert nodes[0] == "project_intake"
        assert "sprint_planner" in nodes
        assert [s for _, s in seen] == list(range(len(seen)))

    def test_max_steps_guard(self, monkeypatch, no_session_logs):
        class StuckGraph:
            def invoke(self, state):
                return dict(state)  # never advances

        monkeypatch.setattr("yeaboi.agent.graph.create_graph", lambda tools=(): StuckGraph())
        qs = QuestionnaireState(awaiting_confirmation=True)
        with pytest.raises(HeadlessPipelineError, match="did not complete"):
            run_planning_pipeline(qs, save_session=False, max_steps=3)

    def test_session_persisted(self, fake_graph, tmp_path):
        db = tmp_path / "sessions.db"
        qs = build_questionnaire_from_answers({1: "My persisted project", 6: "4", 8: "2"})
        state = run_planning_pipeline(qs, session_id="new-cafe1234-2026-07-20", db_path=db)

        from yeaboi.sessions import SessionStore

        with SessionStore(db) as store:
            meta = store.get_session("new-cafe1234-2026-07-20")
            assert meta is not None
            assert meta["project_name"]  # analyzer name or Q1 fallback
            loaded = store.load_state("new-cafe1234-2026-07-20")
        assert loaded is not None
        assert loaded["sprints"]
        assert state["_session_id"] == "new-cafe1234-2026-07-20"

    def test_save_session_false_writes_nothing(self, fake_graph, tmp_path, monkeypatch):
        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        qs = build_questionnaire_from_answers({1: "A test project"})
        run_planning_pipeline(qs, save_session=False)
        assert not db.exists()


class TestProjectScopedPipeline:
    def test_project_id_links_session_and_seeds_profile(self, fake_graph, tmp_path):
        from yeaboi.projects.store import ProjectStore
        from yeaboi.sessions import SessionStore

        db = tmp_path / "sessions.db"
        with ProjectStore(db) as projects:
            project = projects.create("Apollo")
            projects.set_settings(project["project_id"], {"default_analysis_profile_id": "team-apollo"})

        qs = build_questionnaire_from_answers({1: "A test project", 6: "4", 8: "2"})
        state = run_planning_pipeline(qs, db_path=db, project_id=project["project_id"])

        assert state["project_id"] == project["project_id"]
        # The analysis→planning edge: the project's default profile is seeded.
        assert state["analysis_profile_id"] == "team-apollo"
        with SessionStore(db) as store:
            assert store.session_project_id(state["_session_id"]) == project["project_id"]

    def test_project_without_defaults_seeds_no_profile(self, fake_graph, tmp_path):
        from yeaboi.projects.store import ProjectStore

        db = tmp_path / "sessions.db"
        with ProjectStore(db) as projects:
            project = projects.create("Apollo")
        qs = build_questionnaire_from_answers({1: "A test project", 6: "4", 8: "2"})
        state = run_planning_pipeline(qs, db_path=db, project_id=project["project_id"])
        assert "analysis_profile_id" not in state

    def test_unscoped_run_carries_no_project_state(self, fake_graph):
        qs = build_questionnaire_from_answers({1: "A test project", 6: "4", 8: "2"})
        state = run_planning_pipeline(qs, save_session=False)
        assert "project_id" not in state


class TestContextDepsPipeline:
    def _project(self, tmp_path, settings=None):
        from yeaboi.projects.store import ProjectStore

        db = tmp_path / "sessions.db"
        with ProjectStore(db) as projects:
            project = projects.create("Apollo")
            if settings:
                projects.set_settings(project["project_id"], settings)
        return db, project["project_id"]

    def _qs(self):
        return build_questionnaire_from_answers({1: "A test project", 6: "4", 8: "2"})

    def test_explicit_deps_land_on_state_as_a_json_list(self, fake_graph, tmp_path):
        db, pid = self._project(tmp_path)
        state = run_planning_pipeline(self._qs(), db_path=db, project_id=pid, context_deps=["retro", "plan"])
        assert state["context_deps"] == '["plan", "retro"]'

    def test_incognito_lands_as_an_empty_list(self, fake_graph):
        state = run_planning_pipeline(self._qs(), save_session=False, context_deps=[])
        assert state["context_deps"] == "[]"

    def test_none_leaves_the_key_absent(self, fake_graph):
        state = run_planning_pipeline(self._qs(), save_session=False)
        assert "context_deps" not in state

    def test_project_default_deps_apply_when_caller_passes_none(self, fake_graph, tmp_path):
        db, pid = self._project(tmp_path, {"default_context_deps": ["standup"]})
        state = run_planning_pipeline(self._qs(), db_path=db, project_id=pid)
        assert state["context_deps"] == '["standup"]'

    def test_analysis_dep_off_suppresses_the_profile_seed(self, fake_graph, tmp_path):
        db, pid = self._project(tmp_path, {"default_analysis_profile_id": "team-apollo"})
        state = run_planning_pipeline(self._qs(), db_path=db, project_id=pid, context_deps=["retro"])
        assert "analysis_profile_id" not in state

    def test_analysis_dep_on_still_seeds_the_profile(self, fake_graph, tmp_path):
        db, pid = self._project(tmp_path, {"default_analysis_profile_id": "team-apollo"})
        state = run_planning_pipeline(self._qs(), db_path=db, project_id=pid, context_deps=["analysis"])
        assert state["analysis_profile_id"] == "team-apollo"
