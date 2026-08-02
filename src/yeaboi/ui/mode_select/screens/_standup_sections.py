"""Section registry + builders for the sectioned Daily Standup page.

Mirrors the team-analysis pattern (``_analysis_sections.py``): the standup page
shows an overview (headline stats + a selectable list of section cards) and a
detail view per card. Unlike analysis, the card list is COMPUTED per report —
each team member gets their own card — so the registry is a set of functions
over the standup data dict, not a static table.

Card keys: ``"summary"``, ``"my_update"`` (the standup user's own card),
``"team"`` (one row for everyone else — expands inline into
``"member:<name>"`` sub-rows when ``data["team_expanded"]`` is set),
``"activity"``, ``"schedule"``, and ``"notices"`` (only when the report carries
warnings). Sprint/day/confidence facts live in the pinned status strip built
by ``_build_standup_screen``, not in a card.

All colours come from ``STANDUP_THEME`` (no hardcoded values) and all rows are
height-1 ``Text`` lines, so the viewport is a plain list slice — no need for
the analysis module's height-aware packer. Cards may span more than one row
(the summary teaser wraps to two); ``_StandupCtx.card_rows`` records each
card's first row so the auto-scroll never assumes one row per card.

# See docs: "Daily Standup" — TUI page
"""

from __future__ import annotations

import io
import textwrap

import rich.box
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table as RichTable
from rich.text import Text

from yeaboi.ui.shared._components import PAD, Theme

_TEASER_W = 46  # max teaser length on an overview section row
_TITLE_W = 22  # section-title column width — teasers align to it, as does the summary continuation row


class _StandupCtx:
    """Renderable accumulator for Standup overview and detail screens.

    Overview rows remain height-one Text values. Detail pages may also contain
    Rich panels and tables, so ``item_heights`` records their measured terminal
    height for the same height-aware viewport packing used by Analysis mode.
    ``card_rows`` stores overview item indexes for selection auto-scroll.
    """

    def __init__(self, theme: Theme, width: int) -> None:
        self.lines: list = []
        self.item_heights: list[int] = []
        self.theme = theme
        self.width = width
        self.card_rows: list[int] = []

    def add(self, line, rendered_h: int = 1) -> None:
        self.lines.append(line)
        self.item_heights.append(rendered_h)

    def add_renderable(self, renderable) -> None:
        """Add a padded Rich renderable with its actual terminal height."""
        padded = Padding(renderable, (0, 1, 0, len(PAD)))
        console = Console(width=max(10, self.width - 7), file=io.StringIO(), legacy_windows=False)
        self.add(padded, max(1, len(console.render_lines(padded, pad=False))))

    def add_table(self, table: RichTable) -> None:
        self.add_renderable(table)

    def blank(self) -> None:
        self.add(Text(""))

    def heading(self, text: str) -> None:
        self.blank()
        h = Text(PAD + "  ", justify="left")
        h.append(text, style=f"bold {self.theme.accent}")
        self.add(h)
        self.add(Text(PAD + "  " + "─" * min(len(text), 40), style=self.theme.sep, justify="left"))

    def row(self, label: str, value: str, value_style: str = "") -> None:
        r = Text(PAD + "    ", justify="left")
        r.append(f"{label}:  ", style=self.theme.muted)
        r.append(str(value), style=value_style or self.theme.value)
        self.add(r)

    def line(self, text: str, style: str = "") -> None:
        self.add(Text(PAD + "    " + text, style=style or self.theme.value, justify="left"))

    def wrapped(self, text: str, style: str, *, indent: str = "    ", preserve_newlines: bool = False) -> None:
        """Append word-wrapped lines; optionally honour explicit newlines.

        preserve_newlines keeps the user's own paragraph breaks (multi-line
        self-reports typed with Alt+Enter) instead of collapsing them.
        """
        # -7: panel border+padding (6) plus the scrollbar column (1) — one char
        # over and the row wraps, silently eating a viewport row.
        wrap_w = max(24, self.width - len(PAD) - len(indent) - 7)
        paragraphs = text.splitlines() if preserve_newlines else [text]
        for para in paragraphs or [""]:
            for chunk in textwrap.wrap(para, width=wrap_w) or [""]:
                self.add(Text(PAD + indent + chunk, style=style, justify="left"))


