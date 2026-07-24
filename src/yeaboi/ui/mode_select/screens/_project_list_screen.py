"""Project list screen and project row composition.

# See docs: "Architecture" — this module composes project cards with
# action buttons into rows, and builds the full project list screen
# with viewport scrolling, delete popup overlay, team analysis section,
# and "+ New Project" / "+ New Analysis" buttons.
"""

from __future__ import annotations

import rich.box
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.mode_select.screens._project_cards import (
    _BTN_W,
    _CARD_H,
    _CARD_SPACING,
    _EXPORT_SUB_BTN_W,
    _PEEK_H,
    _build_action_button,
    _build_empty_state_card,
    _build_new_analysis_card,
    _build_new_project_card,
    _build_peek_above,
    _build_peek_below,
    _build_profile_card,
    _build_project_card,
    _compute_viewport,
)
from yeaboi.ui.shared._animations import BLACK_RGB, lerp_color
from yeaboi.ui.shared._components import PAD, analysis_title, planning_title

_PAD = PAD  # alias for backward compatibility within this module


def _build_project_row(
    project,
    *,
    selected: bool,
    focus: int = 0,
    box_w: int = 48,
    opacity: float = 1.0,
    del_fade: float = 0.0,
    exp_fade: float = 0.0,
    card_fade: float = 0.0,
    pulse: float = 0.0,
    action_btns_visible: float = 0.0,
    show_export_submenu: bool = False,
    submenu_sel: int = 0,
    submenu_html_fade: float = 0.0,
    submenu_md_fade: float = 0.0,
    submenu_jira_fade: float = 0.0,
    submenu_azdevops_fade: float = 0.0,
    submenu_visible: float = 0.0,
    jira_enabled: bool = True,
    azdevops_enabled: bool = False,
) -> Table:
    """Build a project card with optional Delete/Export buttons to its right.

    # See docs: "Architecture" — each project row is a horizontal grid:
    # [project card] [Delete btn] [Export btn], all the same height.
    # Buttons only appear on the selected row and stagger in one by one.
    # When Export is activated, three sub-buttons [HTML] [Markdown] [Jira]
    # fade in to the right.

    action_btns_visible: 0.0-2.0 stagger-reveal for Delete (0->1) and Export (1->2).
    focus: 0 = card focused, 1 = Delete focused, 2 = Export focused.
    show_export_submenu: when True, HTML, Markdown, and Jira buttons appear.
    submenu_sel: 0 = HTML, 1 = Markdown, 2 = Jira (which button is selected).
    """
    card = _build_project_card(
        project,
        selected=selected,
        box_w=box_w,
        opacity=opacity,
        card_fade=card_fade,
        pulse=pulse,
    )

    row = Table.grid(padding=(0, 1, 0, 0), pad_edge=False)
    row.add_column(width=box_w)

    # Staggered reveal of action buttons — Delete appears first, then Export.
    # action_btns_visible (0->2) controls staggered opacity per button.
    # Non-selected rows get action_btns_visible=0 so no buttons render.
    del_opacity = min(1.0, max(0.0, action_btns_visible))
    exp_opacity = min(1.0, max(0.0, action_btns_visible - 1.0))

    action_btns: list = []
    if del_opacity > 0:
        action_btns.append(
            _build_action_button(
                "Delete",
                focused=(selected and focus == 1),
                card_selected=selected,
                color=(220, 60, 60),
                opacity=opacity * del_opacity,
                fade_t=del_fade,
            )
        )
        row.add_column(width=_BTN_W)
    if exp_opacity > 0:
        action_btns.append(
            _build_action_button(
                "Export",
                focused=(selected and focus == 2 and not show_export_submenu),
                card_selected=selected,
                color=(70, 100, 180),
                opacity=opacity * exp_opacity,
                fade_t=exp_fade,
            )
        )
        row.add_column(width=_BTN_W)

    if show_export_submenu and submenu_visible > 0 and exp_opacity > 0:
        # Staggered reveal: each button fades in as submenu_visible passes
        # its index (0=HTML, 1=Markdown, 2+=tracker buttons). On close, reverse order.
        html_opacity = min(1.0, max(0.0, submenu_visible))
        md_opacity = min(1.0, max(0.0, submenu_visible - 1.0))
        jira_opacity = min(1.0, max(0.0, submenu_visible - 2.0))
        azdevops_opacity = min(1.0, max(0.0, submenu_visible - 3.0))

        # Build dynamic submenu: HTML + Markdown always, then configured trackers
        _sub_items: list[tuple[str, int, float, float, bool]] = [
            ("HTML", 0, submenu_html_fade, html_opacity, True),
            ("Markdown", 1, submenu_md_fade, md_opacity, True),
        ]
        _next_idx = 2
        if jira_enabled:
            _sub_items.append(("Jira", _next_idx, submenu_jira_fade, jira_opacity, True))
            _next_idx += 1
        if azdevops_enabled:
            _sub_items.append(("Azure DevOps", _next_idx, submenu_azdevops_fade, azdevops_opacity, True))
            _next_idx += 1

        sub_btns: list = []
        for btn_label, btn_idx, btn_fade, btn_opacity, _enabled in _sub_items:
            if btn_opacity > 0:
                # Adapt width to label length (min _EXPORT_SUB_BTN_W)
                _btn_w = max(_EXPORT_SUB_BTN_W, len(btn_label) + 4)
                sub_btns.append(
                    _build_action_button(
                        btn_label,
                        focused=(selected and submenu_sel == btn_idx),
                        card_selected=selected,
                        color=(255, 255, 255),
                        opacity=opacity * btn_opacity,
                        fade_t=btn_fade,
                        btn_w=_btn_w,
                    )
                )
                row.add_column(width=_btn_w)
        row.add_row(card, *action_btns, *sub_btns)
    else:
        row.add_row(card, *action_btns)
    return row


