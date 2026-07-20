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

logger = logging.getLogger(__name__)

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
    # See README: "Local Mode (Ollama)" — reliability layer.
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
        warnings=tuple(warnings),
        generated_at=generated_at,
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _validate_window_dates(window_start: str, window_end: str) -> None:
    """Fail fast with a friendly message instead of a deep strptime error —
    the window strings arrive verbatim from the CLI flags and the MCP tool."""
    for name, value in (("window_start", window_start), ("window_end", window_end)):
        if not value:
            continue
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD) — got {value!r}") from None
    if window_start and window_end and window_end < window_start:
        raise ValueError(f"window_end ({window_end}) is before window_start ({window_start})")


def run_delivery_report(
    period: str = activity_mod.PERIOD_LAST_MONTH,
    *,
    session_id: str = "",
    jira_project: str = "",
    azdo_project: str = "",
    db_path=None,
    today: date | None = None,
    window_start: str = "",
    window_end: str = "",
    sprint_names: tuple[str, ...] = (),
    period_label_override: str = "",
) -> DeliveryReport:
    """Generate a business-friendly delivery report for ``period``.

    Gathers the team's completed tickets over the window, computes headline metrics,
    then runs one LLM "design" call to write the executive narrative, group the work
    into outcome themes, and pick section emojis. Persists + auto-exports the report.

    Args:
        period: PERIOD_LAST_SPRINT / PERIOD_LAST_MONTH / PERIOD_QUARTER.
        session_id: session to pull sprint length / project name from (best-effort).
        window_start / window_end: explicit ISO date range (quarter report) — the
            date span of the sprints the user selected. When ``window_start`` is set
            the look-back window is derived from it instead of ``period``.
        sprint_names: the sprint names that make up a quarter report (for framing).
        period_label_override: label to show for a quarter report (e.g. "Q3 2026").
    """
    _validate_window_dates(window_start, window_end)
    today = today or date.today()
    period_end = today.isoformat()
    db_path = _resolve_db_path(db_path)
    is_quarter = period == activity_mod.PERIOD_QUARTER and bool(window_start)
    period_label = period_label_override or activity_mod.PERIOD_LABELS.get(period, "Last month (~2 sprints)")
    logger.info("run_delivery_report: period=%s session=%s quarter=%s", period, session_id, is_quarter)

    state = _load_state(session_id, db_path)
    project_name = str(state.get("project_name", "") or "")

    passed_sprint_names = tuple(sprint_names)
    warnings: list[str] = []
    if is_quarter:
        # Quarter: the selected sprints define the date window; report over that span.
        try:
            days = max(1, (today - date.fromisoformat(window_start)).days)
        except (TypeError, ValueError):
            days = activity_mod.period_days(activity_mod.PERIOD_LAST_MONTH)
        period_start = window_start
        period_end = window_end or period_end
        items, _sprint_list, warnings = activity_mod.gather_delivered_work(
            period, state=state, jira_project=jira_project, azdo_project=azdo_project, days_override=days
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
            period, state=state, jira_project=jira_project, azdo_project=azdo_project
        )
        sprint_names = tuple(sprint_list)

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
        )
    else:
        from yeaboi.prompts.reporting import get_delivery_report_prompt

        prompt = get_delivery_report_prompt(
            delivered_items=[asdict(i) for i in items],
            project_name=project_name,
            period_label=period_label,
            sprint_names=list(sprint_names),
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
                warnings=tuple(warnings),
                generated_at=period_end,
            )

    with _store(db_path) as store:
        store.record_run(report, session_id=session_id)

    _export(report)
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


def _export(report: DeliveryReport) -> None:
    """Auto-export the report to Markdown + HTML + slide deck; swallow any I/O error."""
    try:
        from yeaboi.reporting import export

        export.export_report(report)
    except Exception as e:  # noqa: BLE001 — export is best-effort
        logger.warning("reporting export failed: %s", e)
