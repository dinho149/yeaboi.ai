"""Everything this product already knows about one engineer, gathered per person.

Performance used to reason from tracker tickets alone. Every other mode has been
quietly recording the rest: standup persists a per-member view of their code,
documentation and self-reported updates; analysis scores their practice hygiene;
retro and poker record what they wrote and how they estimated; reporting records
what shipped. This reads all of it back for one person.

Design mirrors ``poker/context.py`` — the codebase's existing cross-mode gatherer:
one graceful I/O entry point that NEVER raises (a missing DB, an empty table or a
broken store just means less evidence), plus pure deterministic helpers that are
trivially unit-testable. Saved stores are read first because they cost nothing;
the network is touched only to fill a stretch nothing has covered, and only when
the caller asks for it.

**Absence of evidence is not evidence of absence.** Every source reports its own
coverage, including the case where runs exist but none of them matched this
engineer's aliases — that is an attribution gap, not a quiet day. The prompt is
told the difference; without that a review can say someone wrote no tests when
the truth is nobody ever scanned a repository.

# See docs: "Session Management" — SQLite persistence
# See docs: "Prompt Construction" — ARC framework (optional context sections)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from yeaboi.agent.state import ActivityEvidence, EngineerActivity, EvidenceGroup, PerfMetric
from yeaboi.performance import identity

logger = logging.getLogger(__name__)

# Coverage vocabulary — the standup's, so one word means one thing app-wide.
COVERED = "covered"
PARTIAL = "partial"
FAILED = "failed"
NOT_CONFIGURED = "not_configured"

SOURCE_TICKETS = "tickets"
SOURCE_CODE = "code"
SOURCE_DOCUMENTATION = "documentation"
SOURCE_STANDUP = "standup"
SOURCE_ANALYSIS = "analysis"
SOURCE_RETRO = "retro"
SOURCE_POKER = "poker"
SOURCE_DELIVERY = "delivery"

# The two vocabularies above, as ordered tuples plus reader-facing labels. Two
# modes now draw the same coverage dot from the same word, and a hand-written
# copy in TypeScript is what would drift with nothing to notice — so these are
# codegen'd into frontend/src/types/enums.ts.
COVERAGE_STATES: tuple[str, ...] = (COVERED, PARTIAL, FAILED, NOT_CONFIGURED)

EVIDENCE_SOURCES: tuple[str, ...] = (
    SOURCE_TICKETS,
    SOURCE_CODE,
    SOURCE_DOCUMENTATION,
    SOURCE_STANDUP,
    SOURCE_ANALYSIS,
    SOURCE_RETRO,
    SOURCE_POKER,
    SOURCE_DELIVERY,
)

EVIDENCE_SOURCE_LABELS: dict[str, str] = {
    SOURCE_TICKETS: "Tickets",
    SOURCE_CODE: "Code",
    SOURCE_DOCUMENTATION: "Documentation",
    SOURCE_STANDUP: "Standup",
    SOURCE_ANALYSIS: "Team analysis",
    SOURCE_RETRO: "Retro",
    SOURCE_POKER: "Estimation",
    SOURCE_DELIVERY: "Delivery",
}

# What a measured number IS, never how to draw it. "" is a bare count.
STAT_UNITS: tuple[str, ...] = ("", "%", "pts", "d")

# Caps — a six-month window must not blow the prompt (poker/context.py:41-48 style).
_MAX_STANDUP_RUNS = 180  # runs read back; ~9 months of weekdays
_MAX_STANDUP_LINES = 12  # self-report / progress lines quoted
_MAX_PRACTICE_LINES = 8
_MAX_CODE_LINES = 12
_MAX_DOC_LINES = 8
_MAX_RETRO_RUNS = 12
_MAX_RETRO_LINES = 10
_MAX_POKER_RUNS = 20
_MAX_POKER_LINES = 8
_MAX_DELIVERY_LINES = 8
_MAX_LINE = 220  # per-line truncation of free text
_MAX_EVIDENCE_ROWS = 24  # structured rows kept per evidence group
_MAX_GAP_DAYS = 45  # hard ceiling on a live gap-fill scan


@dataclass(frozen=True)
class SourceCoverage:
    """What one source contributed, and — when it contributed nothing — why."""

    source: str = ""
    state: str = ""  # covered | partial | failed | not_configured
    detail: str = ""


@dataclass(frozen=True)
class EngineerEvidence:
    """Every attributable signal about one engineer over one period.

    Transient — never persisted. ``summary_md`` is the block the prompts read;
    ``coverage`` is what stops an unscanned source reading as an idle engineer.
    """

    engineer: str = ""
    aliases: tuple[str, ...] = ()
    period_start: str = ""
    period_end: str = ""
    activity: EngineerActivity = EngineerActivity()
    code_lines: tuple[str, ...] = ()
    documentation_lines: tuple[str, ...] = ()
    standup_lines: tuple[str, ...] = ()
    practice_lines: tuple[str, ...] = ()
    analysis_lines: tuple[str, ...] = ()
    retro_lines: tuple[str, ...] = ()
    poker_lines: tuple[str, ...] = ()
    delivery_lines: tuple[str, ...] = ()
    # The same facts the prose above carries, kept as numbers and as rows, so a
    # page can render a meter and a linked item instead of re-reading a sentence.
    metrics: tuple[PerfMetric, ...] = ()
    groups: tuple[EvidenceGroup, ...] = ()
    coverage: tuple[SourceCoverage, ...] = ()
    summary_md: str = ""

    @property
    def contributing_sources(self) -> tuple[str, ...]:
        """The sources that actually produced evidence, in coverage order."""
        return tuple(c.source for c in self.coverage if c.state in (COVERED, PARTIAL))

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.metrics,
                self.groups,
                self.activity.total_items,
                self.code_lines,
                self.documentation_lines,
                self.standup_lines,
                self.practice_lines,
                self.analysis_lines,
                self.retro_lines,
                self.poker_lines,
                self.delivery_lines,
            )
        )


# ---------------------------------------------------------------------------
# Pure helpers — no I/O, deterministic.
# ---------------------------------------------------------------------------


def _clip(text: str) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= _MAX_LINE else flat[: _MAX_LINE - 1] + "…"


def _in_period(value: str, start: str, end: str) -> bool:
    """Whether an ISO date/timestamp falls in [start, end]. Undated values pass."""
    day = str(value or "")[:10]
    if not day:
        return True
    if start and day < start:
        return False
    return not (end and day > end)


def _pct(num: float, den: float) -> str:
    return f"{round(num / den * 100)}%" if den else "n/a"


def _standup_lines(reports, aliases: frozenset[str]) -> dict[str, list[str]]:
    """Split one engineer's saved standup updates into per-category evidence.

    Returns the four line buckets plus ``matched`` (how many runs named them) and
    ``code_covered`` / ``docs_covered`` run counts, which drive coverage honesty.
    """
    out = {"standup": [], "code": [], "documentation": [], "practices": []}
    matched = 0
    code_covered = 0
    docs_covered = 0

    for report in reports:
        coverage = dict(getattr(report, "category_coverage", ()) or ())
        if coverage.get("code") in (COVERED, PARTIAL):
            code_covered += 1
        if coverage.get("documentation") in (COVERED, PARTIAL):
            docs_covered += 1

        for member in getattr(report, "member_updates", ()) or ():
            if not identity.matches(getattr(member, "name", ""), aliases):
                continue
            matched += 1
            date = getattr(report, "date", "")
            if member.self_report:
                out["standup"].append(f"{date} — their own update: {_clip(member.self_report)}")
            elif member.summary:
                out["standup"].append(f"{date} — {_clip(member.summary)}")
            if member.blockers:
                out["standup"].append(f"{date} — blocked: {_clip(member.blockers)}")
            if member.code_summary:
                out["code"].append(f"{date} — {_clip(member.code_summary)}")
            if member.documentation_summary:
                out["documentation"].append(f"{date} — {_clip(member.documentation_summary)}")
            for signal in getattr(member, "practices", ()) or ():
                repeat = " (recurring)" if getattr(signal, "repeat", False) else ""
                out["practices"].append(f"{date} — {signal.title}{repeat}: {_clip(signal.detail)}")

    return {
        **{k: v for k, v in out.items()},
        "matched": matched,
        "runs": len(reports),
        "code_covered": code_covered,
        "docs_covered": docs_covered,
    }


def _analysis_rows(examples: dict, aliases: frozenset[str]) -> tuple[dict, dict, dict]:
    """The three team-analysis rows that belong to this engineer.

    Matching happens here and only here, so the prose the prompt reads and the
    metrics the pages render are two projections of one extraction rather than
    two extractions that can disagree about a number.
    """
    examples = examples or {}
    blob = examples.get("ai_adoption") or {}

    def _first(rows, field: str) -> dict:
        for row in rows or ():
            if isinstance(row, dict) and identity.matches(str(row.get(field, "")), aliases):
                return row
        return {}

    return (
        _first(examples.get("contributor_stats"), "name"),
        _first((blob.get("member_practices") or {}).get("members"), "member"),
        _first(blob.get("member_activity"), "member"),
    )


_PRACTICE_RATES: tuple[tuple[str, str], ...] = (
    ("tests alongside production changes", "tests_rate"),
    ("docs touched", "docs_rate"),
    ("changes referencing a ticket", "ticket_rate"),
    ("PRs with a meaningful description", "desc_rate"),
)


def _analysis_lines(stats: dict, practices: dict, activity: dict) -> list[str]:
    """Delivery stats, practice-hygiene rates and AI markers, as prompt prose."""
    lines: list[str] = []

    if stats:
        bits = [
            f"{stats.get('stories_completed', 0)} of {stats.get('stories_total', 0)} stories completed",
            f"{stats.get('delivery_pts', 0)} points delivered",
        ]
        if stats.get("spill_rate") is not None:
            bits.append(f"{stats['spill_rate']}% spill rate")
        if stats.get("avg_cycle_time"):
            bits.append(f"{stats['avg_cycle_time']}d average cycle time")
        if stats.get("top_discipline"):
            bits.append(f"mostly {stats['top_discipline']}")
        lines.append("Delivery (analysis): " + ", ".join(str(b) for b in bits) + ".")

    if practices:
        rates = [f"{label} {practices[key]}%" for label, key in _PRACTICE_RATES if practices.get(key) is not None]
        volume = f"{practices.get('commits', 0)} commits, {practices.get('prs', 0)} PRs"
        lines.append(
            f"Practice hygiene (analysis, over {volume}): " + ("; ".join(rates) if rates else "no rate had a sample")
        )

    if activity.get("ai_marked"):
        lines.append(
            f"AI-tool markers (analysis, a lower bound): {activity['ai_marked']} of "
            f"{activity.get('commits', 0) + activity.get('prs', 0)} changes carried one."
        )

    return lines


def _analysis_metrics(stats: dict, practices: dict, activity: dict) -> list[PerfMetric]:
    """The same three rows, as numbers.

    A rate with no sample is left out entirely rather than recorded as 0 — an
    engineer whose spill rate was never measured has not spilled 0%.
    """
    out: list[PerfMetric] = []

    def add(key: str, label: str, value, *, denominator=0, unit: str = "", group: str = "", detail: str = "") -> None:
        if value is None:
            return
        try:
            out.append(
                PerfMetric(
                    key=key,
                    label=label,
                    value=float(value),
                    denominator=float(denominator or 0),
                    unit=unit,
                    group=group,
                    source=SOURCE_ANALYSIS,
                    detail=detail,
                )
            )
        except (TypeError, ValueError):
            return

    if stats:
        add(
            "stories_completed",
            "Stories completed",
            stats.get("stories_completed"),
            denominator=stats.get("stories_total"),
            group="delivery",
            detail=f"Mostly {stats['top_discipline']} work." if stats.get("top_discipline") else "",
        )
        add("delivery_points", "Points delivered", stats.get("delivery_pts"), unit="pts", group="delivery")
        add("spill_rate", "Spill rate", stats.get("spill_rate"), unit="%", group="delivery")
        add("avg_cycle_time", "Average cycle time", stats.get("avg_cycle_time"), unit="d", group="delivery")

    if practices:
        for label, key in _PRACTICE_RATES:
            add(key, label[:1].upper() + label[1:], practices.get(key), unit="%", group="practice")
        add("commits", "Commits", practices.get("commits"), group="volume")
        add("prs", "Pull requests", practices.get("prs"), group="volume")

    if activity.get("ai_marked"):
        add(
            "ai_marked",
            "Changes carrying an AI marker",
            activity.get("ai_marked"),
            denominator=activity.get("commits", 0) + activity.get("prs", 0),
            group="practice",
            detail="A lower bound — only changes that announce the tool are counted.",
        )

    return out


def _retro_lines(reports, aliases: frozenset[str]) -> tuple[list[str], int, int]:
    """One engineer's retro cards by grid, plus (participated, total) run counts."""
    from yeaboi.retro.board import CARRIED_STATUS_LABELS, RETRO_GRID_LABELS

    lines: list[str] = []
    participated = 0
    for report in reports:
        took_part = any(identity.matches(name, aliases) for name in getattr(report, "participants", ()) or ())
        if took_part:
            participated += 1
        date = getattr(report, "date", "")
        for card in getattr(report, "cards", ()) or ():
            if getattr(card, "origin", "") != "web" or not identity.matches(getattr(card, "author", ""), aliases):
                continue
            grid = RETRO_GRID_LABELS.get(card.grid, card.grid or "card")
            lines.append(f"{date} — {grid}: {_clip(card.text)}")
        for card in getattr(report, "carried_action_items", ()) or ():
            if not identity.matches(getattr(card, "author", ""), aliases):
                continue
            status = CARRIED_STATUS_LABELS.get(card.status, card.status or "pending")
            lines.append(f"{date} — their action item from the previous retro is {status}: {_clip(card.text)}")
    return lines, participated, len(reports)