def _build_profile_row(
    profile,
    *,
    selected: bool,
    focus: int = 0,
    box_w: int = 48,
    opacity: float = 1.0,
    del_fade: float = 0.0,
    exp_fade: float = 0.0,
    card_fade: float = 0.0,
    pulse: float = 0.0,
    action_btns_visible: float = 0.0,
    show_export_submenu: bool = False,
    submenu_sel: int = 0,
    submenu_html_fade: float = 0.0,
    submenu_md_fade: float = 0.0,
    submenu_visible: float = 0.0,
) -> Table:
    """Build a profile card with Delete/Export buttons to its right.

    Same pattern as _build_project_row — Delete (red) + Export (blue),
    with export submenu limited to HTML + Markdown.
    """
    card = _build_profile_card(
        profile,
        selected=selected,
        box_w=box_w,
        opacity=opacity,
        card_fade=card_fade,
        pulse=pulse,
    )

    row = Table.grid(padding=(0, 1, 0, 0), pad_edge=False)
    row.add_column(width=box_w)

    del_opacity = min(1.0, max(0.0, action_btns_visible))
    exp_opacity = min(1.0, max(0.0, action_btns_visible - 1.0))

    action_btns: list = []
    if del_opacity > 0:
        action_btns.append(
            _build_action_button(
                "Delete",
                focused=(selected and focus == 1),
                card_selected=selected,
                color=(220, 60, 60),
                opacity=opacity * del_opacity,
                fade_t=del_fade,
            )
        )
        row.add_column(width=_BTN_W)
    if exp_opacity > 0:
        action_btns.append(
            _build_action_button(
                "Export",
                focused=(selected and focus == 2 and not show_export_submenu),
                card_selected=selected,
                color=(70, 100, 180),
                opacity=opacity * exp_opacity,
                fade_t=exp_fade,
            )
        )
        row.add_column(width=_BTN_W)

    # Profile export submenu: HTML + Markdown only (no tracker sync)
    if show_export_submenu and submenu_visible > 0 and exp_opacity > 0:
        html_opacity = min(1.0, max(0.0, submenu_visible))
        md_opacity = min(1.0, max(0.0, submenu_visible - 1.0))
        _sub_items = [
            ("HTML", 0, submenu_html_fade, html_opacity),
            ("Markdown", 1, submenu_md_fade, md_opacity),
        ]
        sub_btns: list = []
        for btn_label, btn_idx, btn_fade, btn_opacity in _sub_items:
            if btn_opacity > 0:
                _btn_w = max(_EXPORT_SUB_BTN_W, len(btn_label) + 4)
                sub_btns.append(
                    _build_action_button(
                        btn_label,
                        focused=(selected and submenu_sel == btn_idx),
                        card_selected=selected,
                        color=(255, 255, 255),
                        opacity=opacity * btn_opacity,
                        fade_t=btn_fade,
                        btn_w=_btn_w,
                    )
                )
                row.add_column(width=_btn_w)
        row.add_row(card, *action_btns, *sub_btns)
    else:
        row.add_row(card, *action_btns)
    return row


