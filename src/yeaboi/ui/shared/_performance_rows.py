"""Renderable rows for a performance artifact — the 1:1 prep, summary, review, note.

Renders the artifact object directly, the way ``_reporting_detail_rows`` does.
What it replaces was one function that sniffed the prefix of a *plaintext* line
and picked one of four colours, which is why a nine-section prep read as one
undifferentiated column and why the whole email body of a 1:1 summary rendered
as a bold coral heading.

Two rules hold the layout together:

- **Every renderable is measured**, and prose is wrapped through ``RowCtx`` into
  one-row items, so scroll offsets and terminal rows agree. Only blocks with a
  hard height ceiling (the tile grid) go in as a taller renderable.
- **A long list is N one-row items, never a Rich table.** A 12-row table is one
  item taller than a 24-row terminal's viewport, so it could never be shown.

The four-glyph coverage vocabulary is learned once at the top of the artifact
and reused beside every empty section, so "nothing was found" and "nobody
looked" stop looking identical.
"""

from __future__ import annotations

import rich.box
from rich.console import Group
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

from yeaboi.ui.shared._components import PAD, Theme, build_meter
from yeaboi.ui.shared._row_ctx import RowCtx

# Coverage state → (glyph, theme attribute). The word always renders beside the
# glyph; colour alone is not a signal every reader can receive. An unrecognised
# state degrades to the muted dot with its own word intact, because the engine
# produces these and a new one must render rather than break the page.
_COVERAGE_MARKS: dict[str, tuple[str, str]] = {
    "covered": ("●", "good"),
    "partial": ("◐", "warn"),
    "failed": ("✕", "bad"),
    "not_configured": ("○", "muted"),
}
_UNKNOWN_MARK = ("·", "dim")

# How an empty section explains itself, per coverage state.
_EMPTY_PHRASE = {
    "covered": "none found in this period",
    "partial": "partly scanned",
    "failed": "could not be read",
    "not_configured": "not assessed",
}

_MAX_TABLE_ROWS = 12  # rows shown before a "… and N more" line
_MAX_REASON_ROWS = 2  # wrapped rows a coverage reason may occupy


def _mark(state: str) -> tuple[str, str]:
    return _COVERAGE_MARKS.get(state, _UNKNOWN_MARK)


def _clip(text: str, width: int) -> str:
    """Truncate with an ellipsis rather than mid-word, which reads as a typo."""
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def _fmt(value: float) -> str:
    """A metric value without a trailing ``.0`` on a whole number."""
    return f"{value:g}"


def _metric_text(metric) -> str:
    if metric.denominator:
        return f"{_fmt(metric.value)} of {_fmt(metric.denominator)}"
    return f"{_fmt(metric.value)}{metric.unit}" if metric.unit else _fmt(metric.value)


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------


def _headline(ctx: RowCtx, title: str, subtitle: str) -> None:
    theme = ctx.theme
    ctx.add(Text(PAD + "  " + title, style=f"bold {theme.accent_bright}", justify="left"))
    if subtitle:
        ctx.add(Text(PAD + "  " + subtitle, style=theme.muted, justify="left", no_wrap=True, overflow="ellipsis"))


def _coverage_strip(ctx: RowCtx, coverage) -> None:
    """The counted legend, the per-source chips, and a reason for each gap."""
    if not coverage:
        return
    theme = ctx.theme
    ctx.blank()

    counts: dict[str, int] = {}
    for _source, state, _detail in coverage:
        counts[state] = counts.get(state, 0) + 1
    legend = Text(PAD + "  ", justify="left", no_wrap=True, overflow="ellipsis")
    legend.append("EVIDENCE", style=f"bold {theme.accent}")
    # Known states first, in severity order, then any word the engine has learned
    # since — the legend is the only place a state's word is written, so an
    # unrecognised one must appear here rather than render as a bare grey dot.
    known = [s for s in _COVERAGE_MARKS if counts.get(s)]
    unknown = sorted(s for s in counts if s not in _COVERAGE_MARKS)
    for state in known + unknown:
        glyph, tone = _mark(state)
        legend.append("   ")
        legend.append(f"{glyph} ", style=getattr(theme, tone))
        legend.append(f"{counts[state]} {state.replace('_', ' ')}", style=theme.desc)
    ctx.add(legend)

    # Chips packed to the panel width; a row that would overflow starts a new one.
    budget = max(24, ctx.width - len(PAD) - 9)
    row = Text(PAD + "  ", justify="left")
    used = 0
    for source, state, _detail in coverage:
        glyph, tone = _mark(state)
        chip = f"{glyph} {source}"
        if used and used + len(chip) + 3 > budget:
            ctx.add(row)
            row = Text(PAD + "  ", justify="left")
            used = 0
        if used:
            row.append("   ")
            used += 3
        row.append(f"{glyph} ", style=getattr(theme, tone))
        row.append(source, style=theme.desc)
        used += len(chip)
    if used:
        ctx.add(row)

    for source, state, detail in coverage:
        if state == "covered" or not detail:
            continue
        glyph, tone = _mark(state)
        before = len(ctx.lines)
        ctx.wrapped(f"{glyph} {source} — {detail}", getattr(theme, tone), indent="    ")
        del ctx.lines[before + _MAX_REASON_ROWS :]
        del ctx.item_heights[before + _MAX_REASON_ROWS :]


