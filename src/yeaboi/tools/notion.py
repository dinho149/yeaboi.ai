"""Notion tools — 3 read-only + 2 write (with user-confirmation guard in docstrings).

# See docs: "Tools" — tool types, @tool decorator, risk levels
#
# This module mirrors tools/confluence.py exactly — same 5-tool shape (search,
# read page, list a container, create, update) plus a recent-activity helper for
# Daily Standup. Read tools are low-risk (fetch page text for the LLM to reason
# about during project analysis); write tools (create_page, update_page) are
# high-risk and carry an explicit "only call after user confirms" docstring note.
#
# Why notion-client?
# The official notion-client package wraps the Notion REST API with typed methods,
# handles the required Notion-Version header, and mirrors how the rest of the
# project talks to external services through a per-integration SDK
# (atlassian-python-api for Confluence/Jira, PyGithub for GitHub). This keeps the
# auth + client-creation model consistent and avoids raw REST calls.
#
# Auth: Notion uses its OWN integration token (NOTION_TOKEN) — unlike Confluence,
# which reuses Jira's Atlassian credentials. The only optional extra is
# NOTION_ROOT_PAGE_ID (a page/database ID used as the default create parent and to
# scope the standup feed) — Notion has no "space key" concept.
#
# ONE real divergence from Confluence: Notion has no single storage-format "body"
# to overwrite, so notion_update_page APPENDS content blocks (and can rename the
# page) rather than replacing the whole body like confluence_update_page.
"""

import logging
from datetime import UTC, datetime, timedelta

from langchain_core.tools import tool
from notion_client import Client
from notion_client.errors import APIResponseError

from yeaboi.config import get_notion_root_page_id, get_notion_token

logger = logging.getLogger(__name__)

# Shown whenever the Notion token is missing — single source of truth for the message.
_MISSING_CONFIG_MSG = "Error: Notion is not configured. Ensure NOTION_TOKEN is set in your .env file."

# Truncate page content at this many characters to avoid flooding the LLM context.
# See docs: "Tools" — scoping tool output for LLM relevance
_MAX_CONTENT_CHARS = 8_000

# Block types whose rich_text we render as readable plain text. Notion pages are a
# tree of typed blocks; we pull text from the common textual ones and skip the rest.
_TEXT_BLOCK_TYPES = (
    "paragraph",
    "heading_1",
    "heading_2",
    "heading_3",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "quote",
    "callout",
    "toggle",
    "code",
)


def _make_notion_client() -> Client | None:
    """Return an authenticated Notion client, or None if the token is missing.

    Notion authenticates with a single Bearer integration token; the SDK sets the
    required Notion-Version header internally.
    """
    token = get_notion_token()
    if not token:
        logger.warning("Notion client not created — missing config")
        return None
    logger.debug("Creating Notion client")
    client = Client(auth=token)
    logger.debug("Notion client created successfully")
    return client


def _notion_error_msg(e: APIResponseError) -> str:
    """Return a user-friendly message for common Notion HTTP error codes."""
    # APIResponseError carries an HTTP status code on .status.
    code = getattr(e, "status", 0)
    if code == 401:
        return "Error: Notion authentication failed. Check NOTION_TOKEN in .env."
    if code == 403:
        return "Error: Notion permission denied. Share the page/database with your integration."
    if code == 404:
        return f"Error: Notion resource not found — verify the page or database ID. ({e})"
    if code == 429:
        return "Error: Notion rate limit reached. Wait a moment and try again."
    return f"Error: Notion API error {code}: {e}"


def _rich_text_to_plain(rich_text: list) -> str:
    """Join a Notion rich_text array into a plain string.

    Each rich_text item carries a ``plain_text`` field; concatenating them yields
    the readable text of a block without styling markup.
    """
    if not isinstance(rich_text, list):
        return ""
    return "".join(rt.get("plain_text", "") for rt in rich_text if isinstance(rt, dict))


