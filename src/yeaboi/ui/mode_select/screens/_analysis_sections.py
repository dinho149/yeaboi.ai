"""Analysis results — per-section line builders for the overview + card views.

Extracted verbatim from the former single-function ``_build_team_analysis_screen``
in ``_screens_secondary.py`` so each section renders independently on its own
card. ``_TaCtx`` replaces the old ``_add``/``_heading``/``_kv`` closures; every
section function starts with an alias-unpack line so the moved bodies read
unchanged. ``_TA_CARDS`` maps the seven overview cards to their sections,
glossary terms and titles.
"""

from __future__ import annotations

import io

import rich.box
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

from yeaboi.analysis.ai_usage import _source_label, footprint_small_sample
from yeaboi.analysis.doc_quality import doc_small_sample
from yeaboi.tools.team_learning import ANALYSIS_GLOSSARY as _TA_GLOSSARY
from yeaboi.tools.team_learning import INSIGHT_CATEGORIES, compute_recommendations
from yeaboi.ui.shared._components import PAD

# Colour palette — moved verbatim from the original screen builder (predates
# the Theme system; kept identical so the rendered output does not change).
c_accent = "rgb(100,140,220)"
c_muted = "rgb(120,120,140)"
c_value = "bold white"
c_good = "rgb(80,220,120)"
c_warn = "rgb(220,180,60)"
c_bad = "rgb(220,80,80)"
c_dim = "dim"
c_example = "rgb(90,90,110)"
# AI-generated prose — same green pair as the LLM point descriptions.
c_ai_head = "rgb(100,180,100)"
c_ai_text = "rgb(180,220,180)"


def _measure_render_height(renderable, width: int) -> int:
    """Return the true number of terminal rows a renderable occupies at ``width``.

    The analysis/profile screens pack a scrollable viewport by hand, estimating
    each item's height so the packer knows when to stop and leave room for the
    action buttons below. Plain ``Text`` items are one row, but a Rich table
    with fixed-width columns wraps long cells onto several rows — a naive
    ``row_count`` under-counts it, which lets the packer overfill the viewport
    and push the buttons off the bottom of the fixed-height panel. Rendering to
    an off-screen console and counting the produced lines gives the real height.
    """
    _con = Console(width=max(1, width), file=io.StringIO(), legacy_windows=False)
    return len(_con.render_lines(renderable, pad=False))


class _TaCtx:
    """Line accumulator for the analysis screens (replaces the old closures).

    Collects renderables plus each item's rendered height so the viewport
    packer in ``_build_team_analysis_screen`` can slice a scroll window, and
    carries the shared inputs every section builder reads (examples dict,
    panel width, sprint names, headline stats).
    """

    def __init__(
        self,
        width: int,
        examples: dict | None,
        *,
        sprint_names: list[str] | None = None,
        stats: dict | None = None,
    ) -> None:
        self.width = width
        self.ex = examples or {}
        self.sprint_names = sprint_names
        self.stats = stats or {}
        # Set by the screen builder in 'both' mode: side-by-side headline rows
        # (label, jira_value, azdevops_value) rendered atop the overview.
        self.comparison: list[tuple[str, str, str]] | None = None
        # Code/Docs are global scans (not per delivery tracker); the screen builder
        # sets these from the top-level result so the two cards render regardless of
        # the active tracker's own (empty) profile signals.
        self.ai_sig = None
        self.doc_sig = None
        self.code_blob: dict = {}
        self.doc_blob: dict = {}
        self.analysis_features: tuple[str, ...] = ()
        # Composed card order (delivery cards for the active tracker + global cards),
        # set by the screen builder and shared with the results-loop navigation.
        self.visible_order: tuple[str, ...] = _TA_CARD_ORDER
        self.lines: list = []
        self.item_heights: list[int] = []
        self.rendered_lines = 0
        # Set by _ta_overview: rendered-row offset of the first section-card
        # row, so the screen builder can auto-scroll the selection into view.
        self.overview_first_card_row: int | None = None
        self.overview_card_rows: list[int] = []

    def add(self, item, rendered_h: int = 1) -> None:
        """Append an item and track its rendered height."""
        self.lines.append(item)
        self.item_heights.append(rendered_h)
        self.rendered_lines += rendered_h

    def add_table(self, table) -> None:
        """Append a table with its wrapped height measured (not naive row count)."""
        padded = Padding(table, (0, 0, 0, len(PAD) + 2))
        self.add(padded, rendered_h=_measure_render_height(padded, max(10, self.width - 7)))

    def add_renderable(self, renderable) -> None:
        """Append a rich renderable whose terminal-row height is not known upfront."""
        padded = Padding(renderable, (0, 1, 0, len(PAD)))
        self.add(padded, rendered_h=_measure_render_height(padded, max(10, self.width - 7)))

    def heading(self, text: str) -> None:
        self.add(Text(""))
        h = Text(PAD, justify="left")
        h.append(text, style=f"bold {c_accent}")
        self.add(h)
        self.add(Text(PAD + "─" * min(len(text), 40), style="rgb(50,60,80)"))

    def kv(self, label: str, value: str, val_style: str = c_value) -> None:
        t = Text(PAD + "  ", justify="left")
        t.append(f"{label:<24s}", style=c_muted)
        t.append(value, style=val_style)
        self.add(t)

    @staticmethod
    def pct_dots(pct: float, w: int = 15) -> str:
        """Dot-based percentage bar: ●●●●●○○○○○ 45%."""
        filled = round(pct / 100 * w)
        return "●" * filled + "○" * (w - filled) + f" {pct:.0f}%"

    @staticmethod
    def link(ek: str, url: str) -> str:
        """Style string that embeds a terminal hyperlink into the issue key."""
        if url:
            return f"bold underline {c_accent} link {url}"
        return c_accent

    def show_examples(self, ekey: str, limit: int = 2) -> None:
        items = self.ex.get(ekey, [])
        if not items:
            return
        for ex in items[:limit]:
            t = Text(PAD + "      ", justify="left")
            ek = ex.get("issue_key", "")
            url = ex.get("issue_url", "")
            summary = ex.get("summary", "")
            detail = ex.get("detail", "")
            if ek:
                t.append(ek, style=self.link(ek, url))
            if summary:
                t.append(f"  {summary}", style=c_example)
            if detail:
                t.append(f"  {detail}", style="rgb(70,70,90)")
            self.add(t)


def _ta_wrap(text: str, max_w: int) -> list[str]:
    """Greedy word-wrap (same style as the recommendations block)."""
    out: list[str] = []
    buf = ""
    for word in text.split():
        if len(buf) + len(word) + 1 > max_w:
            if buf.strip():
                out.append(buf.strip())
            buf = word + " "
        else:
            buf += word + " "
    if buf.strip():
        out.append(buf.strip())
    return out


def _ta_meter(value: float, total: float = 100, *, width: int = 18, style: str = c_accent) -> Text:
    """Compact terminal meter used by the AI feature dashboards."""
    total = max(1.0, total)
    filled = round(max(0.0, min(value, total)) / total * width)
    out = Text()
    out.append("▰" * filled, style=style)
    out.append("▱" * (width - filled), style="rgb(55,60,72)")
    return out


def _ta_callout(ctx: _TaCtx, title: str, body: str, *, colour: str = c_accent) -> None:
    """Render a compact, bordered explanatory callout."""
    lines = [Text(title, style=f"bold {colour}")]
    for wrapped in _ta_wrap(body, max(28, ctx.width - len(PAD) - 16)):
        lines.append(Text(wrapped, style=c_muted))
    ctx.add_renderable(
        Panel(
            Group(*lines),
            box=rich.box.ROUNDED,
            border_style="rgb(50,70,60)",
            padding=(0, 1),
        )
    )


_TA_LIST_LIMIT = 6  # scan-coverage repo rows shown before collapsing to "+ N more"


def _ta_more_row(ctx: _TaCtx, hidden: int, noun: str) -> None:
    """Dim '+ N more <noun>' disclosure row closing a capped list."""
    if hidden <= 0:
        return
    t = Text(PAD + "  ", justify="left")
    t.append(f"+ {hidden} more {noun}", style=c_dim)
    ctx.add(t)


def _ta_metric_tiles(ctx: _TaCtx, tiles: list[tuple[str, str, str, str]]) -> None:
    """Render responsive dashboard tiles: label, value, supporting hint, colour."""
    if not tiles:
        return
    columns = 3 if ctx.width >= 96 else (2 if ctx.width >= 68 else 1)
    grid = RichTable.grid(expand=True, padding=(0, 1))
    for _ in range(columns):
        grid.add_column(ratio=1)
    for start in range(0, len(tiles), columns):
        cells = []
        for label, value, hint, colour in tiles[start : start + columns]:
            content = Group(
                Text(label.upper(), style=c_dim),
                Text(value, style=f"bold {colour}"),
                Text(hint, style=c_muted, overflow="ellipsis", no_wrap=True),
            )
            cells.append(
                Panel(
                    content,
                    box=rich.box.ROUNDED,
                    border_style="rgb(48,55,68)",
                    padding=(0, 1),
                )
            )
        cells.extend(Text("") for _ in range(columns - len(cells)))
        grid.add_row(*cells)
    ctx.add_table(grid)


def _ta_ranked_bars(
    ctx: _TaCtx,
    title: str,
    rows,
    *,
    labeler=lambda value: str(value),
    limit: int = 8,
) -> None:
    """Render a ranked breakdown as proportional bars instead of raw key/value rows."""
    clean = [(str(label), int(count)) for label, count in rows if int(count) >= 0][:limit]
    if not clean:
        return
    ctx.heading(title)
    peak = max((count for _label, count in clean), default=1) or 1
    label_w = min(18, max(len(labeler(label)) for label, _count in clean))
    bar_w = 16 if ctx.width >= 78 else 10
    for label, count in clean:
        line = Text(PAD + "  ")
        line.append(f"{labeler(label)[:label_w]:<{label_w}} ", style=c_muted)
        line.append_text(_ta_meter(count, peak, width=bar_w, style=c_accent))
        line.append(f"  {count}", style=c_value)
        ctx.add(line)


_INSIGHT_DASHBOARD_GROUPS = (
    ("Focus now", ("start", "stop")),
    ("Keep working", ("keep",)),
    ("Experiments", ("try",)),
)
_INSIGHT_BADGES = {
    "start": ("START", c_accent),
    "stop": ("AVOID", c_bad),
    "keep": ("KEEP", c_good),
    "try": ("TRY", c_warn),
}


def _ta_coaching_dashboard(ctx: _TaCtx, insights: dict) -> None:
    """Render action-oriented coaching cards shared by all three AI features."""
    counts = {key: len(items) for key, items in insights.items() if key in _INSIGHT_BADGES and isinstance(items, list)}
    if counts:
        _ta_metric_tiles(
            ctx,
            [
                ("Focus now", str(counts.get("start", 0) + counts.get("stop", 0)), "priorities and risks", c_accent),
                ("Keep", str(counts.get("keep", 0)), "practices to protect", c_good),
                ("Try next", str(counts.get("try", 0)), "small experiments", c_warn),
            ],
        )

    for group_title, keys in _INSIGHT_DASHBOARD_GROUPS:
        group_items = [
            (key, item)
            for key in keys
            for item in (insights.get(key) or [])
            if isinstance(item, dict) and str(item.get("title", "")).strip()
        ]
        if not group_items:
            continue
        ctx.heading(group_title)
        for key, item in group_items:
            badge, colour = _INSIGHT_BADGES[key]
            head = Text()
            head.append(f" {badge} ", style=f"bold black on {colour}")
            head.append("  ")
            head.append(str(item["title"]).strip(), style=c_value)
            lines: list = [head]
            detail = str(item.get("detail", "") or "").strip()
            for wrapped in _ta_wrap(detail, max(26, ctx.width - len(PAD) - 18)):
                lines.append(Text(wrapped, style=c_muted))
            evidence = str(item.get("evidence", "") or "").strip()
            if evidence:
                ev = Text()
                ev.append("Evidence  ", style=f"bold {colour}")
                ev.append(evidence, style=c_dim)
                lines.append(ev)
            link = str(item.get("link", "") or "").strip()
            if link:
                linked = Text()
                linked.append("↳ ")
                linked.append(link, style=f"underline {c_accent} link {link}")
                lines.append(linked)
            ctx.add_renderable(
                Panel(
                    Group(*lines),
                    box=rich.box.ROUNDED,
                    border_style=colour,
                    padding=(0, 1),
                )
            )


def _ta_glossary_lines(ctx: _TaCtx, keys: tuple[str, ...]) -> None:
    """Dim plain-English definitions for the jargon used on this card."""
    if not keys:
        return
    ctx.add(Text(""))
    h = Text(PAD, justify="left")
    h.append("What the terms mean", style=f"bold {c_muted}")
    ctx.add(h)
    for k in keys:
        t = Text(PAD + "  ", justify="left")
        t.append(_TA_GLOSSARY[k], style=c_dim)
        ctx.add(t)


