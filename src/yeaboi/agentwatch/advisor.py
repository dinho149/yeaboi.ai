"""Agent Advisor engine — recoverable spend + cache health over local sessions.

The advisor pipeline: where usage answers "what did the agents
cost", the advisor answers "how much of that was avoidable, and why". Same
standalone shape as the rest of the family (engine.py): one deterministic
gather, a single LLM call for prose, parse → fallback → format, and a run that
never raises.

Deterministic half:
  1. refresh the collector's ingest (shared cursors — a usage run just before
     this one makes the scan free),
  2. audit the window's transcripts for Read waste (waste_audit.py — vendored
     from Headroom's audit tooling; every figure is UTF-8 bytes of tool_result
     content, tokens ≈ bytes/4),
  3. price the waste at the window's *blended* input rate — each model's
     input-token share weights its own rate, so a Haiku-heavy window prices
     its waste cheaper than an Opus-heavy one,
  4. scan the prompt-prefix files (CLAUDE.md and friends) for volatile-shaped
     content (cache_signals.py) — an indicator the cache prefix churns.

Deliberately NOT served by the Go sidecar: this pipeline is additive Python
work the sidecar has no twin for (CLAUDE.md's dual-maintenance exemption). It
must not be added to engine.py's mirrored surface without also landing the Go
twin.

The privacy invariant holds: transcripts and prefix files are read on this
machine and only counts, byte totals and file *paths* reach the artifact, the
store, and the export.

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the agentwatch prompts
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from yeaboi.agent.state import AgentAdvisorReport, VolatileFileSignal, WasteLineItem
from yeaboi.agentwatch import cache_signals, collector, waste_audit
from yeaboi.agentwatch.engine import (
    _emit,
    _invoke_llm,
    _resolve_db_path,
    _session_cost,
    _str_list,
)
from yeaboi.agentwatch.store import AgentWatchStore
from yeaboi.pricing import PRICING_AS_OF, lookup_price

logger = logging.getLogger(__name__)

# Bytes-to-tokens divisor — the same ≈4 bytes/token rule of thumb the audit's
# byte figures are documented against. An estimate, labelled as one everywhere.
_BYTES_PER_TOKEN = 4

# Prefix-file scan bounds: enough for one home config + a window of projects,
# small enough that a pathological project list cannot turn the scan into a
# filesystem crawl. A capped scan under-reports, which is the honest direction.
_MAX_PREFIX_FILES = 24
_MAX_PREFIX_BYTES = 256 * 1024

# (mechanism, label, recoverable, note) for each audited waste class, in
# render order. `recoverable` decides whether the line sums into the headline:
# stale re-reads are real bytes but reclaiming them needs staleness-aware
# context handling, so they are sized as context, never promised as savings.
_LINE_ITEM_SPECS: tuple[tuple[str, str, bool, str], ...] = (
    ("identical-repeat", "Identical re-reads", True, "the same file read again, byte-identical"),
    ("subset-containment", "Subset re-reads", True, "a partial read already contained in an earlier full read"),
    ("write-readback", "Write read-backs", True, "a read echoing content the agent had just written"),
    ("line-number-overhead", "Line-number scaffolding", True, "cat -n prefix bytes inside Read output"),
    ("stale-reread", "Stale reads (edited after)", False, "reads later invalidated by an edit — context, not savings"),
)


def _blended_input_rate(sessions: list[dict]) -> tuple[float, float]:
    """The window's input-token-weighted $/Mtok, plus the unknown-rate share.

    Waste bytes are input-context bytes, so they are priced at what this
    window actually paid per input token. Weighting by each model's input
    tokens keeps the estimate honest across mixed-model windows; the unknown
    share travels with it so a fallback-priced window is flagged, not silent.
    """
    weighted = 0.0
    total = 0
    unknown = 0
    for session in sessions:
        for model, usage in session["model_usage"].items():
            tokens = int(usage.get("input", 0))
            if tokens <= 0:
                continue
            price, matched = lookup_price(model)
            weighted += tokens * price.input_per_mtok
            total += tokens
            if not matched:
                unknown += tokens
    if total == 0:
        # No input traffic to weight — the whole audit prices at the table's
        # fallback tier, and that must be FLAGGED, not silent: with sessions in
        # the window this is the least-trustworthy pricing case, not a clean
        # one. An empty window flags nothing because nothing was priced.
        return lookup_price("")[0].input_per_mtok, 1.0 if sessions else 0.0
    return weighted / total, unknown / total


def _line_items(report: waste_audit.ReadAuditReport, rate_per_mtok: float) -> tuple[WasteLineItem, ...]:
    """The audit's per-mechanism figures as priced artifact rows."""
    by_mechanism = {
        "identical-repeat": (report.dedup_identical_calls, report.dedup_identical_bytes),
        "subset-containment": (report.subset_calls, report.subset_bytes),
        "write-readback": (report.write_readback_calls, report.write_readback_bytes),
        "line-number-overhead": (0, report.linenum_overhead_bytes),
        "stale-reread": (report.stale_calls, report.stale_bytes),
    }
    read_bytes = report.read_bytes or 1
    rows = []
    for mechanism, label, recoverable, note in _LINE_ITEM_SPECS:
        calls, content_bytes = by_mechanism[mechanism]
        est_tokens = content_bytes // _BYTES_PER_TOKEN
        rows.append(
            WasteLineItem(
                mechanism=mechanism,
                label=label,
                calls=calls,
                content_bytes=content_bytes,
                est_tokens=est_tokens,
                est_usd=round(est_tokens * rate_per_mtok / 1_000_000, 4),
                share_of_read_bytes=round(content_bytes / read_bytes, 4),
                recoverable=recoverable,
                note=note,
            )
        )
    return tuple(rows)