def _blocks_to_text(blocks: list) -> str:
    """Convert a list of Notion block dicts to LLM-readable plain text.

    Notion's analog of Confluence's _strip_html_tags: walk each block, pull the
    rich_text of the textual block types, and join them with newlines. Headings
    keep a blank line before them; list items get a leading bullet. Unknown/media
    blocks are skipped.
    """
    lines: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype not in _TEXT_BLOCK_TYPES:
            continue
        payload = block.get(btype, {}) if isinstance(block.get(btype), dict) else {}
        text = _rich_text_to_plain(payload.get("rich_text", []))
        if not text:
            continue
        if btype.startswith("heading"):
            lines.append("")
            lines.append(text)
        elif btype in ("bulleted_list_item", "numbered_list_item", "to_do"):
            lines.append(f"- {text}")
        else:
            lines.append(text)
    return "\n".join(lines).strip()


def _text_to_blocks(text: str) -> list:
    """Convert plain text to Notion paragraph blocks (the inverse of _blocks_to_text).

    Splits the text on double-newlines (paragraph boundaries) and wraps each
    paragraph in a paragraph block with a single rich_text run. This produces valid
    block children for pages.create / blocks.children.append without an external
    conversion step. Analogous to Confluence's _text_to_storage.
    """
    paragraphs = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": p}}]},
        }
        for p in paragraphs
    ]


def _page_title(page: dict) -> str:
    """Extract a page's title from its properties (title-type property varies by name)."""
    props = page.get("properties", {}) if isinstance(page, dict) else {}
    for prop in props.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            title = _rich_text_to_plain(prop.get("title", []))
            if title:
                return title
    return "Untitled"


@tool
def notion_search_pages(query: str, limit: int = 10) -> str:
    """Search Notion for pages by keyword or phrase.

    Use this before project analysis to discover architecture docs, ADRs, runbooks,
    and product specs that should inform the scrum plan. Searches every page the
    integration has been granted access to. Returns title, page ID, and URL for
    each result.
    """
    # See docs: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("notion_search_pages called: query=%r, limit=%d", query, limit)
    client = _make_notion_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    try:
        results = client.search(
            query=query,
            filter={"property": "object", "value": "page"},
            page_size=limit,
        )
        pages = results.get("results", []) if isinstance(results, dict) else []

        if not pages:
            return f"No Notion pages found for '{query}'."

        lines: list[str] = [f"Notion search results for '{query}':", ""]
        for page in pages:
            title = _page_title(page)
            page_id = page.get("id", "")
            url = page.get("url", "")
            lines.append(f"[{title}] (ID: {page_id})")
            if url:
                lines.append(f"  URL: {url}")
            lines.append("")

        logger.debug("notion_search_pages found %d results for %r", len(pages), query)
        lines.append(f"({len(pages)} results shown)")
        return "\n".join(lines)

    except APIResponseError as e:
        logger.error("Notion API error in search_pages: %s", e)
        return _notion_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in notion_search_pages: %s", e)
        return f"Error: {e}"


@tool
def notion_read_page(page_id: str) -> str:
    """Fetch and read a Notion page as plain text.

    Provide the page_id (from a Notion URL or from notion_search_pages results).
    Retrieves the page title plus its content blocks and flattens them to plain
    text for LLM context. Truncates at 8 000 characters with a note if the page is
    larger. Use this to read architecture docs, ADRs, runbooks, and product specs.
    """
    logger.debug("notion_read_page called: page_id=%r", page_id)
    client = _make_notion_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    if not page_id.strip():
        return "Error: Provide a page_id."

    try:
        page = client.pages.retrieve(page_id)
        title = _page_title(page)
        url = page.get("url", "")

        # Notion stores page body as a tree of child blocks — fetch the top level.
        children = client.blocks.children.list(page_id, page_size=100)
        blocks = children.get("results", []) if isinstance(children, dict) else []
        content = _blocks_to_text(blocks)

        truncated = False
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS]
            truncated = True

        logger.debug("notion_read_page fetched %r (%d chars)", title, len(content))
        header = f"=== {title} ===\n"
        if url:
            header += f"URL: {url}\n"
        header += "\n"
        suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
        return header + content + suffix

    except APIResponseError as e:
        logger.error("Notion API error in read_page: %s", e)
        return _notion_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in notion_read_page: %s", e)
        return f"Error: {e}"