def _ta_narrative_block(ctx: _TaCtx, key: str) -> None:
    """AI "What this means" explanation at the top of a section card.

    Reads ``examples["narrative"]["sections"][key]`` (generated by one LLM call
    at analysis time). Silently omitted when absent — old saved profiles have
    no narrative and must still render.
    """
    narrative = ctx.ex.get("narrative", {})
    text = narrative.get("sections", {}).get(key, "") if isinstance(narrative, dict) else ""
    if not text:
        return
    ctx.add(Text(""))
    h = Text(PAD, justify="left")
    h.append("What this means", style=f"bold {c_ai_head}")
    ctx.add(h)
    for wrapped in _ta_wrap(str(text), max(40, ctx.width - len(PAD) - 10)):
        t = Text(PAD + "  ", justify="left")
        t.append(wrapped, style=c_ai_text)
        ctx.add(t)


def _ta_sprint_names(ctx: _TaCtx, profile) -> None:
    _add = ctx.add
    sprint_names = ctx.sprint_names
    # ── Sprint names (compressed) ─────────────────────────────────────
    if sprint_names:
        import os

        # Strip common prefix to compress "Dev Sprint 1, Dev Sprint 2" → "1, 2"
        names = [n.strip() for n in sprint_names if n.strip()]
        if len(names) >= 2:
            prefix = os.path.commonprefix(names).rstrip("0123456789")
            if len(prefix) > 3:
                short = [n[len(prefix) :].strip() for n in names]
                compressed = f"{prefix.strip()}: {', '.join(short)}"
            else:
                compressed = ", ".join(names)
        elif names:
            compressed = names[0]
        else:
            compressed = ""
        if compressed:
            sp_line = Text(PAD, justify="left")
            sp_line.append(compressed, style=c_dim)
            _add(sp_line)


def _ta_recurring(ctx: _TaCtx, profile) -> None:
    _add, _link, _ex = ctx.add, ctx.link, ctx.ex
    # ── Recurring work (filtered out) ──────────────────────────────
    rec_count = _ex.get("recurring_count", 0)
    del_count = _ex.get("delivery_count", 0)
    rec_items = _ex.get("recurring", [])
    if rec_count and isinstance(rec_count, int) and rec_count > 0:
        _add(Text(""))
        note = Text(PAD, justify="left")
        note.append(f"{rec_count} recurring tickets excluded ", style=c_muted)
        note.append(f"({del_count} delivery stories analysed)", style=c_dim)
        _add(note)
        if rec_items and isinstance(rec_items, list):
            for ex in rec_items[:3]:
                t = Text(PAD + "  ", justify="left")
                ek = ex.get("issue_key", "")
                summary = ex.get("summary", "")
                url = ex.get("issue_url", "")
                if ek:
                    t.append(ek, style=_link(ek, url))
                if summary:
                    t.append(f"  {summary}", style=c_example)
                _add(t)


def _ta_team_velocity(ctx: _TaCtx, profile) -> None:
    _heading, _kv, _pct_dots, _show_examples = ctx.heading, ctx.kv, ctx.pct_dots, ctx.show_examples
    _ex = ctx.ex
    # ── Team & Velocity ─────────────────────────────────────────────
    team_sz = _ex.get("team_size", 0)
    per_dev_vel = _ex.get("per_dev_velocity", 0)

    _heading("Team & Velocity")

    if team_sz and isinstance(team_sz, int) and team_sz > 0:
        _kv("Team size", f"{team_sz} contributors", c_value)

    # Compute velocity from current sprint details so it matches the table,
    # rather than the merged profile which accumulates historical data.
    _sp_details = _ex.get("sprint_details", [])
    if isinstance(_sp_details, list) and _sp_details:
        _sp_pts = [sd["points"] for sd in _sp_details if isinstance(sd, dict) and sd.get("points", 0) > 0]
        vel = round(sum(_sp_pts) / len(_sp_pts), 1) if _sp_pts else profile.velocity_avg
        import math as _m

        if len(_sp_pts) >= 2:
            _mean = sum(_sp_pts) / len(_sp_pts)
            std = round(_m.sqrt(sum((x - _mean) ** 2 for x in _sp_pts) / len(_sp_pts)), 1)
        else:
            std = profile.velocity_stddev
    else:
        vel = profile.velocity_avg
        std = profile.velocity_stddev

    # Committed vs delivered from scope timelines (preferred when available)
    _vel_scope = _ex.get("scope_changes", {})
    _has_scope_vel = False
    if isinstance(_vel_scope, dict) and _vel_scope.get("totals"):
        _vel_cv = _vel_scope["totals"].get("avg_committed_velocity", 0.0)
        _vel_dv = _vel_scope["totals"].get("avg_delivered_velocity", 0.0)
        if _vel_cv > 0:
            _has_scope_vel = True
            _kv("Team velocity", f"{_vel_dv:g} pts/sprint", c_value)
            _kv("Committed avg", f"{_vel_cv:g} pts/sprint", c_muted)
            _vel_dp = round(_vel_dv / _vel_cv * 100)
            _vel_ds = c_good if _vel_dp >= 85 else (c_warn if _vel_dp >= 70 else c_bad)
            _kv("Delivery accuracy", f"{_vel_dp}%", _vel_ds)

    if not _has_scope_vel:
        _kv("Team velocity", f"{vel} pts/sprint", c_value)

    # Per developer — use actual contributor avg when available
    _pdv_stats = _ex.get("contributor_stats", [])
    if isinstance(_pdv_stats, list) and _pdv_stats:
        _pdv_vals = [c.get("per_sprint", 0) for c in _pdv_stats if c.get("per_sprint", 0) > 0]
        if _pdv_vals:
            _pdv = round(sum(_pdv_vals) / len(_pdv_vals), 1)
            _kv("Per developer", f"{_pdv} pts/sprint", c_accent)
    elif per_dev_vel and isinstance(per_dev_vel, (int, float)) and per_dev_vel > 0:
        _kv("Per developer", f"{per_dev_vel} pts/sprint", c_accent)

    if vel > 0:
        var_pct = std / vel * 100
        var_style = c_good if var_pct < 20 else (c_warn if var_pct < 40 else c_bad)
        _kv("Variance", f"\u00b1{std} ({var_pct:.0f}%)", var_style)

    # Compute completion rate from the current sprint details (not the
    # merged profile which may be dragged down by stale historical data).
    sprint_details = _ex.get("sprint_details", [])
    if isinstance(sprint_details, list) and sprint_details:
        _sp_rates = [sd["rate"] for sd in sprint_details if isinstance(sd, dict) and sd.get("planned", 0) > 0]
        if _sp_rates:
            rate = round(sum(_sp_rates) / len(_sp_rates), 1)
            rate_style = c_good if rate >= 80 else (c_warn if rate >= 60 else c_bad)
            _kv("Completion", _pct_dots(rate), rate_style)
    elif profile.sprint_completion_rate > 0:
        rate = profile.sprint_completion_rate
        rate_style = c_good if rate >= 80 else (c_warn if rate >= 60 else c_bad)
        _kv("Completion", _pct_dots(rate), rate_style)

    if profile.spillover.carried_over_pct > 0:
        sp_pct = profile.spillover.carried_over_pct
        sp_style = c_good if sp_pct < 10 else (c_warn if sp_pct < 20 else c_bad)
        _kv("Spillover", f"{sp_pct}% carried over", sp_style)
        _show_examples("spillover")

    # Velocity trend
    vt = _ex.get("velocity_trend", {})
    if isinstance(vt, dict) and vt.get("trend") and vt["trend"] != "insufficient_data":
        trend_label = vt["trend"]
        slope = vt.get("slope", 0)
        first_v = vt.get("first_velocity", 0)
        last_v = vt.get("last_velocity", 0)
        if trend_label == "improving":
            trend_style = c_good
            trend_icon = "\u2197"  # ↗
        elif trend_label == "degrading":
            trend_style = c_bad
            trend_icon = "\u2198"  # ↘
        else:
            trend_style = c_muted
            trend_icon = "\u2192"  # →
        trend_str = f"{trend_icon} {trend_label.capitalize()} ({first_v}\u2192{last_v}, {slope:+.1f}/sprint)"
        _kv("Trend", trend_str, trend_style)


def _ta_sprint_breakdown(ctx: _TaCtx, profile) -> None:
    _add_table, _add, _heading, _link = ctx.add_table, ctx.add, ctx.heading, ctx.link
    _ex = ctx.ex
    # ── Sprint Breakdown ───────────────────────────────────────────
    from rich.table import Table as RichTable

    sprint_details = _ex.get("sprint_details", [])
    # Build scope lookup for merging into the breakdown table
    _scope_data = _ex.get("scope_changes", {})
    _scope_sprints = _scope_data.get("per_sprint", []) if isinstance(_scope_data, dict) else []
    _scope_by_name: dict[str, dict] = {s.get("name", ""): s for s in _scope_sprints if isinstance(s, dict)}
    _has_scope = any(s.get("committed_pts") for s in _scope_sprints)

    if sprint_details and isinstance(sprint_details, list) and len(sprint_details) > 0:
        _heading("Sprint Breakdown")

        sp_table = RichTable(
            show_header=True,
            header_style=c_muted,
            box=None,
            padding=(0, 1),
            pad_edge=False,
        )
        sp_table.add_column("Sprint", width=28)
        sp_table.add_column("Pts", justify="right", width=5)
        sp_table.add_column("Done", justify="right", width=6)
        sp_table.add_column("Rate", justify="right", width=6)
        sp_table.add_column("", width=2)
        if _has_scope:
            sp_table.add_column("Scope", justify="right", width=6)
            sp_table.add_column("\u0394", justify="right", width=6)
            sp_table.add_column("Churn", justify="right", width=5)

        for sd in sprint_details:
            if not isinstance(sd, dict):
                continue
            name = sd.get("name", "?")
            pts = sd.get("points", 0)
            planned = sd.get("planned", 0)
            completed = sd.get("completed", 0)
            rate = sd.get("rate", 0)
            done = sd.get("done", False)

            rate_style = c_good if rate >= 80 else (c_warn if rate >= 50 else c_bad)
            has_shadow = sd.get("has_shadow", False)
            if done:
                icon = Text("\u2713", style=c_good)
            elif has_shadow:
                icon = Text("\u25cb", style=c_warn)
            else:
                icon = Text("\u2717", style=c_bad)

            row_cells: list[Text | str] = [
                Text(name[:28], style=c_value),
                Text(str(pts), style=c_muted),
                Text(f"{completed}/{planned}", style=c_muted),
                Text(f"{rate}%", style=rate_style),
                icon,
            ]

            if _has_scope:
                sc = _scope_by_name.get(name, {})
                c_pts = sc.get("committed_pts", 0)
                if c_pts:
                    delta = sc.get("scope_change_total", 0)
                    delta_str = f"+{delta:g}" if delta > 0 else f"{delta:g}"
                    d_sty = c_good if delta == 0 else (c_warn if abs(delta) < 5 else c_bad)
                    churn = sc.get("scope_churn", 0)
                    ch_sty = c_good if churn < 0.1 else (c_warn if churn < 0.3 else c_bad)
                    row_cells.extend(
                        [
                            Text(f"{c_pts:g}\u2192{sc.get('final_pts', 0):g}", style=c_muted),
                            Text(delta_str, style=d_sty),
                            Text(f"{churn:.0%}", style=ch_sty),
                        ]
                    )
                else:
                    row_cells.extend([Text("\u2014", style=c_dim)] * 3)

            sp_table.add_row(*row_cells)

        _add_table(sp_table)

        # Analysis of incomplete sprints
        incomplete_sprints = [
            sd
            for sd in sprint_details
            if isinstance(sd, dict)
            and (not sd.get("done", False) or sd.get("has_shadow", False))
            and sd.get("incomplete")
        ]
        if incomplete_sprints:
            _add(Text(""))
            _add(
                Text(
                    PAD + "  Incomplete sprint analysis:",
                    style=c_muted,
                    justify="left",
                )
            )
            for sd in incomplete_sprints[:3]:
                name = sd.get("name", "?")
                planned = sd.get("planned", 0)
                completed = sd.get("completed", 0)
                gap = planned - completed
                inc = sd.get("incomplete", [])

                _add(Text(""))
                hdr = Text(PAD + "    ", justify="left")
                has_sh = sd.get("has_shadow", False)
                hdr.append(name, style=c_warn)
                if gap > 0:
                    hdr.append(
                        f"  {gap} stories not completed",
                        style=c_muted,
                    )
                if has_sh:
                    hdr.append(
                        "  + shadow spillover" if gap > 0 else "  shadow spillover",
                        style=c_warn,
                    )
                _add(hdr)

                for item in inc[:2]:
                    if not isinstance(item, dict):
                        continue
                    t = Text(PAD + "      ", justify="left")
                    ek = item.get("issue_key", "")
                    i_url = item.get("issue_url", "")
                    sm = item.get("summary", "")
                    pts_v = item.get("points", 0)
                    if ek:
                        t.append(ek, style=_link(ek, i_url))
                    if sm:
                        t.append(f"  {sm}", style=c_example)
                    if item.get("shadow"):
                        t.append("  (re-created)", style=c_warn)
                    elif pts_v:
                        t.append(f"  ({pts_v}pts)", style=c_dim)
                    _add(t)


