"""Tests for the poker cross-mode estimation context gatherer."""

from __future__ import annotations

import dataclasses

import pytest

from yeaboi.agent.state import (
    DeliveredItem,
    DeliveryReport,
    MemberUpdate,
    Priority,
    StandupReport,
    StoryPointValue,
    UserStory,
)
from yeaboi.poker.context import (
    _MAX_DELIVERED,
    _MAX_LINE,
    _MAX_MD,
    _MAX_PLANNING_MATCHES,
    PokerEstimationContext,
    _assignee_lines,
    _calibration_lines,
    _clip,
    _delivery_lines,
    _names_match,
    _planning_lines,
    _project_key_for,
    _similar_title,
    _team_lines,
    _title_tokens,
    format_poker_context_md,
    gather_poker_context,
)
from yeaboi.team_profile import SpilloverStats, StoryPointCalibration, TeamProfile


def _profile(**overrides) -> TeamProfile:
    defaults = dict(
        team_id="jira-PROJ-20260701",
        source="jira",
        project_key="PROJ",
        sample_sprints=4,
        sample_stories=30,
        velocity_avg=24.5,
        velocity_stddev=6.1,
        estimation_accuracy_pct=68.0,
        sprint_completion_rate=81.0,
        point_calibrations=(
            StoryPointCalibration(
                point_value=5,
                avg_cycle_time_days=4.2,
                sample_count=12,
                common_patterns=("single API endpoint", "config change", "third pattern"),
                typical_task_count=3.1,
                overshoot_pct=20.0,
            ),
            StoryPointCalibration(point_value=8, avg_cycle_time_days=7.5, sample_count=0),
        ),
        spillover=SpilloverStats(
            carried_over_pct=22.0, avg_spillover_pts=4.0, most_common_spillover_reason="backend stories > 5 pts"
        ),
    )
    defaults.update(overrides)
    return TeamProfile(**defaults)


def _story(title: str, *, points: StoryPointValue = StoryPointValue.FIVE, confidence: str = "medium") -> UserStory:
    return UserStory(
        id="S1",
        feature_id="F1",
        persona="user",
        goal="log in quickly",
        benefit="save time",
        acceptance_criteria=(),
        story_points=points,
        priority=Priority.MEDIUM,
        title=title,
        points_confidence=confidence,
        points_rationale="matches past login work",
    )


class TestProjectKeyFor:
    def test_jira_key_prefix(self):
        assert _project_key_for("jira", "PROJ-123") == "PROJ"

    def test_jira_multi_dash_keeps_all_but_last(self):
        assert _project_key_for("jira", "AB-CD-9") == "AB-CD"

    def test_jira_keyless(self):
        assert _project_key_for("jira", "nodash") == ""

    def test_azdevops_from_config(self, monkeypatch):
        monkeypatch.setenv("AZURE_DEVOPS_PROJECT", "MyProject")
        assert _project_key_for("azdevops", "123") == "MyProject"

    def test_azdevops_unconfigured(self, monkeypatch):
        monkeypatch.delenv("AZURE_DEVOPS_PROJECT", raising=False)
        assert _project_key_for("azdevops", "123") == ""

    def test_demo_and_unknown(self):
        assert _project_key_for("demo", "DEMO-1") == ""
        assert _project_key_for("", "PROJ-1") == ""


class TestMatchingHelpers:
    def test_names_match_case_and_containment(self):
        assert _names_match("Alex", "alex chen")
        assert _names_match("Alex Chen", "alex")
        assert not _names_match("Alex", "Sam")
        assert not _names_match("", "Sam")
        assert not _names_match("Alex", "")

    def test_title_tokens_filters_short_and_stopwords(self):
        tokens = _title_tokens("Add the user login page for this app")
        assert "user" in tokens and "login" in tokens
        assert "the" not in tokens and "for" not in tokens  # short words dropped
        assert "this" not in tokens and "page" not in tokens  # stopwords dropped

    def test_similar_title_requires_two_shared_tokens(self):
        assert _similar_title("Add user login rate limit", "Update user login flow")
        assert not _similar_title("Add user login", "Fix billing export")

    def test_clip_truncates_long_text(self):
        clipped = _clip("x" * 500)
        assert len(clipped) == _MAX_LINE + 3
        assert clipped.endswith("...")