@tool
def notion_read_database(database_id: str, limit: int = 25) -> str:
    """List entries in a Notion database to discover available documentation.

    Returns page titles and IDs for up to limit rows. Use this to discover what
    docs exist (architecture pages, ADRs, runbooks, product specs) before calling
    notion_read_page on specific ones. This is the discovery equivalent of browsing
    a Confluence space. Accepts either a database ID or a data-source ID (Notion's
    2025 API queries data sources, which this resolves from the database if needed).
    """
    logger.debug("notion_read_database called: database_id=%r, limit=%d", database_id, limit)
    client = _make_notion_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    if not database_id.strip():
        return "Error: Provide a database_id."

    try:
        # Notion's 2025 API splits a database into one or more "data sources"; you
        # query a data source, not the database. Try the given id as a data source
        # id directly; if that 400/404s, resolve the database's first data source.
        try:
            results = client.data_sources.query(data_source_id=database_id, page_size=limit)
        except APIResponseError as e:
            if getattr(e, "status", 0) not in (400, 404):
                raise
            db = client.databases.retrieve(database_id)
            sources = db.get("data_sources", []) if isinstance(db, dict) else []
            if not sources:
                return _notion_error_msg(e)
            results = client.data_sources.query(data_source_id=sources[0]["id"], page_size=limit)
        rows = results.get("results", []) if isinstance(results, dict) else []

        if not rows:
            return f"No entries found in Notion database '{database_id}'."

        lines: list[str] = [f"Entries in Notion database '{database_id}':", ""]
        for row in rows:
            title = _page_title(row)
            page_id = row.get("id", "")
            url = row.get("url", "")
            lines.append(f"- {title} (ID: {page_id})")
            if url:
                lines.append(f"  URL: {url}")

        logger.debug("notion_read_database listed %d entries in %s", len(rows), database_id)
        note = "; increase limit to see more" if len(rows) >= limit else ""
        lines.append("")
        lines.append(f"({len(rows)} entries shown{note})")
        return "\n".join(lines)

    except APIResponseError as e:
        logger.error("Notion API error in read_database: %s", e)
        return _notion_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in notion_read_database: %s", e)
        return f"Error: {e}"


@tool
def notion_create_page(title: str, body: str, parent_id: str = "") -> str:
    """Create a new Notion page with the generated sprint plan or project brief.

    Only call this after the user has explicitly confirmed they want to publish to Notion.
    body is plain text — it is converted into Notion paragraph blocks automatically.
    parent_id is the page (or database) to nest the new page under; when omitted it
    falls back to NOTION_ROOT_PAGE_ID. Notion requires a parent, so one of the two
    must be set. Returns the new page's title, ID, and URL on success.
    """
    logger.debug("notion_create_page called: title=%r, parent=%r", title, parent_id)
    client = _make_notion_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    parent = parent_id.strip() or (get_notion_root_page_id() or "")
    if not parent:
        return "Error: No parent_id provided and NOTION_ROOT_PAGE_ID is not set in .env."

    try:
        page = client.pages.create(
            parent={"page_id": parent},
            properties={"title": [{"type": "text", "text": {"content": title}}]},
            children=_text_to_blocks(body),
        )
        page_id = page.get("id", "")
        url = page.get("url", "")
        logger.debug("Created Notion page %s (ID: %s)", title, page_id)
        return f"Created Notion page: '{title}'\nID: {page_id}\nURL: {url}"

    except APIResponseError as e:
        logger.error("Notion API error in create_page: %s", e)
        return _notion_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in notion_create_page: %s", e)
        return f"Error: {e}"


