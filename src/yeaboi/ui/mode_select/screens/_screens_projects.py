"""The Projects pages — the list, one project, and a new project's draft.

A project is the durable way to work: every run inside it reads what the
earlier runs left behind. The list says so with the context-flow strip
(``projects/flow.py``) above the projects, split into In progress and
Completed; a project's own page repeats the strip with what has already run
inside it; the draft page previews the name and pitch a new project gets.

# See docs: "Architecture" — TUI system; this page follows the shared blueprint
"""

from __future__ import annotations

import textwrap

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.projects.flow import AGENTS_FLOW_LINE, FlowStep, flow_for
from yeaboi.ui.mode_select.screens._screens import _MODE_CARDS, _SOLO_CARDS
from yeaboi.ui.mode_select.screens._screens_door import world_theme
from yeaboi.ui.shared._components import (
    PAD,
    PROJECTS_THEME,
    TITLE_ROWS,
    build_action_buttons,
    build_key_hints,
    build_page_panel,
    build_reveal_subtitle,
    build_scrollbar,
    calc_viewport,
    projects_title,
    render_to_lines,
)
from yeaboi.ui.shared._scroll import publish_geometry

# Header = blank + title(TITLE_ROWS) + blank + subtitle.
_HEADER_ROWS = 2 + TITLE_ROWS + 1
# Actions = the spacer blank plus all three button rows (a fixed-height Panel
# crops from the bottom, and buttons half off screen still answer Enter).
_ACTION_ROWS = 4

ACTIONS = ["Open", "New", "Done", "Archive", "Back"]
# A done project's third button reopens it.
REOPEN_LABEL = "Reopen"

# One project's page. Start opens the scoped menu; Plan starts planning inside it.
PROJECT_ACTIONS = ["Start", "Plan", "Runs", "Context", "Back"]

# The draft preview of a project that does not exist yet.
DRAFT_ACTIONS = ["Create", "AI rewrite", "Edit", "Cancel"]

# The project-sessions sub-page: every run inside one project.
SESSIONS_ACTIONS = ["Open", "Back"]

# The context sub-page: which cross-mode sources a run may read.
CONTEXT_ACTIONS = ["All on", "Incognito", "Back"]
CONTEXT_ROWS: tuple[tuple[str, str, str], ...] = (
    ("retro", "Retro history", "action items, themes and carry-over"),
    ("standup", "Standup history", "blockers, confidence trend and cadence"),
    ("plan", "Latest sprint plan", "sprint framing and roster for standups and reports"),
    ("performance", "Performance", "open 1:1 actions and review focus"),
    ("analysis", "Analysis profile", "team calibration and AC style"),
)

STATUS_WORDS = {"active": "In progress", "done": "Completed"}
SECTIONS: tuple[tuple[str, str], ...] = (("active", "In progress"), ("done", "Completed"))

EMPTY_LINE = "Nothing here yet. Describe what you're building and yeaboi names it."


def _cell(text: str, style: str) -> Text:
    return Text(text, style=style, no_wrap=True, overflow="ellipsis")


def _inner_width(width: int) -> int:
    """The columns a body line may use: the frame's borders (2), its padding (4) and the scrollbar's column."""
    return max(24, width - 7)


def list_actions(project: dict | None) -> list[str]:
    """The list's buttons, with Reopen in Done's place for a done project."""
    actions = list(ACTIONS)
    if project is not None and project.get("status") == "done":
        actions[actions.index("Done")] = REOPEN_LABEL
    return actions


def ordered_projects(projects: list[dict]) -> list[dict]:
    """In-progress projects first, then the done ones — the list's row order."""
    return [p for p in projects if p.get("status") != "done"] + [p for p in projects if p.get("status") == "done"]


def _cards_for(world: str) -> list[dict]:
    return _SOLO_CARDS if world == "solo" else _MODE_CARDS


def _flow_steps(world: str) -> tuple[FlowStep, ...]:
    return flow_for(world, (card["key"] for card in _cards_for(world)))


def _card_colors(world: str) -> dict[str, str]:
    return {card["key"]: card["color"] for card in _cards_for(world)}


