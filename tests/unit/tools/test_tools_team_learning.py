"""Unit tests for team_learning tools: analyze_team_history and compare_plan_to_actuals."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from yeaboi.tools.team_learning import (
    _analyse_repositories,
    _azdo_pr_matches_work_item,
    _azdo_work_item_link_target_id,
    _build_profile_from_sprint_data,
    _cycle_time_days,
    _extract_repos_from_azdo_relations,
    _safe_float,
    _stddev,
    _wit_get_work_items_batch,
)

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


class TestSafeFloat:
    def test_normal_value(self):
        assert _safe_float(3.5) == 3.5

    def test_int(self):
        assert _safe_float(5) == 5.0

    def test_none(self):
        assert _safe_float(None) == 0.0

    def test_string(self):
        assert _safe_float("abc") == 0.0


class TestStddev:
    def test_zero_variance(self):
        assert _stddev([5.0, 5.0, 5.0]) == 0.0

    def test_single_value(self):
        assert _stddev([10.0]) == 0.0

    def test_known_variance(self):
        # [2, 4, 4, 4, 5, 5, 7, 9] → stddev = 2.0
        result = _stddev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        assert abs(result - 2.0) < 0.01


class TestCycleTime:
    def test_simple_dates(self):
        ct = _cycle_time_days("2026-01-01", "2026-01-06")
        assert ct is not None
        assert abs(ct - 5.0) < 0.1

    def test_missing_date(self):
        assert _cycle_time_days(None, "2026-01-06") is None
        assert _cycle_time_days("2026-01-01", None) is None

    def test_same_date(self):
        ct = _cycle_time_days("2026-01-01", "2026-01-01")
        assert ct is not None
        assert ct == 0.0

    def test_jira_iso_with_fractional_seconds_and_offset(self):
        """Jira returns e.g. ...000+0000; parsing must not truncate the timezone."""
        start = "2024-01-15T10:00:00.000+0000"
        end = "2024-01-20T16:00:00.000+0000"
        ct = _cycle_time_days(start, end)
        assert ct is not None
        assert 5.0 < ct < 6.0


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------


class TestBuildProfileFromSprintData:
    def _make_sprint(self, stories: list[dict], completed_pts: float = 20.0) -> dict:
        return {
            "sprint_name": "Sprint 1",
            "completed_points": completed_pts,
            "stories": stories,
            "planned_count": len(stories),
            "completed_count": len(stories),
        }

    def test_basic_profile(self):
        sprints = [
            self._make_sprint(
                [
                    {
                        "points": 3,
                        "cycle_time_days": 2.0,
                        "discipline": "backend",
                        "task_count": 2,
                        "ac_count": 3,
                        "epic_key": "E1",
                        "point_changed": False,
                    },
                    {
                        "points": 5,
                        "cycle_time_days": 4.0,
                        "discipline": "frontend",
                        "task_count": 3,
                        "ac_count": 3,
                        "epic_key": "E1",
                        "point_changed": False,
                    },
                ],
                completed_pts=8.0,
            ),
            self._make_sprint(
                [
                    {
                        "points": 3,
                        "cycle_time_days": 2.5,
                        "discipline": "backend",
                        "task_count": 2,
                        "ac_count": 3,
                        "epic_key": "E2",
                        "point_changed": True,
                    },
                ],
                completed_pts=3.0,
            ),
        ]
        profile = _build_profile_from_sprint_data("jira", "PROJ", sprints)

        assert profile.team_id.startswith("jira-PROJ-")
        assert profile.source == "jira"
        assert profile.sample_sprints == 2
        assert profile.sample_stories == 3
        assert profile.velocity_avg > 0

    def test_point_calibrations_populated(self):
        stories = [
            {
                "points": 3,
                "cycle_time_days": 2.0,
                "discipline": "backend",
                "task_count": 2,
                "ac_count": 3,
                "epic_key": "E1",
                "point_changed": False,
            },
            {
                "points": 3,
                "cycle_time_days": 3.0,
                "discipline": "backend",
                "task_count": 1,
                "ac_count": 2,
                "epic_key": "E1",
                "point_changed": False,
            },
        ]
        sprints = [self._make_sprint(stories)]
        profile = _build_profile_from_sprint_data("jira", "PROJ", sprints)

        # Find the 3-point calibration
        cal_3 = next((c for c in profile.point_calibrations if c.point_value == 3), None)
        assert cal_3 is not None
        assert cal_3.sample_count == 2
        assert abs(cal_3.avg_cycle_time_days - 2.5) < 0.1

    def test_story_shapes_by_discipline(self):
        stories = [
            {
                "points": 3,
                "cycle_time_days": 2.0,
                "discipline": "backend",
                "task_count": 2,
                "ac_count": 3,
                "epic_key": "E1",
                "point_changed": False,
            },
            {
                "points": 5,
                "cycle_time_days": 4.0,
                "discipline": "frontend",
                "task_count": 3,
                "ac_count": 4,
                "epic_key": "E2",
                "point_changed": False,
            },
        ]
        sprints = [self._make_sprint(stories)]
        profile = _build_profile_from_sprint_data("azdevops", "MyProject", sprints)

        disc_names = {s.discipline for s in profile.story_shapes}
        assert "backend" in disc_names
        assert "frontend" in disc_names

    def test_empty_sprints(self):
        profile = _build_profile_from_sprint_data("jira", "PROJ", [])
        assert profile.sample_sprints == 0
        assert profile.sample_stories == 0
        assert profile.velocity_avg == 0.0

    def test_estimation_accuracy(self):
        stories = [
            {
                "points": 3,
                "cycle_time_days": 2.0,
                "discipline": "backend",
                "task_count": 2,
                "ac_count": 3,
                "epic_key": "E1",
                "point_changed": False,
            },
            {
                "points": 5,
                "cycle_time_days": 4.0,
                "discipline": "frontend",
                "task_count": 3,
                "ac_count": 3,
                "epic_key": "E1",
                "point_changed": True,
            },
        ]
        sprints = [self._make_sprint(stories)]
        profile = _build_profile_from_sprint_data("jira", "PROJ", sprints)
        # 1 out of 2 stories had points changed → 50% accuracy
        assert profile.estimation_accuracy_pct == 50.0


# ---------------------------------------------------------------------------
# analyze_team_history tool (mocked)
# ---------------------------------------------------------------------------


class TestAnalyzeTeamHistoryTool:
    def test_returns_error_when_no_source(self):
        """When no source is detected and none passed, return error JSON."""
        with patch("yeaboi.tools.team_learning._detect_source", return_value=""):
            from yeaboi.tools.team_learning import analyze_team_history

            result = analyze_team_history.invoke({"source": "", "project_key": ""})
            data = json.loads(result)
            assert "error" in data

    def test_returns_error_for_unknown_source(self):
        from yeaboi.tools.team_learning import analyze_team_history

        result = analyze_team_history.invoke({"source": "gitlab", "project_key": "X"})
        data = json.loads(result)
        assert "error" in data

    def test_sprint_count_clamped(self):
        """Sprint count is clamped to [3, 12]."""
        with patch("yeaboi.tools.team_learning._detect_source", return_value="jira"):
            with patch(
                "yeaboi.tools.team_learning._fetch_jira_history",
                return_value=[],
            ) as mock_fetch:
                from yeaboi.tools.team_learning import analyze_team_history

                analyze_team_history.invoke({"source": "jira", "sprint_count": 50})
                # clamped to 12
                assert mock_fetch.call_args[0][1] == 12

                analyze_team_history.invoke({"source": "jira", "sprint_count": 1})
                # clamped to 3
                assert mock_fetch.call_args[0][1] == 3


# ---------------------------------------------------------------------------
# Repository extraction helpers
# ---------------------------------------------------------------------------


class TestRepoExtractionHelpers:
    def test_azdo_work_item_link_target_id_object(self):
        link = SimpleNamespace(target=SimpleNamespace(id=99))
        assert _azdo_work_item_link_target_id(link) == 99

    def test_azdo_work_item_link_target_id_dict(self):
        link = SimpleNamespace(target={"id": 100})
        assert _azdo_work_item_link_target_id(link) == 100

    def test_wit_get_work_items_batch_fallback_without_expand(self):
        class _Wit:
            def __init__(self):
                self.calls = []

            def get_work_items(self, ids, project=None, fields=None, expand=None):
                self.calls.append((expand,))
                if expand == "Relations":
                    raise RuntimeError("Relations not supported")
                return [SimpleNamespace(id=ids[0], fields={})]

        wit = _Wit()
        out = _wit_get_work_items_batch(wit, "P", [1], ["System.Title"], want_relations=True)
        assert len(out) == 1
        assert wit.calls == [("Relations",), (None,)]

    def test_extract_repos_from_azdo_relations_pullrequest_url(self):
        rel = SimpleNamespace(
            url="https://dev.azure.com/org/proj/_git/my-repo/pullrequest/12",
            attributes=None,
        )
        wi = SimpleNamespace(relations=[rel])
        assert "my-repo" in _extract_repos_from_azdo_relations(wi)

    def test_extract_repos_from_azdo_relations_empty(self):
        assert _extract_repos_from_azdo_relations(SimpleNamespace(relations=[])) == []

    def test_azdo_pr_matches_work_item_ref(self):
        ref = SimpleNamespace(id="42")
        pr = SimpleNamespace(
            work_item_refs=[ref],
            source_ref_name="",
            title="",
            description="",
        )
        assert _azdo_pr_matches_work_item(pr, "42")
        assert not _azdo_pr_matches_work_item(pr, "99")

    def test_azdo_pr_matches_branch_name(self):
        pr = SimpleNamespace(
            work_item_refs=[],
            source_ref_name="refs/heads/feature/42-add-widget",
            title="fix",
            description="",
        )
        assert _azdo_pr_matches_work_item(pr, "42")

    def test_analyse_repositories_detection_sources(self):
        out = _analyse_repositories(
            [
                {
                    "repos": ["a"],
                    "repo_sources": ["jira_text"],
                    "points": 1,
                    "discipline": "backend",
                    "cycle_time_days": 1.0,
                    "carried_over": False,
                },
            ]
        )
        assert out
        assert "ticket text and comments" in out["detection_sources"]

    def test_analyse_repositories_legacy_sources(self):
        out = _analyse_repositories(
            [
                {
                    "repos": ["mono"],
                    "points": 2,
                    "discipline": "backend",
                    "cycle_time_days": 1.0,
                    "carried_over": False,
                },
            ]
        )
        assert out
        assert "PR links in ticket text" in out["detection_sources"]


# ---------------------------------------------------------------------------
# compare_plan_to_actuals tool (mocked)
# ---------------------------------------------------------------------------


class TestComparePlanToActuals:
    def test_returns_error_no_db(self, tmp_path, monkeypatch):
        """Returns error JSON when sessions.db does not exist."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "nonexistent_home")
        from yeaboi.tools.team_learning import compare_plan_to_actuals

        result = compare_plan_to_actuals.invoke({"session_id": "test-123"})
        data = json.loads(result)
        assert "error" in data


