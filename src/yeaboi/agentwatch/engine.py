"""agentwatch engine — the Agents family's headless pipelines.

Like the standup and performance engines, these are standalone pipelines (NOT
LangGraph nodes): one deterministic gather step + a single LLM call following
the same parse → fallback → format convention the graph nodes use
(agent/nodes.py). An LLM auth/billing failure is never re-raised — it becomes a
user-facing *warning* and a deterministic fallback artifact, so every surface
always renders something useful.

Pipelines:
  run_agent_usage()    → ingest local agent sessions → price per day → LLM insights → AgentUsageReport
  run_agent_security() → scan transcripts + audit configs → group, dismiss, diff → LLM summary
                         → AgentSecurityReport

Every number in an artifact is computed deterministically here; the LLM only
writes prose (insights/summary) over the finished aggregates — a narrative can
be wrong without corrupting a dashboard.

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the agentwatch prompts
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from yeaboi.agent.state import (
    AgentSecurityReport,
    AgentUsageBreakdownRow,
    AgentUsageReport,
    DailyUsagePoint,
    ModelUsageRow,
    SecurityFinding,
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


def _distinct_session_count(sessions: list[dict]) -> int:
    """Count logical sessions, not rollup rows.

    Rollups are stored one per transcript file, so a session resumed from a
    different cwd (or a copied transcript) is two rows with one ``session_id``.
    Token totals want every row — they are disjoint — but a *count* shown to a
    human means "how many sessions ran". A row with no id counts on its own.
    """
    return len({s["session_id"] or s["source_path"] for s in sessions})


def _in_repo(session_project_path: str, project_path: str) -> bool:
    """Whether a session's project directory is ``project_path`` or sits under it.

    An exact-or-prefix path match, so a worktree under the repo counts. Never a
    basename substring: two repos named ``api`` must not collide.
    """
    if not project_path:
        return True
    root = os.path.normpath(project_path)
    candidate = os.path.normpath(session_project_path or "")
    return candidate == root or candidate.startswith(root.rstrip(os.sep) + os.sep)


_WORKTREES_SEGMENT = f"{os.sep}.claude{os.sep}worktrees{os.sep}"


@lru_cache(maxsize=512)
def _git_toplevel(project_path: str) -> str:
    """The git toplevel above ``project_path``, or "" (cached per path)."""
    if not project_path or not os.path.isdir(project_path):
        return ""
    from yeaboi.tools.local_git import git_subprocess_env

    try:
        proc = subprocess.run(
            ["git", "-C", project_path, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=git_subprocess_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    top = proc.stdout.strip()
    return top if proc.returncode == 0 and top else ""


def _git_toplevel_name(project_path: str) -> str:
    top = _git_toplevel(project_path)
    return Path(top).name if top else ""


def _project_label(project_path: str) -> str:
    """A readable per-project key that names the repository, not the directory.

    A worktree under ``<repo>/.claude/worktrees/<name>`` labels as
    ``repo/name`` — a branch-named basename alone would merge every repo's
    worktree of the same feature into one row. Elsewhere the git toplevel's
    name wins over a subdirectory's, and the basename is the fallback.
    """
    if not project_path:
        return "(unknown)"
    normalised = os.path.normpath(project_path)
    if _WORKTREES_SEGMENT in normalised:
        repo_root, _sep, tail = normalised.partition(_WORKTREES_SEGMENT)
        repo = Path(repo_root).name
        # A worktree name may hold a slash (``agents/ai-native-sdlc``); the
        # worktree's own git toplevel says where the name ends and the
        # subdirectory begins. Without a checkout, the first segment stands.
        top = os.path.realpath(_git_toplevel(normalised)) if _git_toplevel(normalised) else ""
        marker = f"{os.path.realpath(repo_root)}{_WORKTREES_SEGMENT}"
        name = top[len(marker) :] if top.startswith(marker) else tail.split(os.sep, 1)[0]
        return f"{repo}/{name}" if repo and name else Path(normalised).name
    top = _git_toplevel_name(normalised)
    return top or Path(normalised).name or project_path


def _price(model: str, u: dict):
    """Price one usage bucket (day/model row or session/model entry)."""
    return estimate_cost(
        model,
        int(u.get("input", 0)),
        int(u.get("output", 0)),
        cache_write_tokens=int(u.get("cache_write_5m", 0)),
        cache_write_1h_tokens=int(u.get("cache_write_1h", 0)),
        cache_read_tokens=int(u.get("cache_read", 0)),
        web_search_calls=int(u.get("web_search_calls", 0)),
        web_fetch_calls=int(u.get("web_fetch_calls", 0)),
        premium_input_tokens=int(u.get("premium_input", 0)),
        premium_output_tokens=int(u.get("premium_output", 0)),
    )


def _session_cost(model_usage: dict) -> tuple[float, bool]:
    """Price one session's per-model usage; return (usd, all models known)."""
    total = 0.0
    all_known = True
    for model, u in model_usage.items():
        est = _price(model, u)
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
    share = report_rows.get("cache_cost_share", 0.0)
    if share >= 0.5:
        insights.append(f"{share:.0%} of the estimate is prompt-cache traffic — context size drives this bill.")
    return tuple(insights), ()


