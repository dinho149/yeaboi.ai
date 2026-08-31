"""Reporting engine — the business-friendly delivery report pipeline.

Like the standup / performance engines, this is a standalone pipeline (NOT a
LangGraph node): one deterministic gather step + a single LLM "design" call
following the same parse → fallback → format convention the graph nodes use
(agent/nodes.py). An LLM auth/billing failure is never re-raised — it becomes a
user-facing *warning* and a deterministic fallback report, so the page always
renders something useful.

Pipeline:
  run_delivery_report(period) → gather completed tickets → metrics (deterministic)
                              → LLM narrative + themes + emoji (design pass)
                              → DeliveryReport → store + export (md / html / slides)

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the reporting prompt
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import asdict
from datetime import date, timedelta

from yeaboi.agent.state import DeliveredItem, DeliveryReport
from yeaboi.reporting import activity as activity_mod
from yeaboi.timeparse import parse_date

logger = logging.getLogger(__name__)


class ReportCancelledError(RuntimeError):
    """Raised when the caller's ``cancel_event`` is set mid-pipeline (cooperative cancel)."""


def _emit(on_progress, message: str) -> None:
    """Best-effort progress callback — a broken callback must never kill the pipeline."""
    if on_progress is None:
        return
    try:
        on_progress(message)
    except Exception:  # noqa: BLE001 — progress is cosmetic
        logger.debug("reporting: on_progress callback failed", exc_info=True)


def _check_cancel(cancel_event) -> None:
    """Raise ReportCancelledError when the caller has asked us to stop (between stages)."""
    if cancel_event is not None and cancel_event.is_set():
        logger.info("run_delivery_report: cancelled by caller")
        raise ReportCancelledError("report generation cancelled")


# Deterministic emoji fallback — used when the LLM is unavailable or omits a slot.
_DEFAULT_EMOJI = {
    "headline": "🚀",
    "summary": "📋",
    "metrics": "📊",
    "themes": "🧩",
    "highlights": "⭐",
    "thanks": "🙌",
}


# ---------------------------------------------------------------------------
# Shared LLM helpers (parse → fallback) — mirrors performance/engine.py
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
        logger.warning("reporting: could not parse LLM JSON response")
        return {}


def _str_list(value) -> tuple[str, ...]:
    """Coerce an LLM field into a tuple of clean strings (tolerant of bad shapes)."""
    if not isinstance(value, list):
        return ()
    return tuple(str(v).strip() for v in value if str(v).strip())


def _parse_themes(value) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Coerce the LLM 'themes' field into ((title, (outcome, ...)), ...)."""
    if not isinstance(value, list):
        return ()
    themes: list[tuple[str, tuple[str, ...]]] = []
    for t in value:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title", "")).strip()
        outcomes = _str_list(t.get("outcomes"))
        if title and outcomes:
            themes.append((title, outcomes))
    return tuple(themes)


def _parse_emoji(value) -> tuple[tuple[str, str], ...]:
    """Coerce the LLM 'emoji_theme' dict into ((slot, emoji), ...), defaulting slots."""
    picked = dict(_DEFAULT_EMOJI)
    if isinstance(value, dict):
        for slot in _DEFAULT_EMOJI:
            v = str(value.get(slot, "")).strip()
            if v:
                picked[slot] = v
    return tuple(picked.items())


def _invoke_llm(prompt: str) -> tuple[dict, list[str]]:
    """Run one LLM call for ``prompt``; return (parsed_json, warnings).

    Returns ({}, [warning]) on any non-configured / auth / request failure so the
    caller can fall back deterministically — the engine never crashes on LLM issues.
    """
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("reporting: LLM not configured (%s)", why)
        return {}, [f"AI narrative unavailable — {why}. Showing a plain summary."]

    # invoke_json tracks usage + turns on JSON mode + re-asks once on bad JSON.
    # See docs: "Local Mode (Ollama)" — reliability layer.
    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint

    try:
        logger.info("reporting: invoking LLM design pass")
        response = invoke_json(prompt, temperature=0.3)
        return _parse_json_response(response.content), []
    except Exception as exc:  # noqa: BLE001 — turn any LLM failure into a warning + fallback
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("reporting: LLM auth/billing error: %s", exc)
            return {}, ["AI narrative unavailable — API key invalid or billing issue. Showing a plain summary."]
        local_hint = _local_llm_hint(exc)
        if local_hint:
            logger.warning("reporting: local Ollama failure: %s", exc)
            return {}, [f"AI narrative unavailable — {local_hint} Showing a plain summary."]
        logger.warning("reporting: LLM request failed: %s", exc)
        return {}, ["AI narrative unavailable — LLM request failed (see logs). Showing a plain summary."]


def _load_state(session_id: str, db_path) -> dict:
    """Best-effort load of a session's ScrumState (for sprint length + project name)."""
    if not session_id:
        return {}
    try:
        from yeaboi.sessions import SessionStore

        with SessionStore(db_path) as sessions:
            return sessions.load_state(session_id) or {}
    except Exception as e:  # noqa: BLE001 — state is optional
        logger.warning("reporting: could not load session state: %s", e)
        return {}


