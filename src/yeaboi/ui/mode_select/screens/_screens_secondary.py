"""Secondary screen builders for mode selection: intake, offline, export, import, team analysis.

# See docs: "Architecture" — this module contains rendering functions
# for the intake mode selection, offline sub-menu, export success,
# import file path input, project export success, and team analysis screens.
# These are pure functions that return Rich Panel renderables — no I/O or state.
"""

from __future__ import annotations

import rich.box
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.mode_select.screens._analysis_sections import (
    _TA_CARDS,
    _measure_render_height,
    _ta_glossary_lines,
    _ta_insights,
    _ta_narrative_block,
    _ta_overview,
    _TaCtx,
)
from yeaboi.ui.mode_select.screens._screens import _INTAKE_CARDS, _OFFLINE_CARDS, _build_mode_row
from yeaboi.ui.shared._components import (
    ANALYSIS_THEME,
    PAD,
    build_action_buttons,
    build_progress_dots,
    build_scrollbar,
    calc_viewport,
    planning_title,
)
from yeaboi.ui.shared._scroll import publish_geometry

# ---------------------------------------------------------------------------
# Shared analysis review screen builder (mirrors planning mode layout)
# ---------------------------------------------------------------------------

_ANALYSIS_STAGES = ["Instructions", "Epic", "Stories", "Tasks", "Sprint"]


def _build_analysis_review_screen(
    body_lines: list,
    *,
    stage_index: int = 0,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    actions: list[str] | None = None,
    subtitle: str = "",
) -> Panel:
    """Shared screen builder for all analysis preview pages.

    Uses shared UI primitives (build_action_buttons, build_scrollbar,
    build_progress_dots, calc_viewport) for visual consistency.
    """
    from yeaboi.ui.shared._components import analysis_title

    _actions = actions or ["Accept", "Edit", "Regenerate", "Export"]
    title = analysis_title()
    progress = build_progress_dots(_ANALYSIS_STAGES, stage_index, theme=ANALYSIS_THEME)
    sub = Text(_PAD + subtitle, style="dim", justify="left")

    # ── Viewport (height-aware for line wrapping)
    viewport_h = calc_viewport(height, header_h=7, action_h=4)

    # Measure actual terminal height. Most pages pass Text, while the redesigned
    # Team Insights page also passes Rich panels and tables.
    _content_w = max(20, width - 7)
    _item_heights: list[int] = []
    _total_rendered = 0
    for bl in body_lines:
        h = max(1, _measure_render_height(bl, _content_w))
        _item_heights.append(h)
        _total_rendered += h

    # Find max scroll offset
    max_scroll = max(0, len(body_lines) - 1)
    _acc = 0
    for _ms in range(len(body_lines) - 1, -1, -1):
        _acc += _item_heights[_ms]
        if _acc >= viewport_h:
            max_scroll = _ms
            break
    else:
        max_scroll = 0
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)

    # Collect visible items
    visible: list = []
    _vis_h = 0
    for i in range(actual_scroll, len(body_lines)):
        ih = _item_heights[i]
        if _vis_h + ih > viewport_h:
            break
        visible.append(body_lines[i])
        _vis_h += ih

    # Scrollbar + content padding
    _sb_text = build_scrollbar(viewport_h, _total_rendered, actual_scroll, max_scroll)
    padded_lines: list = list(visible)
    for _i in range(max(0, viewport_h - _vis_h)):
        padded_lines.append(Text(""))

    # Action buttons
    btn_top, btn_mid, btn_bot = build_action_buttons(_actions, action_sel)

    # Build viewport with optional scrollbar
    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        progress,
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


_PAD = PAD  # alias for backward compatibility within this module


def _build_generate_confirm_screen(
    *,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    subtitle: str = "",
) -> Panel:
    """Confirmation screen shown between team/board analysis and ticket generation.

    Separates the two concerns: the user has just analysed the team/board and is
    now explicitly asked whether they want yeaboi to draft a sample epic/stories/
    tasks/sprint (which runs the LLM) — rather than the app assuming they do.

    Delegates to ``_build_analysis_review_screen`` so the layout (title, progress
    dots, viewport, action buttons) stays identical to the rest of analysis mode.
    """
    c_label = "bold white"
    c_body = "rgb(180,180,200)"
    c_bullet = "rgb(100,180,100)"
    c_muted = "rgb(120,120,140)"

    def _bullet(text: str) -> Text:
        t = Text(_PAD + "  ", justify="left")
        t.append("• ", style=c_bullet)
        t.append(text, style=c_body)
        return t

    # The question leads the body so it stays above the fold even on short
    # terminals, where the viewport shows only a few rows. The action buttons
    # below are always visible; the explanation and bullets follow.
    body_lines: list = [
        Text(""),
        Text(
            _PAD + "Analysis complete — generate sample tickets now?",
            style=c_label,
            justify="left",
        ),
        Text(""),
        Text(
            _PAD + "yeaboi can draft a sample set, calibrated to these patterns:",
            style=c_body,
            justify="left",
        ),
        _bullet("a sample epic"),
        _bullet("sample user stories"),
        _bullet("sample tasks"),
        _bullet("a sample sprint plan"),
        Text(""),
        Text(
            _PAD + "This runs the LLM. You can edit, regenerate, or export each step.",
            style=c_muted,
            justify="left",
        ),
    ]

    return _build_analysis_review_screen(
        body_lines,
        stage_index=0,
        width=width,
        height=height,
        action_sel=action_sel,
        actions=["Generate tickets", "Not now"],
        subtitle=subtitle,
    )


def _build_team_insights_screen(
    profile,
    *,
    examples: dict | None = None,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    subtitle: str = "",
) -> Panel:
    """Coaching insights screen shown between analysis results and ticket generation.

    Presents the AI's start/stop/keep/try advice for improving the team before
    the app suggests generating sample tickets. Delegates to
    ``_build_analysis_review_screen`` so the layout (title, progress dots,
    viewport, scrollbar, action buttons) stays identical to the rest of
    analysis mode.
    """
    body_lines: list = [
        Text(""),
        Text(PAD + "How to improve this team", style="bold white", justify="left"),
        Text(
            PAD + "Coaching insights grounded in the analysed sprints.",
            style="rgb(120,120,140)",
            justify="left",
        ),
    ]
    ctx = _TaCtx(width, examples)
    _ta_insights(ctx, profile)
    body_lines.extend(ctx.lines)

    return _build_analysis_review_screen(
        body_lines,
        stage_index=0,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        actions=["Continue", "Export", "Back"],
        subtitle=subtitle,
    )


def _build_team_analysis_screen(
    profile,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    export_sel: int = 0,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    team_name: str = "",
    view: str = "overview",
    selected_card: int = 0,
    actions: list[str] | None = None,
    shimmer_tick: float | None = None,
    anon_note: str = "",
    source_toggle: list[str] | None = None,
    active_source: str = "",
    comparison: list | None = None,
    source: str = "",
    project_key: str = "",
    code_signal=None,
    doc_signal=None,
) -> Panel:
    """Build the team analysis results screen (overview + section cards).

    ``view`` is ``"overview"`` (headline stats, AI executive summary and the
    selectable section-card list) or a ``_TA_CARDS`` key (a focused section
    detail view with its AI "What this means" narrative and jargon glossary).
    Section rendering lives in ``_analysis_sections.py``.

    In 'both' mode ``source_toggle`` (ordered tracker keys) renders a
    ``[ Jira ] Azure DevOps`` switch line under the header and ``comparison``
    (side-by-side headline rows) is shown atop the overview — the two trackers'
    figures stay clearly separate, never blended.
    """
    from yeaboi.tools.team_learning import compute_headline_stats

    # A delivery-off run (docs-only / code-only) has no TeamProfile; fall back to the
    # caller-supplied source/project and describe which components ran in the header.
    if profile is not None:
        src = profile.source
        key = profile.project_key
        sprints = profile.sample_sprints
        stories = profile.sample_stories
    else:
        src = source
        key = project_key

    # Build header: show team name for AzDO, board name for Jira
    board_label = key
    if team_name:
        board_label = f"{team_name} ({key})"
    if profile is not None:
        header_str = f"Team Analysis  ·  {src}/{board_label}  ·  {sprints} sprints  ·  {stories} stories"
    else:
        _ex = examples or {}
        bits = []
        if _ex.get("ai_adoption"):
            bits.append("code scan")
        if _ex.get("doc_quality"):
            bits.append("docs")
        header_str = f"Team Analysis  ·  {src}/{board_label}  ·  {' + '.join(bits) or 'components'} only"
    sub = Text(_PAD + header_str, style="bold white", justify="left")

    # 'Both'-mode source toggle line: highlight the active tracker.
    toggle_line: Text | None = None
    if source_toggle and len(source_toggle) > 1:
        _labels = {"jira": "Jira", "azdevops": "Azure DevOps"}
        toggle_line = Text(_PAD, justify="left")
        for i, s in enumerate(source_toggle):
            if i > 0:
                toggle_line.append("   ")
            lbl = _labels.get(s, s)
            if s == active_source:
                toggle_line.append(f"[ {lbl} ]", style="bold #22c55e")
            else:
                toggle_line.append(f"  {lbl}  ", style="dim")
        toggle_line.append("    (Tab: switch source)", style="rgb(90,90,110)")

    from yeaboi.ui.mode_select.screens._analysis_sections import visible_card_order

    stats = compute_headline_stats(profile, examples) if profile is not None else {}
    ctx = _TaCtx(width, examples, sprint_names=sprint_names, stats=stats)
    ctx.comparison = comparison
    # Code/Docs are GLOBAL scans passed in from the top-level result — feed them so
    # the two cards render regardless of the active delivery tracker. When viewing a
    # stored profile (no top-level signals) they come off the profile itself, where
    # the global scan was persisted.
    ctx.ai_sig = code_signal
    ctx.doc_sig = doc_signal
    _prof_ai = getattr(profile, "ai_adoption", None)
    _prof_doc = getattr(profile, "doc_quality", None)
    has_code = code_signal is not None or bool(_prof_ai and (_prof_ai.scanned_commits + _prof_ai.scanned_prs) > 0)
    has_docs = doc_signal is not None or bool(_prof_doc and _prof_doc.pages_scanned > 0)
    ctx.visible_order = visible_card_order(profile, has_code, has_docs)

    if view == "overview":
        crumb_text = "Overview  ·  ↑/↓ choose a section, Enter to open"
        _ta_overview(ctx, profile, selected_card)
        _actions = actions or ["Open", "Export", "Continue"]
    else:
        card = _TA_CARDS[view]
        crumb_text = f"Overview › {card['title']}"
        _ta_narrative_block(ctx, view)
        for build_section in card["builders"]:
            build_section(ctx, profile)
        _ta_glossary_lines(ctx, card["glossary"])
        _actions = actions or ["Back", "Export", "Continue"]

    # ── Layout matching planning mode ──────────────────────────────────
    from yeaboi.ui.shared._components import analysis_title

    title = analysis_title(shimmer_tick)

    btn_top, btn_mid, btn_bot = build_action_buttons(_actions, export_sel)
    if anon_note:  # anonymized: the crumb line carries the "N masked — review" indicator
        crumb_text = anon_note
    crumb = Text(_PAD + crumb_text, style="rgb(120,120,140)", justify="left")
    # The 'both'-mode toggle line adds one header row; shrink the viewport to match.
    body_h = calc_viewport(height, header_h=8 if toggle_line is not None else 7, action_h=4)

    # Scroll by renderable rather than pretending every item is one terminal row.
    # Dashboard tiles/tables/cards are atomic Rich renderables with measured heights.
    # Choose the earliest trailing item that still lets the bottom of the report fit.
    tail_h = 0
    max_scroll = max(0, len(ctx.lines) - 1)
    for i in range(len(ctx.lines) - 1, -1, -1):
        ih = ctx.item_heights[i] if i < len(ctx.item_heights) else 1
        if tail_h and tail_h + ih > body_h:
            break
        tail_h += ih
        max_scroll = i
    if view == "overview" and ctx.overview_card_rows:
        # ↑/↓ moves the card selection (not a free scroll) — keep the selected
        # card inside the viewport, including the AI-feature group separator.
        card_item = ctx.overview_card_rows[min(selected_card, len(ctx.overview_card_rows) - 1)]
        visible_h = sum(
            ctx.item_heights[i] if i < len(ctx.item_heights) else 1
            for i in range(min(scroll_offset, card_item), card_item + 1)
        )
        if card_item < scroll_offset or visible_h > body_h:
            scroll_offset = min(card_item, max_scroll)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, body_h)

    _vis_items: list = []
    _vis_h = 0
    for i in range(actual_scroll, len(ctx.lines)):
        ih = ctx.item_heights[i] if i < len(ctx.item_heights) else 1
        if _vis_h + ih > body_h:
            break
        _vis_items.append(ctx.lines[i])
        _vis_h += ih

    remaining = max(0, body_h - _vis_h)
    _sb_text = build_scrollbar(body_h, len(ctx.lines), actual_scroll, max_scroll)

    # Build viewport with optional scrollbar
    _body_group = Group(*_vis_items, *[Text("") for _ in range(remaining)])
    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(_body_group, _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = _body_group

    _header_items = [Text(""), title, Text(""), sub]
    if toggle_line is not None:
        _header_items.append(toggle_line)
    _header_items.append(crumb)
    content = Group(
        *_header_items,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


# Component picker — order + friendly labels. Each component runs over its OWN
# sub-sources (a ragged grid: different columns per row). ``_COMPONENT_LABELS`` keeps
# the "Name — description" form for back-compat; the picker splits it on the em dash.
_COMPONENT_KEYS: tuple[str, ...] = ("delivery", "code", "docs")
_COMPONENT_NAMES: dict[str, str] = {"delivery": "Delivery", "code": "Code", "docs": "Docs"}
_COMPONENT_DESCS: dict[str, str] = {
    "delivery": "velocity, calibration, contributors",
    "code": "remote AI-usage scan",
    "docs": "clarity + AI-likelihood",
}
_COMPONENT_LABELS: dict[str, str] = {k: f"{_COMPONENT_NAMES[k]} — {_COMPONENT_DESCS[k]}" for k in _COMPONENT_KEYS}
_SUBSOURCE_TITLES: dict[str, str] = {
    "jira": "Jira",
    "azdevops": "Azure DevOps",
    "github": "GitHub",
    "azdo": "Azure Repos",
    "confluence": "Confluence",
    "notion": "Notion",
}


def _build_component_select_screen(
    grid: dict[str, list[str]],
    rows_order: list[str],
    checked: dict[str, set[int]],
    row_idx: int,
    col_idx: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
) -> Panel:
    """Ragged component × sub-source multi-select.

    ``grid`` maps each component to its CONFIGURED sub-sources (delivery ←
    jira/azdevops, code ← github/azdo, docs ← confluence/notion). ``rows_order`` is
    the components with at least one sub-source. ``checked`` maps component → set of
    selected sub-source indices. ``row_idx``/``col_idx`` locate the focused cell."""
    from yeaboi.ui.shared._components import analysis_title

    theme = ANALYSIS_THEME
    title = analysis_title()
    sub = Text(_PAD + "Choose what to analyse — each part scans its own sources", style="bold white", justify="left")
    crumb = Text(
        _PAD + "↑/↓ · ←/→ · Space toggle · Enter continue · Esc cancel",
        style="rgb(120,120,140)",
        justify="left",
    )

    rule_w = min(max(20, width - len(_PAD) - 4), 40)
    cell_w = 20  # fixed column width so the second source lines up across rows

    lines: list = []
    if message:
        lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))
        lines.append(Text(""))

    per_component: list[tuple[str, int]] = []
    total_selected = 0
    for ci, ckey in enumerate(rows_order):
        subs = grid.get(ckey, [])
        focused_row = ci == row_idx
        # Header: NAME · description (name brighter when this row is focused).
        header = Text(_PAD + "  ", justify="left")
        header.append(
            _COMPONENT_NAMES.get(ckey, ckey).upper(),
            style=f"bold {theme.accent_bright if focused_row else theme.accent}",
        )
        header.append(f"  ·  {_COMPONENT_DESCS.get(ckey, '')}", style=theme.dim)
        lines.append(header)
        lines.append(Text(_PAD + "  " + "─" * rule_w, style=theme.sep))

        n_checked = 0
        boxline = Text(_PAD + "  ", justify="left")
        for si, s in enumerate(subs):
            is_focused = focused_row and si == col_idx
            is_checked = si in checked.get(ckey, set())
            if is_checked:
                n_checked += 1
                total_selected += 1
            dot = "●" if is_checked else "○"
            name = _SUBSOURCE_TITLES.get(s, s)
            if is_focused:
                boxline.append("‹ ", style=theme.accent_bright)
                boxline.append(dot, style=theme.accent_bright)
                boxline.append(f" {name} ", style="bold white")
                boxline.append("›", style=theme.accent_bright)
                vis = 2 + 1 + 1 + len(name) + 1 + 1  # "‹ " + dot + " name " + "›"
            else:
                boxline.append("  ")
                boxline.append(dot, style=theme.accent_bright if is_checked else theme.dim)
                boxline.append(f" {name}", style=theme.accent if is_checked else theme.dim)
                vis = 2 + 1 + 1 + len(name)
            boxline.append(" " * max(2, cell_w - vis))
        lines.append(boxline)
        lines.append(Text(""))
        per_component.append((_COMPONENT_NAMES.get(ckey, ckey), n_checked))

    # Status footer: total + per-component counts (or the at-least-one guard).
    footer = Text(_PAD + "  ", justify="left")
    if total_selected:
        footer.append(f"{total_selected} sources", style=theme.accent_bright)
        footer.append("  ·  " + "  ·  ".join(f"{nm} {n}" for nm, n in per_component), style=theme.muted)
        footer.append("     Enter ⏎", style=theme.dim)
    else:
        footer.append("Select at least one source to analyse", style=theme.accent_bright)
    lines.append(footer)

    viewport_h = calc_viewport(height, header_h=7, action_h=2)
    padded = list(lines[:viewport_h])
    for _ in range(max(0, viewport_h - len(padded))):
        padded.append(Text(""))

    content = Group(Text(""), title, Text(""), sub, crumb, Text(""), Group(*padded))
    return Panel(content, border_style="white", box=rich.box.ROUNDED, expand=True, height=height, padding=(1, 2))