def _claude_stats_path() -> Path:
    """Claude Code's own daily activity cache — overridable in tests."""
    return Path.home() / ".claude" / "stats-cache.json"


def _claude_session_count(period_start: str, period_end: str) -> tuple[int, str] | None:
    """Claude Code's own session count for the window, with the last day it computed.

    ``stats-cache.json`` holds no tokens or money, only per-day message and
    session counts — enough to notice when this scan and the CLI disagree
    about how many sessions there were, which is the cheapest honesty check
    available. The cache lags (it is rebuilt when the CLI feels like it), so
    the caller compares only up to ``lastComputedDate``.
    """
    path = _claude_stats_path()
    try:
        if not path.exists():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    days = parsed.get("dailyActivity") if isinstance(parsed, dict) else None
    if not isinstance(days, list):
        return None
    last_computed = str(parsed.get("lastComputedDate") or period_end)
    until = min(last_computed, period_end)
    if until < period_start:
        return None
    total = 0
    seen = False
    for row in days:
        if not isinstance(row, dict):
            continue
        day = str(row.get("date", ""))
        if period_start <= day <= until:
            seen = True
            total += int(row.get("sessionCount") or 0)
    return (total, until) if seen else None


def _deterministic_usage_report(
    *,
    window_days: int,
    project: str = "",
    source: str = "",
    project_path: str = "",
    db_path=None,
    today: date | None = None,
    on_progress=None,
    roots=None,
) -> AgentUsageReport:
    """Everything in the usage pipeline up to (not including) the LLM.

    Scan, aggregate, price — returns the artifact with empty ``insights``/
    ``recommendations``/``generated_at`` for the caller to fill.

    Tokens are windowed by the *day they were spent* (the per-day rollup the
    collector keeps), so a session that ran across the window boundary
    contributes only its in-window part, and the trend shows each day's own
    traffic. The window is closed at ``today``; a clock-skewed future stamp
    lands in a warning rather than in the total.
    """
    resolved_today = today or datetime.now(timezone.utc).date()
    window_days = max(1, int(window_days))
    period_start = (resolved_today - timedelta(days=window_days - 1)).isoformat()
    period_end = resolved_today.isoformat()
    period_stop = (resolved_today + timedelta(days=1)).isoformat()

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
        rows = store.list_session_days(since=period_start, until=period_stop)
        future = store.list_session_days(since=period_stop)
    if stats.duplicates:
        warnings.append(f"{stats.duplicates} request(s) appeared in more than one transcript and were counted once.")
    if stats.no_request_id:
        warnings.append(f"{stats.no_request_id} assistant line(s) carried no request id — deduplicated per file only.")
    if future:
        warnings.append(f"{len(future)} usage row(s) are stamped after today and were left out of the window.")

    if project:
        rows = [r for r in rows if project.lower() in _project_label(r["project_path"]).lower()]
    if source:
        rows = [r for r in rows if r["source"] == source]
    if project_path:
        rows = [r for r in rows if _in_repo(r["project_path"], project_path)]

    files = {r["source_path"] for r in rows}
    _emit(on_progress, "price", "running", label="Price usage", detail=f"{len(files)} transcript(s)")

    model_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    project_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    source_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    daily_totals: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    unknown_cost = 0.0
    total_cost = 0.0
    cache_cost = 0.0
    priced_from_log = 0

    # Costs sum over day rows (disjoint), but the session *counts* beside them
    # must be distinct logical sessions — a resumed session is two rows
    # carrying one id. Sets, then len() at build.
    bucket_sessions: dict[tuple[str, str], set[str]] = defaultdict(set)
    all_sessions: set[str] = set()
    sessions_by_day: dict[str, set[str]] = defaultdict(set)

    for r in rows:
        model = r["model"]
        est = _price(model, r)
        cost = float(r.get("recorded_cost_usd") or 0.0) or est.usd
        if r.get("recorded_cost_usd"):
            priced_from_log += 1
        p_label = _project_label(r["project_path"])
        s_key = r["session_id"] or r["source_path"]
        src = r["source"] or "(unknown)"
        day = r["day"]
        all_sessions.add(s_key)
        sessions_by_day[day].add(s_key)
        for bucket, kind, key in (
            (project_totals, "project", p_label),
            (source_totals, "source", src),
            (daily_totals, "day", day),
        ):
            bucket_sessions[(kind, key)].add(s_key)
            bucket[key]["cost"] += cost
            bucket[key]["input"] += int(r["input"])
            bucket[key]["output"] += int(r["output"])
        total_cost += cost
        cache_cost += est.cache_usd
        m = model_totals[model]
        m["input"] += int(r["input"])
        m["output"] += int(r["output"])
        m["cache_write"] += int(r["cache_write_5m"]) + int(r["cache_write_1h"])
        m["cache_read"] += int(r["cache_read"])
        m["calls"] += int(r["calls"])
        m["cost"] += cost
        m["known"] = float(est.known_model)
        if not est.known_model:
            unknown_cost += cost

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

    if not rows:
        warnings.append("No local agent sessions found in the window — is Claude Code used on this machine?")
    elif not project and not source and not project_path:
        recorded = _claude_session_count(period_start, period_end)
        if recorded is not None:
            theirs, until = recorded
            ours = len(set().union(*(ids for day, ids in sessions_by_day.items() if day <= until)) or set())
            if ours >= 5 and theirs >= 5 and abs(theirs - ours) / max(theirs, ours) > 0.10:
                warnings.append(
                    f"Session count differs from Claude Code's own record through {until} "
                    f"({ours} here vs {theirs} in its stats cache) — some transcripts may be missing or extra."
                )
    if priced_from_log:
        warnings.append(f"{priced_from_log} row(s) carried a cost recorded by the CLI and were not re-priced.")
    # no_data, not completed, on an empty window — same checklist vocabulary as
    # the insights phase's nothing-to-do case.
    price_status = "completed" if rows else "no_data"
    _emit(on_progress, "price", price_status, label="Price usage", detail=f"{len(files)} transcript(s)")

    from yeaboi.agentwatch.billing import detect_billing

    return AgentUsageReport(
        period_start=period_start,
        period_end=period_end,
        session_count=len(all_sessions),
        total_cost_usd=round(total_cost, 4),
        total_input_tokens=sum(r.input_tokens for r in by_model),
        total_output_tokens=sum(r.output_tokens for r in by_model),
        total_cache_write_tokens=sum(r.cache_write_tokens for r in by_model),
        total_cache_read_tokens=sum(r.cache_read_tokens for r in by_model),
        unknown_model_cost_share=round(unknown_cost / total_cost, 4) if total_cost > 0 else 0.0,
        pricing_as_of=PRICING_AS_OF,
        billing_kind=detect_billing().kind,
        cache_cost_share=round(cache_cost / total_cost, 4) if total_cost > 0 else 0.0,
        window_days=window_days,
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
    project_path: str = "",
    db_path=None,
    today: date | None = None,
    on_progress=None,
    dry_run: bool = False,
) -> AgentUsageReport:
    """Build the agent cost/usage dashboard over locally monitored sessions.

    Deterministic gather: refresh the collector's ingest, aggregate the stored
    per-day rollups over the window, and price every (day, model) row from the
    shared pricing table (``_deterministic_usage_report``). The single LLM
    call then writes ``insights`` and ``recommendations`` prose over the
    computed aggregates — never numbers.

    project: substring filter on the session's project label.
    source:  exact filter on the telemetry source (currently "claude_code").
    project_path: keep only sessions whose project directory is this absolute
        path or sits under it (a worktree counts) — see ``_in_repo``.
    dry_run: skip the LLM (deterministic artifact only, no warning).
    """
    resolved_today = today or datetime.now(timezone.utc).date()
    window_days = max(1, int(window_days))
    logger.info(
        "agent usage: %d-day window to %s (project=%r source=%r repo=%r dry_run=%s)",
        window_days,
        resolved_today.isoformat(),
        project,
        source,
        project_path,
        dry_run,
    )

    report = _deterministic_usage_report(
        window_days=window_days,
        project=project,
        source=source,
        project_path=project_path,
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
                "cache_cost_share": report.cache_cost_share,
            }
        )
        insights = fallback_insights
        recommendations = recommendations or fallback_recs

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
_SECRET_TITLES = {
    "tunnel-hostname": "Live tunnel hostname in a session transcript",
}
_SECRET_REMEDIATION = {
    "tunnel-hostname": "The quick-tunnel address was in a transcript; it expired with the tunnel.",
}