def _fact_style(fact: str, theme) -> str:
    if fact == "done":
        return theme.good
    if fact.endswith(" runs") or fact.endswith(" run") or fact == "in progress":
        return theme.accent_bright
    return theme.dim


def _flow_strip(*, world: str, width: int, inside: dict[str, str] | None = None) -> list[Text]:
    """The context flow: mode dots on one accent rule, each with what it leaves.

    ``inside`` swaps a step's fragment for the fact of what has run inside a
    project (``done`` / ``3 runs`` / ``not yet``).
    """
    theme = PROJECTS_THEME
    steps = _flow_steps(world)
    if not steps:
        return [Text(f"{PAD}{AGENTS_FLOW_LINE}", style=theme.desc)]
    accent = world_theme(world).accent
    colors = _card_colors(world)
    inner = max(20, width - 6 - len(PAD))
    col_w = inner // len(steps)

    heads = Text(PAD)
    for i, step in enumerate(steps):
        heads.append("● ", style=colors.get(step.key, accent))
        heads.append(step.label, style=theme.value)
        if i < len(steps) - 1:
            heads.append(" ", style=accent)
            heads.append("─" * max(1, col_w - len(step.label) - 3), style=accent)
            heads.append(" ", style=accent)
    lines = [heads]

    facts = {step.key: (inside or {}).get(step.key) or step.leaves for step in steps}
    # Under each label when every fact fits its column (a project's page:
    # "done", "3 runs"); otherwise one line per step, since a cut fragment
    # explains nothing.
    if all(len(fact) <= col_w - 2 for fact in facts.values()):
        row = Text(PAD)
        for step in steps:
            row.append(facts[step.key].ljust(col_w), style=_fact_style(facts[step.key], theme))
        lines.append(row)
    else:
        label_w = max(len(step.label) for step in steps) + 2
        for step in steps:
            row = Text(PAD + "  ")
            row.append(step.label.ljust(label_w), style=colors.get(step.key, accent))
            row.append(facts[step.key], style=_fact_style(facts[step.key], theme))
            row.no_wrap = True
            row.overflow = "ellipsis"
            lines.append(row)
    return lines


def _build_rows(projects: list[dict], selected: int, active_project_id: str, theme, width: int) -> list:
    if not projects:
        return [
            Text(f"{PAD}{EMPTY_LINE}", style=theme.muted),
            Text(f"{PAD}New starts one. c sets the context sources for one-off runs.", style=theme.dim),
        ]
    lines: list = []
    index = 0
    for status, label in SECTIONS:
        section = [p for p in projects if (p.get("status") == "done") == (status == "done")]
        if not section:
            continue
        if lines:
            lines.append(Text(""))
        lines.append(Text(f"{PAD}{label}", style=theme.accent))
        table = Table(show_header=False, show_edge=False, box=None, padding=(0, 1), pad_edge=False, expand=True)
        table.add_column(ratio=3)
        table.add_column(ratio=4)
        table.add_column(width=10)
        muted = status == "done"
        for project in section:
            is_selected = index == selected
            is_active = project["project_id"] == active_project_id
            marker = "▸ " if is_selected else "  "
            name_style = f"bold {theme.accent_bright}" if is_selected else (theme.accent if is_active else theme.value)
            if muted and not is_selected:
                name_style = theme.muted
            name = f"{PAD}{marker}{'● ' if is_active else ''}{project['name']}"
            if project.get("archived"):
                name += " (archived)"
            table.add_row(
                _cell(name, name_style),
                _cell(project.get("description", ""), theme.desc if is_selected else theme.dim),
                _cell(project.get("last_active", "")[:10], theme.muted),
            )
            index += 1
        # Flattened to one Text per rendered row: the table draws len(section)
        # lines but counts as a single body entry, which overshoots the viewport
        # and crops the action buttons off the bottom.
        lines.extend(render_to_lines(table, _inner_width(width)))
    return lines