# ---------------------------------------------------------------------------
# Card registry (computed per report)
# ---------------------------------------------------------------------------


def standup_card_order(data: dict) -> list[str]:
    """Return the ordered card keys for the current standup data.

    With no generated report yet only Schedule is available. With a report the
    standup user's own card is a top-level "my_update" row and everyone else
    lives under a single "team" row — expanded inline into ``member:<name>``
    sub-rows when ``data["team_expanded"]`` is set. Notices appears only when
    needed.
    """
    report = data.get("report")
    if report is None:
        return ["schedule"]
    order = ["summary", "my_update", "team"]
    if data.get("team_expanded"):
        order += [f"member:{m.name}" for m in _team_members(data)]
    order += ["activity"]
    # Only when a transcript has actually been reviewed — an empty card would
    # advertise a feature the user hasn't used rather than report a result.
    # A nudge IS a result ("3 standups went unchecked"), so it earns the card on
    # the same terms rather than being an exception to them.
    if data.get("review") is not None or data.get("nudge"):
        order.append("gaps")
    order += ["schedule"]
    if report.warnings:
        order.append("notices")
    return order


def _team_members(data: dict) -> list:
    """Member updates excluding the standup user (their card is "my_update")."""
    report = data.get("report")
    if report is None:
        return []
    my_name = data.get("my_name", "")
    return [m for m in report.member_updates if m.name != my_name]


def _confidence_style(theme: Theme, label: str) -> str:
    return {"On track": theme.good, "At risk": theme.warn, "Behind": theme.bad}.get(label, theme.muted)


def _member_is_active(m) -> bool:
    """True when the member has attributed activity today.

    Reports saved before ``activity_count`` existed deserialize with 0 for
    everyone — fall back to the summary text so old standups don't render the
    whole team as quiet.
    """
    if getattr(m, "activity_count", 0):
        return True
    return bool(m.summary) and m.summary != "No activity detected."


def _member(data: dict, name: str):
    report = data.get("report")
    if report is None:
        return None
    return next((m for m in report.member_updates if m.name == name), None)


def _confidence_text(report) -> str:
    conf = report.confidence_label or "unknown"
    if report.confidence_label and report.confidence_label != "Insufficient data":
        conf = f"{report.confidence_label}  ·  {report.confidence_pct}%"
    return conf


def standup_card_title(key: str, data: dict) -> str:
    """Human title for a card key; member sub-rows are just the member's name."""
    if key.startswith("member:"):
        return key[len("member:") :]
    return {
        "summary": "Team Summary",
        "my_update": "My Update",
        "team": "Team",
        "activity": "Activity",
        "gaps": "Transcript Review",
        "schedule": "Schedule",
        "notices": "⚠ Notices",
    }.get(key, key)


