"""Project scope — the pull-based resolver that narrows cross-mode reads.

A ``ProjectScope`` names a project, the sessions linked to it, and which
context dependencies the run may use; gatherers (``gather_ceremony_context``,
retro carry-forward, …) take an optional ``scope=`` and hard-filter their
store reads to those sessions. ``None`` is today's team-wide behavior,
byte-for-byte — scoping is strictly an opt-in narrowing, and resolution never
raises (a bad id degrades to unscoped).

Context dependencies are the coarse per-producer toggles of
``CONTEXT_DEP_TOKENS``. ``context_deps=None`` means every feed is enabled;
an empty set is an incognito run (context isolation, not ephemerality —
the session still persists). Precedence for a run: an explicit caller value,
else the mode's own persisted config (standup only), else the project's
``default_context_deps`` setting, else all-on.

Deliberately unscoped: PerformanceStore reads. 1:1s and reviews are keyed by
engineer, not project — an engineer's history must not shrink because a
project is active — and ``performance_notes`` has no session column at all.

Naming hazard: this is the ``proj-<8hex>`` id space of ``projects/store.py``,
not the legacy planning-TUI uuid4 "project_id" in ``projects.json``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# The toggleable context feeds, one per producer mode:
#   retro       — retro action items/themes/cadence into planning, retro→retro carry-over
#   standup     — confidence trend/cadence into planning, blockers into retro
#   plan        — latest-sprint-plan substitution into standup and reporting
#   performance — open 1:1 actions and review focus into planning
#   analysis    — analysis-profile seeding and team calibration into planning
CONTEXT_DEP_TOKENS = ("retro", "standup", "plan", "performance", "analysis")


@dataclass(frozen=True)
class ProjectScope:
    """The sessions and context dependencies a run's reads are narrowed to."""

    project_id: str
    # None = no session narrowing (team-wide reads); a tuple hard-filters.
    session_ids: tuple[str, ...] | None
    # None = every dependency enabled; an empty set = incognito.
    context_deps: frozenset[str] | None = None

    def wants(self, dep: str) -> bool:
        """Whether this scope allows the given context dependency."""
        return self.context_deps is None or dep in self.context_deps

    @property
    def incognito(self) -> bool:
        """True only for an explicit empty-deps run — every cross-mode read is off."""
        return self.context_deps is not None and not self.context_deps


def wants(scope: ProjectScope | None, dep: str) -> bool:
    """Whether ``scope`` allows ``dep``; an absent scope allows everything."""
    return scope is None or scope.wants(dep)


def incognito(scope: ProjectScope | None) -> bool:
    """Whether ``scope`` is a full-incognito run; an absent scope never is.

    Gates the cross-mode reads that have no token of their own (poker votes,
    the latest delivery report): they run for any partial toggle set and go
    silent only when every source is switched off.
    """
    return scope is not None and scope.incognito