def _ta_shadow_spillover(ctx: _TaCtx, profile) -> None:
    _add, _link, _ex = ctx.add, ctx.link, ctx.ex
    # ── Shadow Spillover ───────────────────────────────────────────
    shadow = _ex.get("shadow_spillover", [])
    if isinstance(shadow, list) and shadow:
        _add(Text(""))
        hdr = Text(PAD + "  ", justify="left")
        hdr.append(
            f"\u26a0 {len(shadow)} re-created stories detected",
            style=f"bold {c_warn}",
        )
        _add(hdr)
        _add(
            Text(
                PAD + "  Closed in one sprint but re-created in the next:",
                style=c_muted,
                justify="left",
            )
        )
        for sh in shadow[:5]:
            if not isinstance(sh, dict):
                continue
            t = Text(PAD + "    ", justify="left")
            ek = sh.get("issue_key", "")
            url = sh.get("issue_url", "")
            sh_title = sh.get("title", "")
            from_sp = sh.get("from_sprint", "")
            to_sp = sh.get("to_sprint", "")
            if ek:
                t.append(ek, style=_link(ek, url))
            if sh_title:
                t.append(f"  {sh_title}", style=c_example)
            _add(t)
            if from_sp or to_sp:
                m = Text(PAD + "      ", justify="left")
                m.append(f"{from_sp} \u2192 {to_sp}", style=c_dim)
                _add(m)


def _ta_scope(ctx: _TaCtx, profile) -> None:
    _add, _ex = ctx.add, ctx.ex
    # ── Scope Analysis (integrated into Sprint Breakdown) ───────────
    scope = _ex.get("scope_changes", {})
    if isinstance(scope, dict) and scope.get("totals"):
        totals = scope["totals"]
        t_added = totals.get("added_mid_sprint", 0)
        t_re_est = totals.get("re_estimated", 0)
        t_total = totals.get("total_stories", 0)
        avg_committed = totals.get("avg_committed_velocity", 0.0)
        avg_delivered = totals.get("avg_delivered_velocity", 0.0)

        has_data = t_added > 0 or t_re_est > 0 or avg_committed > 0
        if has_data:
            _add(Text(""))
            # Committed → Delivered summary
            if avg_committed > 0:
                delivery_pct = round(avg_delivered / avg_committed * 100)
                d_sty = c_good if delivery_pct >= 85 else (c_warn if delivery_pct >= 70 else c_bad)
                summary = Text(PAD + "  ", justify="left")
                summary.append("Committed ", style=c_muted)
                summary.append(f"{avg_committed:g}", style="bold " + c_value)
                summary.append(" \u2192 Delivered ", style=c_muted)
                summary.append(f"{avg_delivered:g}", style="bold " + c_value)
                summary.append("  pts/sprint avg  ", style=c_muted)
                summary.append(f"({delivery_pct}% accuracy)", style=d_sty)
                _add(summary)

            # Added/re-estimated stats
            if t_total > 0 and (t_added > 0 or t_re_est > 0):
                add_pct = round(t_added / t_total * 100)
                re_pct = round(t_re_est / t_total * 100)
                add_sty = c_good if add_pct < 10 else (c_warn if add_pct < 25 else c_bad)
                re_sty = c_good if re_pct < 10 else (c_warn if re_pct < 25 else c_bad)
                stats = Text(PAD + "  ", justify="left")
                stats.append(f"{t_added} added mid-sprint ", style=add_sty)
                stats.append(f"({add_pct}%)", style=c_dim)
                stats.append("  \u00b7  ", style=c_dim)
                stats.append(f"{t_re_est} re-estimated ", style=re_sty)
                stats.append(f"({re_pct}%)", style=c_dim)
                _add(stats)

            # Per-sprint scope narratives (most recent sprints with changes)
            timelines = scope.get("timelines", [])
            sprints_with_events = [tl for tl in timelines if hasattr(tl, "change_events") and tl.change_events]
            for tl in sprints_with_events[-4:]:  # most recent 4
                _add(Text(""))
                delta = tl.scope_change_total
                pct = round(delta / tl.committed_pts * 100) if tl.committed_pts else 0
                delta_str = f"+{delta:g}" if delta > 0 else f"{delta:g}"
                d_sty_n = c_good if delta == 0 else (c_warn if abs(delta) < 5 else c_bad)
                hdr = Text(PAD + "  ", justify="left")
                hdr.append(tl.sprint_name, style="bold " + c_value)
                hdr.append(f"  {delta_str} scope ", style=d_sty_n)
                hdr.append(f"({pct:+d}%)", style=c_dim)
                _add(hdr)

                # Day 1 committed
                n_stories = len(tl.daily_snapshots[0].stories_in_sprint) if tl.daily_snapshots else 0
                day1 = Text(PAD + "    ", justify="left")
                day1.append(f"committed {tl.committed_pts:g} pts", style=c_muted)
                if n_stories:
                    day1.append(f" ({n_stories} stories)", style=c_dim)
                _add(day1)

                # Events (max 5)
                for ev in tl.change_events[:5]:
                    ct_short = ev.change_type.replace("re_estimated_", "re-est ")
                    ct_short = ct_short.replace("_", " ")
                    delta_s = f"+{ev.delta_pts:g}" if ev.delta_pts > 0 else f"{ev.delta_pts:g}"
                    ev_sty = c_good if ev.delta_pts < 0 else (c_warn if abs(ev.delta_pts) <= 3 else c_bad)
                    ct_sty = "#22c55e" if "removed" in ct_short else ("#ef4444" if "added" in ct_short else c_warn)
                    row = Text(PAD + "    ", justify="left")
                    row.append(f"{delta_s} pts", style=ev_sty)
                    row.append(f"  {ev.issue_key}", style=c_accent)
                    row.append(f"  {ct_short}", style=ct_sty)
                    if ev.summary:
                        row.append(f"  {ev.summary[:45]}", style=c_dim)
                    _add(row)
                if len(tl.change_events) > 5:
                    more = Text(PAD + "    ", justify="left")
                    more.append(f"... +{len(tl.change_events) - 5} more", style=c_dim)
                    _add(more)

                # Final/delivered
                n_final = len(tl.daily_snapshots[-1].stories_in_sprint) if tl.daily_snapshots else 0
                foot = Text(PAD + "    ", justify="left")
                foot.append(f"final {tl.final_pts:g} pts", style=c_muted)
                if n_final:
                    foot.append(f" ({n_final} stories)", style=c_dim)
                foot.append(f" \u00b7 delivered {tl.delivered_pts:g} pts", style=c_muted)
                _add(foot)

            # Carry-over chains
            chains = scope.get("carry_over_chains", [])
            if chains:
                _add(Text(""))
                h = Text(PAD + "  ", justify="left")
                h.append(
                    f"\u26a0 {len(chains)} stories bounced across 3+ sprints",
                    style=f"bold {c_warn}",
                )
                _add(h)
                for ch in chains[:5]:
                    if not isinstance(ch, dict):
                        continue
                    t = Text(PAD + "    ", justify="left")
                    ek = ch.get("issue_key", "")
                    sc = ch.get("sprint_count", 0)
                    sprints = ch.get("sprints", [])
                    t.append(ek, style=c_accent)
                    t.append(f"  {sc} sprints: ", style=c_muted)
                    t.append(" \u2192 ".join(str(s) for s in sprints), style=c_dim)
                    _add(t)


def _ta_team_members(ctx: _TaCtx, profile) -> None:
    _add_table, _add, _heading, _ex = ctx.add_table, ctx.add, ctx.heading, ctx.ex
    # ── Team Members ───────────────────────────────────────────────
    _contrib = _ex.get("contributor_stats", [])
    if isinstance(_contrib, list) and _contrib:
        _heading("Team Members")

        # Interrupted work summary (team-level, since assignment is unreliable)
        total_rec = sum(c.get("recurring_pts", 0) for c in _contrib)
        total_del = sum(c.get("delivery_pts", 0) for c in _contrib)
        if total_rec > 0:
            rec_pct = round(total_rec / (total_rec + total_del) * 100) if (total_rec + total_del) else 0
            rec_row = Text(PAD + "  ", justify="left")
            rec_row.append("Interrupted work: ", style=c_muted)
            rec_row.append(f"{total_rec:g} pts", style=c_warn if rec_pct > 30 else c_value)
            rec_row.append(f" ({rec_pct}% of total effort)", style=c_dim)
            _add(rec_row)

        from rich.table import Table as _MemberTable

        mt = _MemberTable(
            show_header=True,
            header_style=c_muted,
            box=None,
            padding=(0, 1),
            pad_edge=False,
        )
        # Get total sprints for participation display
        _total_sprints = len(_ex.get("sprint_details", [])) or profile.sample_sprints or 1

        mt.add_column("Name", width=20)
        mt.add_column("Delivered", justify="right", width=8)
        mt.add_column("Stories", justify="right", width=7)
        mt.add_column("Spill%", justify="right", width=6)
        mt.add_column("Cycle", justify="right", width=6)
        mt.add_column("Sprints", justify="right", width=7)
        mt.add_column("Focus", width=14)
        mt.add_column("Pts/sprint", justify="right", width=9)

        for cs in _contrib[:10]:
            ps = cs.get("per_sprint", 0)
            ps_sty = c_good if ps >= 3 else (c_warn if ps >= 1.5 else c_dim)
            spill = cs.get("spill_rate", 0)
            sp_sty = c_good if spill < 10 else (c_warn if spill < 25 else c_bad)
            ct_val = cs.get("avg_cycle_time", 0)
            ct_str = f"{ct_val:.0f}d" if ct_val > 0 else "\u2014"
            disc = cs.get("top_discipline", "fullstack")
            wt = cs.get("top_work_type", "")
            focus = disc
            if wt:
                focus = f"{disc}/{wt.split('/')[0]}"
            sa = cs.get("sprints_active", 0)
            sprints_str = f"{sa}/{_total_sprints}"
            mt.add_row(
                Text(cs.get("name", "")[:20], style=c_value),
                Text(str(cs.get("delivery_pts", 0)), style=c_accent),
                Text(str(cs.get("stories_completed", 0)), style=c_muted),
                Text(f"{spill}%" if spill > 0 else "\u2014", style=sp_sty),
                Text(ct_str, style=c_muted),
                Text(sprints_str, style=c_muted),
                Text(focus[:14], style=c_dim),
                Text(str(ps), style=ps_sty),
            )
        _add_table(mt)

        # Insights
        if len(_contrib) >= 3:
            _add(Text(""))
            # Who carries the most load?
            top = _contrib[0]
            if total_del > 0:
                top_pct = round(top["delivery_pts"] / total_del * 100)
                if top_pct >= 40:
                    ins = Text(PAD + "  ", justify="left")
                    ins.append(f"\u26a0 {top['name']}", style=f"bold {c_warn}")
                    ins.append(f" carries {top_pct}% of delivery work", style=c_warn)
                    _add(ins)

            # Who spills most?
            high_spill = [c for c in _contrib if c.get("spill_rate", 0) >= 30 and c.get("stories_total", 0) >= 3]
            if high_spill:
                for hs in high_spill[:2]:
                    ins = Text(PAD + "  ", justify="left")
                    ins.append(f"\u26a0 {hs['name']}", style=f"bold {c_bad}")
                    ins.append(
                        f" spills {hs['spill_rate']}% of stories ({hs['stories_spilled']}/{hs['stories_total']})",
                        style=c_bad,
                    )
                    _add(ins)