def _build_analysis_depth_screen(selected: int = 0, *, width: int = 80, height: int = 24) -> Panel:
    """Choose Quick (zero LLM calls) or Deep (cached AI enrichment)."""
    from yeaboi.ui.shared._components import analysis_title

    theme = ANALYSIS_THEME
    options = (
        (
            "QUICK",
            "Recommended · fastest",
            "Computed metrics, deterministic summaries and coaching. No LLM wait.",
        ),
        (
            "DEEP",
            "Richer AI enrichment",
            "Classifies ticket structure and writes AI explanations. Cached, but slower.",
        ),
    )
    lines: list[Text] = []
    for idx, (name, label, detail) in enumerate(options):
        focused = idx == selected
        line = Text(_PAD + "  ", justify="left")
        line.append("› " if focused else "  ", style=theme.accent_bright)
        line.append(name, style=f"bold {theme.accent_bright if focused else theme.accent}")
        line.append(f"  {label}", style="bold white" if focused else theme.muted)
        lines.append(line)
        lines.append(Text(_PAD + "    " + detail, style=theme.dim, justify="left"))
        lines.append(Text(""))

    content = Group(
        Text(""),
        analysis_title(),
        Text(""),
        Text(_PAD + "Choose analysis depth", style="bold white", justify="left"),
        Text(_PAD + "↑/↓ or ←/→ · Enter continue · Esc cancel", style=theme.muted, justify="left"),
        Text(""),
        Group(*lines),
    )
    return Panel(content, border_style="white", box=rich.box.ROUNDED, expand=True, height=height, padding=(1, 2))


