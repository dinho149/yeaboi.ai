"""Unit tests for TeamProfile data model and TeamProfileStore SQLite CRUD.

Tests cover: dataclass construction, serialisation round-trip, store CRUD,
and the sessions.py v3 migration that creates the team_profiles table.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from yeaboi.team_profile import (
    DailyScopeSnapshot,
    DocQualitySignal,
    DoDSignal,
    EpicPattern,
    ScopeChangeEvent,
    SpilloverStats,
    SprintScopeTimeline,
    StoryPointCalibration,
    StoryShapePattern,
    TeamProfile,
    TeamProfileStore,
    WritingPatterns,
    _json_to_profile,
    _profile_to_json,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_profile(team_id: str = "jira-PROJ") -> TeamProfile:
    return TeamProfile(
        team_id=team_id,
        source="jira",
        project_key="PROJ",
        sample_sprints=5,
        sample_stories=42,
        velocity_avg=23.5,
        velocity_stddev=3.2,
        point_calibrations=(
            StoryPointCalibration(
                point_value=1,
                avg_cycle_time_days=0.5,
                sample_count=10,
                common_patterns=("config change",),
                typical_task_count=1.0,
                overshoot_pct=5.0,
            ),
            StoryPointCalibration(
                point_value=5,
                avg_cycle_time_days=4.2,
                sample_count=15,
                typical_task_count=3.0,
                overshoot_pct=20.0,
            ),
        ),
        story_shapes=(
            StoryShapePattern(
                discipline="backend",
                avg_points=3.2,
                avg_ac_count=3.0,
                avg_task_count=2.8,
                sample_count=20,
            ),
        ),
        epic_pattern=EpicPattern(
            avg_stories_per_epic=6.0,
            avg_points_per_epic=18.0,
            typical_story_count_range=(4, 9),
            sample_count=5,
        ),
        estimation_accuracy_pct=78.0,
        sprint_completion_rate=88.0,
    )


# ---------------------------------------------------------------------------
# Serialisation tests
# ---------------------------------------------------------------------------


class TestProfileSerialisation:
    def test_round_trip(self):
        profile = _make_profile()
        json_str = _profile_to_json(profile)
        restored = _json_to_profile(json_str)

        assert restored.team_id == profile.team_id
        assert restored.source == profile.source
        assert restored.sample_sprints == profile.sample_sprints
        assert restored.velocity_avg == profile.velocity_avg
        assert len(restored.point_calibrations) == 2
        assert restored.point_calibrations[0].point_value == 1
        assert restored.point_calibrations[0].common_patterns == ("config change",)
        assert len(restored.story_shapes) == 1
        assert restored.story_shapes[0].discipline == "backend"
        assert restored.epic_pattern.typical_story_count_range == (4, 9)

    def test_empty_profile(self):
        profile = TeamProfile(team_id="x", source="jira", project_key="X")
        json_str = _profile_to_json(profile)
        restored = _json_to_profile(json_str)
        assert restored.team_id == "x"
        assert restored.point_calibrations == ()
        assert restored.story_shapes == ()

    def test_json_is_valid(self):
        profile = _make_profile()
        json_str = _profile_to_json(profile)
        data = json.loads(json_str)
        assert data["team_id"] == "jira-PROJ"
        assert isinstance(data["point_calibrations"], list)


# ---------------------------------------------------------------------------
# TeamProfileStore CRUD tests
# ---------------------------------------------------------------------------


class TestTeamProfileStore:
    @pytest.fixture
    def db_path(self, tmp_path):
        return tmp_path / "sessions.db"

    def test_save_and_load(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded = store.load("jira-PROJ")

        assert loaded is not None
        assert loaded.team_id == "jira-PROJ"
        assert loaded.sample_sprints == 5
        assert loaded.velocity_avg == 23.5

    def test_load_missing_returns_none(self, db_path):
        with TeamProfileStore(db_path) as store:
            result = store.load("nonexistent")
        assert result is None

    def test_load_by_project(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded = store.load_by_project("PROJ", "jira")

        assert loaded is not None
        assert loaded.project_key == "PROJ"

    def test_load_by_project_wrong_source(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded = store.load_by_project("PROJ", "azdevops")
        assert loaded is None

    def test_upsert_updates_existing(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)

        updated = TeamProfile(
            team_id="jira-PROJ",
            source="jira",
            project_key="PROJ",
            sample_sprints=10,
            sample_stories=80,
            velocity_avg=25.0,
        )
        with TeamProfileStore(db_path) as store:
            store.save(updated)
            loaded = store.load("jira-PROJ")

        assert loaded is not None
        assert loaded.sample_sprints == 10
        assert loaded.velocity_avg == 25.0

    def test_delete_existing(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            deleted = store.delete("jira-PROJ")
            assert deleted is True
            assert store.load("jira-PROJ") is None

    def test_delete_nonexistent(self, db_path):
        with TeamProfileStore(db_path) as store:
            deleted = store.delete("ghost")
        assert deleted is False

    def test_list_profiles(self, db_path):
        with TeamProfileStore(db_path) as store:
            store.save(_make_profile("jira-PROJ"))
            store.save(_make_profile("azdevops-Alpha"))
            profiles = store.list_profiles()
        assert len(profiles) == 2

    def test_table_created_on_init(self, db_path):
        with TeamProfileStore(db_path):
            pass
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='team_profiles'").fetchall()
        conn.close()
        assert rows

    def test_save_and_load_with_examples(self, db_path):
        profile = _make_profile()
        examples = {
            "scope_changes": {
                "totals": {"added_mid_sprint": 2, "total_stories": 10},
                "timelines": [
                    SprintScopeTimeline(
                        sprint_name="S1",
                        committed_pts=20,
                        final_pts=25,
                        delivered_pts=18,
                        scope_change_total=5,
                        scope_churn=0.25,
                        change_events=(
                            ScopeChangeEvent(
                                date="2026-03-03",
                                issue_key="X-5",
                                change_type="added",
                                delta_pts=5,
                            ),
                        ),
                    ),
                ],
            },
            "calibration": [{"point_value": 3, "detail": "test"}],
        }
        with TeamProfileStore(db_path) as store:
            store.save(profile, examples=examples)
            loaded_p, loaded_ex = store.load_with_examples("jira-PROJ")

        assert loaded_p is not None
        assert loaded_p.velocity_avg == 23.5
        assert loaded_ex is not None
        assert loaded_ex["calibration"][0]["detail"] == "test"
        # Timelines should be reconstructed as dataclasses
        tls = loaded_ex["scope_changes"]["timelines"]
        assert len(tls) == 1
        assert tls[0].committed_pts == 20
        assert len(tls[0].change_events) == 1
        assert tls[0].change_events[0].issue_key == "X-5"

    def test_load_with_examples_no_examples(self, db_path):
        profile = _make_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded_p, loaded_ex = store.load_with_examples("jira-PROJ")
        assert loaded_p is not None
        assert loaded_ex is None

    def test_delete_cleans_up_files(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        # Create export and log files
        export_dir = tmp_path / "exports" / "proj"
        export_dir.mkdir(parents=True)
        (export_dir / "team-profile-20260327.html").write_text("html")
        (export_dir / "team-profile-20260327.md").write_text("md")
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True)
        (log_dir / "team-analysis-proj-20260327.log").write_text("log")
        (log_dir / "other-file.log").write_text("keep")

        profile = _make_profile()
        # Override project_key to match dir
        profile = TeamProfile(
            team_id="jira-PROJ",
            source="jira",
            project_key="PROJ",
            sample_sprints=5,
            velocity_avg=23.5,
        )
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            store.delete("jira-PROJ")

        assert not export_dir.exists()
        assert not (log_dir / "team-analysis-proj-20260327.log").exists()
        assert (log_dir / "other-file.log").exists()


# ---------------------------------------------------------------------------
# sessions.py migration v3 test
# ---------------------------------------------------------------------------


class TestSessionsMigration:
    def test_v3_migration_creates_team_profiles_table(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        from yeaboi.sessions import SessionStore

        with SessionStore(db_path) as store:
            assert store.schema_mismatch is False

        # Verify the team_profiles table was created
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='team_profiles'").fetchall()
        conn.close()
        assert rows, "team_profiles table should have been created by v3 migration"

    def test_schema_version_is_current(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        from yeaboi.sessions import CURRENT_SCHEMA_VERSION, SessionStore

        with SessionStore(db_path):
            pass

        conn = sqlite3.connect(str(db_path))
        row = conn.execute("SELECT schema_version FROM schema_info").fetchone()
        conn.close()
        assert row[0] == CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Reading the exported page
#
# The team profile is a React export: the page is a shell plus a boot payload,
# and the markup is built by a bundle this suite deliberately does not run. So
# an assertion about the *report* is an assertion about the payload. What the
# payload turns into — avatars, bars, tone colours — is asserted in
# `frontend/src/export/reports/Profile.test.tsx`, where the components run.
# ---------------------------------------------------------------------------


def _profile_sections(html: str) -> dict[str, dict]:
    """The exported profile's sections, keyed by section id."""
    from tests._pages import island

    report = island(html)["report"]
    assert report["kind"] == "profile", f"expected a profile payload, got {report['kind']}"
    return {section["id"]: section for section in report["sections"]}


def _blocks(html: str, section_id: str, kind: str) -> list[dict]:
    """Every block of one kind in one section, in payload order."""
    sections = _profile_sections(html)
    assert section_id in sections, f"no {section_id!r} section — got {sorted(sections)}"
    return [block for block in sections[section_id]["blocks"] if block["kind"] == kind]


# ---------------------------------------------------------------------------
# Extended data model tests (Phase A)
# ---------------------------------------------------------------------------