@tool
def notion_update_page(page_id: str, body: str, title: str = "") -> str:
    """Update an existing Notion page by appending content (e.g. a new sprint plan).

    Only call this after the user has explicitly confirmed they want to update the page.
    NOTE: unlike Confluence (which replaces the page body), Notion has no single body
    to overwrite — so this APPENDS body as new paragraph blocks to the end of the page.
    If title is provided, the page is also renamed. body is plain text, converted to
    Notion blocks automatically. Returns the page's title, ID, and URL on success.
    """
    logger.debug("notion_update_page called: page_id=%r, title=%r", page_id, title)
    client = _make_notion_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    if not page_id.strip():
        return "Error: Provide a page_id."

    try:
        # Append the new content blocks to the page's children.
        if body.strip():
            client.blocks.children.append(page_id, children=_text_to_blocks(body))

        # Optionally rename the page via its title property.
        if title.strip():
            client.pages.update(
                page_id,
                properties={"title": [{"type": "text", "text": {"content": title}}]},
            )

        page = client.pages.retrieve(page_id)
        effective_title = _page_title(page)
        url = page.get("url", "")
        logger.debug("Updated Notion page %s (ID: %s)", effective_title, page_id)
        return f"Updated Notion page: '{effective_title}'\nID: {page_id}\nURL: {url}"

    except APIResponseError as e:
        logger.error("Notion API error in update_page: %s", e)
        return _notion_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in notion_update_page: %s", e)
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Recent-activity helper for Daily Standup mode
# ---------------------------------------------------------------------------
# Plain function (not @tool) the standup collector calls directly. Returns
# structured data and degrades gracefully to [] on error/missing config.
# See docs: "Daily Standup" — recent-activity collection


def notion_recent_pages(root_id: str = "", days: int = 1, since=None) -> list[dict]:
    """Return Notion pages edited since the window start.

    The window is ``since → now`` when ``since`` (tz-aware datetime) is given,
    else the last ``days`` days. Each item: {author, kind='page', title,
    timestamp, key(id)}. Returns [] when Notion is unconfigured or the search
    fails. The Notion search API has no server-side date filter, so we sort by
    last_edited_time descending and filter client-side. ``root_id`` is accepted
    for signature parity with the other sources but Notion search is
    workspace-wide (scoped by integration grants).
    """
    logger.info("notion_recent_pages: root=%r days=%d since=%s", root_id, days, since)
    client = _make_notion_client()
    if client is None:
        logger.warning("notion_recent_pages skipped — Notion not configured")
        return []

    cutoff = since.astimezone(UTC) if since is not None else datetime.now(UTC) - timedelta(days=int(days))
    # Best-effort cache of user-id → display name so we don't refetch per page.
    _user_names: dict[str, str] = {}

    def _resolve_author(user_id: str) -> str:
        if not user_id:
            return ""
        if user_id in _user_names:
            return _user_names[user_id]
        try:
            user = client.users.retrieve(user_id)
            name = user.get("name", "") if isinstance(user, dict) else ""
        except Exception:
            logger.debug("notion: user lookup failed for id=%s", user_id, exc_info=True)
            name = ""
        _user_names[user_id] = name
        return name

    try:
        results = client.search(
            filter={"property": "object", "value": "page"},
            sort={"direction": "descending", "timestamp": "last_edited_time"},
            page_size=50,
        )
        pages = results.get("results", []) if isinstance(results, dict) else []
        items: list[dict] = []
        for page in pages:
            edited = page.get("last_edited_time", "")
            # last_edited_time is ISO 8601 (e.g. 2026-07-14T10:20:00.000Z).
            try:
                edited_dt = datetime.fromisoformat(edited.replace("Z", "+00:00")) if edited else None
            except ValueError:
                edited_dt = None
            if edited_dt is not None and edited_dt < cutoff:
                # Results are newest-first, so once we pass the cutoff we can stop.
                break
            author_id = (
                page.get("last_edited_by", {}).get("id", "") if isinstance(page.get("last_edited_by"), dict) else ""
            )
            items.append(
                {
                    "author": _resolve_author(author_id),
                    "kind": "page",
                    "title": _page_title(page),
                    "timestamp": (edited or "")[:19],
                    "key": page.get("id", ""),
                    "url": page.get("url", "") or "",
                }
            )
        logger.info("notion_recent_pages: %d page(s) in last %d day(s)", len(items), days)
        return items
    except APIResponseError as e:
        code = getattr(e, "status", 0)
        if code in (401, 403):
            from yeaboi.standup.errors import StandupSourceError

            raise StandupSourceError("notion", "authentication failed — check NOTION_TOKEN") from e
        logger.warning("notion_recent_pages failed: %s", _notion_error_msg(e))
        return []
    except Exception as e:
        logger.warning("notion_recent_pages unexpected error: %s", e)
        return []


