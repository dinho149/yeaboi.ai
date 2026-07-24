"""Confluence Cloud tools — 3 read-only + 2 write (with user-confirmation guard in docstrings).

# See README: "Tools" — tool types, @tool decorator, risk levels
#
# All read tools are low-risk — they fetch page content from the Confluence REST
# API and return it as plain text for the LLM to reason about during project
# analysis. Write tools (create_page, update_page) are high-risk and carry an
# explicit "only call after user confirms" note in their docstrings.
#
# Why atlassian-python-api?
# The atlassian-python-api package provides a Confluence class that wraps the
# REST API with typed methods, handles authentication, and integrates cleanly
# with the Jira auth pattern already used in this project (shared base_url,
# email, and API token). This keeps the auth model consistent and avoids
# writing raw REST calls.
#
# Auth: Confluence Cloud uses the same HTTP Basic Auth as Jira Cloud. When Jira is
# configured, its creds (JIRA_BASE_URL/EMAIL/API_TOKEN) are reused automatically.
# Confluence can also be set up standalone via CONFLUENCE_BASE_URL/EMAIL/API_TOKEN
# (see config.get_confluence_base_url — the CONFLUENCE_* vars win, else Jira's).
# CONFLUENCE_SPACE_KEY (the short space identifier, e.g. "MYSPACE") scopes searches.
"""

import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, wait

from atlassian import Confluence
from langchain_core.tools import tool
from requests.exceptions import HTTPError, Timeout

from yeaboi.config import (
    get_confluence_base_url,
    get_confluence_email,
    get_confluence_space_key,
    get_confluence_token,
)

logger = logging.getLogger(__name__)

# Shown whenever Confluence env vars are missing — single source of truth for the message.
# Confluence reuses the Jira Atlassian creds when present, but can also be configured
# standalone via the CONFLUENCE_* vars (see config.get_confluence_base_url).
_MISSING_CONFIG_MSG = (
    "Error: Confluence is not configured. Ensure CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, "
    "CONFLUENCE_API_TOKEN (or the equivalent JIRA_* creds), and CONFLUENCE_SPACE_KEY are "
    "set in your .env file."
)

# Truncate page content at this many characters to avoid flooding the LLM context.
# See README: "Tools" — scoping tool output for LLM relevance
_MAX_CONTENT_CHARS = 8_000


def _make_confluence_client() -> Confluence | None:
    """Return an authenticated Confluence client, or None if any required config is missing.

    Uses HTTP Basic Auth with the Atlassian account email and API token — the same
    credentials as Jira (both services share the Atlassian identity platform).
    cloud=True enables the Confluence Cloud REST API endpoint path (/wiki/rest/api/).
    """
    base_url, email, token = get_confluence_base_url(), get_confluence_email(), get_confluence_token()
    if not all([base_url, email, token]):
        logger.warning("Confluence client not created — missing config")
        return None
    logger.debug("Creating Confluence client for %s", base_url)
    client = Confluence(url=base_url, username=email, password=token, cloud=True)
    logger.debug("Confluence client created successfully")
    return client


def _confluence_error_msg(e: HTTPError) -> str:
    """Return a user-friendly message for common Confluence HTTP error codes."""
    # HTTPError carries the response object; extract status_code from it.
    code = getattr(getattr(e, "response", None), "status_code", 0)
    if code == 401:
        return "Error: Confluence authentication failed. Check JIRA_EMAIL and JIRA_API_TOKEN in .env."
    if code == 403:
        return "Error: Confluence permission denied. Ensure your API token has access to this space."
    if code == 404:
        return f"Error: Confluence resource not found — verify the page ID or space key. ({e})"
    if code == 429:
        return "Error: Confluence rate limit reached. Wait a moment and try again."
    return f"Error: Confluence API error {code}: {e}"