class TestLineFormatters:
    def test_team_lines_velocity_and_spillover(self):
        lines = _team_lines(_profile())
        assert any("24.5 pts/sprint" in ln and "68%" in ln and "81%" in ln for ln in lines)
        assert any("22% of stories slip" in ln and "backend stories > 5 pts" in ln for ln in lines)

    def test_team_lines_empty_profile_yields_nothing(self):
        assert _team_lines(_profile(velocity_avg=0.0, spillover=SpilloverStats())) == ()

    def test_calibration_lines_skip_zero_samples_and_cap_patterns(self):
        by_value = _calibration_lines(_profile())
        assert [v for v, _ in by_value] == [5.0]  # 8-pt entry has no samples
        line = by_value[0][1]
        assert "5-pt stories" in line and "4.2 days" in line and "20% overshoot" in line and "(n=12)" in line
        assert "single API endpoint" in line and "config change" in line
        assert "third pattern" not in line  # capped at _MAX_PATTERNS

    def test_assignee_lines_matches_and_formats(self):
        report = StandupReport(
            member_updates=(
                MemberUpdate(name="Alex Chen", blockers="waiting on API keys", ticketing_summary="moving PROJ-9"),
                MemberUpdate(name="Sam", blockers="none of note"),
            )
        )
        lines = _assignee_lines(report, "alex")
        assert len(lines) == 2
        assert "Alex Chen (ticket assignee)" in lines[0] and "waiting on API keys" in lines[0]
        assert "moving PROJ-9" in lines[1]

    def test_assignee_lines_no_match_or_no_assignee(self):
        report = StandupReport(member_updates=(MemberUpdate(name="Sam", blockers="x"),))
        assert _assignee_lines(report, "Alex") == ()
        assert _assignee_lines(report, "") == ()
        assert _assignee_lines(None, "Alex") == ()

    def test_delivery_lines_buckets_and_caps(self):
        items = tuple(
            DeliveredItem(key=f"PROJ-{i}", title="Add login rate limit", status="Done", assignee="Alex")
            for i in range(5)
        ) + tuple(
            DeliveredItem(key=f"PROJ-9{i}", title="Tweak login rate widget", status="Done", assignee="Sam")
            for i in range(5)
        )
        lines = _delivery_lines(items, "Alex", "Improve login rate handling", "PROJ-0")
        by_assignee = [ln for ln in lines if ln.startswith("Recently delivered by")]
        similar = [ln for ln in lines if ln.startswith("Similar delivered ticket")]
        assert len(by_assignee) == _MAX_DELIVERED
        assert len(similar) == _MAX_DELIVERED
        assert not any(
            "PROJ-0 " in ln or "'PROJ-0'" in ln or "PROJ-0'" in ln for ln in lines
        )  # current ticket excluded
        assert any("assignee" in ln for ln in similar)

    def test_planning_lines_fuzzy_match_and_cap(self):
        stories = [
            _story("Add user login rate limit"),
            _story("Improve user login speed"),
            _story("Refactor user login audit"),
            _story("Unrelated billing work"),
        ]
        lines = _planning_lines(stories, "Fix user login timeout")
        assert len(lines) == _MAX_PLANNING_MATCHES
        assert "5 pts" in lines[0] and "confidence: medium" in lines[0] and "matches past login work" in lines[0]


class TestFormatMd:
    def test_empty_context_renders_empty(self):
        assert format_poker_context_md(PokerEstimationContext()) == ""
        assert PokerEstimationContext().is_empty

    def test_only_nonempty_sections_render(self):
        ctx = PokerEstimationContext(team_lines=("Velocity: 20 pts/sprint.",), retro_lines=("CI flakiness (3×)",))
        md = format_poker_context_md(ctx)
        assert "**Team estimation history (analysis mode):**" in md
        assert "**Recurring pain points (retros):**" in md
        assert "standup" not in md and "planning" not in md

    def test_hard_cap(self):
        ctx = PokerEstimationContext(team_lines=tuple(f"line {i} " + "x" * 190 for i in range(30)))
        assert len(format_poker_context_md(ctx)) <= _MAX_MD