def _resolve_db_path(db_path):
    if db_path is not None:
        return db_path
    from yeaboi.paths import get_db_path

    return get_db_path()


# ---------------------------------------------------------------------------
# Metrics (deterministic)
# ---------------------------------------------------------------------------


def _compute_metrics(items: list[DeliveredItem]) -> tuple[tuple[str, str], ...]:
    """Derive headline metrics from the completed tickets (no LLM)."""
    if not items:
        return (("Items delivered", "0"),)
    by_source: Counter[str] = Counter(i.source for i in items if i.source)
    contributors = {i.assignee for i in items if i.assignee}
    metrics: list[tuple[str, str]] = [("Items delivered", str(len(items)))]
    if contributors:
        metrics.append(("Contributors", str(len(contributors))))
    _source_names = {"jira": "Jira", "azuredevops": "Azure DevOps"}
    for src, n in sorted(by_source.items()):
        metrics.append((f"From {_source_names.get(src, src)}", str(n)))
    return tuple(metrics)


# ---------------------------------------------------------------------------
# Fallback (deterministic) — evidence, not analysis
# ---------------------------------------------------------------------------


def _fallback_report(
    *,
    period_label: str,
    period_start: str,
    period_end: str,
    project_name: str,
    sprint_names: tuple[str, ...],
    items: list[DeliveredItem],
    metrics: tuple[tuple[str, str], ...],
    warnings: list[str],
    generated_at: str,
    supporting_signals: tuple = (),
    ops_signals: tuple = (),
) -> DeliveryReport:
    """Deterministic delivery report when the LLM is unavailable — counts + evidence."""
    n = len(items)
    headline = (
        f"{n} item{'s' if n != 1 else ''} delivered for {project_name or 'the product'} — {period_label.lower()}."
        if n
        else f"No completed work found for {project_name or 'the product'} in this period."
    )
    summary = (
        f"The team completed {n} tracked item{'s' if n != 1 else ''} during {period_label.lower()}. "
        "A written business narrative could not be generated automatically — the delivered items are listed below."
        if n
        else "No completed tickets were found in the selected window."
    )
    # One "Delivered work" theme listing the items so the deck/report never renders empty.
    outcomes = tuple(f"{i.key} {i.title}".strip() for i in items[:12])
    themes = ((f"Delivered work ({n})", outcomes),) if outcomes else ()
    return DeliveryReport(
        period_label=period_label,
        period_start=period_start,
        period_end=period_end,
        project_name=project_name,
        sprint_names=sprint_names,
        headline=headline,
        executive_summary=summary,
        themes=themes,
        highlights=outcomes[:5],
        metrics=metrics,
        delivered_items=tuple(items),
        emoji_theme=tuple(_DEFAULT_EMOJI.items()),
        supporting_signals=tuple(supporting_signals),
        ops_signals=tuple(ops_signals),
        warnings=tuple(warnings),
        generated_at=generated_at,
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _validate_window_dates(window_start: str, window_end: str) -> tuple[str, str]:
    """Fail fast with a friendly message instead of a deep strptime error —
    the window strings arrive verbatim from the CLI flags and the MCP tool.

    Returns them canonicalised. Everything downstream — the ordering check below,
    the ``day >= period_start`` filter in ``context.py`` and the period label —
    compares these as *strings*, and ISO-8601 has several spellings of the same
    day, so a valid ``20260818`` would validate and then order wrongly against a
    ``2026-08-18`` it should equal.
    """
    canonical = {}
    for name, value in (("window_start", window_start), ("window_end", window_end)):
        if not value:
            canonical[name] = value
            continue
        try:
            canonical[name] = parse_date(value).isoformat()
        except ValueError:
            raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD) — got {value!r}") from None
    start, end = canonical["window_start"], canonical["window_end"]
    if start and end and end < start:
        raise ValueError(f"window_end ({end}) is before window_start ({start})")
    return start, end