def _ta_spillover_root_causes(ctx: _TaCtx, profile) -> None:
    _add, _heading, _ex = ctx.add, ctx.heading, ctx.ex
    # ── Spillover Root Causes ─────────────────────────────────────
    spill_corr = _ex.get("spillover_correlation", {})
    if isinstance(spill_corr, dict) and spill_corr:
        by_size = spill_corr.get("by_size", {})
        by_disc = spill_corr.get("by_discipline", {})
        by_tasks = spill_corr.get("by_task_count", {})
        # Only show if there's meaningful spillover in any dimension
        has_spill = any(v > 0 for d in (by_size, by_disc, by_tasks) if isinstance(d, dict) for v in d.values())
        if has_spill:
            _heading("Spillover Root Causes")
            if by_size:
                row = Text(PAD + "  ", justify="left")
                row.append("By size:       ", style=c_muted)
                parts = []
                for sz, pct in sorted(by_size.items(), key=lambda x: int(x[0])):
                    sty = c_good if pct < 10 else (c_warn if pct < 25 else c_bad)
                    parts.append((f"{sz}pt={pct:.0f}%", sty))
                for i, (txt, sty) in enumerate(parts):
                    if i > 0:
                        row.append("  ", style=c_dim)
                    row.append(txt, style=sty)
                _add(row)
            if by_disc:
                row = Text(PAD + "  ", justify="left")
                row.append("By discipline: ", style=c_muted)
                parts = []
                for disc, pct in sorted(by_disc.items()):
                    sty = c_good if pct < 10 else (c_warn if pct < 25 else c_bad)
                    parts.append((f"{disc}={pct:.0f}%", sty))
                for i, (txt, sty) in enumerate(parts):
                    if i > 0:
                        row.append("  ", style=c_dim)
                    row.append(txt, style=sty)
                _add(row)
            if by_tasks:
                row = Text(PAD + "  ", justify="left")
                row.append("By tasks:      ", style=c_muted)
                parts = []
                for bucket, pct in by_tasks.items():
                    sty = c_good if pct < 10 else (c_warn if pct < 25 else c_bad)
                    parts.append((f"{bucket}={pct:.0f}%", sty))
                for i, (txt, sty) in enumerate(parts):
                    if i > 0:
                        row.append("  ", style=c_dim)
                    row.append(txt, style=sty)
                _add(row)


def _ta_discipline_calibration(ctx: _TaCtx, profile) -> None:
    _add, _heading, _ex = ctx.add, ctx.heading, ctx.ex
    # ── Discipline-Specific Calibration ───────────────────────────
    disc_cal = _ex.get("discipline_calibration", {})
    if isinstance(disc_cal, dict) and len(disc_cal) > 1:
        _heading("Calibration by Discipline")
        _add(
            Text(
                PAD + "  Cycle time + variance per discipline and point value",
                style="rgb(80,80,100)",
                justify="left",
            )
        )
        for disc, entries in sorted(disc_cal.items()):
            if not isinstance(entries, list) or not entries:
                continue
            _add(Text(""))
            h = Text(PAD + "  ", justify="left")
            h.append(disc, style=f"bold {c_accent}")
            _add(h)
            for e in entries:
                if not isinstance(e, dict):
                    continue
                pts = e.get("points", 0)
                avg_d = e.get("avg_cycle_days", 0)
                var = e.get("variance", 0)
                samples = e.get("samples", 0)
                sp = e.get("spill_pct", 0)
                pts_label = f"{pts}pt" if pts == 1 else f"{pts}pts"
                row = Text(PAD + "    ", justify="left")
                row.append(f"{pts_label:<6s}", style=c_muted)
                day_sty = c_value if avg_d <= 15 else (c_warn if avg_d <= 40 else c_bad)
                row.append(f"{avg_d:.0f}d", style=day_sty)
                if var > 0:
                    var_sty = c_good if var < 3 else (c_warn if var < 8 else c_bad)
                    row.append(f" \u00b1{var:.0f}d", style=var_sty)
                row.append(f"  {samples} samples", style=c_dim)
                if sp > 10:
                    row.append(f"  {sp:.0f}% spill", style=c_warn)
                _add(row)


def _ta_point_meanings(ctx: _TaCtx, profile) -> None:
    _add, _heading, _link, _ex = ctx.add, ctx.heading, ctx.link, ctx.ex
    # ── What Each Point Value Means ─────────────────────────────────

    cals_with_data = [c for c in profile.point_calibrations if c.sample_count > 0]
    if cals_with_data:
        _heading("What Each Point Value Means")
        _add(
            Text(
                PAD + "  Based on this team's historical data",
                style="rgb(80,80,100)",
                justify="left",
            )
        )

        for cal in cals_with_data:
            days = cal.avg_cycle_time_days
            pts_label = f"{cal.point_value} pt" if cal.point_value == 1 else f"{cal.point_value} pts"
            day_style = c_value if days <= 15 else (c_warn if days <= 40 else c_bad)

            _add(Text(""))
            # Point value header with key stats
            h = Text(PAD + "  ", justify="left")
            h.append(pts_label, style=f"bold {c_accent}")
            h.append(f"   {days:.0f}d avg cycle", style=day_style)
            h.append(f"  \u00b7  {cal.sample_count} stories", style=c_muted)
            if cal.typical_task_count > 0:
                h.append(f"  \u00b7  ~{cal.typical_task_count:.0f} tasks", style=c_muted)
            # Confidence label
            conf_levels = _ex.get("confidence_levels", {})
            conf = conf_levels.get(cal.point_value, "") if isinstance(conf_levels, dict) else ""
            if conf == "high":
                h.append("  \u00b7  HIGH confidence", style=c_good)
            elif conf == "low":
                h.append("  \u00b7  low confidence", style=c_warn)
            _add(h)

            # LLM-generated description — what this point value means in practice
            _pt_descs = _ex.get("point_descriptions", {})
            _pt_desc = _pt_descs.get(str(cal.point_value), "") if isinstance(_pt_descs, dict) else ""
            if _pt_desc:
                d = Text(PAD + "    ", justify="left")
                d.append("\u2192 ", style="rgb(100,180,100)")
                d.append(str(_pt_desc), style="rgb(180,220,180)")
                _add(d)

            # Common patterns — what kind of work this point value represents
            if cal.common_patterns:
                p = Text(PAD + "    ", justify="left")
                p.append("Typical work: ", style=c_muted)
                p.append(", ".join(cal.common_patterns), style=c_value)
                _add(p)

            # Representative examples
            ex_items = _ex.get(f"calibration_{cal.point_value}pt", [])
            if ex_items:
                for ex in ex_items[:3]:
                    t = Text(PAD + "    ", justify="left")
                    ek = ex.get("issue_key", "")
                    url = ex.get("issue_url", "")
                    summary = ex.get("summary", "")
                    detail = ex.get("detail", "")
                    if ek:
                        t.append(ek, style=_link(ek, url))
                    if summary:
                        t.append(f"  {summary}", style=c_example)
                    if detail:
                        t.append(f"  {detail}", style="rgb(70,70,90)")
                    _add(t)

        _add(Text(""))


def _ta_story_shape(ctx: _TaCtx, profile) -> None:
    _add, _heading = ctx.add, ctx.heading
    # ── Story Shape by Discipline ─────────────────────────────────────
    shapes = profile.story_shapes
    real_shapes = [s for s in shapes if s.discipline != "fullstack" or len(shapes) > 1]
    real_shapes = [s for s in real_shapes if s.sample_count > 0]
    if real_shapes:
        _heading("Story Shape by Discipline")
        for shape in real_shapes:
            row = Text(PAD + "  ", justify="left")
            row.append(f"{shape.discipline:<14s}", style=c_value)
            parts = [f"avg {shape.avg_points} pts"]
            if shape.avg_ac_count > 0:
                parts.append(f"{shape.avg_ac_count} ACs")
            if shape.avg_task_count > 0:
                parts.append(f"{shape.avg_task_count} tasks")
            row.append(" \u00b7 ".join(parts), style=c_muted)
            if shape.sample_count < 5:
                row.append(f"  ({shape.sample_count} samples)", style=c_warn)
            _add(row)


def _ta_task_decomposition(ctx: _TaCtx, profile) -> None:
    _add, _heading, _kv, _pct_dots = ctx.add, ctx.heading, ctx.kv, ctx.pct_dots
    _ex = ctx.ex
    # ── Task Decomposition ─────────────────────────────────────────
    td = _ex.get("task_decomposition", {})
    if isinstance(td, dict) and td.get("total_tasks", 0) > 0:
        _heading("Task Decomposition")

        _kv("Stories with tasks", f"{td['stories_with_tasks']} / {td['total_stories']}")
        _kv("Total tasks", str(td["total_tasks"]))
        _kv("Avg tasks/story", str(td["avg_tasks_per_story"]))
        _kv(
            "Task completion",
            _pct_dots(td["task_completion_rate"]),
            c_good if td["task_completion_rate"] >= 80 else (c_warn if td["task_completion_rate"] >= 50 else c_bad),
        )

        # Type distribution as a table
        type_dist = td.get("type_distribution", {})
        if type_dist:
            _add(Text(""))
            for cat, pct in type_dist.items():
                row = Text(PAD + "    ", justify="left")
                row.append(f"{cat:<16s}", style=c_value)
                row.append(_pct_dots(pct, w=10), style=c_muted)
                _add(row)

        # Bottlenecks
        bottlenecks = td.get("bottlenecks", [])
        if bottlenecks:
            _add(Text(""))
            for cat, rate, count in bottlenecks:
                t = Text(PAD + "  ", justify="left")
                t.append(f"\u26a0 {cat}", style=f"bold {c_warn}")
                t.append(
                    f"  only {rate}% completion ({count} tasks)",
                    style=c_muted,
                )
                _add(t)

        # Common recurring tasks
        common_tasks = td.get("common_tasks", [])
        if common_tasks:
            _add(Text(""))
            _add(
                Text(
                    PAD + "  Common task patterns:",
                    style=c_muted,
                    justify="left",
                )
            )
            for title, cnt in common_tasks[:4]:
                t = Text(PAD + "    ", justify="left")
                t.append(f"{title[:45]}", style=c_example)
                t.append(f"  \u00d7{cnt}", style=c_dim)
                _add(t)

        # Task assignee data is shown in the dedicated Team Members section


def _ta_dod_inferred(ctx: _TaCtx, profile) -> None:
    _add_table, _add, _heading, _kv = ctx.add_table, ctx.add, ctx.heading, ctx.kv
    _pct_dots, _link, _ex = ctx.pct_dots, ctx.link, ctx.ex
    # ── Definition of Done ────────────────────────────────────────────
    dod = profile.dod_signal
    dod_items: list[tuple[str, float, str]] = []
    if dod.stories_with_testing_mention_pct > 0:
        dod_items.append(("Testing", dod.stories_with_testing_mention_pct, "dod_testing"))
    if dod.stories_with_pr_link_pct > 0:
        dod_items.append(("PR linked", dod.stories_with_pr_link_pct, "dod_pr"))
    if dod.stories_with_review_mention_pct > 0:
        dod_items.append(("Code review", dod.stories_with_review_mention_pct, "dod_review"))
    if dod.stories_with_deploy_mention_pct > 0:
        dod_items.append(("Deploy", dod.stories_with_deploy_mention_pct, "dod_deploy"))

    if dod_items:
        _heading("Definition of Done (inferred)")

        dod_table = RichTable(
            show_header=True,
            header_style=c_muted,
            box=None,
            padding=(0, 2),
            pad_edge=False,
        )
        dod_table.add_column("Practice", width=14)
        dod_table.add_column("Coverage", width=30)
        dod_table.add_column("Example", width=30)

        for label, pct, ekey in dod_items:
            bar_style = c_good if pct >= 50 else (c_warn if pct >= 20 else c_muted)
            ex_items = _ex.get(ekey, [])
            ex_text = Text("", style=c_example)
            if ex_items:
                ex0 = ex_items[0]
                ek = ex0.get("issue_key", "")
                eu = ex0.get("issue_url", "")
                sm = ex0.get("summary", "")[:30]
                if ek:
                    ex_text.append(f"{ek} ", style=_link(ek, eu))
                ex_text.append(sm, style=c_example)

            dod_table.add_row(
                Text(label, style=c_value),
                Text(_pct_dots(pct), style=bar_style),
                ex_text,
            )

        _add_table(dod_table)

        if dod.common_checklist_items:
            _add(Text(""))
            items_joined = ", ".join(dod.common_checklist_items[:4])
            _kv("Common signals", items_joined, c_muted)


def _ta_board_workflow(ctx: _TaCtx, profile) -> None:
    _add, _heading, _kv, _ex = ctx.add, ctx.heading, ctx.kv, ctx.ex
    src = profile.source
    # ── Board Workflow ─────────────────────────────────────────────────
    _wf = _ex.get("workflow_style", {})
    if isinstance(_wf, dict) and _wf.get("workflow"):
        if src == "azdevops":
            _heading("Work Item State Flow")
        else:
            _heading("Board Workflow")

        # Workflow sequence
        wf_seq = _wf.get("workflow", [])
        if wf_seq:
            row = Text(PAD + "  ", justify="left")
            row.append(" \u2192 ".join(wf_seq), style=c_value)
            _add(row)

        if src == "azdevops":
            _add(
                Text(
                    PAD + "    Taskboard columns (Documentation, PR, etc.) are board-level config not tracked here.",
                    style=c_dim,
                    justify="left",
                )
            )

        # Style
        wf_style = _wf.get("style", "minimal")
        style_label = {
            "columns-as-dod": "Columns as DoD steps",
            "minimal": "Minimal workflow",
        }.get(wf_style, wf_style)
        _kv("Workflow style", style_label)

        # DoD column pass-through rates
        dod_cols = _wf.get("dod_columns", {})
        if dod_cols:
            for col, rate in dod_cols.items():
                r_sty = c_good if rate >= 70 else (c_warn if rate >= 30 else c_bad)
                _kv(f"  {col}", f"{rate}% pass-through", r_sty)

        # Full workflow compliance
        fw_pct = _wf.get("full_workflow_pct", 0)
        if dod_cols:
            fw_sty = c_good if fw_pct >= 60 else (c_warn if fw_pct >= 30 else c_bad)
            _kv("Full workflow compliance", f"{fw_pct}%", fw_sty)

        # Skip patterns
        skips = _wf.get("skip_patterns", [])
        if skips:
            _add(Text(""))
            for sp in skips[:3]:
                row = Text(PAD + "  ", justify="left")
                row.append(f"\u26a0 {sp['skip_pct']}% skip ", style=c_warn)
                row.append(sp.get("column", "?"), style=c_value)
                _add(row)