def _strip_html_tags(html: str) -> str:
    """Strip HTML/XML tags from Confluence storage format for LLM-readable plain text.

    Confluence pages are stored as XHTML ('storage format'). This function converts
    them to readable plain text by:
      1. Converting <br> and </p> to newlines for natural paragraph breaks.
      2. Removing all remaining tags.
      3. Expanding common HTML entities.
      4. Collapsing excess whitespace.
    """
    # Preserve paragraph and line breaks as newlines before stripping all tags.
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    # Remove all remaining HTML/XML tags.
    text = re.sub(r"<[^>]+>", " ", text)
    # Expand common HTML entities.
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    # Collapse multiple whitespace/newlines into a single space or newline.
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _text_to_storage(text: str) -> str:
    """Convert plain text to Confluence storage format (basic XHTML paragraphs).

    Splits the text on double-newlines (paragraph boundaries) and wraps each
    paragraph in <p> tags. This produces valid Confluence storage XHTML without
    requiring an external library or a conversion API call.
    """
    paragraphs = text.strip().split("\n\n")
    return "".join(f"<p>{p.strip()}</p>" for p in paragraphs if p.strip())


@tool
def confluence_search_docs(query: str, space_key: str = "", limit: int = 10) -> str:
    """Search Confluence pages by keyword or phrase.

    Use this before project analysis to discover architecture docs, ADRs, runbooks,
    and product specs that should inform the scrum plan. Falls back to
    CONFLUENCE_SPACE_KEY env var when space_key is not provided.
    Returns title, excerpt, page ID, and URL for each result.
    """
    # See README: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("confluence_search_docs called: query=%r, space=%r", query, space_key)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    key = space_key.strip() or (get_confluence_space_key() or "")
    space_filter = f' AND space = "{key}"' if key else ""

    try:
        # CQL (Confluence Query Language) — SQL-like syntax for searching content.
        # We match both title and full-text to surface relevant pages.
        cql = f'type = page AND (title ~ "{query}" OR text ~ "{query}"){space_filter}'
        results = conf.cql(cql, limit=limit)
        pages = results.get("results", []) if isinstance(results, dict) else []

        if not pages:
            scope = f" in space '{key}'" if key else ""
            return f"No Confluence pages found for '{query}'{scope}."

        base_url = (get_confluence_base_url() or "").rstrip("/")
        lines: list[str] = [f"Confluence search results for '{query}':", ""]

        for page in pages:
            title = page.get("title", "Untitled")
            page_id = page.get("id", "")
            excerpt = _strip_html_tags(page.get("excerpt", ""))[:200]
            # _links.webui is the canonical page path; fall back to /wiki/pages/{id}.
            web_ui = page.get("_links", {}).get("webui", f"/wiki/pages/{page_id}")
            url = f"{base_url}{web_ui}"
            lines.append(f"[{title}] (ID: {page_id})")
            if excerpt:
                lines.append(f"  {excerpt}")
            lines.append(f"  URL: {url}")
            lines.append("")

        logger.debug("confluence_search_docs found %d results for %r", len(pages), query)
        lines.append(f"({len(pages)} results shown)")
        return "\n".join(lines)

    except HTTPError as e:
        logger.error("Confluence API error in search_docs: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_search_docs: %s", e)
        return f"Error: {e}"


