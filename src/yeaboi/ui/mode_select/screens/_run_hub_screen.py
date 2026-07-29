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
from yeaboi.ui.shared._components import PAD, Theme, build_page_panel

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
    theme: Theme | None = None,
) -> Panel:
    """Build the saved-runs hub: a scrollable list of past runs + a "+ New run" card.

    The item at index ``len(runs)`` is the "+ New run" card. Selecting a run row and
    pressing Right reveals its Delete/Export buttons (``focus`` 1/2); ``focus`` 0 = card,
    where Enter opens the saved snapshot. Mirrors the planning list's key/animation model.

    title_fn: the mode's title function (e.g. ``standup_title``), called with shimmer_tick.
    theme: the mode's Theme — its ``bg`` tints the whole page (None → neutral dark base).
    """
    title = title_fn(shimmer_tick)

    sub_color = lerp_color(card_opacity, BLACK_RGB, (100, 100, 100))
    # Transient toasts (export/delete/run-again results) are spoken by the duck now
    # (see _duck_say below), so the subtitle row keeps its own label.
    if show_subtitle:
        sub = Text(_PAD + subtitle, style=sub_color, justify="left")
    else:
        sub = Text("")

    # Card width leaves room for two action buttons + gaps to the right (same as planning).
    box_w = min(56, width - 12 - 2 * _BTN_W)
    box_w = max(30, box_w)
    body: list = []
    body_h = 0
    _card_pad = (0, 0, 0, len(_PAD))

    # One blank line above the title — the exact offset the select→page slide
    # eases to (its ``end_offset = 1``) and the same layout the planning/analysis
    # project list uses. Anything larger (e.g. the menu's own top-row offset) makes
    # the title drop a couple of rows the instant the slide hands off to the hub.
    title_offset = 1
    inner_h = height - 4
    header_h = title_offset + 5  # title_offset blanks + title(2) + blank + subtitle + blank

    n_items = len(runs) + 1  # runs + "+ New run" card
    _new_idx = len(runs)

    available_h = inner_h - header_h
    start, end, show_above, show_below = _compute_viewport(n_items, selected, available_h)

    # Click hit-testing: record each visible card's 1-based row span so the hub loop
    # can map a mouse click onto an item. Rows are absolute (panel border + top pad
    # + header, then the body). The "+ New" card renders 3 rows tall; run cards 5.
    card_regions: list[tuple[int, int, int]] = []  # (y_top, y_bot, item_index)
    # Delete/Export button rects for the selected run card (empty when the "+ New"
    # card is focused or the buttons are hidden). Column geometry mirrors
    # _build_project_row's Table.grid: panel border(1) + panel L-pad(2) + card
    # L-pad(len PAD) precede the card; then a 1-col grid gap, Delete (_BTN_W wide),
    # a 1-col gap, and Export (_BTN_W wide). Verified against a real render.
    btn_regions: list[tuple[int, int, int, int, str]] = []  # (x0, y0, x1, y1, label)
    _card_x0 = 3 + len(_PAD) + 1  # 1-based first column of the card box
    _new_card_h = 3
    _row_cursor = title_offset + 8

    def _item_title(idx: int) -> str:
        if idx < len(runs):
            return runs[idx].title
        return new_label

    if show_above:
        body.append(
            Padding(_build_peek_above(box_w=box_w, opacity=card_opacity, title=_item_title(start - 1)), _card_pad)
        )
        body_h += _PEEK_H
        _row_cursor += _PEEK_H

    for vi, i in enumerate(range(start, end)):
        if vi >= cards_visible:
            break
        _card_h = _CARD_H if i < len(runs) else _new_card_h
        card_regions.append((_row_cursor, _row_cursor + _card_h - 1, i))
        # Record the Delete/Export button rects when this run card is the selected
        # one and its action buttons are revealed. The buttons occupy the same rows
        # as the card. Delete shows once action_btns_visible > 0, Export once > 1.0
        # (mirrors _build_project_row's del_opacity / exp_opacity gating).
        if i == selected and i < len(runs):
            _y0, _y1 = _row_cursor, _row_cursor + _card_h - 1
            _del_op = min(1.0, max(0.0, action_btns_visible))
            _exp_op = min(1.0, max(0.0, action_btns_visible - 1.0))
            _del_x0 = _card_x0 + box_w + 1  # 1-col grid gap after the card
            if _del_op > 0:
                btn_regions.append((_del_x0, _y0, _del_x0 + _BTN_W - 1, _y1, "delete"))
            if _exp_op > 0:
                _exp_x0 = _del_x0 + _BTN_W + 1  # 1-col gap after Delete
                btn_regions.append((_exp_x0, _y0, _exp_x0 + _BTN_W - 1, _y1, "export"))
        _row_cursor += _card_h + (_CARD_SPACING if i < end - 1 else 0)
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
        # The empty-state hint card (6 rows) + spacer (1) sit above the clickable
        # "+ New" card (the only item here, index 0).
        card_regions = [(title_offset + 8 + 7, title_offset + 8 + 7 + _new_card_h - 1, 0)]
        body_h += 3

    remaining = max(0, inner_h - header_h - body_h)

    # The delete confirmation comes from the DUCK now (see _duck_say below) rather
    # than a red overlay in the middle of the page, so it reads as him asking.
    popup_before = [Text("") for _ in range(remaining)]

    content = Group(
        *[Text("") for _ in range(title_offset)],
        title,
        Text(""),
        sub,
        Text(""),
        *body,
        *popup_before,
    )

    # Top padding of 1 (matching every other page and this builder's own
    # `inner_h = height - 4`) so the title lands one line below the top border —
    # level with where the menu's select→page slide leaves it, not a row higher.
    # Routed through build_page_panel (main #104) so the mode's bg tint applies.
    panel = build_page_panel(content, theme=theme, height=height, padding=(1, 2))
    panel._card_regions = card_regions  # (y_top, y_bot, item_index) per clickable card
    panel._btn_regions = btn_regions  # (x0, y0, x1, y1, label) for the selected card's buttons
    # The duck carries both delete messages: the confirmation (sticky — it must wait
    # for an answer rather than fading) and the "deleted" toast that follows.
    if delete_popup_name and delete_popup_t > 0:
        panel._duck_say = f'Delete "{delete_popup_name}"?  Enter to confirm'
        panel._duck_say_sticky = True
    elif message:
        panel._duck_say = message
    return panel