def _ta_proposed_dod(ctx: _TaCtx, profile) -> None:
    _add_table, _add, _heading, _ex = ctx.add_table, ctx.add, ctx.heading, ctx.ex
    # ── Proposed Definition of Done ────────────────────────────────────
    proposed_dod = _ex.get("proposed_dod", {})
    if isinstance(proposed_dod, dict) and proposed_dod.get("items"):
        _heading("Proposed Definition of Done")
        dod_summary = proposed_dod.get("summary", "")
        dod_health = proposed_dod.get("health", "weak")
        if dod_summary:
            h_style = c_good if dod_health == "strong" else (c_warn if dod_health == "moderate" else c_bad)
            _add(Text(PAD + "  " + dod_summary, style=h_style, justify="left"))

        pdod_table = RichTable(
            show_header=True,
            header_style=c_muted,
            box=None,
            padding=(0, 1),
            pad_edge=False,
        )
        pdod_table.add_column("Practice", width=20)
        pdod_table.add_column("", width=12)
        pdod_table.add_column("Evidence", width=24)
        pdod_table.add_column("Action", ratio=1, no_wrap=True)

        _st_style = {"established": c_good, "emerging": c_warn, "missing": c_bad}
        _st_icon = {"established": "\u2713", "emerging": "\u25cb", "missing": "\u2717"}
        for item in proposed_dod["items"]:
            st = item.get("status", "missing")
            sig = item.get("signals", "no evidence")
            pdod_table.add_row(
                Text(item.get("practice", ""), style=c_value),
                Text(f"{_st_icon.get(st, '?')} {st}", style=_st_style.get(st, c_dim)),
                Text(sig, style=c_muted),
                Text(item.get("recommendation", "")[:55], style=c_dim),
            )
        _add_table(pdod_table)

        # DoD ordering (typical sequence)
        dod_ordering = proposed_dod.get("ordering", [])
        if len(dod_ordering) >= 2:
            ord_row = Text(PAD + "  ", justify="left")
            ord_row.append("Typical order: ", style=c_muted)
            ord_row.append(" \u2192 ".join(dod_ordering), style=c_value)
            _add(ord_row)

        # Custom DoD steps (team-specific patterns)
        custom_steps = proposed_dod.get("custom_steps", [])
        if custom_steps:
            _add(Text(""))
            cs_row = Text(PAD + "  ", justify="left")
            cs_row.append("Team-specific steps: ", style=c_muted)
            cs_parts = [f'"{cs["title"]}" ({cs["pct"]}%)' for cs in custom_steps[:4]]
            cs_row.append(", ".join(cs_parts), style=c_value)
            _add(cs_row)


def _ta_writing_patterns(ctx: _TaCtx, profile) -> None:
    _heading, _kv = ctx.heading, ctx.kv
    # ── Writing Patterns ──────────────────────────────────────────────
    wp = profile.writing_patterns
    wp_items: list[tuple[str, str, str]] = []
    if wp.uses_given_when_then:
        wp_items.append(("AC format", "Given/When/Then \u2713", c_good))
    if wp.median_ac_count > 0:
        wp_items.append(("Median ACs/story", str(wp.median_ac_count), c_value))
    if wp.median_task_count_per_story > 0:
        wp_items.append(("Median tasks/story", str(wp.median_task_count_per_story), c_value))
    if wp.subtask_label_distribution:
        parts = [f"{lbl} {int(pct * 100)}%" for lbl, pct in wp.subtask_label_distribution[:4]]
        wp_items.append(("Sub-task types", " \u00b7 ".join(parts), c_muted))
    if wp.common_personas:
        wp_items.append(("Personas", ", ".join(wp.common_personas[:5]), c_muted))

    if wp_items:
        _heading("Writing Patterns")
        for wp_label, wp_val, wp_sty in wp_items:
            _kv(wp_label, wp_val, wp_sty)


def _ta_naming(ctx: _TaCtx, profile) -> None:
    _add, _heading, _kv, _ex = ctx.add, ctx.heading, ctx.kv, ctx.ex
    # ── Ticket Naming & Organisation ─────────────────────────────────
    _naming = _ex.get("naming_conventions", {})
    if isinstance(_naming, dict) and (
        _naming.get("title_prefixes")
        or _naming.get("label_distribution")
        or _naming.get("epic_examples")
        or _naming.get("template_sections")
    ):
        _heading("Ticket Naming & Organisation")

        # Title prefixes
        prefixes = _naming.get("title_prefixes", [])
        if prefixes:
            row = Text(PAD + "  ", justify="left")
            row.append("Title prefixes: ", style=c_muted)
            p_parts = [f"{p} {pct}%" for p, pct in prefixes[:5]]
            row.append(" \u00b7 ".join(p_parts), style=c_value)
            _add(row)
        else:
            _kv("Title prefixes", "none detected", c_dim)

        # Labels
        lbl_dist = _naming.get("label_distribution", [])
        lbl_pct = _naming.get("stories_with_labels_pct", 0)
        if lbl_dist:
            _kv(
                "Labels",
                f"{lbl_pct}% of stories labelled, avg {_naming.get('labels_per_story', 0)}/story",
                c_good if lbl_pct >= 70 else (c_warn if lbl_pct >= 30 else c_dim),
            )
            row = Text(PAD + "    ", justify="left")
            l_parts = [f"{lbl} {pct}%" for lbl, pct in lbl_dist[:6]]
            row.append(" \u00b7 ".join(l_parts), style=c_value)
            _add(row)
        else:
            _kv("Labels", "none detected", c_dim)

        # Epic naming
        epic_style = _naming.get("epic_naming_style", "")
        epic_ex = _naming.get("epic_examples", [])
        if epic_style and epic_ex:
            _kv("Epic naming", epic_style)
            for ex_title in epic_ex[:3]:
                row = Text(PAD + "    ", justify="left")
                row.append(f"\u2022 {ex_title[:50]}", style=c_example)
                _add(row)

        # Description template
        sections = _naming.get("template_sections", [])
        if sections:
            _kv("Description template", f"{len(sections)} recurring sections detected", c_good)
            row = Text(PAD + "    ", justify="left")
            s_parts = [f'"{s}"' for s, _ in sections[:5]]
            row.append(" \u2192 ".join(s_parts), style=c_value)
            _add(row)


def _ta_story_structure(ctx: _TaCtx, profile) -> None:
    _add, _heading, _kv, _ex = ctx.add, ctx.heading, ctx.kv, ctx.ex
    # ── Story & Epic Structure ──────────────────────────────────────
    _struct = _ex.get("story_structure", {})
    if isinstance(_struct, dict) and (
        _struct.get("subtask_ordering") or _struct.get("epic_completion") or _struct.get("skipped_types")
    ):
        _heading("Story & Epic Structure")

        # Subtask ordering
        ordering = _struct.get("subtask_ordering", [])
        if len(ordering) >= 2:
            _kv("Subtask sequence", " \u2192 ".join(ordering))

        # Skipped subtask types
        skipped = _struct.get("skipped_types", [])
        if skipped:
            skip_parts = [f"{s['type']} ({s['present_pct']}%)" for s in skipped]
            row = Text(PAD + "  ", justify="left")
            row.append("Rarely created: ", style=c_muted)
            row.append(" \u00b7 ".join(skip_parts), style=c_warn)
            _add(row)

        # Epic completion
        avg_comp = _struct.get("avg_epic_completion", 0)
        if avg_comp > 0:
            comp_sty = c_good if avg_comp >= 80 else (c_warn if avg_comp >= 50 else c_bad)
            _kv("Epic completion avg", f"{avg_comp}%", comp_sty)

        lingering = _struct.get("lingering_epics", [])
        if lingering:
            _add(Text(""))
            row = Text(PAD + "  ", justify="left")
            row.append(f"\u26a0 {len(lingering)} epics below 80% completion:", style=f"bold {c_warn}")
            _add(row)
            for ep in lingering[:3]:
                row = Text(PAD + "    ", justify="left")
                row.append(f"{ep.get('epic_title', '?')}", style=c_value)
                row.append(f"  {ep['completed']}/{ep['total']} done ({ep['rate']}%)", style=c_dim)
                _add(row)

        # Epic sprint spread (dependency indicator)
        spread = _struct.get("epic_sprint_spread", [])
        if spread:
            _add(Text(""))
            row = Text(PAD + "  ", justify="left")
            row.append("Multi-sprint epics:", style=c_muted)
            _add(row)
            for ep in spread[:3]:
                row = Text(PAD + "    ", justify="left")
                row.append(f"{ep.get('epic', '?')}", style=c_value)
                row.append(
                    f"  {ep['stories']} stories across {ep['sprints']} sprints",
                    style=c_dim,
                )
                _add(row)

        # Story splitting signals
        splitting = _struct.get("splitting_signals", [])
        if splitting:
            _add(Text(""))
            row = Text(PAD + "  ", justify="left")
            row.append("Story size variation within epics:", style=c_muted)
            _add(row)
            for sp in splitting[:3]:
                row = Text(PAD + "    ", justify="left")
                row.append(f"{sp.get('epic', '?')}", style=c_value)
                row.append(
                    f"  {sp['story_count']} stories, {sp['point_range']} pts range",
                    style=c_dim,
                )
                _add(row)


def _ta_estimation_bias(ctx: _TaCtx, profile) -> None:
    _add, _heading, _kv, _ex = ctx.add, ctx.heading, ctx.kv, ctx.ex
    _addl = _ex.get("additional_patterns", {})
    if not isinstance(_addl, dict):
        return
    # Estimation bias
    est = _addl.get("estimation_bias", {})
    if isinstance(est, dict) and est.get("sample", 0) >= 5:
        _heading("Estimation Accuracy")
        u_pct = est.get("underestimated_pct", 0)
        o_pct = est.get("overestimated_pct", 0)
        a_pct = est.get("accurate_pct", 0)
        _kv("Accurate", f"{a_pct}%", c_good if a_pct >= 60 else c_warn)
        _kv("Underestimated", f"{u_pct}% (took >2x expected)", c_bad if u_pct >= 30 else c_muted)
        _kv("Overestimated", f"{o_pct}% (finished in <½ expected)", c_warn if o_pct >= 20 else c_muted)
        worst = est.get("worst_sizes", [])
        if worst:
            row = Text(PAD + "  ", justify="left")
            row.append("Most underestimated: ", style=c_muted)
            row.append(", ".join(f"{p}pt" for p in worst), style=c_bad)
            _add(row)


def _ta_seasonal_and_bugs(ctx: _TaCtx, profile) -> None:
    _add, _heading, _kv, _ex = ctx.add, ctx.heading, ctx.kv, ctx.ex
    _addl = _ex.get("additional_patterns", {})
    if not isinstance(_addl, dict):
        return
    # Seasonal velocity
    seas = _addl.get("seasonal", {})
    if isinstance(seas, dict) and seas.get("monthly_avg"):
        monthly = seas["monthly_avg"]
        low = seas.get("low_months", {})
        high = seas.get("high_months", {})
        _heading("Seasonal Patterns")
        row = Text(PAD + "  ", justify="left")
        m_parts = [f"{m} {v:g}" for m, v in monthly.items()]
        row.append(" \u00b7 ".join(m_parts), style=c_muted)
        _add(row)
        if low:
            for m, v in low.items():
                row = Text(PAD + "  ", justify="left")
                row.append(f"\u2193 {m}: {v:g} pts", style=c_bad)
                row.append(f" (avg {seas.get('overall_avg', 0):g})", style=c_dim)
                _add(row)
        if high:
            for m, v in high.items():
                row = Text(PAD + "  ", justify="left")
                row.append(f"\u2191 {m}: {v:g} pts", style=c_good)
                row.append(f" (avg {seas.get('overall_avg', 0):g})", style=c_dim)
                _add(row)
        if not low and not high:
            _add(Text(PAD + "    No significant seasonal variation detected.", style=c_dim))

    # Bug rate
    bugs = _addl.get("bug_rate", {})
    if isinstance(bugs, dict) and bugs.get("bug_count", 0) > 0:
        b_pct = bugs.get("bug_pct", 0)
        _kv(
            "Bug rate",
            f"{bugs['bug_count']} bugs ({b_pct}% of stories, {bugs.get('bug_pts', 0):g} pts)",
            c_warn if b_pct >= 10 else c_muted,
        )


