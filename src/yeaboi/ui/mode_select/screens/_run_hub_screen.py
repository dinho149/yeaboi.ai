"""Saved-runs hub screen for the standup / retro / reporting / performance modes.

These modes each append every run to a history table but historically the TUI only
ever showed the latest one. This screen surfaces that history as a browsable list —
the same Open / Delete / Export experience Planning and Analysis already offer via
``_build_project_list_screen`` — so a run is no longer visually overwritten each time.

Design (see the "Saved-Sessions Hub" plan): rather than thread a third ``mode`` value
through ``_build_project_list_screen`` (which is coupled to projects/profiles, team
sections, and tracker export), this is a purpose-built sibling that REUSES the same
low-level primitives (``_build_project_row`` for the card + Delete/Export buttons,
``_build_new_project_card``, ``_build_empty_state_card``, the viewport peek helpers).
Each ``RunSummary`` is adapted to a ``ProjectSummary`` (``RunSummary.to_project``) so a
run card renders identically to a planning/analysis card. Export offers HTML + Markdown
only (no tracker sync for a point-in-time snapshot), so ``_build_project_row`` is called
with ``jira_enabled=False, azdevops_enabled=False``.

# See docs: "Architecture" — TUI system, shared Panel page structure
"""

from __future__ import annotations

from collections.abc import Callable

import rich.box
from rich.console import Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from yeaboi.ui.mode_select.screens._project_cards import (
    _BTN_W,
    _CARD_H,
    _CARD_SPACING,
    _PEEK_H,
    RunSummary,
    _build_empty_state_card,
    _build_new_project_card,
    _build_peek_above,
    _build_peek_below,
    _compute_viewport,
)
from yeaboi.ui.mode_select.screens._project_list_screen import _build_project_row
from yeaboi.ui.shared._animations import BLACK_RGB, lerp_color
from yeaboi.ui.shared._components import PAD

_PAD = PAD