# ---------------------------------------------------------------------------
# Full-page reader for Roadmap intake
# ---------------------------------------------------------------------------
# Plain function (not @tool) the roadmap ingester calls directly. The @tool
# notion_read_page truncates at 8 000 chars and reads only top-level blocks;
# a quarterly roadmap needs a larger budget and often lives inside toggles or
# sections, so this helper takes an explicit max_chars and recurses one level
# into blocks that have children.


def notion_read_page_text(page_id: str, max_chars: int = 30_000) -> dict:
    """Read a full Notion page (one level deep) as plain text for roadmap ingestion.

    Returns {"title", "text", "truncated", "error"} — never raises; any failure
    lands in "error" with empty text so the caller can surface it as a warning.
    """
    logger.info("notion_read_page_text: page_id=%r max_chars=%d", page_id, max_chars)
    client = _make_notion_client()
    if client is None:
        return {"title": "", "text": "", "truncated": False, "error": _MISSING_CONFIG_MSG}
    if not page_id.strip():
        return {"title": "", "text": "", "truncated": False, "error": "Provide a Notion page ID."}

    try:
        page = client.pages.retrieve(page_id)
        title = _page_title(page)

        children = client.blocks.children.list(page_id, page_size=100)
        blocks = children.get("results", []) if isinstance(children, dict) else []

        # Roadmaps commonly nest their real content under toggle/section blocks.
        # Recurse exactly one level into blocks with children (bounded by
        # max_chars) — deep trees are cut off rather than walked exhaustively.
        parts: list[str] = [_blocks_to_text(blocks)]
        for block in blocks:
            if not isinstance(block, dict) or not block.get("has_children"):
                continue
            if sum(len(p) for p in parts) >= max_chars:
                break
            try:
                sub = client.blocks.children.list(block.get("id", ""), page_size=100)
                sub_blocks = sub.get("results", []) if isinstance(sub, dict) else []
                sub_text = _blocks_to_text(sub_blocks)
                if sub_text:
                    parts.append(sub_text)
            except Exception as e:  # one bad child block never sinks the read
                logger.debug("notion_read_page_text: child fetch failed for %r: %s", block.get("id"), e)

        text = "\n".join(p for p in parts if p).strip()
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        logger.info("notion_read_page_text: fetched %r (%d chars, truncated=%s)", title, len(text), truncated)
        return {"title": title, "text": text, "truncated": truncated, "error": ""}
    except APIResponseError as e:
        logger.error("notion_read_page_text API error: %s", e)
        return {"title": "", "text": "", "truncated": False, "error": _notion_error_msg(e)}
    except Exception as e:
        logger.error("notion_read_page_text unexpected error: %s", e)
        return {"title": "", "text": "", "truncated": False, "error": f"Notion read failed: {e}"}
