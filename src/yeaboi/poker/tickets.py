"""Tracker facade for Poker mode — source detection, ticket fetch, write-back.

This module is deliberately SDK-free: all Jira / Azure DevOps API calls live in
``tools/jira.py`` and ``tools/azure_devops.py`` (lazy-imported here, so the
optional SDKs are only loaded when a source is actually used). Poker's board,
server, and TUI talk exclusively to this facade, which makes them unit-testable
by monkeypatching a handful of plain functions — the same seam the standup
collector uses.

Normalized ticket row (every source produces this shape):
    {source, key, summary, description, description_text,
     acceptance, acceptance_text, type,
     story_points: float | None, state, assignee, url}

``description``/``acceptance`` are the raw tracker payloads (Jira wiki-markup
strings, AzDO HTML); the ``*_text`` variants are what the UI displays — AzDO
HTML is stripped and Jira wiki-markup (including embedded ``{adf}`` JSON
blocks) is flattened to readable plain text. When a tracker has no dedicated
acceptance-criteria value, an AC-looking section is lifted out of the
description as a fallback. ``type`` is the raw tracker type name.
"""

import html
import json
import logging
import re

logger = logging.getLogger(__name__)

# Canonical source ids — the "jira"/"azdevops" spelling matches analysis mode
# (the dominant idiom); "demo" is the no-tracker source for dry runs.
SOURCE_JIRA = "jira"
SOURCE_AZDO = "azdevops"
SOURCE_DEMO = "demo"

SOURCE_LABELS = {SOURCE_JIRA: "Jira", SOURCE_AZDO: "Azure DevOps", SOURCE_DEMO: "Demo"}

# Canonical ticket-type categories for the setup toggle. Each source translates
# these to its own type names (tools/jira.py and tools/azure_devops.py own the
# translation tables); sub-tasks are NEVER a category — they're always excluded.
TICKET_TYPES = ("story", "bug", "task")
TICKET_TYPE_LABELS = {"story": "Stories", "bug": "Bugs", "task": "Tasks"}


def default_include_types(source: str) -> tuple[str, ...]:
    """Default type selection per source.

    AzDO "Task" is a child work item (the sub-task analog) — off by default.
    A Jira "Task" is an estimable peer of Story/Bug — on by default; the Jira
    Sub-task level is what's excluded unconditionally.
    """
    return ("story", "bug") if source == SOURCE_AZDO else ("story", "bug", "task")


def source_label(source: str) -> str:
    """Human name for a source id ("Jira", "Azure DevOps", "Demo")."""
    return SOURCE_LABELS.get(source, source or "?")


def available_sources() -> list[str]:
    """Which trackers are configured (creds present). Ordered jira-first — the
    same precedence as analysis mode's ``_available_sources`` so pickers are
    deterministic when both are configured."""
    available: list[str] = []
    try:
        from yeaboi.config import get_jira_base_url, get_jira_token

        if get_jira_base_url() and get_jira_token():
            available.append(SOURCE_JIRA)
    except Exception:
        pass
    try:
        from yeaboi.config import get_azure_devops_org_url, get_azure_devops_token

        if get_azure_devops_org_url() and get_azure_devops_token():
            available.append(SOURCE_AZDO)
    except Exception:
        pass
    return available


def _strip_html(text: str) -> str:
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


def _adf_text(node: object) -> str:
    """Recursively collect readable text from an ADF node (dict/list/other)."""
    if isinstance(node, list):
        return "".join(_adf_text(child) for child in node)
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
    inner = _adf_text(node.get("content") or [])
    if ntype == "listItem":
        return "- " + inner.strip() + "\n"
    if ntype in _ADF_BLOCKS:
        prefix = f"{title}\n" if title else ""
        return f"{prefix}{inner}\n"
    return f"{title}\n{inner}" if title else inner


def _jira_wiki_to_text(text: str) -> str:
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
        return "\n" + _adf_text(doc).strip() + "\n"

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


def _plain_text_to_azdo_html(text: str) -> str:
    """Convert plain-text edits into the HTML AzDO expects for System.Description.

    Escapes everything, so a description edit can never inject markup into the
    board — the trade-off (documented in the edit modal) is that saving
    replaces the ticket's rich formatting with plain text.
    """
    return "<div>" + html.escape(text).replace("\n", "<br>") + "</div>"


# Acceptance-criteria fallback: when the tracker has no dedicated AC field
# (or it's empty), teams often write ACs inside the description. These spot
# the common shapes; the section is COPIED into acceptance_text — the
# description itself is never spliced (lossless, and both may be shown).
_AC_HEADING_RE = re.compile(r"(?im)^\s*(?:acceptance\s+criteria|acceptance\s+tests?)\s*:?\s*$")
_AC_INLINE_RE = re.compile(r"(?im)^\s*acceptance\s+criteria\s*:\s*(\S.*)$")
_AC_ITEM_RE = re.compile(r"(?im)^\s*(?:[-*]\s*)?ac\s*#?\d+\s*[:.)\-]")
_AC_SECTION_END_RE = re.compile(r"^\s*[A-Z][\w /-]{2,40}:\s*$")  # a following "Heading:"-style line


