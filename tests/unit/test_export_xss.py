"""Cross-cutting XSS regression harness for every shareable-HTML builder.

Like test_surface_parity.py, this file enforces a repo-wide contract rather than
testing one module: no user- or LLM-influenced string may reach an exported HTML
page unescaped. Each entry point gets a realistic artifact whose every string
field is replaced with a distinctive probe (via ``_inject``), and the built page
must contain the probe only in HTML-escaped form.

Legitimate pages contain a real <script> (the theme switcher), so assertions key
on the exact probe substrings, never on "<script>" alone.
"""

from __future__ import annotations

import dataclasses
from enum import Enum

import pytest

XSS_PROBE = '<script>window.__xss_probe__=1</script>" onmouseover="alert(1)'

# The raw forms that must never appear in output. Checked separately because an
# exporter may legitimately drop one half (e.g. a field only rendered truncated).
RAW_MARKERS = ("<script>window.__xss_probe__", '" onmouseover="alert(1)')

# html.escape(XSS_PROBE) prefix — proves probed fields reached the page escaped
# rather than being silently dropped.
ESCAPED_MARKER = "&lt;script&gt;window.__xss_probe__"

# The same proof for a React-rendered export, where the probe travels as a JSON
# string rather than as markup. json_island escapes `<`, `>` and `&` to their
# \uXXXX forms — the three characters that could otherwise end the containing
# <script> element early and have the rest parsed as HTML.
ISLAND_MARKER = "\\u003cscript\\u003ewindow.__xss_probe__"

# How a page announces which of the two it is. Set by web.assets.render_page.
ISLAND_TAG = 'id="yeaboi-data"'

# (dataclass name, field name) pairs kept at their original value because the
# builder needs them semantically valid. Every exclusion must state why.
_EXCLUDE_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        # by_grid() only renders cards whose grid is one of RETRO_GRIDS; probing
        # it would silently drop every card (and its probed text) from the page.
        ("RetroCard", "grid"),
    }
)

# Dict keys whose values keep their original value. Every entry must state why.
_EXCLUDE_KEYS: frozenset[str] = frozenset(
    {
        # Feature toggles: probing them disables the AI Usage / Code Health
        # sections entirely, hiding the very fields we want to exercise.
        "enabled_features",
    }
)