def _poker_lines(reports, aliases: frozenset[str]) -> tuple[list[str], int, int, int]:
    """Estimation behaviour: their vote against where the team landed.

    Returns (lines, tickets_voted_on, sessions, sessions_attended). The last two
    are a pair — tickets and sessions are different units, and dividing one by
    the other is how "voted on 40 tickets" became "1 of 20 sessions".
    """
    lines: list[str] = []
    voted = 0
    agreed = 0
    attended = 0
    outliers: list[str] = []

    for report in reports:
        date = getattr(report, "date", "")
        voted_here = voted
        for ticket in getattr(report, "tickets", ()) or ():
            mine = next(
                (v for v in getattr(ticket, "votes", ()) or () if identity.matches(getattr(v, "voter", ""), aliases)),
                None,
            )
            if mine is None:
                continue
            voted += 1
            final = ticket.final_points
            try:
                value = float(mine.value)
            except (TypeError, ValueError):
                continue  # "?" / "☕" carry no estimate
            if final is None:
                continue
            if abs(value - final) < 0.01:
                agreed += 1
            elif abs(value - final) >= max(2.0, final):
                direction = "high" if value > final else "low"
                outliers.append(
                    f"{date} — {ticket.key}: estimated {mine.value}, team settled on {final:g} ({direction})"
                )
        if voted > voted_here:
            attended += 1
        for ticket in getattr(report, "tickets", ()) or ():
            for field in ("duel_low", "duel_high"):
                position = getattr(ticket, field, "")
                name = position.split("(")[0].strip() if position else ""
                if name and identity.matches(name, aliases):
                    side = "low" if field == "duel_low" else "high"
                    lines.append(f"{date} — argued the {side} side of the estimate debate on {ticket.key}: {position}")

    if voted:
        lines.insert(
            0, f"Estimated on {voted} ticket(s); matched the team's final points {_pct(agreed, voted)} of the time."
        )
    lines.extend(outliers)
    return lines, voted, len(reports), attended


