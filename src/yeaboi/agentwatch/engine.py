"""agentwatch engine — the Agents family's headless pipelines.

Like the standup and performance engines, these are standalone pipelines (NOT
LangGraph nodes): one deterministic gather step + a single LLM call following
the same parse → fallback → format convention the graph nodes use
(agent/nodes.py). An LLM auth/billing failure is never re-raised — it becomes a
user-facing *warning* and a deterministic fallback artifact, so every surface
always renders something useful.

Pipelines:
  run_agent_usage()    → ingest local agent sessions → price → LLM insights → AgentUsageReport
  run_agent_standup()  → local sessions + agent-authored tracker activity → LLM narrative
                         → AgentStandupDigest

Every number in an artifact is computed deterministically here; the LLM only
writes prose (insights/narrative) over the finished aggregates — a narrative
can be wrong without corrupting a dashboard.

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the agentwatch prompts
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from yeaboi.agent.state import (
    AgentSecurityReport,
    AgentStandupDigest,
    AgentUsageBreakdownRow,
    AgentUsageReport,
    DailyUsagePoint,
    ModelUsageRow,
)
from yeaboi.agentwatch import collector
from yeaboi.agentwatch.store import AgentWatchStore
from yeaboi.analysis.progress import send_component_progress
from yeaboi.pricing import PRICING_AS_OF, estimate_cost

logger = logging.getLogger(__name__)


def _emit(on_progress, component_id: str, status: str, *, label: str = "", detail: str = "") -> None:
    """Send one phase lifecycle event to the caller's progress callback.

    The TUI keys its phase checklist on ``component_id`` (see
    _screens_agents.py); the label rides along for consumers that render
    events standalone. None-safe, so engines can emit unconditionally.
    """
    send_component_progress(
        on_progress,
        component_id=component_id,
        label=label or component_id,
        status=status,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Shared helpers (parse → fallback) — same shape as performance/engine.py
# ---------------------------------------------------------------------------


def _parse_json_response(raw: str) -> dict:
    """Extract a JSON object from an LLM response, tolerating markdown fences."""
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("agentwatch: could not parse LLM JSON response")
        return {}


def _str_list(value) -> tuple[str, ...]:
    """Coerce an LLM field into a tuple of clean strings (tolerant of bad shapes)."""
    if not isinstance(value, list):
        return ()
    return tuple(str(v).strip() for v in value if str(v).strip())


def _invoke_llm(prompt: str, *, what: str) -> tuple[dict, list[str]]:
    """Run one LLM call; return (parsed_json, warnings). Never raises."""
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("agentwatch[%s]: LLM not configured (%s)", what, why)
        return {}, [f"AI output unavailable — {why}."]

    # invoke_json tracks usage + turns on JSON mode + re-asks once on bad JSON.
    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint

    try:
        logger.info("agentwatch[%s]: invoking LLM", what)
        response = invoke_json(prompt, temperature=0.2)
        return _parse_json_response(response.content), []
    except Exception as exc:  # noqa: BLE001 — turn any LLM failure into a warning + fallback
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("agentwatch[%s]: LLM auth/billing error: %s", what, exc)
            return {}, ["AI output unavailable — API key invalid or billing issue."]
        local_hint = _local_llm_hint(exc)
        if local_hint:
            logger.warning("agentwatch[%s]: local Ollama failure: %s", what, exc)
            return {}, [f"AI output unavailable — {local_hint}"]
        logger.warning("agentwatch[%s]: LLM request failed: %s", what, exc)
        return {}, ["AI output unavailable — LLM request failed (see logs)."]


def _resolve_db_path(db_path):
    if db_path is not None:
        return db_path
    from yeaboi.paths import get_db_path

    return get_db_path()


# ---------------------------------------------------------------------------
# Go core dispatch (the YEABOI_GO pilot — see contracts/v1/rpc.md)
# ---------------------------------------------------------------------------
# The sidecar accelerates the deterministic half only: scanning transcripts
# and aggregating/pricing usage. The LLM prose call, tracker scans, security
# config audits, delivery, history and exports all stay in Python. Every
# failure downgrades to the Python path with one log line — the Go path is
# never the only path.


def _go_client():
    """The sidecar client when the pilot flag is active and healthy, else None."""
    try:
        from yeaboi import gocore

        return gocore.get_client()
    except Exception as exc:  # noqa: BLE001 — dispatch must never sink a pipeline
        logger.warning("gocore: client unavailable (%s: %s) — using the Python path", type(exc).__name__, exc)
        return None


def _stats_from_wire(payload: dict) -> collector.IngestStats:
    return collector.IngestStats(
        files_seen=int(payload.get("files_seen", 0)),
        files_skipped=int(payload.get("files_skipped", 0)),
        files_parsed=int(payload.get("files_parsed", 0)),
        files_pruned=int(payload.get("files_pruned", 0)),
        sessions_upserted=int(payload.get("sessions_upserted", 0)),
        findings_added=int(payload.get("findings_added", 0)),
        malformed_lines=int(payload.get("malformed_lines", 0)),
        warnings=[str(w) for w in payload.get("warnings") or []],
    )


def _go_refresh(db_path, *, on_progress=None, reset_cursors: bool = False) -> collector.IngestStats | None:
    """Run the transcript scan in the Go core. None → caller runs Python."""
    client = _go_client()
    if client is None:
        return None
    from yeaboi.gocore import CoreError

    try:
        result = client.request(
            "agentwatch.refresh",
            {"db_path": str(_resolve_db_path(db_path)), "reset_cursors": reset_cursors},
            on_progress=on_progress,
        )
    except CoreError as exc:
        logger.warning("gocore: agentwatch.refresh failed (%s) — falling back to Python", exc)
        return None
    logger.info("gocore: agentwatch.refresh served by the sidecar")
    return _stats_from_wire(result.get("stats") or {})


def _go_usage_report(*, window_days, project, source, db_path, today, on_progress):
    """Scan + aggregate + price in the Go core → deterministic AgentUsageReport.

    The artifact comes back in the exact payload shape ``report_from_payload``
    already accepts (that is the contract), with empty insights/prose fields
    for the Python side to fill. None → caller computes in Python.
    """
    client = _go_client()
    if client is None:
        return None
    from yeaboi.gocore import CoreError

    try:
        result = client.request(
            "agentwatch.usage",
            {
                "db_path": str(_resolve_db_path(db_path)),
                "window_days": int(window_days),
                "project": project,
                "source": source,
                "today": today.isoformat(),
            },
            on_progress=on_progress,
        )
    except CoreError as exc:
        logger.warning("gocore: agentwatch.usage failed (%s) — falling back to Python", exc)
        return None
    from yeaboi.agentwatch.store import report_from_payload

    report = report_from_payload("usage", result.get("artifact"))
    if report is None:
        logger.warning("gocore: agentwatch.usage artifact did not hydrate — falling back to Python")
        return None
    logger.info("gocore: agentwatch.usage served by the sidecar")
    return report


def _distinct_session_count(sessions: list[dict]) -> int:
    """Count logical sessions, not rollup rows.

    Rollups are stored one per transcript file, so a session resumed from a
    different cwd (or a copied transcript) is two rows with one ``session_id``.
    Token totals want every row — they are disjoint — but a *count* shown to a
    human means "how many sessions ran". A row with no id counts on its own.
    """
    return len({s["session_id"] or s["source_path"] for s in sessions})


def _project_label(project_path: str) -> str:
    """A readable per-project key: the path's last component (repo/dir name)."""
    return Path(project_path).name or project_path or "(unknown)"