def _make_extended_profile(team_id: str = "jira-PROJ") -> TeamProfile:
    """Create a profile with all extended fields populated."""
    return TeamProfile(
        team_id=team_id,
        source="jira",
        project_key="PROJ",
        sample_sprints=8,
        sample_stories=64,
        velocity_avg=23.5,
        velocity_stddev=3.2,
        point_calibrations=(
            StoryPointCalibration(point_value=1, avg_cycle_time_days=0.5, sample_count=10),
            StoryPointCalibration(point_value=5, avg_cycle_time_days=4.2, sample_count=15, overshoot_pct=20.0),
        ),
        story_shapes=(
            StoryShapePattern(
                discipline="backend", avg_points=3.2, avg_ac_count=3.0, avg_task_count=2.8, sample_count=20
            ),
        ),
        epic_pattern=EpicPattern(avg_stories_per_epic=6.0, avg_points_per_epic=18.0, typical_story_count_range=(4, 9)),
        estimation_accuracy_pct=78.0,
        sprint_completion_rate=88.0,
        spillover=SpilloverStats(
            carried_over_pct=12.5, avg_spillover_pts=3.2, most_common_spillover_reason="backend stories"
        ),
        dod_signal=DoDSignal(
            common_checklist_items=("tests passing", "PR merged", "code reviewed"),
            stories_with_comments_pct=85.0,
            stories_with_pr_link_pct=82.0,
            stories_with_review_mention_pct=76.0,
            stories_with_testing_mention_pct=61.0,
            stories_with_deploy_mention_pct=44.0,
            avg_comments_before_resolution=3.2,
        ),
        writing_patterns=WritingPatterns(
            median_ac_count=3.0,
            median_task_count_per_story=2.5,
            subtask_label_distribution=(("Code", 0.58), ("Testing", 0.28), ("Infra", 0.14)),
            common_subtask_patterns=("Write unit tests", "Deploy to staging"),
            subtasks_use_consistent_naming=True,
            common_personas=("developer", "admin", "end user"),
            uses_given_when_then=True,
            epic_description_length_avg=450,
            stories_with_subtasks_pct=72.0,
            epics_with_description_pct=80.0,
        ),
        sprints_fully_completed=6,
        sprints_partially_completed=2,
        sprints_analysed=8,
    )


class TestExtendedProfileSerialisation:
    def test_round_trip_spillover(self):
        profile = _make_extended_profile()
        restored = _json_to_profile(_profile_to_json(profile))
        assert restored.spillover.carried_over_pct == 12.5
        assert restored.spillover.avg_spillover_pts == 3.2
        assert restored.spillover.most_common_spillover_reason == "backend stories"

    def test_round_trip_dod_signal(self):
        profile = _make_extended_profile()
        restored = _json_to_profile(_profile_to_json(profile))
        assert restored.dod_signal.stories_with_pr_link_pct == 82.0
        assert restored.dod_signal.stories_with_review_mention_pct == 76.0
        assert restored.dod_signal.common_checklist_items == ("tests passing", "PR merged", "code reviewed")
        assert restored.dod_signal.avg_comments_before_resolution == 3.2

    def test_round_trip_writing_patterns(self):
        profile = _make_extended_profile()
        restored = _json_to_profile(_profile_to_json(profile))
        assert restored.writing_patterns.median_ac_count == 3.0
        assert restored.writing_patterns.uses_given_when_then is True
        assert restored.writing_patterns.common_personas == ("developer", "admin", "end user")
        assert restored.writing_patterns.subtask_label_distribution == (
            ("Code", 0.58),
            ("Testing", 0.28),
            ("Infra", 0.14),
        )
        assert restored.writing_patterns.subtasks_use_consistent_naming is True
        assert restored.writing_patterns.epic_description_length_avg == 450

    def test_round_trip_doc_quality(self):
        profile = TeamProfile(
            team_id="jira-DQ",
            source="jira",
            project_key="DQ",
            doc_quality=DocQualitySignal(
                pages_scanned=6,
                platforms_scanned=("confluence", "notion"),
                avg_clarity=57.5,
                clear_pages=2,
                mixed_pages=3,
                unclear_pages=1,
                avg_ai_likelihood=48.0,
                likely_ai_pages=2,
                ai_marked_pages=1,
                per_platform=(("confluence", 4), ("notion", 2)),
                flagged_pages=(("Onboarding guide", "clarity 30/100 — dense or long-winded"),),
            ),
        )
        restored = _json_to_profile(_profile_to_json(profile))
        dq = restored.doc_quality
        assert dq.pages_scanned == 6
        assert dq.platforms_scanned == ("confluence", "notion")
        assert dq.avg_clarity == 57.5
        assert (dq.clear_pages, dq.mixed_pages, dq.unclear_pages) == (2, 3, 1)
        assert dq.avg_ai_likelihood == 48.0
        assert dq.likely_ai_pages == 2
        assert dq.ai_marked_pages == 1
        # Pair lists survive as tuples-of-tuples, not lists-of-lists.
        assert dq.per_platform == (("confluence", 4), ("notion", 2))
        assert dq.flagged_pages == (("Onboarding guide", "clarity 30/100 — dense or long-winded"),)
        assert dq.is_ai_estimate is False

    def test_doc_quality_backward_compat_default(self):
        """An old profile with no doc_quality key deserializes to an empty signal."""
        old_json = json.dumps({"team_id": "jira-OLD", "source": "jira", "project_key": "OLD", "sample_sprints": 3})
        restored = _json_to_profile(old_json)
        assert restored.doc_quality == DocQualitySignal()
        assert restored.doc_quality.pages_scanned == 0

    def test_round_trip_sprint_counts(self):
        profile = _make_extended_profile()
        restored = _json_to_profile(_profile_to_json(profile))
        assert restored.sprints_fully_completed == 6
        assert restored.sprints_partially_completed == 2
        assert restored.sprints_analysed == 8

    def test_backward_compat_missing_fields(self):
        """Old profiles (without extended fields) should deserialize with defaults."""
        old_json = json.dumps(
            {
                "team_id": "jira-OLD",
                "source": "jira",
                "project_key": "OLD",
                "sample_sprints": 3,
                "sample_stories": 20,
                "velocity_avg": 15.0,
                "velocity_stddev": 2.0,
                "point_calibrations": [],
                "story_shapes": [],
                "epic_pattern": {},
                "estimation_accuracy_pct": 70.0,
                "sprint_completion_rate": 80.0,
            }
        )
        restored = _json_to_profile(old_json)
        assert restored.spillover == SpilloverStats()
        assert restored.dod_signal == DoDSignal()
        assert restored.writing_patterns == WritingPatterns()
        assert restored.sprints_fully_completed == 0
        assert restored.sprints_analysed == 0

    def test_store_save_load_extended_profile(self, tmp_path):
        db_path = tmp_path / "sessions.db"
        profile = _make_extended_profile()
        with TeamProfileStore(db_path) as store:
            store.save(profile)
            loaded = store.load("jira-PROJ")
        assert loaded is not None
        assert loaded.spillover.carried_over_pct == 12.5
        assert loaded.dod_signal.stories_with_pr_link_pct == 82.0
        assert loaded.writing_patterns.uses_given_when_then is True
        assert loaded.sprints_fully_completed == 6


# ---------------------------------------------------------------------------
# Parallel analysis tests (Phase B)
# ---------------------------------------------------------------------------


class TestParallelAnalysis:
    def test_run_parallel_analysis_basic(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        sprint_data = [
            {
                "sprint_name": "Sprint 1",
                "completed_points": 20.0,
                "planned_count": 5,
                "completed_count": 4,
                "stories": [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 2,
                        "ac_count": 3,
                        "epic_key": "EP-1",
                        "point_changed": False,
                        "issue_key": "P-1",
                        "issue_url": "http://x/P-1",
                        "summary": "Story A",
                    },
                    {
                        "points": 5,
                        "cycle_time_days": 4.5,
                        "discipline": "frontend",
                        "task_count": 3,
                        "ac_count": 2,
                        "epic_key": "EP-1",
                        "point_changed": True,
                        "issue_key": "P-2",
                        "issue_url": "http://x/P-2",
                        "summary": "Story B",
                    },
                ],
            },
            {
                "sprint_name": "Sprint 2",
                "completed_points": 25.0,
                "planned_count": 6,
                "completed_count": 6,
                "stories": [
                    {
                        "points": 1,
                        "cycle_time_days": 0.5,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "EP-2",
                        "point_changed": False,
                        "issue_key": "P-3",
                        "issue_url": "http://x/P-3",
                        "summary": "Story C",
                    },
                ],
            },
        ]
        progress = []
        profile, examples = _run_parallel_analysis("jira", "PROJ", sprint_data, progress)

        assert profile.team_id.startswith("jira-PROJ-")
        assert profile.sample_sprints == 2
        assert profile.sample_stories == 3
        assert profile.velocity_avg > 0
        assert len(profile.point_calibrations) > 0
        assert len(profile.story_shapes) > 0
        assert len(progress) > 0
        assert isinstance(examples, dict)

    def test_run_parallel_analysis_empty(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        profile, examples = _run_parallel_analysis("jira", "EMPTY", [])
        assert profile.sample_sprints == 0
        assert profile.sample_stories == 0
        assert profile.velocity_avg == 0.0
        assert isinstance(examples, dict)

    def test_sprint_completion_counts(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        def _story(pts=3, carried=False, recurring=False):
            return {
                "points": pts,
                "cycle_time_days": 2.0,
                "discipline": "backend",
                "task_count": 0,
                "ac_count": 0,
                "epic_key": "",
                "point_changed": False,
                "carried_over": carried,
                "summary": "Training" if recurring else "Story",
            }

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 20.0,
                "planned_count": 5,
                "completed_count": 5,
                "stories": [_story() for _ in range(5)],
            },
            {
                "sprint_name": "S2",
                "completed_points": 15.0,
                "planned_count": 5,
                "completed_count": 3,
                "stories": [_story() for _ in range(3)] + [_story(carried=True), _story(carried=True)],
            },
            {
                "sprint_name": "S3",
                "completed_points": 25.0,
                "planned_count": 6,
                "completed_count": 6,
                "stories": [_story() for _ in range(6)],
            },
        ]
        profile, _ = _run_parallel_analysis("jira", "PROJ", sprint_data)
        assert profile.sprints_fully_completed == 2
        assert profile.sprints_partially_completed == 1

    def test_examples_include_calibration_stories(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 10.0,
                "planned_count": 3,
                "completed_count": 3,
                "stories": [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "X-1",
                        "issue_url": "http://x/X-1",
                        "summary": "Fast story",
                    },
                    {
                        "points": 3,
                        "cycle_time_days": 8.0,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "X-2",
                        "issue_url": "http://x/X-2",
                        "summary": "Slow story",
                    },
                    {
                        "points": 3,
                        "cycle_time_days": 3.0,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "X-3",
                        "issue_url": "http://x/X-3",
                        "summary": "Mid story",
                    },
                ],
            }
        ]
        _, examples = _run_parallel_analysis("jira", "P", sprint_data)
        assert "calibration_3pt" in examples
        assert len(examples["calibration_3pt"]) >= 1
        assert examples["calibration_3pt"][0]["issue_key"]


