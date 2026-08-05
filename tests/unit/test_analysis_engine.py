"""Tests for analysis/engine.py — the headless team-analysis pipeline.

The engine runs three DECOUPLED components: delivery (one TeamProfile per selected
tracker), and code/docs (each ONE global scan over its own sub-sources). The result is
``{delivery:{tracker:{...}}, code:{signal,examples}|None, docs:{...}|None, comparison,
components, warnings}``.
"""

import pytest

from yeaboi.analysis import get_team_roster, run_team_analysis
from yeaboi.team_profile import AiAdoptionSignal, DocQualitySignal, TeamProfile, TeamProfileStore


def _profile(**overrides) -> TeamProfile:
    defaults = dict(
        team_id="jira:PROJ",
        source="jira",
        project_key="PROJ",
        sample_sprints=8,
        sample_stories=40,
        velocity_avg=32.0,
        velocity_stddev=4.0,
    )
    defaults.update(overrides)
    return TeamProfile(**defaults)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "sessions.db"


@pytest.fixture
def wired(monkeypatch, db, tmp_path):
    """Wire the engine's team_learning + code/docs primitives to fakes; returns the
    capture dict (with per-component call counts)."""
    captured: dict = {"code_calls": 0, "docs_calls": 0, "members": {}}

    def fake_jira_fetch(project, count, **kwargs):
        captured["fetch"] = (project, count)
        captured["fetch_kwargs"] = kwargs
        return [{"sprint_name": "Sprint 1", "stories": []}, {"sprint_name": "Sprint 2", "stories": []}]

    monkeypatch.setattr("yeaboi.tools.team_learning._fetch_jira_history", fake_jira_fetch)
    monkeypatch.setattr(
        "yeaboi.tools.team_learning._fetch_azdevops_history",
        lambda project, count, **kwargs: [{"sprint_name": "Iteration 1", "stories": []}],
    )

    def fake_parallel(
        source,
        project,
        sprint_data,
        progress,
        include_ai_usage=True,
        include_doc_quality=True,
        members=None,
        warnings=None,
        analysis_depth="deep",
        include_insights=True,
        db_path=None,
        cache_updates=None,
    ):
        progress.append("Analysing…")
        captured["parallel"] = (source, project, len(sprint_data))
        # Delivery must NOT run code/docs inline — they're global scans now.
        captured["inline_ai"] = include_ai_usage
        captured["inline_docs"] = include_doc_quality
        captured["members"][source] = members
        examples = {"sprint_details": []}
        if include_insights:
            examples["insights"] = {"start": [], "stop": [], "keep": [], "try": []}
        captured["analysis_depth"] = analysis_depth
        return _profile(team_id=f"{source}:{project}", source=source, project_key=project), examples

    monkeypatch.setattr("yeaboi.tools.team_learning._run_parallel_analysis", fake_parallel)
    monkeypatch.setattr(
        "yeaboi.team_profile_exporter.write_analysis_log",
        lambda profile, *, examples, sprint_names, duration_secs: tmp_path / "analysis.log",
    )
    monkeypatch.setattr(
        "yeaboi.tools.team_learning._generate_team_insights",
        lambda profile, examples: {"start": [], "stop": [], "keep": [], "try": []},
    )

    def fake_ai(source, pk, ds, alls, members=None, sub_sources=None, **kwargs):
        captured["code_calls"] += 1
        captured["code_sub"] = sub_sources
        captured["code_members"] = members
        captured["code_kwargs"] = kwargs
        return AiAdoptionSignal(scanned_commits=10, ai_commits=4, footprint_pct=40.0), {"summary": {}, "coverage": []}

    def fake_doc(source, pk, sub_sources=None, **kwargs):
        captured["docs_calls"] += 1
        captured["docs_sub"] = sub_sources
        captured["docs_kwargs"] = kwargs
        return DocQualitySignal(pages_scanned=5, avg_clarity=70.0), {"summary": {}, "coverage": []}

    monkeypatch.setattr("yeaboi.analysis.ai_usage.run_ai_adoption", fake_ai)
    monkeypatch.setattr("yeaboi.analysis.doc_quality.run_doc_quality", fake_doc)
    return captured


# Full component set for the common single-tracker case.
_ALL = {"delivery": ["jira"], "code": ["github"], "docs": ["confluence"]}