def _metric_tiles(ctx: RowCtx, metrics) -> None:
    """Up to four headline numbers as a responsive tile grid.

    The one place a taller renderable is used, because its height is capped: at
    most one grid row of four, or a single 2x2 card on a narrow terminal.
    """
    theme = ctx.theme
    # Labels are not sliced here: the tile Text ellipsizes at the real column
    # width, which a fixed slice cannot know and gets wrong on a wide terminal.
    tiles = [(m.label.upper(), _metric_text(m), m.unit if not m.denominator else "") for m in metrics[:4]]
    if not tiles:
        return

    if ctx.width < 68:
        rows = []
        for start in (0, 2):
            pair = tiles[start : start + 2]
            if not pair:
                continue
            row = Text()
            for idx, (label, value, _u) in enumerate(pair):
                if idx:
                    row.append("    ")
                row.append(f"{_clip(label, 16)} ", style=theme.dim)
                row.append(value, style=f"bold {theme.accent_bright}")
            rows.append(row)
        ctx.add_renderable(Panel(Group(*rows), box=rich.box.ROUNDED, border_style=theme.sep, padding=(0, 1)))
        return

    columns = 4 if ctx.width >= 112 else 2
    grid = RichTable.grid(expand=True, padding=(0, 1))
    for _ in range(columns):
        grid.add_column(ratio=1)
    for start in range(0, len(tiles), columns):
        cells = [
            Panel(
                Group(
                    Text(label, style=theme.dim, no_wrap=True, overflow="ellipsis"),
                    Text(value, style=f"bold {theme.accent_bright}"),
                ),
                box=rich.box.ROUNDED,
                border_style=theme.sep,
                padding=(0, 1),
            )
            for label, value, _u in tiles[start : start + columns]
        ]
        cells.extend(Text("") for _ in range(columns - len(cells)))
        grid.add_row(*cells)
    ctx.add_table(grid)


def _metrics_block(ctx: RowCtx, metrics) -> None:
    """Tiles for the headline numbers, then every metric as a labelled meter row."""
    if not metrics:
        return
    theme = ctx.theme
    ctx.heading("📊 By the numbers")
    _metric_tiles(ctx, metrics)

    label_w = min(28, max(len(m.label) for m in metrics))
    for group in ("delivery", "practice", "ceremony", "volume", ""):
        rows = [m for m in metrics if m.group == group]
        if not rows:
            continue
        if group:
            ctx.add(Text(PAD + "    " + group.title(), style=theme.dim, justify="left"))
        for metric in rows:
            row = Text(PAD + "    ", justify="left", no_wrap=True, overflow="ellipsis")
            row.append(f"{_clip(metric.label, label_w):<{label_w}}  ", style=theme.desc)
            row.append(f"{_metric_text(metric):>10}", style=f"bold {theme.accent_bright}")
            if metric.denominator:
                row.append("  ")
                row.append_text(build_meter(int(metric.value), int(metric.denominator), width=12, theme=theme))
            elif metric.unit == "%":
                row.append("  ")
                row.append_text(build_meter(int(metric.value), 100, width=12, theme=theme))
            ctx.add(row)
            if metric.detail:
                ctx.wrapped(metric.detail, theme.dim, indent="      ")


def _evidence_block(ctx: RowCtx, groups) -> None:
    """One aligned table per evidence group — key, title, status."""
    if not groups:
        return
    theme = ctx.theme
    ctx.heading("🧾 Evidence")
    for group in groups:
        if not group.items:
            continue
        ctx.add(Text(PAD + "    " + (group.label or group.source), style=f"bold {theme.accent}", justify="left"))
        shown = group.items[:_MAX_TABLE_ROWS]
        key_w = min(14, max((len(i.key) for i in shown), default=1))
        title_w = max(16, ctx.width - len(PAD) - key_w - 30)
        for item in shown:
            row = Text(PAD + "      ", justify="left", no_wrap=True, overflow="ellipsis")
            row.append(f"{_clip(item.key, key_w):<{key_w}}  ", style=theme.id)
            row.append(f"{_clip(item.title, title_w):<{title_w}}  ", style=theme.value)
            if item.status:
                row.append(item.status, style=theme.good)
            ctx.add(row)
        extra = len(group.items) - len(shown)
        note = group.note or (f"… and {extra} more" if extra > 0 else "")
        if note:
            ctx.add(Text(PAD + "      " + note, style=theme.dim, justify="left"))


