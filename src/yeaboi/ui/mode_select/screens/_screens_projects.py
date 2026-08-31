"""The Projects page — every project, and which one this session runs under.

One list, one row per project: name, id, how many sessions it links, when it
was last active. The active project is the row every scoped run reads its
context through, so it wears the accent and a marker rather than hiding in a
status line.

# See docs: "Architecture" — TUI system; this page follows the shared blueprint
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.shared._components import (
    PAD,
    PROJECTS_THEME,
    TITLE_ROWS,
    build_action_buttons,
    build_page_panel,
    build_reveal_subtitle,
    build_scrollbar,
    calc_viewport,
    projects_title,
)
from yeaboi.ui.shared._scroll import publish_geometry

# Header = blank + title(TITLE_ROWS) + blank + subtitle.
_HEADER_ROWS = 2 + TITLE_ROWS + 1
# Actions = the spacer blank plus all three button rows (a fixed-height Panel
# crops from the bottom, and buttons half off screen still answer Enter).
_ACTION_ROWS = 4

ACTIONS = ["Set active", "Context", "Archive", "Back"]

# The context sub-page: which cross-mode sources a run may read.
CONTEXT_ACTIONS = ["All on", "Incognito", "Back"]
CONTEXT_ROWS: tuple[tuple[str, str, str], ...] = (
    ("retro", "Retro history", "action items, themes and carry-over"),
    ("standup", "Standup history", "blockers, confidence trend and cadence"),
    ("plan", "Latest sprint plan", "sprint framing and roster for standups and reports"),
    ("performance", "Performance", "open 1:1 actions and review focus"),
    ("analysis", "Analysis profile", "team calibration and AC style"),
)


def _cell(text: str, style: str) -> Text:
    return Text(text, style=style, no_wrap=True, overflow="ellipsis")


def _build_rows(projects: list[dict], selected: int, active_project_id: str, theme) -> list:
    if not projects:
        return [
            Text(""),
            Text(f"{PAD}No projects yet.", style=theme.muted),
            Text(f"{PAD}Create one from the terminal: yeaboi project create <name>", style=theme.dim),
        ]
    table = Table(show_header=True, show_edge=False, box=None, padding=(0, 1), pad_edge=False, expand=True)
    table.add_column(Text(f"{PAD}  project", style=theme.muted), ratio=3)
    table.add_column(Text("id", style=theme.muted), ratio=2)
    table.add_column(Text("sessions", style=theme.muted), ratio=1, justify="right")
    table.add_column(Text("last active", style=theme.muted), ratio=2)
    for i, project in enumerate(projects):
        is_selected = i == selected
        is_active = project["project_id"] == active_project_id
        marker = "▸ " if is_selected else "  "
        name_style = f"bold {theme.accent_bright}" if is_selected else (theme.accent if is_active else theme.value)
        name = f"{PAD}{marker}{'● ' if is_active else ''}{project['name']}"
        if project.get("archived"):
            name += " (archived)"
        table.add_row(
            _cell(name, name_style),
            _cell(project["project_id"], theme.id),
            _cell(str(project.get("session_count", 0)), theme.muted),
            _cell(project.get("last_active", "")[:10], theme.muted),
        )
    return [table]


def _build_projects_screen(
    projects: list[dict],
    *,
    selected: int = 0,
    active_project_id: str = "",
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    actions: list[str] | None = None,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    message: str = "",
) -> Panel:
    """Build the Projects page: the project list and the active marker."""
    theme = PROJECTS_THEME
    title = projects_title(shimmer_tick)
    sub = build_reveal_subtitle("One project, every mode's context", sub_reveal, pad=PAD + "  ")

    body: list = _build_rows(projects, selected, active_project_id, theme)
    body.append(Text(""))
    if active_project_id:
        body.append(Text(f"{PAD}Scoped runs read context through the ● project.", style=theme.dim))
    else:
        body.append(Text(f"{PAD}No active project — runs stay team-wide until one is set.", style=theme.dim))
    body.append(Text(f"{PAD}New projects come from the terminal: yeaboi project create <name>", style=theme.dim))
    if message:
        body.append(Text(""))
        body.append(Text(f"{PAD}{message}", style=theme.accent))

    viewport_h = calc_viewport(height, header_h=_HEADER_ROWS, action_h=_ACTION_ROWS)
    total = len(body)
    max_scroll = max(0, total - viewport_h)
    offset = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body[offset : offset + viewport_h]
    padded = list(visible) + [Text("")] * max(0, viewport_h - len(visible))

    scrollbar = build_scrollbar(viewport_h, total, offset, max_scroll)
    if scrollbar is not None:
        frame = Table(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        frame.add_column(ratio=1)
        frame.add_column(width=1)
        frame.add_row(Group(*padded), scrollbar)
        viewport: object = frame
    else:
        viewport = Group(*padded)

    btn_top, btn_mid, btn_bot = build_action_buttons(actions or list(ACTIONS), action_sel)
    content = Group(Text(""), title, Text(""), sub, viewport, Text(""), btn_top, btn_mid, btn_bot)
    return build_page_panel(content, theme=theme, height=height)


def _build_context_screen(
    deps: tuple[str, ...] | None,
    *,
    selected: int = 0,
    action_sel: int = 0,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    message: str = "",
) -> Panel:
    """Build the context-toggles sub-page: one ●/○ row per source.

    ``deps`` mirrors the engines' contract: ``None`` inherits (all sources
    on, or the project default when one is set), ``()`` is incognito.
    """
    theme = PROJECTS_THEME
    title = projects_title(shimmer_tick)
    sub = build_reveal_subtitle("Which sources feed this session's runs", sub_reveal, pad=PAD + "  ")

    body: list = [Text("")]
    for i, (token, label, hint) in enumerate(CONTEXT_ROWS):
        focused = i == selected
        on = deps is None or token in deps
        glyph = "●" if on else "○"
        marker = "▸ " if focused else "  "
        style = f"bold {theme.accent_bright}" if focused else (theme.value if on else theme.muted)
        row = Text(f"{PAD}{marker}{glyph} {label}", style=style)
        row.append(f"  ·  {hint}", style=theme.dim)
        body.append(row)
    body.append(Text(""))
    if deps is None:
        body.append(Text(f"{PAD}Inheriting — every source on (or the project's saved default).", style=theme.dim))
    elif not deps:
        body.append(Text(f"{PAD}Incognito — runs read no cross-mode context. Sessions still persist.", style=theme.dim))
    else:
        body.append(Text(f"{PAD}Only the ● sources feed runs started from the menu.", style=theme.dim))
    body.append(Text(f"{PAD}Space toggles a source; changes last until yeaboi is closed.", style=theme.dim))
    if message:
        body.append(Text(""))
        body.append(Text(f"{PAD}{message}", style=theme.accent))

    viewport_h = calc_viewport(height, header_h=_HEADER_ROWS, action_h=_ACTION_ROWS)
    total = len(body)
    visible = body[:viewport_h]
    padded = list(visible) + [Text("")] * max(0, viewport_h - len(visible))
    publish_geometry(None, max(0, total - viewport_h), viewport_h)

    btn_top, btn_mid, btn_bot = build_action_buttons(list(CONTEXT_ACTIONS), action_sel)
    content = Group(Text(""), title, Text(""), sub, Group(*padded), Text(""), btn_top, btn_mid, btn_bot)
    return build_page_panel(content, theme=theme, height=height)