def _extract_acceptance_section(text: str) -> str:
    """Best-effort lift of an AC-looking section out of plain description text.

    First match wins: (a) an "Acceptance Criteria" heading line → the lines
    after it until the next heading-ish line; (b) inline "Acceptance
    criteria: …" → the remainder plus immediately following bullet lines;
    (c) two or more "AC 1:" / "- AC #2." style lines → that block.
    Returns "" when nothing AC-shaped is found.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _AC_HEADING_RE.match(line):
            block: list[str] = []
            for nxt in lines[i + 1 :]:
                if _AC_HEADING_RE.match(nxt) or _AC_SECTION_END_RE.match(nxt):
                    break
                block.append(nxt)
            return "\n".join(block).strip()
    m = _AC_INLINE_RE.search(text)
    if m:
        idx = text[: m.start()].count("\n")
        block = [m.group(1)]
        for nxt in lines[idx + 1 :]:
            if not re.match(r"^\s*(?:[-*]|\d+[.)]|ac\s*#?\d+)", nxt, re.IGNORECASE):
                break
            block.append(nxt)
        return "\n".join(block).strip()
    item_idx = [i for i, line in enumerate(lines) if _AC_ITEM_RE.match(line)]
    if len(item_idx) >= 2:
        return "\n".join(lines[item_idx[0] : item_idx[-1] + 1]).strip()
    return ""


def _with_description_text(row: dict) -> dict:
    """Attach display/edit variants (description_text, acceptance_text) to a row."""
    raw = row.get("description") or ""
    raw_ac = row.get("acceptance") or ""
    # Odd tracker payloads (a rich-text AC field returning a dict/list) must
    # degrade to their string form, not raise and empty the whole fetch.
    if not isinstance(raw, str):
        raw = str(raw)
    if not isinstance(raw_ac, str):
        raw_ac = str(raw_ac)
    if row.get("source") == SOURCE_AZDO:
        row["description_text"] = _strip_html(raw)
        row["acceptance_text"] = _strip_html(raw_ac)
    elif row.get("source") == SOURCE_JIRA:
        # Jira (REST v2) descriptions are wiki-markup strings, often with
        # embedded {adf} JSON — flatten to readable text for display/editing.
        row["description_text"] = _jira_wiki_to_text(raw)
        row["acceptance_text"] = _jira_wiki_to_text(raw_ac)
    else:
        # Demo rows are authored as plain text — display verbatim.
        row["description_text"] = raw
        row["acceptance_text"] = raw_ac
    if not row["acceptance_text"]:
        row["acceptance_text"] = _extract_acceptance_section(row["description_text"])
    return row


def list_sprints(source: str) -> list[dict]:
    """Sprints/iterations for the picker: [{id, name, start_date, end_date, state}].

    Returns [] on any failure (logged) — the wizard shows a "none found"
    message and stays put rather than crashing.
    """
    logger.info("poker list_sprints: source=%r", source)
    try:
        if source == SOURCE_JIRA:
            from yeaboi.tools.jira import jira_list_sprints

            return jira_list_sprints()
        if source == SOURCE_AZDO:
            from yeaboi.tools.azure_devops import azdevops_list_sprints

            return azdevops_list_sprints()
    except Exception as e:
        logger.warning("poker list_sprints failed for %s: %s", source, e)
        return []
    return []


def fetch_tickets(
    source: str,
    *,
    sprint: dict | None = None,
    limit: int = 100,
    include_types: tuple[str, ...] | None = None,
) -> list[dict]:
    """Fetch normalized ticket rows from a sprint (dict from list_sprints) or,
    when sprint is None, the backlog. Returns [] on any failure (logged).

    include_types is a subset of TICKET_TYPES; None applies the per-source
    default (see default_include_types). Sub-tasks are excluded regardless.
    The demo source ignores the filter.
    """
    scope = sprint.get("name") if sprint else "backlog"
    if include_types is None:
        include_types = default_include_types(source)
    logger.info(
        "poker fetch_tickets: source=%r scope=%r limit=%d types=%s",
        source,
        scope,
        limit,
        ",".join(include_types),
    )
    try:
        rows: list[dict] = []
        if source == SOURCE_DEMO:
            rows = demo_tickets()
        elif source == SOURCE_JIRA:
            from yeaboi.tools.jira import jira_backlog_issues, jira_sprint_issues

            if sprint is not None:
                sprint_id = sprint.get("id")
                if sprint_id is None:
                    logger.warning("poker fetch_tickets: jira sprint %r has no id", sprint.get("name"))
                    return []
                rows = jira_sprint_issues(sprint_id, limit=limit, include_types=include_types)
            else:
                rows = jira_backlog_issues(limit=limit, include_types=include_types)
        elif source == SOURCE_AZDO:
            from yeaboi.tools.azure_devops import azdevops_backlog_issues, azdevops_sprint_issues

            if sprint is not None:
                iteration_id = str(sprint.get("id") or "")
                if not iteration_id:
                    logger.warning("poker fetch_tickets: azdevops iteration %r has no id", sprint.get("name"))
                    return []
                rows = azdevops_sprint_issues(iteration_id, limit=limit, include_types=include_types)
            else:
                rows = azdevops_backlog_issues(limit=limit, include_types=include_types)
        rows = [_with_description_text(row) for row in rows[:limit]]
        logger.info("poker fetch_tickets: %d ticket(s)", len(rows))
        return rows
    except Exception as e:
        logger.warning("poker fetch_tickets failed for %s: %s", source, e)
        return []


def update_ticket(
    source: str,
    ticket: dict,
    *,
    summary: str | None = None,
    description: str | None = None,
    story_points: float | None = None,
) -> tuple[bool, str]:
    """Push field edits to the real board. Returns (ok, human_error) — never raises.

    ``description`` is plain text from the edit UI; per-source conversion to the
    tracker's storage format happens here (AzDO gets escaped HTML, Jira gets the
    string verbatim). The demo source accepts everything as a no-op success.
    """
    key = ticket.get("key", "")
    logger.info(
        "poker update_ticket: source=%r key=%r summary=%s description=%s points=%r",
        source,
        key,
        summary is not None,
        description is not None,
        story_points,
    )
    try:
        if source == SOURCE_DEMO:
            return True, ""
        if source == SOURCE_JIRA:
            from yeaboi.tools.jira import jira_update_issue_fields

            return jira_update_issue_fields(key, summary=summary, description=description, story_points=story_points)
        if source == SOURCE_AZDO:
            from yeaboi.tools.azure_devops import azdevops_update_work_item_fields

            azdo_description = _plain_text_to_azdo_html(description) if description is not None else None
            return azdevops_update_work_item_fields(
                int(key), summary=summary, description=azdo_description, story_points=story_points
            )
        return False, f"Error: unknown ticket source {source!r}."
    except Exception as e:
        logger.warning("poker update_ticket failed for %s %s: %s", source, key, e)
        return False, f"Error: {e}"


def demo_tickets() -> list[dict]:
    """Canned tickets for --dry-run and no-tracker demo sessions.

    Same shape as real rows; update_ticket() treats the demo source as a no-op
    success so the full vote → reveal → finalize loop is exercisable offline.
    """
    rows = [
        {
            "key": "DEMO-1",
            "summary": "Add OAuth login with Google and GitHub",
            "description": (
                "Users can sign in with an existing Google or GitHub account.\n"
                "New accounts are provisioned on first login."
            ),
            "acceptance": (
                "AC1: Sign-in works with Google and GitHub accounts.\n"
                "AC2: A first-time login provisions an account automatically.\n"
                "AC3: A failed provider login shows a readable error."
            ),
            "story_points": None,
            "state": "To Do",
            "assignee": "Alex",
        },
        {
            "key": "DEMO-2",
            "summary": "Paginate the activity feed",
            "description": (
                "The activity feed currently loads everything; add cursor pagination with a 50-item page size."
            ),
            "story_points": 5.0,
            "state": "To Do",
            "assignee": "",
        },
        {
            "key": "DEMO-3",
            "summary": "Fix duplicate email notifications",
            "description": "Users receive two copies of every mention email when they watch the thread.",
            "story_points": None,
            "state": "To Do",
            "assignee": "Sam",
        },
        {
            "key": "DEMO-4",
            "summary": "Export dashboard as PDF",
            "description": "One-click PDF export of the metrics dashboard, matching the on-screen layout.",
            "story_points": None,
            "state": "To Do",
            "assignee": "",
        },
        {
            "key": "DEMO-5",
            "summary": "Migrate sessions table to composite index",
            "description": (
                "Query times on the sessions table degrade past 1M rows; "
                "add the (user_id, created_at) index and backfill."
            ),
            "story_points": 8.0,
            "state": "To Do",
            "assignee": "Priya",
        },
        {
            "key": "DEMO-6",
            "summary": "Dark mode for the settings screen",
            "description": "Settings is the last screen without dark-mode styles.",
            "story_points": None,
            "state": "To Do",
            "assignee": "",
        },
    ]
    for row in rows:
        row["source"] = SOURCE_DEMO
        row["url"] = ""
        row.setdefault("type", "Story")
        row.setdefault("acceptance", "")
    return [_with_description_text(row) for row in rows]
