"""Convert the app's generated Markdown to Notion blocks / Confluence storage XHTML.

The input is always Markdown produced by our own ``build_*_markdown`` builders
(standup/retro/performance/reporting exports, team profiles, sprint plans), so
the supported constructs track what those builders emit: ``#``–``###``
headings, ``-`` bullets (with 2-space nesting), ``1.`` numbered lists,
``- [x]`` / ``- [ ]`` checkboxes, ``>`` blockquotes (with ``>   -`` nested
bullets), ``---`` rules, pipe tables, ``![alt](path)`` image lines,
``**bold**`` / ``_italic_`` / ``*italic*`` / `` `code` `` / ``[text](url)``
inline runs, trailing-two-space hard line breaks, and plain paragraphs.

Native-polish touches: a ``##``/``###`` heading containing "⚠" turns its
following bullet run into a Notion callout / Confluence warning panel, and
checkboxes become native Confluence task lists.

Pure functions with no SDK imports — the publish layer (export_targets.py)
turns the returned structures into API calls. Images are referenced by local
path; callers pass ``image_ids`` (Notion file-upload ids) / ``image_filenames``
(Confluence attachment names) mappings so the emitted structures reference the
uploaded copies. Unmapped images degrade to an italic placeholder.
"""

from __future__ import annotations

import html
import re

from yeaboi.html_theme import safe_url

# Notion caps a single rich_text element's content at 2 000 characters.
_NOTION_TEXT_LIMIT = 2_000

_TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
# Inline runs: **bold**, `code`, [text](url), _italic_, *italic* (non-greedy,
# no nesting — our builders never nest emphasis). The link alternative uses a
# negative lookbehind so a stray inline image ref isn't half-matched as a link.
_INLINE_RE = re.compile(r"(\*\*(.+?)\*\*|`([^`]+)`|(?<!!)\[([^\]]+)\]\(([^)\s]+)\)|_([^_]+)_|\*([^*]+)\*)")
# A standalone image line: ![alt](path). Paths may contain spaces (YEABOI_HOME
# can), so the target is "anything but a closing paren".
_IMAGE_LINE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)$")
_NUMBERED_RE = re.compile(r"^\d+[.)] ")


def split_title(md: str) -> tuple[str, str]:
    """Split off the first ``# `` heading as the document title.

    Returns ``(title, body_without_title)``; ``("", md)`` when the document
    doesn't start with an H1.
    """
    lines = md.lstrip().split("\n")
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip(), "\n".join(lines[1:]).lstrip("\n")
    return "", md


def md_table_cell(text: str) -> str:
    """Sanitize free text for use inside a pipe-table cell.

    Pipes would split the cell and newlines would break the row — used by the
    build_*_markdown builders when tabulating user/tracker/LLM content.
    """
    return " ".join(str(text).replace("|", "\\").split())


def md_label(text: str) -> str:
    """Neutralise link-label syntax in free text.

    A bracket left intact lets a tracker title, a monitor name or an incident
    name close the label it is sitting in and open a link of its own choosing.
    """
    return str(text).replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def extract_image_paths(md: str) -> list[str]:
    """Return the ordered, de-duplicated local paths of standalone image lines."""
    seen: list[str] = []
    for raw in md.split("\n"):
        m = _IMAGE_LINE_RE.match(raw.strip())
        if m and m.group(2) not in seen:
            seen.append(m.group(2))
    return seen


# ---------------------------------------------------------------------------
# Shared line-level parsing
# ---------------------------------------------------------------------------


def _iter_segments(text: str):
    """Yield ``(kind, content)`` inline segments: plain/bold/italic/code/link.

    Link segments carry a ``(text, url)`` tuple as content.
    """
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            yield "plain", text[pos : m.start()]
        if m.group(2) is not None:
            yield "bold", m.group(2)
        elif m.group(3) is not None:
            yield "code", m.group(3)
        elif m.group(4) is not None:
            yield "link", (m.group(4), m.group(5))
        elif m.group(6) is not None:
            yield "italic", m.group(6)
        elif m.group(7) is not None:
            yield "italic", m.group(7)
        pos = m.end()
    if pos < len(text):
        yield "plain", text[pos:]