def _ta_ac_patterns(ctx: _TaCtx, profile) -> None:
    _add, _heading, _kv, _link = ctx.add, ctx.heading, ctx.kv, ctx.link
    _ex = ctx.ex
    # ── Acceptance Criteria Patterns ─────────────────────────────────
    ac_pat = _ex.get("ac_patterns", {})
    if isinstance(ac_pat, dict) and ac_pat.get("stories_with_ac_pct") is not None:
        ac_pct = ac_pat.get("stories_with_ac_pct", 0)
        _heading("Acceptance Criteria Patterns")

        # Parse stats
        _ps = _ex.get("parse_stats", {})
        if isinstance(_ps, dict) and _ps.get("llm_parsed", 0) > 0:
            _kv(
                "Analysis method",
                f"LLM parsed {_ps['llm_parsed']}/{_ps.get('total', 0)} stories",
                c_accent,
            )

        ac_cov_sty = c_good if ac_pct >= 70 else (c_warn if ac_pct >= 40 else c_bad)
        _kv("Stories with ACs", f"{ac_pct}%", ac_cov_sty)

        if ac_pct == 0:
            # No ACs found — this is a significant finding
            _add(
                Text(
                    PAD + "  No acceptance criteria detected in any story. "
                    "ACs help define what 'done' means and reduce ambiguity.",
                    style=c_bad,
                    justify="left",
                )
            )
        else:
            _kv("Median ACs/story", str(ac_pat.get("median_ac", 0)))

            spec = ac_pat.get("specificity", {})
            spec_label = spec.get("label", "unknown")
            spec_sty = c_good if spec_label == "precise" else (c_warn if spec_label == "moderate" else c_bad)
            _kv(
                "Specificity",
                f"{spec_label} ({spec.get('precise_pct', 0)}% precise, {spec.get('vague_pct', 0)}% vague)",
                spec_sty,
            )

            # Themes with examples
            themes = ac_pat.get("themes", {})
            t_examples = ac_pat.get("theme_examples", {})
            if themes:
                _add(Text(""))
                for theme, pct in list(themes.items())[:5]:
                    row = Text(PAD + "    ", justify="left")
                    row.append(f"{theme}", style="bold " + c_value)
                    row.append(f"  {pct}%", style=c_muted)
                    ex = t_examples.get(theme)
                    if ex and isinstance(ex, dict) and ex.get("issue_key"):
                        ek = ex["issue_key"]
                        eu = ex.get("issue_url", "")
                        sm = ex.get("summary", "")
                        row.append("  ")
                        row.append(ek, style=_link(ek, eu))
                        if sm:
                            row.append(f"  {sm}", style=c_dim)
                    _add(row)

            # By discipline
            by_disc = ac_pat.get("by_discipline", {})
            if len(by_disc) >= 2:
                row = Text(PAD + "  ", justify="left")
                row.append("By discipline: ", style=c_muted)
                d_parts = [f"{d} {v['avg_ac']:.0f} avg" for d, v in by_disc.items()]
                row.append(" \u00b7 ".join(d_parts), style=c_value)
                _add(row)

            # Spillover correlation
            spill = ac_pat.get("spillover_correlation", {})
            low_s = spill.get("low_ac_spill_pct", 0)
            high_s = spill.get("high_ac_spill_pct", 0)
            if low_s > high_s + 5 and spill.get("low_ac_count", 0) >= 5:
                row = Text(PAD + "  ", justify="left")
                row.append(f"0-1 ACs: {low_s}% spill", style=c_bad)
                row.append(" vs ", style=c_dim)
                row.append(f"3+ ACs: {high_s}% spill", style=c_good)
                row.append(" \u2014 more ACs = better completion", style=c_dim)
                _add(row)


def _ta_epic_sizing(ctx: _TaCtx, profile) -> None:
    _heading, _kv = ctx.heading, ctx.kv
    # ── Epic Sizing ───────────────────────────────────────────────────
    epic = profile.epic_pattern
    if epic.sample_count > 0:
        _heading("Epic Sizing")
        _kv("Avg stories/epic", f"{epic.avg_stories_per_epic:.0f}")
        _kv("Avg points/epic", f"{epic.avg_points_per_epic:.0f}")
        lo, hi = epic.typical_story_count_range
        if lo > 0 or hi > 0:
            _kv("Story count range", f"{lo}\u2013{hi}")


def _ta_repositories(ctx: _TaCtx, profile) -> None:
    _add_table, _add, _heading, _pct_dots = ctx.add_table, ctx.add, ctx.heading, ctx.pct_dots
    _ex = ctx.ex
    # ── Repository Activity ─────────────────────────────────────────
    repos = _ex.get("repositories", {})
    if isinstance(repos, dict) and repos.get("top_repos"):
        top = repos["top_repos"]
        stories_with = repos.get("stories_with_repos", 0)
        _heading("Repository Activity")

        if stories_with:
            sources = repos.get("detection_sources") or []
            if sources:
                src_txt = ", ".join(sources)
                sub = f"  Sources: {src_txt}  ·  {stories_with} stories with repo signals"
            else:
                sub = f"  Repo signals from {stories_with} stories (see ticket text / links)"
            _add(Text(PAD + sub, style="rgb(80,80,100)", justify="left"))

        # Top repos table
        repo_table = RichTable(
            show_header=True,
            header_style=c_muted,
            box=None,
            padding=(0, 2),
            pad_edge=False,
        )
        repo_table.add_column("Repository", width=28)
        repo_table.add_column("Stories", justify="right", width=8)
        repo_table.add_column("Share", width=12)
        repo_table.add_column("Avg cycle", justify="right", width=10)

        avg_cts = repos.get("repo_avg_cycle_time", {})
        spill_repos_set = {r["repo"] for r in repos.get("spillover_repos", []) if isinstance(r, dict)}

        for r in top[:8]:
            if not isinstance(r, dict):
                continue
            repo_name = r.get("repo", "")
            cnt = r.get("stories", 0)
            pct = r.get("pct", 0)
            avg_ct = avg_cts.get(repo_name)
            bar = _pct_dots(pct, w=10)
            name_style = f"bold {c_warn}" if repo_name in spill_repos_set else c_value
            ct_text = Text(f"{avg_ct:.0f}d" if avg_ct else "—", style=c_warn if avg_ct and avg_ct > 15 else c_muted)
            repo_table.add_row(
                Text(repo_name[:28], style=name_style),
                Text(str(cnt), style=c_muted),
                Text(bar, style=c_accent),
                ct_text,
            )

        _add_table(repo_table)

        # Spillover-prone repos
        spill_repos = repos.get("spillover_repos", [])
        if spill_repos:
            _add(Text(""))
            _add(
                Text(
                    PAD + "  Repos with highest spillover rate:",
                    style=c_muted,
                    justify="left",
                )
            )
            for sr in spill_repos[:3]:
                if not isinstance(sr, dict):
                    continue
                t = Text(PAD + "    ", justify="left")
                t.append(sr.get("repo", "")[:28], style=f"bold {c_warn}")
                t.append(
                    f"  {sr.get('spill_rate', 0)}% of stories spill ({sr.get('spills', 0)} times)",
                    style=c_muted,
                )
                _add(t)

        # Repos per point value
        by_pts = repos.get("by_pts", {})
        if by_pts:
            _add(Text(""))
            _add(
                Text(
                    PAD + "  Repos by story size:",
                    style=c_muted,
                    justify="left",
                )
            )
            for pts_key in sorted(by_pts.keys(), key=lambda x: int(x)):
                pt_repos = by_pts[pts_key]
                if not pt_repos:
                    continue
                t = Text(PAD + "    ", justify="left")
                t.append(f"{pts_key}pt  ", style=c_accent)
                t.append(", ".join(str(r) for r in pt_repos[:3]), style=c_dim)
                _add(t)


def _ta_recommendations(ctx: _TaCtx, profile) -> None:
    # ── Recommendations ─────────────────────────────────────────────
    _add, _heading, _ex = ctx.add, ctx.heading, ctx.ex
    width = ctx.width
    recs = compute_recommendations(profile, _ex)
    if recs:
        _heading("Recommendations")
        for icon_label, rec_text in recs:
            _add(Text(""))
            t = Text(PAD + "  ", justify="left")
            t.append(icon_label, style=f"bold {c_warn}")
            _add(t)
            # Wrap recommendation text to fit screen
            max_w = max(40, width - len(PAD) - 10)
            words = rec_text.split()
            line_buf = ""
            for word in words:
                if len(line_buf) + len(word) + 1 > max_w:
                    r = Text(PAD + "    ", justify="left")
                    r.append(line_buf.strip(), style=c_muted)
                    _add(r)
                    line_buf = word + " "
                else:
                    line_buf += word + " "
            if line_buf.strip():
                r = Text(PAD + "    ", justify="left")
                r.append(line_buf.strip(), style=c_muted)
                _add(r)


def _ta_insights(ctx: _TaCtx, profile) -> None:
    """Team coaching plan rendered as actionable, evidence-backed cards.

    Reads ``examples["insights"]`` (generated by one LLM call at analysis time,
    deterministic fallback otherwise). Old saved profiles may lack it — render
    the same "run a new analysis" empty state the overview summary uses.
    """
    _add, _ex = ctx.add, ctx.ex
    insights = _ex.get("insights", {})
    has_items = isinstance(insights, dict) and any(insights.get(k) for k, _ in INSIGHT_CATEGORIES)
    if not has_items:
        _add(Text(""))
        t = Text(PAD + "  ", justify="left")
        t.append(
            "No insights saved for this analysis — run a new analysis to generate them.",
            style=c_dim,
        )
        _add(t)
        return

    _ta_callout(
        ctx,
        "Team coaching plan",
        "Prioritised actions grounded in the delivery patterns found in this analysis.",
        colour=c_ai_head,
    )
    _ta_coaching_dashboard(ctx, insights)


