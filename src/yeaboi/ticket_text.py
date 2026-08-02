"""Tracker long-form text → readable plain text.

Two trackers, two markup dialects, and every consumer wants the same thing: the
words. Jira (REST v2) returns wiki-markup strings, sometimes wrapping
modern-editor content in an ``{adf:…}`` JSON macro; Azure DevOps returns HTML.
This module owns both flatteners plus :func:`ticket_text`, which assembles a
ticket's description, acceptance criteria and definition of done into one
matcher-ready string.

It is a stdlib-only leaf — ``html``/``json``/``re`` and nothing from ``yeaboi``
— so it can be imported from ``tools/`` (which fetches) and ``poker/`` (which
displays) without either direction creating a cycle. Poker owned these
flatteners first and still re-exports them under their private names.
"""

import html
import json
import re

# Per-section clip, applied to the description, the acceptance criteria and the
# definition of done SEPARATELY. One joined budget would be wrong: DoD is
# conventionally written LAST, so a long description would evict exactly the
# text the standup's practice matcher exists to read.
MAX_SECTION_CHARS = 1200
# Joined hard cap. Belt and braces: it bounds the matcher's cost by this
# module's own contract rather than by whatever the collectors happen to send.
MAX_TICKET_TEXT_CHARS = 4000


def strip_html(text: str) -> str:
    """Strip HTML tags and collapse whitespace (AzDO descriptions are HTML).

    <br>/<p>/<div> boundaries become newlines first so paragraph structure
    survives for display and plain-text editing.
    """
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
    clean = re.sub(r"<[^>]+>", " ", text)
    clean = html.unescape(clean)
    clean = re.sub(r"[ \t]+", " ", clean)
    clean = re.sub(r" ?\n ?", "\n", clean)
    return re.sub(r"\n{3,}", "\n\n", clean).strip()


# Embedded ADF documents inside wiki-markup descriptions: Jira's REST v2 API
# renders modern-editor content as a {adf:...} <json> {adf} macro — raw JSON to
# a human. We parse it and keep only the readable text.
_ADF_BLOCK_RE = re.compile(r"\{adf[^}]*\}(.*?)\{adf\}", re.DOTALL)

# Wiki macros whose braces are pure formatting noise once flattened to text.
# "adf" is here as a leftover guard for an unmatched opening/closing tag.
_MACRO_RE = re.compile(r"\{(?:color|panel|noformat|code|quote|anchor|status|expand|adf)[^}\n]*\}", re.IGNORECASE)

# Block-level ADF node types: their text is followed by a line break so
# paragraph/heading structure survives flattening.
_ADF_BLOCKS = {"paragraph", "heading", "blockquote", "panel", "expand", "nestedExpand", "codeBlock", "tableRow"}


def adf_text(node: object) -> str:
    """Recursively collect readable text from an ADF node (dict/list/other)."""
    if isinstance(node, list):
        return "".join(adf_text(child) for child in node)
    if not isinstance(node, dict):
        return ""
    ntype = node.get("type", "")
    if ntype == "text":
        return str(node.get("text", ""))
    if ntype == "hardBreak":
        return "\n"
    if ntype == "mention":
        # Mentions carry an opaque account id; show the display text when the
        # payload has one, otherwise a neutral placeholder (never the id).
        return str((node.get("attrs") or {}).get("text") or "@user")
    title = str((node.get("attrs") or {}).get("title") or "")
    inner = adf_text(node.get("content") or [])
    if ntype == "listItem":
        return "- " + inner.strip() + "\n"
    if ntype in _ADF_BLOCKS:
        prefix = f"{title}\n" if title else ""
        return f"{prefix}{inner}\n"
    return f"{title}\n{inner}" if title else inner


def jira_wiki_to_text(text: str) -> str:
    """Flatten Jira wiki-markup (REST v2 description strings) to readable text.

    Handles the noise real Jira Cloud descriptions carry: embedded ``{adf}``
    JSON documents, ``{color}``/``{panel}``-style macros, ``[~accountid:…]``
    mentions, ``[text|url]`` links, ``h2.``/``bq.`` prefixes, ``*bold*``-style
    emphasis, forced ``\\\\`` line breaks, table pipes, and ``----`` rules.
    Best-effort: unknown constructs pass through rather than being dropped.
    """

    def _adf_repl(match: re.Match[str]) -> str:
        try:
            doc = json.loads(match.group(1).strip())
        except ValueError:
            # Unparseable (e.g. truncated) editor blob: salvage the readable
            # "text" values instead of showing raw JSON or losing everything.
            found = re.findall(r'"text"\s*:\s*("(?:[^"\\]|\\.)*")', match.group(1))
            salvaged = "\n".join(json.loads(value) for value in found).strip()
            return f"\n{salvaged}\n" if salvaged else "\n"
        return "\n" + adf_text(doc).strip() + "\n"

    text = _ADF_BLOCK_RE.sub(_adf_repl, text)
    text = _MACRO_RE.sub("", text)
    text = re.sub(r"\[~accountid:[^\]]+\]", "@user", text)  # never surface account ids
    text = re.sub(r"\[~([^\]]+)\]", r"@\1", text)
    text = re.sub(r"\[([^\]|]+)\|[^\]]*\]", r"\1", text)  # [text|url] → text
    text = re.sub(r"\[(https?://[^\]]+)\]", r"\1", text)  # [url] → url
    text = text.replace("\\\\", "\n")  # wiki forced line break
    text = re.sub(r"(?m)^h[1-6]\.\s*", "", text)
    text = re.sub(r"(?m)^bq\.\s*", "> ", text)
    text = re.sub(r"(?m)^[#*]+\s+", "- ", text)  # ordered/unordered list markers
    text = re.sub(r"(?m)^-{4,}\s*$", "", text)  # horizontal rules
    text = re.sub(r"\{\{(.+?)\}\}", r"\1", text)  # {{monospace}}
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)  # *bold*
    text = re.sub(r"\+([^+\n]+)\+", r"\1", text)  # +underline+
    text = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", text)  # _italic_ (not snake_case)
    text = re.sub(r"\?\?([^?\n]+)\?\?", r"\1", text)  # ??citation??
    text = re.sub(r"(?m)^\|+\s*", "", text)  # table row leading pipes
    text = re.sub(r"(?m)\s*\|+\s*$", "", text)  # table row trailing pipes
    text = re.sub(r"\s*\|\|?\s*", " | ", text)  # inner table cell separators
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def ticket_text(*sections: object, flatten) -> str:
    """Flatten and clip a ticket's long-form fields into one plain-text string.

    ``sections`` is (description, acceptance criteria, definition of done) in
    that order — pass "" for a tracker that has no such field. Each is clipped
    to ``MAX_SECTION_CHARS`` INDEPENDENTLY, so a 5000-character description can
    never push the DoD out of the result.

    Two deliberate softnesses, because this runs inside activity collection and
    a standup must not die over one odd ticket: a non-string payload (a
    checklist-typed acceptance field comes back as a list) is coerced with
    ``str`` rather than skipped, and a flattener that raises degrades to the raw
    text rather than propagating.
    """
    out: list[str] = []
    for raw in sections:
        if not raw:
            continue
        text = raw if isinstance(raw, str) else str(raw)
        try:
            text = flatten(text)
        except Exception:
            pass
        text = text.strip()
        if text:
            out.append(_clip(text, MAX_SECTION_CHARS))
    return _clip("\n\n".join(out), MAX_TICKET_TEXT_CHARS)
