"""Screen builders for the Weekly Review page (the Solo world's self-review).

Same shared-component structure as every other mode page (tui-standards):
pinned wordmark title + subtitle + content, wrapped in ``build_page_panel``
with the Solo theme. Two views, both rendered here (the page loop in
``_solo.py`` owns which is active and every key):

- "carried": last review's actions as toggle rows — mark each done, dropped or
  still pending before this week's review runs, the way a retro walks its
  carried items first.
- "detail": one saved review, rendered richly from the ``WeeklyReview``
  artifact (the same builder the saved-runs hub uses for a snapshot).

Builders are pure (no clocks, no logging — they run every frame).
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.agent.state import ReviewAction, WeeklyReview
from yeaboi.solo.engine import PHASES
from yeaboi.ui.mode_select.screens._screens_secondary import _analysis_toggle_row, _analysis_toggle_viewport
from yeaboi.ui.shared._components import (
    PAD,
    SOLO_THEME,
    build_action_buttons,
    build_meter,
    build_page_panel,
    build_reveal_subtitle,
    build_scrollbar,
    calc_viewport,
    solo_review_title,
)
from yeaboi.ui.shared._row_ctx import RowCtx, max_scroll_for, pack_viewport
from yeaboi.ui.shared._scroll import publish_geometry

SOLO_REVIEW_CARRIED_ACTIONS = ["Generate", "Back"]
SOLO_REVIEW_DETAIL_ACTIONS = ["Export", "Anonymize", "Back"]

#: The engine's phase ids paired with what "pending" reads as on the checklist.
SOLO_REVIEW_PHASES: tuple[tuple[str, str], ...] = tuple(
    zip(
        PHASES,
        (
            "Resolve the project scope",
            "Read this week's standups",
            "Read the sprint plan",
            "Gather delivered work",
            "Carry last week's actions",
            "Draft the review",
            "Save and export",
        ),
        strict=True,
    )
)

_STATUS_GLYPH = {"pending": "○", "done": "●", "dropped": "✕", "carried": "→"}
_STATUS_LABEL = {"pending": "still open", "done": "done", "dropped": "dropped", "carried": "carry forward"}
_PLAN_STYLE = {"on_track": "good", "at_risk": "warn", "behind": "bad"}

# Title (2) + the blanks and subtitle around it, like the reporting page.
_HEADER_H = 7


def _status_style(theme, status: str) -> str:
    if status == "done":
        return theme.good
    if status == "dropped":
        return theme.dim
    return theme.accent


def _action_row(ctx: RowCtx, action: ReviewAction) -> None:
    row = Text(PAD + "    ", justify="left", overflow="ellipsis", no_wrap=True)
    row.append(f"{_STATUS_GLYPH.get(action.status, '○')} ", style=_status_style(ctx.theme, action.status))
    row.append(action.text, style=ctx.theme.dim if action.status == "dropped" else ctx.theme.value)
    if action.origin == "carryover" and action.week_label:
        row.append(f"  (from {action.week_label})", style=ctx.theme.muted)
    ctx.add(row)


def _solo_review_detail_rows(review: WeeklyReview | None, theme, width: int) -> RowCtx:
    """The detail view's rows: verdict banner, meters, prose sections, actions."""
    ctx = RowCtx(theme, width)
    if review is None:
        ctx.line("No review yet — press Generate to review this week.", theme.muted)
        return ctx

    # The verdict first: it is the one line computed without the model.
    ctx.heading("Against the plan")
    verdict_style = getattr(theme, _PLAN_STYLE.get(review.plan_status, "muted"))
    ctx.wrapped(review.plan_line or "no verdict", verdict_style)
    if review.sprint_total_days:
        meter = Text(PAD + "    ", justify="left")
        meter.append("Sprint  ", style=theme.muted)
        meter.append_text(build_meter(review.sprint_day, review.sprint_total_days, theme=theme))
        meter.append(f"  day {review.sprint_day}/{review.sprint_total_days}", style=theme.desc)
        if review.sprint_name:
            meter.append(f"  ·  {review.sprint_name}", style=theme.muted)
        ctx.add(meter)
    if review.confidence_label:
        conf = Text(PAD + "    ", justify="left")
        conf.append("Confidence  ", style=theme.muted)
        conf.append_text(build_meter(review.confidence_end, 100, theme=theme, style=verdict_style))
        delta = review.confidence_end - review.confidence_start
        arrow = "↑" if delta > 0 else "↓" if delta < 0 else "→"
        conf.append(
            f"  {review.confidence_end}% {review.confidence_label}  {arrow} {delta:+d} this week", style=theme.desc
        )
        ctx.add(conf)

    if review.summary:
        ctx.heading("Summary")
        ctx.wrapped(review.summary, theme.value)
    for title, items in (("What went well", review.went_well), ("What to change", review.to_change)):
        if items:
            ctx.heading(title)
            for item in items:
                ctx.wrapped(f"• {item}", theme.value, hanging=2)
    if review.actions:
        ctx.heading("Actions for next week")
        for action in review.actions:
            _action_row(ctx, action)
    if review.carried_actions:
        ctx.heading("Carried from last week")
        for action in review.carried_actions:
            _action_row(ctx, action)
    if review.standup_lines:
        ctx.heading("This week's standups")
        for line in review.standup_lines:
            ctx.wrapped(line, theme.desc, hanging=2)
    if review.delivered_items:
        ctx.heading(f"Delivered ({len(review.delivered_items)})")
        for item in review.delivered_items:
            key = getattr(item, "key", "") or ""
            title = getattr(item, "title", "") or ""
            ctx.wrapped(f"• {key} {title}".strip(), theme.desc, hanging=2)
    if review.warnings:
        ctx.heading("Notices")
        for warning in review.warnings:
            ctx.wrapped(f"! {warning}", theme.warn, hanging=2)
    return ctx


