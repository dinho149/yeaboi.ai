"""Tests for project history persistence."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from yeaboi.agent.state import (
    AcceptanceCriterion,
    Discipline,
    Feature,
    Priority,
    QuestionnaireState,
    StoryPointValue,
    UserStory,
)
from yeaboi.persistence import (
    _compute_jira_summary,
    _compute_progress,
    _compute_status,
    _extract_artifact_counts,
    _extract_pipeline_progress,
    _extract_project_name,
    _relative_time,
    create_project_id,
    delete_project,
    export_project_html,
    export_project_json,
    export_project_md,
    export_project_plan,
    load_graph_state,
    load_projects,
    migrate_history_file,
    save_project_snapshot,
)
from yeaboi.ui.mode_select import ProjectSummary


@pytest.fixture(autouse=True)
def _isolate_config_dir(monkeypatch, tmp_path):
    """Redirect persistence to a temp directory to avoid touching real ~/.scrum-agent/."""
    monkeypatch.setattr("yeaboi.persistence._CONFIG_DIR", tmp_path)
    monkeypatch.setattr("yeaboi.persistence._PROJECTS_FILE", tmp_path / "projects.json")
    monkeypatch.setattr("yeaboi.persistence._STATES_DIR", tmp_path / "states")
    return tmp_path


class TestCreateProjectId:
    def test_returns_uuid_string(self):
        pid = create_project_id()
        assert isinstance(pid, str)
        assert len(pid) == 36  # UUID4 format: 8-4-4-4-12

    def test_unique_ids(self):
        ids = {create_project_id() for _ in range(10)}
        assert len(ids) == 10


class TestSaveAndLoad:
    def test_save_creates_file(self, _isolate_config_dir):
        state = {"messages": ["hello"]}
        save_project_snapshot("proj-1", state)
        assert (_isolate_config_dir / "projects.json").exists()

    def test_save_and_load_roundtrip(self, _isolate_config_dir):
        qs = QuestionnaireState()
        qs.answers[1] = "My Cool App"
        qs.answers[3] = "A productivity tool"
        qs.completed = True
        state = {
            "messages": ["msg"],
            "questionnaire": qs,
            "features": [Feature(id="f1", title="Feature 1", description="desc", priority=Priority.HIGH)],
        }

        save_project_snapshot("proj-1", state)
        projects = load_projects()

        assert len(projects) == 1
        p = projects[0]
        assert p.name == "My Cool App"
        assert p.id == "proj-1"
        assert p.status == "In Progress"
        assert p.feature_count == 1
        assert p.progress == "3/7 stages complete"  # description_input + intake_complete + feature_generator

    def test_upsert_existing_project(self, _isolate_config_dir):
        state1 = {"messages": ["msg"]}
        save_project_snapshot("proj-1", state1)

        state2 = {
            "messages": ["msg"],
            "features": [Feature(id="f1", title="E1", description="d", priority=Priority.HIGH)],
        }
        save_project_snapshot("proj-1", state2)

        projects = load_projects()
        assert len(projects) == 1
        assert projects[0].feature_count == 1

    def test_multiple_projects(self, _isolate_config_dir):
        save_project_snapshot("proj-1", {"messages": ["a"]})
        save_project_snapshot("proj-2", {"messages": ["b"]})

        projects = load_projects()
        assert len(projects) == 2

    def test_load_empty_file(self, _isolate_config_dir):
        projects = load_projects()
        assert projects == []

    def test_load_corrupt_file(self, _isolate_config_dir):
        (_isolate_config_dir / "projects.json").write_text("not json", encoding="utf-8")
        projects = load_projects()
        assert projects == []

    def test_chat_fields_roundtrip(self, _isolate_config_dir):
        # Chat-planning presentation state must survive resume: the greeting
        # flag skips the pre-intake exchange, the preamble rebuilds the
        # transcript (it lives OUTSIDE messages — see agent/state.py).
        state = {
            "messages": ["msg"],
            "_chat_greeting_done": True,
            "_chat_preamble": [
                {"role": "ai", "text": "Hey — tell me about your project."},
                {"role": "user", "text": "a todo app"},
            ],
            "_chat_fast_forward": True,
        }
        save_project_snapshot("proj-chat", state)
        loaded = load_graph_state("proj-chat")
        assert loaded["_chat_greeting_done"] is True
        assert loaded["_chat_preamble"] == state["_chat_preamble"]
        # /finish fast mode must survive resume so a fast run keeps auto-running.
        assert loaded["_chat_fast_forward"] is True

    def test_old_snapshot_without_chat_fields_loads(self, _isolate_config_dir):
        save_project_snapshot("proj-old", {"messages": ["msg"]})
        loaded = load_graph_state("proj-old")
        assert "_chat_greeting_done" not in loaded
        assert "_chat_preamble" not in loaded

    def test_preserves_created_at_on_upsert(self, _isolate_config_dir):
        save_project_snapshot("proj-1", {"messages": ["a"]})
        raw1 = json.loads((_isolate_config_dir / "projects.json").read_text())
        created_at = raw1["projects"][0]["created_at"]

        save_project_snapshot("proj-1", {"messages": ["b"]})
        raw2 = json.loads((_isolate_config_dir / "projects.json").read_text())
        assert raw2["projects"][0]["created_at"] == created_at

    def test_graph_state_roundtrip_restores_enums(self, _isolate_config_dir):
        """Enum fields in frozen dataclasses must be restored as actual enums, not raw strings."""
        qs = QuestionnaireState(completed=True)
        feature = Feature(id="F1", title="Feature 1", description="desc", priority=Priority.HIGH)
        story = UserStory(
            id="US-E1-001",
            feature_id="F1",
            persona="developer",
            goal="set up the environment",
            benefit="the team can start building",
            acceptance_criteria=(AcceptanceCriterion(given="a machine", when="following docs", then="it works"),),
            story_points=StoryPointValue.FIVE,
            priority=Priority.CRITICAL,
            discipline=Discipline.FULLSTACK,
            dod_applicable=(True, True, True, True, True, True, True),
        )
        state = {
            "messages": ["msg"],
            "questionnaire": qs,
            "features": [feature],
            "stories": [story],
        }
        save_project_snapshot("proj-enum", state)

        loaded = load_graph_state("proj-enum")
        assert loaded is not None

        # Features: priority must be an actual Priority enum
        loaded_feature = loaded["features"][0]
        assert isinstance(loaded_feature.priority, Priority)
        assert loaded_feature.priority is Priority.HIGH
        assert loaded_feature.priority.value == "high"

        # Stories: priority, story_points, discipline must be enums; tuples must be tuples
        loaded_story = loaded["stories"][0]
        assert isinstance(loaded_story.priority, Priority)
        assert loaded_story.priority is Priority.CRITICAL
        assert isinstance(loaded_story.story_points, StoryPointValue)
        assert loaded_story.story_points is StoryPointValue.FIVE
        assert isinstance(loaded_story.discipline, Discipline)
        assert loaded_story.discipline is Discipline.FULLSTACK
        assert isinstance(loaded_story.acceptance_criteria, tuple)
        assert isinstance(loaded_story.acceptance_criteria[0], AcceptanceCriterion)
        assert isinstance(loaded_story.dod_applicable, tuple)

    def test_sorted_by_updated_at_desc(self, _isolate_config_dir):
        save_project_snapshot("proj-old", {"messages": ["a"]})
        save_project_snapshot("proj-new", {"messages": ["b"]})

        projects = load_projects()
        # proj-new was saved second so has later updated_at
        assert projects[0].id == "proj-new"


class TestDeleteProject:
    def test_delete_existing_project(self, _isolate_config_dir):
        save_project_snapshot("proj-1", {"messages": ["a"]})
        save_project_snapshot("proj-2", {"messages": ["b"]})
        assert len(load_projects()) == 2

        result = delete_project("proj-1")
        assert result is True
        projects = load_projects()
        assert len(projects) == 1
        assert projects[0].id == "proj-2"

    def test_delete_nonexistent_project(self, _isolate_config_dir):
        save_project_snapshot("proj-1", {"messages": ["a"]})
        result = delete_project("does-not-exist")
        assert result is False
        assert len(load_projects()) == 1

    def test_delete_last_project(self, _isolate_config_dir):
        save_project_snapshot("proj-1", {"messages": ["a"]})
        delete_project("proj-1")
        assert load_projects() == []

    def test_delete_from_empty(self, _isolate_config_dir):
        result = delete_project("no-projects")
        assert result is False

    def test_delete_removes_log_and_rotation_backups(self, _isolate_config_dir, monkeypatch, tmp_path):
        logs_dir = tmp_path / "logs" / "planning"
        logs_dir.mkdir(parents=True)
        monkeypatch.setattr("yeaboi.persistence._LOGS_DIR", logs_dir)

        save_project_snapshot("proj-1", {"messages": ["a"]})
        # Simulate a rotated session log: base file plus RotatingFileHandler backups.
        for name in ("proj-1.log", "proj-1.log.1", "proj-1.log.2"):
            (logs_dir / name).write_text("log line\n")
        (logs_dir / "proj-2.log").write_text("other session\n")

        assert delete_project("proj-1") is True
        assert not list(logs_dir.glob("proj-1.log*"))
        assert (logs_dir / "proj-2.log").exists()  # other sessions untouched


class TestExportProjectJson:
    def test_export_existing_project(self, _isolate_config_dir, tmp_path):
        qs = QuestionnaireState()
        qs.answers[1] = "My Cool App"
        save_project_snapshot("proj-1", {"messages": ["msg"], "questionnaire": qs})

        out_path = export_project_json("proj-1", output_dir=tmp_path)
        assert out_path is not None
        assert out_path.exists()
        assert out_path.name == "my-cool-app-export.json"

        data = json.loads(out_path.read_text())
        assert data["id"] == "proj-1"
        assert data["name"] == "My Cool App"

    def test_export_nonexistent_project(self, _isolate_config_dir, tmp_path):
        result = export_project_json("does-not-exist", output_dir=tmp_path)
        assert result is None

    def test_export_sanitizes_filename(self, _isolate_config_dir, tmp_path):
        qs = QuestionnaireState()
        qs.answers[1] = "My App / Special!"
        save_project_snapshot("proj-1", {"messages": ["msg"], "questionnaire": qs})

        out_path = export_project_json("proj-1", output_dir=tmp_path)
        assert out_path is not None
        # Special chars replaced with hyphens
        assert "/" not in out_path.name
        assert "!" not in out_path.name


class TestExportProjectHtml:
    def test_export_html_existing_project(self, _isolate_config_dir, tmp_path, monkeypatch):
        qs = QuestionnaireState()
        qs.answers[1] = "My Cool App"
        save_project_snapshot("proj-1", {"messages": ["msg"], "questionnaire": qs})

        # Mock the html exporter to avoid needing real graph state artifacts
        expected_path = tmp_path / "my-cool-app-plan.html"
        monkeypatch.setattr(
            "yeaboi.persistence.export_plan_html",
            lambda state, path: path,
            raising=False,
        )
        # The import is deferred, so we need to patch inside the function's import
        import yeaboi.persistence as _mod

        original = _mod.export_project_html

        def _patched(project_id, output_dir=None):
            import yeaboi.html_exporter

            monkeypatch.setattr(yeaboi.html_exporter, "export_plan_html", lambda state, path: path)
            return original(project_id, output_dir)

        out_path = _patched("proj-1", output_dir=tmp_path)
        assert out_path is not None
        assert out_path == expected_path

    def test_export_html_nonexistent_project(self, _isolate_config_dir, tmp_path):
        result = export_project_html("does-not-exist", output_dir=tmp_path)
        assert result is None


class TestExportProjectMd:
    def test_export_md_existing_project(self, _isolate_config_dir, tmp_path, monkeypatch):
        qs = QuestionnaireState()
        qs.answers[1] = "My Cool App"
        save_project_snapshot("proj-1", {"messages": ["msg"], "questionnaire": qs})

        expected_path = tmp_path / "my-cool-app-plan.md"
        import yeaboi.repl._io as _io_mod

        monkeypatch.setattr(_io_mod, "_export_plan_markdown", lambda state, path: path)

        out_path = export_project_md("proj-1", output_dir=tmp_path)
        assert out_path is not None
        assert out_path == expected_path

    def test_export_md_nonexistent_project(self, _isolate_config_dir, tmp_path):
        result = export_project_md("does-not-exist", output_dir=tmp_path)
        assert result is None


class TestExportDefaultDirectory:
    """Without an explicit output_dir, exports land in the planning export dir."""

    def test_default_is_planning_export_dir(self, _isolate_config_dir, tmp_path, monkeypatch):
        qs = QuestionnaireState()
        qs.answers[1] = "My Cool App"
        save_project_snapshot("proj-1", {"messages": ["msg"], "questionnaire": qs})

        import yeaboi.paths as paths_mod
        import yeaboi.repl._io as _io_mod

        monkeypatch.setattr(paths_mod, "PLANNING_EXPORTS_DIR", tmp_path / "planning")
        monkeypatch.setattr(_io_mod, "_export_plan_markdown", lambda state, path: path)

        out_path = export_project_md("proj-1")
        assert out_path == tmp_path / "planning" / "my-cool-app" / "my-cool-app-plan.md"

    def test_explicit_output_dir_still_wins(self, _isolate_config_dir, tmp_path, monkeypatch):
        qs = QuestionnaireState()
        qs.answers[1] = "My Cool App"
        save_project_snapshot("proj-1", {"messages": ["msg"], "questionnaire": qs})

        import yeaboi.repl._io as _io_mod

        monkeypatch.setattr(_io_mod, "_export_plan_markdown", lambda state, path: path)
        out_path = export_project_md("proj-1", output_dir=tmp_path)
        assert out_path == tmp_path / "my-cool-app-plan.md"


class TestExportProjectPlan:
    def test_exports_both_formats(self, _isolate_config_dir, tmp_path, monkeypatch):
        qs = QuestionnaireState()
        qs.answers[1] = "My App"
        save_project_snapshot("proj-1", {"messages": ["msg"], "questionnaire": qs})

        import yeaboi.html_exporter as _html_mod
        import yeaboi.repl._io as _io_mod

        monkeypatch.setattr(_html_mod, "export_plan_html", lambda state, path: path)
        monkeypatch.setattr(_io_mod, "_export_plan_markdown", lambda state, path: path)

        paths = export_project_plan("proj-1", output_dir=tmp_path)
        assert len(paths) == 2

    def test_empty_for_nonexistent(self, _isolate_config_dir, tmp_path):
        paths = export_project_plan("does-not-exist", output_dir=tmp_path)
        assert paths == []


class TestMigrateHistoryFile:
    def test_renames_old_file(self, _isolate_config_dir):
        old = _isolate_config_dir / "history"
        old.write_text("old content", encoding="utf-8")
        migrate_history_file()
        assert not old.exists()
        assert (_isolate_config_dir / "repl-history").exists()
        assert (_isolate_config_dir / "repl-history").read_text() == "old content"

    def test_noop_when_old_missing(self, _isolate_config_dir):
        migrate_history_file()  # should not raise
        assert not (_isolate_config_dir / "repl-history").exists()

    def test_noop_when_new_already_exists(self, _isolate_config_dir):
        old = _isolate_config_dir / "history"
        new = _isolate_config_dir / "repl-history"
        old.write_text("old", encoding="utf-8")
        new.write_text("new", encoding="utf-8")
        migrate_history_file()
        # Old file should NOT be renamed — new already exists
        assert old.exists()
        assert new.read_text() == "new"


class TestRelativeTime:
    def test_just_now(self):
        now = datetime.now(UTC).isoformat()
        assert _relative_time(now) == "just now"

    def test_minutes_ago(self):
        past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        assert _relative_time(past) == "5 minutes ago"

    def test_hours_ago(self):
        past = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        assert _relative_time(past) == "3 hours ago"

    def test_days_ago(self):
        past = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        assert _relative_time(past) == "2 days ago"

    def test_singular(self):
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        assert _relative_time(past) == "1 hour ago"

    def test_empty_string(self):
        assert _relative_time("") == ""

    def test_invalid_string(self):
        assert _relative_time("not-a-date") == ""


class TestComputeStatus:
    def test_complete(self):
        progress = {"sprint_planner": True}
        assert _compute_status(progress) == "Complete"

    def test_in_progress(self):
        progress = {"description_input": True, "sprint_planner": False}
        assert _compute_status(progress) == "In Progress"

    def test_new(self):
        progress = {}
        assert _compute_status(progress) == "New"


class TestComputeJiraSummary:
    def test_no_sync(self):
        assert _compute_jira_summary({}) == ""

    def test_partial_sync(self):
        sync = {
            "stories_synced": 3,
            "stories_total": 4,
            "tasks_synced": 0,
            "tasks_total": 0,
            "sprints_synced": 0,
            "sprints_total": 0,
        }
        assert _compute_jira_summary(sync) == "3/4 stories synced"

    def test_full_sync(self):
        sync = {
            "stories_synced": 15,
            "stories_total": 15,
            "tasks_synced": 30,
            "tasks_total": 30,
            "sprints_synced": 3,
            "sprints_total": 3,
        }
        result = _compute_jira_summary(sync)
        assert "15/15 stories" in result
        assert "30/30 tasks" in result
        assert "3/3 sprints" in result


class TestExtractPipelineProgress:
    def test_empty_state(self):
        progress = _extract_pipeline_progress({})
        assert progress["description_input"] is False
        assert progress["sprint_planner"] is False

    def test_with_messages_and_features(self):
        state = {
            "messages": ["msg"],
            "features": [Feature(id="f1", title="E", description="d", priority=Priority.HIGH)],
        }
        progress = _extract_pipeline_progress(state)
        assert progress["description_input"] is True
        assert progress["feature_generator"] is True
        assert progress["story_writer"] is False


class TestExtractProjectName:
    def test_from_questionnaire(self):
        qs = QuestionnaireState()
        qs.answers[1] = "My Project"
        assert _extract_project_name({"questionnaire": qs}) == "My Project"

    def test_fallback(self):
        assert _extract_project_name({}) == "Untitled Project"


class TestExtractArtifactCounts:
    def test_empty(self):
        counts = _extract_artifact_counts({})
        assert counts == {"features": 0, "stories": 0, "tasks": 0, "sprints": 0}

    def test_with_artifacts(self):
        state = {
            "features": [1, 2, 3],
            "stories": [1, 2],
        }
        counts = _extract_artifact_counts(state)
        assert counts["features"] == 3
        assert counts["stories"] == 2


class TestComputeProgress:
    def test_no_stages_complete(self):
        assert _compute_progress({}) == ""

    def test_some_stages_complete(self):
        pipeline = {"description_input": True, "intake_complete": True, "project_analyzer": True}
        assert _compute_progress(pipeline) == "3/7 stages complete"

    def test_all_stages_complete(self):
        pipeline = {
            stage: True
            for stage in (
                "description_input",
                "intake_complete",
                "project_analyzer",
                "feature_generator",
                "story_writer",
                "task_decomposer",
                "sprint_planner",
            )
        }
        assert _compute_progress(pipeline) == "All stages complete"

    def test_only_false_stages(self):
        pipeline = {"description_input": False, "intake_complete": False}
        assert _compute_progress(pipeline) == ""


class TestProjectSummaryDataclass:
    def test_new_fields_have_defaults(self):
        """Ensure new fields don't break existing code that only passes name."""
        p = ProjectSummary(name="Test")
        assert p.id == ""
        assert p.task_count == 0
        assert p.sprint_count == 0
        assert p.jira_summary == ""
        assert p.progress == ""
