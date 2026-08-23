"""Where a roadmap lives, and whether this machine can read it.

The three sources, the prompt each one asks for, whether its credentials are
configured, and how a pasted URL becomes a :class:`RoadmapSource` — the parsing
already lived in ``ingest.py``, but the offer and the local-file checks were
spelled inline in the terminal page. Both surfaces read them from here.

Deliberately ``setup.py`` and not ``engine.py``: the engine glob in the parity
test registers every public name in ``engine.py`` as a capability of its own.
"""

from __future__ import annotations

import logging
from pathlib import Path

from yeaboi.roadmap.ingest import RoadmapSource, parse_confluence_locator, parse_notion_locator

logger = logging.getLogger(__name__)

SOURCE_KINDS = ("confluence", "notion", "local")

SOURCE_LABELS = {
    "confluence": "Confluence page",
    "notion": "Notion page",
    "local": "Local file (.md .txt .rst .pdf .docx .pptx)",
}

SOURCE_PROMPTS = {
    "confluence": "Confluence page URL, ID, or title",
    "notion": "Notion page URL or ID",
    "local": "Roadmap file path (.md .txt .rst .pdf .docx .pptx)",
}

NO_PROJECTS_MESSAGE = "No projects to plan — Re-analyze or Change Source."


def source_options() -> list[dict]:
    """The three sources with their configured status.

    An unconfigured source stays selectable — the hint says what is missing
    rather than hiding the option, because the fix is one Settings field away.
    """
    from yeaboi.config import get_confluence_base_url, get_notion_token

    configured = {
        "confluence": bool(get_confluence_base_url()),
        "notion": bool(get_notion_token()),
        "local": True,
    }
    hints = {
        "confluence": (
            "Read a page by URL, ID, or title"
            if configured["confluence"]
            else "Not configured — set CONFLUENCE_* (or JIRA_*) in .env"
        ),
        "notion": ("Read a page by URL or ID" if configured["notion"] else "Not configured — set NOTION_TOKEN in .env"),
        "local": "Read a roadmap document from disk",
    }
    return [
        {
            "key": kind,
            "label": SOURCE_LABELS[kind],
            "hint": hints[kind],
            "configured": configured[kind],
            "prompt": SOURCE_PROMPTS[kind],
        }
        for kind in SOURCE_KINDS
    ]


def resolve_source(kind: str, locator: str) -> tuple[RoadmapSource | None, str]:
    """``(source, problem)`` for a typed locator. Never raises, never reads the file.

    A local path is checked for existence here so a typo is a status line rather
    than an exception mid-analysis; the caller still owes the sandbox its
    consent pre-flight before the engine opens the file.
    """
    raw = (locator or "").strip()
    if kind not in SOURCE_KINDS:
        return None, f"Unknown roadmap source {kind!r}."
    if not raw:
        return None, "Enter where the roadmap lives."
    if kind == "local":
        path = Path(raw).expanduser()
        if not path.exists() or not path.is_file():
            return None, f"File not found: {path}"
        return RoadmapSource(source_type="local", locator=str(path), label=path.name), ""
    if kind == "confluence":
        return RoadmapSource(source_type="confluence", locator=parse_confluence_locator(raw), label=raw), ""
    return RoadmapSource(source_type="notion", locator=parse_notion_locator(raw), label=raw), ""


def project_choice(analysis, index: int) -> tuple[str, str] | None:
    """``(intake_mode, description)`` for the picked project — what Plan This hands on.

    ``None`` when the analysis produced nothing to plan.
    """
    from yeaboi.roadmap.engine import intake_mode_for

    projects = tuple(getattr(analysis, "projects", ()) or ())
    if not projects:
        return None
    project = projects[max(0, min(index, len(projects) - 1))]
    logger.info("roadmap setup: planning %r (size=%s)", project.name, project.size)
    return intake_mode_for(project), (project.description or project.name)