def standup_card_teaser(key: str, data: dict) -> str:
    """One-line dim teaser shown next to each section row on the overview."""
    report = data.get("report")
    config = data.get("config") or {}
    if key == "summary":
        text = (report.team_summary if report else "") or "No summary yet"
        return text[: _TEASER_W - 1] + "…" if len(text) > _TEASER_W else text
    if key == "my_update":
        m = _member(data, data.get("my_name", ""))
        if m is None:
            return "No update yet — Generate asks for it"
        gist = (m.summary or "No activity detected.")[:_TEASER_W]
        return f"{gist} · ✍ update" if m.self_report else gist
    if key == "team":
        members = _team_members(data)
        if not members:
            return "No member updates"
        active = sum(1 for m in members if _member_is_active(m))
        quiet = len(members) - active
        return f"{len(members)} update{'s' if len(members) != 1 else ''} · {active} active ● {quiet} quiet ○"
    if key.startswith("member:"):
        m = _member(data, key[len("member:") :])
        if m is None:
            return ""
        if not _member_is_active(m) and not m.self_report:
            return "no activity detected"
        gist = m.summary or "No activity detected."
        # Lead with the first ticket/PR reference so who-is-on-what scans at a glance.
        if getattr(m, "links", ()):
            gist = f"{m.links[0][0]} · {gist}"
        return gist[: _TEASER_W - 1] + "…" if len(gist) > _TEASER_W else gist
    if key == "activity":
        if report is None or not report.activity_counts:
            return "no sources"
        return ", ".join(f"{src}: {n}" for src, n in report.activity_counts)[:_TEASER_W]
    if key == "schedule":
        if not config:
            return "Not configured"
        state = "Enabled" if config.get("enabled") else "Off"
        return f"{state} · {config.get('time', '—')} · {config.get('weekdays', '—')}"
    if key == "gaps":
        review = data.get("review")
        nudge = data.get("nudge")
        if review is None:
            if nudge:
                n = len(nudge.missed_dates)
                return f"{n} standup{'s' if n != 1 else ''} unchecked · oldest {nudge.missed_dates[-1]}"[:_TEASER_W]
            return "Not reviewed yet"
        parts = [f"{len(review.gaps)} gap{'s' if len(review.gaps) != 1 else ''}"]
        if review.config_suggestions:
            parts.append(f"{len(review.config_suggestions)} to fix in config")
        filed = sum(1 for entry in (data.get("gap_issues") or []) if entry.get("issue_number"))
        if filed:
            parts.append(f"{filed} filed")
        return " · ".join(parts)[:_TEASER_W]
    if key == "notices":
        n = len(report.warnings) if report else 0
        return f"{n} notice{'s' if n != 1 else ''}"
    return ""


# ---------------------------------------------------------------------------
# Detail builders
# ---------------------------------------------------------------------------


def _member_link(label: str, url: str, theme: Theme) -> Text:
    """Build a compact evidence link for use inside a member category panel."""
    row = Text(no_wrap=True, overflow="ellipsis")
    row.append("↗ ", style=theme.dim)
    row.append(label or url, style=f"underline {theme.accent_bright} link {url}")
    if label and url:
        row.append(f"  {url}", style=f"{theme.dim} link {url}")
    return row


def _member_metric_tiles(ctx: _StandupCtx, member) -> None:
    """Analysis-style activity tiles for the member dashboard."""
    theme = ctx.theme
    ticketing = int(getattr(member, "ticketing_activity_count", 0) or 0)
    code = int(getattr(member, "code_activity_count", 0) or 0)
    docs = int(getattr(member, "documentation_activity_count", 0) or 0)
    total = max(int(getattr(member, "activity_count", 0) or 0), ticketing + code + docs)
    tiles = (
        ("TOTAL", total, "tracked updates", theme.accent_bright),
        ("TICKETING", ticketing, "Jira / Boards", theme.accent),
        ("CODE", code, "commits / PRs", theme.good),
        ("DOCUMENTATION", docs, "Confluence / Notion", theme.warn),
    )
    if ctx.width < 68:
        # Four stacked tiles are taller than the entire viewport on a compact
        # terminal. Preserve the same dashboard information in a 2×2 compact
        # card so it remains visible and scrollable.
        rows = []
        for start in (0, 2):
            row = Text()
            for idx, (label, value, _hint, colour) in enumerate(tiles[start : start + 2]):
                if idx:
                    row.append("    ")
                row.append(f"{label} ", style=theme.dim)
                row.append(str(value), style=f"bold {colour}")
            rows.append(row)
        ctx.add_renderable(
            Panel(
                Group(*rows),
                box=rich.box.ROUNDED,
                border_style=theme.sep,
                padding=(0, 1),
            )
        )
        return

    columns = 4 if ctx.width >= 112 else 2
    grid = RichTable.grid(expand=True, padding=(0, 1))
    for _ in range(columns):
        grid.add_column(ratio=1)
    for start in range(0, len(tiles), columns):
        cells = []
        for label, value, hint, colour in tiles[start : start + columns]:
            cells.append(
                Panel(
                    Group(
                        Text(label, style=theme.dim),
                        Text(str(value), style=f"bold {colour}"),
                        Text(hint, style=theme.muted, no_wrap=True, overflow="ellipsis"),
                    ),
                    box=rich.box.ROUNDED,
                    border_style=theme.sep,
                    padding=(0, 1),
                )
            )
        cells.extend(Text("") for _ in range(columns - len(cells)))
        grid.add_row(*cells)
    ctx.add_table(grid)