@tool
def confluence_read_page(page_id: str = "", page_title: str = "", space_key: str = "") -> str:
    """Fetch and read a Confluence page as plain text.

    Provide either page_id or page_title (not both). When using page_title,
    space_key is required (or falls back to CONFLUENCE_SPACE_KEY env var).
    Strips Confluence storage format to plain text for LLM context.
    Truncates at 8 000 characters with a note if the page is larger.
    Use this to read architecture docs, ADRs, runbooks, and product specs.
    """
    logger.debug("confluence_read_page called: page_id=%r, title=%r", page_id, page_title)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    if not page_id and not page_title:
        return "Error: Provide either page_id or page_title."

    try:
        if page_id:
            # get_page_by_id returns a dict with the full page including body,
            # or None if the page doesn't exist.
            page = conf.get_page_by_id(page_id, expand="body.storage")
        else:
            key = space_key.strip() or (get_confluence_space_key() or "")
            if not key:
                return "Error: space_key is required when using page_title. Set CONFLUENCE_SPACE_KEY in .env."
            page = conf.get_page_by_title(space=key, title=page_title, expand="body.storage")

        # atlassian-python-api returns None (or False in older versions) for not-found pages.
        if not page:
            ref = page_id or f"'{page_title}'"
            return f"Error: Confluence page {ref} not found."

        title = page.get("title", "Untitled")
        # body.storage.value is the raw XHTML storage format — strip to plain text.
        body_storage = page.get("body", {}).get("storage", {}).get("value", "")
        content = _strip_html_tags(body_storage)

        truncated = False
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS]
            truncated = True

        base_url = (get_confluence_base_url() or "").rstrip("/")
        pid = page.get("id", page_id)
        web_ui = page.get("_links", {}).get("webui", f"/wiki/pages/{pid}")
        url = f"{base_url}{web_ui}"

        logger.debug("confluence_read_page fetched %r (%d chars)", title, len(content))
        header = f"=== {title} ===\nURL: {url}\n\n"
        suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
        return header + content + suffix

    except HTTPError as e:
        logger.error("Confluence API error in read_page: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_read_page: %s", e)
        return f"Error: {e}"


@tool
def confluence_read_space(space_key: str = "", limit: int = 25) -> str:
    """List pages in a Confluence space to discover available documentation.

    Returns page titles and IDs for up to limit pages. Use this to discover what
    docs exist (architecture pages, ADRs, runbooks, product specs) before calling
    confluence_read_page on specific ones. Falls back to CONFLUENCE_SPACE_KEY env
    var when space_key is not provided.
    """
    logger.debug("confluence_read_space called: space=%r, limit=%d", space_key, limit)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    key = space_key.strip() or (get_confluence_space_key() or "")
    if not key:
        return "Error: No space key provided and CONFLUENCE_SPACE_KEY is not set in .env."

    try:
        # get_all_pages_from_space returns a list of page dicts (id, title, type, status).
        pages = conf.get_all_pages_from_space(space=key, limit=limit)

        if not pages:
            return f"No pages found in Confluence space '{key}'."

        base_url = (get_confluence_base_url() or "").rstrip("/")
        lines: list[str] = [f"Pages in Confluence space '{key}':", ""]

        for page in pages:
            title = page.get("title", "Untitled")
            page_id = page.get("id", "")
            web_ui = page.get("_links", {}).get("webui", f"/wiki/pages/{page_id}")
            url = f"{base_url}{web_ui}"
            lines.append(f"- {title} (ID: {page_id})")
            lines.append(f"  URL: {url}")

        logger.debug("confluence_read_space listed %d pages in space %s", len(pages), key)
        note = "; increase limit to see more" if len(pages) >= limit else ""
        lines.append("")
        lines.append(f"({len(pages)} pages shown{note})")
        return "\n".join(lines)

    except HTTPError as e:
        logger.error("Confluence API error in read_space: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_read_space: %s", e)
        return f"Error: {e}"


@tool
def confluence_create_page(
    title: str,
    body: str,
    space_key: str = "",
    parent_id: str = "",
) -> str:
    """Create a new Confluence page with the generated sprint plan or project brief.

    Only call this after the user has explicitly confirmed they want to publish to Confluence.
    Falls back to CONFLUENCE_SPACE_KEY env var when space_key is not provided.
    body accepts plain text (auto-converted to Confluence storage XHTML) or raw
    storage format XHTML (detected by a leading '<' character).
    parent_id optionally nests the new page under an existing page.
    Returns the new page's title, ID, and URL on success.
    """
    logger.debug("confluence_create_page called: title=%r, space=%r", title, space_key)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    key = space_key.strip() or (get_confluence_space_key() or "")
    if not key:
        return "Error: No space key provided and CONFLUENCE_SPACE_KEY is not set in .env."

    try:
        # Detect whether body is already storage XHTML (starts with '<') or plain text.
        # _text_to_storage wraps plain text paragraphs in <p> tags for Confluence.
        storage_body = body if body.strip().startswith("<") else _text_to_storage(body)

        page = conf.create_page(
            space=key,
            title=title,
            body=storage_body,
            parent_id=parent_id or None,
        )

        page_id = page.get("id", "")
        logger.debug("Created Confluence page %s (ID: %s)", title, page_id)
        base_url = (get_confluence_base_url() or "").rstrip("/")
        web_ui = page.get("_links", {}).get("webui", f"/wiki/pages/{page_id}")
        url = f"{base_url}{web_ui}"
        return f"Created Confluence page: '{title}'\nID: {page_id}\nURL: {url}"

    except HTTPError as e:
        logger.error("Confluence API error in create_page: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_create_page: %s", e)
        return f"Error: {e}"