def _stored_findings(store: AgentWatchStore) -> list[SecurityFinding]:
    """Collector-persisted signals → one SecurityFinding per (category, pattern, file).

    The rows reference (pattern, file, line) only — the collector never stored
    the matched content, and neither does this report. A file that repeats
    the same signal on many lines is one finding with an occurrence count
    and the first line as its location; a CI log pasted with a token on
    forty lines is one thing to rotate, not forty.
    """
    from yeaboi.agentwatch import security_checks

    grouped: dict[tuple[str, str, str], dict] = {}
    for finding in store.list_findings():
        category = finding["category"]
        pattern = security_checks.canonical_label(finding["pattern"])
        severity = security_checks.severity_for(category, finding["pattern"], finding["severity"])
        key = (category, pattern, finding["source_path"])
        slot = grouped.setdefault(
            key,
            {"severity": severity, "line_no": int(finding["line_no"]), "count": 0, "session": finding["session_id"]},
        )
        slot["count"] += 1
        if security_checks.SEVERITY_ORDER.get(severity, 9) < security_checks.SEVERITY_ORDER.get(slot["severity"], 9):
            slot["severity"] = severity
        slot["line_no"] = min(slot["line_no"], int(finding["line_no"]))

    rows: list[SecurityFinding] = []
    for (category, pattern, source_path), slot in grouped.items():
        title = _SECRET_TITLES.get(pattern) or _STORED_FINDING_TITLES.get(category, "Session security signal")
        remediation = _SECRET_REMEDIATION.get(pattern) or _STORED_FINDING_REMEDIATION.get(category, "")
        detail = f"{slot['count']} matching line(s)" if slot["count"] > 1 else ""
        if slot["session"]:
            detail = f"{detail}; session {slot['session']}" if detail else f"session {slot['session']}"
        rows.append(
            SecurityFinding(
                severity=slot["severity"],
                category=category,
                title=title,
                location=source_path,
                line_no=slot["line_no"],
                pattern=pattern,
                detail=detail,
                remediation=remediation,
                occurrences=slot["count"],
            )
        )
    return rows


