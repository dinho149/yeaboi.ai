"""Mode adapters that turn generated artifacts into immutable share documents.

Each adapter names the document twice, and the two names are not the same job.
``ShareDocument.title`` is what the TUI's share screen shows the host; it is
also, now, what the browser tab says, threaded through as ``document_title``.
The page's own ``<h1>`` keeps the exporter's heading — "Daily Standup" — because
a heading says what kind of document this is, while a tab has to tell it apart
from the four others the reader has open.
"""

from __future__ import annotations

from yeaboi.sharing.editable import EditableShare
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

    return ShareDocument(
        title=title, html=build_export_html(graph_state, stage=stage, document_title=title), source_mode="planning"
    )


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
        document_title=title,
    )
    return ShareDocument(title=title, html=html, source_mode="analysis")


def standup_document(report, *, anon=None, history=()) -> ShareDocument:
    """``history`` = StandupStore.get_history rows; feeds the confidence-trend chart."""
    title = f"Daily Standup — {report.date}"
    if anon is not None:
        return _masked_document(anon, title, "standup")
    from yeaboi.standup.export import build_standup_html

    return ShareDocument(
        title=title, html=build_standup_html(report, history=history, document_title=title), source_mode="standup"
    )


def retro_document(report, *, anon=None, history=()) -> ShareDocument:
    """``history`` = RetroStore.get_history rows; feeds the card-volume trend chart."""
    title = f"Retro — {report.sprint_name or report.date}"
    if anon is not None:
        return _masked_document(anon, title, "retro")
    from yeaboi.retro.export import build_retro_html

    return ShareDocument(
        title=title, html=build_retro_html(report, history=history, document_title=title), source_mode="retro"
    )


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
    return ShareDocument(title=title, html=builders[kind](artifact, document_title=title), source_mode="performance")


def reporting_document(report, *, anon=None, history=()) -> ShareDocument:
    """``history`` = ReportingStore.get_history rows; feeds the volume-trend chart."""
    title = f"Delivery Report — {report.period_label}"
    if anon is not None:
        return _masked_document(anon, title, "reporting")
    from yeaboi.reporting.export import build_report_html

    return ShareDocument(
        title=title, html=build_report_html(report, history=history, document_title=title), source_mode="reporting"
    )


def roadmap_document(analysis, *, anon=None) -> ShareDocument:
    title = f"Roadmap — {analysis.source_label or 'Analysis'}"
    if anon is not None:
        return _masked_document(anon, title, "roadmap")
    from yeaboi.roadmap.export import build_roadmap_html

    return ShareDocument(title=title, html=build_roadmap_html(analysis, document_title=title), source_mode="roadmap")


# ---------------------------------------------------------------------------
# Editable shares
# ---------------------------------------------------------------------------
#
# The read-only adapters above hand over finished HTML. An editable share cannot:
# its document is rebuilt from the corrected artifact on every request, so what
# crosses this boundary is a *payload builder* rather than a page.
#
# Each entry pairs an artifact kind with the same `<mode>_export_args` the file
# export uses. That is the point — a shared correction and the .html someone
# downloads afterwards are drawn from one builder, so they cannot disagree about
# what the document says.


def _args_builder(kind: str, *, history=()):
    """Return ``artifact -> <mode>_export_args(...)`` for one artifact kind."""

    def args_for(artifact):
        if kind == "standup":
            from yeaboi.standup.export import standup_export_args

            # `editable=True` is what adds the per-region {path, value} maps.
            # A file export never asks for them, which is why a downloaded
            # report stays byte-for-byte what it was.
            return standup_export_args(artifact, history=history, editable=True)
        if kind == "reporting":
            from yeaboi.reporting.export import reporting_export_args

            return reporting_export_args(artifact, history=history, editable=True)
        if kind == "retro":
            from yeaboi.retro.export import retro_export_args

            return retro_export_args(artifact, history=history, editable=True)
        if kind == "roadmap":
            from yeaboi.roadmap.export import roadmap_export_args

            return roadmap_export_args(artifact, editable=True)
        if kind.startswith("performance_"):
            from yeaboi.performance import export

            builders = {
                "performance_prep": export.prep_export_args,
                "performance_completion": export.completion_export_args,
                "performance_review": export.review_export_args,
            }
            return builders[kind](artifact, editable=True)
        raise ValueError(f"no payload builder for {kind!r}")

    return args_for


def editable_share(artifact, *, kind: str, ref: str = "", share_id: str = "", history=()) -> EditableShare:
    """Wrap an artifact as a correctable share, or raise for an uneditable kind."""
    import secrets

    from yeaboi.artifacts.registry import spec_for
    from yeaboi.sharing.editable import EditableDocument

    spec = spec_for(kind)
    if spec is None:
        raise ValueError(f"{kind!r} is not an editable artifact")
    document = EditableDocument(artifact, spec, kind=kind, ref=ref, share_id=share_id)
    return EditableShare(
        document=document,
        args=_args_builder(kind, history=history),
        title=spec.label,
        source_mode=spec.mode,
        # Per-share, so the hashed addresses in one document's log cannot be
        # matched against another's.
        salt=secrets.token_hex(16),
    )


def render_editable_page(share: EditableShare, pid: str = "") -> str:
    """Render the corrected document as a self-contained, editable page.

    The boot payload gains one key the file export never carries — ``editing`` —
    and that key is the whole switch: a document written to disk does not have
    it, so `main.tsx` never reaches the edit stack and the file stays inert.
    """
    from yeaboi.html_theme import export_page

    frame = share.snapshot(pid)
    # Through export_page, not render_page: one function builds every one of
    # these documents, so the shared page and the file someone downloads cannot
    # drift in their chrome, their <title> or their [data-mode].
    return export_page(
        **share.page_args(frame),
        # A served document has no sibling Markdown file, so `markdown_name`
        # would point a reader at something nobody wrote. This is what the
        # `noscript` override exists for.
        noscript=(
            "This document is drawn in the browser and can be edited by anyone holding this "
            "link. With JavaScript off it cannot be shown — ask whoever shared it for the "
            "exported file."
        ),
        document_title=share.title,
    )