def _activity_group(activity: EngineerActivity) -> EvidenceGroup:
    """The engineer's tickets as structured rows, not a sentence about them.

    Reuses ``ActivityEvidence`` — the shape standup already stores and the
    browser already knows how to draw — so this costs a projection rather than a
    second evidence vocabulary.
    """
    items = tuple(
        ActivityEvidence(
            kind=story.kind or "issue",
            key=story.key,
            title=story.title,
            status=story.status,
            repository=story.sprint,
        )
        for story in activity.stories[:_MAX_EVIDENCE_ROWS]
    )
    extra = len(activity.stories) - len(items)
    return EvidenceGroup(
        source=SOURCE_TICKETS,
        label="Tickets worked",
        items=items,
        note=f"capped at {len(items)} of {len(activity.stories)}" if extra > 0 else "",
    )


def _delivery_group(shipped: list) -> EvidenceGroup:
    """Shipped tickets credited to this engineer, as structured rows."""
    items = tuple(
        ActivityEvidence(
            kind="issue",
            key=str(getattr(item, "key", "")),
            title=str(getattr(item, "title", "")),
            status=str(getattr(item, "status", "")),
            url=str(getattr(item, "url", "")),
        )
        for item in shipped[:_MAX_EVIDENCE_ROWS]
    )
    extra = len(shipped) - len(items)
    return EvidenceGroup(
        source=SOURCE_DELIVERY,
        label="Shipped this period",
        items=items,
        note=f"capped at {len(items)} of {len(shipped)}" if extra > 0 else "",
    )