# ---------------------------------------------------------------------------
# New analysis metrics tests (discipline calibration, spillover correlation,
# velocity trend, confidence scoring)
# ---------------------------------------------------------------------------


class TestNewAnalysisMetrics:
    """Tests for improvements added to _run_parallel_analysis."""

    def _make_sprint_data(self):
        """Build sprint data with multiple disciplines and carried-over stories."""
        stories = [
            {
                "points": 3,
                "cycle_time_days": 2.0,
                "discipline": "backend",
                "task_count": 2,
                "ac_count": 1,
                "epic_key": "",
                "point_changed": False,
                "carried_over": False,
                "assignee": "Alice",
                "issue_key": "T-1",
                "issue_url": "",
                "summary": "Backend story",
            },
            {
                "points": 3,
                "cycle_time_days": 5.0,
                "discipline": "frontend",
                "task_count": 1,
                "ac_count": 2,
                "epic_key": "",
                "point_changed": False,
                "carried_over": True,
                "assignee": "Bob",
                "issue_key": "T-2",
                "issue_url": "",
                "summary": "Frontend story",
            },
            {
                "points": 5,
                "cycle_time_days": 8.0,
                "discipline": "backend",
                "task_count": 4,
                "ac_count": 3,
                "epic_key": "",
                "point_changed": False,
                "carried_over": True,
                "assignee": "Alice",
                "issue_key": "T-3",
                "issue_url": "",
                "summary": "Large backend",
            },
            {
                "points": 1,
                "cycle_time_days": 0.5,
                "discipline": "frontend",
                "task_count": 0,
                "ac_count": 1,
                "epic_key": "",
                "point_changed": False,
                "carried_over": False,
                "assignee": "Bob",
                "issue_key": "T-4",
                "issue_url": "",
                "summary": "Quick fix",
            },
        ]
        return [
            {
                "sprint_name": "Sprint 1",
                "completed_points": 10.0,
                "planned_count": 3,
                "completed_count": 2,
                "stories": stories[:2],
            },
            {
                "sprint_name": "Sprint 2",
                "completed_points": 15.0,
                "planned_count": 3,
                "completed_count": 2,
                "stories": stories[2:],
            },
            {
                "sprint_name": "Sprint 3",
                "completed_points": 20.0,
                "planned_count": 4,
                "completed_count": 4,
                "stories": stories,
            },
        ]

    def test_discipline_calibration(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        _, examples = _run_parallel_analysis("jira", "T", self._make_sprint_data())
        disc_cal = examples.get("discipline_calibration", {})
        assert isinstance(disc_cal, dict)
        assert "backend" in disc_cal or "frontend" in disc_cal

    def test_spillover_correlation(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        _, examples = _run_parallel_analysis("jira", "T", self._make_sprint_data())
        corr = examples.get("spillover_correlation", {})
        assert isinstance(corr, dict)
        assert "by_size" in corr
        assert "by_discipline" in corr
        assert "by_task_count" in corr
        # At least one dimension should have non-zero spillover
        all_pcts = []
        for d in corr.values():
            if isinstance(d, dict):
                all_pcts.extend(d.values())
        assert any(p > 0 for p in all_pcts), "should detect some spillover"

    def test_velocity_trend(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        _, examples = _run_parallel_analysis("jira", "T", self._make_sprint_data())
        vt = examples.get("velocity_trend", {})
        assert isinstance(vt, dict)
        assert vt["trend"] in ("improving", "stable", "degrading")
        assert "slope" in vt
        assert "first_velocity" in vt

    def test_confidence_levels(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        _, examples = _run_parallel_analysis("jira", "T", self._make_sprint_data())
        conf = examples.get("confidence_levels", {})
        assert isinstance(conf, dict)
        # With small sample data, all should be "low"
        for level in conf.values():
            assert level in ("high", "medium", "low")


class TestScopeChangeAnalysis:
    """Tests for mid-sprint scope change detection and metrics."""

    def test_analyse_scope_changes_basic(self):
        from yeaboi.tools.team_learning import _analyse_scope_changes

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 10.0,
                "planned_count": 4,
                "completed_count": 3,
                "stories": [
                    {
                        "issue_key": "X-1",
                        "points": 3,
                        "discipline": "backend",
                        "added_mid_sprint": True,
                        "carried_over": False,
                        "point_changed": True,
                        "original_points": 2,
                    },
                    {
                        "issue_key": "X-2",
                        "points": 5,
                        "discipline": "frontend",
                        "carried_over": True,
                    },
                    {"issue_key": "X-3", "points": 2, "discipline": "backend"},
                ],
            },
            {
                "sprint_name": "S2",
                "completed_points": 15.0,
                "planned_count": 3,
                "completed_count": 3,
                "stories": [
                    {"issue_key": "X-1", "points": 3, "discipline": "backend"},
                    {"issue_key": "X-4", "points": 5, "discipline": "frontend"},
                    {"issue_key": "X-5", "points": 2, "discipline": "backend"},
                ],
            },
        ]
        result = _analyse_scope_changes(sprint_data)
        assert "per_sprint" in result
        assert "totals" in result
        assert result["totals"]["added_mid_sprint"] == 1
        assert result["totals"]["re_estimated"] == 1
        assert "re_estimation_by_size" in result
        assert 2 in result["re_estimation_by_size"] or 3 in result["re_estimation_by_size"]

    def test_carry_over_chains(self):
        from yeaboi.tools.team_learning import _analyse_scope_changes

        # Story X-1 appears in all 3 sprints → should be a carry-over chain
        sprint_data = [
            {
                "sprint_name": f"S{i}",
                "completed_points": 10.0,
                "planned_count": 2,
                "completed_count": 1,
                "stories": [
                    {"issue_key": "X-1", "points": 3, "discipline": "backend"},
                    {"issue_key": f"X-{i + 10}", "points": 2, "discipline": "frontend"},
                ],
            }
            for i in range(4)
        ]
        result = _analyse_scope_changes(sprint_data)
        chains = result.get("carry_over_chains", [])
        assert len(chains) >= 1
        assert chains[0]["issue_key"] == "X-1"
        assert chains[0]["sprint_count"] == 4

    def test_scope_changes_in_parallel_analysis(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 10.0,
                "planned_count": 2,
                "completed_count": 2,
                "stories": [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 1,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": True,
                        "original_points": 2,
                        "added_mid_sprint": True,
                        "issue_key": "T-1",
                        "issue_url": "",
                        "summary": "Changed story",
                        "assignee": "Alice",
                    },
                    {
                        "points": 5,
                        "cycle_time_days": 4.0,
                        "discipline": "frontend",
                        "task_count": 2,
                        "ac_count": 2,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "T-2",
                        "issue_url": "",
                        "summary": "Normal story",
                        "assignee": "Bob",
                    },
                ],
            }
        ]
        _, examples = _run_parallel_analysis("jira", "T", sprint_data)
        scope = examples.get("scope_changes")
        assert scope is not None
        assert scope["totals"]["added_mid_sprint"] == 1
        assert scope["totals"]["re_estimated"] == 1


# ---------------------------------------------------------------------------
# Daily scope timeline tests
# ---------------------------------------------------------------------------