def _member_panel(
    ctx: _StandupCtx,
    title: str,
    summary: str,
    *,
    empty: str,
    links: tuple[tuple[str, str], ...] = (),
    colour: str = "",
) -> None:
    """Render one full-width category card with its evidence kept in context."""
    theme = ctx.theme
    body: list = [Text(summary or empty, style=theme.desc if summary else theme.muted)]
    if links:
        body.append(Text(""))
        evidence = Text("EVIDENCE", style=f"bold {theme.accent_bright}")
        evidence.append(f"  {len(links)} link{'s' if len(links) != 1 else ''}", style=theme.dim)
        body.append(evidence)
        body.extend(_member_link(label, url, theme) for label, url in links)
    ctx.add_renderable(
        Panel(
            Group(*body),
            title=f"[bold {colour or theme.accent}]{title}[/]",
            title_align="left",
            box=rich.box.ROUNDED,
            border_style=colour or theme.sep,
            padding=(0, 1),
        )
    )


def _detail_summary(ctx: _StandupCtx, data: dict) -> None:
    report = data["report"]
    theme = ctx.theme
    ctx.heading(f"Team Summary — {report.date}")
    ctx.wrapped(report.team_summary or "No team summary was produced.", theme.value)
    ctx.blank()
    ctx.row("Sprint", report.sprint_name or "unknown")
    if report.sprint_total_days:
        ctx.row("Day", f"{report.sprint_day} of {report.sprint_total_days}")
    ctx.row("Confidence", _confidence_text(report), _confidence_style(theme, report.confidence_label))
    if report.confidence_rationale:
        ctx.wrapped(report.confidence_rationale, theme.dim, indent="      ")


def _detail_member(ctx: _StandupCtx, data: dict, name: str) -> None:
    theme = ctx.theme
    m = _member(data, name)
    ctx.heading(standup_card_title(f"member:{name}", data))
    if m is None:
        ctx.line("No update found for this member.", theme.muted)
        return

    active = _member_is_active(m)
    source_label = {
        "combined": "SELF-REPORT + TRACKED",
        "self-reported": "SELF-REPORTED",
        "inferred": "TRACKED ACTIVITY",
    }.get(m.source, (m.source or "UNKNOWN").upper())
    status = Text(PAD + "    ")
    status_style = f"bold black on {theme.good if active else theme.muted}"
    status.append(f" {'ACTIVE' if active else 'QUIET'} ", style=status_style)
    status.append("  ")
    status.append(f" {source_label} ", style=f"bold {theme.accent_bright} on rgb(48,38,52)")
    ctx.add(status)
    ctx.blank()
    _member_metric_tiles(ctx, m)
    ctx.blank()

    if m.self_report:
        ctx.add_renderable(
            Panel(
                Text(m.self_report, style=theme.value),
                title=f"[bold {theme.accent_bright}]✍ In their words[/]",
                title_align="left",
                box=rich.box.ROUNDED,
                border_style=theme.accent,
                padding=(0, 1),
            )
        )
        ctx.blank()

    _member_panel(
        ctx,
        "General overview",
        m.summary,
        empty="No activity was detected for this member.",
        colour=theme.accent,
    )
    if getattr(m, "progress_note", ""):
        ctx.blank()
        _member_panel(
            ctx,
            "↺ Since last standup",
            m.progress_note,
            empty="",
            colour=theme.accent_bright,
        )
    ctx.blank()
    _member_panel(
        ctx,
        "Ticketing",
        m.ticketing_summary,
        empty="No ticketing activity detected in the selected Jira or Azure Boards scope.",
        links=tuple(getattr(m, "ticketing_links", ()) or ()),
        colour=theme.accent,
    )
    ctx.blank()
    _member_panel(
        ctx,
        "Code",
        m.code_summary,
        empty="No code activity detected in the selected repositories.",
        links=tuple(getattr(m, "code_links", ()) or ()),
        colour=theme.good,
    )
    ctx.blank()
    _member_panel(
        ctx,
        "Documentation",
        m.documentation_summary,
        empty="No documentation activity detected in the selected sources.",
        links=tuple(getattr(m, "documentation_links", ()) or ()),
        colour=theme.warn,
    )
    if getattr(m, "outlook", ""):
        ctx.blank()
        _member_panel(
            ctx,
            "→ Outlook",
            m.outlook,
            empty="",
            colour=theme.good,
        )
    if m.blockers:
        ctx.blank()
        ctx.add_renderable(
            Panel(
                Text(m.blockers, style=theme.value),
                title=f"[bold {theme.warn}]⚠ Blocker[/]",
                title_align="left",
                box=rich.box.ROUNDED,
                border_style=theme.warn,
                padding=(0, 1),
            )
        )
    category_links = (
        *getattr(m, "ticketing_links", ()),
        *getattr(m, "code_links", ()),
        *getattr(m, "documentation_links", ()),
    )
    if getattr(m, "links", ()) and not category_links:
        ctx.blank()
        _member_panel(
            ctx,
            "Evidence",
            "",
            empty="Links captured by an earlier Standup report.",
            links=tuple(m.links),
            colour=theme.accent_bright,
        )
    ctx.blank()
    based_on = {
        "combined": "self-report + tracked activity",
        "self-reported": "self-reported (no tracked activity)",
        "inferred": "inferred from tracked activity",
    }.get(m.source, m.source)
    ctx.row("Based on", based_on, theme.dim)


