"""Roadmap intake — proactive planning from the team's quarterly roadmap.

Instead of describing a project by hand, the user points the Roadmap intake
card at wherever their quarterly roadmap lives (a Confluence page, a Notion
page, or a local .md/.txt/.rst/.pdf/.docx/.pptx file). One LLM call extracts
the concrete candidate projects, ranks them, and classifies each as a Small or
Large planning effort; picking one launches a planning session pre-seeded with
that project's description.

Public API is re-exported here; submodules lazy-import their optional/heavy
deps (LLM, atlassian/notion SDKs, python-docx/pptx) inside functions, so
importing this package is always cheap and safe — mirrors the standup / retro /
performance / reporting packages.

# See docs: "Roadmap Intake"
"""

from __future__ import annotations

from yeaboi.roadmap.export import build_roadmap_html, build_roadmap_markdown, export_roadmap
from yeaboi.roadmap.ingest import RoadmapSource, ingest_source
from yeaboi.roadmap.store import RoadmapStore

__all__ = [
    "RoadmapSource",
    "RoadmapStore",
    "build_roadmap_html",
    "build_roadmap_markdown",
    "export_roadmap",
    "ingest_source",
    "intake_mode_for",
    "run_roadmap_analysis",
]


def __getattr__(name: str):
    """Lazy-load the engine entry points to keep package import cheap.

    ``run_roadmap_analysis`` pulls in the LLM stack; deferring its import means
    ``import yeaboi.roadmap`` (done by sessions.py's v10 migration for the
    schema constant) never drags in langchain.
    """
    if name in ("run_roadmap_analysis", "intake_mode_for"):
        from yeaboi.roadmap import engine

        return getattr(engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