def _prefix_files(sessions: list[dict], *, home: Path | None = None) -> list[Path]:
    """The prompt-prefix files worth scanning for this window, deduped + capped.

    The home CLAUDE.md plus each windowed project's CLAUDE.md variants — the
    files an agent loads into its prompt prefix on every session, where
    volatile content translates directly into cache-prefix churn.

    This reads OUTSIDE the fs_policy builtin roots (~/.claude): a project's
    CLAUDE.md lives in the user's own repository. Deliberate and read-only —
    the projects come from the user's own session history, only counts leave
    the scan, and like the collector this path never writes.
    """
    resolved_home = home or Path.home()
    candidates: list[Path] = [resolved_home / ".claude" / "CLAUDE.md"]
    seen_projects: set[str] = set()
    for session in sessions:
        project = session.get("project_path") or ""
        if not project or project in seen_projects:
            continue
        seen_projects.add(project)
        candidates.append(Path(project) / "CLAUDE.md")
        candidates.append(Path(project) / ".claude" / "CLAUDE.md")
    out: list[Path] = []
    seen_paths: set[Path] = set()
    for path in candidates:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            if path.is_file():
                out.append(path)
        except OSError:
            continue
        if len(out) >= _MAX_PREFIX_FILES:
            break
    return out


def _scan_prefix_files(paths: list[Path]) -> tuple[tuple[VolatileFileSignal, ...], int]:
    """Volatile-content counts per prefix file, plus the 0-100 alignment score.

    Only files with at least one finding become signal rows — a clean file is
    the normal case, not a row. Counts only ever leave the scan; content never
    does (cache_signals.count_volatile returns labels and counts, no samples).
    """
    signals: list[VolatileFileSignal] = []
    for path in paths:
        try:
            text = path.read_bytes()[:_MAX_PREFIX_BYTES].decode("utf-8", errors="replace")
        except OSError:
            continue
        counts = cache_signals.count_volatile(text)
        total = sum(counts.values())
        if not total:
            continue
        signals.append(
            VolatileFileSignal(
                location=str(path),
                counts=tuple((label, str(n)) for label, n in sorted(counts.items())),
                total=total,
            )
        )
    signals.sort(key=lambda s: s.total, reverse=True)
    return tuple(signals), cache_signals.alignment_score([s.total for s in signals])