def _section(
    ctx: RowCtx, emoji: str, label: str, items, *, state: str = "", reason: str = "", marker: str = "•"
) -> None:
    """A titled bullet run. The heading always renders — silence is the bug.

    An empty section says which kind of empty it is, in the coverage vocabulary
    the strip at the top of the artifact already taught the reader.
    """
    theme = ctx.theme
    count = f" ({len(items)})" if items else ""
    ctx.heading(f"{emoji} {label}{count}")
    if items:
        for item in items:
            if not item:
                continue
            ctx.wrapped(f"{marker} {item}", theme.value, indent="    ", hanging=len(marker) + 1)
        return
    glyph, tone = _mark(state or "covered")
    phrase = _EMPTY_PHRASE.get(state or "covered", "not reported")
    line = f"{glyph} {phrase}" + (f" — {reason}" if reason else "")
    ctx.wrapped(line, getattr(theme, tone), indent="    ")


def _prose(ctx: RowCtx, emoji: str, label: str, text: str) -> None:
    if not text:
        return
    ctx.heading(f"{emoji} {label}")
    ctx.wrapped(text, ctx.theme.value, indent="    ", preserve_newlines=True)


def _notices(ctx: RowCtx, warnings) -> None:
    if not warnings:
        return
    ctx.heading("⚠ Notices")
    for warning in warnings:
        ctx.wrapped(f"- {warning}", ctx.theme.warn, indent="    ")


def _annotations(ctx: RowCtx, annotations) -> None:
    """Reader-added notes and fields. Stored-but-never-rendered is worse than never accepted."""
    if not annotations:
        return
    theme = ctx.theme
    ctx.heading("🗒 Added by the team")
    for note in annotations:
        if note.kind == "field" and note.label:
            row = Text(PAD + "    ", justify="left", no_wrap=True, overflow="ellipsis")
            row.append(f"{note.label}:  ", style=theme.muted)
            row.append(note.text, style=theme.value)
            ctx.add(row)
        else:
            ctx.wrapped(note.text, theme.value, indent="    ")
        if note.author:
            ctx.add(Text(PAD + "      — " + note.author, style=theme.dim, justify="left"))


# ---------------------------------------------------------------------------
# Per-artifact layouts
# ---------------------------------------------------------------------------


def _states(artifact) -> dict[str, tuple[str, str]]:
    return {s: (state, reason) for s, state, reason in getattr(artifact, "section_states", ()) or ()}


def _prep_rows(ctx: RowCtx, prep) -> None:
    states = _states(prep)

    def section(emoji: str, label: str, field: str, marker: str = "•") -> None:
        state, reason = states.get(field, ("", ""))
        _section(ctx, emoji, label, getattr(prep, field, ()), state=state, reason=reason, marker=marker)

    activity = getattr(prep, "activity", None)
    windows = (getattr(activity, "previous_sprint", ""), getattr(activity, "current_sprint", ""))
    sprints = " → ".join(s for s in windows if s)
    subtitle = f"Prepared {prep.date}" + (f"  ·  {sprints}" if sprints else "")
    _headline(ctx, f"1:1 Prep — {prep.engineer}", subtitle)
    _coverage_strip(ctx, getattr(prep, "evidence_coverage", ()))

    # Carried actions first: the reader opened this to run a meeting.
    section("↺", "Carried actions", "carried_action_items", marker="☐")
    _metrics_block(ctx, getattr(prep, "metrics", ()))
    _evidence_block(ctx, getattr(prep, "evidence_items", ()))
    _prose(ctx, "📝", "Sprint work", prep.activity_summary)
    section("💬", "Talking points", "talking_points")
    section("🫱", "Feedback to give", "feedback")
    section("🎯", "Goals to align on", "goals")
    section("🕳", "Gaps observed", "gaps")
    section("🪜", "Areas to improve", "improvements")
    _annotations(ctx, prep.annotations)
    _notices(ctx, prep.warnings)


_DELIVERY_WORDS = {
    "sent": ("EMAIL SENT", "good"),
    "failed": ("NOT DELIVERED", "bad"),
    "not_configured": ("EMAIL NOT CONFIGURED", "muted"),
}


