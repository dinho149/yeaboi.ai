"""Where an export can go, and what each destination will do.

The Export button is on every result screen of every mode, and what it offers
depends only on configuration: Files and Copy always work, Notion appears when
its token is set, Confluence when its base URL, email and token all resolve.
Those rules — plus the one-line description under each choice and the hint shown
when a destination is configured-but-unusable — are decisions, not rendering, so
they live here and every surface reads the same answer.

:mod:`yeaboi.export_targets` still owns the publishing itself. This module owns
the menu.
"""

from __future__ import annotations

import os

from yeaboi.config import (
    get_confluence_base_url,
    get_confluence_email,
    get_confluence_space_key,
    get_confluence_token,
    get_notion_export_parent_page_id,
    get_notion_token,
)
from yeaboi.export_targets import CONFLUENCE_PATH_HINT, NOTION_PATH_HINT

DEST_FILES = "files"
DEST_COPY = "copy"
DEST_NOTION = "notion"
DEST_CONFLUENCE = "confluence"

DEST_LABELS: dict[str, str] = {
    DEST_FILES: "Files",
    DEST_COPY: "Copy to clipboard",
    DEST_NOTION: "Notion",
    DEST_CONFLUENCE: "Confluence",
}

#: Destinations the *client* completes rather than the backend: nothing leaves
#: the machine and no publishing call is made, so a surface handles them with
#: whatever clipboard it has rather than posting the document anywhere.
LOCAL_DESTINATIONS: frozenset[str] = frozenset({DEST_COPY})


def available_destinations() -> list[str]:
    """The export destinations the current configuration can reach.

    Files + Copy are always available (no config); Copy sits second so it is a
    prominent, zero-setup way to pull the data out.
    """
    destinations = [DEST_FILES, DEST_COPY]
    if get_notion_token():
        destinations.append(DEST_NOTION)
    if get_confluence_base_url() and get_confluence_email() and get_confluence_token():
        destinations.append(DEST_CONFLUENCE)
    return destinations


def destination_description(key: str, *, mode: str, label: str = "") -> str:
    """One line saying what choosing *key* will do."""
    if key == DEST_FILES:
        from yeaboi.paths import EXPORTS_DIR

        base = str(EXPORTS_DIR).replace(str(os.path.expanduser("~")), "~", 1)
        return f"Markdown + HTML → {base}/{mode}"
    if key == DEST_COPY:
        return "Copy the Markdown to your clipboard"
    if key == DEST_NOTION:
        # The exports page (raw env — the getter already folds in the root-page
        # fallback) vs the 🤙 yeaboi container, so the hint names the target.
        if os.getenv("NOTION_EXPORT_PARENT_PAGE_ID"):
            return "Publish a page under your Notion exports page"
        if get_notion_export_parent_page_id():
            return "Publish under the 🤙 yeaboi page in Notion"
        return "Needs a Notion page — press Enter to set it up"
    if key == DEST_CONFLUENCE:
        space = get_confluence_space_key()
        if space and os.getenv("CONFLUENCE_EXPORT_PARENT_PAGE_ID"):
            return f"Publish under your Confluence exports page in {space}"
        if space:
            return f"Publish under the 🤙 yeaboi page in space {space}"
        return "Needs a Confluence space key — press Enter to set it up"
    if key == "back":
        return "Return without exporting"
    if key == "shareonline":
        return "Publish this saved HTML temporarily behind an access code"
    if key == "powerpoint":
        return "A .pptx deck styled by the selected palette (needs the docs extra)"
    return f"Send to {label or key}"


def destination_blocker(key: str) -> str:
    """The Setup hint when publishing to *key* is impossible, else ``""``.

    Notion needs *some* page to create under (exports page or root page);
    Confluence needs a space key. Both come from Setup → Docs.
    """
    if key == DEST_NOTION and not get_notion_export_parent_page_id():
        return NOTION_PATH_HINT
    if key == DEST_CONFLUENCE and not get_confluence_space_key():
        return CONFLUENCE_PATH_HINT
    return ""


def destination_options(*, mode: str, extras: list[str] | None = None) -> list[dict]:
    """The whole menu for one mode, as data.

    ``extras`` are mode-specific destinations the caller adds (``"jira"``,
    ``"powerpoint"``, ``"shareonline"``); they are described but never blocked
    here, because what makes them possible is the caller's own state.
    """
    options = [
        {
            "key": key,
            "label": DEST_LABELS[key],
            "description": destination_description(key, mode=mode),
            "blocked": destination_blocker(key),
            "local": key in LOCAL_DESTINATIONS,
        }
        for key in available_destinations()
    ]
    for extra in extras or []:
        options.append(
            {
                "key": extra,
                "label": extra,
                "description": destination_description(extra, mode=mode, label=extra),
                "blocked": "",
                "local": extra in LOCAL_DESTINATIONS,
            }
        )
    return options