def _delivery_lines(items, aliases: frozenset[str]) -> list[str]:
    """Shipped tickets credited to this engineer (reporting mode)."""
    return [
        f"{item.key} {_clip(item.title)} ({item.status})"
        for item in items or ()
        if identity.matches(getattr(item, "assignee", ""), aliases)
    ]


def _coverage_detail(state: str, source: str, matched: int, total: int) -> str:
    """One honest sentence about what a source did or did not deliver."""
    if state == NOT_CONFIGURED:
        return f"No {source} history in this period."
    if state == PARTIAL and not matched and total:
        return (
            f"{total} {source} run(s) in the period, none of which named this engineer "
            "— an attribution gap, not an idle period."
        )
    return f"{matched} of {total} {source} run(s) named this engineer."


def format_evidence_md(ev: EngineerEvidence) -> str:
    """Render the gathered evidence as the prompt's context block."""
    sections: list[tuple[str, tuple[str, ...]]] = [
        ("Their own standup updates, progress and blockers", ev.standup_lines),
        ("Code activity", ev.code_lines),
        ("Documentation activity", ev.documentation_lines),
        ("Engineering-practice observations (deterministic, not AI-generated)", ev.practice_lines),
        ("Delivery and practice metrics (team analysis)", ev.analysis_lines),
        ("Shipped work (delivery reporting)", ev.delivery_lines),
        ("Retrospectives", ev.retro_lines),
        ("Estimation (planning poker)", ev.poker_lines),
    ]
    parts = [f"**{title}:**\n" + "\n".join(f"  - {line}" for line in lines) for title, lines in sections if lines]
    return "\n\n".join(parts)


