"""Mode adapters that turn generated artifacts into immutable share documents."""

from __future__ import annotations

from yeaboi.sharing.server import ShareDocument


def _masked_document(anon, title: str, mode: str) -> ShareDocument:
    from yeaboi.anonymize.export import build_anonymized_html

    return ShareDocument(title=title, html=build_anonymized_html(anon, title=title), source_mode=mode)


def planning_document(graph_state: dict, *, stage: str = "complete", anon=None) -> ShareDocument:
    analysis = graph_state.get("project_analysis")
    name = getattr(analysis, "project_name", "") if analysis is not None else ""
    title = f"Sprint Plan — {name}" if name else "Sprint Plan"
    if anon is not None:
        return _masked_document(anon, title, "planning")
    from yeaboi.html_exporter import build_export_html

    return ShareDocument(title=title, html=build_export_html(graph_state, stage=stage), source_mode="planning")


def analysis_document(
    profile,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    ceremony=None,
    anon=None,
) -> ShareDocument:
    title = f"Team Profile — {profile.source}/{profile.project_key}"
    if anon is not None:
        return _masked_document(anon, title, "analysis")
    from yeaboi.team_profile_exporter import build_team_profile_html

    html = build_team_profile_html(
        profile,
        examples=examples,
        sprint_names=sprint_names,
        ceremony=ceremony,
    )
    return ShareDocument(title=title, html=html, source_mode="analysis")


def standup_document(report, *, anon=None, history=()) -> ShareDocument:
    """``history`` = StandupStore.get_history rows; feeds the confidence-trend chart."""
    title = f"Daily Standup — {report.date}"
    if anon is not None:
        return _masked_document(anon, title, "standup")
    from yeaboi.standup.export import build_standup_html

    return ShareDocument(title=title, html=build_standup_html(report, history=history), source_mode="standup")


def retro_document(report, *, anon=None, history=()) -> ShareDocument:
    """``history`` = RetroStore.get_history rows; feeds the card-volume trend chart."""
    title = f"Retro — {report.sprint_name or report.date}"
    if anon is not None:
        return _masked_document(anon, title, "retro")
    from yeaboi.retro.export import build_retro_html

    return ShareDocument(title=title, html=build_retro_html(report, history=history), source_mode="retro")


def performance_document(artifact, *, kind: str, anon=None) -> ShareDocument:
    from yeaboi.performance import export

    labels = {"prep": "1:1 Prep", "completion": "1:1 Summary", "review": "6-Month Review"}
    title = f"{labels[kind]} — {artifact.engineer}"
    if anon is not None:
        return _masked_document(anon, title, "performance")
    builders = {
        "prep": export.build_prep_html,
        "completion": export.build_completion_html,
        "review": export.build_review_html,
    }
    return ShareDocument(title=title, html=builders[kind](artifact), source_mode="performance")


def reporting_document(report, *, anon=None, history=()) -> ShareDocument:
    """``history`` = ReportingStore.get_history rows; feeds the volume-trend chart."""
    title = f"Delivery Report — {report.period_label}"
    if anon is not None:
        return _masked_document(anon, title, "reporting")
    from yeaboi.reporting.export import build_report_html

    return ShareDocument(title=title, html=build_report_html(report, history=history), source_mode="reporting")


def roadmap_document(analysis, *, anon=None) -> ShareDocument:
    title = f"Roadmap — {analysis.source_label or 'Analysis'}"
    if anon is not None:
        return _masked_document(anon, title, "roadmap")
    from yeaboi.roadmap.export import build_roadmap_html

    return ShareDocument(title=title, html=build_roadmap_html(analysis), source_mode="roadmap")
