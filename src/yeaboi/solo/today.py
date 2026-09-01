"""The Solo welcome's "where am I" snapshot.

One frozen dataclass, built once when the Solo menu is entered and read by
two surfaces: the terminal's Today strip (``_build_today_strip``) and the
desktop's ``GET /api/solo/today``. Text and numbers only — a surface decides
how to draw them. Every source is read under its own guard, so a missing or
half-migrated store yields an honest empty field and a warning, never a
broken welcome screen. Nothing here scans the filesystem or calls a tracker:
the agent spend comes from the last agentwatch ingest, not a fresh one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

#: Longest text the strip carries for one line — the terminal ellipsises
#: anyway, but the desktop tile should not receive a paragraph.
_CLIP = 160


@dataclass(frozen=True)
class TodaySnapshot:
    """What the Solo welcome says about today. Every field defaulted: an empty
    snapshot is the honest answer for a fresh install."""

    project_id: str = ""
    project_name: str = ""
    # The latest standup: "" when there is none yet.
    standup_date: str = ""
    standup_summary: str = ""
    standup_blockers: str = ""
    sprint_name: str = ""
    sprint_day: int = 0
    sprint_total_days: int = 0
    confidence_pct: int = 0
    confidence_label: str = ""
    confidence_trend: str = ""  # "improving" | "steady" | "declining" | ""
    # The next story from the newest plan's current sprint.
    next_story_id: str = ""
    next_story_title: str = ""
    next_sprint_name: str = ""
    plan_session_id: str = ""
    plan_scoped: bool = False  # True when the plan came from the active project
    # Agent spend since Monday, from the last agentwatch ingest.
    spend_usd: float = 0.0
    spend_sessions: int = 0
    spend_known: bool = False  # every model in the window has a price
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _CLIP else text[: _CLIP - 1].rstrip() + "…"


def _attr(obj: object, name: str, default=None):
    """Read a field off a dataclass or a plain dict — plans load either way."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def build_today_snapshot(
    *, project_id: str = "", db_path: Path | None = None, today: date | None = None
) -> TodaySnapshot:
    """Assemble the snapshot from the standup, planning and agentwatch stores.

    ``project_id`` narrows the standup and plan reads to that project (the
    welcome passes the active one); '' reads the newest of everything.
    Never raises.
    """
    from yeaboi.paths import get_db_path

    path = Path(db_path or get_db_path())
    today = today or date.today()
    if not path.exists():
        logger.info("today snapshot: no sessions db at %s — empty snapshot", path)
        return TodaySnapshot()

    fields: dict = {"project_id": project_id}
    warnings: list[str] = []
    scope = _resolve(project_id, path, warnings)
    fields.update(_project_name(project_id, path, warnings))
    fields.update(_standup_fields(scope, path, warnings))
    fields.update(_plan_fields(scope, path, today, warnings))
    fields.update(_spend_fields(path, today, warnings))
    snapshot = TodaySnapshot(**fields, warnings=tuple(warnings))
    logger.info(
        "today snapshot: standup=%s sprint=%d/%d next=%s spend=%.2f/%d warnings=%d",
        snapshot.standup_date or "-",
        snapshot.sprint_day,
        snapshot.sprint_total_days,
        snapshot.next_story_id or "-",
        snapshot.spend_usd,
        snapshot.spend_sessions,
        len(warnings),
    )
    return snapshot


def _resolve(project_id: str, path: Path, warnings: list[str]):
    try:
        from yeaboi.projects.scope import resolve_scope

        return resolve_scope(project_id, db_path=path)
    except Exception as e:  # noqa: BLE001 — the strip must never take the welcome down
        logger.warning("today snapshot: scope resolution failed: %s", e)
        warnings.append("could not resolve the active project")
        return None


def _project_name(project_id: str, path: Path, warnings: list[str]) -> dict:
    if not project_id:
        return {}
    try:
        from yeaboi.projects.store import ProjectStore

        with ProjectStore(path) as store:
            row = store.get(project_id)
        return {"project_name": str((row or {}).get("name") or "")}
    except Exception as e:  # noqa: BLE001
        logger.warning("today snapshot: project lookup failed: %s", e)
        warnings.append("could not read the project")
        return {}