def format_coverage_md(ev: EngineerEvidence) -> str:
    """Render what was scanned and what was not — the anti-inference block."""
    if not ev.coverage:
        return ""
    rows = "\n".join(f"  - {c.source}: {c.state} — {c.detail}" for c in ev.coverage)
    return "**Evidence coverage for this period:**\n" + rows


# ---------------------------------------------------------------------------
# The gatherer — graceful I/O, one try/except per source.
# ---------------------------------------------------------------------------


def gather_engineer_evidence(
    engineer: str,
    *,
    period_start: str = "",
    period_end: str = "",
    state: dict | None = None,
    jira_project: str = "",
    azdo_project: str = "",
    sprints: int = 2,
    deep_scan: bool = False,
    db_path=None,
) -> EngineerEvidence:
    """Read every mode's history for one engineer. Never raises.

    Saved stores are read first (free); ``deep_scan`` additionally permits one
    capped live multi-source collection over the stretch no saved standup covered.
    """
    state = state or {}
    logger.info(
        "gather_engineer_evidence: engineer=%s period=%s..%s deep_scan=%s",
        engineer,
        period_start,
        period_end,
        deep_scan,
    )

    from yeaboi.performance import activity as activity_mod

    aliases = identity.resolve_aliases(
        engineer,
        extra=identity.roster_handles(engineer, jira_project=jira_project, azdo_project=azdo_project, db_path=db_path),
    )
    coverage: list[SourceCoverage] = []

    # ── Tickets — the existing gatherer, now alias-aware and deduped. ────────
    activity = activity_mod.gather_engineer_activity(
        engineer,
        state=state,
        jira_project=jira_project,
        azdo_project=azdo_project,
        sprints=sprints,
        aliases=aliases,
    )
    coverage.append(
        SourceCoverage(
            SOURCE_TICKETS,
            COVERED if activity.total_items else (PARTIAL if activity.sources else NOT_CONFIGURED),
            f"{activity.total_items} ticket(s) from " + (", ".join(s for s, _ in activity.sources) or "no tracker"),
        )
    )

    # Built before the no-database exit below: the tickets came from the tracker,
    # not from saved history, so they are evidence on a machine that has never
    # run a standup too. Deriving them after that exit is what made the same
    # tickets produce a metric on one machine and only a coverage row on another.
    metrics: list[PerfMetric] = []
    groups: list[EvidenceGroup] = []
    if activity.total_items:
        metrics.append(
            PerfMetric(
                key="tickets_total",
                label="Tickets worked",
                value=float(activity.total_items),
                group="volume",
                source=SOURCE_TICKETS,
            )
        )
        groups.append(_activity_group(activity))

    db_path = _resolve_db(db_path)
    if db_path is None:
        ev = EngineerEvidence(
            engineer=engineer,
            aliases=tuple(sorted(aliases)),
            period_start=period_start,
            period_end=period_end,
            activity=activity,
            metrics=tuple(metrics),
            groups=tuple(groups),
            coverage=tuple(coverage),
        )
        return replace(ev, summary_md=format_evidence_md(ev))

    standup_lines: list[str] = []
    code_lines: list[str] = []
    doc_lines: list[str] = []
    practice_lines: list[str] = []
    analysis_lines: list[str] = []
    retro_lines: list[str] = []
    poker_lines: list[str] = []
    delivery_lines: list[str] = []
    standup_stats: dict = {}

    # ── Standup — per-member code, docs, self-reports, blockers, practices. ──
    try:
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            reports = [
                r
                for r in store.get_recent_reports(_MAX_STANDUP_RUNS)
                if _in_period(getattr(r, "date", ""), period_start, period_end)
            ]
        standup_stats = _standup_lines(reports, aliases)
        standup_lines = standup_stats["standup"][:_MAX_STANDUP_LINES]
        code_lines = standup_stats["code"][:_MAX_CODE_LINES]
        doc_lines = standup_stats["documentation"][:_MAX_DOC_LINES]
        practice_lines = standup_stats["practices"][:_MAX_PRACTICE_LINES]
    except Exception:  # noqa: BLE001 — evidence is best-effort; never abort the run
        logger.debug("performance evidence: standup read failed (non-fatal)", exc_info=True)
        coverage.append(SourceCoverage(SOURCE_STANDUP, FAILED, "The standup history could not be read."))

    if standup_stats:
        runs = standup_stats["runs"]
        matched = standup_stats["matched"]
        state_ = COVERED if matched else (PARTIAL if runs else NOT_CONFIGURED)
        coverage.append(SourceCoverage(SOURCE_STANDUP, state_, _coverage_detail(state_, "standup", matched, runs)))
        coverage.append(_category_coverage(SOURCE_CODE, standup_stats["code_covered"], runs, bool(code_lines)))
        coverage.append(_category_coverage(SOURCE_DOCUMENTATION, standup_stats["docs_covered"], runs, bool(doc_lines)))

    # ── Analysis — delivery stats + practice hygiene + AI markers. ───────────
    try:
        analysis_lines, analysis_metrics = _read_analysis(db_path, aliases, jira_project, azdo_project)
        metrics.extend(analysis_metrics)
        coverage.append(
            SourceCoverage(
                SOURCE_ANALYSIS,
                COVERED if analysis_lines else NOT_CONFIGURED,
                "Team analysis metrics for this engineer."
                if analysis_lines
                else "No saved team analysis covers this engineer.",
            )
        )
    except Exception:  # noqa: BLE001
        logger.debug("performance evidence: analysis read failed (non-fatal)", exc_info=True)
        coverage.append(SourceCoverage(SOURCE_ANALYSIS, FAILED, "The team analysis profile could not be read."))

    # ── Retro — their cards, their action items, their attendance. ───────────
    try:
        from yeaboi.retro.store import RetroStore

        with RetroStore(db_path) as store:
            reports = [
                r
                for r in store.get_recent_reports(_MAX_RETRO_RUNS, str(state.get("project_name", "")))
                if _in_period(getattr(r, "date", ""), period_start, period_end)
            ]
        lines, participated, total = _retro_lines(reports, aliases)
        retro_lines = lines[:_MAX_RETRO_LINES]
        if participated and total:
            retro_lines.insert(0, f"Took part in {participated} of {total} retro(s) in this period.")
        if total:
            metrics.append(
                PerfMetric(
                    key="retros_attended",
                    label="Retros attended",
                    value=float(participated),
                    denominator=float(total),
                    group="ceremony",
                    source=SOURCE_RETRO,
                )
            )
        state_ = COVERED if participated else (PARTIAL if total else NOT_CONFIGURED)
        coverage.append(SourceCoverage(SOURCE_RETRO, state_, _coverage_detail(state_, "retro", participated, total)))
    except Exception:  # noqa: BLE001
        logger.debug("performance evidence: retro read failed (non-fatal)", exc_info=True)
        coverage.append(SourceCoverage(SOURCE_RETRO, FAILED, "The retro history could not be read."))

    # ── Poker — how their estimates track where the team lands. ──────────────
    try:
        from yeaboi.poker.store import PokerStore

        with PokerStore(db_path) as store:
            rows = store.get_all_history(_MAX_POKER_RUNS)
            reports = []
            for row in rows:
                if not _in_period(str(row.get("poker_date", "")), period_start, period_end):
                    continue
                report = store.get_run_by_id(int(row.get("id", 0)))
                if report is not None:
                    reports.append(report)
        lines, voted, total, attended = _poker_lines(reports, aliases)
        poker_lines = lines[:_MAX_POKER_LINES]
        if total:
            metrics.append(
                PerfMetric(
                    key="poker_sessions",
                    label="Estimation sessions joined",
                    value=float(attended),
                    denominator=float(total),
                    group="ceremony",
                    source=SOURCE_POKER,
                    detail=f"Voted on {voted} ticket(s).",
                )
            )
        state_ = COVERED if voted else (PARTIAL if total else NOT_CONFIGURED)
        coverage.append(
            SourceCoverage(
                SOURCE_POKER,
                state_,
                f"Voted on {voted} ticket(s) across {total} session(s)."
                if voted
                else _coverage_detail(state_, "poker", 0, total),
            )
        )
    except Exception:  # noqa: BLE001
        logger.debug("performance evidence: poker read failed (non-fatal)", exc_info=True)
        coverage.append(SourceCoverage(SOURCE_POKER, FAILED, "The poker history could not be read."))

    # ── Reporting — what actually shipped under their name. ──────────────────
    try:
        from yeaboi.reporting.store import ReportingStore

        with ReportingStore(db_path) as store:
            report = store.get_latest_report()
        delivered = getattr(report, "delivered_items", ()) or ()
        shipped = [i for i in delivered if identity.matches(getattr(i, "assignee", ""), aliases)]
        delivery_lines = _delivery_lines(delivered, aliases)[:_MAX_DELIVERY_LINES]
        if shipped:
            metrics.append(
                PerfMetric(
                    key="shipped_items",
                    label="Items shipped",
                    value=float(len(shipped)),
                    group="delivery",
                    source=SOURCE_DELIVERY,
                )
            )
            groups.append(_delivery_group(shipped))
        coverage.append(
            SourceCoverage(
                SOURCE_DELIVERY,
                COVERED if shipped else NOT_CONFIGURED,
                # ``shipped``, not the display list: ``delivery_lines`` is capped
                # for the prompt, and counting the cap is how a tile saying 23
                # ended up beside a sentence saying 8.
                f"{len(shipped)} shipped item(s) credited to this engineer."
                if shipped
                else "No delivery report credits this engineer.",
            )
        )
    except Exception:  # noqa: BLE001
        logger.debug("performance evidence: reporting read failed (non-fatal)", exc_info=True)
        coverage.append(SourceCoverage(SOURCE_DELIVERY, FAILED, "The delivery report could not be read."))

    # ── Live gap-fill — only when asked, only over what nothing covered. ─────
    if deep_scan:
        extra_code, extra_docs, gap_note, gap_outcome = _gap_fill(
            aliases, db_path=db_path, period_start=period_start, period_end=period_end
        )
        code_lines = (code_lines + extra_code)[:_MAX_CODE_LINES]
        doc_lines = (doc_lines + extra_docs)[:_MAX_DOC_LINES]
        if gap_note:
            coverage = _amend_coverage(coverage, SOURCE_CODE, gap_note, gap_outcome, bool(code_lines))

    if standup_stats.get("runs"):
        metrics.append(
            PerfMetric(
                key="standup_runs",
                label="Standups that named them",
                value=float(standup_stats["matched"]),
                denominator=float(standup_stats["runs"]),
                group="ceremony",
                source=SOURCE_STANDUP,
            )
        )
    ev = EngineerEvidence(
        engineer=engineer,
        aliases=tuple(sorted(aliases)),
        period_start=period_start,
        period_end=period_end,
        activity=activity,
        code_lines=tuple(code_lines),
        documentation_lines=tuple(doc_lines),
        standup_lines=tuple(standup_lines),
        practice_lines=tuple(practice_lines),
        analysis_lines=tuple(analysis_lines),
        retro_lines=tuple(retro_lines),
        poker_lines=tuple(poker_lines),
        delivery_lines=tuple(delivery_lines),
        metrics=tuple(metrics),
        groups=tuple(groups),
        coverage=tuple(coverage),
    )
    ev = replace(ev, summary_md=format_evidence_md(ev))
    logger.info(
        "performance evidence: engineer=%s sources=%s tickets=%d standup=%d code=%d docs=%d "
        "practices=%d analysis=%d retro=%d poker=%d delivery=%d",
        engineer,
        ",".join(ev.contributing_sources) or "none",
        ev.activity.total_items,
        len(ev.standup_lines),
        len(ev.code_lines),
        len(ev.documentation_lines),
        len(ev.practice_lines),
        len(ev.analysis_lines),
        len(ev.retro_lines),
        len(ev.poker_lines),
        len(ev.delivery_lines),
    )
    return ev


