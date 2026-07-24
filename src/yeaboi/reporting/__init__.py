"""Reporting mode — a business-friendly summary of delivered work.

A team lead picks a period (last sprint, or the last ~month / ~2 sprints) and the
mode gathers the tickets the team actually completed from Jira / Azure DevOps, then
runs a single LLM "design" pass to write an executive narrative, group the work into
outcome themes, and pick section emojis. The result is exported as Markdown, a
self-contained HTML report, and a self-contained HTML *slide deck* for presenting to
the business.

Public API is re-exported here; submodules lazy-import their optional/heavy deps
(LLM, tracker SDKs) inside functions, so importing this package is always cheap and
safe — mirrors the standup / retro / performance packages.

# See docs: "Reporting Mode"
"""

from __future__ import annotations

from yeaboi.reporting.store import ReportingStore

__all__ = [
    "ReportingStore",
    "build_presentation_html",
    "export_report",
    "gather_delivered_work",
    "list_sprints",
    "quarter_bounds",
    "run_delivery_report",
]


def __getattr__(name: str):
    """Lazy-load engine/export entry points to keep package import cheap.

    ``run_delivery_report`` pulls in the LLM stack; deferring its import means
    ``import yeaboi.reporting`` (done by sessions.py's v9 migration for the
    schema constant) never drags in langchain.
    """
    if name == "run_delivery_report":
        from yeaboi.reporting.engine import run_delivery_report

        return run_delivery_report
    if name == "gather_delivered_work":
        from yeaboi.reporting.activity import gather_delivered_work

        return gather_delivered_work
    if name in ("list_sprints", "quarter_bounds"):
        from yeaboi.reporting import sprints

        return getattr(sprints, name)
    if name in ("export_report", "build_presentation_html"):
        from yeaboi.reporting import export, presentation

        return export.export_report if name == "export_report" else presentation.build_presentation_html
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