def _session_cost(model_usage: dict) -> tuple[float, bool]:
    """Price one session's per-model usage; return (usd, all models known)."""
    total = 0.0
    all_known = True
    for model, u in model_usage.items():
        est = estimate_cost(
            model,
            int(u.get("input", 0)),
            int(u.get("output", 0)),
            cache_write_tokens=int(u.get("cache_write_5m", 0)),
            cache_write_1h_tokens=int(u.get("cache_write_1h", 0)),
            cache_read_tokens=int(u.get("cache_read", 0)),
        )
        total += est.usd
        all_known = all_known and est.known_model
    return total, all_known


# ---------------------------------------------------------------------------
# Agent Usage
# ---------------------------------------------------------------------------


def _fallback_usage_insights(report_rows: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Deterministic insights when the LLM is unavailable — evidence, not analysis."""
    insights: list[str] = []
    by_model = report_rows.get("by_model", ())
    by_project = report_rows.get("by_project", ())
    if by_model:
        top = by_model[0]
        insights.append(f"Most spend went to {top.model} (${top.cost_usd:,.2f}).")
    if by_project:
        top_p = by_project[0]
        insights.append(f"Busiest project: {top_p.key} ({top_p.sessions} session(s), ${top_p.cost_usd:,.2f}).")
    reads = report_rows.get("cache_read", 0)
    writes = report_rows.get("cache_write", 0)
    if reads or writes:
        insights.append(f"Cache traffic: {reads:,} tokens read vs {writes:,} written.")
    return tuple(insights), ()


def _deterministic_usage_report(
    *,
    window_days: int,
    project: str = "",
    source: str = "",
    db_path=None,
    today: date | None = None,
    on_progress=None,
    roots=None,
) -> AgentUsageReport:
    """Everything in the usage pipeline up to (not including) the LLM.

    Scan, aggregate, price — returns the artifact with empty ``insights``/
    ``recommendations``/``generated_at`` for the caller to fill. This function
    is the exact computation the Go core mirrors (contracts/v1/
    agentwatch.usage.json); tests/parity runs both over the same fixtures, so
    behavior changes here must bump or update the contract.

    Two deliberate approximations in the windowing, both erring toward showing
    work rather than hiding it. A session is placed by its ``ended_at``, so one
    that ran across the window boundary has its whole cost attributed to the
    day it finished. And the window has no upper bound, so a session with a
    clock-skewed future ``ended_at`` stays visible instead of disappearing into
    a gap — a cost dashboard that silently omits spend is the worse failure.
    """
    resolved_today = today or datetime.now(UTC).date()
    window_days = max(1, int(window_days))
    period_start = (resolved_today - timedelta(days=window_days - 1)).isoformat()
    period_end = resolved_today.isoformat()

    warnings: list[str] = []
    with AgentWatchStore(_resolve_db_path(db_path)) as store:
        _emit(on_progress, "scan", "running", label="Scan agent sessions")
        stats = collector.refresh(store, roots=roots, on_progress=on_progress)
        _emit(
            on_progress,
            "scan",
            "completed",
            label="Scan agent sessions",
            detail=f"{stats.files_parsed} parsed · {stats.files_skipped} cached",
        )
        warnings.extend(stats.warnings)
        sessions = store.list_sessions(since=period_start)

    if project:
        sessions = [s for s in sessions if project.lower() in _project_label(s["project_path"]).lower()]
    if source:
        sessions = [s for s in sessions if s["source"] == source]

    _emit(on_progress, "price", "running", label="Price usage", detail=f"{len(sessions)} transcript(s)")

    model_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    project_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    source_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    daily_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    unknown_cost = 0.0
    total_cost = 0.0

    # Costs sum over rollup ROWS (one per transcript file, disjoint), but the
    # session *counts* beside them must be distinct logical sessions — a
    # resumed session is two rows carrying one id. Sets, then len() at build.
    bucket_sessions: dict[tuple[str, str], set[str]] = defaultdict(set)

    for session in sessions:
        s_cost, _known = _session_cost(session["model_usage"])
        p_label = _project_label(session["project_path"])
        day = (session["ended_at"] or "")[:10]
        s_key = session["session_id"] or session["source_path"]
        for bucket, kind, key in (
            (project_totals, "project", p_label),
            (source_totals, "source", session["source"] or "(unknown)"),
        ):
            bucket_sessions[(kind, key)].add(s_key)
            bucket[key]["cost"] += s_cost
        if day:
            bucket_sessions[("day", day)].add(s_key)
            daily_totals[day]["cost"] += s_cost
        total_cost += s_cost
        for model, u in session["model_usage"].items():
            est = estimate_cost(
                model,
                int(u.get("input", 0)),
                int(u.get("output", 0)),
                cache_write_tokens=int(u.get("cache_write_5m", 0)),
                cache_write_1h_tokens=int(u.get("cache_write_1h", 0)),
                cache_read_tokens=int(u.get("cache_read", 0)),
            )
            m = model_totals[model]
            m["input"] += int(u.get("input", 0))
            m["output"] += int(u.get("output", 0))
            m["cache_write"] += int(u.get("cache_write_5m", 0)) + int(u.get("cache_write_1h", 0))
            m["cache_read"] += int(u.get("cache_read", 0))
            m["calls"] += int(u.get("calls", 0))
            m["cost"] += est.usd
            m["known"] = float(est.known_model)
            if not est.known_model:
                unknown_cost += est.usd
            for bucket, key in (
                (project_totals, p_label),
                (source_totals, session["source"] or "(unknown)"),
            ):
                bucket[key]["input"] += int(u.get("input", 0))
                bucket[key]["output"] += int(u.get("output", 0))
            if day:
                daily_totals[day]["input"] += int(u.get("input", 0))
                daily_totals[day]["output"] += int(u.get("output", 0))

    by_model = tuple(
        ModelUsageRow(
            model=model,
            input_tokens=int(t["input"]),
            output_tokens=int(t["output"]),
            cache_write_tokens=int(t["cache_write"]),
            cache_read_tokens=int(t["cache_read"]),
            calls=int(t["calls"]),
            cost_usd=round(t["cost"], 4),
            known_pricing=bool(t["known"]),
        )
        for model, t in sorted(model_totals.items(), key=lambda kv: kv[1]["cost"], reverse=True)
    )

    def _breakdown(bucket: dict[str, dict[str, float]], kind: str) -> tuple[AgentUsageBreakdownRow, ...]:
        return tuple(
            AgentUsageBreakdownRow(
                key=key,
                sessions=len(bucket_sessions[(kind, key)]),
                input_tokens=int(t["input"]),
                output_tokens=int(t["output"]),
                cost_usd=round(t["cost"], 4),
            )
            for key, t in sorted(bucket.items(), key=lambda kv: kv[1]["cost"], reverse=True)
        )

    by_project = _breakdown(project_totals, "project")
    by_source = _breakdown(source_totals, "source")
    daily_trend = tuple(
        DailyUsagePoint(
            date=day,
            cost_usd=round(t["cost"], 4),
            input_tokens=int(t["input"]),
            output_tokens=int(t["output"]),
            sessions=len(bucket_sessions[("day", day)]),
        )
        for day, t in sorted(daily_totals.items())
    )

    if not sessions:
        warnings.append("No local agent sessions found in the window — is Claude Code used on this machine?")
    # no_data, not completed, on an empty window — same checklist vocabulary as
    # the insights/trackers phases' nothing-to-do case.
    price_status = "completed" if sessions else "no_data"
    _emit(on_progress, "price", price_status, label="Price usage", detail=f"{len(sessions)} transcript(s)")

    return AgentUsageReport(
        period_start=period_start,
        period_end=period_end,
        session_count=_distinct_session_count(sessions),
        total_cost_usd=round(total_cost, 4),
        total_input_tokens=sum(r.input_tokens for r in by_model),
        total_output_tokens=sum(r.output_tokens for r in by_model),
        total_cache_write_tokens=sum(r.cache_write_tokens for r in by_model),
        total_cache_read_tokens=sum(r.cache_read_tokens for r in by_model),
        unknown_model_cost_share=round(unknown_cost / total_cost, 4) if total_cost > 0 else 0.0,
        pricing_as_of=PRICING_AS_OF,
        by_model=by_model,
        by_project=by_project,
        by_source=by_source,
        daily_trend=daily_trend,
        warnings=tuple(warnings),
    )


def run_agent_usage(
    *,
    window_days: int = 30,
    project: str = "",
    source: str = "",
    db_path=None,
    today: date | None = None,
    on_progress=None,
    dry_run: bool = False,
) -> AgentUsageReport:
    """Build the agent cost/usage dashboard over locally monitored sessions.

    Deterministic gather: refresh the collector's ingest, aggregate the stored
    session rollups over the window, and price every (model, session) pair from
    the shared pricing table — served by the Go core when the YEABOI_GO pilot
    is active, by ``_deterministic_usage_report`` otherwise, with identical
    results (tests/parity). The single LLM call then writes ``insights`` and
    ``recommendations`` prose over the computed aggregates — never numbers.

    project: substring filter on the session's project directory name.
    source:  exact filter on the telemetry source (currently "claude_code").
    dry_run: skip the LLM (deterministic artifact only, no warning).
    """
    resolved_today = today or datetime.now(UTC).date()
    window_days = max(1, int(window_days))
    logger.info(
        "agent usage: %d-day window to %s (project=%r source=%r dry_run=%s)",
        window_days,
        resolved_today.isoformat(),
        project,
        source,
        dry_run,
    )

    report = _go_usage_report(
        window_days=window_days,
        project=project,
        source=source,
        db_path=db_path,
        today=resolved_today,
        on_progress=on_progress,
    )
    if report is None:
        report = _deterministic_usage_report(
            window_days=window_days,
            project=project,
            source=source,
            db_path=db_path,
            today=resolved_today,
            on_progress=on_progress,
        )

    warnings = list(report.warnings)
    by_model = report.by_model
    by_project = report.by_project
    has_sessions = report.session_count > 0

    # ── The one LLM call: prose over finished numbers ─────────────────────
    insights: tuple[str, ...] = ()
    recommendations: tuple[str, ...] = ()
    if has_sessions and not dry_run:
        _emit(on_progress, "insights", "running", label="Write insights")
        from yeaboi.prompts.agentwatch import get_usage_insights_prompt

        prompt = get_usage_insights_prompt(
            period_start=report.period_start,
            period_end=report.period_end,
            total_cost_usd=round(report.total_cost_usd, 2),
            by_model=[(r.model, r.cost_usd, r.input_tokens, r.output_tokens) for r in by_model[:8]],
            by_project=[(r.key, r.cost_usd, r.sessions) for r in by_project[:8]],
            cache_read_tokens=sum(r.cache_read_tokens for r in by_model),
            cache_write_tokens=sum(r.cache_write_tokens for r in by_model),
        )
        parsed, llm_warnings = _invoke_llm(prompt, what="usage-insights")
        warnings.extend(llm_warnings)
        insights = _str_list(parsed.get("insights"))[:5]
        recommendations = _str_list(parsed.get("recommendations"))[:5]
        _emit(on_progress, "insights", "fallback" if llm_warnings else "completed", label="Write insights")
    else:
        _emit(on_progress, "insights", "no_data", label="Write insights", detail="skipped")
    if not insights:
        # Per-field, not both-or-nothing: a model that returns usable
        # recommendations but an empty insights list used to have them thrown
        # away, because the fallback returns () for recommendations by design.
        fallback_insights, fallback_recs = _fallback_usage_insights(
            {
                "by_model": by_model,
                "by_project": by_project,
                "cache_read": sum(r.cache_read_tokens for r in by_model),
                "cache_write": sum(r.cache_write_tokens for r in by_model),
            }
        )
        insights = fallback_insights
        recommendations = recommendations or fallback_recs

    from dataclasses import replace

    report = replace(
        report,
        insights=insights,
        recommendations=recommendations,
        warnings=tuple(warnings),
        generated_at=datetime.now(UTC).isoformat(),
    )

    # Persist + auto-export (blueprint: every run leaves an artifact on disk).
    try:
        with AgentWatchStore(_resolve_db_path(db_path)) as store:
            store.record_report("usage", report, key_date=report.period_start)
    except Exception as exc:  # noqa: BLE001 — history is best-effort
        logger.warning("agent usage: could not record report history: %s", exc)
    try:
        from yeaboi.agentwatch.export import export_artifact

        export_artifact(report, kind="usage")
    except Exception as exc:  # noqa: BLE001 — export must never sink the run
        logger.warning("agent usage: export failed: %s", exc)

    logger.info(
        "agent usage: %d session(s), $%.2f total, %d model(s)",
        report.session_count,
        report.total_cost_usd,
        len(report.by_model),
    )
    return report


# ---------------------------------------------------------------------------
# Agent Standup
# ---------------------------------------------------------------------------


def _summarise_sessions(sessions: list[dict]) -> tuple:
    """Local session rollups → AgentSessionSummary rows, costliest first."""
    from yeaboi.agent.state import AgentSessionSummary

    summaries = []
    for session in sessions:
        cost, _known = _session_cost(session["model_usage"])
        top_tools = sorted(session["tool_counts"].items(), key=lambda kv: kv[1], reverse=True)[:3]
        summaries.append(
            AgentSessionSummary(
                session_id=session["session_id"],
                source=session["source"],
                project=_project_label(session["project_path"]),
                branch=session["git_branch"],
                models=tuple(sorted(session["model_usage"])),
                turns=session["turns"],
                cost_usd=round(cost, 4),
                top_tools=tuple((name, str(count)) for name, count in top_tools),
                started_at=session["started_at"],
                ended_at=session["ended_at"],
            )
        )
    return tuple(sorted(summaries, key=lambda s: s.cost_usd, reverse=True))


def _collect_agent_repo_activity(
    *,
    window_days: int,
    tracker_sources: list[str] | None,
    github_owners: list[str] | None,
    azdo_projects: list[str] | None,
    on_progress=None,
) -> tuple[tuple, tuple[str, ...]]:
    """Agent-authored tracker items in the window → (rows, coverage notes).

    Reuses the analysis mode's agent-identity detection (trailers, bot account
    shapes, branch prefixes) and the standup automation filter, so a Wiz-style
    service hook never shows up as "an agent shipped something". Best-effort:
    missing credentials contribute a coverage note, never a failure.
    """
    from yeaboi.agent.state import AgentRepoActivityRow

    if tracker_sources is not None and not tracker_sources:
        _emit(on_progress, "trackers", "no_data", label="Scan trackers", detail="skipped — local sessions only")
        return (), ("Tracker scan skipped (tracker_sources=[]) — local sessions only.",)

    _emit(on_progress, "trackers", "running", label="Scan trackers")
    try:
        from yeaboi.analysis.ai_usage import _classify_ai_item, collect_ai_activity

        scope: dict[str, list[str]] = {}
        if github_owners:
            scope["github"] = list(github_owners)
        if azdo_projects:
            scope["azdo"] = list(azdo_projects)
        items, _sources, coverage, _repos = collect_ai_activity(
            "",
            # No project key: this scan is team-wide, not scoped to one board.
            # "agent-standup" used to sit here as a label, which is inert only
            # because ai_usage gates project_key on source == "azdevops" — a
            # change to that gate would silently scope the AzDO scan to a
            # project of that name.
            "",
            list(tracker_sources) if tracker_sources else None,
            window_days=window_days,
            analysis_scope=scope or None,
        )
    except Exception as exc:  # noqa: BLE001 — trackers are optional context
        logger.warning("agent standup: tracker scan failed: %s", exc)
        _emit(on_progress, "trackers", "no_data", label="Scan trackers", detail="unavailable")
        return (), (f"Tracker scan unavailable: {exc}",)

    # Drop non-agent automation (service hooks, scanners) before classifying —
    # the standup filter only inspects review/comment kinds.
    from yeaboi.standup.automation import partition_automated

    kept, clusters = partition_automated(items)
    if clusters:
        logger.info("agent standup: %d automation cluster(s) excluded", len(clusters))

    rows = []
    for item in kept:
        hits = _classify_ai_item(item)
        if not hits:
            continue
        rows.append(
            AgentRepoActivityRow(
                source=str(item.get("source", "") or "github"),
                repo=str(item.get("repository", "")).rsplit("/", 1)[-1],
                kind=str(item.get("kind", "")),
                title=str(item.get("title", ""))[:140],
                url=str(item.get("url", "")),
                author=str(item.get("author", "")),
                status=str(item.get("status", "")),
                agent_marker=", ".join(sorted(hits)),
            )
        )
    order = {"pr": 0, "commit": 1, "review": 2, "comment": 3}
    rows.sort(key=lambda r: (order.get(r.kind, 9), r.repo, r.title))
    _emit(on_progress, "trackers", "completed", label="Scan trackers", detail=f"{len(rows)} item(s)")
    return tuple(rows), tuple(coverage)


def _fallback_standup_prose(summaries: tuple, repo_rows: tuple) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    """Deterministic highlights / attention items / narrative — evidence, not analysis."""
    highlights = []
    for row in repo_rows:
        if row.kind == "pr" and row.status == "merged":
            highlights.append(f"Merged: {row.title} ({row.repo}, {row.agent_marker})")
    for summary in summaries[:3]:
        highlights.append(f"Session on {summary.project} ({summary.source}, ${summary.cost_usd:,.2f})")
    attention = [
        f"Open agent PR: {row.title} ({row.repo})" for row in repo_rows if row.kind == "pr" and row.status == "open"
    ]
    narrative = (
        f"{len(summaries)} local agent session(s) and {len(repo_rows)} agent-authored tracker item(s) in the window."
    )
    return tuple(highlights[:6]), tuple(attention[:6]), narrative


def run_agent_standup(
    *,
    days: int | None = None,
    tracker_sources: list[str] | None = None,
    github_owners: list[str] | None = None,
    azdo_projects: list[str] | None = None,
    deliver: bool = False,
    db_path=None,
    today: date | None = None,
    on_progress=None,
    dry_run: bool = False,
) -> AgentStandupDigest:
    """Build the daily "what did the agents do" digest.

    Window: like the human standup, ``days=None`` reaches back to the start of
    the previous working day (a Monday run covers Friday), so weekend gaps
    never hide agent work; an explicit ``days`` looks back that many days.

    Sources: local session rollups always; tracker scanning (GitHub/AzDO
    agent-authored commits/PRs) is best-effort — pass ``tracker_sources=[]``
    for a local-only digest, or a subset of {"github", "azdo"}.

    deliver=True posts the digest to the configured Slack webhook and raises a
    desktop notification (never raises; failures become warnings).
    """
    from yeaboi.standup.collector import previous_working_day_start

    resolved_today = today or datetime.now(UTC).date()
    if days is None:
        window_start_dt = previous_working_day_start(resolved_today)
        window_days = max(1, (resolved_today - window_start_dt.date()).days + 1)
    else:
        window_days = max(1, int(days))
        window_start_dt = datetime.combine(resolved_today - timedelta(days=window_days - 1), datetime.min.time())
    window_start = window_start_dt.date().isoformat()
    digest_date = resolved_today.isoformat()
    logger.info("agent standup: window %s..%s (deliver=%s dry_run=%s)", window_start, digest_date, deliver, dry_run)

    warnings: list[str] = []
    _emit(on_progress, "scan", "running", label="Scan agent sessions")
    # Go core first (single-writer: the sidecar writes before Python opens the
    # store for reads); None falls back to the Python collector in-process.
    stats = _go_refresh(db_path, on_progress=on_progress)
    with AgentWatchStore(_resolve_db_path(db_path)) as store:
        if stats is None:
            stats = collector.refresh(store, on_progress=on_progress)
        _emit(
            on_progress,
            "scan",
            "completed",
            label="Scan agent sessions",
            detail=f"{stats.files_parsed} parsed · {stats.files_skipped} cached",
        )
        warnings.extend(stats.warnings)
        sessions = store.list_sessions(since=window_start)
    summaries = _summarise_sessions(sessions)
    total_cost = round(sum(s.cost_usd for s in summaries), 4)

    repo_rows, tracker_coverage = _collect_agent_repo_activity(
        window_days=window_days,
        tracker_sources=tracker_sources,
        github_owners=github_owners,
        azdo_projects=azdo_projects,
        on_progress=on_progress,
    )

    # Say so when half the picture is missing. A cloud/CI environment has no
    # ~/.claude at all, so its digest is tracker-only — without this note the
    # reader cannot tell "the agents were idle" from "this machine can't see
    # them", and the cowork routine's scheduled run is exactly that case.
    coverage_notes = list(tracker_coverage)
    if not sessions:
        coverage_notes.insert(
            0,
            "No local agent sessions in the window — this environment has no agent session history, "
            "so the digest covers tracker activity only.",
        )
    coverage_notes = tuple(coverage_notes)

    agents_seen = tuple(
        sorted({s.source for s in summaries} | {m for r in repo_rows for m in r.agent_marker.split(", ") if m})
    )
    in_flight = tuple(f"{row.title} ({row.repo})" for row in repo_rows if row.kind == "pr" and row.status == "open")[:8]

    if not summaries and not repo_rows:
        warnings.append("No agent activity found in the window — nothing worked locally, nothing agent-marked landed.")

    # ── The one LLM call: narrative prose over the deterministic rows ─────
    highlights: tuple[str, ...] = ()
    attention: tuple[str, ...] = ()
    narrative = ""
    if (summaries or repo_rows) and not dry_run:
        _emit(on_progress, "digest", "running", label="Write the digest")
        from yeaboi.prompts.agentwatch import get_standup_digest_prompt

        prompt = get_standup_digest_prompt(
            digest_date=digest_date,
            window_start=window_start,
            total_cost_usd=total_cost,
            sessions=[(s.project, s.source, s.cost_usd, s.turns, list(s.models)) for s in summaries[:12]],
            repo_items=[(r.kind, r.title, r.repo, r.status, r.agent_marker) for r in repo_rows[:20]],
        )
        parsed, llm_warnings = _invoke_llm(prompt, what="standup-digest")
        warnings.extend(llm_warnings)
        highlights = _str_list(parsed.get("highlights"))[:6]
        attention = _str_list(parsed.get("attention_items"))[:6]
        narrative = str(parsed.get("narrative") or "").strip()
        _emit(on_progress, "digest", "fallback" if llm_warnings else "completed", label="Write the digest")
    else:
        _emit(on_progress, "digest", "no_data", label="Write the digest", detail="skipped")
    if not narrative:
        highlights, attention, narrative = _fallback_standup_prose(summaries, repo_rows)

    digest = AgentStandupDigest(
        digest_date=digest_date,
        window_start=window_start,
        window_end=digest_date,
        sessions_worked=len(summaries),
        total_cost_usd=total_cost,
        agents_seen=agents_seen,
        session_summaries=summaries,
        repo_activity=repo_rows,
        highlights=highlights,
        in_flight=in_flight,
        attention_items=attention,
        narrative=narrative,
        coverage_notes=coverage_notes,
        warnings=tuple(warnings),
        generated_at=datetime.now(UTC).isoformat(),
    )

    if deliver:
        delivered = _deliver_digest(digest)
        if not all(delivered.values()):
            failed = ", ".join(channel for channel, ok in delivered.items() if not ok)
            digest = AgentStandupDigest(
                **{**_as_dict_shallow(digest), "warnings": (*digest.warnings, f"Delivery failed: {failed}.")}
            )

    try:
        with AgentWatchStore(_resolve_db_path(db_path)) as store:
            store.record_report("standup", digest, key_date=digest_date)
    except Exception as exc:  # noqa: BLE001 — history is best-effort
        logger.warning("agent standup: could not record digest history: %s", exc)
    try:
        from yeaboi.agentwatch.export import export_artifact

        export_artifact(digest, kind="standup")
    except Exception as exc:  # noqa: BLE001 — export must never sink the run
        logger.warning("agent standup: export failed: %s", exc)

    logger.info(
        "agent standup: %d session(s), %d tracker item(s), $%.2f",
        digest.sessions_worked,
        len(digest.repo_activity),
        digest.total_cost_usd,
    )
    return digest


def _as_dict_shallow(artifact) -> dict:
    """Field → value for rebuilding a frozen artifact with one field changed.

    dataclasses.replace would also work; this keeps tuple fields as-is without
    asdict's deep list conversion.
    """
    from dataclasses import fields

    return {f.name: getattr(artifact, f.name) for f in fields(artifact)}


def _deliver_digest(digest) -> dict[str, bool]:
    """Post the digest to Slack (+ a desktop notification). Never raises.

    The standup mode's ``deliver()`` is typed to StandupReport and formats via
    its own plaintext renderer, so agentwatch posts through the same
    configured webhook directly rather than duck-typing another mode's report.
    """
    from yeaboi.agentwatch.export import build_standup_plaintext

    results: dict[str, bool] = {}
    text = build_standup_plaintext(digest)
    try:
        from yeaboi import config

        webhook = getattr(config, "get_slack_webhook_url", lambda: "")() or ""
    except Exception:  # noqa: BLE001
        webhook = ""
    if not webhook:
        logger.warning("agent standup delivery: no SLACK_WEBHOOK_URL configured")
        results["slack"] = False
    else:
        import json as json_mod
        import urllib.request

        payload = json_mod.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — user-configured webhook
                results["slack"] = 200 <= resp.status < 300
        except Exception as exc:  # noqa: BLE001
            logger.error("agent standup delivery[slack] failed: %s", exc)
            results["slack"] = False
    try:
        from yeaboi.standup.delivery import notify_desktop

        results["desktop"] = notify_desktop(
            "Agent Standup",
            f"{digest.sessions_worked} session(s), ${digest.total_cost_usd:,.2f} — {digest.narrative[:120]}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("agent standup delivery[desktop] failed: %s", exc)
        results["desktop"] = False
    return results


# ---------------------------------------------------------------------------
# Agent Security
# ---------------------------------------------------------------------------

_STORED_FINDING_TITLES = {
    "secret": "Credential-shaped text in a session transcript",
    "risky_tool": "Risky shell command run by an agent",
}
_STORED_FINDING_REMEDIATION = {
    "secret": "Rotate the credential; avoid pasting secrets into agent sessions.",
    "risky_tool": "Review the session; consider denying the pattern in your agent's permission rules.",
}


def _stored_findings(store: AgentWatchStore) -> list:
    """Collector-persisted signals (secrets, risky commands) → SecurityFinding rows.

    The rows reference (pattern, file, line) only — the collector never stored
    the matched content, and neither does this report.
    """
    from yeaboi.agent.state import SecurityFinding

    rows = []
    for finding in store.list_findings():
        category = finding["category"]
        rows.append(
            SecurityFinding(
                severity=finding["severity"],
                category=category,
                title=_STORED_FINDING_TITLES.get(category, "Session security signal"),
                location=finding["source_path"],
                line_no=finding["line_no"],
                pattern=finding["pattern"],
                detail=f"session {finding['session_id']}" if finding["session_id"] else "",
                remediation=_STORED_FINDING_REMEDIATION.get(category, ""),
            )
        )
    return rows


def run_agent_security(
    *,
    deep: bool = False,
    db_path=None,
    today: date | None = None,
    on_progress=None,
    dry_run: bool = False,
) -> AgentSecurityReport:
    """Audit the local agent setup: settings, MCP servers, secrets, risky tools.

    Every check is a deterministic pattern scan (see security_checks.py) — an
    indicator, not a security audit. The single LLM call writes the ``summary``
    and prioritised ``recommendations`` prose over the finished findings.

    deep=True forgets the ingest cursors first, so every transcript is
    re-scanned rather than only new/changed files.
    """
    from yeaboi.agentwatch import security_checks

    resolved_today = today or datetime.now(UTC).date()
    scan_date = resolved_today.isoformat()
    logger.info("agent security: scan %s (deep=%s dry_run=%s)", scan_date, deep, dry_run)

    warnings: list[str] = []
    scan_label = "Re-scan every transcript" if deep else "Scan transcripts"
    _emit(on_progress, "scan", "running", label=scan_label)
    # Go core first (deep re-scan travels as reset_cursors over the wire);
    # None falls back to the Python collector in-process.
    stats = _go_refresh(db_path, on_progress=on_progress, reset_cursors=deep)
    with AgentWatchStore(_resolve_db_path(db_path)) as store:
        if stats is None:
            if deep:
                store.reset_cursors()
            stats = collector.refresh(store, on_progress=on_progress)
        _emit(
            on_progress,
            "scan",
            "completed",
            label=scan_label,
            detail=f"{stats.files_parsed} parsed · {stats.files_skipped} cached",
        )
        warnings.extend(stats.warnings)
        sessions_scanned = _distinct_session_count(store.list_sessions())
        files_scanned = stats.files_seen
        findings = _stored_findings(store)

    _emit(on_progress, "settings", "running", label="Audit settings")
    settings_findings = security_checks.audit_settings()
    findings.extend(settings_findings)
    _emit(on_progress, "settings", "completed", label="Audit settings", detail=f"{len(settings_findings)} finding(s)")
    _emit(on_progress, "mcp", "running", label="Inventory MCP servers")
    mcp_servers, mcp_findings = security_checks.inventory_mcp()
    findings.extend(mcp_findings)
    _emit(on_progress, "mcp", "completed", label="Inventory MCP servers", detail=f"{len(mcp_servers)} server(s)")

    ranked = security_checks.rank_findings(findings)
    posture = security_checks.compute_posture(ranked)
    secrets_found = sum(1 for f in ranked if f.category == "secret")
    settings_flags = tuple(sorted({f.pattern for f in ranked if f.category == "settings" and f.severity != "info"}))

    # ── The one LLM call: summary + prioritised advice over the findings ──
    summary = ""
    recommendations: tuple[str, ...] = ()
    if ranked and not dry_run:
        _emit(on_progress, "summary", "running", label="Write the summary")
        from yeaboi.prompts.agentwatch import get_security_summary_prompt

        prompt = get_security_summary_prompt(
            scan_date=scan_date,
            posture=posture,
            findings=[(f.severity, f.category, f.title, f.pattern) for f in ranked[:25]],
            mcp_count=len(mcp_servers),
            sessions_scanned=sessions_scanned,
        )
        parsed, llm_warnings = _invoke_llm(prompt, what="security-summary")
        warnings.extend(llm_warnings)
        summary = str(parsed.get("summary") or "").strip()
        recommendations = _str_list(parsed.get("recommendations"))[:5]
        _emit(on_progress, "summary", "fallback" if llm_warnings else "completed", label="Write the summary")
    else:
        _emit(on_progress, "summary", "no_data", label="Write the summary", detail="skipped")
    if not summary:
        by_severity: dict[str, int] = {}
        for f in ranked:
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        counts = ", ".join(f"{n} {sev}" for sev, n in sorted(by_severity.items(), key=lambda kv: kv[0]))
        summary = (
            f"{len(ranked)} finding(s) across {sessions_scanned} session(s) and the agent configs"
            + (f" ({counts})" if counts else "")
            + "."
            if ranked
            else "No known risk patterns matched — remember this is an indicator, not an audit."
        )
        recommendations = recommendations or tuple(f.remediation for f in ranked[:3] if f.remediation)

    report = AgentSecurityReport(
        scan_date=scan_date,
        posture=posture,
        sessions_scanned=sessions_scanned,
        files_scanned=files_scanned,
        secrets_found=secrets_found,
        findings=ranked,
        mcp_servers=tuple(mcp_servers),
        settings_flags=settings_flags,
        summary=summary,
        recommendations=recommendations,
        warnings=tuple(warnings),
        generated_at=datetime.now(UTC).isoformat(),
    )

    try:
        with AgentWatchStore(_resolve_db_path(db_path)) as store:
            store.record_report("security", report, key_date=scan_date)
    except Exception as exc:  # noqa: BLE001 — history is best-effort
        logger.warning("agent security: could not record report history: %s", exc)
    try:
        from yeaboi.agentwatch.export import export_artifact

        export_artifact(report, kind="security")
    except Exception as exc:  # noqa: BLE001 — export must never sink the run
        logger.warning("agent security: export failed: %s", exc)

    logger.info(
        "agent security: %s — %d finding(s), %d MCP server(s)",
        report.posture,
        len(report.findings),
        len(report.mcp_servers),
    )
    return report
