"""Weekly Review engine — a solo developer's review of their own week.

A standalone pipeline (NOT a LangGraph node), the same shape as the reporting
engine: one deterministic gather over the user's own standups, delivered work
and sprint plan, then a single LLM call for the prose, following the parse →
fallback → format convention the graph nodes use. The "on track vs your plan"
line is computed without the model, so it can never invent a match. An LLM
failure becomes a warning and a plain review, never a crash.

Carry-forward follows the retro rule: last week's actions arrive on this
review as ``carried_actions`` with the statuses marked *now*, and the review
that created them stays an append-only record.

Pipeline (run_weekly_review):
  resolve scope → week window → own standups (standup dep) → delivered tickets
  → plan + current sprint (plan dep) → deterministic verdict → carried actions
  → LLM prose → WeeklyReview → store + Markdown export

# See docs: "The ReAct Loop" — using the LLM outside the main graph
# See docs: "Prompt Construction" — the weekly review prompt
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from yeaboi.agent.state import DeliveredItem, ReviewAction, WeeklyReview
from yeaboi.timeparse import parse_date

logger = logging.getLogger(__name__)

#: Progress phase ids, in order — a surface's checklist keys on these.
PHASES: tuple[str, ...] = ("scope", "standups", "plan", "delivery", "carried", "model", "save")

ACTION_STATUSES = ("pending", "done", "dropped", "carried")
#: Statuses that keep an action alive into the next review.
OPEN_STATUSES = ("pending", "carried")

_MAX_LIST = 6


def _emit(on_progress: Callable[[str], None] | None, phase: str) -> None:
    if on_progress is None:
        return
    try:
        on_progress(phase)
    except Exception:  # noqa: BLE001 — progress is cosmetic
        logger.debug("weekly review: on_progress callback failed", exc_info=True)


def _resolve_db_path(db_path) -> Path:
    if db_path is not None:
        return Path(db_path)
    from yeaboi.paths import get_db_path

    return Path(get_db_path())


def _attr(obj: object, name: str, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ---------------------------------------------------------------------------
# Week window
# ---------------------------------------------------------------------------


def _week_window(week_end: str, today: date) -> tuple[date, date, str]:
    """``(monday, end, label)`` for the week ``week_end`` (or today) falls in."""
    end = today
    if week_end:
        from yeaboi.timeparse import parse_date

        try:
            parsed = parse_date(week_end)
        except ValueError:
            parsed = None
        if parsed is None:
            raise ValueError(f"week_end must be an ISO date, got {week_end!r}")
        end = parsed
    monday = end - timedelta(days=end.weekday())
    iso = end.isocalendar()
    return monday, end, f"{iso[0]}-W{iso[1]:02d}"


# ---------------------------------------------------------------------------
# Gather steps — each best-effort, each a warning rather than a failure
# ---------------------------------------------------------------------------


def _own_update(report):
    own = next((m for m in report.member_updates if m.name == report.my_name), None)
    if own is None and report.member_updates:
        own = report.member_updates[0]
    return own


def _standups(scope, path: Path, monday: date, end: date, warnings: list[str]) -> dict:
    """The user's own standup lines for the week, oldest first, plus sprint context."""
    from yeaboi.projects.scope import wants

    if not wants(scope, "standup"):
        warnings.append("standup context is switched off for this run")
        return {}
    try:
        from yeaboi.standup.insights import yesterday_context
        from yeaboi.standup.store import StandupStore

        session_ids = scope.session_ids if scope is not None else None
        lo, hi = monday.isoformat(), end.isoformat()
        with StandupStore(path) as store:
            rows = store.get_all_history(limit=60, session_ids=session_ids)
            picked = [r for r in rows if r.get("status") == "success" and lo <= str(r.get("standup_date", "")) <= hi]
            reports = [store.get_run_by_id(int(r["id"])) for r in picked]
        reports = sorted((r for r in reports if r is not None), key=lambda r: r.date)
        if not reports:
            return {}
        lines: list[str] = []
        dates: list[str] = []
        blockers: list[str] = []
        my_name = ""
        for report in reports:
            own = _own_update(report)
            ctx = yesterday_context(report).get(own.name, {}) if own is not None else {}
            my_name = my_name or report.my_name
            summary = ctx.get("summary", "") or "no update recorded"
            blocked = ctx.get("blockers", "")
            day = parse_date(report.date).strftime("%a") if report.date else ""
            lines.append(f"{day} {report.date}: {summary}" + (f" — blocked: {blocked}" if blocked else ""))
            dates.append(report.date)
            if blocked:
                blockers.append(blocked)
        last = reports[-1]
        logger.info("weekly review: %d standup(s) in %s..%s", len(reports), lo, hi)
        return {
            "standup_dates": tuple(dates),
            "standup_lines": tuple(lines),
            "confidence_start": int(reports[0].confidence_pct),
            "confidence_end": int(last.confidence_pct),
            "confidence_label": last.confidence_label,
            "sprint_name": last.sprint_name,
            "sprint_day": int(last.sprint_day),
            "sprint_total_days": int(last.sprint_total_days),
            "my_name": my_name,
            "_blockers": blockers,
        }
    except Exception as e:  # noqa: BLE001 — a broken store is a warning, not a failed review
        logger.warning("weekly review: standup read failed: %s", e)
        warnings.append("could not read this week's standups")
        return {}