def _amend_coverage(
    rows: list[SourceCoverage], source: str, note: str, outcome: str, has_lines: bool
) -> list[SourceCoverage]:
    """Fold a second pass over one source into that source's existing row.

    One source, one row. Appending a second is what made ``contributing_sources``
    repeat a word, gave the browser duplicate keys, and drew two chips with
    different states for the same thing.

    The merged state is the honest one: a failed extra scan is only ``failed``
    when nothing else covered the source — when saved history did, the source is
    ``partial`` (we have some, we tried for more and could not get it).
    """
    if outcome == "failed":
        state = FAILED if not has_lines else PARTIAL
    elif outcome == "ok":
        state = COVERED if has_lines else ""
    else:
        state = ""  # skipped: the extra pass says nothing about the source

    out: list[SourceCoverage] = []
    merged = False
    for row in rows:
        if row.source != source or merged:
            out.append(row)
            continue
        detail = f"{row.detail.rstrip()} {note}".strip() if row.detail else note
        out.append(SourceCoverage(source, state or row.state, detail))
        merged = True
    if not merged:
        out.append(SourceCoverage(source, state or NOT_CONFIGURED, note))
    return out


def _category_coverage(source: str, covered_runs: int, total_runs: int, has_lines: bool) -> SourceCoverage:
    """Coverage for a standup-derived category, distinguishing quiet from unscanned."""
    if not total_runs or not covered_runs:
        return SourceCoverage(
            source,
            NOT_CONFIGURED,
            f"No standup run in this period scanned {source}; nothing is known about it either way.",
        )
    detail = f"Scanned by {covered_runs} of {total_runs} standup run(s)."
    if not has_lines:
        detail += f" No {source} activity was attributed to this engineer in those runs."
    return SourceCoverage(source, COVERED if has_lines else PARTIAL, detail)