def _inject(obj):
    """Recursively replace every ``str`` in an artifact with the XSS probe.

    Enums are left alone (StrEnum members are ``str`` instances but carry
    semantic meaning); dict keys are left alone (they are lookup/sort keys —
    key-rendered paths get targeted tests instead); numbers/bools/None pass
    through so format specs and arithmetic keep working.
    """
    if isinstance(obj, Enum):
        return obj
    if isinstance(obj, str):
        return XSS_PROBE
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        cls_name = type(obj).__name__
        changes = {
            f.name: _inject(getattr(obj, f.name))
            for f in dataclasses.fields(obj)
            if f.init and (cls_name, f.name) not in _EXCLUDE_FIELDS
        }
        return dataclasses.replace(obj, **changes)
    if isinstance(obj, dict):
        return {k: (v if k in _EXCLUDE_KEYS else _inject(v)) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return tuple(_inject(v) for v in obj)
    if isinstance(obj, list):
        return [_inject(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Base artifacts — one realistic builder per HTML entry point
# ---------------------------------------------------------------------------


def _retro_html() -> str:
    from yeaboi.agent.state import RetroCard, RetroReport
    from yeaboi.retro.export import build_retro_html

    report = RetroReport(
        date="2026-07-10",
        session_id="sess-1",
        project_name="Demo",
        sprint_name="Sprint 5",
        cards=(
            RetroCard(grid="went_well", text="fast deploys", author="Sam", origin="web"),
            RetroCard(grid="didnt_go_well", text="flaky tests", author="Rae", origin="web", reactions=(("👍", 3),)),
            RetroCard(grid="action_items", text="add retry guard", author="AI", origin="ai"),
        ),
        participants=("Sam", "Rae"),
        carried_action_items=(RetroCard(grid="action_items", text="ship docs", status="carried_over"),),
    )
    return build_retro_html(_inject(report))


def _poker_html() -> str:
    from yeaboi.agent.state import PokerReport, PokerTicketResult, PokerVote
    from yeaboi.poker.export import build_poker_html

    report = PokerReport(
        date="2026-07-25",
        session_id="sess-1",
        project_name="Proj",
        source="jira",
        scope_label="Sprint 42",
        tickets=(
            PokerTicketResult(
                key="PROJ-1",
                url="https://x.example/browse/PROJ-1",
                summary="Add login",
                description="Details",
                final_points=5.0,
                estimated=True,
                votes=(PokerVote("Alex", "🦊", "5"), PokerVote("Sam", "🐙", "8")),
                ai_note="The 8 voter sees risk",
                duel_transcript="Alex — turn 1:\nsimple endpoint",
                duel_low="Alex (5)",
                duel_high="Sam (8)",
            ),
            PokerTicketResult(key="PROJ-2", summary="Skipped one", initial_points=3.0),
        ),
        participants=("Alex", "Sam"),
    )
    return build_poker_html(_inject(report))


def _standup_html() -> str:
    from yeaboi.agent.state import MemberUpdate, StandupReport
    from yeaboi.standup.export import build_standup_html

    report = StandupReport(
        date="2026-07-10",
        session_id="demo",
        sprint_name="Sprint 5",
        sprint_day=3,
        sprint_total_days=10,
        confidence_pct=82,
        confidence_label="At risk",
        confidence_rationale="behind ideal burn",
        team_summary="steady progress",
        member_updates=(
            MemberUpdate(
                name="Alice",
                summary="login page",
                source="inferred",
                links=(("PR #1", "https://x.example/pr/1"),),
            ),
            MemberUpdate(
                name="Bob",
                summary="paired on auth",
                blockers="waiting on review",
                source="combined",
                self_report="paired with Alice\non auth",
                ticketing_summary="moved PROJ-9",
                ticketing_links=(("PROJ-9", "https://x.example/PROJ-9"),),
                code_summary="two PRs merged",
                documentation_summary="updated runbook",
            ),
        ),
        activity_counts=(("github", 2), ("jira", 1)),
        category_coverage=(("code", "covered"), ("docs", "not_configured")),
        skipped_sources=(("notion", "no token"),),
        warnings=("Jira: authentication failed",),
    )
    return build_standup_html(_inject(report))


def _reporting_html() -> str:
    from yeaboi.agent.state import DeliveredItem, DeliveryReport
    from yeaboi.reporting.export import build_report_html

    report = DeliveryReport(
        period_label="Last sprint",
        period_start="2026-06-29",
        period_end="2026-07-13",
        project_name="Acme Portal",
        sprint_names=("Sprint 12",),
        headline="Shipped SSO.",
        executive_summary="We delivered\nsingle sign-on.",
        themes=(("Security", ("SSO login", "MFA rollout")),),
        highlights=("SSO live for all users",),
        metrics=(("Items delivered", "7"), ("Contributors", "3")),
        delivered_items=(DeliveredItem(key="ACME-1", title="SSO", status="Done", source="jira", assignee="Ada"),),
        emoji_theme=(("headline", "🚀"),),
        warnings=("test warning",),
    )
    return build_report_html(_inject(report))


def _roadmap_html() -> str:
    from yeaboi.agent.state import RoadmapAnalysis, RoadmapProject
    from yeaboi.roadmap.export import build_roadmap_html

    analysis = RoadmapAnalysis(
        source_type="local",
        source_locator="/tmp/q3.md",
        source_label="Q3 2026 Roadmap",
        summary="Revenue and security.",
        projects=(
            RoadmapProject(
                name="Billing revamp",
                description="Rebuild billing\nfor metered plans.",
                size="large",
                rationale="Revenue-critical.",
                priority=1,
                themes=("Payments",),
                quarter="Q3 2026",
            ),
        ),
        warnings=("Roadmap truncated",),
        generated_at="2026-07-20T09:00:00",
    )
    return build_roadmap_html(_inject(analysis))


def _perf_prep_html() -> str:
    from yeaboi.agent.state import OneOnOnePrep
    from yeaboi.performance.export import build_prep_html

    prep = OneOnOnePrep(
        engineer="Ada",
        date="2026-07-12",
        activity_summary="shipped auth\nfixed flaky test",
        talking_points=("tp",),
        feedback=("fb",),
        goals=("goal",),
        gaps=("gap",),
        improvements=("improve",),
        carried_action_items=("carry",),
        warnings=("w",),
    )
    return build_prep_html(_inject(prep))


def _perf_completion_html() -> str:
    from yeaboi.agent.state import OneOnOneRecord
    from yeaboi.performance.export import build_completion_html

    record = OneOnOneRecord(
        engineer="Ada",
        date="2026-07-12",
        email_subject="1:1 recap",
        email_summary="Hi Ada\nthanks",
        action_items=("do x",),
        highlights=("hl",),
        warnings=("w",),
    )
    return build_completion_html(_inject(record))


def _perf_review_html() -> str:
    from yeaboi.agent.state import SixMonthReview
    from yeaboi.performance.export import build_review_html

    review = SixMonthReview(
        engineer="Ada",
        period_start="2026-01-01",
        period_end="2026-06-30",
        overall="great",
        strengths=("s",),
        achievements=("a",),
        areas_for_improvement=("i",),
        goals=("g",),
        framework_used="GROW",
        warnings=("w",),
    )
    return build_review_html(_inject(review))


def _anonymize_html() -> str:
    from yeaboi.agent.state import AnonymizedOutput
    from yeaboi.anonymize.export import build_anonymized_html

    result = AnonymizedOutput(
        anonymized_text="# Title\n\nSome `code` and **bold** text\n\n- item one\n\n> quoted",
        source_mode="standup",
        warnings=("masked 3 names",),
        generated_at="2026-07-20",
    )
    return build_anonymized_html(_inject(result), title=XSS_PROBE)


def _plan_html() -> str:
    from tests._node_helpers import (
        make_dummy_analysis,
        make_sample_features,
        make_sample_sprints,
        make_sample_stories,
    )
    from yeaboi.agent.state import QuestionnaireState, Task, TaskLabel
    from yeaboi.html_exporter import build_export_html

    qs = QuestionnaireState(completed=True)
    qs.answers[1] = "answer one"

    tasks = [
        Task(
            id="T-1",
            story_id="US-F1-001",
            title="Build endpoint",
            description="POST /register",
            label=TaskLabel.CODE,
            test_plan="unit + integration",
            ai_prompt="Implement the endpoint",
        )
    ]
    graph_state = {
        "questionnaire": _inject(qs),
        "project_analysis": _inject(make_dummy_analysis()),
        "features": _inject(make_sample_features()),
        "stories": _inject(make_sample_stories()),
        "tasks": _inject(tasks),
        "sprints": _inject(make_sample_sprints()),
        "jira_epic_key": XSS_PROBE,
        "analysis_profile_id": XSS_PROBE,
        "team_size": 4,
        "velocity_per_sprint": 10,
        "net_velocity_per_sprint": 8,
        "sprint_length_weeks": 2,
    }
    return build_export_html(graph_state, stage="complete")


def _team_profile_examples() -> dict:
    """A blob exercising most optional branches of build_team_profile_html."""
    insight = {"title": "t", "detail": "d", "evidence": "e", "link": "https://x.example/1"}
    return {
        "analysis_depth": "deep",
        "narrative": {"executive_summary": "summary", "sections": {"velocity": "v", "team": "t"}},
        "insights": {"start": [insight], "stop": [], "keep": [], "try": []},
        "ai_adoption": {
            "enabled_features": ["ai_footprint", "code_health"],
            "activity_coverage": {"status": "complete", "completed": 30, "eligible": 30},
            "selected_users": ["Ava Lee"],
            "matched_identities": {"Ava Lee": "ava"},
            "coverage": ["STANDUP_GITHUB_REPO not set"],
            "samples": [
                {"tool": "claude", "title": "Fix login", "url": "https://x.example/c/1", "source": "github"},
                {"tool": "other_ai", "title": "Refactor", "key": "a1b2c3", "source": "local"},
            ],
            "insights": {"start": [insight], "stop": [], "keep": [], "try": []},
            "member_practices": {
                "min_sample": 5,
                "file_data": {"with_file_data": 9, "total": 12},
                "members": [
                    {
                        "member": "Ava",
                        "commits": 20,
                        "prs": 10,
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
                "team": None,
            },
            "repository_health": {"files_analysed": 4, "repositories_touched": 2, "findings": 3},
            "coverage_report": {"status": "complete", "completed": 4, "eligible": 4},
            "action_plan": [
                {
                    "priority": "high",
                    "title": "Add tests",
                    "detail": "cover auth",
                    "affected_scope": ["repo-a"],
                    "owner_role": "backend",
                    "effort": "S",
                    "link": "https://x.example/p/1",
                }
            ],
        },
        "doc_quality": {
            "coverage_report": {"status": "complete", "completed": 6, "eligible": 6},
            "samples": [
                {
                    "title": "Runbook",
                    "url": "https://x.example/d/1",
                    "platform": "notion",
                    "clarity": 80.0,
                    "usefulness": 70.0,
                }
            ],
            "insights": {"start": [insight], "stop": [], "keep": [], "try": []},
            "action_plan": [
                {
                    "priority": "medium",
                    "title": "Add owners",
                    "detail": "assign owners",
                    "affected_scope": ["space-a"],
                    "owner_role": "lead",
                    "effort": "M",
                }
            ],
        },
        "team_size": 3,
        "team_members": ["Ava", "Ben", "Cy"],
        "per_dev_velocity": 4.0,
        "sprint_details": [
            {
                "name": "Sprint 1",
                "points": 20,
                "planned": 10,
                "completed": 8,
                "rate": 80,
                "done": False,
                "has_shadow": True,
                "incomplete": [
                    {"issue_key": "P-1", "summary": "slipped story", "shadow": False, "points": 3},
                    {"issue_key": "P-2", "summary": "cloned story", "shadow": True},
                ],
            },
            {"name": "Sprint 2", "points": 22, "planned": 11, "completed": 11, "rate": 100, "done": True},
        ],
        "velocity_trend": {"trend": "improving", "slope": 1.2, "first_velocity": 18, "last_velocity": 22},
        "recurring_count": 4,
        "delivery_count": 9,
        "recurring": [{"issue_key": "P-7", "summary": "weekly deploy"}],
        "spillover_correlation": {
            "by_size": {"5": 40.0, "8": 60.0},
            "by_discipline": {"backend": 30.0},
            "by_task_count": {"0-2": 10.0},
        },
        "contributor_stats": [
            {
                "name": "Ava",
                "delivery_pts": 30,
                "recurring_pts": 4,
                "stories_completed": 12,
                "spill_rate": 8,
                "avg_cycle_time": 4.0,
                "sprints_active": 5,
                "top_discipline": "backend",
                "top_work_type": "feature/api",
                "per_sprint": 6.0,
            },
            {
                "name": "Ben",
                "delivery_pts": 10,
                "recurring_pts": 0,
                "stories_completed": 5,
                "spill_rate": 30,
                "avg_cycle_time": 9.0,
                "sprints_active": 5,
                "top_discipline": "frontend",
                "top_work_type": "",
                "per_sprint": 2.0,
            },
            {
                "name": "Cy",
                "delivery_pts": 8,
                "recurring_pts": 1,
                "stories_completed": 4,
                "spill_rate": 12,
                "avg_cycle_time": 6.0,
                "sprints_active": 4,
                "top_discipline": "qa",
                "top_work_type": "",
                "per_sprint": 1.6,
            },
        ],
        "shadow_spillover": [
            {
                "issue_key": "P-3",
                "issue_url": "https://x.example/P-3",
                "title": "auth",
                "from_sprint": "S1",
                "to_sprint": "S2",
            },
            {"issue_key": "P-4", "title": "billing", "from_sprint": "S1", "to_sprint": "S2"},
        ],
        "discipline_calibration": {
            "backend": [{"points": 3, "avg_cycle_days": 4.0, "variance": 1.0, "samples": 6, "spill_pct": 12.0}],
            "frontend": [{"points": 3, "avg_cycle_days": 5.0, "variance": 2.0, "samples": 5, "spill_pct": 30.0}],
        },
        "confidence_levels": {"5": "high"},
        "calibration_5pt": [
            {"issue_key": "P-5", "issue_url": "https://x.example/P-5", "summary": "typical five", "detail": "api work"}
        ],
        "task_decomposition": {
            "stories_with_tasks": 8,
            "total_stories": 20,
            "total_tasks": 30,
            "avg_tasks_per_story": 2.5,
            "task_completion_rate": 55.0,
            "type_distribution": {"code": 60.0, "qa": 40.0},
            "bottlenecks": [["qa", 40, 6]],
            "common_tasks": [["write tests", 4]],
            "task_assignees": {"Ava": 9},
        },
        "proposed_dod": {
            "summary": "DoD is emerging",
            "health": "moderate",
            "items": [
                {
                    "practice": "code review",
                    "status": "established",
                    "signals": "PRs linked",
                    "recommendation": "keep it up",
                },
                {
                    "practice": "testing",
                    "status": "emerging",
                    "signals": "some mentions",
                    "recommendation": "make explicit",
                },
            ],
            "ordering": ["review", "test"],
            "custom_steps": [{"title": "design sign-off", "pct": 40}],
        },
        "dod_testing": [{"issue_key": "P-6", "issue_url": "https://x.example/P-6", "summary": "tested story"}],
        "repositories": {
            "top_repos": [{"repo": "acme/api", "stories": 12, "pct": 55.0}],
            "repo_avg_cycle_time": {"acme/api": 6.0},
            "spillover_repos": [{"repo": "acme/api", "spill_rate": 45, "spills": 5}],
            "by_pts": {"3": ["acme/api", "acme/web"]},
        },
        "naming_conventions": {
            "title_prefixes": [["[BE]", 40]],
            "label_distribution": [["tech-debt", 30]],
            "stories_with_labels_pct": 60,
            "epic_naming_style": "verb-first",
            "epic_examples": ["Build billing"],
            "template_sections": [["Context", 12]],
        },
        "story_structure": {
            "subtask_ordering": ["design", "build", "test"],
            "skipped_types": [{"type": "spike", "present_pct": 5}],
            "avg_epic_completion": 70,
            "lingering_epics": [{"epic_title": "Old epic", "completed": 2, "total": 10, "rate": 20}],
            "epic_sprint_spread": [{"epic": "Wide epic", "stories": 8, "sprints": 5}],
        },
        "ac_patterns": {
            "stories_with_ac_pct": 60,
            "median_ac": 2,
            "specificity": {"label": "mostly precise", "precise_pct": 70},
            "themes": {"auth": 30},
            "theme_examples": {
                "auth": {"issue_key": "P-8", "issue_url": "https://x.example/P-8", "summary": "login story"}
            },
            "by_discipline": {"backend": {"avg_ac": 2.5}, "frontend": {"avg_ac": 1.5}},
            "spillover_correlation": {"low_ac_spill_pct": 30, "high_ac_spill_pct": 10, "low_ac_count": 6},
            "recommendation": "add ACs to frontend stories",
        },
        "point_descriptions": {"3": "a well-understood day or two"},
        "additional_patterns": {
            "estimation_bias": {
                "sample_size": 12,
                "accurate_pct": 50.0,
                "underestimated_pct": 30.0,
                "overestimated_pct": 20.0,
                "worst_overestimate_sizes": [8],
            },
            "seasonal": {"monthly_avg": {"Jan": 18.0}, "low_months": {"Dec": 9.0}, "high_months": {"Mar": 24.0}},
        },
        "workflow_style": {
            "workflow": ["To Do", "In Review", "Done"],
            "style": "columns-as-dod",
            "dod_columns": {"In Review": 90},
        },
        "scope_changes": {
            "totals": {
                "avg_committed_velocity": 20.0,
                "avg_delivered_velocity": 15.0,
                "added_mid_sprint": 4,
                "re_estimated": 4,
                "total_stories": 20,
            },
            "per_sprint": [{"name": "Sprint 1", "scope_churn": 0.5}, {"name": "Sprint 2", "scope_churn": 0.4}],
            "timelines": [],
            "carry_over_chains": [
                {"issue_key": "P-9", "sprints": ["S1", "S2", "S3"]},
                {"issue_key": "P-10", "sprints": ["S1", "S2", "S3"]},
                {"issue_key": "P-11", "sprints": ["S2", "S3", "S4"]},
            ],
        },
    }


def _team_profile() -> object:
    from yeaboi.team_profile import (
        AiAdoptionSignal,
        DocQualitySignal,
        DoDSignal,
        EpicPattern,
        SpilloverStats,
        StoryPointCalibration,
        StoryShapePattern,
        TeamProfile,
        WritingPatterns,
    )

    return TeamProfile(
        team_id="jira-P-1",
        source="jira",
        project_key="P",
        team_name="Core",
        sample_sprints=5,
        sample_stories=40,
        velocity_avg=20.0,
        velocity_stddev=9.0,
        point_calibrations=(
            StoryPointCalibration(
                point_value=5,
                avg_cycle_time_days=4.0,
                sample_count=6,
                common_patterns=("single endpoint",),
                typical_task_count=3.0,
                overshoot_pct=20.0,
            ),
            StoryPointCalibration(point_value=8, avg_cycle_time_days=70.0, sample_count=3),
        ),
        story_shapes=(StoryShapePattern(discipline="backend", avg_points=3.0, avg_ac_count=2.0, sample_count=9),),
        epic_pattern=EpicPattern(
            avg_stories_per_epic=6.0, avg_points_per_epic=20.0, typical_story_count_range=(4, 9), sample_count=3
        ),
        sprint_completion_rate=55.0,
        spillover=SpilloverStats(carried_over_pct=25.0),
        dod_signal=DoDSignal(
            common_checklist_items=("tests pass", "PR reviewed"),
            stories_with_pr_link_pct=15.0,
            stories_with_review_mention_pct=40.0,
            stories_with_testing_mention_pct=10.0,
            stories_with_deploy_mention_pct=20.0,
        ),
        writing_patterns=WritingPatterns(
            median_ac_count=2.0,
            median_task_count_per_story=3.0,
            subtask_label_distribution=(("code", 0.6),),
            common_personas=("admin", "shopper"),
            uses_given_when_then=True,
        ),
        ai_adoption=AiAdoptionSignal(
            scanned_commits=30,
            scanned_prs=10,
            ai_commits=6,
            ai_prs=2,
            footprint_pct=20.0,
            per_tool=(("claude", 5), ("other_ai", 3)),
            per_author=(("Ava", 5),),
            per_activity=(("code", 6),),
            per_source=(("github", 8),),
            repos_scanned=("acme/api", "acme/web"),
            sources_scanned=("github",),
        ),
        doc_quality=DocQualitySignal(
            pages_scanned=6,
            platforms_scanned=("notion",),
            avg_clarity=70.0,
            avg_usefulness=60.0,
            clear_pages=3,
            mixed_pages=2,
            unclear_pages=1,
            ai_marked_pages=1,
            flagged_pages=(("Stale page", "no owner"),),
        ),
    )


def _team_profile_html() -> str:
    from yeaboi.team_profile_exporter import build_team_profile_html

    profile = _inject(_team_profile())
    examples = _inject(_team_profile_examples())
    return build_team_profile_html(profile, examples=examples, sprint_names=["Sprint 1", "Sprint 2"])


_BUILDERS = {
    "plan": _plan_html,
    "retro": _retro_html,
    "poker": _poker_html,
    "standup": _standup_html,
    "reporting": _reporting_html,
    "roadmap": _roadmap_html,
    "performance-prep": _perf_prep_html,
    "performance-completion": _perf_completion_html,
    "performance-review": _perf_review_html,
    "anonymize": _anonymize_html,
    "team-profile": _team_profile_html,
}


@pytest.mark.parametrize("name", sorted(_BUILDERS))
def test_injected_strings_never_land_raw(name):
    html_out = _BUILDERS[name]()
    for marker in RAW_MARKERS:
        assert marker not in html_out, f"{name}: raw XSS probe leaked into exported HTML"

    # Which proof applies depends on how the page is built, and the page says
    # which. The distinction is load-bearing rather than tidy: on a React export
    # the <title> alone satisfies ESCAPED_MARKER, so keeping that assertion
    # would leave the *payload* — where every probed field actually lands —
    # unchecked, and a builder silently dropping a field would still pass. This
    # branch disappears when the last exporter migrates.
    if ISLAND_TAG in html_out:
        assert ISLAND_MARKER in html_out, f"{name}: probe never reached the boot payload"
    else:
        assert ESCAPED_MARKER in html_out, f"{name}: probe neither escaped nor rendered — fixture reaches no output"


class TestDictKeyRenderedFields:
    """Targeted probes for fields the generic injector cannot reach (dict keys
    rendered into HTML, e.g. discipline names used as mapping keys)."""

    def test_team_profile_spillover_discipline_keys_escaped(self):
        from yeaboi.team_profile_exporter import build_team_profile_html

        html_out = build_team_profile_html(
            _team_profile(),
            examples={
                "spillover_correlation": {"by_discipline": {XSS_PROBE: 30.0}, "by_task_count": {XSS_PROBE: 10.0}}
            },
        )
        for marker in RAW_MARKERS:
            assert marker not in html_out
        assert ESCAPED_MARKER in html_out

    def test_team_profile_ac_by_discipline_keys_escaped(self):
        from yeaboi.team_profile_exporter import build_team_profile_html

        html_out = build_team_profile_html(
            _team_profile(),
            examples={
                "ac_patterns": {
                    "stories_with_ac_pct": 60,
                    "median_ac": 2,
                    "specificity": {"label": "mixed", "precise_pct": 50},
                    "by_discipline": {XSS_PROBE: {"avg_ac": 2.0}, "backend": {"avg_ac": 1.0}},
                }
            },
        )
        for marker in RAW_MARKERS:
            assert marker not in html_out
        assert ESCAPED_MARKER in html_out


# ---------------------------------------------------------------------------
# URL scheme allowlist
# ---------------------------------------------------------------------------

# A scheme-shaped probe. The markup-shaped XSS_PROBE above cannot catch this
# class of bug: `javascript:` contains no character html.escape() rewrites, so
# it survives escaping intact and executes on click. React does not block it
# either (it only warns in development), so the allowlist is the only defense.
URL_PROBE = "javascript:alert(1)"

# Any of these appearing in output means a live click-to-execute link shipped.
UNSAFE_HREF_MARKERS = (
    'href="javascript:',
    "href='javascript:",
    "](javascript:",  # Markdown link — renders to <a href> downstream
)


def _poker_report_with_url(url: str):
    """A minimal poker report whose ticket URL is caller-controlled."""
    from yeaboi.agent.state import PokerReport, PokerTicketResult

    return PokerReport(
        date="2026-07-25",
        session_id="sess-1",
        project_name="Proj",
        source="jira",
        tickets=(PokerTicketResult(key="PROJ-1", url=url, summary="Add login", final_points=5.0, estimated=True),),
        participants=("Alex",),
    )


def _standup_report_with_url(url: str):
    """A minimal standup report whose evidence link is caller-controlled."""
    from yeaboi.agent.state import MemberUpdate, StandupReport

    return StandupReport(
        date="2026-07-10",
        session_id="demo",
        team_summary="steady progress",
        member_updates=(MemberUpdate(name="Alice", summary="login page", source="inferred", links=(("PROJ-1", url),)),),
    )


def _assert_no_live_javascript_url(out: str, label: str) -> None:
    lowered = out.lower()
    for marker in UNSAFE_HREF_MARKERS:
        assert marker not in lowered, f"{label}: javascript: URL reached an href"


class TestUrlSchemeAllowlist:
    """Every builder must refuse a `javascript:` URL from tracker-supplied data."""

    def test_poker_ticket_url(self):
        from yeaboi.poker.export import build_poker_html, build_poker_markdown

        report = _poker_report_with_url(URL_PROBE)
        _assert_no_live_javascript_url(build_poker_html(report), "poker html")
        _assert_no_live_javascript_url(build_poker_markdown(report), "poker md")

    def test_standup_ticket_url(self):
        from yeaboi.standup.export import build_standup_html

        _assert_no_live_javascript_url(build_standup_html(_standup_report_with_url(URL_PROBE)), "standup html")

    def test_anonymize_markdown_link(self):
        """The anonymize page carries Markdown, so the allowlist runs client-side.

        `](javascript:` is *expected* in this document now — it is the masked
        text, verbatim, inside a JSON island — so the Markdown marker cannot be
        the assertion here. It is not a live link until something turns it into
        an `href`, and the only thing that does is `RichText`, which routes
        every run through `safeUrl`. That half is proven in
        `frontend/src/export/reports/Anonymize.test.tsx`; this half proves the
        page ships no anchor at all.
        """
        from yeaboi.agent.state import AnonymizedOutput
        from yeaboi.anonymize.export import build_anonymized_html

        art = AnonymizedOutput(
            anonymized_text=f"See [the ticket]({URL_PROBE}) for details.",
            source_mode="retro",
            generated_at="2026-07-20",
        )
        out = build_anonymized_html(art).lower()
        assert 'href="javascript:' not in out and "href='javascript:" not in out
        assert "<a " not in out.split("<script>", 1)[0], "the document shell must contain no anchors"

    def test_team_profile_example_links(self):
        from yeaboi.team_profile_exporter import build_team_profile_html

        out = build_team_profile_html(
            _team_profile(),
            examples={"doc_samples": [{"title": "t", "platform": "notion", "url": URL_PROBE}]},
        )
        _assert_no_live_javascript_url(out, "team profile html")

    def test_markdown_convert_confluence(self):
        from yeaboi.markdown_convert import markdown_to_confluence_storage

        out = markdown_to_confluence_storage(f"See [the ticket]({URL_PROBE}).")
        _assert_no_live_javascript_url(out, "confluence storage")

    def test_safe_urls_still_render_as_links(self):
        """Guard against over-blocking: a real tracker URL must still link."""
        from yeaboi.poker.export import build_poker_html

        out = build_poker_html(_poker_report_with_url("https://jira.example.com/browse/ABC-1"))
        assert 'href="https://jira.example.com/browse/ABC-1"' in out.replace("'", '"')
