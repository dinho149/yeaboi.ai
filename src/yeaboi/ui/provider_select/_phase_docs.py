"""Docs phase of the provider selection wizard — a single Notion / Confluence / Skip picker.

# See docs: "Architecture" — the Docs step lets the user pick one documentation
# provider, mirroring the Issue Tracking step's Jira / Azure DevOps / Skip picker
# (_phase_issue_tracking). It replaces the previous two sequential sub-pickers
# (Notion, then a Jira-gated Confluence) with one unified picker so Confluence is a
# first-class option regardless of whether Jira was configured.
#
# The picker dispatches to the chosen provider's credential form:
#   * Notion    → _run_notion_form   (its own integration token)
#   * Confluence→ _run_confluence_form (reuses Jira's Atlassian creds when present,
#                 else collects a standalone Atlassian login inline)
# Both forms reuse the shared multi-field renderer + verify/confirm animation.
"""

from __future__ import annotations

import logging

from rich.console import Console
from rich.live import Live

from yeaboi.ui.provider_select._nav import StepNav, nav_for_key
from yeaboi.ui.provider_select._phase_confluence import _run_confluence_form
from yeaboi.ui.provider_select._phase_notion import _drain, _run_notion_form
from yeaboi.ui.provider_select.screens._screens import (
    _ACCENT,
    _build_provider_row,
    _build_screen_frame,
)

logger = logging.getLogger(__name__)

_TITLE = "Docs"
_DOCS_STEP = 2  # _STEPS[2] == "Docs"


def _run_docs(
    console: Console,
    read_key,
    existing_config: dict[str, str] | None,
    live: Live,
    *,
    jira_creds: dict[str, str] | None,
) -> dict[str, dict[str, str]] | StepNav | None:
    """Run the Docs step: pick Notion / Confluence / Skip, then collect that provider.

    ``jira_creds`` supplies the shared Atlassian credentials from the Issue Tracking
    step (used to keep the Confluence form to just a space key when present).

    Returns ``{"notion": {...}, "confluence": {...}}`` — exactly one populated, the
    other empty (Skip leaves both empty). Returns a StepNav for section navigation
    (←/→/F), or None when the user presses Esc to go back to Issue Tracking.
    """
    from rich.align import Align
    from rich.text import Text

    cards = [
        {"name": "Notion", "color": _ACCENT},
        {"name": "Confluence", "color": _ACCENT},
        {"name": "Skip", "color": _ACCENT},
    ]
    pick = 0

    def _render_menu() -> None:
        rows = [_build_provider_row(c, selected=(i == pick)) for i, c in enumerate(cards)]
        body = [item for row in rows for item in (Align.center(row), Text(""))]
        if body:
            body = body[:-1]
        body_h = len(rows) * 3 - 1 if rows else 0
        w, h = console.size
        live.update(
            _build_screen_frame(
                subtitle="Docs · ↑↓ choose · Enter select · ←→ section · F finish",
                step=_DOCS_STEP,
                body_items=body,
                body_height=body_h,
                width=w,
                height=h,
                title_text=_TITLE,
            )
        )

    # The picker loop re-runs after a form Esc so the user lands back on the choices
    # (rather than jumping all the way back to Issue Tracking).
    while True:
        _drain()
        _render_menu()
        choice: str | StepNav | None = None
        while True:
            key = read_key()
            # Section navigation (←/→ between chips, F to finish) short-circuits the
            # picker so the user can leave the Docs step without choosing.
            nav = nav_for_key(key, _DOCS_STEP)
            if nav is not None:
                logger.info("Docs step: section navigation (%r)", nav)
                return nav
            if key in ("up", "scroll_up"):
                pick = (pick - 1) % len(cards)
            elif key in ("down", "scroll_down"):
                pick = (pick + 1) % len(cards)
            elif key == "enter":
                choice = ("notion", "confluence", "skip")[pick]
                break
            elif key == "esc":
                logger.info("Docs step: user pressed Esc (back to Issue Tracking)")
                return None
            _render_menu()

        if choice == "skip":
            logger.info("Docs step: skipped")
            return {"notion": {}, "confluence": {}}

        if choice == "notion":
            result = _run_notion_form(console, read_key, existing_config, live)
            if result is None:
                continue  # Esc in the form → back to the Docs picker
            logger.info("Docs step: Notion configured (%d keys)", len(result))
            return {"notion": result, "confluence": {}}

        # choice == "confluence"
        result = _run_confluence_form(console, read_key, existing_config, live, jira_creds=jira_creds)
        if result is None:
            continue  # Esc in the form → back to the Docs picker
        logger.info("Docs step: Confluence configured (%d keys)", len(result))
        return {"notion": {}, "confluence": result}