def _ta_ai_adoption(ctx: _TaCtx, profile) -> None:
    """AI-adoption footprint: how much tracked work shows an AI-tool trace + coaching.

    Reads ``profile.ai_adoption`` for the numbers and ``examples["ai_adoption"]`` for
    coaching insights + coverage. Always frames the footprint as a lower bound. Old
    profiles (no scan) show the same "run a new analysis" empty state as the other cards.
    """
    _add, _ex = ctx.add, ctx.ex
    # Prefer the profile's signal; fall back to ctx.ai_sig for a delivery-off run
    # (no profile) where the signal is carried on the context instead.
    sig = getattr(profile, "ai_adoption", None) or ctx.ai_sig
    blob = ctx.code_blob or _ex.get("ai_adoption", {})
    scanned = (getattr(sig, "scanned_commits", 0) + getattr(sig, "scanned_prs", 0)) if sig else 0

    # A 0% that comes from unresolved identities is a data problem, not an AI
    # signal — say so wherever the card lands (empty state included).
    _selected = (blob.get("selected_users") or []) if isinstance(blob, dict) else []
    _identity_mismatch = bool(_selected) and not (blob.get("matched_identities") or {})

    def _mismatch_callout() -> None:
        unmatched = ", ".join(str(u) for u in (blob.get("unmatched_users") or _selected))
        _ta_callout(
            ctx,
            "IDENTITY MISMATCH",
            f"None of the selected members ({unmatched}) matched a git author name or email "
            "in the scan window — 0% here means unmatched identities, not zero AI usage. "
            "Compare the selection against real commit authors.",
            colour=c_warn,
        )

    ctx.heading("AI Footprint")
    if not sig or scanned == 0:
        if _identity_mismatch:
            _mismatch_callout()
        coverage = (blob.get("coverage") or []) if isinstance(blob, dict) else []
        detail = (
            "No AI-usage scan for this analysis. Run a new analysis with a repository or "
            "tracker configured to generate one."
        )
        if coverage:
            detail += " Coverage: " + " · ".join(str(gap) for gap in coverage)
        _ta_callout(ctx, "NO RECENT CODE DATA", detail, colour=c_muted)
        return
    marked = sig.ai_commits + sig.ai_prs
    if _identity_mismatch:
        _mismatch_callout()
    if marked == 0:
        _ta_callout(
            ctx,
            "LOWER BOUND SIGNAL",
            f"No detectable markers in {scanned} scanned items. Only tools that leave attribution "
            "traces are counted — Claude Code and aider trailers, Codex / Copilot / Devin / Cursor "
            "agent commits, PRs, and branches. Inline autocomplete and chat copy-paste (Copilot "
            "ghost-text, Cursor Tab, ChatGPT) leave no trace, so zero detected does not mean zero AI use.",
            colour=c_warn,
        )
    else:
        _ta_callout(
            ctx,
            "LOWER BOUND SIGNAL",
            "Only AI tools that leave a marker in commits or PR descriptions are counted. "
            "Inline IDE assistance leaves no trace, so actual usage may be higher.",
            colour=c_warn,
        )

    fp = getattr(sig, "footprint_pct", 0.0)
    fp_sty = c_good if fp >= 40 else (c_warn if fp >= 15 else c_bad)
    _window = blob.get("window_days") if isinstance(blob, dict) else None
    _scan_detail = f"{sig.scanned_commits} commits · {sig.scanned_prs} PRs"
    if _window:
        _scan_detail += f" · last {_window} days"
    # Below the sample floor a % swings wildly on single items — show the raw
    # count instead of a definitive-looking percentage (see footprint_small_sample).
    _small = footprint_small_sample(sig)
    _fp_tile = (
        ("Detectable footprint", f"{marked} of {scanned}", "small sample — % unstable", c_muted)
        if _small
        else ("Detectable footprint", f"{fp:.0f}%", "of scanned work carries a marker", fp_sty)
    )
    _ta_metric_tiles(
        ctx,
        [
            _fp_tile,
            ("Work scanned", str(scanned), _scan_detail, c_accent),
            ("AI-marked work", str(marked), f"{sig.ai_commits} commits · {sig.ai_prs} PRs", c_good),
        ],
    )
    if not _small:
        hero = Text(PAD + "  ")
        hero.append("Footprint  ", style=c_muted)
        hero.append_text(_ta_meter(fp, 100, width=24 if ctx.width >= 72 else 14, style=fp_sty))
        hero.append(f"  {fp:.0f}%", style=f"bold {fp_sty}")
        _add(hero)

    if isinstance(blob, dict) and blob.get("selected_users"):
        ctx.heading("Selected-user scope")
        ctx.kv("Selected", ", ".join(str(user) for user in blob.get("selected_users", [])))
        matched = blob.get("matched_identities") or {}
        ctx.kv("Matched", f"{len(matched)}/{len(blob.get('selected_users', []))}")
        if blob.get("unmatched_users"):
            ctx.kv("Unmatched", ", ".join(str(user) for user in blob["unmatched_users"]), c_warn)

    repos = list(getattr(sig, "repos_scanned", ()) or ())
    if sig.sources_scanned or repos:
        ctx.heading("Scan coverage")
        if sig.sources_scanned:
            ctx.kv("Sources", ", ".join(_source_label(s) for s in sig.sources_scanned))
        if repos:
            ctx.kv("Repositories", str(len(repos)))
            for repo in repos[:_TA_LIST_LIMIT]:
                ctx.kv("", repo)
            _ta_more_row(ctx, len(repos) - _TA_LIST_LIMIT, "repositories")

    _ta_ranked_bars(
        ctx,
        "Tools detected",
        sig.per_tool,
        labeler=lambda tool: "unlabelled AI" if tool == "other_ai" else tool,
    )
    _ta_ranked_bars(ctx, "Activity", sig.per_activity)
    _ta_ranked_bars(ctx, "Sources", getattr(sig, "per_source", ()), labeler=_source_label)
    _ta_ranked_bars(ctx, "Contributors", sig.per_author)

    # Sources that were NOT scanned (so local-vs-remote coverage is explicit, not silent).
    coverage = blob.get("coverage") if isinstance(blob, dict) else None
    if coverage:
        ctx.heading("Not scanned")
        for gap in coverage[:8]:
            g = Text(PAD + "  ", justify="left")
            g.append(f"• {gap}", style=c_dim)
            _add(g)
        _ta_more_row(ctx, len(coverage) - 8, "coverage notes (full list in export)")

    # Examples — real AI-marked items (with links/SHAs) so the numbers are inspectable.
    samples = blob.get("samples") if isinstance(blob, dict) else None
    if samples:
        ctx.heading("Evidence")
        table = RichTable(show_header=True, header_style=c_muted, box=None, padding=(0, 1), pad_edge=False)
        table.add_column("Tool", width=14)
        table.add_column("Tracked work", ratio=1)
        table.add_column("Reference", ratio=1)
        for s in samples[:8]:
            tool = "unlabelled AI" if s.get("tool") == "other_ai" else s.get("tool", "")
            title = str(s.get("title", "")).strip()
            ref = s.get("url") or (f"commit {s.get('key')}" if s.get("key") else "")
            ref_text = Text(str(ref), style=f"underline {c_accent} link {ref}" if s.get("url") else c_example)
            table.add_row(Text(str(tool), style=c_ai_head), Text(title, style=c_value), ref_text)
        ctx.add_table(table)
        _ta_more_row(ctx, len(samples) - 8, "AI-marked items (full list in export)")

    insights = blob.get("insights", {}) if isinstance(blob, dict) else {}
    if isinstance(insights, dict) and any(insights.get(k) for k, _ in INSIGHT_CATEGORIES):
        ctx.heading("Recommended actions")
        _ta_coaching_dashboard(ctx, insights)


def _ta_code_health(ctx: _TaCtx, profile) -> None:
    """Selected-user code-change health, deliberately separate from AI markers."""
    blob = ctx.code_blob or ctx.ex.get("ai_adoption", {})
    health = blob.get("repository_health", {}) if isinstance(blob, dict) else {}
    ctx.heading("Code Health — Selected-user Changed Files")
    if not health:
        coverage = (blob.get("coverage") or []) if isinstance(blob, dict) else []
        detail = "No attributable changed files were available for the selected users."
        if coverage:
            detail += " Coverage: " + " · ".join(str(gap) for gap in coverage)
        _ta_callout(ctx, "NO RECENT CODE-HEALTH DATA", detail, colour=c_muted)
        return
    _ta_callout(
        ctx,
        "SELECTED-USER SCOPE",
        "These findings cover files attributable to the selected users' commits and authored PRs. "
        "Untouched repositories and unrelated contributors are not analysed.",
        colour=c_warn,
    )
    _ta_metric_tiles(
        ctx,
        [
            ("Files analysed", str(health.get("files_analysed", 0)), "selected-user changes", c_accent),
            ("Repositories touched", str(health.get("repositories_touched", 0)), "by selected users", c_value),
            ("Change findings", str(health.get("findings", 0)), "evidence-backed indicators", c_warn),
        ],
    )
    _ta_ranked_bars(ctx, "Findings by category", tuple((health.get("by_category") or {}).items()))
    selected = blob.get("selected_users") or []
    if selected:
        ctx.heading("Selected-user scope")
        ctx.kv("Selected", ", ".join(str(user) for user in selected))
        matched = blob.get("matched_identities") or {}
        ctx.kv("Matched", f"{len(matched)}/{len(selected)}")
        if blob.get("unmatched_users"):
            ctx.kv("Unmatched", ", ".join(str(user) for user in blob["unmatched_users"]), c_warn)
    actions = blob.get("action_plan", []) if isinstance(blob, dict) else []
    if actions:
        ctx.heading("Prioritized action plan")
        for action in actions[:8]:
            _ta_callout(
                ctx,
                f"{str(action.get('priority', 'medium')).upper()} · {action.get('title', '')}",
                f"{action.get('detail', '')} Scope: {', '.join(action.get('affected_scope', []))}. "
                f"Owner: {action.get('owner_role', '')} · Effort: {action.get('effort', '')}.",
                colour=c_warn if action.get("priority") == "high" else c_accent,
            )


def _ta_doc_quality(ctx: _TaCtx, profile) -> None:
    """Documentation quality: how clear the team's written pages are + how AI shows up.

    Reads ``profile.doc_quality`` for the numbers and ``examples["doc_quality"]`` for
    coaching insights + coverage. Clarity is a readability score and usefulness
    measures purpose, ownership, structure, and actionability. Old profiles show the same
    "run a new analysis" empty state as the other cards.
    """
    _add, _ex = ctx.add, ctx.ex
    sig = getattr(profile, "doc_quality", None) or ctx.doc_sig
    blob = ctx.doc_blob or _ex.get("doc_quality", {})
    pages = getattr(sig, "pages_scanned", 0) if sig else 0

    ctx.heading("Documentation")
    if not sig or pages == 0:
        coverage = (blob.get("coverage") or []) if isinstance(blob, dict) else []
        detail = (
            "No documentation scan for this analysis. Run a new analysis with Notion or "
            "Confluence configured to generate one."
        )
        if coverage:
            detail += " Coverage: " + " · ".join(str(gap) for gap in coverage)
        _ta_callout(ctx, "NO DOCUMENTATION DATA", detail, colour=c_muted)
        return
    _ta_callout(
        ctx,
        "HOW TO READ THESE SIGNALS",
        "Clarity measures readability. Usefulness measures whether pages state their purpose, "
        "owner, structure, and concrete actions. Explicit AI disclosures remain a lower bound. "
        "Documentation is scanned workspace-wide and attributed to each page's last editor; "
        "the code scan covers only the selected members.",
        colour=c_warn,
    )
    _doc_small = doc_small_sample(sig)
    if _doc_small:
        _ta_callout(
            ctx,
            "SMALL SAMPLE",
            f"Averages cover only {pages} page(s) — read them as examples, not a trend.",
            colour=c_muted,
        )

    clarity = getattr(sig, "avg_clarity", 0.0)
    cl_sty = c_muted if _doc_small else (c_good if clarity >= 60 else (c_warn if clarity >= 40 else c_bad))
    usefulness = getattr(sig, "avg_usefulness", 0.0)
    useful_sty = c_muted if _doc_small else (c_good if usefulness >= 70 else (c_warn if usefulness >= 50 else c_bad))
    _ta_metric_tiles(
        ctx,
        [
            ("Average clarity", f"{clarity:.0f}/100", "higher means easier to read", cl_sty),
            (
                "Pages scanned",
                str(pages),
                (", ".join(sig.platforms_scanned) or "platform unavailable")
                + (
                    f" · last {blob.get('window_days')} days"
                    if isinstance(blob, dict) and blob.get("window_days")
                    else ""
                ),
                c_accent,
            ),
            (
                "Average usefulness",
                f"{usefulness:.0f}/100",
                f"{getattr(sig, 'actionable_pages', 0)} actionable · {getattr(sig, 'owned_pages', 0)} owned",
                useful_sty,
            ),
            (
                "Explicit AI markers",
                str(sig.ai_marked_pages),
                "lower-bound disclosure count",
                c_warn if sig.ai_marked_pages else c_muted,
            ),
        ],
    )
    clarity_line = Text(PAD + "  ")
    clarity_line.append("Clarity  ", style=c_muted)
    clarity_line.append_text(_ta_meter(clarity, 100, width=24 if ctx.width >= 72 else 14, style=cl_sty))
    clarity_line.append(f"  {clarity:.0f}/100", style=f"bold {cl_sty}")
    _add(clarity_line)

    total_split = max(1, sig.clear_pages + sig.mixed_pages + sig.unclear_pages)
    split_width = 24 if ctx.width >= 72 else 14
    n_clear = round(sig.clear_pages / total_split * split_width)
    n_mixed = round(sig.mixed_pages / total_split * split_width)
    n_unclear = max(0, split_width - n_clear - n_mixed)
    split = Text(PAD + "  ")
    split.append("Page mix ", style=c_muted)
    split.append("▰" * n_clear, style=c_good)
    split.append("▰" * n_mixed, style=c_warn)
    split.append("▰" * n_unclear, style=c_bad)
    split.append(
        f"  {sig.clear_pages} clear · {sig.mixed_pages} mixed · {sig.unclear_pages} unclear",
        style=c_dim,
    )
    _add(split)

    if sig.per_platform:
        _ta_ranked_bars(ctx, "Platforms", sig.per_platform)

    # Pages worth a look
    if sig.flagged_pages:
        ctx.heading("Pages needing attention")
        flagged = RichTable(show_header=True, header_style=c_muted, box=None, padding=(0, 1), pad_edge=False)
        flagged.add_column("Page", ratio=1)
        flagged.add_column("Why it was flagged", ratio=2)
        for title, reason in sig.flagged_pages:
            flagged.add_row(Text(title, style=c_value), Text(reason, style=c_warn))
        ctx.add_table(flagged)

    # Examples — real scanned pages (with links) so the scores are inspectable.
    samples = blob.get("samples") if isinstance(blob, dict) else None
    if samples:
        ctx.heading("Page evidence")
        sample_table = RichTable(show_header=True, header_style=c_muted, box=None, padding=(0, 1), pad_edge=False)
        sample_table.add_column("Page", ratio=2)
        sample_table.add_column("Platform", width=12)
        sample_table.add_column("Clarity", width=9)
        sample_table.add_column("Useful", width=8)
        for s in samples:
            title = str(s.get("title", "")).strip()
            url = str(s.get("url", "") or "")
            page = Text(title, style=f"underline {c_accent} link {url}" if url else c_value)
            if url:
                page.append(f"\n{url}", style=f"dim underline {c_accent} link {url}")
            sample_table.add_row(
                page,
                Text(str(s.get("platform", "")), style=c_muted),
                Text(f"{s.get('clarity', 0):.0f}/100", style=c_value),
                Text(f"{s.get('usefulness', 0):.0f}/100", style=c_dim),
            )
        ctx.add_table(sample_table)

    insights = blob.get("insights", {}) if isinstance(blob, dict) else {}
    if isinstance(insights, dict) and any(insights.get(k) for k, _ in INSIGHT_CATEGORIES):
        ctx.heading("Recommended actions")
        _ta_coaching_dashboard(ctx, insights)
    actions = blob.get("action_plan", []) if isinstance(blob, dict) else []
    if actions:
        ctx.heading("Prioritized action plan")
        for action in actions[:8]:
            _ta_callout(
                ctx,
                f"{str(action.get('priority', 'medium')).upper()} · {action.get('title', '')}",
                f"{action.get('detail', '')} Scope: {', '.join(action.get('affected_scope', []))}. "
                f"Owner: {action.get('owner_role', '')} · Effort: {action.get('effort', '')}.",
                colour=c_warn if action.get("priority") == "high" else c_accent,
            )