def _resolve_db(db_path):
    """The sessions DB, or None when there is nothing to read."""
    try:
        if db_path is None:
            from yeaboi.config import get_sessions_db

            db_path = get_sessions_db()
        return db_path if getattr(db_path, "exists", lambda: True)() else None
    except Exception:  # noqa: BLE001
        logger.debug("performance evidence: db path unavailable (non-fatal)", exc_info=True)
        return None


def _read_analysis(
    db_path, aliases: frozenset[str], jira_project: str, azdo_project: str
) -> tuple[list[str], list[PerfMetric]]:
    """Per-member analysis signals from the newest profile that carries them.

    Returns the prompt's prose and the same facts as numbers — both projected
    from one row match, so they cannot disagree.
    """
    from yeaboi.team_profile import TeamProfileStore

    with TeamProfileStore(db_path) as store:
        profiles = []
        for project, source in ((jira_project, "jira"), (azdo_project, "azdevops")):
            if project:
                found = store.load_by_project(project, source)
                if found is not None:
                    profiles.append(found)
        if not profiles:
            profiles = store.list_profiles()
        for profile in profiles:
            _, examples = store.load_with_examples(profile.team_id)
            rows = _analysis_rows(examples or {}, aliases)
            lines = _analysis_lines(*rows)
            if lines:
                return lines, _analysis_metrics(*rows)
    return [], []