def _split_table_row(line: str) -> list[str]:
    """Split a ``| a | b |`` table row into stripped cell strings."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _quote_line(stripped: str) -> str:
    """Content of a ``> `` line; ``>   - x`` nested bullets become ``• x``."""
    q = stripped[1:].strip()
    return "• " + q[2:] if q.startswith("- ") else q


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------


def _rich_text(text: str) -> list[dict]:
    """Markdown inline runs → Notion rich_text array (chunked at the API limit)."""
    out: list[dict] = []
    for kind, content in _iter_segments(text):
        link_url = ""
        if kind == "link":
            content, link_url = content
        if not content:
            continue
        annotations = {}
        if kind == "bold":
            annotations = {"bold": True}
        elif kind == "italic":
            annotations = {"italic": True}
        elif kind == "code":
            annotations = {"code": True}
        # Chunk long runs — Notion rejects rich_text content over 2 000 chars.
        for i in range(0, len(content), _NOTION_TEXT_LIMIT):
            chunk = content[i : i + _NOTION_TEXT_LIMIT]
            item: dict = {"type": "text", "text": {"content": chunk}}
            if link_url:
                item["text"]["link"] = {"url": link_url}
            if annotations:
                item["annotations"] = dict(annotations)
            out.append(item)
    return out


def _notion_block(block_type: str, text: str, **extra) -> dict:
    payload = {"rich_text": _rich_text(text), **extra}
    return {"object": "block", "type": block_type, block_type: payload}


def _notion_table(rows: list[list[str]]) -> dict:
    """Pipe-table rows → one Notion table block (consistent width, header row)."""
    width = max(len(r) for r in rows)
    children = []
    for row in rows:
        cells = row + [""] * (width - len(row))  # pad short rows; Notion requires equal widths
        children.append(
            {
                "object": "block",
                "type": "table_row",
                "table_row": {"cells": [_rich_text(c) for c in cells[:width]]},
            }
        )
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": children,
        },
    }


_LIST_TYPES = ("bulleted_list_item", "numbered_list_item", "to_do")


def markdown_to_notion_blocks(md: str, image_ids: dict[str, str] | None = None) -> list[dict]:
    """Convert generated Markdown to a list of Notion block dicts.

    ``image_ids`` maps a local image path (the target of an ``![alt](path)``
    line) to a Notion file-upload id — mapped images become native image
    blocks; unmapped ones degrade to an italic placeholder paragraph.
    """
    image_ids = image_ids or {}
    blocks: list[dict] = []
    paragraph: list[tuple[str, bool]] = []  # (text, ends-with-hard-break)
    table_rows: list[list[str]] = []
    quote_lines: list[str] = []
    notice_items: list[str] = []  # bullets inside a "⚠" section → one callout
    notice_mode = False

    def last_list_item() -> dict | None:
        if blocks and blocks[-1]["type"] in _LIST_TYPES:
            return blocks[-1]
        return None

    def place_list_item(item: dict, indent: int) -> None:
        # 2-space indent nests under the previous top-level list item (our
        # builders nest at most one level, within Notion's create-depth limit).
        if indent >= 2:
            parent = last_list_item()
            if parent is not None:
                parent[parent["type"]].setdefault("children", []).append(item)
                return
        blocks.append(item)

    def flush_paragraph() -> None:
        if paragraph:
            text = ""
            for i, (part, _) in enumerate(paragraph):
                if i:
                    # A trailing two-space hard break keeps its line break.
                    text += "\n" if paragraph[i - 1][1] else " "
                text += part
            blocks.append(_notion_block("paragraph", text))
            paragraph.clear()

    def flush_table() -> None:
        if table_rows:
            blocks.append(_notion_table(table_rows))
            table_rows.clear()

    def flush_quote() -> None:
        if quote_lines:
            blocks.append(_notion_block("quote", "\n".join(quote_lines)))
            quote_lines.clear()

    def flush_notices() -> None:
        if notice_items:
            blocks.append(
                {
                    "object": "block",
                    "type": "callout",
                    "callout": {
                        "rich_text": _rich_text("\n".join(notice_items)),
                        "icon": {"type": "emoji", "emoji": "⚠️"},
                        "color": "yellow_background",
                    },
                }
            )
            notice_items.clear()

    for raw in md.split("\n"):
        line = raw.rstrip("\n")
        hard_break = line.endswith("  ")
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            flush_quote()
            flush_notices()
            notice_mode = False
            if not _TABLE_SEPARATOR_RE.match(stripped):
                table_rows.append(_split_table_row(stripped))
            continue
        flush_table()

        if stripped.startswith(">"):
            flush_paragraph()
            flush_notices()
            notice_mode = False
            quote_lines.append(_quote_line(stripped))
            continue
        flush_quote()

        if not stripped:
            flush_paragraph()
            continue  # a blank line does not end a ⚠ Notices section

        if stripped.startswith(("### ", "## ", "# ")):
            flush_paragraph()
            flush_notices()
            level = len(stripped.split(" ", 1)[0])
            text = stripped[level + 1 :]
            blocks.append(_notion_block(f"heading_{level}", text))
            notice_mode = "⚠" in text
            continue

        img = _IMAGE_LINE_RE.match(stripped)
        if img:
            flush_paragraph()
            flush_notices()
            notice_mode = False
            alt, path = img.group(1), img.group(2)
            if path in image_ids:
                blocks.append(
                    {
                        "object": "block",
                        "type": "image",
                        "image": {
                            "type": "file_upload",
                            "file_upload": {"id": image_ids[path]},
                            "caption": _rich_text(alt) if alt else [],
                        },
                    }
                )
            else:
                blocks.append(_notion_block("paragraph", f"_[image: {alt or 'attachment'}]_"))
            continue

        if stripped.startswith(("- [x] ", "- [X] ", "- [ ] ")):
            flush_paragraph()
            checked = not stripped.startswith("- [ ] ")
            place_list_item(_notion_block("to_do", stripped[6:], checked=checked), indent)
            continue

        if stripped.startswith("- "):
            flush_paragraph()
            if notice_mode:
                notice_items.append(stripped[2:])
                continue
            place_list_item(_notion_block("bulleted_list_item", stripped[2:]), indent)
            continue

        num = _NUMBERED_RE.match(stripped)
        if num:
            flush_paragraph()
            place_list_item(_notion_block("numbered_list_item", stripped[num.end() :]), indent)
            continue

        if stripped == "---":
            flush_paragraph()
            flush_notices()
            notice_mode = False
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            continue

        # Indented continuation of a list item (e.g. the plan's "  **When** …"
        # acceptance-criteria lines) stays inside that item with a line break.
        parent = last_list_item()
        if indent >= 2 and parent is not None and not paragraph:
            payload = parent[parent["type"]]
            children = payload.get("children")
            if children and indent >= 4:
                target = children[-1][children[-1]["type"]]
            else:
                target = payload
            target["rich_text"].extend(_rich_text("\n" + stripped))
            continue

        if notice_mode:  # a plain paragraph ends the notices section
            flush_notices()
            notice_mode = False
        paragraph.append((stripped, hard_break))

    flush_paragraph()
    flush_quote()
    flush_table()
    flush_notices()
    return blocks


# ---------------------------------------------------------------------------
# Confluence (storage format XHTML)
# ---------------------------------------------------------------------------


def _inline_to_xhtml(text: str) -> str:
    """Markdown inline runs → escaped XHTML with <strong>/<em>/<code>/<a>."""
    parts: list[str] = []
    for kind, content in _iter_segments(text):
        if kind == "link":
            label, url = content
            # Confluence renders this storage XHTML as a live anchor, so the
            # scheme is allowlisted here too — escaping alone leaves
            # `javascript:` intact. Unsafe links degrade to plain text.
            safe = safe_url(url)
            parts.append(
                f'<a href="{html.escape(safe, quote=True)}">{html.escape(label)}</a>' if safe else html.escape(label)
            )
            continue
        escaped = html.escape(content)
        if kind == "bold":
            parts.append(f"<strong>{escaped}</strong>")
        elif kind == "italic":
            parts.append(f"<em>{escaped}</em>")
        elif kind == "code":
            parts.append(f"<code>{escaped}</code>")
        else:
            parts.append(escaped)
    return "".join(parts)


def _render_nested_list(items: list[tuple[int, str]], tag: str) -> str:
    """Render (depth, html) items as a nested <ul>/<ol> tree.

    Depth can only grow one level per item (clamped); our builders nest at
    most one level anyway.
    """
    out = [f"<{tag}>"]
    prev = 0
    first = True
    for depth, content in items:
        depth = 0 if first else min(depth, prev + 1)
        if first:
            out.append(f"<li>{content}")
            first = False
        elif depth > prev:
            out.append(f"<{tag}><li>{content}")
        else:
            out.append("</li>")
            for _ in range(prev - depth):
                out.append(f"</{tag}></li>")
            out.append(f"<li>{content}")
        prev = depth
    out.append("</li>")
    for _ in range(prev):
        out.append(f"</{tag}></li>")
    out.append(f"</{tag}>")
    return "".join(out)


def markdown_to_confluence_storage(md: str, image_filenames: dict[str, str] | None = None) -> str:
    """Convert generated Markdown to Confluence storage-format XHTML.

    ``image_filenames`` maps a local image path to the attachment filename the
    publish layer will upload — mapped images become ``<ac:image>`` macros;
    unmapped ones degrade to an italic placeholder.
    """
    image_filenames = image_filenames or {}
    out: list[str] = []
    paragraph: list[tuple[str, bool]] = []  # (xhtml, ends-with-hard-break)
    list_items: list[tuple[int, str]] = []  # (depth, xhtml)
    list_tag = "ul"
    task_items: list[tuple[bool, str]] = []  # (done, xhtml) → native task list
    table_rows: list[list[str]] = []
    quote_lines: list[str] = []
    notice_items: list[str] = []  # bullets inside a "⚠" section → warning panel
    notice_mode = False

    def flush_paragraph() -> None:
        if paragraph:
            text = ""
            for i, (part, _) in enumerate(paragraph):
                if i:
                    text += "<br />" if paragraph[i - 1][1] else " "
                text += part
            out.append(f"<p>{text}</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if list_items:
            out.append(_render_nested_list(list_items, list_tag))
            list_items.clear()
        list_tag = "ul"

    def flush_tasks() -> None:
        if task_items:
            tasks = "".join(
                f"<ac:task><ac:task-status>{'complete' if done else 'incomplete'}</ac:task-status>"
                f"<ac:task-body>{body}</ac:task-body></ac:task>"
                for done, body in task_items
            )
            out.append(f"<ac:task-list>{tasks}</ac:task-list>")
            task_items.clear()

    def flush_table() -> None:
        if table_rows:
            width = max(len(r) for r in table_rows)
            rows_html = []
            for idx, row in enumerate(table_rows):
                tag = "th" if idx == 0 else "td"
                cells = row + [""] * (width - len(row))
                rows_html.append(
                    "<tr>" + "".join(f"<{tag}>{_inline_to_xhtml(c)}</{tag}>" for c in cells[:width]) + "</tr>"
                )
            out.append("<table><tbody>" + "".join(rows_html) + "</tbody></table>")
            table_rows.clear()

    def flush_quote() -> None:
        if quote_lines:
            out.append(f"<blockquote><p>{'<br />'.join(quote_lines)}</p></blockquote>")
            quote_lines.clear()

    def flush_notices() -> None:
        if notice_items:
            body = "<ul>" + "".join(f"<li>{item}</li>" for item in notice_items) + "</ul>"
            out.append(
                '<ac:structured-macro ac:name="warning"><ac:rich-text-body>'
                + body
                + "</ac:rich-text-body></ac:structured-macro>"
            )
            notice_items.clear()

    def flush_blocks() -> None:
        flush_paragraph()
        flush_list()
        flush_tasks()

    for raw in md.split("\n"):
        line = raw.rstrip("\n")
        hard_break = line.endswith("  ")
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if stripped.startswith("|") and stripped.endswith("|"):
            flush_blocks()
            flush_quote()
            flush_notices()
            notice_mode = False
            if not _TABLE_SEPARATOR_RE.match(stripped):
                table_rows.append(_split_table_row(stripped))
            continue
        flush_table()

        if stripped.startswith(">"):
            flush_blocks()
            flush_notices()
            notice_mode = False
            quote_lines.append(_inline_to_xhtml(_quote_line(stripped)))
            continue
        flush_quote()

        if not stripped:
            flush_blocks()
            continue  # a blank line does not end a ⚠ Notices section

        if stripped.startswith(("### ", "## ", "# ")):
            flush_blocks()
            flush_notices()
            level = len(stripped.split(" ", 1)[0])
            text = stripped[level + 1 :]
            out.append(f"<h{level}>{_inline_to_xhtml(text)}</h{level}>")
            notice_mode = "⚠" in text
            continue

        img = _IMAGE_LINE_RE.match(stripped)
        if img:
            flush_blocks()
            flush_notices()
            notice_mode = False
            alt, path = img.group(1), img.group(2)
            if path in image_filenames:
                alt_attr = f' ac:alt="{html.escape(alt, quote=True)}"' if alt else ""
                filename = html.escape(image_filenames[path], quote=True)
                out.append(f'<ac:image{alt_attr}><ri:attachment ri:filename="{filename}" /></ac:image>')
            else:
                out.append(f"<p><em>[image: {html.escape(alt) or 'attachment'}]</em></p>")
            continue

        if stripped.startswith(("- [x] ", "- [X] ", "- [ ] ")):
            flush_paragraph()
            flush_list()
            task_items.append((not stripped.startswith("- [ ] "), _inline_to_xhtml(stripped[6:])))
            continue

        if stripped.startswith("- "):
            flush_paragraph()
            flush_tasks()
            if notice_mode:
                notice_items.append(_inline_to_xhtml(stripped[2:]))
                continue
            if list_tag != "ul":
                flush_list()
                list_tag = "ul"
            list_items.append((indent // 2, _inline_to_xhtml(stripped[2:])))
            continue

        num = _NUMBERED_RE.match(stripped)
        if num:
            flush_paragraph()
            flush_tasks()
            if list_tag != "ol":
                flush_list()
                list_tag = "ol"
            list_items.append((indent // 2, _inline_to_xhtml(stripped[num.end() :])))
            continue

        if stripped == "---":
            flush_blocks()
            flush_notices()
            notice_mode = False
            out.append("<hr />")
            continue

        # Indented continuation of a list/task item stays inside it.
        if indent >= 2 and not paragraph:
            if list_items:
                depth, content = list_items[-1]
                list_items[-1] = (depth, content + "<br />" + _inline_to_xhtml(stripped))
                continue
            if task_items:
                done, body = task_items[-1]
                task_items[-1] = (done, body + "<br />" + _inline_to_xhtml(stripped))
                continue

        flush_list()
        flush_tasks()
        if notice_mode:  # a plain paragraph ends the notices section
            flush_notices()
            notice_mode = False
        paragraph.append((_inline_to_xhtml(stripped), hard_break))

    flush_blocks()
    flush_quote()
    flush_table()
    flush_notices()
    return "".join(out)