def _delivered(
    plan_state: dict, monday: date, end: date, my_name: str, warnings: list[str], on_progress
) -> tuple[DeliveredItem, ...]:
    """Tickets closed in the window, narrowed to the user when the tracker names them."""
    try:
        from yeaboi.reporting import activity

        items, _sprints, warns = activity.gather_delivered_work(
            activity.PERIOD_WINDOW,
            state=plan_state,
            days_override=(end - monday).days + 1,
            window_start=monday.isoformat(),
            window_end=end.isoformat(),
            on_progress=on_progress,
        )
        warnings.extend(warns)
    except Exception as e:  # noqa: BLE001
        logger.warning("weekly review: delivered-work gather failed: %s", e)
        warnings.append("could not read delivered work from the tracker")
        return ()
    if my_name:
        mine = [i for i in items if i.assignee.strip().lower() == my_name.strip().lower()]
        if mine:
            logger.info("weekly review: %d/%d delivered item(s) assigned to %s", len(mine), len(items), my_name)
            return tuple(mine)
    return tuple(items)


def _plan(scope, path: Path, today: date, warnings: list[str]) -> tuple[dict, str, int, str]:
    """``(state, session_id, planned_story_count, sprint_name)`` for the current sprint."""
    from yeaboi.projects.scope import latest_planning_state, wants
    from yeaboi.solo.today import current_sprint, sprint_story_ids

    if not wants(scope, "plan"):
        warnings.append("plan context is switched off for this run")
        return {}, "", 0, ""
    try:
        scoped = latest_planning_state(scope, db_path=path)
        if scoped is not None:
            session_id, state = scoped
        elif scope is not None and scope.project_id:
            return {}, "", 0, ""  # a project with no plan has no plan
        else:
            from yeaboi.ship.plans import latest_plan_with_work

            found = latest_plan_with_work(db_path=path)
            if found is None:
                return {}, "", 0, ""
            state, session_id, _name = found
        sprint = current_sprint(state, today)
        if sprint is None:
            return state, session_id, 0, ""
        return state, session_id, len(sprint_story_ids(sprint)), str(_attr(sprint, "name", "") or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("weekly review: plan read failed: %s", e)
        warnings.append("could not read the sprint plan")
        return {}, "", 0, ""


def _plan_verdict(
    *,
    has_plan: bool,
    has_standups: bool,
    confidence_label: str,
    confidence_start: int,
    confidence_end: int,
    sprint_name: str,
    sprint_day: int,
    sprint_total_days: int,
    delivered: int,
    planned: int,
) -> tuple[str, str]:
    """``(plan_status, plan_line)`` — deterministic, from numbers the standups stored."""
    from yeaboi.standup.confidence import LABEL_AT_RISK, LABEL_BEHIND, LABEL_ON_TRACK

    if not has_plan:
        return "no_plan", "No sprint plan on file — nothing to measure against."
    if not has_standups:
        return "no_data", "No standups this week — run one so next week's review has something to measure."
    status = {LABEL_ON_TRACK: "on_track", LABEL_AT_RISK: "at_risk", LABEL_BEHIND: "behind"}.get(
        confidence_label, "no_data"
    )
    delta = confidence_end - confidence_start
    trend = f"up {delta}" if delta > 0 else f"down {-delta}" if delta < 0 else "flat"
    where = f"Day {sprint_day}/{sprint_total_days} of {sprint_name or 'the sprint'}"
    label = confidence_label or "No confidence figure"
    counts = f"{delivered} ticket{'s' if delivered != 1 else ''} closed against {planned} planned"
    return status, f"{where} · {label} ({confidence_end}%, {trend} since Monday) · {counts}"


# ---------------------------------------------------------------------------
# Carry-forward
# ---------------------------------------------------------------------------


def carried_actions(scope, *, db_path: Path | None = None) -> tuple[ReviewAction, ...]:
    """Last review's actions still worth tracking, reset to pending carry-overs.

    Source = the previous review's new actions plus whatever *it* carried and
    left open — the retro rule, so an action marked "carried" twice does not
    vanish. Deduplicated by text, ids kept so a surface can mark them.
    ``scope`` narrows to the project's own reviews; ``None`` reads the newest.
    Never raises.
    """
    from dataclasses import replace

    try:
        from yeaboi.solo.store import WeeklyReviewStore

        path = _resolve_db_path(db_path)
        if not path.exists():
            return ()
        with WeeklyReviewStore(path) as store:
            previous = store.get_latest_report(session_ids=scope.session_ids if scope is not None else None)
    except Exception as e:  # noqa: BLE001
        logger.warning("weekly review: could not read the previous review: %s", e)
        return ()
    if previous is None:
        return ()
    kept_open = [a for a in previous.carried_actions if a.status in OPEN_STATUSES]
    seen: set[str] = set()
    combined: list[ReviewAction] = []
    for action in (*previous.actions, *kept_open):
        key = action.text.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        combined.append(replace(action, origin="carryover", status="pending"))
    logger.info("weekly review: %d carried action(s) from %s", len(combined), previous.week_label)
    return tuple(combined)


def _apply_statuses(
    carried: tuple[ReviewAction, ...], statuses: dict[str, str] | None, warnings: list[str]
) -> tuple[ReviewAction, ...]:
    from dataclasses import replace

    if not statuses:
        return carried
    by_id = {a.id: a for a in carried}
    marked = dict(by_id)
    for action_id, status in statuses.items():
        if action_id not in by_id:
            warnings.append(f"no carried action with id {action_id!r}")
            continue
        if status not in ACTION_STATUSES:
            warnings.append(f"unknown status {status!r} for action {action_id!r}")
            continue
        marked[action_id] = replace(by_id[action_id], status=status)
    return tuple(marked[a.id] for a in carried)


# ---------------------------------------------------------------------------
# LLM — parse → fallback
# ---------------------------------------------------------------------------


def _parse_json_response(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]
    try:
        parsed = json.loads(raw.strip())
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        logger.warning("weekly review: could not parse LLM JSON response")
        return {}


def _str_list(value, limit: int = _MAX_LIST) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v).strip() for v in value if str(v).strip())[:limit]


