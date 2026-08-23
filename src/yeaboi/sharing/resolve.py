"""Load a stored artifact by reference, and turn it into the four things a
result-screen action needs: a title, its Markdown, a share document, and a file
export.

The TUI never needed this — its result screens already hold the artifact in
memory, so Export, Share Online and Anonymize all close over it. Every other
surface arrives with a *reference* instead (``kind`` plus the ids that address
one run), and has to read the artifact back before it can do anything with it.

One table, one row per kind. Adding a kind is an entry, which is the property
that matters: the alternative is each caller growing its own three-way branch
over the same stores.

Not every kind can do all four, and :func:`capabilities` says so rather than
leaving a caller to discover it by being refused. **Poker exports and nothing
else**: it has no share document in any surface, because the estimates go back
to the tracker rather than out as a page. Roadmap, a team profile and the
performance artifacts publish read-only shares, which is the same gap
``artifacts.engine.SHARED_KINDS`` reports — corrections with nowhere to be
written back to would be collected and dropped when the tunnel closed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Artifact kinds this module can read back. Every one of them exports.
RESOLVABLE_KINDS: tuple[str, ...] = (
    "standup",
    "retro",
    "analysis",
    "poker",
    "reporting",
    "performance",
    "roadmap",
)

#: Of those, the ones that can be published as a document at all. Poker is
#: absent by design — see the module docstring.
SHAREABLE_KINDS: tuple[str, ...] = ("standup", "retro", "analysis", "reporting", "performance", "roadmap")

#: Of the shareable ones, those a reader may correct. Mirrors
#: ``artifacts.engine.SHARED_KINDS`` for the kinds we can resolve — a team
#: profile publishes read-only, because every number on it is computed from
#: tracker data and correcting one in place would make the page disagree with
#: the run that produced it.
EDITABLE_KINDS: tuple[str, ...] = ("standup", "retro", "reporting")


def capabilities() -> list[dict]:
    """What each kind can do, so no surface offers an action that will refuse."""
    return [
        {
            "kind": kind,
            "export": True,
            "share": kind in SHAREABLE_KINDS,
            # Masking runs over the Markdown, so anything with Markdown can be
            # masked — which is every kind that can be exported.
            "anonymize": True,
            "edit": kind in EDITABLE_KINDS,
        }
        for kind in RESOLVABLE_KINDS
    ]


@dataclass(frozen=True)
class Resolved:
    """One stored artifact, addressed and ready to act on."""

    kind: str
    artifact: Any
    title: str
    project_name: str
    #: The run the correction log anchors to — the *generated* row, never a
    #: corrected one. Zero for kinds with no run table (a team profile).
    run_id: int = 0
    session_id: str = ""
    #: Prior runs, for the trend charts an export and a share both draw.
    history: tuple = ()
    #: Side data a document builder needs and the artifact does not carry.
    extras: dict | None = None

    @property
    def editable(self) -> bool:
        return self.kind in EDITABLE_KINDS and bool(self.run_id)


def _db(db_path: Path | None) -> Path:
    from yeaboi.paths import get_db_path

    return db_path or get_db_path()


def _sub_kind(resolved: Resolved) -> str:
    """Which of the three performance artifacts this is: prep, completion or review."""
    return (resolved.extras or {}).get("artifact_kind", "prep")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_standup(session_id: str, run_id: int, db_path: Path) -> Resolved | None:
    from yeaboi.standup.store import StandupStore

    with StandupStore(db_path) as store:
        base = store.get_base_run(session_id=session_id, run_id=run_id)
        if base is None:
            return None
        base_id, report = base
        history = tuple(store.get_history(report.session_id or session_id, limit=30))
    return Resolved(
        kind="standup",
        artifact=report,
        title=f"Daily Standup — {report.date}",
        project_name=getattr(report, "project_name", "") or "",
        run_id=base_id,
        session_id=report.session_id or session_id,
        history=history,
    )


def _load_retro(session_id: str, run_id: int, db_path: Path) -> Resolved | None:
    from yeaboi.retro.store import RetroStore

    with RetroStore(db_path) as store:
        base = store.get_base_run(session_id=session_id, run_id=run_id)
        if base is None:
            return None
        base_id, report = base
        history = tuple(store.get_history(report.session_id or session_id, limit=30))
    return Resolved(
        kind="retro",
        artifact=report,
        title=f"Retro — {report.sprint_name or report.date}",
        project_name=getattr(report, "project_name", "") or "",
        run_id=base_id,
        session_id=report.session_id or session_id,
        history=history,
    )


def _load_poker(session_id: str, run_id: int, db_path: Path) -> Resolved | None:
    """A poker session, for export only.

    No ``get_base_run``: corrections never reach a poker session, so there is no
    log to anchor and the newest row is simply the row.
    """
    from yeaboi.poker.export import _title
    from yeaboi.poker.store import PokerStore

    with PokerStore(db_path) as store:
        report = store.get_run_by_id(run_id) if run_id else store.get_latest_report(session_id)
    if report is None:
        return None
    return Resolved(
        kind="poker",
        artifact=report,
        title=_title(report),
        project_name=getattr(report, "project_name", "") or "",
        run_id=run_id,
        session_id=getattr(report, "session_id", "") or session_id,
    )


def _load_analysis(team_id: str, db_path: Path) -> Resolved | None:
    from yeaboi.team_profile import TeamProfileStore

    with TeamProfileStore(db_path) as store:
        profile, examples = store.load_with_examples(team_id)
    if profile is None:
        return None
    return Resolved(
        kind="analysis",
        artifact=profile,
        title=f"Team Profile — {profile.source}/{profile.project_key}",
        project_name=profile.project_key or "",
        session_id=team_id,
        extras={"examples": examples or {}},
    )


def _load_reporting(session_id: str, run_id: int, db_path: Path) -> Resolved | None:
    from yeaboi.reporting.export import _title
    from yeaboi.reporting.store import ReportingStore

    with ReportingStore(db_path) as store:
        base = store.get_base_run(session_id=session_id, run_id=run_id)
        if base is None:
            return None
        base_id, report = base
        history = tuple(store.get_history(session_id, limit=30))
    return Resolved(
        kind="reporting",
        artifact=report,
        title=_title(report),
        project_name=getattr(report, "project_name", "") or "",
        run_id=base_id,
        session_id=session_id,
        history=history,
    )


def _load_performance(engineer: str, db_path: Path) -> Resolved | None:
    """The engineer's most recent artifact, by the priority every surface uses.

    Review beats completion beats prep — usefulness order, not recency: a
    6-month review is what someone asks for by name, and it is written over the
    1:1s that came before it.
    """
    from yeaboi.performance.store import PerformanceStore

    if not engineer:
        return None
    with PerformanceStore(db_path) as store:
        review = store.get_latest_review(engineer)
        completions = store.get_recent_completions(engineer, limit=1)
        prep = store.get_latest_prep(engineer)
    if review is not None:
        artifact, sub_kind, label = review, "review", "6-Month Review"
    elif completions:
        artifact, sub_kind, label = completions[0], "completion", "1:1 Summary"
    elif prep is not None:
        artifact, sub_kind, label = prep, "prep", "1:1 Prep"
    else:
        return None
    return Resolved(
        kind="performance",
        artifact=artifact,
        title=f"{label} — {engineer}",
        project_name="",
        session_id=engineer,
        extras={"engineer": engineer, "artifact_kind": sub_kind},
    )


def _load_roadmap(roadmap_id: int, db_path: Path) -> Resolved | None:
    from yeaboi.roadmap.export import _title
    from yeaboi.roadmap.store import RoadmapStore

    with RoadmapStore(db_path) as store:
        analysis = None
        if roadmap_id:
            row = store.get_roadmap(roadmap_id)
            analysis = (row or {}).get("analysis")
        else:
            analysis = store.get_latest_analysis()
    if analysis is None:
        return None
    return Resolved(
        kind="roadmap",
        artifact=analysis,
        title=_title(analysis),
        project_name="",
        run_id=roadmap_id,
    )


def load(
    kind: str,
    *,
    session_id: str = "",
    run_id: int = 0,
    db_path: Path | None = None,
) -> Resolved | None:
    """Read one stored artifact back, or ``None`` when it is no longer there.

    ``session_id`` addresses a team profile by its team id, a performance
    artifact by its engineer, and a run by its session; with a ``run_id`` the
    run wins. Raises for a kind this module has no row for — an unknown kind is
    a caller bug, while a missing run is not.
    """
    if kind not in RESOLVABLE_KINDS:
        raise ValueError(f"{kind!r} cannot be resolved — one of {', '.join(RESOLVABLE_KINDS)}")
    path = _db(db_path)
    if not path.exists():
        return None
    if kind == "standup":
        return _load_standup(session_id, run_id, path)
    if kind == "retro":
        return _load_retro(session_id, run_id, path)
    if kind == "poker":
        return _load_poker(session_id, run_id, path)
    if kind == "reporting":
        return _load_reporting(session_id, run_id, path)
    if kind == "performance":
        return _load_performance(session_id, path)
    if kind == "roadmap":
        return _load_roadmap(run_id, path)
    return _load_analysis(session_id, path)


# ---------------------------------------------------------------------------
# What a resolved artifact can become
# ---------------------------------------------------------------------------


def markdown(resolved: Resolved) -> str:
    """The artifact as the Markdown every export and clipboard copy carries."""
    if resolved.kind == "standup":
        from yeaboi.standup.export import build_standup_markdown

        return build_standup_markdown(resolved.artifact)
    if resolved.kind == "retro":
        from yeaboi.retro.export import build_retro_markdown

        return build_retro_markdown(resolved.artifact)
    if resolved.kind == "poker":
        from yeaboi.poker.export import build_poker_markdown

        return build_poker_markdown(resolved.artifact)
    if resolved.kind == "reporting":
        from yeaboi.paths import get_reporting_export_dir
        from yeaboi.reporting.export import _slug, build_report_markdown

        # charts_dir gives the delivered-work chart an on-disk home so a
        # publish layer can upload it alongside the page.
        charts_dir = get_reporting_export_dir(_slug(resolved.project_name or "report"))
        return build_report_markdown(resolved.artifact, charts_dir=charts_dir)
    if resolved.kind == "performance":
        from yeaboi.performance import export as perf_export

        builders = {
            "prep": perf_export.build_prep_markdown,
            "completion": perf_export.build_completion_markdown,
            "review": perf_export.build_review_markdown,
        }
        return builders[_sub_kind(resolved)](resolved.artifact)
    if resolved.kind == "roadmap":
        from yeaboi.roadmap.export import build_roadmap_markdown

        return build_roadmap_markdown(resolved.artifact)
    from yeaboi.team_profile_exporter import build_team_profile_markdown

    return build_team_profile_markdown(resolved.artifact, examples=(resolved.extras or {}).get("examples"))


def document(resolved: Resolved, *, anon=None):
    """The artifact as a :class:`~yeaboi.sharing.server.ShareDocument`.

    Raises for a kind that has no share adapter — see :data:`SHAREABLE_KINDS`.
    """
    from yeaboi.sharing import documents

    if resolved.kind not in SHAREABLE_KINDS:
        raise ValueError(f"a {resolved.kind} has no share document — it exports instead")
    if resolved.kind == "standup":
        return documents.standup_document(resolved.artifact, anon=anon, history=resolved.history)
    if resolved.kind == "retro":
        return documents.retro_document(resolved.artifact, anon=anon, history=resolved.history)
    if resolved.kind == "reporting":
        return documents.reporting_document(resolved.artifact, anon=anon, history=resolved.history)
    if resolved.kind == "performance":
        return documents.performance_document(resolved.artifact, kind=_sub_kind(resolved), anon=anon)
    if resolved.kind == "roadmap":
        return documents.roadmap_document(resolved.artifact, anon=anon)
    return documents.analysis_document(resolved.artifact, examples=(resolved.extras or {}).get("examples"), anon=anon)


def export_files(resolved: Resolved) -> dict[str, Path]:
    """Write the artifact to disk as Markdown + HTML, returning both paths."""
    if resolved.kind == "standup":
        from yeaboi.standup.export import export_standup

        return export_standup(resolved.artifact, project_name=resolved.project_name, history=list(resolved.history))
    if resolved.kind == "retro":
        from yeaboi.retro.export import export_retro

        return export_retro(resolved.artifact, project_name=resolved.project_name, history=list(resolved.history))
    if resolved.kind == "poker":
        from yeaboi.poker.export import export_poker

        return export_poker(resolved.artifact, project_name=resolved.project_name)
    if resolved.kind == "reporting":
        from yeaboi.reporting.export import export_report
        from yeaboi.reporting.style import load_deck_style

        extras = resolved.extras or {}
        # The deck style customises only the presentation outputs; the caller
        # passes one when it has already answered the content-fit question.
        return export_report(
            resolved.artifact,
            project_name=resolved.project_name,
            theme=str(extras.get("theme") or "midnight"),
            history=list(resolved.history),
            style=extras.get("style") or load_deck_style(),
        )
    if resolved.kind == "performance":
        from yeaboi.performance.export import export_artifact

        return export_artifact(
            resolved.artifact,
            engineer=(resolved.extras or {}).get("engineer", ""),
            kind=_sub_kind(resolved),
        )
    if resolved.kind == "roadmap":
        from yeaboi.roadmap.export import export_roadmap

        return export_roadmap(resolved.artifact)
    from yeaboi.team_profile_exporter import export_team_profile_html, export_team_profile_md

    examples = (resolved.extras or {}).get("examples")
    # Markdown first, so the HTML page can name it: the page is drawn in the
    # browser, and the Markdown is what someone with scripting off gets.
    md_path = export_team_profile_md(resolved.artifact, examples=examples)
    html_path = export_team_profile_html(resolved.artifact, examples=examples, markdown_name=md_path.name)
    return {"markdown": md_path, "html": html_path}


def editable_session(resolved: Resolved, *, db_path: Path | None = None):
    """A correctable share for this artifact, or ``None`` when it is read-only.

    Read-only is the honest answer for a team profile and for a run with no id
    to anchor a correction log to — not an error, because the caller's next move
    is to open a read-only share either way.
    """
    if not resolved.editable:
        return None
    from yeaboi.artifacts.session import EditableSession

    return EditableSession(
        resolved.artifact,
        kind=resolved.kind,
        db_path=_db(db_path),
        run_id=resolved.run_id,
        session_id=resolved.session_id,
        history=resolved.history,
    )