def _pattern_totals(findings: list[SecurityFinding]) -> tuple[tuple[str, str], ...]:
    """(pattern, "N matches across M files") for every transcript pattern present."""
    per_pattern: dict[str, tuple[int, set[str]]] = {}
    for f in findings:
        if f.category not in ("secret", "risky_tool"):
            continue
        count, files = per_pattern.get(f.pattern, (0, set()))
        files = set(files)
        files.add(f.location)
        per_pattern[f.pattern] = (count + f.occurrences, files)
    return tuple(
        (pattern, f"{count} match(es) across {len(files)} file(s)")
        for pattern, (count, files) in sorted(per_pattern.items(), key=lambda kv: -kv[1][0])
    )


def _previous_keys(store: AgentWatchStore) -> set[str] | None:
    """The finding keys of the last saved security report, or None on a first run."""
    from yeaboi.agentwatch import security_checks

    previous = store.latest_report("security")
    if not previous:
        return None
    stored_keys = previous["report"].get("finding_keys")
    if isinstance(stored_keys, list):
        return {str(k) for k in stored_keys}
    keys: set[str] = set()
    for row in previous["report"].get("findings") or ():
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "")
        if not key:
            key = security_checks.finding_key(
                SecurityFinding(
                    category=str(row.get("category", "")),
                    pattern=security_checks.canonical_label(str(row.get("pattern", ""))),
                    location=str(row.get("location", "")),
                )
            )
        keys.add(key)
    return keys