@tool
def confluence_update_page(
    page_id: str,
    body: str,
    title: str = "",
    version_comment: str = "",
) -> str:
    """Update an existing Confluence page (e.g. append a new sprint plan to a running log).

    Only call this after the user has explicitly confirmed they want to update the page.
    body accepts plain text (auto-converted to Confluence storage XHTML) or raw
    storage format XHTML (detected by a leading '<' character).
    If title is omitted, the existing page title is preserved.
    version_comment appears in the page's version history — use it to describe the change.
    Returns the updated page's title, ID, and URL on success.
    """
    logger.debug("confluence_update_page called: page_id=%r, title=%r", page_id, title)
    conf = _make_confluence_client()
    if conf is None:
        return _MISSING_CONFIG_MSG

    try:
        # Fetch the existing page to (a) confirm it exists and (b) get its current
        # title when the caller hasn't provided a new one.
        existing = conf.get_page_by_id(page_id)
        if not existing:
            return f"Error: Confluence page '{page_id}' not found."

        effective_title = title.strip() or existing.get("title", "")
        storage_body = body if body.strip().startswith("<") else _text_to_storage(body)

        # atlassian-python-api's update_page handles version incrementing internally.
        conf.update_page(
            page_id=page_id,
            title=effective_title,
            body=storage_body,
            version_comment=version_comment or None,
        )

        logger.debug("Updated Confluence page %s (ID: %s)", effective_title, page_id)
        base_url = (get_confluence_base_url() or "").rstrip("/")
        web_ui = existing.get("_links", {}).get("webui", f"/wiki/pages/{page_id}")
        url = f"{base_url}{web_ui}"
        return f"Updated Confluence page: '{effective_title}'\nID: {page_id}\nURL: {url}"

    except HTTPError as e:
        logger.error("Confluence API error in update_page: %s", e)
        return _confluence_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in confluence_update_page: %s", e)
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Recent-activity helper for Daily Standup mode
# ---------------------------------------------------------------------------
# Plain function (not @tool) the standup collector calls directly. Returns
# structured data and degrades gracefully to [] on error/missing config.
# See README: "Daily Standup" — recent-activity collection


# The CQL search is the required source of latest-editor/creator activity.
# Version history is optional enrichment for earlier co-editors, so it gets a
# strict wall-clock budget and only the newest cache misses go to the network.
_STANDUP_COLLECTION_BUDGET_SECONDS = 8.0
_MAX_LIVE_VERSION_LOOKUPS = 5


def _iso_to_dt(ts: str):
    """Parse an ISO timestamp from the Confluence API; None when unparseable."""
    from datetime import UTC, datetime

    try:
        parsed = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _page_cutoff(days: int, since):
    from datetime import UTC, datetime, timedelta

    if since is not None:
        return since.astimezone(UTC) if since.tzinfo else since.replace(tzinfo=UTC)
    return datetime.now(UTC) - timedelta(days=int(days))


def _page_link(content: dict, page_id: str) -> str:
    """Browser URL for a page ("" when the base URL is unconfigured).

    Prefers the API's own webui link (correct space/pretty path); falls back to
    the id-based viewpage URL, which works on both Cloud and Server.
    """
    base = (get_confluence_base_url() or "").rstrip("/")
    if not base or not page_id:
        return ""
    links = content.get("_links", {}) if isinstance(content, dict) else {}
    webui = links.get("webui", "") if isinstance(links, dict) else ""
    if webui:
        return f"{base}/wiki{webui}" if not webui.startswith("/wiki") else f"{base}{webui}"
    return f"{base}/wiki/pages/viewpage.action?pageId={page_id}"