def _build_member_select_screen(
    roster: list[str],
    checked: set[int],
    cursor: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
) -> Panel:
    """Roster multi-select with an explicit checked state for every member."""
    from yeaboi.ui.shared._components import analysis_title

    theme = ANALYSIS_THEME
    title = analysis_title()
    sub = Text(_PAD + "Choose who to include in the analysis", style="bold white", justify="left")
    crumb = Text(
        _PAD + "↑/↓ move · Space toggle · A toggle all · Enter run · Esc cancel",
        style="rgb(120,120,140)",
        justify="left",
    )

    rows: list = []
    if message:
        rows.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))
        rows.append(Text(""))
    n_checked = len(checked)
    scope = f"{n_checked} of {len(roster)} selected"
    rows.append(Text(_PAD + f"  Space to toggle · A select/deselect all · {scope}", style=theme.muted))
    rows.append(Text(""))
    for idx, name in enumerate(roster):
        is_cursor = idx == cursor
        is_checked = idx in checked
        dot = "●" if is_checked else "○"
        row = Text(_PAD + "  ", justify="left")
        if is_cursor:
            row.append("‹ ", style=theme.accent_bright)
            row.append(dot, style=theme.accent_bright if is_checked else theme.dim)
            row.append(f" {name} ", style="bold white")
            row.append("›", style=theme.accent_bright)
        else:
            row.append("  ")
            row.append(dot, style=theme.accent_bright if is_checked else theme.dim)
            row.append(f" {name}", style=theme.accent if is_checked else theme.desc)
        rows.append(row)
    if not roster:
        rows.append(Text(_PAD + "  No members found — the analysis will cover the whole team.", style=theme.muted))

    viewport_h = calc_viewport(height, header_h=6, action_h=2)
    total = len(rows)
    cursor_line = min(total - 1, cursor + 2) if roster else 0
    max_scroll = max(0, total - viewport_h)
    start = 0 if total <= viewport_h else max(0, min(cursor_line - viewport_h // 2, max_scroll))
    visible = rows[start : start + viewport_h]
    _sb = build_scrollbar(viewport_h, total, start, max_scroll, always_show=True)
    padded = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded.append(Text(""))
    if _sb is not None:
        from rich.table import Table as _SbTable

        _vp = _SbTable(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        _vp.add_column(ratio=1)
        _vp.add_column(width=1)
        _vp.add_row(Group(*padded), _sb)
        viewport_renderable = _vp
    else:
        viewport_renderable = Group(*padded)

    content = Group(Text(""), title, Text(""), sub, crumb, Text(""), viewport_renderable)
    return Panel(content, border_style="white", box=rich.box.ROUNDED, expand=True, height=height, padding=(1, 2))


def _build_instructions_review_screen(
    instructions_text: str,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    editing: bool = False,
) -> Panel:
    """Build the planning instructions review screen using shared layout."""
    import re as _re

    c_section = "bold #22c55e"
    c_subsection = "bold rgb(180,200,220)"
    c_label = "bold white"
    c_value = "rgb(180,180,200)"
    c_muted = "rgb(120,120,140)"
    c_accent = "rgb(100,180,100)"
    c_warn = "rgb(220,180,60)"
    c_arrow = "rgb(100,180,220)"
    c_sep = "rgb(50,60,80)"
    c_dim = "rgb(80,80,100)"

    body_lines: list = []
    wrap_w = max(40, width - len(_PAD) - 14)

    def _wrap_append(text: str, style: str, indent: str = "    ") -> None:
        """Word-wrap text into body_lines."""
        # Strip markdown bold markers for display
        text = _re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
        words = text.split()
        buf = ""
        for word in words:
            if buf and len(buf) + len(word) + 1 > wrap_w:
                body_lines.append(Text(_PAD + indent + buf, style=style, justify="left"))
                buf = word
            else:
                buf = (buf + " " + word).strip()
        if buf:
            body_lines.append(Text(_PAD + indent + buf, style=style, justify="left"))

    def _styled_bullet(text: str) -> None:
        """Parse a markdown bullet line into styled Rich Text."""
        # Strip leading "- "
        text = text.strip()
        if text.startswith("- "):
            text = text[2:].strip()

        # Strip markdown bold from entire text for processing
        clean = _re.sub(r"\*\*([^*]+)\*\*", r"\1", text)

        # Pattern: "**N pt**: description" — point calibration
        pt_match = _re.match(r"(\d+)\s*pt\b[s]?[*]*:\s*(.*)", clean)
        if pt_match:
            pts, desc = pt_match.group(1), pt_match.group(2)
            row = Text(_PAD + "    ", justify="left")
            row.append(f"{pts} pt", style=f"bold {c_accent}")
            row.append("  ", style=c_dim)
            # Wrap long descriptions
            if len(desc) > wrap_w - 10:
                row.append(desc[: wrap_w - 10], style=c_value)
                body_lines.append(row)
                _wrap_append(desc[wrap_w - 10 :], c_value, indent="          ")
            else:
                row.append(desc, style=c_value)
                body_lines.append(row)
            return

        # Pattern: "**label** stories: stats" — discipline shape
        disc_match = _re.match(r"(\w[\w\-]*)\s+stories:\s*(.*)", clean)
        if disc_match:
            disc, stats = disc_match.group(1), disc_match.group(2)
            row = Text(_PAD + "    ", justify="left")
            row.append(f"{disc:<16s}", style=c_label)
            row.append(stats, style=c_muted)
            body_lines.append(row)
            return

        # Pattern: "label — value" or "label: value"
        for sep in [" — ", "\u2014", ": "]:
            if sep in clean:
                parts = clean.split(sep, 1)
                lbl, val = parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""
                row = Text(_PAD + "    ", justify="left")
                row.append(lbl, style=c_label)
                if val:
                    row.append(f"  {val}", style=c_value)
                body_lines.append(row)
                return

        # Fallback: plain bullet
        _wrap_append(clean, c_value)

    for line in instructions_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # ## Section header
        if stripped.startswith("## "):
            body_lines.append(Text(""))
            title_text = stripped.lstrip("#").strip().rstrip(":")
            body_lines.append(Text(_PAD + "  " + title_text, style=c_section, justify="left"))
            body_lines.append(Text(_PAD + "  " + "\u2500" * min(len(title_text), 40), style=c_sep, justify="left"))
            continue

        # ### Subsection header
        if stripped.startswith("### "):
            body_lines.append(Text(""))
            body_lines.append(
                Text(_PAD + "  " + stripped.lstrip("#").strip().rstrip(":"), style=c_subsection, justify="left")
            )
            continue

        # → Arrow directives
        if stripped.startswith("\u2192") or stripped.startswith("→"):
            clean = _re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
            body_lines.append(Text(_PAD + "      " + clean, style=f"bold {c_arrow}", justify="left"))
            continue

        # Bullet items
        if stripped.startswith("- "):
            _styled_bullet(stripped)
            continue

        # Standalone key: value lines (e.g. "Velocity: 14 ± 7")
        if ":" in stripped and not stripped.startswith("Weight"):
            clean = _re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
            k, _, v = clean.partition(":")
            row = Text(_PAD + "  ", justify="left")
            row.append(k.strip(), style=f"bold {c_warn}")
            if v.strip():
                row.append(": " + v.strip(), style=c_value)
            body_lines.append(row)
            continue

        # Fallback: plain text
        clean = _re.sub(r"\*\*([^*]+)\*\*", r"\1", stripped)
        _wrap_append(clean, c_muted)

    return _build_analysis_review_screen(
        body_lines,
        stage_index=0,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        actions=["Accept", "Edit", "Export"],
        subtitle="Review planning instructions",
    )


def _build_sample_epic_screen(
    epic: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    examples: dict | None = None,
) -> Panel:
    """Build the sample epic review screen matching planning mode's feature display.

    Shows the generated epic card with description sections properly parsed,
    followed by a compact "why this matches" rationale and pattern summary.
    """
    c_accent = "#22c55e"
    c_muted = "rgb(120,120,140)"
    c_value = "bold white"
    c_id = "cyan"
    c_desc = "rgb(160,160,160)"
    c_sep = "rgb(40,40,50)"
    c_section = f"bold {c_accent}"
    c_label = "rgb(220,180,60)"
    c_dim = "dim"

    _ex = examples or {}
    body_lines: list = []
    wrap_w = max(40, width - len(_PAD) - 14)

    def _wrap_text(text: str, style: str, indent: str = "      ") -> None:
        """Word-wrap text into body_lines with given style and indent."""
        words = text.split()
        line_buf = ""
        for word in words:
            if line_buf and len(line_buf) + len(word) + 1 > wrap_w:
                body_lines.append(Text(_PAD + indent + line_buf, style=style, justify="left"))
                line_buf = word
            else:
                line_buf = (line_buf + " " + word).strip()
        if line_buf:
            body_lines.append(Text(_PAD + indent + line_buf, style=style, justify="left"))

    # ── Epic Header ───────────────────────────────────────────────
    title = epic.get("title", "Sample Epic")
    priority = epic.get("priority", "high")
    _prio_colors = {"critical": "bold red", "high": "yellow", "medium": "rgb(70,100,180)", "low": "dim"}
    _prio_style = _prio_colors.get(priority, "yellow")

    hdr = Text(_PAD + "  ", justify="left")
    hdr.append("[F1]", style=c_id)
    hdr.append("  \u00b7  ", style=c_dim)
    hdr.append(title, style=c_value)
    hdr.append("  \u00b7  ", style=c_dim)
    hdr.append(priority, style=_prio_style)
    body_lines.append(hdr)

    # Metadata line
    stories_est = epic.get("stories_estimate", 0)
    points_est = epic.get("points_estimate", 0)
    meta = Text(_PAD + "  ", justify="left")
    meta.append(f"~{stories_est} stories", style=c_muted)
    meta.append("  \u00b7  ", style=c_dim)
    meta.append(f"~{points_est} story points", style=c_muted)
    body_lines.append(meta)
    body_lines.append(Text(_PAD + "  " + "\u2500" * min(40, wrap_w), style=c_sep, justify="left"))
    body_lines.append(Text(""))

    # ── Description — parse section markers into styled blocks ──
    desc = epic.get("description", "")
    if desc:
        import re as _re

        # Try **Bold** markers first, then ## Heading markers
        _section_re = _re.compile(r"\*\*([^*]+)\*\*\s*")
        parts = _section_re.split(desc)
        if len(parts) <= 2:
            # No **bold** markers — try ## heading markers
            _heading_re = _re.compile(r"#{1,3}\s+([^\n?]+\??)\s*")
            parts = _heading_re.split(desc)

        if len(parts) > 2:
            # parts = [text_before, section_title, section_body, title2, body2, ...]
            if parts[0].strip():
                _wrap_text(parts[0].strip(), c_desc, indent="    ")
                body_lines.append(Text(""))

            i = 1
            while i < len(parts) - 1:
                section_title = parts[i].strip().rstrip("?")
                section_body = parts[i + 1].strip() if i + 1 < len(parts) else ""
                body_lines.append(Text(_PAD + "    " + section_title, style=f"bold {c_label}", justify="left"))
                if section_body:
                    _wrap_text(section_body, c_desc, indent="    ")
                body_lines.append(Text(""))
                i += 2
        else:
            # No section markers at all — show raw description
            _wrap_text(desc, c_desc, indent="    ")
            body_lines.append(Text(""))
    else:
        body_lines.append(Text(_PAD + "    No description provided.", style=c_muted, justify="left"))
        body_lines.append(Text(""))

    # ── Rationale ─────────────────────────────────────────────────
    rationale = epic.get("rationale", "")
    if rationale:
        body_lines.append(Text(_PAD + "  Why this matches your team", style=c_section, justify="left"))
        _wrap_text(rationale, c_muted, indent="    ")
        body_lines.append(Text(""))

    # ── Pattern Summary (compact) ─────────────────────────────────
    _naming = _ex.get("naming_conventions", {})
    _epic_style = _naming.get("epic_naming_style", "")
    _epic_ex = _naming.get("epic_examples", [])

    if _epic_style or _epic_ex:
        body_lines.append(Text(_PAD + "  Team Patterns", style=c_section, justify="left"))
        if _epic_style:
            row = Text(_PAD + "    ", justify="left")
            row.append("Naming: ", style=c_dim)
            row.append(_epic_style, style=c_muted)
            body_lines.append(row)
        if _epic_ex:
            row = Text(_PAD + "    ", justify="left")
            row.append("Examples: ", style=c_dim)
            row.append(", ".join(f'"{e}"' for e in _epic_ex[:3]), style=c_muted)
            body_lines.append(row)

    return _build_analysis_review_screen(
        body_lines,
        stage_index=1,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        subtitle="Does this epic match your team's style?",
    )


def _build_sample_stories_screen(
    stories: list[dict],
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    epic_title: str = "",
    examples: dict | None = None,
) -> Panel:
    """Build the sample stories review screen matching planning mode's story cards."""
    c_accent = "#22c55e"
    c_id = "cyan"
    c_muted = "rgb(120,120,140)"
    c_desc = "rgb(160,160,160)"
    c_sep = "rgb(40,40,50)"
    c_section = f"bold {c_accent}"
    c_given = "rgb(100,180,100)"
    c_when = "rgb(220,180,60)"
    c_then = "rgb(100,140,220)"
    _prio_colors = {
        "critical": "bold red",
        "high": "yellow",
        "medium": "rgb(70,100,180)",
        "low": "dim",
    }

    body_lines: list = []
    max_w = max(40, width - len(_PAD) - 12)

    # Pattern breakdown
    body_lines.append(Text(_PAD + "  Story Design Patterns", style=c_section, justify="left"))
    if epic_title:
        body_lines.append(Text(_PAD + f"    Epic: {epic_title}", style=c_muted, justify="left"))
    body_lines.append(
        Text(
            _PAD + f"    {len(stories)} sample stories generated",
            style=c_muted,
            justify="left",
        )
    )
    body_lines.append(Text(""))
    body_lines.append(Text(_PAD + "  " + "\u2500" * 36, style=c_sep, justify="left"))
    body_lines.append(Text(""))

    # Story cards
    for idx, story in enumerate(stories):
        sid = story.get("id", f"S{idx + 1}")
        title = story.get("title", "")
        pts = story.get("story_points", 3)
        priority = story.get("priority", "medium")
        discipline = story.get("discipline", "fullstack")
        persona = story.get("persona", "user")
        goal = story.get("goal", "")
        benefit = story.get("benefit", "")

        # Header: S1 · 3 pts · high · infrastructure
        hdr = Text(_PAD + "  ", justify="left")
        hdr.append(sid, style=c_id)
        hdr.append("  \u00b7  ", style="dim")
        hdr.append(f"{pts} pts", style="dim")
        hdr.append("  \u00b7  ", style="dim")
        hdr.append(priority, style=_prio_colors.get(priority, "yellow"))
        hdr.append("  \u00b7  ", style="dim")
        hdr.append(discipline, style="dim")
        body_lines.append(hdr)

        if title:
            body_lines.append(Text(_PAD + f"    {title}", style="bold white", justify="left"))

        # Description
        body_lines.append(Text(_PAD + "    Description", style=f"bold {c_muted}", justify="left"))
        story_text = f"As a {persona}, I want to {goal}, so that {benefit}."
        words = story_text.split()
        buf = ""
        for word in words:
            if len(buf) + len(word) + 1 > max_w:
                body_lines.append(Text(_PAD + "      " + buf, style=c_desc, justify="left"))
                buf = word
            else:
                buf = (buf + " " + word).strip()
        if buf:
            body_lines.append(Text(_PAD + "      " + buf, style=c_desc, justify="left"))

        # Acceptance Criteria
        acs = story.get("acceptance_criteria", [])
        if acs:
            body_lines.append(Text(""))
            body_lines.append(Text(_PAD + "    Acceptance Criteria", style=f"bold {c_muted}", justify="left"))
            for ac in acs[:3]:
                if isinstance(ac, dict):
                    for kw, style in [("given", c_given), ("when", c_when), ("then", c_then)]:
                        val = ac.get(kw, "")
                        if val:
                            row = Text(_PAD + "      ", justify="left")
                            row.append(f"{kw.capitalize():5s} ", style=f"bold {style}")
                            row.append(val, style=c_desc)
                            body_lines.append(row)
                    body_lines.append(Text(""))

        # Definition of Done — from LLM response, or fall back to team's proposed DoD
        dod = story.get("definition_of_done", [])
        if not dod and examples:
            proposed = examples.get("proposed_dod", {})
            if isinstance(proposed, dict):
                dod = [
                    it["practice"]
                    for it in proposed.get("items", [])
                    if isinstance(it, dict) and it.get("status") in ("established", "emerging")
                ]
        if dod:
            body_lines.append(Text(_PAD + "    Definition of Done", style=f"bold {c_muted}", justify="left"))
            for item in dod:
                row = Text(_PAD + "      ", justify="left")
                row.append("\u2713 ", style="rgb(80,180,80)")
                row.append(str(item), style=c_desc)
                body_lines.append(row)
            body_lines.append(Text(""))

        if idx < len(stories) - 1:
            body_lines.append(Text(_PAD + "  " + "\u2500" * 36, style=c_sep, justify="left"))
            body_lines.append(Text(""))

    return _build_analysis_review_screen(
        body_lines,
        stage_index=2,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        subtitle="Do these stories match your team's style?",
    )


def _build_sample_tasks_screen(
    tasks: list[dict],
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    stories: list[dict] | None = None,
) -> Panel:
    """Build the sample tasks review screen matching planning mode's task display."""
    c_accent = "#22c55e"
    c_id = "cyan"
    c_muted = "rgb(120,120,140)"
    c_desc = "rgb(160,160,160)"
    c_sep = "rgb(40,40,50)"
    c_section = f"bold {c_accent}"
    _label_colors = {
        "code": "rgb(100,140,220)",
        "testing": "rgb(220,180,60)",
        "documentation": "rgb(160,100,220)",
        "infrastructure": "rgb(100,180,100)",
    }

    body_lines: list = []
    max_w = max(40, width - len(_PAD) - 12)

    # Group tasks by story
    _by_story: dict[str, list[dict]] = {}
    for t in tasks:
        sid = t.get("story_id", "?")
        _by_story.setdefault(sid, []).append(t)

    # Pattern breakdown
    body_lines.append(
        Text(
            _PAD + "  Task Decomposition Preview",
            style=c_section,
            justify="left",
        )
    )
    body_lines.append(
        Text(
            _PAD + f"    {len(tasks)} tasks across {len(_by_story)} stories",
            style=c_muted,
            justify="left",
        )
    )
    body_lines.append(Text(""))
    body_lines.append(Text(_PAD + "  " + "\u2500" * 36, style=c_sep, justify="left"))
    body_lines.append(Text(""))

    # Render tasks grouped by story
    # Build story title lookup
    _story_titles: dict[str, str] = {}
    if stories:
        for s in stories:
            _story_titles[s.get("id", "")] = s.get("title", "")

    for s_idx, (sid, story_tasks) in enumerate(_by_story.items()):
        # Story header with title
        hdr = Text(_PAD + "  ", justify="left")
        hdr.append(sid, style=f"bold {c_id}")
        story_title = _story_titles.get(sid, "")
        if story_title:
            hdr.append(f"  {story_title}", style="bold white")
        hdr.append(f"  ({len(story_tasks)} tasks)", style="dim")
        body_lines.append(hdr)
        body_lines.append(Text(""))

        for t in story_tasks:
            tid = t.get("id", "T-?")
            title = t.get("title", "")
            label = t.get("label", "Code")
            desc = t.get("description", "")
            test_plan = t.get("test_plan", "")
            label_sty = _label_colors.get(label.lower(), c_muted)

            # Task header: T-S1-01 · [Code] · Title
            row = Text(_PAD + "    ", justify="left")
            row.append(tid, style=c_id)
            row.append("  ", style="dim")
            row.append(f"[{label}]", style=label_sty)
            row.append("  ", style="dim")
            row.append(title, style="bold white")
            body_lines.append(row)

            # Description (wrapped)
            if desc:
                words = desc.split()
                buf = ""
                for word in words:
                    if len(buf) + len(word) + 1 > max_w:
                        body_lines.append(
                            Text(
                                _PAD + "         " + buf,
                                style=c_desc,
                                justify="left",
                            )
                        )
                        buf = word
                    else:
                        buf = (buf + " " + word).strip()
                if buf:
                    body_lines.append(
                        Text(
                            _PAD + "         " + buf,
                            style=c_desc,
                            justify="left",
                        )
                    )

            # Test plan
            if test_plan:
                tp_row = Text(_PAD + "         ", justify="left")
                tp_row.append("Test: ", style="bold rgb(220,180,60)")
                tp_row.append(test_plan[:60], style=c_desc)
                body_lines.append(tp_row)

            body_lines.append(Text(""))

        if s_idx < len(_by_story) - 1:
            body_lines.append(
                Text(
                    _PAD + "  " + "\u2500" * 36,
                    style=c_sep,
                    justify="left",
                )
            )
            body_lines.append(Text(""))

    return _build_analysis_review_screen(
        body_lines,
        stage_index=3,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        subtitle="Do these tasks match your team's decomposition style?",
    )


def _build_sample_sprint_screen(
    sprint: dict,
    stories: list[dict],
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
) -> Panel:
    """Build the sample sprint plan review screen."""
    c_accent = "#22c55e"
    c_muted = "rgb(120,120,140)"
    c_desc = "rgb(160,160,160)"
    c_sep = "rgb(40,40,50)"
    c_section = f"bold {c_accent}"
    c_standalone = "rgb(220,180,60)"

    body_lines: list = []
    max_w = max(40, width - len(_PAD) - 12)

    # Sprint header
    sprint_name = sprint.get("sprint_name", "Sprint 1")
    vel_target = sprint.get("velocity_target", 0)
    total_pts = sprint.get("total_points", 0)

    body_lines.append(
        Text(
            _PAD + "  Sprint Plan Preview",
            style=c_section,
            justify="left",
        )
    )
    body_lines.append(Text(""))

    # Sprint card
    hdr = Text(_PAD + "  ", justify="left")
    hdr.append(sprint_name, style="bold white")
    hdr.append(f"  \u00b7  {total_pts} pts", style=c_muted)
    hdr.append(f"  \u00b7  capacity {vel_target} pts", style=c_muted)
    body_lines.append(hdr)
    body_lines.append(Text(""))

    # Capacity notes
    cap_notes = sprint.get("capacity_notes", "")
    if cap_notes:
        body_lines.append(
            Text(
                _PAD + f"    {cap_notes}",
                style=c_standalone,
                justify="left",
            )
        )
        body_lines.append(Text(""))

    # Stories in sprint
    included = sprint.get("stories_included", [])
    if included:
        body_lines.append(
            Text(
                _PAD + "  Stories included:",
                style=f"bold {c_muted}",
                justify="left",
            )
        )
        for sid in included:
            # Find matching story
            story = next((s for s in stories if s.get("id") == sid), None)
            row = Text(_PAD + "    ", justify="left")
            row.append(sid, style="cyan")
            if story:
                row.append(f"  {story.get('title', '')}  ", style="white")
                row.append(f"{story.get('story_points', '?')} pts", style="dim")
            body_lines.append(row)
        body_lines.append(Text(""))

    # Utilisation
    if vel_target > 0 and total_pts > 0:
        util_pct = round(total_pts / vel_target * 100)
        util_style = c_accent if 70 <= util_pct <= 90 else (c_standalone if util_pct < 70 else "bold red")
        body_lines.append(
            Text(
                _PAD + f"  Sprint utilisation: {util_pct}%",
                style=util_style,
                justify="left",
            )
        )
        body_lines.append(Text(""))

    # Risks
    risks = sprint.get("risks", [])
    if risks:
        body_lines.append(Text(_PAD + "  " + "\u2500" * 36, style=c_sep, justify="left"))
        body_lines.append(Text(""))
        body_lines.append(Text(_PAD + "  Risks:", style=f"bold {c_standalone}", justify="left"))
        for risk in risks[:5]:
            body_lines.append(Text(_PAD + f"    \u26a0 {risk}", style=c_desc, justify="left"))
        body_lines.append(Text(""))

    # Rationale
    rationale = sprint.get("rationale", "")
    if rationale:
        body_lines.append(Text(_PAD + "  " + "\u2500" * 36, style=c_sep, justify="left"))
        body_lines.append(Text(""))
        body_lines.append(
            Text(
                _PAD + "  Why this sprint plan matches your team",
                style=f"bold {c_muted}",
                justify="left",
            )
        )
        words = rationale.split()
        buf = ""
        for word in words:
            if len(buf) + len(word) + 1 > max_w:
                body_lines.append(Text(_PAD + "    " + buf, style=c_desc, justify="left"))
                buf = word
            else:
                buf = (buf + " " + word).strip()
        if buf:
            body_lines.append(Text(_PAD + "    " + buf, style=c_desc, justify="left"))

    return _build_analysis_review_screen(
        body_lines,
        stage_index=4,
        scroll_offset=scroll_offset,
        scroll_meta=scroll_meta,
        width=width,
        height=height,
        action_sel=action_sel,
        actions=["Done", "Regenerate", "Export"],
        subtitle="Does this sprint plan match your team's capacity?",
    )


def _build_intake_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible_items: int = -1,
) -> Panel:
    """Build the intake mode selection screen with Planning title pinned at top.

    Shown after the user selects '+ New Project' on the project list.
    Uses the same ASCII art + shimmer + typewriter pattern as the top-level mode screen.
    visible_items: how many intake options to show (-1 = all). For staggered fade-in.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Select intake mode", style="dim", justify="left")

    # Intake option rows — same rendering as mode rows
    show_n = len(_INTAKE_CARDS) if visible_items < 0 else min(visible_items, len(_INTAKE_CARDS))
    body: list = []
    body_h = 0

    for i in range(show_n):
        card = _INTAKE_CARDS[i]
        is_sel = i == selected
        items = _build_mode_row(
            card,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show_n - 1:
            body.append(Text(""))
            body_h += 1

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_offline_screen(
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    visible_items: int = -1,
) -> Panel:
    """Build the offline sub-menu screen with Planning title pinned at top.

    Shown after the user selects 'Offline' on the intake screen.
    Uses the same ASCII art + shimmer + typewriter pattern as the intake mode screen.
    visible_items: how many offline options to show (-1 = all). For staggered reveal.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Offline questionnaire", style="dim", justify="left")

    # Offline option rows — same rendering as mode rows
    show_n = len(_OFFLINE_CARDS) if visible_items < 0 else min(visible_items, len(_OFFLINE_CARDS))
    body: list = []
    body_h = 0

    for i in range(show_n):
        card = _OFFLINE_CARDS[i]
        is_sel = i == selected
        items = _build_mode_row(
            card,
            selected=is_sel,
            shimmer_tick=shimmer_tick,
            desc_reveal=desc_reveal if is_sel else 0,
        )
        body.extend(items)
        body_h += 2 + (2 if is_sel else 0)
        if i < show_n - 1:
            body.append(Text(""))
            body_h += 1

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_export_success_screen(
    file_path: str,
    *,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Build the export success screen with Planning title pinned at top.

    Shown after a blank questionnaire template is exported.
    Displays confirmation, file path, and a hint to re-run the agent.
    """
    # Planning title pinned at top
    title = planning_title()

    # Success message body
    body: list = []
    body.append(Text(_PAD + "Questionnaire exported", style="bold bright_green", justify="left"))
    body.append(Text(""))
    body.append(Text(_PAD + f"Saved to: {file_path}", style="white", justify="left"))
    body.append(Text(""))
    body.append(
        Text(
            _PAD + "Fill it in at your own pace, then re-run the agent and select Import.",
            style="dim",
            justify="left",
        )
    )
    body.append(Text(""))
    body.append(Text(_PAD + "Press any key to exit.", style="dim", justify="left"))
    body_h = 7

    # Layout: blank + title(2) + blank + [body]
    inner_h = height - 4
    header_h = 4  # blank + title(2) + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_import_screen(
    input_value: str,
    *,
    width: int = 80,
    height: int = 24,
    error: str = "",
    placeholder: str = "scrum-questionnaire.md",
) -> Panel:
    """Build the import file path input screen with Planning title pinned at top.

    Shown when the user selects 'Import' from the offline sub-menu.
    Same text input pattern as provider_select.py API key input.
    """
    # Planning title pinned at top
    title = planning_title()

    sub = Text(_PAD + "Import questionnaire", style="dim", justify="left")

    # Input box
    box_w = min(70, width - 16)
    box_inner_w = box_w - 2 - 4  # panel border(2) + padding(4)

    if input_value:
        display = input_value + "\u2588"
        text_style = "bold white"
    else:
        display = placeholder + "\u2588"
        text_style = "rgb(80,80,80)"

    avail = box_inner_w - 4
    input_content = Text(justify="left", no_wrap=True, overflow="crop")
    if len(display) <= avail:
        input_content.append("  " + display, style=text_style)
    else:
        visible = display[-(avail - 1) :]
        input_content.append(" \u25c2", style="dim")
        input_content.append(visible, style=text_style)

    if error:
        border_color = "bright_red"
    else:
        border_color = "white"

    input_box = Panel(
        input_content,
        title=" File path ",
        title_align="left",
        border_style=border_color,
        box=rich.box.ROUNDED,
        padding=(1, 2),
        width=box_w,
    )

    # Error text
    error_text = Text(_PAD + error, style="bright_red", justify="left") if error else Text("")

    # Hint
    hint = Text(
        _PAD + "Enter path to a filled .md questionnaire file. Press Enter to confirm.",
        style="dim",
        justify="left",
    )

    body: list = [
        Padding(input_box, (0, 0, 0, len(_PAD))),
        error_text,
        Text(""),
        hint,
    ]
    body_h = 8  # input_box(5) + error(1) + blank + hint(1)

    # Layout: blank + title(2) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 6  # blank + title(2) + blank + subtitle + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_analysis_progress_screen(
    progress: list[str],
    *,
    width: int = 80,
    height: int = 24,
    elapsed: float = 0.0,
    anim_tick: float = 0.0,
    source: str = "",
    mode: str = "planning",
) -> Panel:
    """Build the team analysis progress screen with spinner and step indicators.

    Shows a visual progress display while the analysis thread runs in the background.
    """
    from yeaboi.ui.shared._components import analysis_title

    title = analysis_title() if mode == "analysis" else planning_title()

    # Spinner frames
    _spinners = ["\u25d0", "\u25d3", "\u25d1", "\u25d2"]
    spinner = _spinners[int(anim_tick * 4) % len(_spinners)]

    # Elapsed time display
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    time_str = f"{mins}:{secs:02d}" if mins > 0 else f"{secs}s"

    # Header
    source_label = f" ({source})" if source else ""
    body: list = [
        Text(
            _PAD + f"{spinner}  Analysing team board{source_label}",
            style="bold bright_green",
            justify="left",
        ),
        Text(_PAD + f"   Elapsed: {time_str}", style="dim", justify="left"),
        Text(""),
    ]

    # Progress steps with status indicators
    _done_steps = progress[:-1] if len(progress) > 1 else []
    _current = progress[-1] if progress else ""

    for step in _done_steps:
        body.append(Text(_PAD + f"  \u2713 {step}", style="#22c55e", justify="left"))

    if _current:
        # Animated dots for current step
        dots = "." * (int(anim_tick * 2) % 4)
        body.append(
            Text(
                _PAD + f"  \u25b8 {_current}{dots}",
                style="bold white",
                justify="left",
            )
        )

    # Fill remaining space
    body_h = 4 + len(progress)
    inner_h = height - 4
    remaining = max(0, inner_h - 4 - body_h)
    body.extend([Text("") for _ in range(remaining)])

    content = Group(Text(""), title, Text(""), *body)

    return Panel(
        content,
        border_style="#22c55e",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_project_export_success_screen(
    file_path: str,
    *,
    width: int = 80,
    height: int = 24,
    subtitle: str = "Plan exported",
    hint: str = "Press any key to continue.",
    mode: str = "planning",
    shimmer_tick: float | None = None,
) -> Panel:
    """Build the project export success/status screen.

    Shown after exporting a project's plan as Markdown and HTML,
    or during/after Jira sync operations. subtitle and hint can
    be customised for different contexts (e.g. loading states).
    shimmer_tick: if set, animates the title's travelling highlight.
    """
    if mode == "analysis":
        from yeaboi.ui.shared._components import analysis_title

        title = analysis_title(shimmer_tick)
    else:
        title = planning_title(shimmer_tick)

    body: list = [
        Text(_PAD + subtitle, style="bold bright_green", justify="left"),
        Text(""),
    ]
    for line in file_path.splitlines():
        body.append(Text(_PAD + f"  {line}", style="white", justify="left"))
    if hint:
        body.extend(
            [
                Text(""),
                Text(_PAD + hint, style="dim", justify="left"),
            ]
        )
    body_h = 3 + len(file_path.splitlines()) + 2

    inner_h = height - 4
    header_h = 4  # blank + title(2) + blank
    remaining = max(0, inner_h - header_h - body_h)

    content = Group(
        Text(""),
        title,
        Text(""),
        *body,
        *[Text("") for _ in range(remaining)],
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Usage screen
# ---------------------------------------------------------------------------


def _build_usage_screen(
    usage_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    actions: list[str] | None = None,
    message: str = "",
) -> Panel:
    """Build the usage dashboard screen using shared TUI components.

    Shows API token usage, session history, provider info, and cost estimates.
    Uses USAGE_THEME (amber) with shared buttons, scrollbar, and viewport.
    ``actions`` defaults to ["Back"]; the Copy button passes ["Copy", "Back"].
    """
    from yeaboi.ui.shared._components import USAGE_THEME, build_reveal_subtitle, usage_title

    theme = USAGE_THEME
    title = usage_title(shimmer_tick)
    sub = build_reveal_subtitle("API usage and session history", sub_reveal, pad=_PAD)

    body_lines: list = []
    if message:
        body_lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body_lines.append(Text(""))

    def _heading(text: str) -> None:
        body_lines.append(Text(""))
        h = Text(_PAD + "  ", justify="left")
        h.append(text, style=f"bold {theme.accent}")
        body_lines.append(h)
        body_lines.append(Text(_PAD + "  " + "\u2500" * min(len(text), 40), style=theme.sep, justify="left"))

    def _row(label: str, value: str, value_style: str = "") -> None:
        r = Text(_PAD + "    ", justify="left")
        r.append(f"{label}:  ", style=theme.muted)
        r.append(str(value), style=value_style or theme.value)
        body_lines.append(r)

    # ── Provider Info ──────────────────────────────────────────────
    _heading("LLM Provider")
    _row("Provider", usage_data.get("provider", "unknown"))
    _row("Model", usage_data.get("model", "unknown"))
    api_status = usage_data.get("api_key_status", "not configured")
    status_style = theme.good if api_status == "configured" else theme.bad
    _row("API key", api_status, status_style)

    # ── Lifetime Token Usage (persisted across all sessions) ────
    lifetime = usage_data.get("lifetime_tokens", {})
    if lifetime:
        _heading("Lifetime Token Usage")
        _row("Total LLM calls", f"{lifetime.get('calls', 0):,}")
        _row("Input tokens", f"{lifetime.get('input', 0):,}")
        _row("Output tokens", f"{lifetime.get('output', 0):,}")
        _row("Total tokens", f"{lifetime.get('total', 0):,}")
        lt_cost = lifetime.get("estimated_cost", 0.0)
        if lt_cost > 0:
            _row("Estimated total cost", f"${lt_cost:.4f}", theme.warn)
        elif usage_data.get("provider") == "ollama":
            _row("Estimated total cost", "$0.00 — local model, runs on your hardware", theme.good)

    # ── Current Session Usage ─────────────────────────────────────
    _heading("Current Session")
    tokens = usage_data.get("tokens", {})
    if tokens:
        _row("LLM calls", f"{tokens.get('calls', 0):,}")
        _row("Input tokens", f"{tokens.get('input', 0):,}")
        _row("Output tokens", f"{tokens.get('output', 0):,}")
        _row("Total tokens", f"{tokens.get('total', 0):,}")
        cost = tokens.get("estimated_cost", 0.0)
        if cost > 0:
            _row("Session cost", f"${cost:.4f}", theme.warn)
        elif usage_data.get("provider") == "ollama":
            _row("Session cost", "$0.00 — local model", theme.good)
    else:
        body_lines.append(Text(_PAD + "    No calls in this session yet.", style=theme.muted, justify="left"))

    if not lifetime and not tokens:
        body_lines.append(
            Text(
                _PAD + "    Token tracking starts when you run analysis or planning.",
                style=theme.dim,
                justify="left",
            )
        )

    # ── Local Model Performance ───────────────────────────────────
    # Only present once a local (Ollama) call has recorded timing — hidden
    # entirely for cloud-only histories.
    perf = usage_data.get("local_performance", {})
    if perf:
        _heading("Local Model Performance")
        _row("Ollama calls", f"{perf.get('calls', 0):,}")
        _row("Avg speed", f"{perf.get('avg_tps', 0):.1f} tok/s")
        _row("Max speed", f"{perf.get('max_tps', 0):.1f} tok/s")
        _row("Avg call duration", f"{perf.get('avg_duration_ms', 0) / 1000:.1f}s")
        _row("Avg model load", f"{perf.get('avg_load_ms', 0) / 1000:.1f}s")
        last_call = perf.get("last") or {}
        if last_call:
            _row("Last call", f"{last_call.get('model', '?')} · {last_call.get('tps', 0):.1f} tok/s")

    # ── Session History ───────────────────────────────────────────
    _heading("Session History")
    sessions = usage_data.get("sessions", {})
    _row("Total sessions", str(sessions.get("total", 0)))
    _row("Planning sessions", str(sessions.get("planning", 0)))
    _row("Analysis sessions", str(sessions.get("analysis", 0)))
    last = sessions.get("last_used", "")
    if last:
        _row("Last session", last)

    # ── Environment ───────────────────────────────────────────────
    _heading("Environment")
    _row("Version", usage_data.get("version", "?"))
    _row("Python", usage_data.get("python_version", "?"))
    langsmith = usage_data.get("langsmith", "disabled")
    ls_style = theme.good if langsmith == "enabled" else theme.dim
    _row("LangSmith", langsmith, ls_style)
    _row("Session DB", usage_data.get("db_path", "~/.scrum-agent/sessions.db"))

    # ── Team Profiles ─────────────────────────────────────────────
    profiles = usage_data.get("profiles", [])
    if profiles:
        _heading("Team Profiles")
        for p in profiles:
            r = Text(_PAD + "    ", justify="left")
            r.append(p.get("name", "?"), style=theme.value)
            r.append(f"  {p.get('source', '')} \u00b7 {p.get('sprints', 0)} sprints", style=theme.muted)
            age = p.get("age", "")
            if age:
                r.append(f"  \u00b7 {age}", style=theme.dim)
            body_lines.append(r)

    # ── Layout using shared components ────────────────────────────
    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    btn_top, btn_mid, btn_bot = build_action_buttons(actions or ["Back"], action_sel)

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_standup_screen(
    standup_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    view: str = "overview",
    selected_card: int = 0,
    actions: list[str] | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Daily Standup screen (compact dashboard + expandable section cards).

    A pinned status strip under the subtitle carries the sprint/day/confidence
    facts as meters (visible on every view, never scrolls away), with an
    optional one-row notice banner (transient message or first warning). The
    overview body is just the selectable section list (Team Summary, My
    Update, Team — which expands inline into per-member sub-rows — Activity,
    Schedule, Notices); ``view`` set to a card key from ``standup_card_order``
    renders that section's detail. Uses STANDUP_THEME (magenta) with shared
    buttons, scrollbar, meters, and viewport.

    standup_data keys: session_name, my_name, config (dict|None), schedule
    (dict), report (StandupReport|None), message (str, transient status line),
    team_expanded (bool, inline Team-row expansion).

    # See docs: "Daily Standup" — TUI page
    """
    from yeaboi.ui.mode_select.screens._standup_sections import (
        _confidence_style,
        _StandupCtx,
        build_standup_detail,
        build_standup_overview,
        standup_card_title,
    )
    from yeaboi.ui.shared._components import STANDUP_THEME, build_meter, build_reveal_subtitle, standup_title

    theme = STANDUP_THEME
    title = standup_title(shimmer_tick)
    session_name = standup_data.get("session_name", "")
    if view == "overview":
        base = f"Daily standup for {session_name}" if session_name else "Daily standup"
        sub_text = f"{base}  ·  ↑/↓ sections · Enter open · ←/→ buttons"
        if len(_PAD) + len(sub_text) > width - 6:  # hint doesn't fit → keep just the base
            sub_text = base
    else:
        sub_text = f"Overview › {standup_card_title(view, standup_data)}"
    if anon_note:  # anonymized: the subtitle becomes the "N masked — review" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=_PAD)
    sub.no_wrap = True  # the header row budget counts the subtitle as one row
    sub.overflow = "ellipsis"

    report = standup_data.get("report")
    # Pinned status strip: the sprint facts stay visible while the body scrolls.
    # no_wrap on strip + banner is load-bearing: header_h counts each as ONE row,
    # so a wrap would push the button bottom border off the fixed-height panel.
    strip = Text(_PAD, justify="left", no_wrap=True, overflow="ellipsis")
    if report is not None:
        strip.append(f"Sprint {report.sprint_name or 'unknown'}", style=theme.value)
        if report.sprint_total_days:
            strip.append("   ")
            strip.append(f"Day {report.sprint_day}/{report.sprint_total_days} ", style=theme.muted)
            strip.append_text(build_meter(report.sprint_day, report.sprint_total_days, theme=theme))
        if report.confidence_label:
            conf_style = _confidence_style(theme, report.confidence_label)
            strip.append("   ")
            strip.append(f"{report.confidence_label} ", style=conf_style)
            if report.confidence_label != "Insufficient data":
                strip.append_text(build_meter(report.confidence_pct, 100, theme=theme, style=conf_style))
                strip.append(f" {report.confidence_pct}%", style=conf_style)
    else:
        strip.append("No standup yet — Generate creates today's standup", style=theme.muted)

    # One-row banner: a transient status message wins, else the first warning.
    message = standup_data.get("message", "")
    warnings = tuple(getattr(report, "warnings", ()) or ()) if report is not None else ()
    banner: Text | None = None
    if message:
        banner = Text(_PAD + message, style=theme.accent_bright, justify="left", no_wrap=True, overflow="ellipsis")
    elif warnings:
        n = len(warnings)
        prefix = f"⚠ {n} notice{'s' if n != 1 else ''} · "
        # Panel border (2) + padding (4) leave width-6 columns. Keep a 3-col safety
        # margin so an ambiguous-width glyph (⚠ / — count as 1 cell to Rich but often
        # render as 2 in the terminal) can't nudge the line past the right border, and
        # cap the gist so this stays a readable teaser instead of stretching edge-to-edge
        # on ultra-wide terminals — the full warning list lives in the Notices section.
        avail = (width - 6) - len(_PAD) - len(prefix) - 3
        room = max(16, min(avail, 90))
        gist = warnings[0]
        if len(gist) > room:
            gist = gist[: room - 1] + "…"
        banner = Text(_PAD + prefix + gist, style=theme.warn, justify="left", no_wrap=True, overflow="ellipsis")

    ctx = _StandupCtx(theme, width)
    if view == "overview":
        build_standup_overview(ctx, standup_data, selected_card)
    else:
        build_standup_detail(ctx, view, standup_data)
    body_lines = ctx.lines

    # ── Layout using shared components ────────────────────────────
    # header_h must match the Group rows above the viewport exactly (blank +
    # title(2) + blanks + sub + strip + optional banner),
    # else the button bottom border falls off the fixed-height panel.
    header_h = 8 + (1 if banner is not None else 0)
    if (height - 4) - header_h - 4 < 3:
        # Terminal too short for the strip — drop it rather than push the
        # buttons off the panel (same floor as before the strip existed).
        strip = None
        banner = None
        header_h = 6
    viewport_h = calc_viewport(height, header_h=header_h, action_h=4)
    total_items = len(body_lines)
    total_rendered = sum(ctx.item_heights)
    # Scroll offsets identify renderable items, not terminal rows. Find the
    # earliest trailing item from which the report's tail fits in the viewport.
    tail_h = 0
    max_scroll = max(0, total_items - 1)
    for i in range(total_items - 1, -1, -1):
        item_h = ctx.item_heights[i] if i < len(ctx.item_heights) else 1
        if tail_h and tail_h + item_h > viewport_h:
            break
        tail_h += item_h
        max_scroll = i
    # On the overview every item remains a one-row Text value. Keep the whole
    # selected card visible, including a wrapped summary teaser.
    if view == "overview" and ctx.card_rows and selected_card < len(ctx.card_rows):
        row_top = ctx.card_rows[selected_card]
        row_bot = ctx.card_rows[selected_card + 1] - 1 if selected_card + 1 < len(ctx.card_rows) else total_items - 1
        if row_bot + 1 > scroll_offset + viewport_h:
            scroll_offset = row_bot + 1 - viewport_h
        if row_top < scroll_offset:
            scroll_offset = row_top
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible: list = []
    visible_h = 0
    for i in range(actual_scroll, total_items):
        item_h = ctx.item_heights[i] if i < len(ctx.item_heights) else 1
        if visible_h + item_h > viewport_h:
            break
        visible.append(body_lines[i])
        visible_h += item_h

    _sb_text = build_scrollbar(viewport_h, total_rendered, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - visible_h)):
        padded_lines.append(Text(""))

    if actions is None:
        actions = ["Generate", "Team", "Configure", "Back"] if view == "overview" else ["Back", "Export"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    header_rows: list = [] if strip is None else [Text(""), strip]
    if banner is not None:
        header_rows.append(banner)
    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        *header_rows,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_standup_team_source_screen(
    sources: list[tuple[str, str]],
    checked: set[int],
    cursor: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
    heading: str = "Choose update sources",
) -> Panel:
    """Build the first Team step: Jira/Azure DevOps tracker multi-select."""
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    theme = STANDUP_THEME
    rows: list[Text] = []
    if message:
        rows.extend((Text(_PAD + message, style=theme.warn), Text("")))
    rows.append(Text(_PAD + f"{len(checked)} of {len(sources)} selected", style=theme.muted))
    rows.append(Text(""))
    for idx, (_key, label) in enumerate(sources):
        selected = idx in checked
        active = idx == cursor
        row = Text(_PAD + "  ")
        row.append("‹ " if active else "  ", style=theme.accent_bright)
        row.append("●" if selected else "○", style=theme.accent_bright if selected else theme.dim)
        row.append(f" {label}", style="bold white" if active else (theme.accent if selected else theme.desc))
        if active:
            row.append(" ›", style=theme.accent_bright)
        rows.append(row)
    viewport_h = calc_viewport(height, header_h=6, action_h=2)
    rows.extend(Text("") for _ in range(max(0, viewport_h - len(rows))))
    content = Group(
        Text(""),
        standup_title(),
        Text(""),
        Text(_PAD + heading, style="bold white"),
        Text(_PAD + "↑/↓ move · Space toggle · Enter continue · Esc cancel", style=theme.muted),
        Text(""),
        Group(*rows[:viewport_h]),
    )
    return Panel(content, border_style="white", box=rich.box.ROUNDED, expand=True, height=height, padding=(1, 2))


def _build_standup_team_member_screen(
    roster: list[str],
    checked: set[int],
    cursor: int,
    *,
    width: int = 80,
    height: int = 24,
    message: str = "",
    heading: str = "Choose team members",
    empty_message: str = "No members found in the selected tracker(s).",
) -> Panel:
    """Build the second Team step: authoritative member multi-select."""
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    theme = STANDUP_THEME
    rows: list[Text] = []
    if message:
        rows.extend((Text(_PAD + message, style=theme.warn), Text("")))
    rows.extend(
        (
            Text(_PAD + f"{len(checked)} of {len(roster)} selected", style=theme.muted),
            Text(""),
        )
    )
    for idx, name in enumerate(roster):
        selected = idx in checked
        active = idx == cursor
        row = Text(_PAD + "  ")
        row.append("‹ " if active else "  ", style=theme.accent_bright)
        row.append("●" if selected else "○", style=theme.accent_bright if selected else theme.dim)
        row.append(f" {name}", style="bold white" if active else (theme.accent if selected else theme.desc))
        if active:
            row.append(" ›", style=theme.accent_bright)
        rows.append(row)
    if not roster:
        rows.append(Text(_PAD + empty_message, style=theme.muted))

    viewport_h = calc_viewport(height, header_h=6, action_h=2)
    total = len(rows)
    max_scroll = max(0, total - viewport_h)
    start = min(max(0, cursor - viewport_h // 2 + 2), max_scroll) if roster else 0
    visible = rows[start : start + viewport_h]
    visible.extend(Text("") for _ in range(max(0, viewport_h - len(visible))))
    scrollbar = build_scrollbar(viewport_h, total, start, max_scroll, always_show=True)
    if scrollbar is not None:
        from rich.table import Table

        viewport = Table(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        viewport.add_column(ratio=1)
        viewport.add_column(width=1)
        viewport.add_row(Group(*visible), scrollbar)
    else:
        viewport = Group(*visible)
    content = Group(
        Text(""),
        standup_title(),
        Text(""),
        Text(_PAD + heading, style="bold white"),
        Text(_PAD + "↑/↓ move · Space toggle · A toggle all · Enter save · Esc cancel", style=theme.muted),
        Text(""),
        viewport,
    )
    return Panel(content, border_style="white", box=rich.box.ROUNDED, expand=True, height=height, padding=(1, 2))


def _build_changelog_screen(
    entries: list,
    *,
    update_status: dict | None = None,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    actions: list[str] | None = None,
    message: str = "",
) -> Panel:
    """Build the Changelog page: per-version AI-written notes with area tags.

    ``entries`` is ``changelog.load_changelog()`` output (newest-first). Each
    highlight's feature-area tags render in that mode's accent colour
    (``changelog.AREA_COLORS``) so a change reads as the feature the user already
    knows by colour. ``update_status`` (``update_check.get_update_status()``)
    drives an upgrade banner at the top when a newer PyPI release is known.
    """
    import textwrap

    from yeaboi.changelog import AREA_COLORS
    from yeaboi.ui.shared._components import CHANGELOG_THEME, build_reveal_subtitle, changelog_title

    theme = CHANGELOG_THEME
    title = changelog_title(shimmer_tick, width=width)
    sub = build_reveal_subtitle("What's new in yeaboi", sub_reveal, pad=PAD)

    body_lines: list = []
    if message:
        body_lines.append(Text(PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body_lines.append(Text(""))
    wrap_w = max(24, width - len(PAD) - 12)

    def _wrapped(text: str, style: str, *, indent: str = "    ") -> None:
        for chunk in textwrap.wrap(text, width=wrap_w) or [""]:
            body_lines.append(Text(PAD + indent + chunk, style=style, justify="left"))

    # ── Upgrade banner — newer release known from the background PyPI check ──
    status = update_status or {}
    if status.get("update_available"):
        banner = Text(PAD + "  ", justify="left")
        banner.append("⬆ ", style=theme.warn)
        banner.append(f"v{status.get('latest', '')} is available", style=f"bold {theme.warn}")
        banner.append("  —  run: ", style=theme.muted)
        banner.append(status.get("upgrade_command", ""), style=theme.warn)
        body_lines.append(banner)
        body_lines.append(Text(PAD + "  " + "─" * min(wrap_w, 40), style=theme.sep, justify="left"))

    if not entries:
        body_lines.append(Text(""))
        body_lines.append(Text(PAD + "    No changelog data available.", style=theme.muted, justify="left"))

    for entry in entries:
        body_lines.append(Text(""))
        heading = Text(PAD + "  ", justify="left")
        heading.append(f"v{entry.version}", style=f"bold {theme.accent_bright}")
        if entry.date:
            heading.append("  ·  ", style=theme.sep)
            heading.append(entry.date, style=theme.muted)
        body_lines.append(heading)
        body_lines.append(Text(PAD + "  " + "─" * min(wrap_w, 40), style=theme.sep, justify="left"))
        if entry.summary:
            _wrapped(entry.summary, theme.desc)
        for hl in entry.highlights:
            # Reserve room on the bullet's last line for the coloured area tags.
            tags_len = sum(len(a) + 2 for a in hl.areas)
            chunks = textwrap.wrap(hl.text, width=max(24, wrap_w - 3)) or [""]
            for i, chunk in enumerate(chunks):
                prefix = "    •  " if i == 0 else "       "
                line = Text(PAD + prefix + chunk, style=theme.value, justify="left")
                if i == len(chunks) - 1 and len(prefix) + len(chunk) + tags_len <= wrap_w + 8:
                    for area in hl.areas:
                        line.append("  ")
                        line.append(area, style=f"bold {AREA_COLORS.get(area, theme.muted)}")
                    body_lines.append(line)
                elif i == len(chunks) - 1:
                    body_lines.append(line)
                    tag_line = Text(PAD + "       ", justify="left")
                    for area in hl.areas:
                        tag_line.append(area, style=f"bold {AREA_COLORS.get(area, theme.muted)}")
                        tag_line.append("  ")
                    body_lines.append(tag_line)
                else:
                    body_lines.append(line)

    # ── Layout using shared components ────────────────────────────
    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    btn_top, btn_mid, btn_bot = build_action_buttons(actions or ["Back"], action_sel)

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_all_tips_screen(
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    actions: list[str] | None = None,
    message: str = "",
) -> Panel:
    """Build the All Tips gallery page: every discoverability tip in one scroll.

    Same scrollable Panel skeleton as :func:`_build_changelog_screen`. Content is
    the live ``get_tips()`` list, grouped into modes, workflows, and setup so the
    gallery scans like the other sectioned pages. Freshly-shipped features get a
    gold ``NEW`` badge and tips that map to a home card note the mode they open.
    The ``Copy all`` action copies the whole list to the clipboard (see the run
    loop in mode_select/__init__.py).
    """
    import textwrap

    from yeaboi.ui.mode_select.screens._screens import _MODE_CARDS, _TIP_DOT_ON
    from yeaboi.ui.shared._components import CHANGELOG_THEME, build_reveal_subtitle, tips_title
    from yeaboi.ui.shared._tips import get_tips

    theme = CHANGELOG_THEME
    title = tips_title(shimmer_tick, width=width)
    sub = build_reveal_subtitle("Everything yeaboi can do", sub_reveal, pad=PAD)
    cards = {card["key"]: card for card in _MODE_CARDS}
    gold = f"rgb({_TIP_DOT_ON[0]},{_TIP_DOT_ON[1]},{_TIP_DOT_ON[2]})"

    body_lines: list = []
    if message:
        body_lines.append(Text(PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body_lines.append(Text(""))

    # Account explicitly for the frame, horizontal panel padding, scrollbar,
    # and the gutter beside it. Keeping wrapping inside this budget prevents
    # wide glyphs or metadata from visually colliding with the right frame.
    panel_inner_w = max(20, width - 2 - 4)
    viewport_body_w = max(18, panel_inner_w - 3)
    bullet_prefix = PAD + "    •  "
    continuation_prefix = PAD + "       "
    tip_wrap_w = max(16, viewport_body_w - len(bullet_prefix) - 1)
    separator_w = max(8, min(viewport_body_w - len(PAD) - 2, 40))

    tips = get_tips()
    grouped_tips = (
        ("Modes", [tip for tip in tips if tip.mode_key]),
        (
            "More workflows",
            [
                tip
                for tip in tips
                if not tip.mode_key and tip.key not in {"voice", "music"} and not tip.key.startswith("meta:")
            ],
        ),
        (
            "Shortcuts & setup",
            [tip for tip in tips if tip.key in {"voice", "music"} or tip.key.startswith("meta:")],
        ),
    )

    rendered_section = False
    for section, section_tips in grouped_tips:
        if not section_tips:
            continue
        if rendered_section:
            body_lines.append(Text(""))
        rendered_section = True
        heading = Text(PAD + "  ", justify="left")
        heading.append(section, style=f"bold {theme.accent_bright}")
        body_lines.append(heading)
        body_lines.append(Text(PAD + "  " + "─" * separator_w, style=theme.sep, justify="left"))

        for tip in section_tips:
            # Emoji variation selectors are not measured consistently across
            # terminals. On a full-width panel that disagreement shifts Rich's
            # final frame cell and makes the right border appear fragmented.
            # The gallery already has a bullet and colour-coded mode metadata,
            # so omit the decorative "<emoji> Tip:" prefix here. The canonical
            # tip text (and therefore Copy all) remains unchanged.
            _prefix, marker, display_text = tip.text.partition("Tip: ")
            if not marker:
                display_text = tip.text
            chunks = textwrap.wrap(display_text, width=tip_wrap_w) or [""]
            for i, chunk in enumerate(chunks):
                prefix = bullet_prefix if i == 0 else continuation_prefix
                body_lines.append(Text(prefix + chunk, style=theme.value, justify="left"))

            if tip.is_new or (tip.mode_key and tip.mode_key in cards):
                metadata = Text(continuation_prefix, justify="left")
                if tip.is_new:
                    metadata.append("NEW", style=f"bold {gold}")
                if tip.mode_key and tip.mode_key in cards:
                    if tip.is_new:
                        metadata.append("  ·  ", style=theme.sep)
                    metadata.append("opens ", style=theme.muted)
                    card = cards[tip.mode_key]
                    metadata.append(card["title"], style=f"bold {card['color']}")
                body_lines.append(metadata)

            # A deliberate spacer makes each wrapped record read as one unit.
            body_lines.append(Text(""))

    # ── Layout using shared components (identical to the changelog page) ──
    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    btn_top, btn_mid, btn_bot = build_action_buttons(actions or ["Back"], action_sel)

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_column(width=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), Text(""), _sb_text, Text(""))
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_feedback_screen(
    view: str,
    *,
    kind_idx: int = 0,
    area_idx: int = 0,
    title_text: str = "",
    description: str = "",
    attachments_count: int = 0,
    field_sel: int = 0,
    focus: str = "fields",
    action_sel: int = 0,
    polished: tuple[str, str] | None = None,
    result_url: str = "",
    show_open_browser: bool = False,
    status: str = "",
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    border_style: str = "",
) -> Panel:
    """Build the Feedback page (opened with `f` from mode select).

    ``view`` selects the screen state: ``"form"`` (type/area/title/description
    rows + Submit / AI Polish / Back), ``"busy"`` (worker running — the caller
    animates ``border_style`` for the pulsing frame), ``"polish_preview"``
    (AI-rewritten draft + Accept / Keep Original) and ``"result"`` (submission
    outcome + Done / Open Browser). The area chip renders in that mode's accent
    colour (``changelog.AREA_COLORS``) — the frame itself stays neutral silver
    like the changelog page.
    """
    import textwrap

    from yeaboi.changelog import AREA_COLORS
    from yeaboi.feedback import FEEDBACK_AREAS, FEEDBACK_TYPES
    from yeaboi.ui.shared._components import FEEDBACK_THEME, build_reveal_subtitle, feedback_title

    theme = FEEDBACK_THEME
    title = feedback_title(shimmer_tick, width=width)
    subtitles = {
        "form": "Report a bug or request a feature — filed as a GitHub issue",
        "busy": "Working…",
        "polish_preview": "AI-polished draft — accept it or keep your original",
        "result": "Submission result",
    }
    sub = build_reveal_subtitle(subtitles.get(view, ""), sub_reveal, pad=PAD)

    body_lines: list = []
    wrap_w = max(24, width - len(PAD) - 12)

    def _wrapped(text: str, style: str, *, indent: str = "    ") -> None:
        for seg in text.split("\n"):
            for chunk in textwrap.wrap(seg, width=wrap_w) or [""]:
                body_lines.append(Text(PAD + indent + chunk, style=style, justify="left"))

    kind = FEEDBACK_TYPES[kind_idx % len(FEEDBACK_TYPES)]
    area = FEEDBACK_AREAS[area_idx % len(FEEDBACK_AREAS)]
    area_color = AREA_COLORS.get(area, theme.muted)

    if view in ("form", "busy"):
        fields_focused = focus == "fields" and view == "form"

        def _row(idx: int, label: str, render_value) -> None:
            is_sel = fields_focused and field_sel == idx
            line = Text(PAD + ("  ❯ " if is_sel else "    "), justify="left")
            line.stylize(f"bold {theme.accent_bright}" if is_sel else theme.dim)
            line.append(f"{label:<13}", style=f"bold {theme.accent_bright}" if is_sel else theme.muted)
            render_value(line, is_sel)
            body_lines.append(line)
            body_lines.append(Text(""))

        def _kind_value(line: Text, is_sel: bool) -> None:
            line.append("◄ " if is_sel else "  ", style=theme.dim)
            line.append(kind, style=f"bold {theme.value}" if is_sel else theme.desc)
            line.append(" ►" if is_sel else "", style=theme.dim)

        def _area_value(line: Text, is_sel: bool) -> None:
            line.append("◄ " if is_sel else "  ", style=theme.dim)
            line.append(area, style=f"bold {area_color}")
            line.append(" ►" if is_sel else "", style=theme.dim)

        def _title_value(line: Text, is_sel: bool) -> None:
            if title_text:
                shown = title_text if len(title_text) <= wrap_w - 20 else title_text[: wrap_w - 21] + "…"
                line.append(shown, style=theme.value)
            else:
                line.append("(required — press Enter to write)", style=theme.dim)

        # The value column starts after the 4-space selector gutter + 13-char label.
        _val_indent = " " * 17
        desc_wrap_w = max(24, wrap_w - len(_val_indent))
        desc_lines: list[str] = []
        for seg in description.split("\n"):
            desc_lines.extend(textwrap.wrap(seg, width=desc_wrap_w) or [""])

        def _desc_value(line: Text, is_sel: bool) -> None:
            if description.strip():
                line.append(desc_lines[0], style=theme.value)
            else:
                line.append("(press Enter to write — voice + Ctrl+V screenshots)", style=theme.dim)
            if attachments_count:
                line.append(f"  📎 {attachments_count}", style=theme.warn)

        body_lines.append(Text(""))
        _row(0, "Type", _kind_value)
        _row(1, "Area", _area_value)
        _row(2, "Title", _title_value)
        _row(3, "Description", _desc_value)

        # Continuation lines: fill the otherwise-empty viewport with the rest of
        # the description instead of hiding it behind a "+N more lines" note —
        # that note now only appears when the text genuinely overflows the page.
        # (The blank line _row() appended after the Description row is dropped so
        # the continuation reads as one block.)
        continuations = desc_lines[1:] if description.strip() else []
        if continuations:
            body_lines.pop()
            # Rows consumed above the continuation block: top blank + 4 field
            # rows + 3 blanks between them, plus one trailing status/spacer row.
            desc_budget = max(1, calc_viewport(height, header_h=6, action_h=4) - 10)
            for cont in continuations[:desc_budget]:
                body_lines.append(Text(PAD + _val_indent + cont, style=theme.value, justify="left"))
            hidden = len(continuations) - desc_budget
            if hidden > 0:
                body_lines.append(
                    Text(
                        PAD + _val_indent + f"(+{hidden} more line{'s' if hidden > 1 else ''})",
                        style=theme.dim,
                        justify="left",
                    )
                )
            body_lines.append(Text(""))

    elif view == "polish_preview" and polished is not None:
        p_title, p_desc = polished
        body_lines.append(Text(""))
        heading = Text(PAD + "  ", justify="left")
        heading.append(f"[{kind}] {p_title}", style=f"bold {theme.accent_bright}")
        body_lines.append(heading)
        body_lines.append(Text(PAD + "  " + "─" * min(wrap_w, 40), style=theme.sep, justify="left"))
        _wrapped(p_desc, theme.value)

    elif view == "result":
        body_lines.append(Text(""))
        _wrapped(status, theme.value, indent="  ")
        if result_url:
            body_lines.append(Text(""))
            _wrapped(result_url, f"bold {theme.accent_bright}", indent="  ")

    if status and view not in ("result",):
        body_lines.append(Text(""))
        _wrapped(status, theme.warn, indent="  ")

    # ── Layout using shared components (same skeleton as the changelog page) ──
    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    if view == "form" and focus == "fields":
        # Auto-scroll so the selected field row stays visible on short
        # terminals (the form itself has no manual scroll keys — up/down
        # move the selection instead).
        sel_line = 1 + 2 * field_sel  # body index: top blank + 2 rows per field
        scroll_offset = max(scroll_offset, sel_line - viewport_h + 1)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=view == "polish_preview")
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    buttons_by_view = {
        "form": ["Submit", "AI Polish", "Back"],
        "busy": [],
        "polish_preview": ["Accept", "Keep Original"],
        "result": (["Done", "Open Browser"] if show_open_browser else ["Done"]),
    }
    labels = buttons_by_view.get(view, ["Back"])
    highlight = action_sel if (focus == "buttons" or view != "form") else -1
    if labels:
        btn_top, btn_mid, btn_bot = build_action_buttons(labels, highlight)
    else:
        btn_top, btn_mid, btn_bot = Text(""), Text(""), Text("")

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style=border_style or "white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _performance_roster_window(selected: int, n: int, budget: int) -> tuple[int, int]:
    """Return the [start, end) engineer window that fits ``budget`` visual lines.

    Big ASCII rows are ~3 lines each (the selected one ~5 with its description), so
    only a few engineers show at once. The window always contains ``selected`` and
    grows outward alternately (below first) so the selection stays comfortably in
    view; callers mark any hidden engineers with ▲/▼ counters.
    """
    if n <= 0:
        return 0, 0
    used = 5  # the selected row (2 ASCII lines + blank + description + spacer)
    start, end = selected, selected + 1
    grew = True
    while grew:
        grew = False
        if end < n and used + 3 <= budget:
            used += 3
            end += 1
            grew = True
        if start > 0 and used + 3 <= budget:
            used += 3
            start -= 1
            grew = True
    return start, end


def _build_performance_screen(
    performance_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float = 0.0,
    desc_reveal: float = 0.0,
    sub_reveal: float | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Performance dashboard screen using shared TUI components.

    Two views, both rendered here (the run page owns which is active):
    - "roster": a selectable list of engineers (from Jira / Azure DevOps) — up/down
      moves the selection, the action buttons run a workflow for the selected person.
    - "detail": the artifact produced by an action (1:1 prep / completion / review),
      scrollable, with Back / Export buttons.

    performance_data keys: session_name, view ("roster"|"detail"), roster (list[str]),
    selected_idx (int), detail_lines (list[str] plaintext), detail_title (str),
    actions (list[str]), message (str, transient status line).

    Uses PERFORMANCE_THEME (coral) with shared buttons, scrollbar, and viewport.

    # See docs: "Performance Mode" — TUI page
    """
    from yeaboi.ui.mode_select.screens._screens import _build_mode_row
    from yeaboi.ui.shared._components import PERFORMANCE_THEME, build_reveal_subtitle, performance_title

    theme = PERFORMANCE_THEME
    _accent = "rgb(220,110,90)"  # PERFORMANCE_THEME accent — the mode-row colour key
    title = performance_title(shimmer_tick, width=width)
    view = performance_data.get("view", "roster")
    session_name = performance_data.get("session_name", "")

    if view == "detail":
        sub_text = performance_data.get("detail_title", "") or "Performance"
    else:
        sub_text = f"Team performance — {session_name}" if session_name else "Team performance"
    if anon_note:  # anonymized detail view: the subtitle carries the "N masked" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=PAD)

    message = performance_data.get("message", "")

    def _styled(line: str) -> Text:
        """Style a plaintext artifact line: headers accent, bullets value, notices warn."""
        stripped = line.strip()
        style = theme.value
        if not stripped:
            return Text("")
        if stripped.startswith("⚠"):
            style = theme.warn
        elif stripped.startswith(("•", "-", "☐", "↺")) or line.startswith("  "):
            style = theme.desc
        elif stripped.endswith(":") or line == line.lstrip():
            style = f"bold {theme.accent}"
        return Text(PAD + "  " + line, style=style, justify="left")

    actions = performance_data.get("actions") or ["1:1 Prep", "1:1 Complete", "6mo Review", "Notes", "Export", "Back"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)

    # ── Roster view — big ASCII engineer names (mirrors the intake mode picker) ──
    if view != "detail":
        roster = performance_data.get("roster", []) or []
        hints = performance_data.get("roster_hints", []) or []
        selected_idx = max(0, min(performance_data.get("selected_idx", 0), len(roster) - 1)) if roster else 0

        body: list = []
        if message:
            body.append(Text(PAD + message, style=theme.accent_bright, justify="left"))
            body.append(Text(""))

        if not roster:
            body.append(Text(PAD + "No engineers found.", style=theme.muted, justify="left"))
            body.append(Text(""))
            body.append(Text(PAD + "Connect Jira or Azure DevOps (see Settings) — the roster is", style=theme.muted))
            body.append(Text(PAD + "built from the people assigned work on your board.", style=theme.muted))
        else:
            # Window the engineers that fit vertically, centred on the selection —
            # big ASCII rows are tall, so only a few show at once (▲/▼ mark the rest).
            budget = max(6, height - 16 - (2 if message else 0))
            start, end = _performance_roster_window(selected_idx, len(roster), budget)
            if start > 0:
                body.append(Text(PAD + f"▲ {start} more", style=theme.dim, justify="left"))
                body.append(Text(""))
            for idx in range(start, end):
                name = roster[idx]
                hint = hints[idx] if idx < len(hints) else "1:1 prep · completion · 6-month review"
                card = {"title": name, "color": _accent, "available": True, "description": hint}
                is_sel = idx == selected_idx
                body.extend(
                    _build_mode_row(
                        card,
                        selected=is_sel,
                        shimmer_tick=shimmer_tick,
                        desc_reveal=desc_reveal if is_sel else 0,
                    )
                )
                if idx < end - 1:
                    body.append(Text(""))
            if end < len(roster):
                body.append(Text(""))
                body.append(Text(PAD + f"▼ {len(roster) - end} more", style=theme.dim, justify="left"))

        content = Group(
            Text(""),
            title,
            Text(""),
            sub,
            Text(""),
            *body,
            Text(""),
            btn_top,
            btn_mid,
            btn_bot,
        )
        return Panel(
            content,
            border_style="white",
            box=rich.box.ROUNDED,
            expand=True,
            height=height,
            padding=(1, 2),
        )

    # ── Detail view — the produced artifact, scrollable ──────────────────────────
    body_lines: list = []
    if message:
        body_lines.append(Text(PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body_lines.append(Text(""))
    for line in performance_data.get("detail_lines", []) or ["(nothing to show)"]:
        body_lines.append(_styled(line))

    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_reporting_screen(
    reporting_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Reporting screen using shared TUI components.

    Three views, all rendered here (the run page owns which is active):
    - "picker": choose a reporting period (Last sprint / Last month / Whole quarter)
      with ▲/▼, then Generate a business-friendly delivery report.
    - "sprint_select": for a quarter, a checkbox list of sprints (▸ cursor, ■/□
      toggle) with the quarter's sprints pre-checked — Space toggles, Enter generates.
    - "detail": the generated report (headline, metrics, themes, highlights),
      scrollable, with Export / Theme / Back buttons.

    reporting_data keys: session_name, view ("picker"|"sprint_select"|"detail"),
    periods (list[(key, label, hint)]), selected_idx (int), theme (str), detail_lines
    (list[str] plaintext), detail_title (str), actions (list[str]), message (str),
    quarter_label (str), sprints (list[SprintRef]), sprint_cursor (int),
    sprint_checked (set[int]).

    Uses REPORTING_THEME (indigo) with shared buttons, scrollbar, and viewport.

    # See docs: "Reporting Mode" — TUI page
    """
    from yeaboi.ui.shared._components import REPORTING_THEME, build_reveal_subtitle, reporting_title

    theme = REPORTING_THEME
    title = reporting_title(shimmer_tick, width=width)
    view = reporting_data.get("view", "picker")
    session_name = reporting_data.get("session_name", "")
    deck_theme = reporting_data.get("theme", "midnight")
    message = reporting_data.get("message", "")

    if view == "detail":
        sub_text = reporting_data.get("detail_title", "") or "Delivery Report"
    elif view == "sprint_select":
        sub_text = f"Select sprints for {reporting_data.get('quarter_label', 'the quarter')}"
    else:
        sub_text = f"Report delivered work — {session_name}" if session_name else "Report delivered work"
    if anon_note:  # anonymized detail view: the subtitle carries the "N masked" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=PAD)

    actions = reporting_data.get("actions") or ["Generate Report", "Theme", "Back"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)

    # ── Sprint-select view — checkbox list of the quarter's sprints ──────────────
    if view == "sprint_select":
        sprints = reporting_data.get("sprints", []) or []
        cursor = max(0, min(reporting_data.get("sprint_cursor", 0), len(sprints) - 1)) if sprints else 0
        checked = reporting_data.get("sprint_checked", set()) or set()

        rows: list = []
        if message:
            rows.append(Text(PAD + "  " + message, style=theme.accent_bright, justify="left"))
            rows.append(Text(""))
        n_checked = len(checked)
        rows.append(Text(PAD + f"  Space to toggle · {n_checked} selected · Enter to generate", style=theme.muted))
        rows.append(Text(""))
        for idx, sp in enumerate(sprints):
            is_cursor = idx == cursor
            is_checked = idx in checked
            box = "■" if is_checked else "□"
            cur_mark = "▸ " if is_cursor else "  "
            rng = f"({sp.start_date} → {sp.end_date})" if sp.start_date else "(no dates)"
            row = Text(justify="left")
            row.append(PAD + "  " + cur_mark, style=theme.accent_bright if is_cursor else theme.dim)
            row.append(box + " ", style=theme.accent if is_checked else theme.dim)
            name_style = "bold white" if is_cursor else (theme.value if is_checked else theme.desc)
            row.append(f"{sp.name}  ", style=name_style)
            row.append(rng, style=theme.muted)
            if getattr(sp, "in_quarter", False):
                row.append("  · in quarter", style=theme.dim)
            rows.append(row)
        if not sprints:
            rows.append(Text(PAD + "  No sprints found.", style=theme.muted, justify="left"))

        viewport_h = calc_viewport(height, header_h=6, action_h=4)
        total_lines = len(rows)
        # Window around the cursor row so it stays visible as you move.
        cursor_line = min(total_lines - 1, cursor + (3 if message else 1) + 1) if sprints else 0
        max_scroll = max(0, total_lines - viewport_h)
        start = 0 if total_lines <= viewport_h else max(0, min(cursor_line - viewport_h // 2, max_scroll))
        visible = rows[start : start + viewport_h]

        _sb_text = build_scrollbar(viewport_h, total_lines, start, max_scroll, always_show=True)
        padded_lines = list(visible)
        for _ in range(max(0, viewport_h - len(visible))):
            padded_lines.append(Text(""))
        if _sb_text is not None:
            from rich.table import Table as _SbTable

            _vp = _SbTable(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
            _vp.add_column(ratio=1)
            _vp.add_column(width=1)
            _vp.add_row(Group(*padded_lines), _sb_text)
            viewport_renderable = _vp
        else:
            viewport_renderable = Group(*padded_lines)

        content = Group(
            Text(""),
            title,
            Text(""),
            sub,
            Text(""),
            viewport_renderable,
            Text(""),
            btn_top,
            btn_mid,
            btn_bot,
        )
        return Panel(
            content,
            border_style="white",
            box=rich.box.ROUNDED,
            expand=True,
            height=height,
            padding=(1, 2),
        )

    # ── Picker view — choose the reporting period ────────────────────────────────
    if view != "detail":
        periods = reporting_data.get("periods", []) or []
        selected_idx = max(0, min(reporting_data.get("selected_idx", 0), len(periods) - 1)) if periods else 0

        body: list = []
        if message:
            body.append(Text(PAD + message, style=theme.accent_bright, justify="left"))
            body.append(Text(""))
        body.append(Text(PAD + "Choose a reporting period:", style=f"bold {theme.accent}", justify="left"))
        body.append(Text(""))
        for idx, (_key, label, hint) in enumerate(periods):
            is_sel = idx == selected_idx
            marker = "▸ " if is_sel else "  "
            row = Text(justify="left")
            row.append(PAD + marker, style=theme.accent_bright if is_sel else theme.dim)
            row.append(label, style=theme.value if is_sel else theme.desc)
            body.append(row)
            if hint:
                body.append(Text(PAD + "    " + hint, style=theme.muted, justify="left"))
            body.append(Text(""))
        body.append(Text(PAD + f"Presentation theme: {deck_theme}", style=theme.muted, justify="left"))

        content = Group(
            Text(""),
            title,
            Text(""),
            sub,
            Text(""),
            *body,
            Text(""),
            btn_top,
            btn_mid,
            btn_bot,
        )
        return Panel(
            content,
            border_style="white",
            box=rich.box.ROUNDED,
            expand=True,
            height=height,
            padding=(1, 2),
        )

    # ── Detail view — the generated report, scrollable ───────────────────────────
    def _styled(line: str) -> Text:
        stripped = line.strip()
        if not stripped:
            return Text("")
        style = theme.value
        if stripped.startswith("⚠"):
            style = theme.warn
        elif stripped.startswith(("•", "-")) or line.startswith("  "):
            style = theme.desc
        elif stripped.endswith(":") or line == line.lstrip():
            style = f"bold {theme.accent}"
        return Text(PAD + "  " + line, style=style, justify="left")

    body_lines: list = []
    if message:
        body_lines.append(Text(PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body_lines.append(Text(""))
    for line in reporting_data.get("detail_lines", []) or ["(nothing to show)"]:
        body_lines.append(_styled(line))

    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(show_header=False, show_edge=False, box=None, padding=0, pad_edge=False, expand=True)
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )
    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_roadmap_screen(
    roadmap_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Roadmap intake screen using shared TUI components.

    Two views, both rendered here (the run page owns which is active):
    - "source": choose where the quarterly roadmap lives (Confluence / Notion /
      local file) with ▲/▼, then Select to enter the page URL / file path.
    - "results": the analysis — summary, a *selectable* recommended-project list
      (▸ cursor, [Small]/[Large] badges, rationale), and a ⚠ Notices block —
      with Plan This / Re-analyze / Change Source / Back buttons.

    Saved roadmaps are listed as amber-tagged cards inside the Planning
    "Your projects" list (see _build_project_list_screen), not here.

    roadmap_data keys: view ("source"|"results"), sources (list[(key, label,
    hint)]), selected_idx (int), analysis (RoadmapAnalysis | None),
    project_cursor (int), actions (list[str]), message (str), busy (bool),
    source_label (str), analyzed_at (str).

    A Planning sub-page — uses PLANNING_THEME + planning_title (not a new mode
    theme), with shared buttons, scrollbar, and viewport.

    # See docs: "Roadmap Intake" — TUI page
    """
    from yeaboi.ui.shared._components import PLANNING_THEME, build_reveal_subtitle

    theme = PLANNING_THEME
    title = planning_title(shimmer_tick, width=width)
    view = roadmap_data.get("view", "source")
    message = roadmap_data.get("message", "")
    actions = roadmap_data.get("actions") or ["Select", "Back"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)

    busy = bool(roadmap_data.get("busy"))
    if busy:
        sub_text = "Analyzing your roadmap…"
    elif view == "results":
        source_label = roadmap_data.get("source_label", "")
        analyzed_at = roadmap_data.get("analyzed_at", "")
        sub_text = " · ".join(x for x in (source_label, f"analyzed {analyzed_at}" if analyzed_at else "") if x)
        sub_text = sub_text or "Roadmap analysis"
    else:
        sub_text = "Where does your quarterly roadmap live?"
    if anon_note and not busy:  # anonymized results: the subtitle carries the "N masked" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=PAD)

    # ── Busy overlay — while the analysis worker runs, show only the spinner so
    # the source options / buttons underneath don't confuse the user. ─────────────
    if busy:
        spinner = Text(PAD + message, style=theme.accent_bright, justify="left") if message else Text("")
        return Panel(
            Group(Text(""), title, Text(""), sub, Text(""), Text(""), spinner),
            border_style="white",
            box=rich.box.ROUNDED,
            expand=True,
            height=height,
            padding=(1, 2),
        )

    # ── Source view — pick where the roadmap lives ───────────────────────────────
    if view == "source":
        sources = roadmap_data.get("sources", []) or []
        selected_idx = max(0, min(roadmap_data.get("selected_idx", 0), len(sources) - 1)) if sources else 0

        body: list = []
        if message:
            body.append(Text(PAD + message, style=theme.accent_bright, justify="left"))
            body.append(Text(""))
        body.append(Text(PAD + "Choose a roadmap source:", style=f"bold {theme.accent}", justify="left"))
        body.append(Text(""))
        for idx, (_key, label, hint) in enumerate(sources):
            is_sel = idx == selected_idx
            marker = "▸ " if is_sel else "  "
            row = Text(justify="left")
            row.append(PAD + marker, style=theme.accent_bright if is_sel else theme.dim)
            row.append(label, style=theme.value if is_sel else theme.desc)
            body.append(row)
            if hint:
                body.append(Text(PAD + "    " + hint, style=theme.muted, justify="left"))
            body.append(Text(""))

        content = Group(
            Text(""),
            title,
            Text(""),
            sub,
            Text(""),
            *body,
            Text(""),
            btn_top,
            btn_mid,
            btn_bot,
        )
        return Panel(
            content,
            border_style="white",
            box=rich.box.ROUNDED,
            expand=True,
            height=height,
            padding=(1, 2),
        )

    # ── Results view — summary + bordered project cards (selected expands) ────
    # Mirrors the list branch above: _Padding-wrapped rounded cards with peek
    # stubs and no scrollbar. Unlike the fixed-height project list, the selected
    # card grows to reveal the full description + rationale, so a variable-height
    # window (_window_project_cards) replaces _compute_viewport.
    import textwrap

    from rich.padding import Padding as _Padding

    from yeaboi.ui.mode_select.screens._project_cards import (
        _PEEK_H,
        _ROADMAP_UNSEL_H,
        _build_empty_state_card,
        _build_peek_above,
        _build_peek_below,
        _build_roadmap_notices_card,
        _build_roadmap_project_card,
        _window_project_cards,
    )

    analysis = roadmap_data.get("analysis")
    cursor = roadmap_data.get("project_cursor", 0)
    projects = tuple(getattr(analysis, "projects", ()) or ())
    cursor = max(0, min(cursor, len(projects) - 1)) if projects else 0
    summary = getattr(analysis, "summary", "") if analysis is not None else ""
    warnings = tuple(getattr(analysis, "warnings", ()) or ()) if analysis is not None else ()

    box_w = min(72, max(32, width - len(PAD) - 4))
    inner_w = max(16, box_w - 6)  # border(2) + padding(4)
    card_pad = (0, 0, 0, len(PAD))

    body: list = []
    if message:
        body.append(Text(PAD + "  " + message, style=theme.accent_bright, justify="left"))
        body.append(Text(""))
    if summary:
        body.append(Text(PAD + "  " + summary, style=theme.desc, justify="left"))
        body.append(Text(""))

    if not projects:
        body.append(
            _Padding(
                _build_empty_state_card(
                    selected=False,
                    title="No projects extracted",
                    subtitle="from the roadmap — Re-analyze or Change Source",
                    box_w=box_w,
                ),
                card_pad,
            )
        )
        # The zero-project fallback is exactly where the warnings carry the
        # failure reason (LLM/auth/ingest errors) — always show them here.
        if warnings:
            base = calc_viewport(height, header_h=6, action_h=4) - len(body)
            if base >= 2 + 2 + len(warnings):  # blank + borders + title + bullets
                body.append(Text(""))
                body.append(_Padding(_build_roadmap_notices_card(warnings, box_w=box_w), card_pad))
            else:
                plural = "s" if len(warnings) != 1 else ""
                body.append(
                    Text(
                        PAD + f"⚠ {len(warnings)} Notice{plural} — enlarge the window to view",
                        style=theme.muted,
                        justify="left",
                    )
                )
    else:
        # Key hint (like the list view's) then the card viewport.
        body.append(Text(PAD + "↑/↓ choose a project · Plan This to plan it", style=theme.muted, justify="left"))
        body.append(Text(""))

        # Budget: the shared viewport line count, minus the fixed lines already
        # placed above the cards, minus room reserved for the notices block (so
        # the cards never push the bottom buttons off-panel). Peek-stub space is
        # accounted for inside _window_project_cards, not reserved here.
        base = calc_viewport(height, header_h=6, action_h=4) - len(body)
        notices_full = (1 + 2 + len(warnings)) if warnings else 0  # blank + border + title + bullets
        notices_mode = "card" if warnings else "none"
        available_h = base - notices_full
        if warnings and available_h < _ROADMAP_UNSEL_H:
            notices_mode = "hint"  # not enough room for the card — one-line hint instead
            available_h = base - 1
        available_h = max(_ROADMAP_UNSEL_H, available_h)

        # Wrap the selected project's full description + rationale, capped so its
        # (taller) card — plus room for peek stubs when there are other cards —
        # still fits the viewport.
        sel = projects[cursor]
        wrapped: list[str] = []
        for para in (getattr(sel, "description", "") or "").strip().splitlines() or [""]:
            wrapped.extend(textwrap.wrap(para, inner_w) or [""])
        rationale = (getattr(sel, "rationale", "") or "").strip()
        if rationale:
            wrapped.append("")
            wrapped.extend(textwrap.wrap("Why now: " + rationale, inner_w))
        peek_reserve = 2 * _PEEK_H if len(projects) > 1 else 0
        max_body = max(0, available_h - _ROADMAP_UNSEL_H - 1 - peek_reserve)
        if len(wrapped) > max_body:
            wrapped = wrapped[:max_body]
            if wrapped:
                wrapped[-1] = (wrapped[-1][: max(0, inner_w - 1)]).rstrip() + "…"
        body_lines = tuple(x for x in wrapped if x is not None)

        heights = [
            (_ROADMAP_UNSEL_H + 1 + len(body_lines)) if (idx == cursor and body_lines) else _ROADMAP_UNSEL_H
            for idx in range(len(projects))
        ]
        start, end, peek_above, peek_below = _window_project_cards(heights, cursor, available_h)

        def _pname(i: int) -> str:
            return getattr(projects[i], "name", "") or "(unnamed)"

        if peek_above:
            body.append(_Padding(_build_peek_above(title=_pname(start - 1), box_w=box_w), card_pad))
        for idx in range(start, end):
            index = getattr(projects[idx], "priority", 0) or (idx + 1)
            card = _build_roadmap_project_card(
                projects[idx],
                index=index,
                selected=(idx == cursor),
                box_w=box_w,
                body_lines=body_lines if idx == cursor else (),
            )
            body.append(_Padding(card, card_pad))
            if idx < end - 1:
                body.append(Text(""))
        if peek_below:
            body.append(_Padding(_build_peek_below(title=_pname(end), box_w=box_w), card_pad))

        # Notices below the cards (a distinct amber card, or a one-line hint).
        if notices_mode == "card":
            body.append(Text(""))
            body.append(_Padding(_build_roadmap_notices_card(warnings, box_w=box_w), card_pad))
        elif notices_mode == "hint":
            plural = "s" if len(warnings) != 1 else ""
            body.append(Text(PAD + f"⚠ {len(warnings)} Notice{plural} — enlarge the window to view", style=theme.warn))

    # No scrollbar geometry to publish — the card viewport uses peek stubs, like
    # the list view (publish an empty geometry so stale scroll state clears).
    publish_geometry(scroll_meta, 0, 0)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )
    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_retro_screen(
    retro_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
    anon_note: str = "",
) -> Panel:
    """Build the Retro board screen using shared TUI components.

    Shows the live share code + URL teammates use to join, then the four retro
    grids (What went well / What didn't go well / Action items / Demos) with the
    cards added so far. Uses RETRO_THEME (teal) with shared buttons, scrollbar,
    and viewport. The host's TUI is a monitoring view — the four-column layout
    lives in the browser; here the grids stack vertically so narrow terminals and
    the shared scrollbar behave like every other page.

    retro_data keys: session_name, display_code, url (token-free LAN share URL),
    host_url (optional private token'd host link), message (transient status),
    grids (dict[grid_key -> list[RetroCard]]), public_url (optional remote tunnel
    URL), actions (optional button-label list).

    # See docs: "Retro" — TUI page
    """
    from yeaboi.retro.board import CARRIED_STATUS_LABELS, RETRO_GRID_LABELS, RETRO_GRIDS
    from yeaboi.ui.shared._components import RETRO_THEME, build_reveal_subtitle, retro_title

    theme = RETRO_THEME
    title = retro_title(shimmer_tick)
    session_name = retro_data.get("session_name", "")
    sub_text = f"Sprint retro for {session_name}" if session_name else "Sprint retro"
    if anon_note:  # anonymized: the subtitle carries the "N masked — review" indicator
        sub_text = anon_note
    sub = build_reveal_subtitle(sub_text, sub_reveal, pad=_PAD)

    body_lines: list = []

    def _heading(text: str) -> None:
        body_lines.append(Text(""))
        h = Text(_PAD + "  ", justify="left")
        h.append(text, style=f"bold {theme.accent}")
        body_lines.append(h)
        body_lines.append(Text(_PAD + "  " + "─" * min(len(text), 40), style=theme.sep, justify="left"))

    def _row(label: str, value: str, value_style: str = "") -> None:
        r = Text(_PAD + "    ", justify="left")
        r.append(f"{label}:  ", style=theme.muted)
        r.append(str(value), style=value_style or theme.value)
        body_lines.append(r)

    def _line(text: str, style: str = "") -> None:
        body_lines.append(Text(_PAD + "    " + text, style=style or theme.value, justify="left"))

    def _wrapped(text: str, style: str, *, indent: str = "      ") -> None:
        import textwrap

        wrap_w = max(24, width - len(_PAD) - len(indent) - 6)
        for chunk in textwrap.wrap(text, width=wrap_w) or [""]:
            body_lines.append(Text(_PAD + indent + chunk, style=style, justify="left"))

    # ── Transient status message (e.g. after Generate / Export) ───
    message = retro_data.get("message", "")
    if message:
        body_lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))

    # ── Join info ─────────────────────────────────────────────────
    # Live-board only: a saved-run snapshot has no share code / LAN URL, so the
    # hub passes snapshot=True to suppress this whole block (the report replays
    # the grids + carried actions, not a resumable board).
    if not retro_data.get("snapshot"):
        _heading("Join this retro")
        _row("Share code", retro_data.get("display_code", "—"), f"bold {theme.accent_bright}")
        _row("LAN URL", retro_data.get("url", "—"), theme.value)
        _line("Teammates on the same Wi-Fi open the LAN URL, then enter the Share code above.", theme.muted)
        public_url = retro_data.get("public_url", "")
        if public_url:
            _row("Remote URL", public_url, f"bold {theme.accent_bright}")
            _line("Off-network teammates open the Remote URL (public HTTPS link), then enter the code.", theme.muted)
        host_url = retro_data.get("host_url", "")
        if host_url:
            _row("Host link (private)", host_url, theme.muted)
            _line("For you only — this link skips the code. Don't share it.", theme.muted)

    # ── Last sprint's actions (progress review) ───────────────────
    # Set from teammates' browsers (the review column); the host view is read-only,
    # matching the live-board-is-browser model. Hidden when there's no prior retro.
    carried = retro_data.get("carried") or []
    if carried:
        done_n = sum(1 for c in carried if getattr(c, "status", "") in ("done", "not_relevant"))
        _heading(f"Last sprint's actions  ({done_n}/{len(carried)} resolved)")
        _line("Teammates set each status in the browser — review before generating new actions.", theme.muted)
        for c in carried:
            status = getattr(c, "status", "") or "pending"
            badge = CARRIED_STATUS_LABELS.get(status, status)
            _wrapped(c.text, theme.value, indent="    • ")
            body_lines.append(Text(_PAD + "        " + f"[{badge}]", style=theme.dim, justify="left"))

    # ── The four grids ────────────────────────────────────────────
    grids = retro_data.get("grids") or {}
    total_cards = 0
    for key in RETRO_GRIDS:
        cards = grids.get(key, [])
        total_cards += len(cards)
        _heading(f"{RETRO_GRID_LABELS[key]}  ({len(cards)})")
        if cards:
            for c in cards:
                origin = getattr(c, "origin", "web")
                if origin == "ai":
                    who = "🤖 AI"
                    card_style = theme.accent
                elif origin == "carryover":
                    who = "↩ carried over"
                    card_style = theme.accent
                else:
                    who = getattr(c, "author", "") or "anon"
                    card_style = theme.value
                _wrapped(c.text, card_style, indent="    • ")
                body_lines.append(Text(_PAD + "        " + f"— {who}", style=theme.dim, justify="left"))
        else:
            _line("No cards yet.", theme.muted)

    # ── Layout using shared components ────────────────────────────
    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    actions = retro_data.get("actions") or ["Generate Action Items", "Export", "Close"]
    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_standup_progress_screen(
    progress: list[str],
    *,
    width: int = 80,
    height: int = 24,
    elapsed: float = 0.0,
    anim_tick: float = 0.0,
    theme=None,
    title=None,
    label: str = "Generating standup",
) -> Panel:
    """Build a worker-thread progress screen (spinner + phase steps).

    Shown while a long pipeline (``run_standup``, ``run_anonymize``, ...) runs on a
    worker thread — it makes tracker + LLM network calls that can take many seconds,
    so the user must see live progress instead of a frozen input box. Defaults to the
    Daily Standup look; ``theme``/``title``/``label`` let any mode reuse the identical
    screen with its own accent (this is "the consistent loading screen").
    """
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    if theme is None:
        theme = STANDUP_THEME
    if title is None:
        title = standup_title()

    _spinners = ["◐", "◓", "◑", "◒"]
    spinner = _spinners[int(anim_tick * 4) % len(_spinners)]
    mins, secs = int(elapsed) // 60, int(elapsed) % 60
    time_str = f"{mins}:{secs:02d}" if mins > 0 else f"{secs}s"

    body: list = [
        Text(_PAD + f"{spinner}  {label}", style=f"bold {theme.accent_bright}", justify="left"),
        Text(_PAD + f"   Elapsed: {time_str}", style=theme.dim, justify="left"),
        Text(""),
    ]

    # Completed phases get a check; the current phase gets animated dots.
    # Source-level collection can emit many completions; keep the latest rows
    # visible instead of letting an ever-growing history push the current task
    # below the fixed-height panel.
    max_visible_steps = max(2, height - 15)
    done_steps = progress[:-1][-max_visible_steps:] if len(progress) > 1 else []
    current = progress[-1] if progress else ""
    for step in done_steps:
        body.append(Text(_PAD + f"  ✓ {step}", style=theme.good, justify="left"))
    if current:
        dots = "." * (int(anim_tick * 2) % 4)
        body.append(Text(_PAD + f"  ▸ {current}{dots}", style=f"bold {theme.value}", justify="left"))

    inner_h = height - 4
    remaining = max(0, inner_h - 8 - len(body))
    body.extend(Text("") for _ in range(remaining))

    content = Group(Text(""), title, Text(""), *body)
    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


def _build_standup_input_screen(
    prompt: str,
    value: str,
    *,
    step: str = "",
    default: str = "",
    width: int = 80,
    height: int = 24,
    border_style: str = "",
    status: str = "",
    theme=None,
    title=None,
    box_rows: int = 1,
    show_image_hint: bool = False,
) -> Panel:
    """Build a themed single-line input screen for the Daily Standup flows.

    Stays inside the Live display (driven by read_key), so it matches the app's
    full-screen style and never drops to a raw terminal prompt. Supports voice
    dictation (double-tap Space): pass ``border_style``/``status`` to show the
    recording/transcribing indicator on the same screen.

    Other pages reuse this screen with their own branding by passing ``theme``
    (a Theme constant) and ``title`` (a rendered ASCII-art title); defaults
    keep the standup look. ``box_rows > 1`` renders a large multi-row text box
    (the value wraps across rows and honours explicit ``\\n`` newlines from
    Alt+Enter; the cursor row always stays visible) for longer free-text
    answers like standup updates — Enter confirms.

    # See docs: "Daily Standup" — TUI page
    # See docs: "TUI system" — voice input overlay
    """
    from yeaboi.ui.session.screens._screens_input import _image_hint, _voice_hint
    from yeaboi.ui.shared._components import STANDUP_THEME, standup_title

    theme = theme or STANDUP_THEME
    title = title if title is not None else standup_title()
    sub = Text(_PAD + (step or "Configure standup"), style="dim", justify="left")
    box_style = border_style or theme.accent

    # Prompt label + a bordered input field showing the current value and a cursor.
    label = Text(_PAD + "  ", justify="left")
    label.append(prompt, style=f"bold {theme.accent}")
    if default:
        label.append(f"   (default: {default})", style=theme.dim)

    if box_rows <= 1:
        field_inner = f" {value}█ "
        box_top = Text(_PAD + "  ╭" + "─" * max(len(field_inner), 40) + "╮", style=box_style)
        box_mid = Text(_PAD + "  │", style=box_style)
        box_mid.append(field_inner.ljust(max(len(field_inner), 40)), style=f"bold {theme.accent_bright}")
        box_mid.append("│", style=box_style)
        box_bot = Text(_PAD + "  ╰" + "─" * max(len(field_inner), 40) + "╯", style=box_style)
        box_lines = [box_top, box_mid, box_bot]
    else:
        # Large text box: wide, several rows, the value wraps across them.
        # Clamp the row count so the box + hint always fit the terminal
        # (label + 2 blanks + hint = 4 rows, box borders = 2 rows).
        rows = max(2, min(box_rows, calc_viewport(height, header_h=6, action_h=1) - 6))
        inner_w = max(46, min(width - len(_PAD) - 12, 110))
        text_w = inner_w - 2  # one space of padding each side
        raw = value + "█"
        # Newline-aware chunking: split on explicit newlines (Alt+Enter) first,
        # then width-wrap each segment; an empty segment still takes one row.
        chunks = []
        for seg in raw.split("\n"):
            chunks.extend([seg[i : i + text_w] for i in range(0, len(seg), text_w)] or [""])
        chunks = chunks[-rows:]  # keep the cursor row visible when the text overflows
        while len(chunks) < rows:
            chunks.append("")
        box_lines = [Text(_PAD + "  ╭" + "─" * inner_w + "╮", style=box_style)]
        for chunk in chunks:
            row = Text(_PAD + "  │", style=box_style)
            row.append(f" {chunk}".ljust(inner_w), style=f"bold {theme.accent_bright}")
            row.append("│", style=box_style)
            box_lines.append(row)
        box_lines.append(Text(_PAD + "  ╰" + "─" * inner_w + "╯", style=box_style))

    # While recording/transcribing, the voice status replaces the usual hint.
    if status:
        hint_line = Text(_PAD + "  " + status, style=box_style or theme.accent, justify="left")
    else:
        newline_hint = "  ·  Alt+Enter (or Ctrl+N) for a new line" if box_rows > 1 else ""
        hints = (
            "Enter to confirm  ·  Esc to cancel"
            + newline_hint
            + _voice_hint()
            + (_image_hint() if show_image_hint else "")
        )
        hint_line = Text(_PAD + "  " + hints, style=theme.dim, justify="left")

    # Vertically pad the middle so the field sits in the upper-third like the dashboard.
    body: list = [label, Text(""), *box_lines, Text(""), hint_line]
    pad_rows = max(0, calc_viewport(height, header_h=6, action_h=1) - len(body))
    body.extend(Text("") for _ in range(pad_rows))

    content = Group(Text(""), title, Text(""), sub, Text(""), *body)
    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Profile picker screen (planning mode — select which analysis to use)
# ---------------------------------------------------------------------------


def _build_profile_picker_screen(
    profiles: list,
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
) -> Panel:
    """Build the analysis profile picker shown before planning intake.

    Lists available team analysis profiles as styled cards + a Skip option.
    Uses PLANNING_THEME and shared components for visual consistency.
    """
    from yeaboi.ui.shared._components import PLANNING_THEME, planning_title

    theme = PLANNING_THEME
    title = planning_title()
    sub = Text(_PAD + "Use a team analysis to calibrate planning?", style="dim", justify="left")

    body_lines: list = []
    _source_icons = {"jira": "\U0001f4cb", "azdevops": "\u2601"}  # 📋 for Jira, ☁ for AzDO
    card_w = min(60, width - len(_PAD) - 10)

    for i, p in enumerate(profiles):
        is_sel = i == selected
        team_id = getattr(p, "team_id", "?")
        source = getattr(p, "source", "?")
        sprints = getattr(p, "sample_sprints", 0)
        stories = getattr(p, "sample_stories", 0)
        vel = getattr(p, "velocity_avg", 0.0)
        updated = getattr(p, "updated_at", "")
        completion = getattr(p, "sprint_completion_rate", 0.0)

        # Compute age
        age_str = ""
        stale = False
        if updated:
            try:
                from datetime import UTC, datetime

                _up = datetime.fromisoformat(updated)
                days = (datetime.now(UTC) - _up).days
                age_str = "today" if days == 0 else (f"{days}d ago")
                stale = days > 30
            except Exception:
                pass

        # Card border
        sel_border = theme.accent if is_sel else "rgb(50,50,60)"
        icon = _source_icons.get(source, "\u25cb")

        body_lines.append(Text(""))

        # Top border
        body_lines.append(Text(_PAD + "  \u256d" + "\u2500" * card_w + "\u256e", style=sel_border, justify="left"))

        # Title row
        title_row = Text(_PAD + "  \u2502 ", justify="left")
        title_row.append(f" {icon} ", style=sel_border)
        # Display name: strip source prefix for cleaner look
        display_name = team_id.split("-", 1)[1] if "-" in team_id else team_id
        title_row.append(display_name, style="bold white" if is_sel else theme.muted)
        # Pad to card width
        used = len(title_row.plain) - len(_PAD) - 4
        title_row.append(" " * max(1, card_w - used), style="")
        title_row.append("\u2502", style=sel_border)
        body_lines.append(title_row)

        # Stats row
        stats_row = Text(_PAD + "  \u2502  ", justify="left")
        stat_parts = [f"{sprints} sprints", f"{stories} stories"]
        if vel > 0:
            stat_parts.append(f"{vel:.0f} pts/sprint")
        if completion > 0:
            stat_parts.append(f"{completion:.0f}% completion")
        stats_str = "  \u00b7  ".join(stat_parts)
        stats_row.append(f"  {stats_str}", style=theme.muted)
        used = len(stats_row.plain) - len(_PAD) - 4
        stats_row.append(" " * max(1, card_w - used), style="")
        stats_row.append("\u2502", style=sel_border)
        body_lines.append(stats_row)

        # Source + age row
        meta_row = Text(_PAD + "  \u2502  ", justify="left")
        meta_row.append(f"  {source}", style=theme.dim)
        if age_str:
            meta_row.append("  \u00b7  ", style=theme.dim)
            if stale:
                meta_row.append(f"\u26a0 {age_str}", style=theme.warn)
            else:
                meta_row.append(f"\u2713 {age_str}", style="rgb(80,180,80)")
        used = len(meta_row.plain) - len(_PAD) - 4
        meta_row.append(" " * max(1, card_w - used), style="")
        meta_row.append("\u2502", style=sel_border)
        body_lines.append(meta_row)

        # Bottom border
        body_lines.append(Text(_PAD + "  \u2570" + "\u2500" * card_w + "\u256f", style=sel_border, justify="left"))

    # Skip option — simple row, no card
    body_lines.append(Text(""))
    is_skip_sel = selected == len(profiles)
    skip_border = theme.accent if is_skip_sel else "rgb(50,50,60)"
    body_lines.append(Text(_PAD + "  \u256d" + "\u2500" * card_w + "\u256e", style=skip_border, justify="left"))
    skip_row = Text(_PAD + "  \u2502 ", justify="left")
    skip_row.append(" \u2192 ", style=skip_border)
    skip_row.append("Skip — plan without analysis", style="bold white" if is_skip_sel else theme.muted)
    used = len(skip_row.plain) - len(_PAD) - 4
    skip_row.append(" " * max(1, card_w - used), style="")
    skip_row.append("\u2502", style=skip_border)
    body_lines.append(skip_row)
    skip_detail = Text(_PAD + "  \u2502  ", justify="left")
    skip_detail.append("  Planning will use generic Fibonacci defaults", style=theme.dim)
    used = len(skip_detail.plain) - len(_PAD) - 4
    skip_detail.append(" " * max(1, card_w - used), style="")
    skip_detail.append("\u2502", style=skip_border)
    body_lines.append(skip_detail)
    body_lines.append(Text(_PAD + "  \u2570" + "\u2500" * card_w + "\u256f", style=skip_border, justify="left"))

    # Layout
    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    visible = body_lines[:viewport_h]

    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    btn_top, btn_mid, btn_bot = build_action_buttons(["Select"], 0)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        Group(*padded_lines),
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )


# ---------------------------------------------------------------------------
# Settings screen
# ---------------------------------------------------------------------------


def _build_settings_screen(
    config_data: dict,
    *,
    scroll_offset: int = 0,
    scroll_meta: dict | None = None,
    width: int = 80,
    height: int = 24,
    action_sel: int = 0,
    shimmer_tick: float | None = None,
    sub_reveal: float | None = None,
) -> Panel:
    """Build the settings dashboard showing current configuration.

    Displays all config values grouped by category with secrets masked.
    Uses SETTINGS_THEME (silver) with shared components.
    """
    from yeaboi.ui.shared._components import SETTINGS_THEME, build_reveal_subtitle, settings_title

    theme = SETTINGS_THEME
    title = settings_title(shimmer_tick)
    sub = build_reveal_subtitle("Current configuration", sub_reveal, pad=_PAD)

    body_lines: list = []

    # ── Transient status message (e.g. after a Data Dir change) ───
    message = config_data.get("_message", "")
    if message:
        body_lines.append(Text(_PAD + "  " + message, style=theme.accent_bright, justify="left"))

    def _heading(text: str) -> None:
        body_lines.append(Text(""))
        h = Text(_PAD + "  ", justify="left")
        h.append(text, style=f"bold {theme.accent}")
        body_lines.append(h)
        body_lines.append(Text(_PAD + "  " + "\u2500" * min(len(text), 40), style=theme.sep, justify="left"))

    def _row(label: str, value: str, value_style: str = "", masked: bool = False) -> None:
        r = Text(_PAD + "    ", justify="left")
        r.append(f"{label}:  ", style=theme.muted)
        if masked and value:
            display = value[:4] + "\u2022" * min(12, len(value) - 4) if len(value) > 4 else "\u2022" * len(value)
            r.append(display, style=value_style or theme.dim)
        elif value:
            r.append(str(value), style=value_style or theme.value)
        else:
            r.append("not set", style=theme.dim)
        body_lines.append(r)

    # Token help sub-lines: where to create the token + the minimum scope it needs.
    # Sourced from the shared TOKEN_HELP registry (same one the setup wizard uses)
    # so both token surfaces stay consistent. The creation URL is a clickable
    # OSC-8 hyperlink; both lines are dim so they read as a secondary hint.
    #
    # Each line MUST render as exactly one visual row — the viewport height math
    # below (visible = body_lines[scroll : scroll + viewport_h]) assumes one row
    # per body line, so a wrapped line would overflow the fixed-height panel. We
    # force single-row with no_wrap + ellipsis; the full scope is always visible in
    # the setup wizard, and wide terminals show it in full here too.
    from yeaboi.ui.provider_select._constants import TOKEN_HELP

    def _token_help(env_var: str) -> None:
        entry = TOKEN_HELP.get(env_var)
        if not entry:
            return
        link = Text(_PAD + "      ", justify="left", no_wrap=True, overflow="ellipsis")
        link.append("↳ create: ", style=theme.muted)
        link.append(entry["url"], style=f"{theme.dim} underline link {entry['url']}")
        body_lines.append(link)
        scope = Text(_PAD + "        ", justify="left", no_wrap=True, overflow="ellipsis")
        scope.append("scope: ", style=theme.muted)
        scope.append(entry["scope"], style=theme.dim)
        body_lines.append(scope)

    # ── LLM Provider ──────────────────────────────────────────────
    _heading("LLM Provider")
    _row("Provider", config_data.get("LLM_PROVIDER", "anthropic"))
    _row("Model", config_data.get("LLM_MODEL", "(default)"))
    _row("Anthropic Key", config_data.get("ANTHROPIC_API_KEY", ""), masked=True)
    _row("OpenAI Key", config_data.get("OPENAI_API_KEY", ""), masked=True)
    _row("Google Key", config_data.get("GOOGLE_API_KEY", ""), masked=True)
    # Ollama is keyless — its server URL/context rows only appear when the user
    # runs local mode (or has customised the vars), keeping the page uncluttered.
    if config_data.get("LLM_PROVIDER", "") == "ollama" or config_data.get("OLLAMA_BASE_URL", ""):
        _row("Ollama URL", config_data.get("OLLAMA_BASE_URL", "") or "http://localhost:11434 (default)")
        _row("Ollama Context", config_data.get("OLLAMA_NUM_CTX", "") or "16384 (default)")

    # ── Jira ──────────────────────────────────────────────────────
    _heading("Jira")
    _row("Base URL", config_data.get("JIRA_BASE_URL", ""))
    _row("Email", config_data.get("JIRA_EMAIL", ""))
    _row("API Token", config_data.get("JIRA_API_TOKEN", ""), masked=True)
    _token_help("JIRA_API_TOKEN")
    _row("Project Key", config_data.get("JIRA_PROJECT_KEY", ""))
    _row("Confluence Space", config_data.get("CONFLUENCE_SPACE_KEY", ""))

    # ── Azure DevOps ──────────────────────────────────────────────
    _heading("Azure DevOps")
    _row("Org URL", config_data.get("AZURE_DEVOPS_ORG_URL", ""))
    _row("Project", config_data.get("AZURE_DEVOPS_PROJECT", ""))
    _row("PAT", config_data.get("AZURE_DEVOPS_TOKEN", ""), masked=True)
    _token_help("AZURE_DEVOPS_TOKEN")
    _row("Team", config_data.get("AZURE_DEVOPS_TEAM", ""))

    # ── GitHub ────────────────────────────────────────────────────
    _heading("GitHub")
    _row("Token", config_data.get("GITHUB_TOKEN", ""), masked=True)
    _token_help("GITHUB_TOKEN")

    # ── Notion ────────────────────────────────────────────────────
    # Independent doc tool (its own integration token, unlike Confluence).
    _heading("Notion")
    _row("Token", config_data.get("NOTION_TOKEN", ""), masked=True)
    _token_help("NOTION_TOKEN")
    _row("Root Page/DB", config_data.get("NOTION_ROOT_PAGE_ID", ""))

    # ── Storage ───────────────────────────────────────────────────
    # One YEABOI_HOME override relocates the whole data tree (exports, logs,
    # sessions DB…). Edited via the Data Dir action button.
    _heading("Storage")
    _row("Data Directory", config_data.get("YEABOI_HOME", "") or "~/.yeaboi (default)")

    # ── Daily Standup delivery ────────────────────────────────────
    # Secrets (Slack webhook, SMTP password) are masked like every other credential.
    _heading("Daily Standup")
    _row("GitHub Repo", config_data.get("STANDUP_GITHUB_REPO", ""))
    _row("Slack Webhook", config_data.get("SLACK_WEBHOOK_URL", ""), masked=True)
    _row("SMTP Host", config_data.get("STANDUP_SMTP_HOST", ""))
    _row("SMTP User", config_data.get("STANDUP_SMTP_USER", ""))
    _row("SMTP Password", config_data.get("STANDUP_SMTP_PASSWORD", ""), masked=True)
    _row("Email Recipients", config_data.get("STANDUP_EMAIL_RECIPIENTS", ""))

    # ── Voice Input ───────────────────────────────────────────────
    # Local, offline dictation (double-tap Space in any text field) — works with every
    # LLM provider, no API key. See docs: "Voice Input".
    _heading("Voice Input")
    from yeaboi.voice import backend_label, is_voice_available

    _voice_ok, _voice_reason = is_voice_available()
    if _voice_ok:
        _row("Dictation", f"available — {backend_label()}", value_style=theme.good)
    else:
        _row("Dictation", f"unavailable — {_voice_reason}", value_style=theme.warn)
    _row("Model Size", config_data.get("VOICE_MODEL", "") or "base (default)")

    # ── AWS Bedrock ───────────────────────────────────────────────
    aws_region = config_data.get("AWS_REGION", "")
    aws_profile = config_data.get("AWS_PROFILE", "")
    if aws_region or aws_profile:
        _heading("AWS Bedrock")
        _row("Region", aws_region)
        _row("Profile", aws_profile)

    # ── Advanced ──────────────────────────────────────────────────
    _heading("Advanced")
    _row("Log Level", config_data.get("LOG_LEVEL", "WARNING"))
    _row("Session Prune Days", config_data.get("SESSION_PRUNE_DAYS", "30"))
    # Tips default on; only the literal "false" disables them (matches is_tips_enabled).
    _tips_on = config_data.get("TIPS_ENABLED", "").strip().lower() != "false"
    _row("Tips", "on" if _tips_on else "off", value_style=theme.good if _tips_on else theme.muted)
    langsmith = "enabled" if config_data.get("LANGSMITH_TRACING") == "true" else "disabled"
    _row("LangSmith", langsmith)
    _row("Config File", config_data.get("_config_path", ""))

    # ── Layout ────────────────────────────────────────────────────
    viewport_h = calc_viewport(height, header_h=6, action_h=4)
    total_lines = len(body_lines)
    max_scroll = max(0, total_lines - viewport_h)
    actual_scroll = min(scroll_offset, max_scroll)
    publish_geometry(scroll_meta, max_scroll, viewport_h)
    visible = body_lines[actual_scroll : actual_scroll + viewport_h]

    _sb_text = build_scrollbar(viewport_h, total_lines, actual_scroll, max_scroll, always_show=True)
    padded_lines: list = list(visible)
    for _ in range(max(0, viewport_h - len(visible))):
        padded_lines.append(Text(""))

    btn_top, btn_mid, btn_bot = build_action_buttons(["Configure", "Log Level", "Data Dir", "Back"], action_sel)

    if _sb_text is not None:
        from rich.table import Table as _SbTable

        _vp_table = _SbTable(
            show_header=False,
            show_edge=False,
            box=None,
            padding=0,
            pad_edge=False,
            expand=True,
        )
        _vp_table.add_column(ratio=1)
        _vp_table.add_column(width=1)
        _vp_table.add_row(Group(*padded_lines), _sb_text)
        viewport_renderable = _vp_table
    else:
        viewport_renderable = Group(*padded_lines)

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        viewport_renderable,
        Text(""),
        btn_top,
        btn_mid,
        btn_bot,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