# The overview cards: title, section builders (render order), glossary terms.
_TA_CARDS: dict[str, dict] = {
    "velocity": {
        "title": "Velocity & Sprints",
        "builders": (
            _ta_sprint_names,
            _ta_recurring,
            _ta_team_velocity,
            _ta_sprint_breakdown,
            _ta_shadow_spillover,
            _ta_scope,
            _ta_spillover_root_causes,
        ),
        "glossary": ("churn", "delta", "spill", "variance"),
    },
    "team": {
        "title": "Team Members",
        "builders": (_ta_team_members,),
        "glossary": ("spill", "cycle"),
    },
    "estimation": {
        "title": "Estimation & Points",
        "builders": (
            _ta_discipline_calibration,
            _ta_point_meanings,
            _ta_story_shape,
            _ta_estimation_bias,
            _ta_epic_sizing,
        ),
        "glossary": ("cycle", "confidence"),
    },
    "workflow": {
        "title": "Workflow & DoD",
        "builders": (_ta_task_decomposition, _ta_dod_inferred, _ta_board_workflow, _ta_proposed_dod),
        "glossary": (),
    },
    "writing": {
        "title": "Writing Style",
        "builders": (_ta_writing_patterns, _ta_naming, _ta_story_structure, _ta_ac_patterns),
        "glossary": (),
    },
    "trends": {
        "title": "Trends & Repos",
        "builders": (_ta_seasonal_and_bugs, _ta_repositories),
        "glossary": ("variance",),
    },
    "recommendations": {
        "title": "Recommendations",
        "builders": (_ta_recommendations,),
        "glossary": (),
    },
    "ai-adoption": {
        "title": "AI Footprint",
        "builders": (_ta_ai_adoption,),
        "glossary": (),
    },
    "code-health": {
        "title": "Code Health",
        "builders": (_ta_code_health,),
        "glossary": (),
    },
    "documentation": {
        "title": "Documentation",
        "builders": (_ta_doc_quality,),
        "glossary": (),
    },
    "insights": {
        "title": "Team Insights",
        "builders": (_ta_insights,),
        "glossary": (),
    },
}
_TA_CARD_ORDER: tuple[str, ...] = tuple(_TA_CARDS)


# Delivery cards (everything except the two global cards + the insights coach).
_DELIVERY_CARD_ORDER: tuple[str, ...] = tuple(
    k for k in _TA_CARDS if k not in ("ai-adoption", "code-health", "documentation")
)


def visible_card_order(
    profile,
    has_code: bool,
    has_docs: bool,
    *,
    has_code_health: bool = False,
    analysis_features: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Which section cards to show, composing per-tracker delivery cards with the
    global code/docs cards.

    Delivery cards (velocity/contributors/… + insights) appear iff there's a delivery
    ``profile`` for the active tracker; ``ai-adoption`` iff the global Code scan ran;
    ``documentation`` iff the global Docs scan ran. The two global cards do NOT depend
    on the active tracker, so they stay put when the delivery toggle switches. The
    overview and the results-loop navigation share this so the selection index and the
    rendered card list can never drift apart.
    """
    order: list[str] = []
    if profile is not None:
        order.extend(k for k in _DELIVERY_CARD_ORDER if k != "insights")
    features = set(analysis_features or ())
    explicit = analysis_features is not None
    # code-health first: it's deterministic, so it sits with the regular cards,
    # ABOVE the ✦ AI-POWERED INSIGHTS group the LLM-backed cards render under.
    if has_code_health and (not explicit or "code_health" in features):
        order.append("code-health")
    if has_code and (not explicit or "ai_footprint" in features):
        order.append("ai-adoption")
    if has_docs and (not explicit or "documentation" in features):
        order.append("documentation")
    if profile is not None:
        order.append("insights")
    return tuple(order) or ("ai-adoption",)  # never empty — always render something


def _ta_card_teaser(ctx: _TaCtx, profile, key: str) -> str:
    """One-line stat teaser shown next to a card title on the overview."""
    stats = ctx.stats
    ex = ctx.ex
    if key == "velocity":
        parts = []
        if stats.get("velocity"):
            parts.append(f"{stats['velocity']:g} pts/sprint")
        if stats.get("completion_rate"):
            parts.append(f"{stats['completion_rate']:.0f}% done")
        return " · ".join(parts)
    if key == "team":
        parts = []
        if stats.get("team_size"):
            parts.append(f"{stats['team_size']} contributors")
        if stats.get("per_dev"):
            parts.append(f"{stats['per_dev']:g} pts/dev")
        return " · ".join(parts)
    if key == "estimation":
        if stats.get("estimation_accuracy"):
            return f"{stats['estimation_accuracy']:.0f}% estimates hold"
        return ""
    if key == "workflow":
        td = ex.get("task_decomposition", {})
        if isinstance(td, dict) and td.get("total_stories"):
            return f"{td.get('stories_with_tasks', 0)}/{td['total_stories']} stories have subtasks"
        return ""
    if key == "writing":
        wp = profile.writing_patterns
        parts = []
        if wp.median_ac_count > 0:
            parts.append(f"{wp.median_ac_count:g} ACs median")
        if wp.uses_given_when_then:
            parts.append("Given/When/Then")
        return " · ".join(parts)
    if key == "trends":
        parts = []
        if stats.get("trend"):
            parts.append(f"velocity {stats['trend']}")
        repos = ex.get("repositories", {})
        if isinstance(repos, dict) and repos.get("top_repos"):
            parts.append(f"{len(repos['top_repos'])} repos")
        return " · ".join(parts)
    if key == "recommendations":
        n = len(compute_recommendations(profile, ex))
        return f"⚠ {n} flagged" if n else "none flagged"
    if key == "ai-adoption":
        sig = getattr(profile, "ai_adoption", None) or ctx.ai_sig
        scanned = (getattr(sig, "scanned_commits", 0) + getattr(sig, "scanned_prs", 0)) if sig else 0
        if scanned:
            if footprint_small_sample(sig):
                marked = getattr(sig, "ai_commits", 0) + getattr(sig, "ai_prs", 0)
                return f"{marked} of {scanned} AI-marked (small sample)"
            return f"{sig.footprint_pct:.0f}% AI footprint"
        return "not scanned"
    if key == "code-health":
        health = (ctx.code_blob or ex.get("ai_adoption", {})).get("repository_health", {})
        if health:
            return f"{health.get('files_analysed', 0)} files · {health.get('findings', 0)} findings"
        return "no attributable changes"
    if key == "documentation":
        sig = getattr(profile, "doc_quality", None) or ctx.doc_sig
        pages = getattr(sig, "pages_scanned", 0) if sig else 0
        if pages:
            suffix = " (small sample)" if doc_small_sample(sig) else ""
            return f"{sig.avg_clarity:.0f}/100 clarity · {pages} pages{suffix}"
        return "not scanned"
    if key == "insights":
        ins = ex.get("insights", {})
        if isinstance(ins, dict):
            parts = [
                f"{len(ins[k])} {k}" for k, _label in INSIGHT_CATEGORIES if isinstance(ins.get(k), list) and ins[k]
            ]
            if parts:
                return " · ".join(parts)
        return "none saved"
    return ""


def _ta_source_comparison(ctx: _TaCtx) -> None:
    """'Both'-mode header: Jira vs Azure DevOps headline stats side by side.

    Deliberately a plain side-by-side table — the two trackers' numbers are NOT
    aggregated (velocity/point scales aren't comparable across trackers); this
    just makes it easy to see each source's figure next to the other's."""
    rows = ctx.comparison or []
    if not rows:
        return
    ctx.heading("Jira vs Azure DevOps")
    table = RichTable(show_header=True, box=None, pad_edge=False, padding=(0, 2, 0, 0))
    table.add_column("Metric", style=c_muted)
    table.add_column("Jira", style=c_value)
    table.add_column("Azure DevOps", style=c_value)
    for label, jira_val, azdo_val in rows:
        table.add_row(label, jira_val, azdo_val)
    ctx.add_table(table)


def _ta_overview(ctx: _TaCtx, profile, selected_card: int) -> None:
    """Overview page: headline stats, AI executive summary, section card list."""
    stats = ctx.stats

    # 'Both'-mode: lead with the per-tracker side-by-side comparison.
    if ctx.comparison:
        _ta_source_comparison(ctx)

    # Delivery headline + AI summary only when a delivery profile is present for the
    # active tracker; a code/docs-only view leads straight into its cards.
    if profile is not None:
        ctx.heading("At a Glance")
        depth = str(ctx.ex.get("analysis_depth", "")).strip().lower()
        if depth in ("quick", "deep"):
            ctx.kv("Analysis", f"{depth.capitalize()} mode")
        if stats.get("team_size"):
            ctx.kv("Team size", f"{stats['team_size']} contributors")
        if stats.get("velocity"):
            ctx.kv("Velocity", f"{stats['velocity']:g} ± {stats.get('stddev', 0):g} pts/sprint")
        rate = stats.get("completion_rate", 0)
        if rate:
            rate_sty = c_good if rate >= 80 else (c_warn if rate >= 60 else c_bad)
            ctx.kv("Completion", ctx.pct_dots(rate), rate_sty)
        acc = stats.get("delivery_accuracy", 0)
        if acc:
            acc_sty = c_good if acc >= 85 else (c_warn if acc >= 70 else c_bad)
            ctx.kv("Delivery accuracy", f"{acc}% of committed scope delivered", acc_sty)
        if stats.get("estimation_accuracy"):
            ctx.kv("Estimation accuracy", f"{stats['estimation_accuracy']:.0f}% of estimates hold")

        # AI executive summary (generated at analysis time; absent on old profiles)
        narrative = ctx.ex.get("narrative", {})
        summary = narrative.get("executive_summary", "") if isinstance(narrative, dict) else ""
        ctx.heading("Summary")
        if summary:
            for wrapped in _ta_wrap(str(summary), max(40, ctx.width - len(PAD) - 10)):
                t = Text(PAD + "  ", justify="left")
                t.append(wrapped, style=c_ai_text)
                ctx.add(t)
        else:
            t = Text(PAD + "  ", justify="left")
            t.append("No AI summary saved for this analysis — run a new analysis to generate one.", style=c_dim)
            ctx.add(t)
    else:
        ctx.heading("Components")
        t = Text(PAD + "  ", justify="left")
        t.append("This view has no delivery/velocity profile — code and/or docs only.", style=c_dim)
        ctx.add(t)

    ctx.heading("Sections")
    ctx.overview_first_card_row = ctx.rendered_lines
    # LLM-backed cards only — code-health is deterministic and deliberately
    # ordered above this group (see visible_card_order), so no star, no indent.
    ai_keys = {"ai-adoption", "documentation", "insights"}
    ai_heading_added = False
    for i, key in enumerate(ctx.visible_order):
        if key in ai_keys and not ai_heading_added:
            ctx.add(Text(""))
            label = Text(PAD + "  ")
            label.append("✦ AI-POWERED INSIGHTS", style=f"bold {c_ai_head}")
            label.append("  metrics · evidence · coaching", style=c_dim)
            ctx.add(label)
            ai_heading_added = True
        ctx.overview_card_rows.append(len(ctx.lines))
        card = _TA_CARDS[key]
        selected = i == selected_card
        row = Text(PAD + "  ", justify="left")
        row.append("▸ " if selected else "  ", style=c_accent if selected else c_dim)
        if key in ai_keys:
            row.append("✦ ", style=c_ai_head if selected else c_dim)
        row.append(f"{card['title']:<20s}", style=f"bold {c_accent}" if selected else c_value)
        teaser = _ta_card_teaser(ctx, profile, key)
        if teaser:
            row.append(f"  {teaser}", style=c_muted if selected else c_dim)
        ctx.add(row)