def _detail_activity(ctx: _StandupCtx, data: dict) -> None:
    report = data["report"]
    theme = ctx.theme
    ctx.heading("Activity")
    if report.activity_window:
        ctx.row("Window", report.activity_window, theme.dim)
    if report.activity_counts:
        for src, n in report.activity_counts:
            ctx.row(src.replace("_", " ").title(), f"{n} item{'s' if n != 1 else ''}")
        ctx.blank()
        ctx.wrapped(
            "Counts are the items examined per source since the last standup window. "
            "Sources are enabled by their identifying setting (GitHub repo, Jira project, "
            "local repo path, Confluence space, Notion root).",
            theme.dim,
        )
    else:
        ctx.wrapped(
            "No activity sources reported anything. Configure a local repo path here, or connect "
            "GitHub/Jira/Azure DevOps/Confluence/Notion in .env to infer updates from real activity.",
            theme.muted,
        )
    if getattr(report, "category_coverage", ()):
        ctx.blank()
        ctx.heading("Category coverage")
        for category, status in report.category_coverage:
            ctx.row(category.title(), status.replace("_", " "), theme.dim)
    if getattr(report, "skipped_sources", ()):
        ctx.blank()
        ctx.heading("Not scanned")
        for src, reason in report.skipped_sources:
            ctx.row(src.replace("_", " ").title(), reason, theme.muted)


def _detail_schedule(ctx: _StandupCtx, data: dict) -> None:
    theme = ctx.theme
    config = data.get("config") or {}
    schedule = data.get("schedule") or {}
    ctx.heading("Schedule")
    if config:
        ctx.row(
            "Enabled", "yes" if config.get("enabled") else "no", theme.good if config.get("enabled") else theme.muted
        )
        standup_time = config.get("time", "—")
        lead = config.get("lead_minutes", 10)
        ctx.row("Standup time", standup_time)
        if standup_time and standup_time != "—":
            from yeaboi.standup.scheduler import run_time_str

            ctx.row("Runs at", f"{run_time_str(standup_time, lead)}  ({lead} min before)")
        ctx.row("Weekdays", config.get("weekdays", "—"))
        ctx.row("Channels", ", ".join(config.get("delivery_channels", [])) or "—")
        if config.get("repo_path"):
            ctx.row("Local repo", config["repo_path"])
        if config.get("my_aliases"):
            ctx.row("My aliases", config["my_aliases"])
        # Service-hook detection (standup/automation.py): only shown when tuned
        # away from the defaults, to keep the card compact.
        if config.get("automation_handling", "exclude") == "off":
            ctx.row("Automation filter", "off", theme.muted)
        elif config.get("automation_markers"):
            ctx.row("Automation markers", config["automation_markers"])
    else:
        ctx.line("Not configured — set up a schedule from the Standup hub.", theme.muted)
    installed = schedule.get("installed")
    if installed is not None:
        ctx.row(
            "OS schedule",
            f"installed ({schedule.get('platform', '?')})" if installed else "not installed",
            theme.good if installed else theme.muted,
        )