def _fallback_advisor_prose(report: AgentAdvisorReport) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Deterministic insights when the LLM is unavailable — evidence, not analysis."""
    insights: list[str] = []
    recoverable = [i for i in report.line_items if i.recoverable and i.content_bytes]
    if recoverable:
        top = max(recoverable, key=lambda i: i.content_bytes)
        insights.append(
            f"Largest recoverable class: {top.label.lower()} — "
            f"{top.share_of_read_bytes:.0%} of Read bytes (~${top.est_usd:,.2f})."
        )
    if report.recoverable_usd and report.total_cost_usd:
        insights.append(
            f"~${report.recoverable_usd:,.2f} of the window's ${report.total_cost_usd:,.2f} "
            f"looks mechanically recoverable ({report.recoverable_share:.0%})."
        )
    if report.volatile_signals:
        worst = report.volatile_signals[0]
        insights.append(
            f"{len(report.volatile_signals)} prompt-prefix file(s) carry volatile-shaped content "
            f"(worst: {Path(worst.location).name}, {worst.total} finding(s)) — cache prefix likely churns."
        )
    return tuple(insights[:4]), ()


def run_agent_advisor(
    *,
    window_days: int = 30,
    project_path: str = "",
    db_path=None,
    today: date | None = None,
    on_progress=None,
    dry_run: bool = False,
) -> AgentAdvisorReport:
    """Audit the window's agent sessions for recoverable spend + cache health.

    Deterministic gather: refresh the collector, audit the window's transcript
    files for Read waste, price it at the blended input rate, and scan the
    prompt-prefix files for volatile content. The single LLM call then writes
    ``insights``/``recommendations`` prose over the finished figures — never
    numbers. Every dollar figure is an estimate and every mechanism count is a
    floor: an unreadable transcript under-reports rather than failing the run.

    project_path: keep only sessions whose project directory is this absolute
        path or sits under it (``engine._in_repo``).
    dry_run: skip the LLM (deterministic artifact only, no warning).
    """
    resolved_today = today or datetime.now(timezone.utc).date()
    window_days = max(1, int(window_days))
    period_start = (resolved_today - timedelta(days=window_days - 1)).isoformat()
    period_end = resolved_today.isoformat()
    logger.info(
        "agent advisor: %d-day window to %s (repo=%r dry_run=%s)", window_days, period_end, project_path, dry_run
    )

    warnings: list[str] = []
    with AgentWatchStore(_resolve_db_path(db_path)) as store:
        _emit(on_progress, "scan", "running", label="Scan agent sessions")
        stats = collector.refresh(store, on_progress=on_progress)
        _emit(
            on_progress,
            "scan",
            "completed",
            label="Scan agent sessions",
            detail=f"{stats.files_parsed} parsed · {stats.files_skipped} cached",
        )
        warnings.extend(stats.warnings)
        sessions = store.list_sessions(since=period_start)
    if project_path:
        from yeaboi.agentwatch.engine import _in_repo

        sessions = [s for s in sessions if _in_repo(s["project_path"], project_path)]

    # One audit per transcript file: rollup rows are keyed per file, so the
    # distinct source paths ARE the window's transcript set. A session is
    # windowed by its ended_at and its WHOLE transcript is audited, so waste
    # from turns before period_start can be measured against in-window spend —
    # one more reason every figure here is an estimate, not an invoice.
    paths = sorted({s["source_path"] for s in sessions if s["source_path"]})
    audit_paths = [p for p in (Path(raw) for raw in paths) if p.suffix == ".jsonl"]

    _emit(on_progress, "audit", "running", label="Audit Read waste", detail=f"{len(audit_paths)} transcript(s)")

    def _meter(current: int, total: int) -> None:
        _emit(
            on_progress,
            "audit",
            "running",
            label="Audit Read waste",
            detail=f"{current}/{total} transcript(s)",
        )

    audit = waste_audit.audit_files(audit_paths, on_file=_meter)
    _emit(
        on_progress,
        "audit",
        "completed" if audit_paths else "no_data",
        label="Audit Read waste",
        detail=f"{audit.sessions} audited" if audit_paths else "no transcripts in the window",
    )
    if audit.files_skipped:
        warnings.append(f"{audit.files_skipped} transcript(s) could not be re-read — waste figures are a floor.")

    rate, unknown_rate_share = _blended_input_rate(sessions)
    line_items = _line_items(audit, rate)
    recoverable_usd = round(sum(i.est_usd for i in line_items if i.recoverable), 4)
    total_cost = round(sum(_session_cost(s["model_usage"])[0] for s in sessions), 4)
    # Waste is priced at the fresh-input rate, but a re-read that hit the
    # prompt cache actually billed at the (10x cheaper) cache-read rate — so on
    # a cache-heavy window the raw estimate can exceed the window's measured
    # spend, and "$9 recoverable of $4" is not a claim worth rendering. Cap the
    # headline at what was actually spent and say the cap fired; the per-item
    # figures keep their raw values, labelled estimates.
    if total_cost > 0 and recoverable_usd > total_cost:
        warnings.append(
            f"Recoverable estimate (${recoverable_usd:,.2f}) exceeded the window's measured spend — "
            "waste is priced at fresh-input rates while cached re-reads billed cheaper. "
            "Headline capped at the window total; treat it as an upper bound."
        )
        recoverable_usd = total_cost

    _emit(on_progress, "signals", "running", label="Check cache health")
    volatile_signals, score = _scan_prefix_files(_prefix_files(sessions))
    _emit(
        on_progress,
        "signals",
        "completed",
        label="Check cache health",
        detail=f"score {score}/100",
    )

    if not sessions:
        warnings.append("No local agent sessions found in the window — is Claude Code used on this machine?")

    report = AgentAdvisorReport(
        period_start=period_start,
        period_end=period_end,
        session_count=len({s["session_id"] or s["source_path"] for s in sessions}),
        files_audited=audit.sessions,
        total_cost_usd=total_cost,
        read_calls=audit.read_calls,
        read_bytes=audit.read_bytes,
        tool_bytes_total=sum(audit.tool_bytes.values()),
        recoverable_usd=recoverable_usd,
        recoverable_share=round(recoverable_usd / total_cost, 4) if total_cost > 0 else 0.0,
        effective_input_rate_per_mtok=round(rate, 4),
        unknown_rate_share=round(unknown_rate_share, 4),
        pricing_as_of=PRICING_AS_OF,
        line_items=line_items,
        residency_median=audit.residency_median,
        residency_p90=audit.residency_p90,
        gaps_over_5m=audit.gaps_over_5m,
        gaps_over_1h=audit.gaps_over_1h,
        sessions_with_gap=audit.sessions_with_gap,
        volatile_signals=volatile_signals,
        alignment_score=score,
        warnings=tuple(warnings),
    )

    # ── The one LLM call: prose over finished numbers ─────────────────────
    insights: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    if sessions and not dry_run:
        _emit(on_progress, "insights", "running", label="Write advice")
        from yeaboi.prompts.agentwatch import get_advisor_insights_prompt

        prompt = get_advisor_insights_prompt(
            period_start=period_start,
            period_end=period_end,
            total_cost_usd=round(total_cost, 2),
            recoverable_usd=round(recoverable_usd, 2),
            line_items=[
                (i.label, i.calls, i.est_usd, i.share_of_read_bytes, i.recoverable) for i in line_items if i.est_tokens
            ],
            residency_median=audit.residency_median,
            gaps_over_5m=audit.gaps_over_5m,
            volatile_files=[(Path(s.location).name, s.total) for s in volatile_signals[:6]],
            alignment_score=score,
        )
        parsed, llm_warnings = _invoke_llm(prompt, what="advisor-insights")
        insights = _str_list(parsed.get("insights"))[:5]
        recommendations = _str_list(parsed.get("recommendations"))[:5]
        warnings.extend(llm_warnings)
        _emit(on_progress, "insights", "fallback" if llm_warnings else "completed", label="Write advice")
    else:
        _emit(on_progress, "insights", "no_data", label="Write advice", detail="skipped")
    if not insights:
        fallback_insights, fallback_recs = _fallback_advisor_prose(report)
        insights = fallback_insights
        recommendations = recommendations or fallback_recs

    from dataclasses import replace

    report = replace(
        report,
        insights=insights,
        recommendations=recommendations,
        warnings=tuple(warnings),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    # Persist + auto-export (blueprint: every run leaves an artifact on disk).
    try:
        with AgentWatchStore(_resolve_db_path(db_path)) as store:
            store.record_report("advisor", report, key_date=report.period_start)
    except Exception as exc:  # noqa: BLE001 — history is best-effort
        logger.warning("agent advisor: could not record report history: %s", exc)
    try:
        from yeaboi.agentwatch.export import export_artifact

        export_artifact(report, kind="advisor")
    except Exception as exc:  # noqa: BLE001 — export must never sink the run
        logger.warning("agent advisor: export failed: %s", exc)

    logger.info(
        "agent advisor: %d file(s) audited, ~$%.2f recoverable of $%.2f, alignment %d/100",
        report.files_audited,
        report.recoverable_usd,
        report.total_cost_usd,
        report.alignment_score,
    )
    return report