def _carried_rows(carried: list[ReviewAction], cursor: int, theme) -> list[Text]:
    if not carried:
        return [
            Text(PAD + "  Nothing carried from last week.", style=theme.muted, justify="left"),
            Text(PAD + "  Press Generate to review this week.", style=theme.dim, justify="left"),
        ]
    rows = []
    for i, action in enumerate(carried):
        origin = f"from {action.week_label}" if action.week_label else "last review"
        rows.append(
            _analysis_toggle_row(
                action.text,
                origin,
                focused=i == cursor,
                selected=action.status == "done",
                enabled=action.status != "dropped",
                note=f"{_STATUS_LABEL.get(action.status, action.status)}  ·  {origin}",
                theme=theme,
            )
        )
    return rows


def _build_solo_review_screen(
    data: dict,
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
    """Build the Weekly Review screen using shared TUI components.

    ``data`` keys: view ("carried" | "detail"), review (WeeklyReview | None),
    carried (list[ReviewAction]), cursor (int — the carried row in focus),
    actions (list[str]), message (str), detail_title (str).

    # See docs: "TUI system" — shared component structure
    """
    theme = SOLO_THEME
    view = data.get("view", "detail")
    review: WeeklyReview | None = data.get("review")
    actions = data.get("actions") or (SOLO_REVIEW_CARRIED_ACTIONS if view == "carried" else SOLO_REVIEW_DETAIL_ACTIONS)
    message = data.get("message", "")

    title = solo_review_title(shimmer_tick, width=width)
    if view == "carried":
        subtitle_text = "Mark what became of last week's actions, then generate this week's review"
    else:
        subtitle_text = data.get("detail_title") or (
            f"Week {review.week_label} · {review.week_start} to {review.week_end}" if review else "Weekly Review"
        )
    if anon_note:
        subtitle_text = f"{subtitle_text}  ·  {anon_note}"
    subtitle = build_reveal_subtitle(subtitle_text, sub_reveal)

    header_h = _HEADER_H + (1 if message else 0)
    viewport_h = calc_viewport(height, header_h=header_h)

    if view == "carried":
        rows = _carried_rows(list(data.get("carried") or ()), int(data.get("cursor", 0)), theme)
        hint = Text(PAD + "  ↑/↓ move · Space marks done → dropped → open · Enter generates", style=theme.dim)
        cursor = int(data.get("cursor", 0))
        viewport = Group(hint, Text(""), _analysis_toggle_viewport(rows, cursor, height=viewport_h + 2))
    else:
        ctx = _solo_review_detail_rows(review, theme, width)
        max_scroll = max_scroll_for(ctx.item_heights, viewport_h)
        actual_scroll = min(scroll_offset, max_scroll)
        publish_geometry(scroll_meta, max_scroll, viewport_h)
        visible, visible_h = pack_viewport(ctx.lines, ctx.item_heights, actual_scroll, viewport_h)
        padded: list = list(visible)
        padded.extend(Text("") for _ in range(max(0, viewport_h - visible_h)))
        scrollbar = build_scrollbar(viewport_h, sum(ctx.item_heights), actual_scroll, max_scroll, always_show=True)
        if scrollbar is None:
            viewport = Group(*padded)
        else:
            shell = Table.grid(expand=True, padding=0)
            shell.add_column(ratio=1)
            shell.add_column(width=1)
            shell.add_row(Group(*padded), scrollbar)
            viewport = shell

    btn_top, btn_mid, btn_bot = build_action_buttons(actions, action_sel)
    parts: list = [Text(""), title, Text(""), subtitle, Text("")]
    if message:
        parts.append(Text(PAD + message, style=theme.accent_bright, justify="left"))
    parts.extend((viewport, Text(""), btn_top, btn_mid, btn_bot))
    return build_page_panel(Group(*parts), theme=theme, border_style=theme.accent, height=height)