def _display_name(by) -> str:
    """Display name from a Confluence user dict — "" for app/automation accounts.

    Cloud marks bots with accountType == "app"; filtering here keeps them out of
    the activity feed and the standup team (callers treat "" as "no author").
    """
    if not isinstance(by, dict) or by.get("accountType", "") == "app":
        return ""
    return by.get("displayName", "") or ""


def _fetch_version_history(conf, page_id: str) -> list[dict]:
    """Fetch raw page versions so they can be cached independently of the date window."""
    data = conf.get(f"rest/api/content/{page_id}/version", params={"limit": 50})
    return data.get("results", []) if isinstance(data, dict) else []


def _version_editor_items(
    versions: list[dict],
    page_id: str,
    title: str,
    cutoff,
    exclude: set[str],
    url: str = "",
) -> list[dict]:
    """One item per distinct in-window editor beyond those already credited."""
    out: list[dict] = []
    seen = set(exclude)
    for version in versions:
        if not isinstance(version, dict):
            continue
        when = _iso_to_dt(version.get("when", ""))
        if when is None or when < cutoff:
            continue
        by = version.get("by", {}) if isinstance(version.get("by"), dict) else {}
        name = _display_name(by)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            {
                "author": name,
                "author_email": by.get("email", "") or "",
                "kind": "page",
                "title": f"edited '{title}'",
                "timestamp": (version.get("when", "") or "")[:19],
                "key": page_id,
                "url": url,
            }
        )
    return out