def _gap_fill(aliases: frozenset[str], *, db_path, period_start: str, period_end: str):
    """One capped live multi-source scan, reusing the standup's saved scope.

    The lead configures sources once, in standup; this borrows that scope rather
    than asking again. Returns (code_lines, doc_lines, note, outcome), where
    outcome is "ok" | "skipped" | "failed" — the caller needs the difference to
    choose a coverage word, and a note alone cannot carry it.
    """
    from datetime import datetime, timedelta, timezone

    try:
        from yeaboi.standup.store import StandupStore

        with StandupStore(db_path) as store:
            session_id = store.get_latest_configured_session()
            config = store.load_config(session_id) if session_id else None
        if not config:
            return [], [], "No saved standup scope to scan with; the live gap-fill was skipped.", "skipped"

        since = datetime.now(timezone.utc) - timedelta(days=_MAX_GAP_DAYS)
        from yeaboi.standup import categories, collector
        from yeaboi.standup.engine import _group_activity_by_author

        bundle = collector.collect_recent_activity(
            since=since,
            jira_project=config.get("jira_project", "") or "",
            azdo_project=config.get("azdo_project", "") or "",
            github_owners=config.get("github_owners") or None,
            github_repositories=config.get("github_repositories") or None,
            github_excluded_repositories=config.get("github_excluded_repositories") or None,
            azdo_projects=config.get("azdo_projects") or None,
            azdo_repositories=config.get("azdo_repositories") or None,
            ticket_context=False,
        )
        grouped = _group_activity_by_author(bundle.items, ["_engineer"], {"_engineer": set(aliases)})
        split = categories.split_activity(grouped.get("_engineer", []))
        code = [
            f"{str(i.get('timestamp', ''))[:10]} — {_clip(i.get('title', ''))} ({i.get('repository', '')})"
            for i in split.get(categories.CATEGORY_CODE, [])
        ]
        docs = [
            f"{str(i.get('timestamp', ''))[:10]} — {_clip(i.get('title', ''))}"
            for i in split.get(categories.CATEGORY_DOCUMENTATION, [])
        ]
        note = (
            f"Live scan of the last {_MAX_GAP_DAYS} day(s) added "
            f"{len(code)} code and {len(docs)} documentation item(s)."
        )
        return code, docs, note, "ok"
    except Exception:  # noqa: BLE001 — a gap-fill failure must not fail the run
        logger.debug("performance evidence: gap-fill scan failed (non-fatal)", exc_info=True)
        return [], [], "The live gap-fill scan failed; only saved history was used.", "failed"