class TestDailyScopeTimeline:
    """Tests for daily scope snapshot dataclasses and timeline analysis."""

    def test_scope_change_event_frozen(self):
        ev = ScopeChangeEvent(
            date="2026-03-01",
            issue_key="X-1",
            change_type="added",
            from_pts=0,
            to_pts=3,
            delta_pts=3,
        )
        assert ev.delta_pts == 3
        with pytest.raises(AttributeError):
            ev.delta_pts = 5  # type: ignore[misc]

    def test_daily_scope_snapshot_frozen(self):
        snap = DailyScopeSnapshot(
            date="2026-03-01",
            total_scope_pts=10.0,
            stories_in_sprint=(("X-1", 5.0), ("X-2", 5.0)),
        )
        assert snap.total_scope_pts == 10.0
        assert len(snap.stories_in_sprint) == 2

    def test_sprint_scope_timeline_frozen(self):
        tl = SprintScopeTimeline(
            sprint_name="Sprint 1",
            committed_pts=20,
            final_pts=25,
            delivered_pts=22,
            scope_change_total=5,
            scope_churn=0.25,
        )
        assert tl.scope_change_total == 5
        assert tl.scope_churn == 0.25

    def test_analyse_scope_changes_with_timelines(self):
        """_analyse_scope_changes includes timeline data when present."""
        from yeaboi.tools.team_learning import _analyse_scope_changes

        timeline = SprintScopeTimeline(
            sprint_name="S1",
            committed_pts=20,
            final_pts=25,
            delivered_pts=18,
            scope_change_total=5,
            scope_churn=0.25,
            change_events=(
                ScopeChangeEvent(
                    date="2026-03-03",
                    issue_key="X-5",
                    change_type="added",
                    from_pts=0,
                    to_pts=5,
                    delta_pts=5,
                ),
            ),
        )
        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 18.0,
                "planned_count": 4,
                "completed_count": 3,
                "scope_timeline": timeline,
                "stories": [
                    {"issue_key": "X-1", "points": 5, "discipline": "backend"},
                    {"issue_key": "X-2", "points": 5, "discipline": "frontend"},
                    {
                        "issue_key": "X-5",
                        "points": 5,
                        "discipline": "backend",
                        "added_mid_sprint": True,
                    },
                ],
            },
        ]
        result = _analyse_scope_changes(sprint_data)
        assert len(result["timelines"]) == 1
        assert result["timelines"][0].committed_pts == 20
        assert result["totals"]["avg_committed_velocity"] == 20.0
        assert result["totals"]["avg_delivered_velocity"] == 18.0
        # Per-sprint should have timeline fields
        ps = result["per_sprint"][0]
        assert ps["committed_pts"] == 20
        assert ps["delivered_pts"] == 18
        assert ps["scope_churn"] == 0.25

    def test_analyse_scope_changes_without_timelines(self):
        """_analyse_scope_changes still works when no timelines are present."""
        from yeaboi.tools.team_learning import _analyse_scope_changes

        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 10.0,
                "planned_count": 2,
                "completed_count": 2,
                "stories": [
                    {"issue_key": "X-1", "points": 5, "discipline": "backend"},
                    {"issue_key": "X-2", "points": 5, "discipline": "frontend"},
                ],
            },
        ]
        result = _analyse_scope_changes(sprint_data)
        assert result["timelines"] == []
        assert result["totals"]["avg_committed_velocity"] == 0.0
        assert "committed_pts" not in result["per_sprint"][0]

    def test_date_range_helper(self):
        from yeaboi.tools.team_learning import _date_range, _parse_date

        s = _parse_date("2026-03-01")
        e = _parse_date("2026-03-05")
        days = _date_range(s, e)
        assert len(days) == 5
        assert days[0] == "2026-03-01"
        assert days[-1] == "2026-03-05"

    def test_scope_timeline_in_parallel_analysis(self):
        """Timelines flow through _run_parallel_analysis."""
        from yeaboi.tools.team_learning import _run_parallel_analysis

        timeline = SprintScopeTimeline(
            sprint_name="S1",
            committed_pts=15,
            final_pts=18,
            delivered_pts=13,
            scope_change_total=3,
            scope_churn=0.2,
        )
        sprint_data = [
            {
                "sprint_name": "S1",
                "completed_points": 13.0,
                "planned_count": 3,
                "completed_count": 2,
                "scope_timeline": timeline,
                "stories": [
                    {
                        "points": 5,
                        "cycle_time_days": 3.0,
                        "discipline": "backend",
                        "task_count": 2,
                        "ac_count": 1,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "T-1",
                        "issue_url": "",
                        "summary": "Story 1",
                        "assignee": "Alice",
                    },
                    {
                        "points": 8,
                        "cycle_time_days": 5.0,
                        "discipline": "frontend",
                        "task_count": 3,
                        "ac_count": 2,
                        "epic_key": "",
                        "point_changed": False,
                        "issue_key": "T-2",
                        "issue_url": "",
                        "summary": "Story 2",
                        "assignee": "Bob",
                    },
                ],
            },
        ]
        _, examples = _run_parallel_analysis("azdevops", "T", sprint_data)
        scope = examples.get("scope_changes")
        assert scope is not None
        assert len(scope["timelines"]) == 1
        assert scope["totals"]["avg_committed_velocity"] == 15.0


# ---------------------------------------------------------------------------
# Team profile exporter tests (Phase G)
# ---------------------------------------------------------------------------