def _detail_notices(ctx: _StandupCtx, data: dict) -> None:
    report = data["report"]
    ctx.heading("⚠ Notices")
    for w in report.warnings:
        ctx.wrapped(f"- {w}", ctx.theme.warn)


def _detail_gaps(ctx: _StandupCtx, data: dict) -> None:
    """What the standup MEETING revealed the report had missed, and why.

    Two clearly separated halves, because they need different actions: gaps in
    yeaboi itself become GitHub issues, while config gaps are the user's to fix
    and carry an exact remedy.
    """
    review = data.get("review")
    nudge = data.get("nudge")
    theme = ctx.theme
    if review is None:
        ctx.heading("Transcript Review")
        if nudge:
            ctx.wrapped(nudge.message, theme.warn)
            ctx.blank()
            shown = nudge.missed_dates[:8]
            ctx.line(f"    Unchecked: {', '.join(shown)}", theme.dim)
            if len(nudge.missed_dates) > len(shown):
                ctx.line(f"    …and {len(nudge.missed_dates) - len(shown)} more", theme.dim)
            ctx.blank()
            ctx.wrapped(
                "Press Review to paste a transcript, or to point Standup at your recordings folder.", theme.muted
            )
            return
        ctx.wrapped(
            "No standup transcript has been reviewed yet. Press Review to paste one, "
            "or to point Standup at the folder your recordings land in.",
            theme.muted,
        )
        return

    ctx.heading(f"Transcript Review · {review.standup_date or 'unknown date'}")
    if review.sources:
        ctx.line(f"    Read: {', '.join(s.filename for s in review.sources)}", theme.dim)
    if review.accuracy_note:
        ctx.wrapped(review.accuracy_note, theme.muted)

    filed_by_fingerprint = {entry.get("fingerprint"): entry for entry in (data.get("gap_issues") or [])}

    if review.gaps:
        ctx.blank()
        ctx.heading("Gaps in standup itself")
        for gap in review.gaps:
            ctx.line(f"    • {gap.title}", theme.value)
            if gap.root_cause:
                ctx.wrapped(gap.root_cause, theme.muted, indent="      ")
            for claim in gap.claims[:1]:
                if claim.quote:
                    ctx.wrapped(f"“{claim.quote}”", theme.dim, indent="      ")
            entry = filed_by_fingerprint.get(gap.fingerprint) or {}
            if entry.get("issue_number"):
                ctx.line(f"      issue #{entry['issue_number']} · seen ×{entry.get('occurrences', 1)}", theme.dim)
            else:
                ctx.line(f"      drafted · {gap.priority} priority", theme.dim)

    if review.config_suggestions:
        ctx.blank()
        ctx.heading("Fix in your configuration")
        for gap in review.config_suggestions:
            ctx.line(f"    • {gap.title}", theme.value)
            if gap.remedy:
                ctx.wrapped(f"→ {gap.remedy}", theme.muted, indent="      ")

    if not review.gaps and not review.config_suggestions:
        ctx.blank()
        ctx.wrapped("No gaps found — the report matched what the team said.", theme.muted)

    if review.warnings:
        ctx.blank()
        for warning in review.warnings[:3]:
            ctx.wrapped(f"- {warning}", theme.warn)


def build_standup_detail(ctx: _StandupCtx, key: str, data: dict) -> None:
    """Append the detail-view lines for one card into ctx."""
    if key == "my_update":
        _detail_member(ctx, data, data.get("my_name", ""))
        return
    if key.startswith("member:"):
        _detail_member(ctx, data, key[len("member:") :])
        return
    builder = {
        "summary": _detail_summary,
        "activity": _detail_activity,
        "gaps": _detail_gaps,
        "schedule": _detail_schedule,
        "notices": _detail_notices,
    }.get(key)
    if builder is None:
        ctx.line("Unknown section.", ctx.theme.muted)
        return
    builder(ctx, data)