def confluence_recent_pages(
    space_key: str = "",
    days: int = 1,
    since=None,
    metadata_cache=None,
    *,
    enrichment_budget_seconds: float = _STANDUP_COLLECTION_BUDGET_SECONDS,
    on_partial=None,
) -> list[dict]:
    """Return Confluence page activity since the window start — every editor, not just the last.

    The window is ``since → now`` when ``since`` (a datetime — always a midnight
    for the standup, so a CQL date literal is exact) is given, else the last
    ``days`` days. Emitted kinds:

    - ``page``         — one item per distinct in-window editor of each modified page
                         (the last editor from CQL plus cached/live version enrichment)
    - ``page-created`` — the creator of a page created in-window (no extra call)

    Each item: {author, author_email?, kind, title, timestamp, key(id)}.
    Returns [] when Confluence is unconfigured or the CQL query fails.
    """
    logger.info("confluence_recent_pages: space=%r days=%d since=%s", space_key, days, since)
    conf = _make_confluence_client()
    if conf is None:
        logger.warning("confluence_recent_pages skipped — Confluence not configured")
        return []

    budget = max(0.0, float(enrichment_budget_seconds))
    started = time.monotonic()
    deadline = started + budget
    # atlassian-python-api stores the requests timeout on the client. Bounding
    # each request prevents abandoned enrichment workers from lingering.
    if hasattr(conf, "timeout"):
        conf.timeout = max(1, math.ceil(budget))

    key = space_key.strip() or (get_confluence_space_key() or "")
    space_filter = f' AND space = "{key}"' if key else ""
    # CQL supports absolute "yyyy-MM-dd" date literals and now("-Nd") date math.
    modified_clause = (
        f'lastModified >= "{since:%Y-%m-%d}"' if since is not None else f'lastModified >= now("-{int(days)}d")'
    )
    cutoff = _page_cutoff(days, since)
    try:
        cql = f"type = page AND {modified_clause}{space_filter} ORDER BY lastModified DESC"
        results = conf.cql(cql, limit=50, expand="history.lastUpdated,history.createdBy,history.createdDate")
        pages = results.get("results", []) if isinstance(results, dict) else []
        items: list[dict] = []
        version_candidates: list[dict] = []
        for page in pages:
            content = page.get("content", page)  # cql may nest the page under "content"
            history = content.get("history", {}) if isinstance(content, dict) else {}
            last_updated = history.get("lastUpdated", {}) if isinstance(history, dict) else {}
            by = last_updated.get("by", {}) if isinstance(last_updated, dict) else {}
            author = _display_name(by)
            title = content.get("title", page.get("title", "Untitled"))
            page_id = content.get("id", page.get("id", ""))
            page_url = _page_link(content if isinstance(content, dict) else {}, page_id)
            items.append(
                {
                    "author": author,
                    "author_email": (by.get("email", "") or "") if isinstance(by, dict) else "",
                    "kind": "page",
                    "title": title,
                    "timestamp": (last_updated.get("when", "") or "")[:19] if isinstance(last_updated, dict) else "",
                    "key": page_id,
                    "url": page_url,
                }
            )
            credited = {author} if author else set()
            # Page created in-window → credit the creator (data already in the expand).
            created_by = history.get("createdBy", {}) if isinstance(history, dict) else {}
            created_when = _iso_to_dt(history.get("createdDate", "") if isinstance(history, dict) else "")
            creator = _display_name(created_by)
            if creator and created_when is not None and created_when >= cutoff:
                if creator not in credited:
                    items.append(
                        {
                            "author": creator,
                            "author_email": (created_by.get("email", "") or ""),
                            "kind": "page-created",
                            "title": f"created '{title}'",
                            "timestamp": (history.get("createdDate", "") or "")[:19],
                            "key": page_id,
                            "url": page_url,
                        }
                    )
                credited.add(creator)
            # A first revision cannot have an earlier editor. A missing revision
            # number remains eligible because older Confluence responses omit it.
            page_number = last_updated.get("number") if isinstance(last_updated, dict) else None
            if page_id and page_number != 1:
                page_revision = str(last_updated.get("number") or last_updated.get("when") or "current")
                version_candidates.append(
                    {
                        "page_id": page_id,
                        "title": title,
                        "credited": credited,
                        "url": page_url,
                        "revision": page_revision,
                    }
                )

        # Cache hits are effectively free and are applied for every candidate.
        live_candidates: list[dict] = []
        enriched_pages = 0
        for candidate in version_candidates:
            cached = (
                metadata_cache.get(
                    "confluence",
                    "version_history",
                    candidate["page_id"],
                    candidate["revision"],
                )
                if metadata_cache is not None
                else None
            )
            if isinstance(cached, list):
                items.extend(
                    _version_editor_items(
                        cached,
                        candidate["page_id"],
                        candidate["title"],
                        cutoff,
                        candidate["credited"],
                        url=candidate["url"],
                    )
                )
                enriched_pages += 1
            else:
                live_candidates.append(candidate)

        # Only the newest misses are fetched. Futures that have not finished at
        # the deadline are left to their request timeout; their results are not
        # allowed to hold up or mutate the completed standup run.
        selected_live = live_candidates[:_MAX_LIVE_VERSION_LOOKUPS]
        remaining = max(0.0, deadline - time.monotonic())
        if selected_live and remaining > 0:
            if hasattr(conf, "timeout"):
                conf.timeout = max(1, math.ceil(remaining))
            pool = ThreadPoolExecutor(
                max_workers=len(selected_live),
                thread_name_prefix="standup-confluence",
            )
            future_candidates = {
                pool.submit(_fetch_version_history, conf, candidate["page_id"]): candidate
                for candidate in selected_live
            }
            done, _pending = wait(future_candidates, timeout=remaining)
            for future in done:
                candidate = future_candidates[future]
                try:
                    versions = future.result()
                except Exception as e:
                    logger.debug("confluence version lookup failed for %s: %s", candidate["page_id"], e)
                    continue
                if metadata_cache is not None:
                    metadata_cache.set(
                        "confluence",
                        "version_history",
                        candidate["page_id"],
                        candidate["revision"],
                        versions,
                        replace_revisions=True,
                    )
                items.extend(
                    _version_editor_items(
                        versions,
                        candidate["page_id"],
                        candidate["title"],
                        cutoff,
                        candidate["credited"],
                        url=candidate["url"],
                    )
                )
                enriched_pages += 1
            pool.shutdown(wait=False, cancel_futures=True)

        incomplete_pages = len(version_candidates) - enriched_pages
        if incomplete_pages:
            message = f"latest editors captured; earlier-editor enrichment incomplete for {incomplete_pages} page(s)"
            logger.warning("confluence_recent_pages partial: %s", message)
            if on_partial is not None:
                try:
                    on_partial(message)
                except Exception:
                    logger.debug("confluence partial callback failed", exc_info=True)
        logger.info(
            "confluence_recent_pages: %d item(s) from %d page(s) in %.2fs",
            len(items),
            len(pages),
            time.monotonic() - started,
        )
        return items
    except Timeout as e:
        from yeaboi.standup.errors import StandupSourceError

        raise StandupSourceError("confluence", f"request timed out after {budget:g} seconds") from e
    except HTTPError as e:
        code = getattr(getattr(e, "response", None), "status_code", 0)
        if code in (401, 403):
            from yeaboi.standup.errors import StandupSourceError

            raise StandupSourceError("confluence", "authentication failed — check Atlassian API token") from e
        logger.warning("confluence_recent_pages failed: %s", _confluence_error_msg(e))
        return []
    except Exception as e:
        logger.warning("confluence_recent_pages unexpected error: %s", e)
        return []