def _completion_rows(ctx: RowCtx, record) -> None:
    theme = ctx.theme
    _headline(ctx, f"1:1 Completed — {record.engineer}", record.date)

    word, tone = _DELIVERY_WORDS.get(getattr(record, "delivery_state", ""), ("", ""))
    if word:
        chip = Text(PAD + "  ", justify="left")
        chip.append(f" {word} ", style=f"bold {getattr(theme, tone)}")
        ctx.add(chip)
    # The numbers below were gathered for the prep, not for this meeting. Saying
    # whose scan they are is the difference between evidence and an assertion.
    carried = str(getattr(record, "evidence_date", "") or "")
    if carried:
        ctx.line(f"carried from the prep of {carried}", theme.dim)
    _coverage_strip(ctx, getattr(record, "evidence_coverage", ()))

    states = _states(record)
    state, reason = states.get("action_items", ("", ""))
    _section(ctx, "☑", "Action items", record.action_items, state=state, reason=reason, marker="☐")
    state, reason = states.get("highlights", ("", ""))
    _section(ctx, "⭐", "Highlights", record.highlights, state=state, reason=reason)

    if record.email_subject:
        ctx.heading("✉ Summary email")
        row = Text(PAD + "    ", justify="left", no_wrap=True, overflow="ellipsis")
        row.append("Subject:  ", style=theme.muted)
        row.append(record.email_subject, style=theme.value)
        ctx.add(row)
        # Wrapped prose at value weight — this is the body of an email someone
        # sent, not a heading, which is what the old prefix-sniffing made of it.
        ctx.wrapped(record.email_summary, theme.value, indent="    ", preserve_newlines=True)
    else:
        _prose(ctx, "✉", "Summary email", record.email_summary)

    _metrics_block(ctx, getattr(record, "metrics", ()))
    _evidence_block(ctx, getattr(record, "evidence_items", ()))
    _annotations(ctx, record.annotations)
    _notices(ctx, record.warnings)
    # `transcript` is deliberately not rendered: it is the input rather than the
    # output, and the most sensitive text this mode holds.


def _review_rows(ctx: RowCtx, review) -> None:
    states = _states(review)

    def section(emoji: str, label: str, field: str) -> None:
        state, reason = states.get(field, ("", ""))
        _section(ctx, emoji, label, getattr(review, field, ()), state=state, reason=reason)

    period = f"{review.period_start or '?'} → {review.period_end or '?'}"
    if review.framework_used:
        period += f"  ·  framework {review.framework_used}"
    _headline(ctx, f"6-Month Review — {review.engineer}", period)
    _coverage_strip(ctx, getattr(review, "evidence_coverage", ()))

    _metrics_block(ctx, getattr(review, "metrics", ()))
    _prose(ctx, "🧭", "Overall", review.overall)
    section("💪", "Strengths", "strengths")
    section("🏆", "Achievements", "achievements")
    section("🪜", "Areas for improvement", "areas_for_improvement")
    section("🎯", "Goals for next period", "goals")
    _evidence_block(ctx, getattr(review, "evidence_items", ()))
    _annotations(ctx, review.annotations)
    _notices(ctx, review.warnings)


def _note_rows(ctx: RowCtx, note) -> None:
    theme = ctx.theme
    _headline(ctx, f"Note — {note.engineer}" if note.engineer else "Note", str(note.date or "")[:10])
    ctx.add(Text(PAD + "  " + "─" * 24, style=theme.sep, justify="left"))
    ctx.blank()
    if note.text.strip():
        ctx.wrapped(note.text, theme.value, indent="  ", preserve_newlines=True)
    else:
        ctx.line("(empty note)", style=theme.muted)


def _empty_rows(ctx: RowCtx) -> None:
    theme = ctx.theme
    _headline(ctx, "Nothing to show", "")
    ctx.blank()
    ctx.wrapped(
        "No performance artifact has been produced yet. Choose 1:1 Prep, 1:1 Complete or "
        "6mo Review for an engineer, or connect Jira / Azure DevOps in Settings so there is "
        "history to read.",
        theme.muted,
        indent="  ",
    )


_LAYOUTS = {
    "prep": _prep_rows,
    "completion": _completion_rows,
    "review": _review_rows,
    "note": _note_rows,
}


def performance_detail_rows(artifact, *, kind: str, theme: Theme, width: int) -> tuple[list, list[int]]:
    """Render one performance artifact as ``(renderables, item_heights)``.

    ``kind`` is ``prep`` | ``completion`` | ``review`` | ``note``. An unknown kind
    or a missing artifact renders the empty state rather than raising: this runs
    from a page loop, and a page that cannot draw is worse than one that says so.
    """
    ctx = RowCtx(theme, width)
    layout = _LAYOUTS.get(kind)
    if artifact is None or layout is None:
        _empty_rows(ctx)
    else:
        layout(ctx, artifact)
    return ctx.lines, ctx.item_heights