class TestDelivery:
    def test_happy_path_saves_profile(self, wired, db):
        r = run_team_analysis(source="jira", project_key="PROJ", components=_ALL, db_path=db)
        assert set(r["delivery"]) == {"jira"}
        sub = r["delivery"]["jira"]
        assert sub["profile"].project_key == "PROJ"
        assert sub["insights"] is not None
        assert sub["headline_stats"]
        assert r["warnings"] == []
        # Delivery must not have run code/docs inline.
        assert wired["inline_ai"] is False and wired["inline_docs"] is False
        with TeamProfileStore(db) as store:
            assert store.list_profiles()

    def test_project_key_passthrough_single_tracker(self, wired, db):
        run_team_analysis(source="jira", project_key="ABC", components={"delivery": ["jira"]}, db_path=db)
        assert wired["fetch"] == ("ABC", 8)

    def test_sprint_count_passthrough(self, wired, db):
        run_team_analysis(
            source="jira", project_key="PROJ", sprint_count=4, components={"delivery": ["jira"]}, db_path=db
        )
        assert wired["fetch"] == ("PROJ", 4)

    def test_insights_skippable(self, wired, db, monkeypatch):
        def boom(profile, examples):
            raise AssertionError("insights must not run when include_insights=False")

        monkeypatch.setattr("yeaboi.tools.team_learning._generate_team_insights", boom)
        r = run_team_analysis(components={"delivery": ["jira"]}, include_insights=False, db_path=db)
        assert r["delivery"]["jira"]["insights"] is None

    def test_log_failure_is_warning_not_crash(self, wired, db, monkeypatch):
        def boom(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr("yeaboi.team_profile_exporter.write_analysis_log", boom)
        r = run_team_analysis(components={"delivery": ["jira"]}, db_path=db)
        assert r["delivery"]["jira"]["log_path"] == ""
        assert any("Analysis log" in w for w in r["warnings"])

    def test_progress_list_is_shared(self, wired, db):
        progress: list = []
        run_team_analysis(components={"delivery": ["jira"]}, progress=progress, db_path=db)
        assert "Analysing…" in progress
        lifecycle = [item for item in progress if isinstance(item, dict)]
        assert [item["status"] for item in lifecycle] == ["running", "completed"]

    def test_no_sprints_degrades_to_warning(self, wired, db, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.team_learning._fetch_jira_history", lambda project, count, **kwargs: [])
        # Delivery fails, but a global code scan still returns → no raise.
        r = run_team_analysis(components={"delivery": ["jira"], "code": ["github"]}, db_path=db)
        assert r["delivery"] == {}
        assert r["code"] is not None
        assert any("delivery analysis failed" in w for w in r["warnings"])

    def test_nothing_selected_raises(self, monkeypatch, db):
        monkeypatch.setattr("yeaboi.tools.team_learning._detect_source", lambda: "")
        with pytest.raises(ValueError, match="No tracker configured"):
            run_team_analysis(components={"delivery": [], "code": [], "docs": []}, db_path=db)

    def test_deep_is_default_and_metadata_is_persisted(self, wired, db):
        result = run_team_analysis(components={"delivery": ["jira"]}, db_path=db)
        sub = result["delivery"]["jira"]
        assert result["analysis_depth"] == "deep"
        assert sub["analysis_depth"] == "deep"
        assert sub["examples"]["analysis_depth"] == "deep"
        assert set(sub["stage_timings"]) == {"fetch_secs", "analysis_secs", "total_secs"}
        assert wired["analysis_depth"] == "deep"

    def test_rejects_unknown_analysis_depth(self, db):
        with pytest.raises(ValueError, match="analysis_depth"):
            run_team_analysis(analysis_depth="turbo", components={"delivery": []}, db_path=db)

    def test_quick_rejects_llm_generated_samples(self, db):
        with pytest.raises(ValueError, match="requires analysis_depth='deep'"):
            run_team_analysis(
                generate_samples=True,
                analysis_depth="quick",
                components={"delivery": []},
                db_path=db,
            )


class TestGlobalCodeDocs:
    def test_feature_selection_skips_unselected_jobs(self, wired, db):
        progress = []
        result = run_team_analysis(
            components=_ALL,
            analysis_features=["documentation"],
            progress=progress,
            db_path=db,
        )
        assert result["analysis_features"] == ["documentation"]
        assert result["delivery"] == {}
        assert result["code"] is None
        assert result["docs"] is not None
        assert wired["code_calls"] == 0
        assert wired["docs_calls"] == 1
        ids = {item["component_id"] for item in progress if isinstance(item, dict)}
        assert ids == {"docs:documentation"}

    def test_unknown_feature_is_rejected(self, wired, db):
        with pytest.raises(ValueError, match="analysis_features"):
            run_team_analysis(
                components=_ALL,
                analysis_features=["security_audit"],
                db_path=db,
            )

    def test_feature_lifecycle_uses_each_coverage_report(self, wired, db, monkeypatch):
        def code_component(*args, **kwargs):
            return AiAdoptionSignal(scanned_commits=10), {
                "enabled_features": ["ai_footprint", "code_health"],
                "activity_coverage": {
                    "status": "complete",
                    "completed": 2,
                    "eligible": 2,
                    "has_data": True,
                    "assets": [],
                },
                "coverage_report": {
                    "status": "failed",
                    "completed": 0,
                    "eligible": 10,
                    "failed": 10,
                    "inaccessible": 0,
                    "truncated": 0,
                    "has_data": False,
                    "grouped_errors": [
                        {
                            "provider": "azdo",
                            "status": "failed",
                            "detail": "provider pagination repeated results",
                        }
                    ],
                    "assets": [],
                },
                "repository_health": {"files_analysed": 0},
                "findings": [],
                "action_plan": [],
            }

        monkeypatch.setattr("yeaboi.tools.team_learning._run_ai_usage_component", code_component)
        progress = []

        result = run_team_analysis(
            components={"delivery": ["jira"], "code": ["azdo"]},
            analysis_features=["delivery", "ai_footprint", "code_health"],
            progress=progress,
            db_path=db,
        )

        latest = {item["component_id"]: item for item in progress if isinstance(item, dict)}
        assert latest["code:ai_footprint"]["status"] == "completed"
        assert latest["code:code_health"]["status"] == "failed"
        assert latest["code:code_health"]["detail"] == ("0/10 completed · provider pagination repeated results")
        assert result["coverage"]["status"] == "partial"
        assert result["coverage"]["components"]["ai_footprint"]["status"] == "complete"
        assert result["coverage"]["components"]["code_health"]["status"] == "failed"

    def test_partial_documentation_does_not_stop_other_analysis_jobs(self, wired, db, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.tools.team_learning._run_doc_quality_component",
            lambda *args, **kwargs: (
                DocQualitySignal(pages_scanned=1),
                {
                    "coverage_report": {
                        "status": "partial",
                        "completed": 1,
                        "eligible": 2,
                        "failed": 1,
                        "inaccessible": 0,
                        "truncated": 0,
                        "grouped_errors": [{"detail": "one unreadable page"}],
                        "assets": [],
                    },
                    "action_plan": [],
                },
            ),
        )
        monkeypatch.setattr(
            "yeaboi.tools.team_learning._run_ai_usage_component",
            lambda *args, **kwargs: (
                None,
                {
                    "enabled_features": ["code_health"],
                    "coverage_report": {
                        "status": "complete",
                        "completed": 3,
                        "eligible": 3,
                        "assets": [],
                    },
                    "repository_health": {"files_analysed": 3},
                    "action_plan": [],
                },
            ),
        )

        result = run_team_analysis(
            components={"code": ["azdo"], "docs": ["confluence"]},
            analysis_features=["code_health", "documentation"],
            db_path=db,
        )

        assert result["docs"] is not None
        assert result["code"] is not None
        assert result["coverage"]["components"]["documentation"]["status"] == "partial"
        assert result["coverage"]["components"]["code_health"]["status"] == "complete"

    def test_code_health_can_run_without_ai_footprint_signal(self, wired, db):
        result = run_team_analysis(
            components={"delivery": ["jira"], "code": ["github"]},
            analysis_features=["delivery", "code_health"],
            db_path=db,
        )
        assert result["code"] is not None
        assert result["code"]["signal"] is None
        assert "ai_adoption" in result["delivery"]["jira"]["examples"]

    def test_code_and_docs_run_once_and_attach(self, wired, db):
        r = run_team_analysis(components=_ALL, db_path=db)
        assert wired["code_calls"] == 1 and wired["docs_calls"] == 1
        assert r["code"]["signal"].footprint_pct == 40.0
        assert r["docs"]["signal"].avg_clarity == 70.0
        # Global signals attached to the saved delivery profile (stored-browser view).
        prof = r["delivery"]["jira"]["profile"]
        assert prof.ai_adoption.footprint_pct == 40.0
        assert prof.doc_quality.avg_clarity == 70.0

    def test_scanned_once_across_two_delivery_trackers(self, wired, db, monkeypatch):
        _configure(monkeypatch, jira=True, azdevops=True)
        r = run_team_analysis(
            components={"delivery": ["jira", "azdevops"], "code": ["github", "azdo"], "docs": ["confluence"]},
            db_path=db,
        )
        # The core fix: ONE code scan + ONE docs scan even with two delivery trackers.
        assert wired["code_calls"] == 1 and wired["docs_calls"] == 1
        assert wired["code_sub"] == ["github", "azdo"]
        assert wired["docs_sub"] == ["confluence"]
        # Both trackers carry the same global signal.
        assert r["delivery"]["jira"]["profile"].ai_adoption.footprint_pct == 40.0
        assert r["delivery"]["azdevops"]["profile"].ai_adoption.footprint_pct == 40.0

    def test_code_only_no_delivery(self, wired, db):
        r = run_team_analysis(components={"code": ["github"]}, db_path=db)
        assert r["delivery"] == {}
        assert r["code"] is not None and r["docs"] is None
        # No delivery profile → nothing persisted.
        with TeamProfileStore(db) as store:
            assert store.list_profiles() == []

    def test_docs_only_no_delivery(self, wired, db):
        r = run_team_analysis(components={"docs": ["confluence"]}, db_path=db)
        assert r["delivery"] == {} and r["code"] is None
        assert r["docs"]["signal"].avg_clarity == 70.0


class TestTopLevelConcurrency:
    def test_all_four_component_jobs_overlap_and_persist_after_completion(self, monkeypatch, db):
        import threading

        barrier = threading.Barrier(4)
        state = {"active": 0, "peak": 0, "completed": set()}
        lock = threading.Lock()

        def overlap(label, result):
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                barrier.wait(timeout=2)
                return result
            finally:
                with lock:
                    state["active"] -= 1
                    state["completed"].add(label)

        def delivery(tracker, *args, **kwargs):
            profile = _profile(
                team_id=f"{tracker}:PROJ",
                source=tracker,
                project_key="PROJ",
            )
            return overlap(
                tracker,
                {
                    "profile": profile,
                    "examples": {},
                    "warnings": [],
                },
            )

        monkeypatch.setattr("yeaboi.analysis.engine._run_delivery", delivery)
        monkeypatch.setattr(
            "yeaboi.tools.team_learning._run_ai_usage_component",
            lambda *a, **k: overlap(
                "code",
                (AiAdoptionSignal(scanned_commits=1), {}),
            ),
        )
        monkeypatch.setattr(
            "yeaboi.tools.team_learning._run_doc_quality_component",
            lambda *a, **k: overlap(
                "docs",
                (DocQualitySignal(pages_scanned=1), {}),
            ),
        )

        def persist(delivery_results, code, docs, path):
            assert state["completed"] == {"jira", "azdevops", "code", "docs"}

        monkeypatch.setattr("yeaboi.analysis.engine._persist_delivery", persist)

        result = run_team_analysis(
            components={
                "delivery": ["jira", "azdevops"],
                "code": ["github"],
                "docs": ["confluence"],
            },
            include_insights=False,
            db_path=db,
        )

        assert state["peak"] == 4
        assert list(result["delivery"]) == ["jira", "azdevops"]
        assert result["code"] is not None and result["docs"] is not None

    def test_delivery_order_is_configured_order_not_completion_order(self, monkeypatch, db):
        import threading

        azdo_finished = threading.Event()

        def delivery(tracker, *args, **kwargs):
            if tracker == "jira":
                assert azdo_finished.wait(timeout=2)
            else:
                azdo_finished.set()
            return {
                "profile": _profile(
                    team_id=f"{tracker}:PROJ",
                    source=tracker,
                    project_key="PROJ",
                ),
                "examples": {},
                "warnings": [],
            }

        monkeypatch.setattr("yeaboi.analysis.engine._run_delivery", delivery)
        monkeypatch.setattr("yeaboi.analysis.engine._persist_delivery", lambda *a, **k: None)

        result = run_team_analysis(
            components={"delivery": ["jira", "azdevops"]},
            include_insights=False,
            db_path=db,
        )

        assert list(result["delivery"]) == ["jira", "azdevops"]

    def test_unexpected_component_failure_does_not_cancel_other_jobs(self, wired, monkeypatch, db):
        monkeypatch.setattr(
            "yeaboi.tools.team_learning._run_ai_usage_component",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate limited")),
        )

        result = run_team_analysis(components=_ALL, db_path=db)

        assert result["delivery"]["jira"]["profile"] is not None
        assert result["docs"] is not None
        assert result["code"] is None
        assert "Code analysis failed: rate limited" in result["warnings"]


class TestWindowForwarding:
    def test_default_window_reaches_both_components(self, wired, db):
        """Regression: a '!= 120' guard used to skip forwarding the default, so
        docs silently scanned run_doc_quality's own 90-day default while code
        scanned 120 days — shown side by side undisclosed."""
        run_team_analysis(components=_ALL, db_path=db)
        assert wired["code_kwargs"]["window_days"] == 120
        assert wired["docs_kwargs"]["window_days"] == 120

    def test_custom_window_reaches_both_components(self, wired, db):
        run_team_analysis(components=_ALL, db_path=db, analysis_window_days=45)
        assert wired["code_kwargs"]["window_days"] == 45
        assert wired["docs_kwargs"]["window_days"] == 45


class TestCancelEvent:
    """cancel_event is the TUI's cooperative Ctrl-C seam: queued jobs abort at
    pickup and a set event discards every result before anything persists."""

    def test_delivery_forwards_progress_and_cancel_to_fetcher(self, wired, db):
        import threading

        event = threading.Event()
        run_team_analysis(components={"delivery": ["jira"], "code": [], "docs": []}, cancel_event=event, db_path=db)
        kwargs = wired["fetch_kwargs"]
        assert kwargs["cancel_event"] is event
        assert isinstance(kwargs["progress"], list)

    def test_pre_set_cancel_event_aborts_without_saving(self, wired, db):
        import threading

        from yeaboi.analysis.engine import AnalysisCancelledError

        event = threading.Event()
        event.set()
        with pytest.raises(AnalysisCancelledError):
            run_team_analysis(components=_ALL, cancel_event=event, db_path=db)
        with TeamProfileStore(db) as store:
            assert store.list_profiles() == []

    def test_cancel_mid_run_discards_finished_results(self, monkeypatch, db):
        import threading

        from yeaboi.analysis.engine import AnalysisCancelledError

        event = threading.Event()

        def delivery(tracker, *args, **kwargs):
            # A job that completes normally, but sets the event while running —
            # its finished result must still be discarded at the pre-persist gate.
            event.set()
            return {"profile": _profile(), "examples": {}, "warnings": []}

        persisted = []
        monkeypatch.setattr("yeaboi.analysis.engine._run_delivery", delivery)
        monkeypatch.setattr("yeaboi.analysis.engine._persist_delivery", lambda *a, **k: persisted.append(a))

        with pytest.raises(AnalysisCancelledError):
            run_team_analysis(components={"delivery": ["jira"]}, cancel_event=event, db_path=db)
        assert persisted == []

    def test_cancelled_queued_job_marks_progress_cancelled(self, wired, db):
        import threading

        from yeaboi.analysis.engine import AnalysisCancelledError

        event = threading.Event()
        event.set()
        progress: list = []
        with pytest.raises(AnalysisCancelledError):
            run_team_analysis(components=_ALL, cancel_event=event, progress=progress, db_path=db)
        lifecycle = [item for item in progress if isinstance(item, dict)]
        assert any(item["status"] == "failed" and item["detail"] == "cancelled" for item in lifecycle)

    def test_unset_cancel_event_runs_normally(self, wired, db):
        import threading

        result = run_team_analysis(components=_ALL, cancel_event=threading.Event(), db_path=db)
        assert result["delivery"]["jira"]["profile"] is not None
        with TeamProfileStore(db) as store:
            assert store.list_profiles()


def _configure(monkeypatch, *, jira=True, azdevops=True):
    """Toggle which trackers _available_sources() sees as configured."""
    monkeypatch.setattr("yeaboi.config.get_jira_base_url", lambda: "https://x.atlassian.net" if jira else None)
    monkeypatch.setattr("yeaboi.config.get_jira_token", lambda: "tok" if jira else None)
    monkeypatch.setattr(
        "yeaboi.config.get_azure_devops_org_url", lambda: "https://dev.azure.com/x" if azdevops else None
    )
    monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: "pat" if azdevops else None)


class TestMultiTrackerDelivery:
    def test_both_trackers_separate_profiles(self, wired, db, monkeypatch):
        _configure(monkeypatch, jira=True, azdevops=True)
        r = run_team_analysis(components={"delivery": ["jira", "azdevops"]}, db_path=db)
        assert set(r["delivery"]) == {"jira", "azdevops"}
        assert r["delivery"]["jira"]["profile"].source == "jira"
        assert r["delivery"]["azdevops"]["profile"].source == "azdevops"
        assert r["comparison"]  # side-by-side rows when >=2 delivery trackers
        assert "Avg velocity" in [row[0] for row in r["comparison"]]
        with TeamProfileStore(db) as store:
            assert len(store.list_profiles()) == 2

    def test_source_both_default_components(self, wired, db, monkeypatch):
        _configure(monkeypatch, jira=True, azdevops=True)
        r = run_team_analysis(source="both", db_path=db)
        assert set(r["delivery"]) == {"jira", "azdevops"}

    def test_one_tracker_fails_other_returns(self, wired, db, monkeypatch):
        _configure(monkeypatch, jira=True, azdevops=True)
        monkeypatch.setattr("yeaboi.tools.team_learning._fetch_azdevops_history", lambda project, count, **kwargs: [])
        r = run_team_analysis(components={"delivery": ["jira", "azdevops"]}, db_path=db)
        assert set(r["delivery"]) == {"jira"}
        assert any("Azure DevOps delivery analysis failed" in w for w in r["warnings"])
        assert r["comparison"] == []  # only one tracker survived


class TestMemberSubset:
    def test_members_reach_delivery_and_code(self, wired, db):
        run_team_analysis(components=_ALL, members={"jira": ["Alice"]}, db_path=db)
        assert wired["members"]["jira"] == ["Alice"]
        # Code author filter uses the union of selected members.
        assert wired["code_members"] == ["Alice"]

    def test_members_union_across_trackers_for_code(self, wired, db, monkeypatch):
        _configure(monkeypatch, jira=True, azdevops=True)
        run_team_analysis(
            components={"delivery": ["jira", "azdevops"], "code": ["github"]},
            members={"jira": ["Alice"], "azdevops": ["Bob"]},
            db_path=db,
        )
        assert wired["members"]["jira"] == ["Alice"]
        assert wired["members"]["azdevops"] == ["Bob"]
        assert wired["code_members"] == ["Alice", "Bob"]  # sorted union


class TestSelectedMemberVelocity:
    def test_sums_selected_per_sprint_case_insensitive(self):
        from yeaboi.tools.team_learning import selected_member_velocity

        contrib = [
            {"name": "Alice", "per_sprint": 8.0},
            {"name": "Bob", "per_sprint": 5.0},
            {"name": "Carol", "per_sprint": 3.0},
        ]
        assert selected_member_velocity(contrib, ["alice", "bob"]) == 13.0

    def test_empty_inputs(self):
        from yeaboi.tools.team_learning import selected_member_velocity

        assert selected_member_velocity([], ["Alice"]) == 0.0
        assert selected_member_velocity([{"name": "A", "per_sprint": 5}], []) == 0.0


class TestGetTeamRoster:
    def test_returns_sorted_unique_assignees_without_history_scan(self, monkeypatch, tmp_path):
        from yeaboi.team_roster import RosterMember, RosterResult

        monkeypatch.setattr(
            "yeaboi.tools.team_learning._fetch_jira_history",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("roster must not scan sprint history")),
        )
        monkeypatch.setattr(
            "yeaboi.team_roster.fetch_roster_result",
            lambda **kwargs: RosterResult(
                (
                    RosterMember("Bob", "jira", "2"),
                    RosterMember("Alice", "jira", "1"),
                    RosterMember("Alice", "jira", "1"),
                ),
                "complete",
                (),
            ),
        )
        assert get_team_roster(source="jira", project_key="PROJ", db_path=tmp_path / "db") == ["Alice", "Bob"]

    def test_empty_board_returns_empty(self, monkeypatch, tmp_path):
        from yeaboi.team_roster import RosterResult

        monkeypatch.setattr(
            "yeaboi.team_roster.fetch_roster_result",
            lambda **kwargs: RosterResult((), "empty", ()),
        )
        assert get_team_roster(source="jira", project_key="PROJ", db_path=tmp_path / "db") == []

    def test_no_tracker_raises(self, monkeypatch):
        monkeypatch.setattr("yeaboi.tools.team_learning._detect_source", lambda: "")
        with pytest.raises(ValueError, match="No tracker configured"):
            get_team_roster()