def _build_project_list_screen(
    projects,
    selected: int,
    *,
    width: int = 80,
    height: int = 24,
    card_opacity: float = 1.0,
    cards_visible: float = 999.0,
    show_subtitle: bool = True,
    focus: int = 0,
    del_fade: float = 0.0,
    exp_fade: float = 0.0,
    card_fade: float = 0.0,
    pulse: float = 0.0,
    action_btns_visible: float = 0.0,
    show_export_submenu: bool = False,
    submenu_sel: int = 0,
    submenu_html_fade: float = 0.0,
    submenu_md_fade: float = 0.0,
    submenu_jira_fade: float = 0.0,
    submenu_azdevops_fade: float = 0.0,
    submenu_visible: float = 0.0,
    delete_popup_name: str = "",
    delete_popup_t: float = 0.0,
    delete_popup_pulse: float = 0.0,
    delete_popup_flash: float = 0.0,
    team_popup_t: float = 0.0,
    team_popup_sel: int = 0,
    team_popup_pulse: float = 0.0,
    team_popup_message: str = "",
    jira_enabled: bool = True,
    azdevops_enabled: bool = False,
    # ── Team Analysis section ──────────────────────────────────────
    profiles: list | None = None,
    new_analysis_labels: list[str] | None = None,
    profile_focus: int = 0,
    profile_del_fade: float = 0.0,
    profile_card_fade: float = 0.0,
    profile_pulse: float = 0.0,
    profile_action_btns_visible: float = 0.0,
    profile_export_submenu: bool = False,
    profile_submenu_sel: int = 0,
    profile_submenu_html_fade: float = 0.0,
    profile_submenu_md_fade: float = 0.0,
    profile_submenu_visible: float = 0.0,
    profile_exp_fade: float = 0.0,
    mode: str = "planning",
    shimmer_tick: float | None = None,
) -> Panel:
    """Build the project list screen with title pinned at top.

    mode="planning": Shows "Your projects" section + optional Team Analysis.
    mode="analysis": Shows only profile cards + analysis buttons (no projects).
    shimmer_tick: if set, animates the title's travelling highlight.
    """
    title = analysis_title(shimmer_tick) if mode == "analysis" else planning_title(shimmer_tick)

    sub_color = lerp_color(card_opacity, BLACK_RGB, (100, 100, 100))
    if mode == "analysis":
        sub = Text(_PAD + "Your analyses", style=sub_color, justify="left") if show_subtitle else Text("")
    else:
        sub = Text(_PAD + "Your projects", style=sub_color, justify="left") if show_subtitle else Text("")

    # Card width leaves room for two action buttons + gaps to the right.
    box_w = min(56, width - 12 - 2 * _BTN_W)
    box_w = max(30, box_w)  # floor so it never collapses
    body: list = []
    body_h = 0

    # Left pad matches _PAD (4 chars) so cards align with the ASCII title
    _card_pad = (0, 0, 0, len(_PAD))

    # Layout: blank + title(6) + blank + subtitle + blank + [body]
    inner_h = height - 4
    header_h = 10  # blank + title(6) + blank + subtitle + blank

    _profiles = profiles or []
    _analysis_labels = new_analysis_labels or []

    # In analysis mode, skip project items entirely
    if mode == "analysis":
        n_project_items = 0
    else:
        n_project_items = (len(projects) + 1) if projects else 2
    n_team_items = len(_profiles) + len(_analysis_labels)
    n_items = n_project_items + n_team_items

    _use_viewport = mode == "analysis" or bool(projects)

    if not _use_viewport and not projects:
        # No scrolling needed for empty state (only 2 items + team section)
        body.append(
            Padding(
                _build_empty_state_card(selected=(selected == 0), box_w=box_w, opacity=card_opacity),
                _card_pad,
            )
        )
        body_h += 6  # empty state card: border(2) + padding(2) + content(2)
        body.append(Text(""))
        body_h += 1
        body.append(
            Padding(
                _build_new_project_card(
                    selected=(selected == 1),
                    box_w=box_w,
                    opacity=card_opacity,
                ),
                _card_pad,
            )
        )
        body_h += 3

        # Team Analysis section (after "+ New Project")
        if _profiles or _analysis_labels:
            body.append(Text(""))
            body_h += 1
            team_sub = Text(_PAD + "Team Analysis", style=sub_color, justify="left")
            body.append(team_sub)
            body_h += 1
            body.append(Text(""))
            body_h += 1

            _team_start_idx = 2  # after empty_state + new_project
            for pi, prof in enumerate(_profiles):
                idx = _team_start_idx + pi
                is_sel = idx == selected
                row = _build_profile_row(
                    prof,
                    selected=is_sel,
                    focus=profile_focus if is_sel else 0,
                    box_w=box_w,
                    opacity=card_opacity,
                    del_fade=profile_del_fade if is_sel else 0.0,
                    exp_fade=profile_exp_fade if is_sel else 0.0,
                    card_fade=profile_card_fade if is_sel else 0.0,
                    pulse=profile_pulse if is_sel else 0.0,
                    action_btns_visible=profile_action_btns_visible if is_sel else 0.0,
                    show_export_submenu=profile_export_submenu if is_sel else False,
                    submenu_sel=profile_submenu_sel if is_sel else 0,
                    submenu_html_fade=profile_submenu_html_fade if is_sel else 0.0,
                    submenu_md_fade=profile_submenu_md_fade if is_sel else 0.0,
                    submenu_visible=profile_submenu_visible if is_sel else 0.0,
                )
                body.append(Padding(row, _card_pad))
                body_h += _CARD_H
                if pi < len(_profiles) - 1 or _analysis_labels:
                    body.append(Text(""))
                    body_h += _CARD_SPACING

            for ai, al in enumerate(_analysis_labels):
                idx = _team_start_idx + len(_profiles) + ai
                card = _build_new_analysis_card(
                    label=al,
                    selected=(idx == selected),
                    box_w=box_w,
                    opacity=card_opacity,
                )
                body.append(Padding(card, _card_pad))
                body_h += 3
                if ai < len(_analysis_labels) - 1:
                    body.append(Text(""))
                    body_h += _CARD_SPACING
    if _use_viewport:
        # Viewport scrolling — show only cards that fit on screen with peek
        # stubs at the edges hinting at off-screen cards.
        available_h = inner_h - header_h
        start, end, show_above, show_below = _compute_viewport(n_items, selected, available_h)

        # Helper: map flat index to display title for peek stubs
        if mode == "analysis":
            _proj_boundary = 0  # no projects in analysis mode
            _team_section_start = 0
        else:
            _proj_boundary = len(projects)  # index of "+ New Project"
            _team_section_start = _proj_boundary + 1
        _profile_end = _team_section_start + len(_profiles)

        def _item_title(idx: int) -> str:
            if mode != "analysis" and idx < _proj_boundary:
                return projects[idx].name
            if mode != "analysis" and idx == _proj_boundary:
                return "+ New Project"
            if idx < _profile_end:
                p = _profiles[idx - _team_section_start]
                _tn = getattr(p, "team_name", "")
                return f"{p.source}/{p.project_key}" + (f" — {_tn}" if _tn else "")
            # Analysis buttons
            ai = idx - _profile_end
            if ai < len(_analysis_labels):
                return _analysis_labels[ai]
            return ""

        # Peek above
        if show_above:
            body.append(
                Padding(
                    _build_peek_above(box_w=box_w, opacity=card_opacity, title=_item_title(start - 1)),
                    _card_pad,
                )
            )
            body_h += _PEEK_H

        # Full cards in viewport — staggered reveal via cards_visible.
        for vi, i in enumerate(range(start, end)):
            if vi >= cards_visible:
                break  # remaining cards aren't visible yet
            item_opacity = card_opacity

            # Insert "Team Analysis" section header before the first team item
            # (only in planning mode — analysis mode already has the right title)
            if mode != "analysis" and i == _team_section_start and (_profiles or _analysis_labels):
                body.append(Text(""))
                body_h += 1
                team_sub = Text(_PAD + "Team Analysis", style=sub_color, justify="left")
                body.append(team_sub)
                body_h += 1
                body.append(Text(""))
                body_h += 1

            if mode != "analysis" and i < _proj_boundary:
                # Project row: card + Delete + Export buttons
                is_sel = i == selected
                row = _build_project_row(
                    projects[i],
                    selected=is_sel,
                    focus=focus if is_sel else 0,
                    box_w=box_w,
                    opacity=item_opacity,
                    del_fade=del_fade if is_sel else 0.0,
                    exp_fade=exp_fade if is_sel else 0.0,
                    card_fade=card_fade if is_sel else 0.0,
                    pulse=pulse if is_sel else 0.0,
                    action_btns_visible=action_btns_visible if is_sel else 0.0,
                    show_export_submenu=show_export_submenu if is_sel else False,
                    submenu_sel=submenu_sel if is_sel else 0,
                    submenu_html_fade=submenu_html_fade if is_sel else 0.0,
                    submenu_md_fade=submenu_md_fade if is_sel else 0.0,
                    submenu_jira_fade=submenu_jira_fade if is_sel else 0.0,
                    submenu_azdevops_fade=submenu_azdevops_fade if is_sel else 0.0,
                    submenu_visible=submenu_visible if is_sel else 0.0,
                    jira_enabled=jira_enabled,
                    azdevops_enabled=azdevops_enabled,
                )
                body.append(Padding(row, _card_pad))
            elif mode != "analysis" and i == _proj_boundary:
                # "+ New Project" card (planning mode only)
                card = _build_new_project_card(
                    selected=(i == selected),
                    box_w=box_w,
                    opacity=item_opacity,
                )
                body.append(Padding(card, _card_pad))
            elif i < _profile_end:
                # Profile row: card + Re-analyse + Export buttons
                is_sel = i == selected
                pi = i - _team_section_start
                row = _build_profile_row(
                    _profiles[pi],
                    selected=is_sel,
                    focus=profile_focus if is_sel else 0,
                    box_w=box_w,
                    opacity=item_opacity,
                    del_fade=profile_del_fade if is_sel else 0.0,
                    exp_fade=profile_exp_fade if is_sel else 0.0,
                    card_fade=profile_card_fade if is_sel else 0.0,
                    pulse=profile_pulse if is_sel else 0.0,
                    action_btns_visible=profile_action_btns_visible if is_sel else 0.0,
                    show_export_submenu=profile_export_submenu if is_sel else False,
                    submenu_sel=profile_submenu_sel if is_sel else 0,
                    submenu_html_fade=profile_submenu_html_fade if is_sel else 0.0,
                    submenu_md_fade=profile_submenu_md_fade if is_sel else 0.0,
                    submenu_visible=profile_submenu_visible if is_sel else 0.0,
                )
                body.append(Padding(row, _card_pad))
            else:
                # "+ New Analysis" card(s)
                ai = i - _profile_end
                lbl = _analysis_labels[ai] if ai < len(_analysis_labels) else "+ New Analysis"
                card = _build_new_analysis_card(
                    label=lbl,
                    selected=(i == selected),
                    box_w=box_w,
                    opacity=item_opacity,
                )
                body.append(Padding(card, _card_pad))

            body_h += _CARD_H
            if i < end - 1:
                body.append(Text(""))
                body_h += _CARD_SPACING

        # Peek below
        if show_below:
            body.append(
                Padding(
                    _build_peek_below(box_w=box_w, opacity=card_opacity, title=_item_title(end)),
                    _card_pad,
                )
            )
            body_h += _PEEK_H

    remaining = max(0, inner_h - header_h - body_h)

    # Delete popup — overlays the bottom of the screen
    popup_before: list = []
    popup_mid: list = []
    popup_after: list = []
    if delete_popup_name and delete_popup_t > 0:
        popup_msg = f'Delete "{delete_popup_name}"?  Enter to confirm'
        panel_inner_w = width - 6
        popup_w = min(panel_inner_w, max(40, len(popup_msg) + 8))

        import math as _math

        dark_red = (140, 30, 30)
        bright_red = (255, 90, 90)
        t = (_math.cos(delete_popup_pulse * 3) + 1) / 2
        br = int(dark_red[0] + (bright_red[0] - dark_red[0]) * t)
        bg = int(dark_red[1] + (bright_red[1] - dark_red[1]) * t)
        bb = int(dark_red[2] + (bright_red[2] - dark_red[2]) * t)

        if delete_popup_flash > 0:
            br = int(br + (255 - br) * delete_popup_flash)
            bg = int(bg + (255 - bg) * delete_popup_flash)
            bb = int(bb + (255 - bb) * delete_popup_flash)

        border_style = f"rgb({br},{bg},{bb})"
        inner_w = popup_w - 2

        msg_pad_l = max(0, (inner_w - len(popup_msg)) // 2)
        msg_pad_r = max(0, inner_w - len(popup_msg) - msg_pad_l)
        centered_msg = " " * msg_pad_l + popup_msg + " " * msg_pad_r

        h_pad = " " * max(0, (panel_inner_w - popup_w) // 2)

        line_top = Text(h_pad, justify="left")
        line_top.append("\u256d" + "\u2500" * inner_w + "\u256e", style=border_style)
        line_blank1 = Text(h_pad, justify="left")
        line_blank1.append("\u2502" + " " * inner_w + "\u2502", style=border_style)
        line_msg = Text(h_pad, justify="left")
        line_msg.append("\u2502", style=border_style)
        line_msg.append(centered_msg, style="bold white")
        line_msg.append("\u2502", style=border_style)
        line_blank2 = Text(h_pad, justify="left")
        line_blank2.append("\u2502" + " " * inner_w + "\u2502", style=border_style)
        line_bot = Text(h_pad, justify="left")
        line_bot.append("\u256e" + "\u2500" * inner_w + "\u256f", style=border_style)

        popup_lines = [line_top, line_blank1, line_msg, line_blank2, line_bot]
        popup_h = len(popup_lines)

        overflow = popup_h - remaining
        while overflow > 0 and body and body_h > 0:
            last = body[-1]
            if isinstance(last, Text) and not last.plain.strip():
                item_h = _CARD_SPACING
            elif isinstance(last, Padding) and isinstance(last.renderable, Group):
                item_h = _PEEK_H
            else:
                item_h = _CARD_H
            body.pop()
            body_h -= item_h
            overflow -= item_h
        remaining = max(0, inner_h - header_h - body_h)

        resting_above = max(0, remaining - popup_h)
        start_above = remaining
        current_above = int(start_above + (resting_above - start_above) * delete_popup_t)
        current_below = max(0, remaining - current_above - popup_h)

        popup_before = [Text("") for _ in range(current_above)]
        popup_mid = popup_lines
        popup_after = [Text("") for _ in range(current_below)]
    else:
        popup_before = [Text("") for _ in range(remaining)]

    # Team analysis staleness popup — blue-bordered overlay.
    # Uses team_popup_message for dynamic text (staleness or "no profile found").
    if team_popup_t > 0 and not (delete_popup_name and delete_popup_t > 0):
        import math as _math

        panel_inner_w = width - 6
        popup_w = min(panel_inner_w, 42)
        inner_w = popup_w - 2

        dark_blue = (40, 60, 120)
        bright_blue = (100, 140, 220)
        t_osc = (_math.cos(team_popup_pulse * 3) + 1) / 2
        br = int(dark_blue[0] + (bright_blue[0] - dark_blue[0]) * t_osc)
        bg = int(dark_blue[1] + (bright_blue[1] - dark_blue[1]) * t_osc)
        bb = int(dark_blue[2] + (bright_blue[2] - dark_blue[2]) * t_osc)
        border_style = f"rgb({br},{bg},{bb})"

        h_pad = " " * max(0, (panel_inner_w - popup_w) // 2)

        _both = jira_enabled and azdevops_enabled
        if _both:
            btns = [" Jira ", " Azure DevOps ", " Both ", " Skip "]
        else:
            btns = [" Yes, Analyse ", " Skip "]

        btn_gap = "  "
        btn_total_w = sum(len(b) for b in btns) + len(btn_gap) * (len(btns) - 1)
        btn_pad_l = max(0, (inner_w - btn_total_w) // 2)

        def _team_line(content_str: str = "", style: str = "") -> Text:
            line = Text(h_pad, justify="left")
            line.append("\u2502", style=border_style)
            if content_str:
                pad_l = max(0, (inner_w - len(content_str)) // 2)
                pad_r = max(0, inner_w - len(content_str) - pad_l)
                line.append(" " * pad_l + content_str + " " * pad_r, style=style or "white")
            else:
                line.append(" " * inner_w)
            line.append("\u2502", style=border_style)
            return line

        # Dynamic message based on staleness
        _msg = team_popup_message or "Re-analyse your team board?"
        _title_str = "Re-analyse Team Board?"
        _rec_style = "rgb(220,180,60)"

        # Wrap message into max 3 lines of inner_w - 4 chars
        _max_line_w = inner_w - 4
        _msg_words = _msg.split()
        _msg_lines: list[str] = []
        _line_buf = ""
        for w in _msg_words:
            if len(_line_buf) + len(w) + 1 > _max_line_w:
                _msg_lines.append(_line_buf.strip())
                _line_buf = w + " "
            else:
                _line_buf += w + " "
        if _line_buf.strip():
            _msg_lines.append(_line_buf.strip())

        t_lines = []
        _top = Text(h_pad, justify="left")
        _top.append("\u256d" + "\u2500" * inner_w + "\u256e", style=border_style)
        t_lines.append(_top)
        t_lines.append(_team_line())
        t_lines.append(_team_line(_title_str, "bold white"))
        t_lines.append(_team_line())
        for ml in _msg_lines[:3]:
            t_lines.append(_team_line(ml, _rec_style))
        t_lines.append(_team_line())

        # Button row
        btn_line = Text(h_pad, justify="left")
        btn_line.append("\u2502", style=border_style)
        btn_line.append(" " * btn_pad_l)
        used_w = btn_pad_l
        for bi, blabel in enumerate(btns):
            if bi > 0:
                btn_line.append(btn_gap)
                used_w += len(btn_gap)
            style = "bold white" if bi == team_popup_sel else "dim"
            btn_line.append(blabel, style=style)
            used_w += len(blabel)
        btn_line.append(" " * max(0, inner_w - used_w))
        btn_line.append("\u2502", style=border_style)
        t_lines.append(btn_line)

        t_lines.append(_team_line())
        _bot = Text(h_pad, justify="left")
        _bot.append("\u2570" + "\u2500" * inner_w + "\u256f", style=border_style)
        t_lines.append(_bot)

        team_popup_h = len(t_lines)

        overflow = team_popup_h - remaining
        while overflow > 0 and body and body_h > 0:
            last = body[-1]
            if isinstance(last, Text) and not last.plain.strip():
                item_h = _CARD_SPACING
            elif isinstance(last, Padding) and isinstance(last.renderable, Group):
                item_h = _PEEK_H
            else:
                item_h = _CARD_H
            body.pop()
            body_h -= item_h
            overflow -= item_h
        remaining = max(0, inner_h - header_h - body_h)
        popup_before = [Text("") for _ in range(remaining)]

        total_popup_space = remaining
        resting_above = max(0, total_popup_space - team_popup_h)
        start_above = total_popup_space
        current_above = int(start_above + (resting_above - start_above) * team_popup_t)
        current_below = max(0, total_popup_space - current_above - team_popup_h)
        popup_before = [Text("") for _ in range(current_above)]
        popup_mid = t_lines
        popup_after = [Text("") for _ in range(current_below)]

    content = Group(
        Text(""),
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *popup_before,
        *popup_mid,
        *popup_after,
    )

    return Panel(
        content,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