def run_delivery_report(
    period: str = activity_mod.PERIOD_LAST_MONTH,
    *,
    session_id: str = "",
    jira_project: str = "",
    azdo_project: str = "",
    project_id: str = "",
    context_deps: list[str] | None = None,
    db_path=None,
    today: date | None = None,
    window_start: str = "",
    window_end: str = "",
    sprint_names: tuple[str, ...] = (),
    period_label_override: str = "",
    theme: str = "midnight",
    sources: dict | None = None,
    on_progress=None,
    cancel_event=None,
) -> DeliveryReport:
    """Generate a business-friendly delivery report for ``period``.

    Gathers the team's completed tickets over the window, computes headline metrics,
    then runs one LLM "design" call to write the executive narrative, group the work
    into outcome themes, and pick section emojis. Persists + auto-exports the report.

    Args:
        period: one of activity's PERIOD_* constants (last_week / last_sprint /
            last_month / quarter / window).
        session_id: session to pull sprint length / project name from (best-effort).
        project_id: project to scope by ("" inherits the session's own link).
            A scoped report frames itself with the project's latest sprint
            plan instead of the session's own saved state.
        context_deps: context-source toggles for this run (see
            ``projects.scope.CONTEXT_DEP_TOKENS``). ``None`` inherits the
            project default; an empty list is an incognito run. The ``plan``
            dep gates the sprint-plan framing above.
        window_start / window_end: explicit ISO date range (quarter or custom-window
            report). When ``window_start`` is set the look-back window is derived
            from it instead of ``period``.
        sprint_names: the sprint names that make up a quarter report (for framing).
        period_label_override: label to show for a quarter report (e.g. "Q3 2026").
        theme: presentation palette name for the auto-export (built-in or a custom
            name from reporting_themes.json; unknown names fall back to midnight).
        sources: optional ``{"delivery": [...], "code": [...], "docs": [...]}``
            selection. Delivery restricts which tracker(s) completed tickets come
            from ("jira" / "azuredevops"; azdevops / azure_devops accepted as
            aliases); code/docs pick the supporting-context sources. ``None``
            means every configured source.
        on_progress: optional callable(str) receiving live status lines.
        cancel_event: optional ``threading.Event``; when set between stages the
            pipeline raises ``ReportCancelledError`` without persisting anything.
    """
    window_start, window_end = _validate_window_dates(window_start, window_end)
    delivery_sel, code_sel, docs_sel = activity_mod.normalize_sources(sources)
    today = today or date.today()
    period_end = today.isoformat()
    db_path = _resolve_db_path(db_path)
    # Any explicit start date defines the window — quarter sprint spans and the
    # TUI/CLI custom date range both flow through the same path.
    use_window = bool(window_start)
    if not period_label_override and period == activity_mod.PERIOD_WINDOW and window_start:
        period_label_override = f"{window_start} → {window_end or period_end}"
    period_label = period_label_override or activity_mod.PERIOD_LABELS.get(period, "Last month (~2 sprints)")
    logger.info("run_delivery_report: period=%s session=%s window=%s", period, session_id, use_window)

    _emit(on_progress, "Loading session state")
    state = _load_state(session_id, db_path)
    # Planning→reporting edge: a scoped run frames itself with the project's
    # latest sprint plan, mirroring run_standup.
    from yeaboi.projects.scope import latest_planning_state, resolve_scope

    scope = resolve_scope(project_id, session_id, context_deps=context_deps, db_path=db_path)
    if scope is not None and scope.wants("plan"):
        planned = latest_planning_state(scope, db_path=db_path)
        if planned is not None:
            logger.info("run_delivery_report: sprint framing from project %s plan %s", scope.project_id, planned[0])
            state = planned[1]
    project_name = str(state.get("project_name", "") or "")
    _check_cancel(cancel_event)

    passed_sprint_names = tuple(sprint_names)
    warnings: list[str] = []
    if use_window:
        # Explicit window: the selected sprints / custom dates define the date span.
        try:
            days = max(1, (today - parse_date(window_start)).days)
        except (TypeError, ValueError):
            days = activity_mod.period_days(activity_mod.PERIOD_LAST_MONTH)
        period_start = window_start
        period_end = window_end or period_end
        items, _sprint_list, warnings = activity_mod.gather_delivered_work(
            period,
            state=state,
            jira_project=jira_project,
            azdo_project=azdo_project,
            days_override=days,
            delivery_sources=delivery_sel,
            window_start=window_start,
            window_end=window_end,
            on_progress=on_progress,
        )
        sprint_names = passed_sprint_names
        # The recent-activity helpers cap at ~100 rows per source — be honest about it.
        warnings = warnings + ["Large periods may be truncated to the ~100 most recent completed items per source."]
    else:
        try:
            length_weeks = int(state.get("sprint_length_weeks") or 2)
        except (TypeError, ValueError):
            length_weeks = 2
        days = activity_mod.period_days(period, sprint_length_weeks=length_weeks)
        period_start = (today - timedelta(days=days)).isoformat()
        items, sprint_list, warnings = activity_mod.gather_delivered_work(
            period,
            state=state,
            jira_project=jira_project,
            azdo_project=azdo_project,
            delivery_sources=delivery_sel,
            on_progress=on_progress,
        )
        sprint_names = tuple(sprint_list)
    _check_cancel(cancel_event)

    # Supporting code/docs signals — reference context gathered even when zero
    # tickets closed ("activity happened but nothing shipped" is useful framing).
    # Auto selections only reach for what is actually configured.
    avail = activity_mod.available_report_sources()
    code_sel = [s for s in code_sel if s in avail["code"]]
    docs_sel = [s for s in docs_sel if s in avail["docs"]]
    supporting_signals: tuple = ()
    if code_sel or docs_sel:
        from yeaboi.reporting.context import gather_supporting_signals

        supporting_signals, signal_warnings = gather_supporting_signals(
            period_start=period_start,
            period_end=period_end,
            code_sources=code_sel,
            doc_sources=docs_sel,
            azdo_project=azdo_project,
            db_path=db_path,
            on_progress=on_progress,
        )
        warnings = warnings + signal_warnings
        _check_cancel(cancel_event)

    # What production did over the SAME window. Its own gather, its own field
    # and its own heading below: corroboration and consequence are different
    # claims, and joining them into one sentence would make an incident read as
    # evidence that the work landed. Nothing connected costs no network and
    # leaves every surface exactly as it was.
    from yeaboi.reporting.context import gather_ops_signals

    ops_signals, ops_warnings = gather_ops_signals(
        period_start=period_start,
        period_end=period_end,
        on_progress=on_progress,
    )
    warnings = warnings + ops_warnings
    _check_cancel(cancel_event)

    metrics = _compute_metrics(items)

    # No delivered work → skip the LLM entirely; the deterministic report is correct.
    if not items:
        report = _fallback_report(
            period_label=period_label,
            period_start=period_start,
            period_end=period_end,
            project_name=project_name,
            sprint_names=sprint_names,
            items=items,
            metrics=metrics,
            warnings=warnings,
            generated_at=period_end,
            supporting_signals=supporting_signals,
            ops_signals=ops_signals,
        )
    else:
        from yeaboi.prompts.reporting import get_delivery_report_prompt

        _check_cancel(cancel_event)
        _emit(on_progress, "Designing the report narrative (AI)…")
        prompt = get_delivery_report_prompt(
            delivered_items=[asdict(i) for i in items],
            project_name=project_name,
            period_label=period_label,
            sprint_names=list(sprint_names),
            supporting_signals=[asdict(s) for s in supporting_signals],
        )
        parsed, llm_warnings = _invoke_llm(prompt)
        warnings = warnings + llm_warnings

        if not parsed:
            report = _fallback_report(
                period_label=period_label,
                period_start=period_start,
                period_end=period_end,
                project_name=project_name,
                sprint_names=sprint_names,
                items=items,
                metrics=metrics,
                warnings=warnings,
                generated_at=period_end,
                supporting_signals=supporting_signals,
                ops_signals=ops_signals,
            )
        else:
            report = DeliveryReport(
                period_label=period_label,
                period_start=period_start,
                period_end=period_end,
                project_name=project_name,
                sprint_names=sprint_names,
                headline=(parsed.get("headline") or "").strip(),
                executive_summary=(parsed.get("executive_summary") or "").strip(),
                themes=_parse_themes(parsed.get("themes")),
                highlights=_str_list(parsed.get("highlights")),
                metrics=metrics,
                delivered_items=tuple(items),
                emoji_theme=_parse_emoji(parsed.get("emoji_theme")),
                supporting_signals=supporting_signals,
                ops_signals=ops_signals,
                warnings=tuple(warnings),
                generated_at=period_end,
            )

    _check_cancel(cancel_event)
    _emit(on_progress, "Saving & exporting…")
    with _store(db_path) as store:
        store.record_run(report, session_id=session_id)
        # Fetched AFTER record_run so this report is part of the volume trend.
        run_history = store.get_history(session_id, limit=30)

    _export(report, history=run_history, theme=theme)
    logger.info(
        "run_delivery_report complete: items=%d themes=%d warnings=%d",
        len(report.delivered_items),
        len(report.themes),
        len(report.warnings),
    )
    return report


def _store(db_path):
    from yeaboi.reporting.store import ReportingStore

    return ReportingStore(db_path)


def _export(report: DeliveryReport, *, history=(), theme: str = "midnight") -> None:
    """Auto-export the report to Markdown + HTML + slide deck (+ .pptx); swallow any I/O error."""
    try:
        from yeaboi.reporting import export
        from yeaboi.reporting.style import load_deck_style

        # The saved deck-style preferences (~/.yeaboi/data/reporting_prefs.json) are
        # resolved here — the one seam every auto-export (TUI generate, CLI, MCP
        # report_delivery) flows through — so builders stay disk-free and hermetic.
        export.export_report(report, history=history, theme=theme, style=load_deck_style())
    except Exception as e:  # noqa: BLE001 — export is best-effort
        logger.warning("reporting export failed: %s", e)