class TestTeamProfileExporter:
    def test_export_html(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_html

        profile = _make_extended_profile()
        path = export_team_profile_html(profile, output_dir=tmp_path)
        assert path.exists()

        from tests._pages import island

        boot = island(path.read_text())
        assert boot["chrome"]["title"] == "Team Profile — PROJ"
        assert boot["chrome"]["mode"] == "analysis"
        titles = [section["title"] for section in boot["report"]["sections"]]
        assert "Team & Velocity" in titles
        assert "What Each Point Value Means" in titles
        # Every section is reachable from the contents rail — a generated report
        # that only some sections link to is one nobody scrolls to the end of.
        assert {nav[0] for nav in boot["chrome"]["nav"]} <= {s["id"] for s in boot["report"]["sections"]}

    def test_export_md(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_md

        profile = _make_extended_profile()
        path = export_team_profile_md(profile, output_dir=tmp_path)
        assert path.exists()
        content = path.read_text()
        assert "# Team Profile" in content
        assert "PROJ" in content
        assert "## Team & Velocity" in content
        assert "23.5 pts/sprint" in content

    def test_export_html_minimal_profile(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_html

        profile = TeamProfile(team_id="x", source="jira", project_key="X")
        path = export_team_profile_html(profile, output_dir=tmp_path)
        assert path.exists()

    def test_export_md_minimal_profile(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_md

        profile = TeamProfile(team_id="x", source="jira", project_key="X")
        path = export_team_profile_md(profile, output_dir=tmp_path)
        assert path.exists()

    def test_build_markdown_returns_string(self):
        """The string builder (used by Notion/Confluence export) matches the file content."""
        from yeaboi.team_profile_exporter import build_team_profile_markdown

        md = build_team_profile_markdown(_make_extended_profile())
        assert isinstance(md, str)
        assert "# Team Profile" in md
        assert "## Team & Velocity" in md

    def test_build_markdown_minimal_profile(self):
        from yeaboi.team_profile_exporter import build_team_profile_markdown

        md = build_team_profile_markdown(TeamProfile(team_id="x", source="jira", project_key="X"))
        assert "# Team Profile" in md

    def test_markdown_ai_adoption_source_and_examples(self):
        from yeaboi.team_profile import AiAdoptionSignal
        from yeaboi.team_profile_exporter import build_team_profile_markdown

        sig = AiAdoptionSignal(
            scanned_commits=134,
            ai_commits=133,
            footprint_pct=99.0,
            per_tool=(("claude", 131),),
            per_source=(("local_git", 133),),
            repos_scanned=("Local clone: /Users/dinho/repo",),
            sources_scanned=("local_git",),
        )
        profile = TeamProfile(team_id="t", source="jira", project_key="P", ai_adoption=sig)
        ex = {
            "ai_adoption": {
                "coverage": ["github: STANDUP_GITHUB_REPO / GITHUB_TOKEN not set"],
                "samples": [
                    {
                        "tool": "claude",
                        "title": "Fix login",
                        "source": "local_git",
                        "key": "a1b2c3d4",
                        "url": "https://github.com/o/r/commit/a1b2c3d4",
                    },
                ],
                "insights": {
                    "start": [
                        {
                            "title": "Open PRs",
                            "detail": "d",
                            "evidence": "e",
                            "link": "https://github.com/o/r/commit/a1b2c3d4",
                        }
                    ],
                    "stop": [],
                    "keep": [],
                    "try": [],
                },
            }
        }
        md = build_team_profile_markdown(profile, examples=ex)
        assert "Local clone (remote)" in md or "Local clone" in md  # friendly source label
        assert "/Users/dinho/repo" in md  # scanned path
        assert "**By source:**" in md
        assert "Not scanned:" in md and "STANDUP_GITHUB_REPO" in md
        assert "### Examples" in md
        assert "[Fix login](https://github.com/o/r/commit/a1b2c3d4)" in md  # linked example
        assert "[↳ example](https://github.com/o/r/commit/a1b2c3d4)" in md  # linked coaching

    def test_export_practices_table(self, tmp_path, monkeypatch):
        from yeaboi.team_profile import AiAdoptionSignal
        from yeaboi.team_profile_exporter import (
            build_team_profile_html,
            build_team_profile_markdown,
            write_analysis_log,
        )

        sig = AiAdoptionSignal(scanned_commits=30, ai_commits=6, footprint_pct=20.0)
        profile = TeamProfile(team_id="t", source="jira", project_key="P", ai_adoption=sig)
        ex = {
            "ai_adoption": {
                "member_practices": {
                    "min_sample": 5,
                    "file_data": {"with_file_data": 9, "total": 12},
                    "members": [
                        {
                            "member": "Ava",
                            "commits": 20,
                            "prs": 10,
                            "with_file_data": 8,
                            "tests_num": 6,
                            "tests_den": 8,
                            "tests_rate": 75.0,
                            "docs_num": 2,
                            "docs_den": 8,
                            "docs_rate": 25.0,
                            "ticket_num": 5,
                            "ticket_den": 10,
                            "ticket_rate": 50.0,
                            "desc_num": 1,
                            "desc_den": 4,
                            "desc_rate": 25.0,
                        }
                    ],
                    "team": {
                        "member": "Team",
                        "commits": 20,
                        "prs": 10,
                        "with_file_data": 9,
                        "tests_num": 6,
                        "tests_den": 8,
                        "tests_rate": 75.0,
                        "docs_num": 2,
                        "docs_den": 9,
                        "docs_rate": 22.2,
                        "ticket_num": 5,
                        "ticket_den": 14,
                        "ticket_rate": 35.7,
                        "desc_num": 1,
                        "desc_den": 4,
                        "desc_rate": 25.0,
                    },
                }
            }
        }
        md = build_team_profile_markdown(profile, examples=ex)
        assert "### Engineering practices by member" in md
        assert "| Ava | 20 | 10 | 75% | 25% | 50% | 1/4 |" in md  # desc under the sample floor stays a fraction
        assert "| Team |" in md
        assert "cover 9 of 12 items with change metadata" in md
        table = _blocks(build_team_profile_html(profile, examples=ex), "ai-adoption", "table")[0]
        assert table["title"] == "Engineering practices by member"
        assert table["headers"] == ["Member", "Commits", "PRs", "Tests", "Docs", "Tickets", "Descriptions"]
        assert table["rows"][0][0] == {"t": "Ava", "person": True}
        assert table["rows"][0][6] == "1/4"  # desc under the sample floor stays a fraction
        assert table["rows"][-1][0]["t"] == "Team"
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        text = write_analysis_log(profile, examples=ex).read_text()
        assert "Practices (team): tests 75% · docs 22% · tickets 36% · descriptions 1/4" in text

    def test_export_without_practices_blob_still_works(self):
        from yeaboi.team_profile import AiAdoptionSignal
        from yeaboi.team_profile_exporter import build_team_profile_html, build_team_profile_markdown

        sig = AiAdoptionSignal(scanned_commits=30, ai_commits=6, footprint_pct=20.0)
        profile = TeamProfile(team_id="t", source="jira", project_key="P", ai_adoption=sig)
        md = build_team_profile_markdown(profile, examples={"ai_adoption": {}})
        assert "## AI Usage" in md
        assert "Engineering practices" not in md
        html = build_team_profile_html(profile, examples={"ai_adoption": {}})
        assert "Engineering practices" not in html

    def test_code_health_only_exports_without_ai_footprint(self):
        from yeaboi.team_profile_exporter import build_team_profile_html, build_team_profile_markdown

        profile = TeamProfile(team_id="t", source="jira", project_key="P")
        examples = {
            "ai_adoption": {
                "enabled_features": ["code_health"],
                "repository_health": {
                    "files_analysed": 12,
                    "repositories_touched": 2,
                    "findings": 3,
                },
                "action_plan": [{"priority": "high", "title": "Add focused tests"}],
            }
        }
        markdown = build_team_profile_markdown(profile, examples=examples)
        html = build_team_profile_html(profile, examples=examples)
        assert "## Code Health" in markdown
        assert "## AI Usage" not in markdown
        assert "Code Health" in html
        assert "AI Usage" not in html

    def test_failed_code_and_docs_export_status_without_zero_metrics_or_advice(self):
        from yeaboi.team_profile_exporter import build_team_profile_html, build_team_profile_markdown

        profile = TeamProfile(
            team_id="t",
            source="jira",
            project_key="P",
            doc_quality=DocQualitySignal(),
        )
        failed = {
            "status": "failed",
            "completed": 0,
            "eligible": 15929,
            "has_data": False,
            "grouped_errors": [
                {
                    "provider": "azdo",
                    "status": "failed",
                    "count": 15929,
                    "detail": "SDK wrapper mismatch",
                }
            ],
        }
        examples = {
            "ai_adoption": {
                "enabled_features": ["code_health"],
                "coverage_report": failed,
                "repository_health": {
                    "files_analysed": 0,
                    "repositories_touched": 0,
                    "findings": 0,
                },
                "action_plan": [{"priority": "high", "title": "Fabricated action"}],
            },
            "doc_quality": {
                "coverage_report": {
                    **failed,
                    "eligible": 1,
                    "grouped_errors": [
                        {
                            "provider": "confluence",
                            "status": "inaccessible",
                            "count": 1,
                            "detail": "repeated page IDs",
                        }
                    ],
                },
                "insights": {
                    "start": [{"title": "Fabricated docs advice", "detail": "x", "evidence": "0 pages"}],
                },
            },
        }

        markdown = build_team_profile_markdown(profile, examples=examples)
        html = build_team_profile_html(profile, examples=examples)

        assert "Failed — 0 of 15,929 eligible items analysed" in markdown
        assert "SDK wrapper mismatch" in markdown
        assert "## Documentation" in markdown
        assert "repeated page IDs" in markdown
        assert "Changed files analysed" not in markdown
        assert "Average clarity" not in markdown
        assert "Fabricated action" not in markdown
        assert "Fabricated docs advice" not in markdown
        assert "Failed" in html and "repeated page IDs" in html

    def test_build_markdown_embeds_velocity_chart(self, tmp_path):
        import pytest

        pytest.importorskip("matplotlib")
        from yeaboi.team_profile_exporter import build_team_profile_markdown

        examples = {
            "sprint_details": [
                {"name": "S1", "points": 20, "planned": 10, "completed": 9, "rate": 90, "done": True},
                {"name": "S2", "points": 18, "planned": 12, "completed": 12, "rate": 100, "done": True},
            ]
        }
        md = build_team_profile_markdown(_make_extended_profile(), examples=examples, charts_dir=tmp_path)
        assert f"![Sprint velocity]({tmp_path / 'velocity.png'})" in md
        assert (tmp_path / "velocity.png").exists()

    def test_build_markdown_no_charts_dir_no_image(self):
        from yeaboi.team_profile_exporter import build_team_profile_markdown

        examples = {
            "sprint_details": [{"name": "S1", "points": 20, "planned": 10, "completed": 9, "rate": 90, "done": True}]
        }
        md = build_team_profile_markdown(_make_extended_profile(), examples=examples)
        assert "![Sprint velocity]" not in md
        assert "## Sprint Breakdown" in md

    def test_exports_sorted_into_project_subdirectory(self, tmp_path):
        """Exports land in a per-project subdirectory: {base}/{project_key}/."""
        from yeaboi.team_profile_exporter import export_team_profile_html, export_team_profile_md

        profile = _make_extended_profile()  # project_key="PROJ"
        html_path = export_team_profile_html(profile, output_dir=tmp_path)
        md_path = export_team_profile_md(profile, output_dir=tmp_path)
        # Both files should be in tmp_path/proj/ (lowercase)
        assert html_path.parent.name == "proj"
        assert md_path.parent.name == "proj"
        assert html_path.parent == md_path.parent

    def test_write_analysis_log(self, tmp_path, monkeypatch):
        """Analysis log written to ~/.scrum-agent/logs/ with structured content."""
        from yeaboi.team_profile_exporter import write_analysis_log

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        profile = _make_extended_profile()
        log_path = write_analysis_log(
            profile,
            examples={"team_size": 5},
            sprint_names=["Sprint 1", "Sprint 2"],
            duration_secs=12.3,
        )
        assert log_path.exists()
        content = log_path.read_text()
        assert "PROJ" in content
        assert "Sprint 1" in content
        assert "12.3s" in content
        assert "Raw profile JSON:" in content
        assert '"team_id"' in content
        # Log goes to the analysis logs directory
        assert log_path.parent.name == "analysis"
        assert "team-analysis-proj-" in log_path.name

    def test_analysis_log_caps_repo_list(self, tmp_path, monkeypatch):
        """The log lists at most 5 scanned repos (+N more) but keeps every sample."""
        from dataclasses import replace

        from yeaboi.team_profile import AiAdoptionSignal
        from yeaboi.team_profile_exporter import write_analysis_log

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        profile = replace(
            _make_extended_profile(),
            ai_adoption=AiAdoptionSignal(
                scanned_commits=30,
                ai_commits=9,
                footprint_pct=30.0,
                repos_scanned=tuple(f"acme/repo-{i}" for i in range(8)),
            ),
        )
        examples = {"ai_adoption": {"samples": [{"tool": "claude", "title": f"Sample change {i}"} for i in range(9)]}}
        content = write_analysis_log(profile, examples=examples).read_text()
        assert content.count("  Scanned:") == 6  # 5 repos + the "+N more" row
        assert "Scanned: +3 more repositories" in content
        assert "Examples (9):" in content
        for i in range(9):
            assert f"Sample change {i}" in content


# ---------------------------------------------------------------------------
# Team analysis screen rendering tests (Phase E)
# ---------------------------------------------------------------------------


class TestTeamAnalysisScreen:
    def test_build_team_analysis_screen_renders(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_team_analysis_screen

        profile = _make_extended_profile()
        panel = _build_team_analysis_screen(profile, scroll_offset=0, width=80, height=30)
        assert panel is not None

    def test_build_team_analysis_screen_scrollable(self):
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_team_analysis_screen

        profile = _make_extended_profile()
        panel1 = _build_team_analysis_screen(profile, scroll_offset=0, width=80, height=30)
        panel2 = _build_team_analysis_screen(profile, scroll_offset=5, width=80, height=30)
        assert panel1 is not None
        assert panel2 is not None


# ---------------------------------------------------------------------------
# Project card staleness hint tests (Phase F)
# ---------------------------------------------------------------------------


class TestProfileCards:
    def test_new_project_card_no_staleness(self):
        from yeaboi.ui.mode_select.screens._project_cards import _build_new_project_card

        card = _build_new_project_card(selected=True, box_w=64)
        assert card is not None

    def test_profile_card_renders(self):
        from yeaboi.ui.mode_select.screens._project_cards import ProfileSummary, _build_profile_card

        ps = ProfileSummary(
            team_id="jira-PROJ",
            source="jira",
            project_key="PROJ",
            sample_sprints=8,
            velocity_avg=23.0,
            sample_stories=64,
            updated="2 days ago",
            staleness_days=2,
        )
        card = _build_profile_card(ps, selected=True, box_w=64)
        assert card is not None

    def test_profile_card_stale_shows_hint(self):
        from yeaboi.ui.mode_select.screens._project_cards import ProfileSummary, _build_profile_card

        ps = ProfileSummary(
            team_id="jira-X",
            source="jira",
            project_key="X",
            staleness_days=45,
            updated="45 days ago",
        )
        card = _build_profile_card(ps, selected=False, box_w=64)
        assert card is not None

    def test_new_analysis_card_renders(self):
        from yeaboi.ui.mode_select.screens._project_cards import _build_new_analysis_card

        card = _build_new_analysis_card(selected=True, box_w=64)
        assert card is not None

    def test_new_analysis_card_custom_label(self):
        from yeaboi.ui.mode_select.screens._project_cards import _build_new_analysis_card

        card = _build_new_analysis_card(label="+ Analyse Jira Board", selected=False, box_w=64)
        assert card is not None


# ---------------------------------------------------------------------------
# Project list screen popup rendering tests (Phase C)
# ---------------------------------------------------------------------------


class TestMergeProfiles:
    def test_merge_combines_sample_counts(self):
        from yeaboi.team_profile import merge_profiles

        old = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_sprints=5,
            sample_stories=30,
            velocity_avg=20.0,
            point_calibrations=(StoryPointCalibration(point_value=3, avg_cycle_time_days=2.0, sample_count=10),),
            story_shapes=(StoryShapePattern(discipline="backend", avg_points=3.0, sample_count=15),),
        )
        new = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_sprints=3,
            sample_stories=20,
            velocity_avg=25.0,
            point_calibrations=(StoryPointCalibration(point_value=3, avg_cycle_time_days=3.0, sample_count=8),),
            story_shapes=(StoryShapePattern(discipline="backend", avg_points=4.0, sample_count=10),),
        )
        merged = merge_profiles(old, new)
        assert merged.sample_sprints == 8
        assert merged.sample_stories == 50
        assert merged.velocity_avg > 20.0
        assert merged.velocity_avg < 25.0
        assert len(merged.point_calibrations) == 1
        assert merged.point_calibrations[0].sample_count == 18
        assert len(merged.story_shapes) == 1
        assert merged.story_shapes[0].sample_count == 25

    def test_merge_new_discipline_added(self):
        from yeaboi.team_profile import merge_profiles

        old = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_stories=10,
            story_shapes=(StoryShapePattern(discipline="backend", avg_points=3.0, sample_count=10),),
        )
        new = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_stories=5,
            story_shapes=(StoryShapePattern(discipline="frontend", avg_points=2.0, sample_count=5),),
        )
        merged = merge_profiles(old, new)
        disciplines = {s.discipline for s in merged.story_shapes}
        assert "backend" in disciplines
        assert "frontend" in disciplines

    def test_merge_qualitative_fields_use_new(self):
        from yeaboi.team_profile import merge_profiles

        old = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_stories=10,
            dod_signal=DoDSignal(stories_with_pr_link_pct=50.0),
            writing_patterns=WritingPatterns(uses_given_when_then=False),
        )
        new = TeamProfile(
            team_id="jira-X",
            source="jira",
            project_key="X",
            sample_stories=5,
            dod_signal=DoDSignal(stories_with_pr_link_pct=80.0),
            writing_patterns=WritingPatterns(uses_given_when_then=True),
        )
        merged = merge_profiles(old, new)
        assert merged.dod_signal.stories_with_pr_link_pct == 80.0
        assert merged.writing_patterns.uses_given_when_then is True