def _build_run_hub_screen(
    runs: list[RunSummary],
    selected: int,
    *,
    title_fn: Callable[..., Text],
    subtitle: str = "Saved runs",
    message: str = "",
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
    submenu_visible: float = 0.0,
    delete_popup_name: str = "",
    delete_popup_t: float = 0.0,
    delete_popup_pulse: float = 0.0,
    delete_popup_flash: float = 0.0,
    new_label: str = "+ New run",
    empty_title: str = "No saved runs yet",
    empty_subtitle: str = "Press Enter to start your first run",
    shimmer_tick: float | None = None,
) -> Panel:
    """Build the saved-runs hub: a scrollable list of past runs + a "+ New run" card.

    The item at index ``len(runs)`` is the "+ New run" card. Selecting a run row and
    pressing Right reveals its Delete/Export buttons (``focus`` 1/2); ``focus`` 0 = card,
    where Enter opens the saved snapshot. Mirrors the planning list's key/animation model.

    title_fn: the mode's title function (e.g. ``standup_title``), called with shimmer_tick.
    """
    title = title_fn(shimmer_tick)

    sub_color = lerp_color(card_opacity, BLACK_RGB, (100, 100, 100))
    if message:
        # A transient toast (export/delete/run-again result) takes the subtitle row so
        # list-level actions give feedback without disturbing the fixed header height.
        sub = Text(_PAD + message, style="rgb(120,200,140)", justify="left")
    elif show_subtitle:
        sub = Text(_PAD + subtitle, style=sub_color, justify="left")
    else:
        sub = Text("")

    # Card width leaves room for two action buttons + gaps to the right (same as planning).
    box_w = min(56, width - 12 - 2 * _BTN_W)
    box_w = max(30, box_w)
    body: list = []
    body_h = 0
    _card_pad = (0, 0, 0, len(_PAD))

    inner_h = height - 4
    header_h = 10  # blank + title(6) + blank + subtitle + blank

    n_items = len(runs) + 1  # runs + "+ New run" card
    _new_idx = len(runs)

    available_h = inner_h - header_h
    start, end, show_above, show_below = _compute_viewport(n_items, selected, available_h)

    def _item_title(idx: int) -> str:
        if idx < len(runs):
            return runs[idx].title
        return new_label

    if show_above:
        body.append(
            Padding(_build_peek_above(box_w=box_w, opacity=card_opacity, title=_item_title(start - 1)), _card_pad)
        )
        body_h += _PEEK_H

    for vi, i in enumerate(range(start, end)):
        if vi >= cards_visible:
            break
        if i < len(runs):
            is_sel = i == selected
            row = _build_project_row(
                runs[i].to_project(),
                selected=is_sel,
                focus=focus if is_sel else 0,
                box_w=box_w,
                opacity=card_opacity,
                del_fade=del_fade if is_sel else 0.0,
                exp_fade=exp_fade if is_sel else 0.0,
                card_fade=card_fade if is_sel else 0.0,
                pulse=pulse if is_sel else 0.0,
                action_btns_visible=action_btns_visible if is_sel else 0.0,
                show_export_submenu=show_export_submenu if is_sel else False,
                submenu_sel=submenu_sel if is_sel else 0,
                submenu_html_fade=submenu_html_fade if is_sel else 0.0,
                submenu_md_fade=submenu_md_fade if is_sel else 0.0,
                submenu_visible=submenu_visible if is_sel else 0.0,
                jira_enabled=False,  # a saved snapshot exports to files only
                azdevops_enabled=False,
            )
            body.append(Padding(row, _card_pad))
        else:
            body.append(
                Padding(
                    _build_new_project_card(
                        selected=(i == selected), box_w=box_w, opacity=card_opacity, label_text=new_label
                    ),
                    _card_pad,
                )
            )
        body_h += _CARD_H
        if i < end - 1:
            body.append(Text(""))
            body_h += _CARD_SPACING

    if show_below:
        body.append(Padding(_build_peek_below(box_w=box_w, opacity=card_opacity, title=_item_title(end)), _card_pad))
        body_h += _PEEK_H

    # Empty state: no runs yet → a hint card above the "+ New run" card.
    if not runs:
        body = []
        body_h = 0
        body.append(
            Padding(
                _build_empty_state_card(
                    selected=False,
                    box_w=box_w,
                    opacity=card_opacity,
                    title=empty_title,
                    subtitle=empty_subtitle,
                ),
                _card_pad,
            )
        )
        body_h += 6
        body.append(Text(""))
        body_h += 1
        body.append(
            Padding(
                _build_new_project_card(
                    selected=(selected == 0), box_w=box_w, opacity=card_opacity, label_text=new_label
                ),
                _card_pad,
            )
        )
        body_h += 3

    remaining = max(0, inner_h - header_h - body_h)

    # Delete confirmation popup — red-bordered overlay sliding up from the bottom.
    # Ported from _build_project_list_screen so the confirm UX matches exactly.
    popup_before: list = []
    popup_mid: list = []
    popup_after: list = []
    if delete_popup_name and delete_popup_t > 0:
        import math as _math

        popup_msg = f'Delete "{delete_popup_name}"?  Enter to confirm'
        panel_inner_w = width - 6
        popup_w = min(panel_inner_w, max(40, len(popup_msg) + 8))

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
        line_top.append("╭" + "─" * inner_w + "╮", style=border_style)
        line_blank1 = Text(h_pad, justify="left")
        line_blank1.append("│" + " " * inner_w + "│", style=border_style)
        line_msg = Text(h_pad, justify="left")
        line_msg.append("│", style=border_style)
        line_msg.append(centered_msg, style="bold white")
        line_msg.append("│", style=border_style)
        line_blank2 = Text(h_pad, justify="left")
        line_blank2.append("│" + " " * inner_w + "│", style=border_style)
        line_bot = Text(h_pad, justify="left")
        line_bot.append("╰" + "─" * inner_w + "╯", style=border_style)

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
        padding=(0, 2),
    )
