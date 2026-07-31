"""Export an anonymized document to Markdown and self-contained HTML.

Mirrors the other mode exporters (standup/export.py, reporting/export.py): the
masked, shareable copy is written under ``~/.yeaboi/exports/anonymize/<project>/`` as
both a Markdown file (the primary artifact — paste it into a README/post) and a
self-contained HTML page using the shared design system (``html_theme``).

Unlike the other exporters the input is already a Markdown *string* (a mode's masked
Export document), so there is nothing to lay out: the HTML page carries the document
verbatim and the bundle reads it (``frontend/src/export/markdown.ts``). That replaced
110 lines of Markdown→HTML here, and it means the two artifacts this module writes are
provably the same text rather than two renderings that could drift. It never emits the
raw sensitive originals — only the already-masked text is written.

# See docs: "Export Formats" — Markdown, HTML
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from yeaboi.agent.state import AnonymizedOutput

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    """Return a filesystem-safe slug for the export subdirectory / filename stem."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "output"


def build_anonymized_markdown(result: AnonymizedOutput, *, title: str = "") -> str:
    """Return the Markdown document for a masked output (a light header + the text)."""
    header = f"# {title}\n\n" if title else ""
    notices = ""
    if result.warnings:
        notices = "\n\n> ⚠ Notices\n" + "\n".join(f"> - {w}" for w in result.warnings)
    return f"{header}{result.anonymized_text}{notices}\n"


def build_anonymized_html(result: AnonymizedOutput, *, title: str = "", markdown_name: str = "") -> str:
    """Return a self-contained HTML page for the masked output.

    The document travels as Markdown and is drawn client-side. ``markdown_name``
    is the sibling ``.md`` file, named in the page's ``<noscript>`` note.
    """
    from yeaboi.html_theme import export_page

    stamp = result.generated_at or datetime.now().strftime("%Y-%m-%d")
    # The default accent rather than a mode-specific one: anonymize is a
    # post-processing step over another mode's output, and wearing that mode's
    # colour would claim the masked copy is the original.
    return export_page(
        mode="analysis",
        title=title or "Anonymized output",
        wordmark="masked",
        subtitle="Names, tickets and identifiers have been replaced with stable placeholders.",
        facts=[("SOURCE", result.source_mode or ""), ("MASKED", stamp)],
        report={
            "kind": "anonymize",
            "markdown": result.anonymized_text,
            "warnings": list(result.warnings or []),
        },
        footer=f"Anonymized with yeaboi.ai • {stamp}",
        markdown_name=markdown_name,
    )


def export_anonymized(result: AnonymizedOutput, *, title: str = "", project_name: str = "") -> dict[str, Path]:
    """Write the masked output as Markdown + HTML under the anonymize export dir.

    Returns ``{"markdown": Path, "html": Path}``. The subdirectory keys off the
    project name (falling back to the source mode) so a project's shareable copies
    group together, mirroring the other exporters' per-project layout.
    """
    from yeaboi.paths import get_anonymize_export_dir

    key = _slug(project_name or result.source_mode)
    out_dir = get_anonymize_export_dir(key)
    stem = f"{_slug(title or result.source_mode or 'output')}-anonymized"

    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    md_path.write_text(build_anonymized_markdown(result, title=title), encoding="utf-8")
    html_path.write_text(build_anonymized_html(result, title=title, markdown_name=md_path.name), encoding="utf-8")
    logger.info("anonymize export: wrote %s + .html", md_path)
    return {"markdown": md_path, "html": html_path}