class TestProjectListPopup:
    def test_build_with_team_popup_params(self):
        from yeaboi.ui.mode_select.screens._project_list_screen import _build_project_list_screen

        panel = _build_project_list_screen(
            [],
            0,
            width=80,
            height=30,
            team_popup_t=0.0,
            team_popup_sel=0,
            team_popup_pulse=0.0,
        )
        assert panel is not None

    def test_build_with_team_popup_visible(self):
        from yeaboi.ui.mode_select.screens._project_list_screen import _build_project_list_screen

        panel = _build_project_list_screen(
            [],
            0,
            width=80,
            height=30,
            team_popup_t=1.0,
            team_popup_sel=0,
            team_popup_pulse=0.5,
        )
        assert panel is not None

    def test_build_with_team_popup_skip_selected(self):
        from yeaboi.ui.mode_select.screens._project_list_screen import _build_project_list_screen

        panel = _build_project_list_screen(
            [],
            0,
            width=80,
            height=30,
            team_popup_t=1.0,
            team_popup_sel=1,
            team_popup_pulse=1.0,
        )
        assert panel is not None

    def test_build_with_team_popup_trims_body_when_crowded(self):
        """Popup must render even when project list fills the screen."""
        from yeaboi.ui.mode_select.screens._project_cards import ProjectSummary
        from yeaboi.ui.mode_select.screens._project_list_screen import _build_project_list_screen

        projects = [ProjectSummary(name=f"Project {i}", id=str(i)) for i in range(6)]
        panel = _build_project_list_screen(
            projects,
            len(projects),  # selected = "+ New Project"
            width=80,
            height=30,
            team_popup_t=1.0,
            team_popup_sel=0,
            team_popup_pulse=0.5,
        )
        assert panel is not None