def _invoke_llm(prompt: str) -> tuple[dict, list[str]]:
    """One LLM call; ``({}, [warning])`` on any failure so the caller falls back."""
    from yeaboi.config import is_llm_configured

    configured, why = is_llm_configured()
    if not configured:
        logger.warning("weekly review: LLM not configured (%s)", why)
        return {}, [f"AI review unavailable — {why}. Showing a plain review."]

    # invoke_json tracks usage, turns on JSON mode and re-asks once on bad JSON.
    from yeaboi.agent.llm import invoke_json
    from yeaboi.agent.nodes import _is_llm_auth_or_billing_error, _local_llm_hint

    try:
        logger.info("weekly review: invoking LLM")
        response = invoke_json(prompt, temperature=0.3)
        return _parse_json_response(response.content), []
    except Exception as exc:  # noqa: BLE001 — any LLM failure is a warning + fallback
        if _is_llm_auth_or_billing_error(exc):
            logger.warning("weekly review: LLM auth/billing error: %s", exc)
            return {}, ["AI review unavailable — API key invalid or billing issue. Showing a plain review."]
        hint = _local_llm_hint(exc)
        if hint:
            logger.warning("weekly review: local Ollama failure: %s", exc)
            return {}, [f"AI review unavailable — {hint} Showing a plain review."]
        logger.warning("weekly review: LLM request failed: %s", exc)
        return {}, ["AI review unavailable — LLM request failed (see logs). Showing a plain review."]