# ---------------------------------------------------------------------------
# Full-page reader for Roadmap intake
# ---------------------------------------------------------------------------
# Plain function (not @tool) the roadmap ingester calls directly. The @tool
# confluence_read_page truncates at 8 000 chars to protect the ReAct loop's
# context; a quarterly roadmap needs a larger budget, so this helper takes an
# explicit max_chars and returns structured data instead of display text.


def confluence_read_page_text(page_id: str = "", page_title: str = "", max_chars: int = 30_000) -> dict:
    """Read a full Confluence page as plain text for roadmap ingestion.

    Provide either page_id or page_title (title lookup needs CONFLUENCE_SPACE_KEY).
    Returns {"title", "text", "truncated", "error"} — never raises; any failure
    lands in "error" with empty text so the caller can surface it as a warning.
    """
    logger.info("confluence_read_page_text: page_id=%r title=%r max_chars=%d", page_id, page_title, max_chars)
    conf = _make_confluence_client()
    if conf is None:
        return {"title": "", "text": "", "truncated": False, "error": _MISSING_CONFIG_MSG}
    if not page_id and not page_title:
        return {"title": "", "text": "", "truncated": False, "error": "Provide a Confluence page ID or title."}

    try:
        if page_id:
            page = conf.get_page_by_id(page_id, expand="body.storage")
        else:
            key = get_confluence_space_key() or ""
            if not key:
                return {
                    "title": "",
                    "text": "",
                    "truncated": False,
                    "error": "Looking up a page by title needs CONFLUENCE_SPACE_KEY set in .env.",
                }
            page = conf.get_page_by_title(space=key, title=page_title, expand="body.storage")
        if not page:
            ref = page_id or f"'{page_title}'"
            return {"title": "", "text": "", "truncated": False, "error": f"Confluence page {ref} not found."}

        title = page.get("title", "Untitled")
        text = _strip_html_tags(page.get("body", {}).get("storage", {}).get("value", ""))
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        logger.info("confluence_read_page_text: fetched %r (%d chars, truncated=%s)", title, len(text), truncated)
        return {"title": title, "text": text, "truncated": truncated, "error": ""}
    except HTTPError as e:
        logger.error("confluence_read_page_text HTTP error: %s", e)
        return {"title": "", "text": "", "truncated": False, "error": _confluence_error_msg(e)}
    except Exception as e:
        logger.error("confluence_read_page_text unexpected error: %s", e)
        return {"title": "", "text": "", "truncated": False, "error": f"Confluence read failed: {e}"}