class TestNarrativePersistenceAndExport:
    """examples["narrative"] survives the store round-trip and lands in exports."""

    _NARRATIVE = {
        "executive_summary": "The team is healthy overall.",
        "sections": {
            "velocity": "Velocity is steady.",
            "team": "Load is balanced.",
            "estimation": "Estimates hold.",
            "workflow": "DoD is emerging.",
            "writing": "Tickets are clear.",
            "trends": "No trend concerns.",
            "recommendations": "Nothing urgent.",
        },
    }

    def test_narrative_survives_store_round_trip(self, tmp_path):
        profile = _make_profile()
        examples = {"team_size": 4, "narrative": self._NARRATIVE}
        with TeamProfileStore(tmp_path / "sessions.db") as store:
            store.save(profile, examples=examples)
            loaded, loaded_ex = store.load_with_examples("jira-PROJ")

        assert loaded is not None
        assert loaded_ex is not None
        assert loaded_ex["narrative"]["executive_summary"] == "The team is healthy overall."
        assert loaded_ex["narrative"]["sections"]["velocity"] == "Velocity is steady."

    def test_examples_without_narrative_still_load(self, tmp_path):
        """Profiles saved before the narrative existed load and render fine."""
        from yeaboi.ui.mode_select.screens._screens_secondary import _build_team_analysis_screen

        profile = _make_profile()
        with TeamProfileStore(tmp_path / "sessions.db") as store:
            store.save(profile, examples={"team_size": 4})
            loaded, loaded_ex = store.load_with_examples("jira-PROJ")

        assert "narrative" not in (loaded_ex or {})
        panel = _build_team_analysis_screen(loaded, examples=loaded_ex, width=80, height=24)
        assert panel is not None

    def test_html_export_includes_summary_and_glossary(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_html

        profile = _make_extended_profile()
        examples = {
            "narrative": self._NARRATIVE,
            "sprint_details": [{"name": "S1", "points": 20, "planned": 10, "completed": 9, "rate": 90, "done": True}],
        }
        content = export_team_profile_html(profile, output_dir=tmp_path, examples=examples).read_text()
        assert "Executive Summary" in content
        assert "The team is healthy overall." in content
        assert "Velocity is steady." in content
        assert "Churn — % of committed points added or removed mid-sprint" in content

    def test_html_export_escapes_narrative(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_html

        profile = _make_extended_profile()
        hostile = {"executive_summary": "<script>alert(1)</script>", "sections": {}}
        content = export_team_profile_html(profile, output_dir=tmp_path, examples={"narrative": hostile}).read_text()
        assert "<script>alert(1)</script>" not in content
        # And it is still *rendered* — the probe reaches the payload as text.
        # Without this half, an exporter that simply dropped the field would pass.
        assert _blocks(content, "summary", "prose")[0]["text"] == "<script>alert(1)</script>"

    def test_md_export_includes_summary_and_glossary(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_md

        profile = _make_extended_profile()
        examples = {
            "narrative": self._NARRATIVE,
            "sprint_details": [{"name": "S1", "points": 20, "planned": 10, "completed": 9, "rate": 90, "done": True}],
        }
        content = export_team_profile_md(profile, output_dir=tmp_path, examples=examples).read_text()
        assert "## Executive Summary" in content
        assert "- **Velocity & Sprints:** Velocity is steady." in content
        assert "Churn — % of committed points added or removed mid-sprint" in content

    def test_md_export_without_narrative_has_no_summary(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_md

        profile = _make_extended_profile()
        content = export_team_profile_md(profile, output_dir=tmp_path, examples={}).read_text()
        assert "Executive Summary" not in content


class TestNarrativeInParallelAnalysis:
    """_run_parallel_analysis always attaches a narrative (LLM or fallback)."""

    def test_narrative_attached_to_examples(self):
        from unittest.mock import patch

        from yeaboi.tools.team_learning import _run_parallel_analysis

        sprint_data = [
            {
                "sprint_name": "Sprint 1",
                "completed_points": 20.0,
                "planned_count": 2,
                "completed_count": 2,
                "stories": [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 2,
                        "ac_count": 3,
                        "epic_key": "EP-1",
                        "point_changed": False,
                        "issue_key": "P-1",
                        "issue_url": "",
                        "summary": "Story A",
                    },
                ],
            },
        ]
        progress: list[str] = []
        # No LLM in unit tests — the fallback narrative must still be attached.
        with patch("yeaboi.agent.llm.get_llm", side_effect=RuntimeError("no key")):
            _profile, examples = _run_parallel_analysis("jira", "PROJ", sprint_data, progress)

        narrative = examples.get("narrative")
        assert isinstance(narrative, dict)
        assert narrative["executive_summary"]
        assert len(narrative["sections"]) == 7
        assert "Generating AI enrichments in one coherent pass…" in progress


class TestInsightsPersistenceAndExport:
    """examples["insights"] survives the store round-trip and lands in exports."""

    _INSIGHTS = {
        "start": [{"title": "Link PRs to tickets", "detail": "Add PR links.", "evidence": "10% PR linkage"}],
        "stop": [{"title": "Overcommitting sprints", "detail": "Plan to capacity.", "evidence": "55% completion"}],
        "keep": [{"title": "Given/When/Then ACs", "detail": "Keep the format.", "evidence": "GWT detected"}],
        "try": [{"title": "WIP limits", "detail": "Cap in-progress work.", "evidence": "22% spillover"}],
    }

    def test_insights_survive_store_round_trip(self, tmp_path):
        profile = _make_profile()
        examples = {"team_size": 4, "insights": self._INSIGHTS}
        with TeamProfileStore(tmp_path / "sessions.db") as store:
            store.save(profile, examples=examples)
            loaded, loaded_ex = store.load_with_examples("jira-PROJ")

        assert loaded is not None
        assert loaded_ex is not None
        assert loaded_ex["insights"]["start"][0]["title"] == "Link PRs to tickets"
        assert loaded_ex["insights"]["try"][0]["evidence"] == "22% spillover"

    def test_examples_without_insights_still_render(self, tmp_path):
        """Profiles saved before insights existed load and render fine."""
        from yeaboi.ui.mode_select.screens._screens_secondary import (
            _build_team_analysis_screen,
            _build_team_insights_screen,
        )

        profile = _make_profile()
        with TeamProfileStore(tmp_path / "sessions.db") as store:
            store.save(profile, examples={"team_size": 4})
            loaded, loaded_ex = store.load_with_examples("jira-PROJ")

        assert "insights" not in (loaded_ex or {})
        assert _build_team_analysis_screen(loaded, examples=loaded_ex, view="insights", width=80, height=24)
        assert _build_team_insights_screen(loaded, examples=loaded_ex, width=80, height=24)

    def test_html_export_includes_insights(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_html

        profile = _make_extended_profile()
        out = export_team_profile_html(profile, output_dir=tmp_path, examples={"insights": self._INSIGHTS})
        content = out.read_text()
        assert "Team Insights" in content
        assert "Link PRs to tickets" in content
        assert "Worth trying" in content
        assert "22% spillover" in content

    def test_html_export_escapes_insights(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_html

        profile = _make_extended_profile()
        hostile = {"start": [{"title": "<script>alert(1)</script>", "detail": "x", "evidence": ""}]}
        content = export_team_profile_html(profile, output_dir=tmp_path, examples={"insights": hostile}).read_text()
        assert "<script>alert(1)</script>" not in content
        cards = _blocks(content, "insights", "cards")[0]["cards"]
        assert cards[0]["items"][0][0] == {"s": "<script>alert(1)</script>", "strong": True}

    def test_md_export_includes_insights(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_md

        profile = _make_extended_profile()
        out = export_team_profile_md(profile, output_dir=tmp_path, examples={"insights": self._INSIGHTS})
        content = out.read_text()
        assert "## Team Insights" in content
        assert "### Start doing" in content
        assert "- **Link PRs to tickets** — Add PR links. *(10% PR linkage)*" in content

    def test_md_export_without_insights_has_no_section(self, tmp_path):
        from yeaboi.team_profile_exporter import export_team_profile_md

        profile = _make_extended_profile()
        content = export_team_profile_md(profile, output_dir=tmp_path, examples={}).read_text()
        assert "Team Insights" not in content

    def test_analysis_log_includes_insights(self, tmp_path, monkeypatch):
        from yeaboi.team_profile_exporter import write_analysis_log

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        profile = _make_profile()
        log_path = write_analysis_log(profile, examples={"insights": self._INSIGHTS})
        content = log_path.read_text()
        assert "Team Insights:" in content
        assert "Link PRs to tickets" in content
        assert "(22% spillover)" in content


class TestInsightsInParallelAnalysis:
    """_run_parallel_analysis always attaches insights (LLM or fallback)."""

    def test_insights_attached_to_examples(self):
        from unittest.mock import patch

        from yeaboi.tools.team_learning import _run_parallel_analysis

        sprint_data = [
            {
                "sprint_name": "Sprint 1",
                "completed_points": 20.0,
                "planned_count": 2,
                "completed_count": 2,
                "stories": [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 2,
                        "ac_count": 3,
                        "epic_key": "EP-1",
                        "point_changed": False,
                        "issue_key": "P-1",
                        "issue_url": "",
                        "summary": "Story A",
                    },
                ],
            },
        ]
        progress: list[str] = []
        # No LLM in unit tests — the fallback insights must still be attached.
        with patch("yeaboi.agent.llm.get_llm", side_effect=RuntimeError("no key")):
            _profile, examples = _run_parallel_analysis("jira", "PROJ", sprint_data, progress)

        insights = examples.get("insights")
        assert isinstance(insights, dict)
        assert all(insights[k] for k in ("start", "stop", "keep", "try"))
        assert "Generating AI enrichments in one coherent pass…" in progress


class TestAnalysisDepth:
    @staticmethod
    def _sprint_data(summary: str = "Story A"):
        return [
            {
                "sprint_name": "Sprint 1",
                "completed_points": 3.0,
                "planned_count": 1,
                "completed_count": 1,
                "stories": [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 2,
                        "ac_count": 2,
                        "epic_key": "EP-1",
                        "point_changed": False,
                        "issue_key": "P-1",
                        "description": "Acceptance Criteria: returns HTTP 200",
                        "summary": summary,
                    }
                ],
            }
        ]

    def test_quick_mode_makes_no_llm_calls(self, monkeypatch):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        monkeypatch.setattr(
            "yeaboi.tools.team_learning._llm_invoke",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("Quick must not invoke an LLM")),
        )
        progress: list[str] = []
        _profile, examples = _run_parallel_analysis(
            "jira",
            "PROJ",
            self._sprint_data(),
            progress,
            include_ai_usage=False,
            include_doc_quality=False,
            analysis_depth="quick",
        )

        assert "Building fast summaries…" in progress
        assert examples["narrative"]["executive_summary"]
        assert examples["point_descriptions"]
        assert all(examples["insights"][key] for key in ("start", "stop", "keep", "try"))

    def test_quick_mode_can_omit_insights(self):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        _profile, examples = _run_parallel_analysis(
            "jira",
            "PROJ",
            self._sprint_data(),
            include_ai_usage=False,
            include_doc_quality=False,
            analysis_depth="quick",
            include_insights=False,
        )
        assert "insights" not in examples

    def test_deep_final_enrichments_use_one_synthesis(self, monkeypatch):
        from yeaboi.tools.team_learning import _run_parallel_analysis

        calls = {"count": 0}
        monkeypatch.setattr("yeaboi.tools.team_learning._parse_tickets_with_llm", lambda *a, **k: {})

        def synthesis(*args, **kwargs):
            calls["count"] += 1
            return {
                "point_descriptions": {"3": "Three points"},
                "narrative": {"executive_summary": "Summary", "sections": {}},
                "insights": {"start": [], "stop": [], "keep": [], "try": []},
                "_fallback_sections": [],
            }

        monkeypatch.setattr("yeaboi.tools.team_learning._generate_analysis_synthesis", synthesis)

        _run_parallel_analysis(
            "jira",
            "PROJ",
            self._sprint_data(),
            include_ai_usage=False,
            include_doc_quality=False,
            analysis_depth="deep",
        )
        assert calls["count"] == 1

    def test_deep_progress_marks_deterministic_fallbacks(self, monkeypatch):
        from yeaboi.tools import team_learning

        monkeypatch.setattr("yeaboi.tools.team_learning._parse_tickets_with_llm", lambda *a, **k: {})
        monkeypatch.setattr(
            "yeaboi.tools.team_learning._generate_point_descriptions",
            lambda _stories, calibrations, *_rest: team_learning._fallback_point_descriptions(calibrations),
        )
        monkeypatch.setattr(
            "yeaboi.tools.team_learning._generate_analysis_narrative",
            lambda profile, examples: team_learning._fallback_narrative(profile, examples),
        )
        monkeypatch.setattr(
            "yeaboi.tools.team_learning._generate_team_insights",
            lambda profile, examples: team_learning._fallback_team_insights(profile, examples),
        )
        progress = []

        team_learning._run_parallel_analysis(
            "jira",
            "PROJ",
            self._sprint_data(),
            progress=progress,
            include_ai_usage=False,
            include_doc_quality=False,
            analysis_depth="deep",
        )

        fallback_events = [item for item in progress if isinstance(item, dict) and item.get("status") == "fallback"]
        assert {item["component_id"] for item in fallback_events} == {
            "enrichment:point_descriptions",
            "enrichment:narrative",
            "enrichment:insights",
        }


class TestTicketParseCache:
    @staticmethod
    def _story(description: str = "Acceptance Criteria: returns HTTP 200"):
        return {
            "issue_key": "P-1",
            "summary": "Build endpoint",
            "description": description,
            "points": 3,
            "task_count": 2,
            "carried_over": False,
        }

    def test_cache_hit_skips_llm_and_content_change_invalidates(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from yeaboi.tools.team_learning import _parse_tickets_with_llm

        calls = {"count": 0}

        def invoke(*args, **kwargs):
            calls["count"] += 1
            return SimpleNamespace(content='[{"key":"P-1","discipline":"backend"}]')

        monkeypatch.setattr("yeaboi.tools.team_learning._llm_invoke", invoke)
        db = tmp_path / "sessions.db"
        updates: dict = {}
        first = _parse_tickets_with_llm(
            [self._story()],
            [],
            source="jira",
            project_key="PROJ",
            db_path=db,
            cache_updates=updates,
        )
        with TeamProfileStore(db) as store:
            store.save_ticket_parse_cache("jira", "PROJ", updates)

        second_updates: dict = {}
        second = _parse_tickets_with_llm(
            [self._story()],
            [],
            source="jira",
            project_key="PROJ",
            db_path=db,
            cache_updates=second_updates,
        )
        changed = _parse_tickets_with_llm(
            [self._story("Acceptance Criteria: returns HTTP 201")],
            [],
            source="jira",
            project_key="PROJ",
            db_path=db,
            cache_updates={},
        )

        assert first == second
        assert changed["P-1"]["discipline"] == "backend"
        assert calls["count"] == 2

    def test_deep_parser_uses_token_aware_batches(self, monkeypatch):
        import re
        from types import SimpleNamespace

        from yeaboi.tools.team_learning import _parse_tickets_with_llm

        calls = {"count": 0}

        def invoke(prompt, **kwargs):
            calls["count"] += 1
            keys = re.findall(r"--- ([A-Z]+-\d+):", prompt)
            return SimpleNamespace(content=json.dumps([{"key": key} for key in keys]))

        monkeypatch.setattr("yeaboi.tools.team_learning._llm_invoke", invoke)
        stories = [{**self._story(), "issue_key": f"P-{idx}", "summary": f"Story {idx}"} for idx in range(25)]
        result = _parse_tickets_with_llm(stories, [])

        assert len(result) == 25
        assert calls["count"] == 1

    def test_failed_large_batch_is_split_and_recovered(self, monkeypatch):
        import re
        from types import SimpleNamespace

        from yeaboi.tools.team_learning import _parse_tickets_with_llm

        calls = {"count": 0}

        def invoke(prompt, **kwargs):
            calls["count"] += 1
            keys = re.findall(r"--- ([A-Z]+-\d+):", prompt)
            if len(keys) > 4:
                return SimpleNamespace(content="[]")
            return SimpleNamespace(content=json.dumps([{"key": key} for key in keys]))

        monkeypatch.setattr("yeaboi.tools.team_learning._llm_invoke", invoke)
        stories = [{**self._story(), "issue_key": f"P-{idx}"} for idx in range(12)]

        result = _parse_tickets_with_llm(stories, [])

        assert len(result) == 12
        assert calls["count"] == 7


class TestSharedDesignSystem:
    def test_team_profile_html_uses_shared_theme(self):
        from yeaboi.team_profile_exporter import build_team_profile_html

        html = build_team_profile_html(TeamProfile(team_id="x", source="jira", project_key="X"))
        assert 'data-theme="midnight"' in html
        assert "yeaboi-export-theme" in html  # theme switcher present
        assert 'src="http' not in html and "<link" not in html  # self-contained


class TestAnalysisEnrichmentCache:
    def test_round_trip_is_scoped_by_task_key_and_model(self, tmp_path):
        with TeamProfileStore(tmp_path / "sessions.db") as store:
            store.save_analysis_enrichment("final_synthesis", "digest-1", "model-a", {"summary": "ok"})

            assert store.load_analysis_enrichment("final_synthesis", "digest-1", "model-a") == {"summary": "ok"}
            assert store.load_analysis_enrichment("final_synthesis", "digest-2", "model-a") is None
            assert store.load_analysis_enrichment("final_synthesis", "digest-1", "model-b") is None


class TestRunShape:
    """``Run[]`` is rendered by two things, and one of them is Markdown.

    A space against an emphasis delimiter cancels the emphasis — ``**x **`` and
    ``_ x_`` are literal asterisks and underscores — so a run that carries its
    own separating whitespace produces a broken bullet in the ``.md`` twin while
    looking fine in the browser. It is the kind of thing that comes back, which
    is why the block builders normalise it rather than each producer.
    """

    def _every_run(self, payload):
        if isinstance(payload, dict):
            if "s" in payload and ("strong" in payload or "em" in payload):
                yield payload
            for value in payload.values():
                yield from self._every_run(value)
        elif isinstance(payload, list):
            for item in payload:
                yield from self._every_run(item)

    def test_no_emphasis_run_carries_edge_whitespace(self):
        from tests.unit.test_web_wire_shapes import _team_profile_page

        # The wire fixture's profile, because it is the one input that reaches
        # every section — a narrower fixture would only pin the producers it
        # happens to exercise.
        runs = list(self._every_run(_profile_sections(_team_profile_page())))
        assert runs, "fixture must produce emphasised runs, or this asserts nothing"
        offenders = [run for run in runs if run["s"] != run["s"].strip()]
        assert not offenders, f"emphasis runs with edge whitespace: {offenders}"

    def test_normaliser_splits_the_space_out(self):
        from yeaboi.team_profile_exporter import _md_runs, _runs

        assert _runs([{"s": "Title: ", "strong": True}]) == [{"s": "Title:", "strong": True}, {"s": " "}]
        assert _md_runs([{"s": "Title: ", "strong": True}, {"s": "detail"}]) == "**Title:** detail"

    def test_whitespace_only_emphasis_becomes_a_plain_gap(self):
        from yeaboi.team_profile_exporter import _runs

        # Splitting this into a lead and a trail would duplicate it.
        assert _runs([{"s": "  ", "em": True}]) == [{"s": "  "}]


class TestExportVisuals:
    """Shared-design-system visuals rolled out to the team-analysis export."""

    def test_contributor_avatars_in_team_members_table(self):
        from yeaboi.team_profile_exporter import build_team_profile_html

        profile = TeamProfile(team_id="t", source="jira", project_key="P")
        ex = {
            "contributor_stats": [
                {
                    "name": "Ava Lin",
                    "delivery_pts": 21,
                    "stories_completed": 7,
                    "spill_rate": 5,
                    "avg_cycle_time": 4.0,
                    "sprints_active": 3,
                    "top_discipline": "backend",
                    "per_sprint": 7.0,
                },
                {
                    "name": "Bo Chen",
                    "delivery_pts": 13,
                    "stories_completed": 5,
                    "spill_rate": 12,
                    "avg_cycle_time": 6.0,
                    "sprints_active": 3,
                    "top_discipline": "frontend",
                    "per_sprint": 4.3,
                },
            ]
        }
        html = build_team_profile_html(profile, examples=ex)
        table = _blocks(html, "team-members", "table")[0]
        # `person` is what earns the avatar disc. The payload names a human; it
        # does not carry initials or a colour — those are derived in the bundle,
        # from the same digest the live boards use.
        assert [row[0] for row in table["rows"]] == [
            {"t": "Ava Lin", "person": True},
            {"t": "Bo Chen", "person": True},
        ]

    def test_ai_by_contributor_avatars(self):
        from yeaboi.team_profile import AiAdoptionSignal
        from yeaboi.team_profile_exporter import build_team_profile_html

        sig = AiAdoptionSignal(
            scanned_commits=30, ai_commits=6, footprint_pct=20.0, per_author=(("Ava Lin", 4), ("Bo Chen", 2))
        )
        profile = TeamProfile(team_id="t", source="jira", project_key="P", ai_adoption=sig)
        html = build_team_profile_html(profile, examples={"ai_adoption": {}})
        table = next(b for b in _blocks(html, "ai-adoption", "table") if b.get("title") == "By contributor")
        assert table["rows"] == [
            [{"t": "Ava Lin", "person": True}, "4"],
            [{"t": "Bo Chen", "person": True}, "2"],
        ]

    def test_task_type_distribution_mix_bar(self):
        from yeaboi.team_profile_exporter import build_team_profile_html

        profile = TeamProfile(team_id="t", source="jira", project_key="P")
        ex = {
            "task_decomposition": {
                "stories_with_tasks": 6,
                "total_stories": 8,
                "total_tasks": 24,
                "avg_tasks_per_story": 3.0,
                "task_completion_rate": 80.0,
                "type_distribution": {"backend": 50.0, "frontend": 30.0, "qa": 20.0},
            }
        }
        html = build_team_profile_html(profile, examples=ex)
        assert _blocks(html, "tasks", "bar")[0]["counts"] == [
            ["backend", 50.0],
            ["frontend", 30.0],
            ["qa", 20.0],
        ]

    def test_repo_activity_mix_bar(self):
        from yeaboi.team_profile_exporter import build_team_profile_html

        profile = TeamProfile(team_id="t", source="jira", project_key="P")
        ex = {
            "repositories": {
                "top_repos": [
                    {"repo": "acme/api", "stories": 9, "pct": 60.0},
                    {"repo": "acme/web", "stories": 6, "pct": 40.0},
                ]
            }
        }
        html = build_team_profile_html(profile, examples=ex)
        assert _blocks(html, "repos", "bar")[0]["counts"] == [["acme/api", 9], ["acme/web", 6]]

    def test_sprint_scope_sparkline_from_daily_snapshots(self):
        from yeaboi.team_profile import DailyScopeSnapshot, ScopeChangeEvent, SprintScopeTimeline
        from yeaboi.team_profile_exporter import build_team_profile_html

        tl = SprintScopeTimeline(
            sprint_name="Sprint 12",
            committed_pts=30.0,
            final_pts=36.0,
            delivered_pts=28.0,
            scope_change_total=6.0,
            daily_snapshots=(
                DailyScopeSnapshot(date="2026-07-06", total_scope_pts=30.0, stories_in_sprint=(("A-1", 30.0),)),
                DailyScopeSnapshot(date="2026-07-08", total_scope_pts=33.0, stories_in_sprint=(("A-1", 33.0),)),
                DailyScopeSnapshot(date="2026-07-10", total_scope_pts=36.0, stories_in_sprint=(("A-1", 36.0),)),
            ),
            change_events=(ScopeChangeEvent(issue_key="A-2", change_type="added", delta_pts=6.0),),
        )
        profile = TeamProfile(team_id="t", source="jira", project_key="P")
        ex = {
            "sprint_details": [{"name": "Sprint 12", "points": 30, "planned": 8, "completed": 7, "rate": 88}],
            "scope_changes": {
                "totals": {
                    "added_mid_sprint": 1,
                    "re_estimated": 0,
                    "total_stories": 8,
                    "avg_committed_velocity": 30.0,
                    "avg_delivered_velocity": 28.0,
                },
                "timelines": [tl],
            },
        }
        html = build_team_profile_html(profile, examples=ex)
        trend = _blocks(html, "sprints", "trend")[0]["trend"]
        assert trend["points"] == [["2026-07-06", 30.0], ["2026-07-08", 33.0], ["2026-07-10", 36.0]]
        assert "Sprint 12" in trend["label"]

    def test_self_contained_with_visuals(self):
        from yeaboi.team_profile_exporter import build_team_profile_html

        profile = TeamProfile(team_id="t", source="jira", project_key="P")
        ex = {"contributor_stats": [{"name": "Ava", "delivery_pts": 5}]}
        html = build_team_profile_html(profile, examples=ex)
        assert 'src="http' not in html and "<link" not in html