def _viewport(body: list, *, height: int, scroll_offset: int, scroll_meta: dict | None):
    viewport_h = calc_viewport(height, header_h=_HEADER_ROWS, action_h=_ACTION_ROWS)
    total = len(body)
    max_scroll = max(0, total - viewport_h)
    offset = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body[offset : offset + viewport_h]
    padded = list(visible) + [Text("")] * max(0, viewport_h - len(visible))
    scrollbar = build_scrollbar(viewport_h, total, offset, max_scroll)
    if scrollbar is None:
        return Group(*padded)
    frame = Table(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
    frame.add_column(ratio=1)
    frame.add_column(width=1)
    frame.add_row(Group(*padded), scrollbar)
    return frame


def _build_projects_screen(
    projects: list[dict],
    *,
    world: str = "team",
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
    """Build the Projects page: the context flow, then the projects by status.

    ``projects`` must already be in :func:`ordered_projects` order — the
    loop and the builder count rows the same way.
    """
    theme = PROJECTS_THEME
    title = projects_title(shimmer_tick)
    sub = build_reveal_subtitle(
        "Every run inside a project reads what the others left behind", sub_reveal, pad=PAD + "  "
    )

    body: list = [Text("")]
    body.extend(_flow_strip(world=world, width=width))
    body.append(Text(""))
    body.extend(_build_rows(projects, selected, active_project_id, theme, width))
    if message:
        body.append(Text(""))
        body.append(Text(f"{PAD}{message}", style=theme.accent))

    viewport = _viewport(body, height=height, scroll_offset=scroll_offset, scroll_meta=scroll_meta)
    current = projects[selected] if 0 <= selected < len(projects) else None
    btn_top, btn_mid, btn_bot = build_action_buttons(actions or list_actions(current), action_sel)
    content = Group(Text(""), title, Text(""), sub, viewport, Text(""), btn_top, btn_mid, btn_bot)
    return build_page_panel(content, theme=theme, height=height)


def _wrap(text: str, width: int, *, max_lines: int) -> list[str]:
    lines = textwrap.wrap(" ".join(text.split()), width=max(20, width))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(0, width - 1)] + "…"
    return lines


def _build_project_screen(
    project: dict,
    runs: list,
    *,
    inside: dict[str, str] | None = None,
    world: str = "team",
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    message: str = "",
) -> Panel:
    """Build one project's page: name, status, description, the flow, its runs.

    ``runs`` are ``sessions_recent.RecentSession`` rows (newest first, a
    handful); ``inside`` is the per-step fact the strip shows instead of the
    fragment (``done`` / ``3 runs`` / ``not yet``).
    """
    theme = PROJECTS_THEME
    title = projects_title(shimmer_tick)
    status = project.get("status", "active")
    sub = Text(PAD + "  ")
    sub.append(project["name"], style=f"bold {theme.accent_bright}")
    sub.append("   ")
    sub.append(STATUS_WORDS.get(status, "In progress"), style=theme.good if status == "done" else theme.warn)
    sub.no_wrap = True
    sub.overflow = "ellipsis"

    body: list = []
    description = project.get("description", "")
    if description:
        for line in _wrap(description, width - 8, max_lines=3):
            body.append(Text(f"{PAD}{line}", style=theme.desc))
    else:
        body.append(Text(f"{PAD}No description yet.", style=theme.dim))
    body.append(Text(""))
    body.extend(_flow_strip(world=world, width=width, inside=inside))
    body.append(Text(""))
    body.append(Text(f"{PAD}Inside this project", style=theme.accent))
    if runs:
        table = Table(show_header=False, show_edge=False, box=None, padding=(0, 1), pad_edge=False, expand=True)
        table.add_column(ratio=2)
        table.add_column(ratio=5)
        table.add_column(width=10)
        for row in runs:
            table.add_row(
                _cell(f"{PAD}  {row.mode}", theme.value),
                _cell(row.title, theme.desc),
                _cell(row.last_modified[:10], theme.muted),
            )
        body.extend(render_to_lines(table, _inner_width(width)))
    else:
        body.append(Text(f"{PAD}  Nothing has run inside it yet. Start opens the menu scoped to it.", style=theme.dim))
    body.append(Text(""))
    body.append(build_key_hints([("d", "done / reopen"), ("a", "archive"), ("esc", "back")], pad=PAD))
    if message:
        body.append(Text(""))
        body.append(Text(f"{PAD}{message}", style=theme.accent))

    viewport = _viewport(body, height=height, scroll_offset=0, scroll_meta=None)
    btn_top, btn_mid, btn_bot = build_action_buttons(list(PROJECT_ACTIONS), action_sel)
    content = Group(Text(""), title, Text(""), sub, viewport, Text(""), btn_top, btn_mid, btn_bot)
    return build_page_panel(content, theme=theme, height=height)


def _build_draft_screen(
    draft: dict,
    *,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    message: str = "",
) -> Panel:
    """Build the new-project preview: the name and pitch a project would get.

    ``draft`` is ``projects.engine.draft_project_idea``'s shape
    (``name``, ``description``, ``source``, ``note``).
    """
    theme = PROJECTS_THEME
    title = projects_title(shimmer_tick)
    sub = build_reveal_subtitle("New project", sub_reveal, pad=PAD + "  ")

    body: list = [Text("")]
    name = Text(PAD)
    name.append(draft.get("name") or "untitled project", style=f"bold {theme.accent_bright}")
    body.append(name)
    body.append(Text(""))
    for line in _wrap(draft.get("description", ""), width - 8, max_lines=6):
        body.append(Text(f"{PAD}{line}", style=theme.value))
    body.append(Text(""))
    note = draft.get("note", "")
    if note:
        body.append(Text(f"{PAD}{note}", style=theme.good if draft.get("source") == "ai" else theme.dim))
    body.append(
        Text(f"{PAD}Create makes the project and opens it. AI rewrite gives it a pitch and a name.", style=theme.dim)
    )
    if message:
        body.append(Text(""))
        body.append(Text(f"{PAD}{message}", style=theme.accent))

    viewport = _viewport(body, height=height, scroll_offset=0, scroll_meta=None)
    btn_top, btn_mid, btn_bot = build_action_buttons(list(DRAFT_ACTIONS), action_sel)
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


def _build_session_rows(rows: list, selected: int, theme, width: int) -> list:
    if not rows:
        return [
            Text(""),
            Text(f"{PAD}Nothing has run inside this project yet.", style=theme.muted),
            Text(f"{PAD}Open it from the door and start a standup, a report or a review.", style=theme.dim),
        ]
    table = Table(show_header=True, show_edge=False, box=None, padding=(0, 1), pad_edge=False, expand=True)
    table.add_column(Text(f"{PAD}  mode", style=theme.muted), ratio=1)
    table.add_column(Text("title", style=theme.muted), ratio=3)
    table.add_column(Text("when", style=theme.muted), ratio=1)
    for i, row in enumerate(rows):
        is_selected = i == selected
        marker = "▸ " if is_selected else "  "
        style = f"bold {theme.accent_bright}" if is_selected else theme.value
        table.add_row(
            _cell(f"{PAD}{marker}{row.mode}", style),
            _cell(row.title, style if is_selected else theme.desc),
            _cell(row.last_modified[:10], theme.muted),
        )
    return render_to_lines(table, _inner_width(width))


def _build_project_sessions_screen(
    rows: list,
    *,
    project_name: str = "",
    selected: int = 0,
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
    """Build the project-sessions sub-page: one row per run, every mode.

    ``rows`` are ``sessions_recent.RecentSession``s (mode · title · when).
    Enter on a row opens its mode's saved-runs hub; planning and analysis
    rows have no hub and say so in ``message``.
    """
    theme = PROJECTS_THEME
    title = projects_title(shimmer_tick)
    sub = build_reveal_subtitle(f"Sessions in {project_name or 'this project'}", sub_reveal, pad=PAD + "  ")

    body: list = _build_session_rows(rows, selected, theme, width)
    body.append(Text(""))
    body.append(Text(f"{PAD}Enter opens the run's hub; planning and analysis open from their cards.", style=theme.dim))
    if message:
        body.append(Text(""))
        body.append(Text(f"{PAD}{message}", style=theme.accent))

    viewport = _viewport(body, height=height, scroll_offset=scroll_offset, scroll_meta=scroll_meta)
    btn_top, btn_mid, btn_bot = build_action_buttons(actions or list(SESSIONS_ACTIONS), action_sel)
    content = Group(Text(""), title, Text(""), sub, viewport, Text(""), btn_top, btn_mid, btn_bot)
    return build_page_panel(content, theme=theme, height=height)
