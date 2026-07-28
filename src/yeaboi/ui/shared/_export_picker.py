"""Shared export-destination picker used by every Export button in the TUI.

One modal run-loop (the ``_confirm_ticket_generation`` pattern) instead of a
hand-rolled submenu per page: the caller passes its Live/console/read_key and
gets back the chosen destination key, or ``None`` when the user backs out.

Destinations:
    "files"       — Markdown + HTML on disk (always available)
    "notion"      — publish to Notion (shown only when NOTION_TOKEN is set)
    "confluence"  — publish to Confluence (shown only when creds resolve,
                    including the JIRA_* credential fallback)
plus any lowercased ``extra_options`` the caller adds (e.g. "jira").

Publish destinations come from provider setup: Notion pages go under the
exports page, Confluence pages under the exports page in CONFLUENCE_SPACE_KEY.
With no exports page configured, docs are grouped under an auto-created
**🤙 yeaboi** container page (under the Notion root page / at the space root)
so everything yeaboi publishes stays together. A warning popup only appears
when publishing is impossible — Notion with no page at all, Confluence with no
space key — and offers to open the setup wizard.
"""

from __future__ import annotations

import logging
import os

from rich.align import Align
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from yeaboi.config import (
    get_confluence_base_url,
    get_confluence_email,
    get_confluence_space_key,
    get_confluence_token,
    get_notion_export_parent_page_id,
    get_notion_token,
)
from yeaboi.export_targets import CONFLUENCE_PATH_HINT, NOTION_PATH_HINT
from yeaboi.ui.shared._click import parse_click
from yeaboi.ui.shared._components import (
    ANALYSIS_THEME,
    PAD,
    PERFORMANCE_THEME,
    PLANNING_THEME,
    REPORTING_THEME,
    RETRO_THEME,
    STANDUP_THEME,
    analysis_title,
    build_action_buttons,
    build_page_panel,
    build_popup,
    performance_title,
    planning_title,
    reporting_title,
    retro_title,
    standup_title,
)

logger = logging.getLogger(__name__)

DEST_FILES = "files"
DEST_COPY = "copy"
DEST_NOTION = "notion"
DEST_CONFLUENCE = "confluence"

_LABELS = {
    DEST_FILES: "Files",
    DEST_COPY: "Copy to clipboard",
    DEST_NOTION: "Notion",
    DEST_CONFLUENCE: "Confluence",
}

# Per-mode (title builder, theme) so the picker inherits the look of the page
# that opened it — same palette rules as every other screen.
_MODE_STYLES = {
    "planning": (planning_title, PLANNING_THEME),
    "analysis": (analysis_title, ANALYSIS_THEME),
    "standup": (standup_title, STANDUP_THEME),
    "retro": (retro_title, RETRO_THEME),
    "performance": (performance_title, PERFORMANCE_THEME),
    "reporting": (reporting_title, REPORTING_THEME),
}


def available_destinations() -> list[str]:
    """Return the export destinations available in the current configuration.

    "files" is always available. Notion appears when its token is set;
    Confluence when base URL + email + token all resolve (the getters already
    fall back to the JIRA_* credentials, so a Jira-only setup counts).
    """
    # Files + Copy are always available (no config); Copy sits second so it's a
    # prominent, zero-setup way to pull the data out of the terminal.
    dests = [DEST_FILES, DEST_COPY]
    if get_notion_token():
        dests.append(DEST_NOTION)
    if get_confluence_base_url() and get_confluence_email() and get_confluence_token():
        dests.append(DEST_CONFLUENCE)
    return dests


def _dest_description(key: str, label: str, mode: str) -> str:
    """One-line description of what the highlighted destination will do."""
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
    return f"Send to {label}"


def _export_path_warning(dest: str) -> str:
    """Return the Setup hint when publishing to *dest* is impossible, else ''.

    Notion needs *some* page to create under (exports page or root page);
    Confluence needs a space key. Both come from Setup → Docs.
    """
    if dest == DEST_NOTION and not get_notion_export_parent_page_id():
        return NOTION_PATH_HINT
    if dest == DEST_CONFLUENCE and not get_confluence_space_key():
        return CONFLUENCE_PATH_HINT
    return ""