class TestCliRunLearn:
    def test_learn_uses_engine_and_real_db(self, monkeypatch):
        import io

        from rich.console import Console

        from yeaboi.cli import _run_learn

        called: dict = {}

        def fake_run(**kwargs):
            called.update(kwargs)
            return {
                "delivery": {"jira": {"profile": _profile()}},
                "code": None,
                "docs": None,
                "warnings": ["Jira rate limited"],
            }

        monkeypatch.setattr("yeaboi.analysis.engine.run_team_analysis", fake_run)
        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", fake_run)
        buf = io.StringIO()
        _run_learn(Console(file=buf, width=100))
        out = buf.getvalue()
        assert "Team profile saved for jira/PROJ" in out
        assert "Jira rate limited" in out
        assert called == {"include_insights": False}  # engine defaults handle DB + source

    def test_learn_prints_engine_error(self, monkeypatch):
        import io

        from rich.console import Console

        from yeaboi.cli import _run_learn

        def boom(**kwargs):
            raise ValueError("No tracker configured for analysis")

        monkeypatch.setattr("yeaboi.analysis.run_team_analysis", boom)
        buf = io.StringIO()
        _run_learn(Console(file=buf, width=100))
        assert "No tracker configured" in buf.getvalue()


class TestOfferableCodeSources:
    """The Code row's gate, which is deliberately looser than the headless one.

    ``_offerable_code_sources`` answers "what can the wizard set up during this
    run"; ``_available_code_sources`` answers "what is scannable with zero further
    input" and drives the headless component default. Collapsing the two would
    either hide GitHub from the picker (the bug) or make headless runs emit an
    empty code section for a user who never configured owners.
    """

    @staticmethod
    def _gates(monkeypatch, *, gh_token="", gh_owners=(), azdo_token="", azdo_projects=()):
        from yeaboi.analysis.engine import _available_code_sources, _offerable_code_sources

        monkeypatch.setattr("yeaboi.config.get_github_token", lambda: gh_token)
        monkeypatch.setattr("yeaboi.config.get_team_analysis_github_owners", lambda: tuple(gh_owners))
        monkeypatch.setattr("yeaboi.config.get_azure_devops_token", lambda: azdo_token)
        monkeypatch.setattr("yeaboi.config.get_team_analysis_azdo_projects", lambda: tuple(azdo_projects))
        return _offerable_code_sources(), _available_code_sources()

    def test_bare_token_is_offerable_but_not_yet_scannable(self, monkeypatch):
        offerable, available = self._gates(monkeypatch, gh_token="ghp_x")
        assert offerable == ["github"]
        assert available == []

    def test_configured_owners_satisfy_both_gates(self, monkeypatch):
        offerable, available = self._gates(monkeypatch, gh_token="ghp_x", gh_owners=("acme",))
        assert offerable == ["github"] and available == ["github"]

    def test_no_token_offers_nothing(self, monkeypatch):
        # Owners without a token are unusable — the picker must not offer a host
        # whose discovery call would fail on the next screen.
        offerable, available = self._gates(monkeypatch, gh_owners=("acme",))
        assert offerable == [] and available == []

    def test_azure_gate_is_unchanged_by_the_split(self, monkeypatch):
        offerable, available = self._gates(monkeypatch, azdo_token="pat", azdo_projects=("Infra",))
        assert offerable == ["azdo"] and available == ["azdo"]
        offerable, available = self._gates(monkeypatch, azdo_token="pat")
        assert offerable == [] and available == []