# ---------------------------------------------------------------------------
# Overview builder
# ---------------------------------------------------------------------------


def build_standup_overview(ctx: _StandupCtx, data: dict, selected_card: int) -> None:
    """Append the overview lines: just the selectable section list.

    The headline stats (sprint/day/confidence) and transient message live in
    the pinned status strip + banner built by ``_build_standup_screen``, so
    the body starts straight at the cards.
    """
    theme = ctx.theme
    report = data.get("report")
    order = standup_card_order(data)
    for i, key in enumerate(order):
        ctx.card_rows.append(len(ctx.lines))
        selected = i == selected_card
        # no_wrap keeps every card row height-1 on narrow terminals — the
        # card_rows auto-scroll math counts rows, so a wrap would corrupt it.
        row = Text(PAD + "  ", justify="left", no_wrap=True, overflow="ellipsis")
        row.append("▸ " if selected else "  ", style=theme.accent if selected else theme.dim)
        title = standup_card_title(key, data)
        if key == "team":  # trailing chevron shows the inline expand state
            title += "  ▾" if data.get("team_expanded") else "  ▸"
        if key.startswith("member:"):
            _member_row(ctx, row, data, order, i, selected)
            continue
        row.append(f"{title:<{_TITLE_W}s}", style=f"bold {theme.accent}" if selected else theme.value)
        if key == "summary" and report is not None and report.team_summary:
            _summary_rows(ctx, row, report.team_summary, selected)
            continue
        teaser = standup_card_teaser(key, data)
        if teaser:
            row.append(f"  {teaser}", style=theme.muted if selected else theme.dim)
        ctx.add(row)


def _summary_rows(ctx: _StandupCtx, row: Text, text: str, selected: bool) -> None:
    """Emit the Team Summary card as up to two height-1 rows.

    The teaser wraps onto a continuation row aligned under the teaser column;
    anything beyond two rows is ellipsized (the detail view has the full text).
    """
    theme = ctx.theme
    chunks = textwrap.wrap(text, width=_TEASER_W) or [text]
    row.append(f"  {chunks[0]}", style=theme.muted if selected else theme.dim)
    ctx.add(row)
    if len(chunks) > 1:
        cont = chunks[1] + ("…" if len(chunks) > 2 else "")
        indent = PAD + "  " + "  " + " " * _TITLE_W + "  "  # marker + title columns
        style = theme.muted if selected else theme.dim
        ctx.add(Text(indent + cont, style=style, justify="left", no_wrap=True, overflow="ellipsis"))


def _member_row(ctx: _StandupCtx, row: Text, data: dict, order: list[str], i: int, selected: bool) -> None:
    """Emit one expanded Team sub-row: tree guide + activity glyph + name + gist.

    ``●`` marks members with attributed activity today, ``○`` quiet ones (their
    whole row dims so the active people pop), and ``✍`` rides along when they
    typed their own update.
    """
    theme = ctx.theme
    key = order[i]
    name = key[len("member:") :]
    m = _member(data, name)
    nxt = order[i + 1] if i + 1 < len(order) else ""
    guide = "├" if nxt.startswith("member:") else "└"
    active = _member_is_active(m) if m is not None else False
    wrote = bool(m.self_report) if m is not None else False
    row.append(f"  {guide} ", style=theme.dim)
    # Fixed 2-char glyph cell keeps the name column aligned with or without ✍.
    row.append("●" if active else "○", style=theme.good if active else theme.dim)
    row.append("✍" if wrote else " ", style=theme.accent_bright)
    row.append(" ")
    name_w = max(1, _TITLE_W - 7)  # guide "  ├ " (4) + glyph cell (2) + gap (1)
    quiet_style = theme.dim if not (active or wrote) else theme.value
    row.append(f"{name:<{name_w}s}", style=f"bold {theme.accent}" if selected else quiet_style)
    teaser = standup_card_teaser(key, data)
    if teaser:
        row.append(f"  {teaser}", style=theme.muted if selected else theme.dim)
    ctx.add(row)