def _build_export_picker_screen(
    *,
    mode: str,
    labels: list[str],
    selected: int,
    warning: str = "",
    warning_actions: list[str] | None = None,
    warning_sel: int = 0,
    subtitle: str = "Files → Markdown + HTML on disk",
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Render the destination picker as a standard full-screen panel.

    Follows the shared page structure (title → subtitle → content → buttons);
    *subtitle* describes the highlighted destination and changes as the
    selection moves. When *warning* is set a popup card replaces the hint line
    so the user sees why the chosen destination is blocked. With
    *warning_actions* the button row swaps to those actions
    (Open Setup / Back) while the popup is up.
    """
    title_fn, theme = _MODE_STYLES.get(mode, _MODE_STYLES["planning"])

    lines: list = [Text("")]
    lines.append(title_fn(width=width))
    lines.append(Text(""))
    lines.append(Text(PAD + "Choose export destination", style="bold white", justify="left"))
    lines.append(Text(PAD + subtitle, style=theme.muted, justify="left"))
    lines.append(Text(""))

    if warning:
        lines.append(Align.center(build_popup(warning, width=min(width - 8, 56), border_style=theme.warn)))
    else:
        lines.append(Text(""))
        lines.append(Text(""))
        lines.append(Text(""))

    lines.append(Text(""))
    if warning and warning_actions:
        btn_top, btn_mid, btn_bot = build_action_buttons(warning_actions, warning_sel)
    else:
        btn_top, btn_mid, btn_bot = build_action_buttons(labels, selected)
    lines += [btn_top, btn_mid, btn_bot]

    return build_page_panel(Group(*lines), theme=theme, border_style=theme.sep, height=height)


def pick_export_destination(
    live,
    console,
    read_key,
    frame_time,
    supports_timeout,
    *,
    mode: str,
    extra_options: list[str] | None = None,
    open_setup=None,
) -> str | None:
    """Run the modal destination picker; return the chosen key or None.

    Mirrors the ``_confirm_ticket_generation`` modal-loop calling convention so
    it composes with every frame-timed page loop (mode_select and session
    phases alike). Returns "files"/"notion"/"confluence", a lowercased extra
    option (e.g. "jira"), or None on Back/Esc.

    ``open_setup`` (optional zero-arg callable) launches the setup wizard.
    When provided, the blocked-destination warning popup offers
    **Open Setup / Back** buttons; after the wizard closes, the export
    proceeds straight away if the missing destination is now configured.
    Without it, any key dismisses the warning (the legacy behaviour).
    """
    dests = available_destinations()
    labels = [_LABELS[d] for d in dests] + list(extra_options or []) + ["Back"]
    keys = dests + [opt.lower().replace(" ", "").replace("azuredevops", "azdevops") for opt in extra_options or []]
    logger.info("Export picker opened (mode=%s, destinations=%s)", mode, labels)

    sel = 0
    warning = ""
    warning_choice = ""  # the blocked destination the warning is about
    wsel = 0  # warning-popup button selection: 0 = Open Setup, 1 = Back
    while True:
        # Subtitle tracks the highlighted button — re-read each frame so it
        # reflects destinations configured via the Open Setup hook immediately.
        if warning and open_setup is not None:
            subtitle = "Add the destination in Setup, then continue" if wsel == 0 else "Return to the picker"
        else:
            key = keys[sel] if sel < len(keys) else "back"
            subtitle = _dest_description(key, labels[sel], mode)
        w, h = console.size
        panel = _build_export_picker_screen(
            mode=mode,
            labels=labels,
            selected=sel,
            warning=warning,
            warning_actions=["Open Setup", "Back"] if warning and open_setup is not None else None,
            warning_sel=wsel,
            subtitle=subtitle,
            width=w,
            height=h,
        )
        live.update(panel)
        # Session phases pass a _key(timeout=...) that may not accept the kwarg —
        # same TypeError fallback the phase loops themselves use.
        try:
            k = read_key(timeout=frame_time) if supports_timeout else read_key()
        except TypeError:
            k = read_key()
        # read_key returns "" on a timeout tick (and for consumed mouse events)
        # — treat those as idle, not as a keypress, or the warning popup would
        # be dismissed by the very next frame tick.
        if not k:
            continue
        # Click a button → select it and act, exactly like arrow-to + Enter.
        clicked = parse_click(k)
        if clicked is not None:
            _cx, _cy = clicked
            row_labels = ["Open Setup", "Back"] if (warning and open_setup is not None) else labels
            idx = button_click(console, panel, _cx, _cy, row_labels)
            if idx is None:
                continue  # clicked off the button row — ignore
            if warning and open_setup is not None:
                wsel = idx
            else:
                sel = idx
            k = "enter"
        if warning:
            if open_setup is None:
                # Any real key acknowledges the warning and returns to the picker.
                warning = ""
                continue
            if k == "left":
                wsel = max(0, wsel - 1)
            elif k == "right":
                wsel = min(1, wsel + 1)
            elif k in ("enter", " "):
                if wsel == 0:  # Open Setup → provider setup wizard
                    logger.info("Export picker: opening Setup for %s", warning_choice)
                    open_setup()
                    if not _export_path_warning(warning_choice):
                        # Destination configured in Setup — carry on with the export.
                        logger.info("Export destination chosen after Setup: %s", warning_choice)
                        return warning_choice
                    logger.info("Export destination %s still unconfigured after Setup", warning_choice)
                warning = ""
                wsel = 0
            elif k in ("esc", "q"):
                warning = ""
                wsel = 0
            continue
        if k == "left":
            sel = max(0, sel - 1)
        elif k == "right":
            sel = min(len(labels) - 1, sel + 1)
        elif k in ("enter", " "):
            if sel == len(labels) - 1:  # Back
                logger.info("Export picker cancelled (Back)")
                return None
            choice = keys[sel]
            warning = _export_path_warning(choice)
            if warning:
                warning_choice = choice
                wsel = 0
                logger.info("Export destination %s blocked: %s", choice, warning)
                continue
            logger.info("Export destination chosen: %s", choice)
            return choice
        elif k in ("esc", "q"):
            logger.info("Export picker cancelled (esc)")
            return None
