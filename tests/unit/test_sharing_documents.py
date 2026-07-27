"""Every generated artifact has a self-contained HTML sharing adapter."""

from yeaboi.agent.state import (
    AnonymizedOutput,
    DeliveryReport,
    OneOnOnePrep,
    ProjectAnalysis,
    RetroReport,
    RoadmapAnalysis,
    StandupReport,
)
from yeaboi.sharing.documents import (
    analysis_document,
    performance_document,
    planning_document,
    reporting_document,
    retro_document,
    roadmap_document,
    standup_document,
)
from yeaboi.team_profile import TeamProfile


def _project_analysis():
    return ProjectAnalysis(
        project_name="Acme",
        project_description="Plan",
        project_type="greenfield",
        goals=(),
        end_users=(),
        target_state="Done",
        tech_stack=(),
        integrations=(),
        constraints=(),
        sprint_length_weeks=2,
        target_sprints=1,
        risks=(),
        out_of_scope=(),
        assumptions=(),
    )


def test_all_raw_mode_adapters_return_html():
    documents = [
        planning_document({"project_analysis": _project_analysis()}),
        analysis_document(TeamProfile(team_id="t", source="jira", project_key="ACME")),
        standup_document(StandupReport(date="2026-07-24")),
        retro_document(RetroReport(date="2026-07-24", sprint_name="Sprint 1")),
        performance_document(OneOnOnePrep(engineer="Ada"), kind="prep"),
        reporting_document(DeliveryReport(period_label="Last sprint")),
        roadmap_document(RoadmapAnalysis(source_label="Q3")),
    ]
    assert {d.source_mode for d in documents} == {
        "planning",
        "analysis",
        "standup",
        "retro",
        "performance",
        "reporting",
        "roadmap",
    }
    assert all(d.html.startswith("<!DOCTYPE html>") for d in documents)


def test_anonymized_adapter_uses_masked_output_only():
    anon = AnonymizedOutput(
        anonymized_text="# [PROJECT]\n\nSafe summary",
        replacements=(("Acme", "[PROJECT]"),),
        source_mode="standup",
    )
    document = standup_document(StandupReport(date="2026-07-24", team_summary="Acme secret"), anon=anon)
    assert "[PROJECT]" in document.html
    assert "Acme secret" not in document.html


def test_standup_document_history_feeds_trend_chart():
    history = [
        {"standup_date": "2026-07-24", "confidence_pct": 80, "status": "success"},
        {"standup_date": "2026-07-23", "confidence_pct": 60, "status": "success"},
    ]
    doc = standup_document(StandupReport(date="2026-07-24", confidence_pct=80), history=history)
    assert 'class="spark-wrap"' in doc.html
    # Without history the page renders unchanged (no trend chart).
    assert 'class="spark-wrap"' not in standup_document(StandupReport(date="2026-07-24")).html
