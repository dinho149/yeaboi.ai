"""Export an anonymized document to Markdown and self-contained HTML.

Mirrors the other mode exporters (standup/export.py, reporting/export.py): the
masked, shareable copy is written under ``~/.yeaboi/exports/anonymize/<project>/`` as
both a Markdown file (the primary artifact — paste it into a README/post) and a
self-contained HTML page reusing the plan stylesheet (``html_exporter._CSS``).

Unlike the other exporters the input is already a Markdown *string* (a mode's masked
Export document), so this module carries a small, defensive Markdown→HTML renderer
rather than building HTML from a dataclass. It never emits the raw sensitive
originals — only the already-masked text is written.

# See docs: "Export Formats" — Markdown, HTML
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from html import escape as _e
from pathlib import Path

from yeaboi.agent.state import AnonymizedOutput

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    """Return a filesystem-safe slug for the export subdirectory / filename stem."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:40] or "output"


def _inline(text: str) -> str:
    """Render inline Markdown (already HTML-escaped) — code, bold, italic, links."""
    # `code` first so ** / * inside it stay literal.
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', text)
    return text


def _md_to_html(md: str) -> str:
    """Minimal, defensive Markdown→HTML for the masked document.

    Handles the constructs the mode exporters emit — headings, unordered/ordered
    lists, fenced code blocks, blockquotes, horizontal rules, GFM pipe tables, and
    inline emphasis/code/links. Everything is HTML-escaped before rendering, so an
    unrecognised line degrades to a safe paragraph rather than injecting markup.
    """
    lines = (md or "").split("\n")
    out: list[str] = []
    i = 0
    in_list = False
    list_tag = "ul"

    def _close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append(f"</{list_tag}>")
            in_list = False

    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()

        # Fenced code block.
        if stripped.startswith("```"):
            _close_list()
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(_e(lines[i]))
                i += 1
            i += 1  # skip closing fence
            out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            continue

        # Blank line.
        if not stripped:
            _close_list()
            i += 1
            continue

        # Horizontal rule.
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            _close_list()
            out.append("<hr>")
            i += 1
            continue

        # Heading.
        h = re.match(r"(#{1,6})\s+(.*)", stripped)
        if h:
            _close_list()
            level = len(h.group(1))
            out.append(f"<h{level}>{_inline(_e(h.group(2).strip()))}</h{level}>")
            i += 1
            continue

        # GFM pipe table: a header row followed by a |---|---| separator.
        if (
            "|" in stripped
            and i + 1 < len(lines)
            and re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", lines[i + 1].strip())
            and "-" in lines[i + 1]
        ):
            _close_list()

            def _cells(row: str) -> list[str]:
                row = row.strip().strip("|")
                return [c.strip() for c in row.split("|")]

            header = _cells(stripped)
            i += 2  # skip header + separator
            body_rows: list[list[str]] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                body_rows.append(_cells(lines[i]))
                i += 1
            thead = "".join(f"<th>{_inline(_e(c))}</th>" for c in header)
            tbody = "".join("<tr>" + "".join(f"<td>{_inline(_e(c))}</td>" for c in row) + "</tr>" for row in body_rows)
            out.append(f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>")
            continue

        # Blockquote.
        if stripped.startswith(">"):
            _close_list()
            out.append(f"<blockquote>{_inline(_e(stripped.lstrip('> ').strip()))}</blockquote>")
            i += 1
            continue

        # List item (unordered or ordered).
        li = re.match(r"[-*+]\s+(.*)", stripped)
        oli = re.match(r"\d+[.)]\s+(.*)", stripped)
        if li or oli:
            want_tag = "ul" if li else "ol"
            if in_list and list_tag != want_tag:
                _close_list()
            if not in_list:
                list_tag = want_tag
                out.append(f"<{list_tag}>")
                in_list = True
            content = (li or oli).group(1)
            out.append(f"<li>{_inline(_e(content))}</li>")
            i += 1
            continue

        # Plain paragraph.
        _close_list()
        out.append(f"<p>{_inline(_e(stripped))}</p>")
        i += 1

    _close_list()
    return "\n".join(out)


def build_anonymized_markdown(result: AnonymizedOutput, *, title: str = "") -> str:
    """Return the Markdown document for a masked output (a light header + the text)."""
    header = f"# {title}\n\n" if title else ""
    notices = ""
    if result.warnings:
        notices = "\n\n> ⚠ Notices\n" + "\n".join(f"> - {w}" for w in result.warnings)
    return f"{header}{result.anonymized_text}{notices}\n"


def build_anonymized_html(result: AnonymizedOutput, *, title: str = "") -> str:
    """Return a self-contained HTML page for the masked output (reuses the plan CSS)."""
    from yeaboi.html_exporter import _CSS

    heading = f"<h1>{_e(title)}</h1>" if title else ""
    body = _md_to_html(result.anonymized_text)
    notices = ""
    if result.warnings:
        items = "".join(f"<li>{_e(w)}</li>" for w in result.warnings)
        notices = f"<h2>⚠ Notices</h2><ul>{items}</ul>"
    stamp = _e(result.generated_at or datetime.now().strftime("%Y-%m-%d"))
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_e(title or "Anonymized output")}</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="report">
{heading}
{body}
{notices}
</div>
<footer class="site-footer">
  Anonymized with yeaboi.ai &bull; {stamp}
</footer>
</body>
</html>"""


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
    html_path.write_text(build_anonymized_html(result, title=title), encoding="utf-8")
    logger.info("anonymize export: wrote %s + .html", md_path)
    return {"markdown": md_path, "html": html_path}