class TestGatherPokerContext:
    def _seed_db(self, tmp_path, monkeypatch):
        db = tmp_path / "sessions.db"
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
        monkeypatch.setattr("yeaboi.config.get_sessions_db", lambda: db)
        return db

    def test_demo_source_skips_gathering(self, tmp_path, monkeypatch):
        self._seed_db(tmp_path, monkeypatch)
        ctx = gather_poker_context({"source": "demo", "key": "DEMO-1", "summary": "x", "assignee": "Demo"})
        assert ctx.is_empty and ctx.summary_md == ""

    def test_missing_db_yields_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: tmp_path / "nope.db")
        ctx = gather_poker_context({"source": "jira", "key": "PROJ-1", "summary": "x", "assignee": "Alex"})
        assert ctx.is_empty

    def test_seeded_stores_produce_sections(self, tmp_path, monkeypatch):
        db = self._seed_db(tmp_path, monkeypatch)
        from yeaboi.reporting.store import ReportingStore
        from yeaboi.sessions import SessionStore
        from yeaboi.standup.store import StandupStore
        from yeaboi.team_profile import TeamProfileStore

        with SessionStore(db) as sessions:  # creates the base schema first
            sessions.create_session("plan-1", "Login")
            sessions.save_state("plan-1", {"stories": [_story("Add user login rate limit")]})
        with TeamProfileStore(db) as tstore:
            tstore.save(_profile())
        with StandupStore(db) as sstore:
            sstore.record_run(
                StandupReport(
                    date="2026-07-24",
                    session_id="s1",
                    confidence_pct=72,
                    member_updates=(MemberUpdate(name="Alex", blockers="waiting on API keys"),),
                )
            )
        with ReportingStore(db) as rstore:
            rstore.record_run(
                DeliveryReport(
                    project_name="Login",
                    delivered_items=(
                        DeliveredItem(
                            key="PROJ-87",
                            title="Add signup rate limit for user login",
                            status="Done",
                            source="jira",
                            assignee="Sam",
                        ),
                    ),
                )
            )

        ctx = gather_poker_context(
            {"source": "jira", "key": "PROJ-123", "summary": "Improve user login rate limit", "assignee": "Alex"},
            project_name="Login",
        )
        assert not ctx.is_empty
        assert any("24.5 pts/sprint" in ln for ln in ctx.team_lines)
        assert any("5-pt stories" in ln for ln in ctx.calibration_lines)
        assert ctx.calibration_by_value[0][0] == 5.0
        assert any("waiting on API keys" in ln for ln in ctx.assignee_lines)
        assert any("PROJ-87" in ln for ln in ctx.delivery_lines)
        assert any("similar story" in ln for ln in ctx.planning_lines)
        for marker in ("analysis mode", "latest standup", "reporting mode", "planning mode"):
            assert marker in ctx.summary_md

    def test_broken_store_is_non_fatal(self, tmp_path, monkeypatch):
        db = self._seed_db(tmp_path, monkeypatch)
        from yeaboi.sessions import SessionStore

        with SessionStore(db) as sessions:
            sessions.create_session("plan-1", "Login")
            sessions.save_state("plan-1", {"stories": [_story("Add user login rate limit")]})

        def _boom(*args, **kwargs):
            raise RuntimeError("store exploded")

        monkeypatch.setattr("yeaboi.team_profile.TeamProfileStore.load_by_project", _boom)
        ctx = gather_poker_context(
            {"source": "jira", "key": "PROJ-1", "summary": "Fix user login rate limit", "assignee": "Alex"}
        )
        assert any("similar story" in ln for ln in ctx.planning_lines)  # other sources still gathered

    def test_context_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            PokerEstimationContext().summary_md = "x"  # type: ignore[misc]