def _standup_fields(scope, path: Path, warnings: list[str]) -> dict:
    try:
        from yeaboi.standup.insights import yesterday_context
        from yeaboi.standup.store import StandupStore

        session_ids = scope.session_ids if scope is not None else None
        with StandupStore(path) as store:
            rows = store.get_all_history(limit=10, session_ids=session_ids)
            row = next((r for r in rows if r.get("status") in ("success", "partial")), None)
            report = store.get_run_by_id(int(row["id"])) if row else None
        if report is None:
            return {}
        own = next((m for m in report.member_updates if m.name == report.my_name), None)
        if own is None and report.member_updates:
            own = report.member_updates[0]
        context = yesterday_context(report).get(own.name, {}) if own is not None else {}
        return {
            "standup_date": report.date,
            "standup_summary": _clip(context.get("summary", "")),
            "standup_blockers": _clip(context.get("blockers", "")),
            "sprint_name": report.sprint_name,
            "sprint_day": int(report.sprint_day),
            "sprint_total_days": int(report.sprint_total_days),
            "confidence_pct": int(report.confidence_pct),
            "confidence_label": report.confidence_label,
            "confidence_trend": report.confidence_trend,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("today snapshot: standup read failed: %s", e)
        warnings.append("could not read the latest standup")
        return {}


def _plan_fields(scope, path: Path, today: date, warnings: list[str]) -> dict:
    try:
        from yeaboi.projects.scope import latest_planning_state
        from yeaboi.reporting.sprints import _from_plan
        from yeaboi.ship.plans import latest_plan_with_work

        scoped = latest_planning_state(scope, db_path=path)
        if scoped is not None:
            session_id, state = scoped
            plan_scoped = True
        elif scope is not None and scope.project_id:
            # A project with no plan has no plan — another project's next story
            # would be a wrong answer, not a helpful one.
            return {}
        else:
            found = latest_plan_with_work(db_path=path)
            if found is None:
                return {}
            state, session_id, _name = found
            plan_scoped = False
        sprints = list(state.get("sprints") or [])
        if not sprints:
            return {"plan_session_id": session_id, "plan_scoped": plan_scoped}
        # The sprint whose date window holds today, else the first — the plan's
        # own dates, the same arithmetic the report and the tracker sync use.
        idx = 0
        for i, ref in enumerate(_from_plan(state, 0)):
            if ref.start_date <= today.isoformat() <= ref.end_date:
                idx = i
                break
        sprint = sprints[min(idx, len(sprints) - 1)]
        titles = {
            str(_attr(s, "id", "")): (_attr(s, "title") or _attr(s, "goal") or "") for s in state.get("stories") or []
        }
        story_ids = [str(sid) for sid in (_attr(sprint, "story_ids", ()) or ())]
        next_id = story_ids[0] if story_ids else ""
        return {
            "next_story_id": next_id,
            "next_story_title": _clip(titles.get(next_id, "")),
            "next_sprint_name": str(_attr(sprint, "name", "") or ""),
            "plan_session_id": session_id,
            "plan_scoped": plan_scoped,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning("today snapshot: plan read failed: %s", e)
        warnings.append("could not read the latest plan")
        return {}


def _spend_fields(path: Path, today: date, warnings: list[str]) -> dict:
    try:
        from yeaboi.agentwatch.engine import _session_cost  # the advisor and ship costing share it the same way
        from yeaboi.agentwatch.store import AgentWatchStore

        monday = today - timedelta(days=today.weekday())
        with AgentWatchStore(path) as store:
            sessions = store.list_sessions(since=monday.isoformat())
        total = 0.0
        known = True
        for session in sessions:
            usd, all_known = _session_cost(session.get("model_usage") or {})
            total += usd
            known = known and all_known
        return {"spend_usd": round(total, 4), "spend_sessions": len(sessions), "spend_known": known and bool(sessions)}
    except Exception as e:  # noqa: BLE001
        logger.warning("today snapshot: agent spend read failed: %s", e)
        warnings.append("could not read agent spend")
        return {}