# ---------------------------------------------------------------------------
# Point description generation
# ---------------------------------------------------------------------------


class TestGeneratePointDescriptions:
    def _make_calibrations(self):
        from yeaboi.team_profile import StoryPointCalibration

        return (
            StoryPointCalibration(
                point_value=1,
                avg_cycle_time_days=0.5,
                sample_count=10,
                common_patterns=("config change",),
                typical_task_count=1.0,
                overshoot_pct=5.0,
            ),
            StoryPointCalibration(
                point_value=3,
                avg_cycle_time_days=3.0,
                sample_count=20,
                common_patterns=("create/build (40%)", "fix/resolve (30%)"),
                typical_task_count=3.0,
                overshoot_pct=25.0,
            ),
            StoryPointCalibration(
                point_value=5,
                avg_cycle_time_days=7.0,
                sample_count=6,
                common_patterns=("create/build (50%)",),
                typical_task_count=8.0,
                overshoot_pct=60.0,
            ),
        )

    def _make_stories(self):
        return [
            {"points": 1, "summary": "Update staging config"},
            {"points": 1, "summary": "Fix typo in error page"},
            {"points": 3, "summary": "Build notification endpoint"},
            {"points": 3, "summary": "Create user settings page"},
            {"points": 5, "summary": "Rebuild central region failover"},
            {"points": 5, "summary": "Migrate database to new cluster"},
        ]

    @patch("yeaboi.agent.llm.get_llm")
    def test_successful_generation(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_point_descriptions

        result_json = json.dumps(
            {
                "1": "Quick config changes completed in under a day.",
                "3": "Standard feature work spanning 3 days with 3 subtasks.",
                "5": "Complex multi-component work with high spill risk.",
            }
        )
        mock_get_llm.return_value.invoke.return_value = SimpleNamespace(content=result_json)

        result = _generate_point_descriptions(
            self._make_stories(),
            self._make_calibrations(),
            {},
            {},
        )
        assert isinstance(result, dict)
        assert "1" in result
        assert "3" in result
        assert "5" in result
        assert "config" in result["1"].lower() or "day" in result["1"].lower()

    @patch("yeaboi.agent.llm.get_llm")
    def test_fallback_on_error(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_point_descriptions

        mock_get_llm.return_value.invoke.side_effect = RuntimeError("API error")

        result = _generate_point_descriptions(
            self._make_stories(),
            self._make_calibrations(),
            {},
            {},
        )
        assert isinstance(result, dict)
        assert len(result) >= 1
        # Fallback should still produce descriptions
        assert "1" in result

    def test_empty_calibrations(self):
        from yeaboi.tools.team_learning import _generate_point_descriptions

        result = _generate_point_descriptions([], (), {}, {})
        assert result == {}

    @patch("yeaboi.agent.llm.get_llm")
    def test_json_with_code_fences(self, mock_get_llm):
        from yeaboi.tools.team_learning import _generate_point_descriptions

        result_json = '```json\n{"3": "Standard feature work."}\n```'
        mock_get_llm.return_value.invoke.return_value = SimpleNamespace(content=result_json)

        result = _generate_point_descriptions(
            self._make_stories(),
            self._make_calibrations(),
            {},
            {},
        )
        assert isinstance(result, dict)
        assert "3" in result


class TestFallbackPointDescriptions:
    def test_produces_descriptions(self):
        from yeaboi.team_profile import StoryPointCalibration
        from yeaboi.tools.team_learning import _fallback_point_descriptions

        cals = (
            StoryPointCalibration(
                point_value=3,
                avg_cycle_time_days=3.0,
                sample_count=20,
                common_patterns=("create/build (40%)",),
                typical_task_count=3.0,
                overshoot_pct=10.0,
            ),
        )
        result = _fallback_point_descriptions(cals)
        assert "3" in result
        assert "cycle time" in result["3"].lower() or "3d" in result["3"].lower()

    def test_high_overshoot_warning(self):
        from yeaboi.team_profile import StoryPointCalibration
        from yeaboi.tools.team_learning import _fallback_point_descriptions

        cals = (
            StoryPointCalibration(
                point_value=5,
                avg_cycle_time_days=7.0,
                sample_count=6,
                typical_task_count=8.0,
                overshoot_pct=60.0,
            ),
        )
        result = _fallback_point_descriptions(cals)
        assert "splitting" in result["5"].lower() or "overshoots" in result["5"].lower()

    def test_empty_calibrations(self):
        from yeaboi.tools.team_learning import _fallback_point_descriptions

        assert _fallback_point_descriptions(()) == {}


# ---------------------------------------------------------------------------
# Pooled per-story fetch (Jira extras / AzDO revisions)
# ---------------------------------------------------------------------------


class _FakeJiraClient:
    """Minimal JIRA stand-in with per-endpoint call counters."""

    def __init__(self):
        self.calls: dict[str, int] = {}
        self._options: dict = {}

    def _count(self, key):
        self.calls[key] = self.calls.get(key, 0) + 1

    def fields(self):
        return []

    def boards(self, projectKeyOrID=None):  # noqa: N803 - mirrors jira-lib API
        return [SimpleNamespace(id=1, name="Board")]

    def sprints(self, board_id, state=""):
        return [
            SimpleNamespace(
                id=10,
                name="Sprint 1",
                startDate="2024-01-01T00:00:00.000+0000",
                endDate="2024-01-14T00:00:00.000+0000",
            )
        ]

    def sprint_info(self, board_id, sprint_id):
        return {"completedPoints": 3}

    def _issue_row(self, key):
        fields = SimpleNamespace(
            customfield_10016=3.0,
            story_points=None,
            issuetype=SimpleNamespace(name="Story"),
            resolutiondate="2024-01-10T00:00:00.000+0000",
            created="2024-01-02T00:00:00.000+0000",
            subtasks=[],
            customfield_10014="",
            summary="Do thing",
            description="",
            labels=[],
            assignee=None,
            status=SimpleNamespace(name="Done"),
        )
        return SimpleNamespace(key=key, id="1001", fields=fields)

    def search_issues(self, jql, maxResults=0, fields=""):  # noqa: N803 - mirrors jira-lib API
        self._count("search")
        return [self._issue_row("PROJ-1")]

    def comments(self, key):
        self._count(f"comments:{key}")
        return [SimpleNamespace(body="done note", created="2024-01-03T00:00:00.000+0000")]

    def issue(self, key, expand=""):
        self._count(f"changelog:{key}")
        return SimpleNamespace(
            key=key,
            changelog=SimpleNamespace(histories=[]),
            fields=SimpleNamespace(created="2024-01-02T00:00:00.000+0000"),
        )


class TestFetchJiraHistoryPooled:
    def _run(self, monkeypatch, progress=None, cancel_event=None):
        from yeaboi.tools.team_learning import _fetch_jira_history

        fake = _FakeJiraClient()
        monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
        monkeypatch.setenv("JIRA_EMAIL", "t@example.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")
        monkeypatch.setattr("jira.JIRA", lambda server=None, basic_auth=None: fake)
        result = _fetch_jira_history("PROJ", 8, progress=progress, cancel_event=cancel_event)
        return fake, result

    def test_changelog_fetched_exactly_once_per_story(self, monkeypatch):
        """Regression: scope enrichment + timeline used to fetch the same changelog twice."""
        fake, result = self._run(monkeypatch)
        assert fake.calls["changelog:PROJ-1"] == 1
        assert fake.calls["comments:PROJ-1"] == 1

    def test_comments_backfilled_into_story(self, monkeypatch):
        fake, result = self._run(monkeypatch)
        assert len(result) == 1
        (story,) = [s for s in result[0]["stories"] if not s.get("carried_over")]
        assert story["comments"] == ["done note"]
        assert story["comments_timed"] == [("2024-01-03T00:00:00.000+0000", "done note")]

    def test_progress_events_emitted_on_delivery_row(self, monkeypatch):
        progress: list = []
        self._run(monkeypatch, progress=progress)
        events = [e for e in progress if isinstance(e, dict) and e.get("component_id") == "delivery:jira"]
        assert events, "expected delivery:jira lifecycle events during fetch"
        assert all(e["phase"] == "Reading sprint history" for e in events)
        assert events[0]["total"] == 1
        assert events[-1]["secondary_count"] >= 1

    def test_pre_set_cancel_event_raises_before_fetch(self, monkeypatch):
        import threading

        import pytest

        from yeaboi.analysis.cancellation import AnalysisCancelledError

        event = threading.Event()
        event.set()
        with pytest.raises(AnalysisCancelledError):
            self._run(monkeypatch, cancel_event=event)


class TestFetchJiraStoryExtras:
    def test_spillover_gets_changelog_only(self):
        from yeaboi.tools.team_learning import _fetch_jira_story_extras

        fake = _FakeJiraClient()
        done = [fake._issue_row("PROJ-1")]
        extras = _fetch_jira_story_extras(fake, done, ["PROJ-2"])
        assert set(extras) == {"PROJ-1", "PROJ-2"}
        assert extras["PROJ-1"]["comments_timed"]
        assert extras["PROJ-2"]["comments_timed"] == []
        assert "comments:PROJ-2" not in fake.calls
        assert fake.calls["changelog:PROJ-2"] == 1

    def test_each_subfetch_degrades_independently(self):
        from yeaboi.tools.team_learning import _fetch_jira_story_extras

        fake = _FakeJiraClient()

        def _boom(key):
            raise RuntimeError("throttled")

        fake.comments = _boom
        extras = _fetch_jira_story_extras(fake, [fake._issue_row("PROJ-1")], [])
        assert extras["PROJ-1"]["comments_timed"] == []
        assert extras["PROJ-1"]["changelog_issue"] is not None

        fake2 = _FakeJiraClient()
        fake2.issue = lambda key, expand="": (_ for _ in ()).throw(RuntimeError("throttled"))
        extras2 = _fetch_jira_story_extras(fake2, [fake2._issue_row("PROJ-1")], [])
        assert extras2["PROJ-1"]["changelog_issue"] is None
        assert extras2["PROJ-1"]["comments_timed"]


class TestPrefetchedScopeConsumers:
    def test_enrich_skips_story_with_missing_changelog(self):
        from yeaboi.tools.team_learning import _enrich_jira_scope_changes

        story = {"issue_key": "PROJ-1", "points": 3}
        _enrich_jira_scope_changes([story], "Sprint 1", "2024-01-01", "2024-01-14", {"PROJ-1": None})
        assert "point_changed" not in story or story["point_changed"] is False

    def test_enrich_detects_point_change_from_prefetched_changelog(self):
        from yeaboi.tools.team_learning import _enrich_jira_scope_changes

        history = SimpleNamespace(
            created="2024-01-05T10:00:00.000+0000",
            items=[SimpleNamespace(field="Story Points", fromString="3", toString="5")],
        )
        issue = SimpleNamespace(
            changelog=SimpleNamespace(histories=[history]),
            fields=SimpleNamespace(created="2023-12-01T00:00:00.000+0000"),
        )
        story = {"issue_key": "PROJ-1", "points": 5.0}
        _enrich_jira_scope_changes(
            [story],
            "Sprint 1",
            "2024-01-01T00:00:00.000+0000",
            "2024-01-14T00:00:00.000+0000",
            {"PROJ-1": issue},
        )
        assert story["point_changed"] is True
        assert story["original_points"] == 3.0

    def test_timeline_missing_changelog_assumes_in_scope_all_sprint(self):
        from yeaboi.tools.team_learning import _build_jira_sprint_scope_timeline

        story = {"issue_key": "PROJ-1", "points": 5.0, "summary": "Thing", "issue_url": ""}
        timeline = _build_jira_sprint_scope_timeline([story], "Sprint 1", "2024-01-01", "2024-01-14", 5.0, {})
        assert timeline is not None
        assert timeline.committed_pts == 5.0
        assert timeline.final_pts == 5.0


class TestFetchAzdoRevisions:
    def test_one_get_revisions_call_per_story_and_failures_omitted(self):
        from yeaboi.tools.team_learning import _fetch_azdo_revisions

        calls: list[int] = []

        class _Wit:
            def get_revisions(self, wi_id, project=""):
                calls.append(wi_id)
                if wi_id == 102:
                    raise RuntimeError("boom")
                return [SimpleNamespace(fields={})]

        stories = [{"issue_key": "101"}, {"issue_key": "102"}, {"issue_key": "abc"}, {"issue_key": ""}]
        out = _fetch_azdo_revisions(_Wit(), "Proj", stories)
        assert sorted(calls) == [101, 102]
        assert set(out) == {"101"}

    def test_cancel_event_raises(self):
        import threading

        import pytest

        from yeaboi.analysis.cancellation import AnalysisCancelledError
        from yeaboi.tools.team_learning import _fetch_azdo_revisions

        class _Wit:
            def get_revisions(self, wi_id, project=""):
                return [SimpleNamespace(fields={})]

        event = threading.Event()
        event.set()
        with pytest.raises(AnalysisCancelledError):
            _fetch_azdo_revisions(_Wit(), "Proj", [{"issue_key": "101"}], cancel_event=event)