def normalize_context_deps(value: object) -> frozenset[str] | None:
    """Coerce a caller-supplied deps value to a frozenset of known tokens.

    Accepts ``None`` (→ ``None`` = all on), an iterable of tokens, a JSON
    list string, or a comma-separated string. Unknown tokens are dropped with
    a warning rather than raised — same never-raise contract as the resolver.

    A value whose tokens are *all* unknown returns ``None`` (all on), never an
    empty frozenset: only an explicitly empty value means incognito, and a
    surface typo must not read as "every source is switched off".
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("["):
            try:
                value = json.loads(text)
            except ValueError:
                logger.warning("normalize_context_deps: unparseable JSON %r — treating as all-on", text)
                return None
        else:
            value = [part.strip() for part in text.split(",")]
    if not isinstance(value, Iterable):
        logger.warning("normalize_context_deps: unsupported value %r — treating as all-on", value)
        return None
    tokens = {str(item).strip() for item in value if str(item).strip()}
    known = tokens & set(CONTEXT_DEP_TOKENS)
    unknown = tokens - set(CONTEXT_DEP_TOKENS)
    if unknown:
        logger.warning("normalize_context_deps: dropping unknown token(s) %s", sorted(unknown))
    if tokens and not known:
        logger.warning(
            "normalize_context_deps: no known token in %s — treating as all-on, not incognito", sorted(tokens)
        )
        return None
    return frozenset(known)


def parse_context_spec(spec: str) -> list[str] | None:
    """Parse the surface grammar for context toggles into an engine value.

    ``""``/``"inherit"`` → ``None`` (inherit), ``"all"`` → every token,
    ``"none"`` → ``[]`` (incognito), else a comma-separated token list.
    Raises ``ValueError`` on an unknown token — a surface typo must not read
    as "that source is switched off".
    """
    word = spec.strip().lower()
    if word in ("", "inherit"):
        return None
    if word == "all":
        return list(CONTEXT_DEP_TOKENS)
    if word == "none":
        return []
    tokens = [part.strip() for part in spec.split(",") if part.strip()]
    unknown = [token for token in tokens if token not in CONTEXT_DEP_TOKENS]
    if unknown:
        raise ValueError(f"unknown context source(s) {unknown} — valid: {', '.join(CONTEXT_DEP_TOKENS)}")
    return list(dict.fromkeys(tokens))


def resolve_scope(
    project_id: str = "",
    session_id: str = "",
    *,
    context_deps: Iterable[str] | str | None = None,
    db_path: Path | None = None,
) -> ProjectScope | None:
    """Resolve the scope a run operates under. ``None`` = unscoped (team-wide).

    Precedence: an explicit ``project_id`` wins; otherwise the project is
    inherited from ``session_id``'s ``sessions_meta`` row. ``context_deps``
    falls back to the project's ``default_context_deps`` setting when the
    caller passes ``None``. Returns ``None`` only when there is neither a
    project nor a deps restriction. Never raises.
    """
    deps = normalize_context_deps(context_deps)
    try:
        from yeaboi.paths import get_db_path
        from yeaboi.sessions import SessionStore

        path = db_path or get_db_path()
        if not Path(path).exists():
            return ProjectScope(project_id="", session_ids=None, context_deps=deps) if deps is not None else None
        with SessionStore(path) as store:
            pid = project_id or (store.session_project_id(session_id) if session_id else "")
            if not pid:
                if deps is None:
                    return None
                return ProjectScope(project_id="", session_ids=None, context_deps=deps)
            ids = tuple(store.session_ids_for_project(pid))
        if deps is None:
            deps = _project_default_deps(pid, db_path=path)
        logger.info(
            "Resolved project scope: project=%s sessions=%d deps=%s",
            pid,
            len(ids),
            "all" if deps is None else sorted(deps),
        )
        return ProjectScope(project_id=pid, session_ids=ids, context_deps=deps)
    except Exception:  # noqa: BLE001 — scoping is best-effort; a bad id must not break a run
        logger.debug("resolve_scope failed (non-fatal)", exc_info=True)
        return ProjectScope(project_id="", session_ids=None, context_deps=deps) if deps is not None else None


def _project_default_deps(project_id: str, *, db_path: Path | None = None) -> frozenset[str] | None:
    """The project's ``default_context_deps`` setting, or ``None`` (all on)."""
    try:
        from yeaboi.paths import get_db_path
        from yeaboi.projects.store import ProjectStore

        with ProjectStore(db_path or get_db_path()) as store:
            return normalize_context_deps(store.get_settings(project_id).get("default_context_deps"))
    except Exception:  # noqa: BLE001 — same never-raise contract as resolve_scope
        logger.debug("_project_default_deps failed (non-fatal)", exc_info=True)
        return None


def recent_standup_blockers(scope: ProjectScope | None, *, limit: int = 10, db_path: Path | None = None) -> list[str]:
    """Blockers from the project's recent standups, newest first, deduped.

    Feeds the standup→retro edge: a scoped retro board seeds these as
    dismissible review cards. Unscoped (``None``) returns nothing — the
    team-wide board keeps its carry-forward-only seeding — and a scope with
    the ``standup`` dep off returns nothing likewise. Never raises.
    """
    if scope is None or not scope.session_ids or not scope.wants("standup"):
        return []
    try:
        from yeaboi.paths import get_db_path
        from yeaboi.standup.store import StandupStore

        path = db_path or get_db_path()
        if not Path(path).exists():
            return []
        with StandupStore(path) as store:
            reports = store.get_recent_reports(limit, session_ids=scope.session_ids)
    except Exception:  # noqa: BLE001 — same never-raise contract as resolve_scope
        logger.debug("recent_standup_blockers failed (non-fatal)", exc_info=True)
        return []
    seen: set[str] = set()
    blockers: list[str] = []
    for report in reports:
        for member in report.member_updates:
            text = (member.blockers or "").strip()
            if not text or text.lower() in seen:
                continue
            seen.add(text.lower())
            blockers.append(f"{member.name}: {text}" if member.name else text)
    logger.info("recent_standup_blockers: project=%s blockers=%d", scope.project_id, len(blockers))
    return blockers


def latest_planning_state(scope: ProjectScope | None, *, db_path: Path | None = None) -> tuple[str, dict] | None:
    """The project's newest planning session that carries a sprint plan.

    Returns ``(session_id, state)`` or ``None``; sessions without ``sprints``
    are skipped (an intake that never reached sprint_planner feeds nothing).
    Never raises.
    """
    if scope is None or not scope.project_id:
        return None
    try:
        from yeaboi.paths import get_db_path
        from yeaboi.sessions import SessionStore

        path = db_path or get_db_path()
        if not Path(path).exists():
            return None
        with SessionStore(path) as store:
            for sid in store.session_ids_for_project(scope.project_id, mode="planning"):
                state = store.load_state(sid)
                if state and state.get("sprints"):
                    logger.info("latest_planning_state: project=%s session=%s", scope.project_id, sid)
                    return sid, state
    except Exception:  # noqa: BLE001 — same never-raise contract as resolve_scope
        logger.debug("latest_planning_state failed (non-fatal)", exc_info=True)
        return None
    return None