def _new_action(text: str, week_label: str, origin: str = "ai") -> ReviewAction:
    return ReviewAction(id=uuid.uuid4().hex[:12], text=text, status="pending", origin=origin, week_label=week_label)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def run_weekly_review(
    *,
    session_id: str = "",
    project_id: str = "",
    context_deps: list[str] | None = None,
    week_end: str = "",
    carried_statuses: dict[str, str] | None = None,
    dry_run: bool = False,
    db_path: Path | None = None,
    today: date | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> WeeklyReview:
    """Review the week ending ``week_end`` (default today) and store the result.

    Args:
        session_id / project_id: the scope — an explicit project wins, else the
            session's linked project; blank reads the newest of everything.
        context_deps: per-run context toggles (``standup`` and ``plan`` gate the
            reads here); ``None`` inherits, ``[]`` is incognito.
        week_end: ISO date inside the week under review; the window is that
            week's Monday through this date.
        carried_statuses: ``{action_id: status}`` marks for last review's
            actions, recorded on this review's ``carried_actions``.
        dry_run: skip the tracker and the LLM — the deterministic review only.
        on_progress: receives each phase id from ``PHASES`` as it starts.
    """
    from yeaboi.projects.scope import resolve_scope

    path = _resolve_db_path(db_path)
    today = today or date.today()
    warnings: list[str] = []
    logger.info(
        "run_weekly_review: session=%s project=%s week_end=%s dry_run=%s",
        session_id or "-",
        project_id or "-",
        week_end or "today",
        dry_run,
    )

    _emit(on_progress, "scope")
    scope = resolve_scope(project_id, session_id, context_deps=context_deps, db_path=path)
    monday, end, week_label = _week_window(week_end, today)
    pid = scope.project_id if scope is not None else project_id
    project_name = _project_name(pid, path)

    _emit(on_progress, "standups")
    standup = _standups(scope, path, monday, end, warnings) if path.exists() else {}
    blockers: list[str] = standup.pop("_blockers", [])
    my_name = standup.get("my_name") or _configured_name()

    _emit(on_progress, "plan")
    plan_state, plan_session, planned, plan_sprint = (
        _plan(scope, path, end, warnings) if path.exists() else ({}, "", 0, "")
    )

    _emit(on_progress, "delivery")
    if dry_run:
        delivered: tuple[DeliveredItem, ...] = ()
        warnings.append("dry run — the tracker was not read")
    else:
        delivered = _delivered(plan_state, monday, end, my_name, warnings, on_progress)

    plan_status, plan_line = _plan_verdict(
        has_plan=bool(plan_state),
        has_standups=bool(standup),
        confidence_label=standup.get("confidence_label", ""),
        confidence_start=standup.get("confidence_start", 0),
        confidence_end=standup.get("confidence_end", 0),
        sprint_name=standup.get("sprint_name") or plan_sprint,
        sprint_day=standup.get("sprint_day", 0),
        sprint_total_days=standup.get("sprint_total_days", 0),
        delivered=len(delivered),
        planned=planned,
    )

    _emit(on_progress, "carried")
    carried = _apply_statuses(carried_actions(scope, db_path=path), carried_statuses, warnings)

    _emit(on_progress, "model")
    parsed: dict = {}
    if dry_run:
        warnings.append("dry run — the AI review was skipped")
    else:
        from yeaboi.prompts.weekly_review import get_weekly_review_prompt

        prompt = get_weekly_review_prompt(
            week_label=week_label,
            standup_lines=standup.get("standup_lines", ()),
            delivered_titles=[f"{i.key} {i.title}".strip() for i in delivered],
            plan_line=plan_line,
            carried_open=[a.text for a in carried if a.status in OPEN_STATUSES],
            carried_done=[a.text for a in carried if a.status == "done"],
        )
        parsed, llm_warnings = _invoke_llm(prompt)
        warnings.extend(llm_warnings)

    summary = str(parsed.get("summary", "")).strip()
    went_well = _str_list(parsed.get("went_well"))
    to_change = _str_list(parsed.get("to_change"))
    actions = tuple(_new_action(t, week_label) for t in _str_list(parsed.get("actions")))
    if not summary:
        # Deterministic fallback: the verdict is the story, evidence stands in for prose.
        summary = plan_line
        went_well = went_well or tuple(f"{i.key} {i.title}".strip() for i in delivered[:5])
        to_change = to_change or tuple(dict.fromkeys(blockers))[:5]
        logger.info("weekly review: deterministic fallback (no LLM summary)")

    review = WeeklyReview(
        week_label=week_label,
        week_start=monday.isoformat(),
        week_end=end.isoformat(),
        project_id=pid,
        project_name=project_name,
        session_id=session_id or plan_session,
        my_name=my_name,
        standup_dates=standup.get("standup_dates", ()),
        standup_lines=standup.get("standup_lines", ()),
        confidence_start=standup.get("confidence_start", 0),
        confidence_end=standup.get("confidence_end", 0),
        confidence_label=standup.get("confidence_label", ""),
        sprint_name=standup.get("sprint_name") or plan_sprint,
        sprint_day=standup.get("sprint_day", 0),
        sprint_total_days=standup.get("sprint_total_days", 0),
        delivered_items=delivered,
        planned_story_count=planned,
        plan_status=plan_status,
        plan_line=plan_line,
        summary=summary,
        went_well=went_well,
        to_change=to_change,
        actions=actions,
        carried_actions=carried,
        warnings=tuple(warnings),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )

    _emit(on_progress, "save")
    _save(review, path)
    logger.info(
        "run_weekly_review: done week=%s status=%s delivered=%d actions=%d carried=%d warnings=%d",
        week_label,
        plan_status,
        len(delivered),
        len(actions),
        len(carried),
        len(warnings),
    )
    return review


def _configured_name() -> str:
    from yeaboi.config import get_standup_user_name

    return get_standup_user_name()


def _project_name(project_id: str, path: Path) -> str:
    if not project_id or not path.exists():
        return ""
    try:
        from yeaboi.projects.store import ProjectStore

        with ProjectStore(path) as store:
            row = store.get(project_id)
        return str((row or {}).get("name") or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("weekly review: project lookup failed: %s", e)
        return ""


def _save(review: WeeklyReview, path: Path) -> None:
    from yeaboi.solo.store import WeeklyReviewStore

    with WeeklyReviewStore(path) as store:
        store.record_run(review)
    try:
        from yeaboi.solo.export import export_weekly_review

        export_weekly_review(review)
    except Exception as e:  # noqa: BLE001 — export is best-effort
        logger.warning("weekly review export failed: %s", e)