def _deterministic_security_report(
    *,
    scan_date: str,
    deep: bool = False,
    include_info: bool = False,
    db_path=None,
    on_progress=None,
    roots=None,
) -> AgentSecurityReport:
    """Everything in the security pipeline up to (not including) the LLM.

    Scan (deep forgets the cursors first), group the stored findings, audit the
    settings files, inventory the MCP servers, drop what the user dismissed,
    hide ``info`` unless asked, diff against the previous report, rank and
    score — returns the report with empty ``summary``/``recommendations``/
    ``generated_at`` for the caller to fill.
    """
    from yeaboi.agentwatch import dismissals, security_checks

    warnings: list[str] = []
    scan_label = "Re-scan every transcript" if deep else "Scan transcripts"
    _emit(on_progress, "scan", "running", label=scan_label)
    with AgentWatchStore(_resolve_db_path(db_path)) as store:
        if deep:
            store.reset_cursors()
        stats = collector.refresh(store, roots=roots, on_progress=on_progress, scan_security=True)
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
        previous_keys = _previous_keys(store)

    _emit(on_progress, "settings", "running", label="Audit settings")
    settings_findings = security_checks.audit_settings()
    findings.extend(settings_findings)
    _emit(on_progress, "settings", "completed", label="Audit settings", detail=f"{len(settings_findings)} finding(s)")
    _emit(on_progress, "mcp", "running", label="Inventory MCP servers")
    mcp_servers, mcp_findings = security_checks.inventory_mcp()
    findings.extend(mcp_findings)
    _emit(on_progress, "mcp", "completed", label="Inventory MCP servers", detail=f"{len(mcp_servers)} server(s)")

    keyed = [replace(f, key=security_checks.finding_key(f)) for f in findings]
    dismissed = dismissals.active(scan_date)
    kept = [f for f in keyed if f.key not in dismissed]
    dismissed_count = len(keyed) - len(kept)
    visible = [f for f in kept if include_info or f.severity != "info"]
    hidden_info = len(kept) - len(visible)

    ranked = security_checks.rank_findings(visible)
    current_keys = {f.key for f in kept}
    if previous_keys is None:
        new_keys: tuple[str, ...] = ()
        resolved_keys: tuple[str, ...] = ()
    else:
        new_keys = tuple(sorted(current_keys - previous_keys))
        resolved_keys = tuple(sorted(previous_keys - current_keys - set(dismissed)))
    posture_basis = tuple(f for f in kept if f.severity != "info")
    return AgentSecurityReport(
        scan_date=scan_date,
        posture=security_checks.compute_posture(posture_basis),
        sessions_scanned=sessions_scanned,
        files_scanned=files_scanned,
        secrets_found=len({(f.pattern, f.location) for f in posture_basis if f.category == "secret"}),
        findings=ranked,
        mcp_servers=tuple(mcp_servers),
        settings_flags=tuple(sorted({f.pattern for f in ranked if f.category == "settings" and f.severity != "info"})),
        finding_keys=tuple(sorted(current_keys)),
        new_findings=new_keys,
        resolved_findings=resolved_keys,
        dismissed_count=dismissed_count,
        hidden_info_count=hidden_info,
        posture_reason=security_checks.posture_reason(posture_basis),
        pattern_totals=_pattern_totals(kept),
        warnings=tuple(warnings),
    )


def run_agent_security(
    *,
    deep: bool = False,
    include_info: bool = False,
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
    re-scanned rather than only new/changed files. include_info=True lists
    the informational findings the report otherwise only counts.
    """
    resolved_today = today or datetime.now(timezone.utc).date()
    scan_date = resolved_today.isoformat()
    logger.info("agent security: scan %s (deep=%s include_info=%s dry_run=%s)", scan_date, deep, include_info, dry_run)

    report = _deterministic_security_report(
        scan_date=scan_date, deep=deep, include_info=include_info, db_path=db_path, on_progress=on_progress
    )

    warnings = list(report.warnings)
    ranked = report.findings
    mcp_servers = report.mcp_servers
    sessions_scanned = report.sessions_scanned
    posture = report.posture

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

    report = replace(
        report,
        summary=summary,
        recommendations=recommendations,
        warnings=tuple(warnings),
        generated_at=datetime.now(timezone.utc).isoformat(),
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
        "agent security: %s — %d finding(s) (%d dismissed, %d info hidden), %d MCP server(s)",
        report.posture,
        len(report.findings),
        report.dismissed_count,
        report.hidden_info_count,
        len(report.mcp_servers),
    )
    return report
