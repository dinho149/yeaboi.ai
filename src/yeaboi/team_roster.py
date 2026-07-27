"""Shared, lightweight team-roster discovery for tracker-backed workflows."""

from __future__ import annotations

import json
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_CACHE_TTL_SECONDS = 15 * 60

_CACHE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS team_roster_cache (
    provider       TEXT NOT NULL,
    project_key    TEXT NOT NULL,
    window_days    INTEGER NOT NULL,
    members_json   TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (provider, project_key, window_days)
);"""


@dataclass(frozen=True)
class RosterMember:
    name: str
    source: str
    identity: str = ""
    email: str = ""


@dataclass(frozen=True)
class RosterSourceResult:
    provider: str
    project: str
    status: str
    members: tuple[RosterMember, ...] = ()
    from_cache: bool = False
    warning: str = ""
    fetched_at: str = ""


@dataclass(frozen=True)
class RosterResult:
    members: tuple[RosterMember, ...]
    status: str
    sources: tuple[RosterSourceResult, ...]
    warnings: tuple[str, ...] = ()


def _effective_db_path(db_path) -> Path:
    if db_path is not None:
        return Path(db_path)
    from yeaboi.paths import get_db_path

    return Path(get_db_path())


def _ensure_cache_schema(db_path: Path) -> None:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(_CACHE_SCHEMA)
    except Exception:
        logger.warning("Could not initialise roster cache", exc_info=True)


def _load_cache(db_path: Path, provider: str, project: str, days: int) -> tuple[list[dict], datetime] | None:
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                """SELECT members_json, fetched_at FROM team_roster_cache
                   WHERE provider = ? AND project_key = ? AND window_days = ?""",
                (provider, project, days),
            ).fetchone()
        if not row:
            return None
        fetched_at = datetime.fromisoformat(row[1])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=UTC)
        return list(json.loads(row[0])), fetched_at
    except Exception:
        logger.warning("Could not read roster cache", exc_info=True)
        return None


def _save_cache(db_path: Path, provider: str, project: str, days: int, members: list[dict]) -> str:
    fetched_at = datetime.now(UTC).isoformat()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(_CACHE_SCHEMA)
            conn.execute(
                """INSERT INTO team_roster_cache
                       (provider, project_key, window_days, members_json, fetched_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(provider, project_key, window_days) DO UPDATE SET
                       members_json = excluded.members_json,
                       fetched_at = excluded.fetched_at""",
                (provider, project, days, json.dumps(members, ensure_ascii=False), fetched_at),
            )
    except Exception:
        logger.warning("Could not save roster cache", exc_info=True)
    return fetched_at


def _as_members(provider: str, rows: list[dict]) -> tuple[RosterMember, ...]:
    unique: dict[str, RosterMember] = {}
    for row in rows:
        name = str(row.get("name", "") or "").strip()
        if not name:
            continue
        identity = str(row.get("identity", "") or row.get("email", "") or name.casefold())
        unique.setdefault(
            identity,
            RosterMember(
                name=name,
                source=provider,
                identity=identity,
                email=str(row.get("email", "") or ""),
            ),
        )
    return tuple(sorted(unique.values(), key=lambda member: member.name.casefold()))


def _fetch_source(
    provider: str,
    project: str,
    days: int,
    db_path: Path,
    force_refresh: bool,
    cache_ttl_seconds: int,
) -> RosterSourceResult:
    cached = _load_cache(db_path, provider, project, days)
    if cached and not force_refresh:
        rows, fetched = cached
        if datetime.now(UTC) - fetched <= timedelta(seconds=cache_ttl_seconds):
            return RosterSourceResult(
                provider,
                project,
                "complete" if rows else "empty",
                _as_members(provider, rows),
                True,
                fetched_at=fetched.isoformat(),
            )
    try:
        if provider == "jira":
            from yeaboi.tools.jira import jira_assignee_roster

            rows = jira_assignee_roster(project, days=days)
        else:
            from yeaboi.tools.azure_devops import azdevops_assignee_roster

            rows = azdevops_assignee_roster(project, days=days)
        fetched_at = _save_cache(db_path, provider, project, days, rows)
        members = _as_members(provider, rows)
        return RosterSourceResult(
            provider,
            project,
            "complete" if members else "empty",
            members,
            fetched_at=fetched_at,
        )
    except Exception as exc:
        warning = f"{provider} roster lookup failed: {exc}"
        logger.warning(warning)
        if cached:
            rows, fetched = cached
            if not rows:
                return RosterSourceResult(provider, project, "failed", warning=warning)
            return RosterSourceResult(
                provider,
                project,
                "stale",
                _as_members(provider, rows),
                True,
                warning,
                fetched.isoformat(),
            )
        return RosterSourceResult(provider, project, "failed", warning=warning)


def fetch_roster_result(
    *,
    jira_project: str = "",
    azdo_project: str = "",
    days: int = DEFAULT_LOOKBACK_DAYS,
    db_path=None,
    force_refresh: bool = False,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> RosterResult:
    """Discover recent/WIP assignees from configured trackers concurrently."""
    if not jira_project and not azdo_project:
        from yeaboi.config import get_azure_devops_project, get_jira_project_key

        jira_project = get_jira_project_key() or ""
        azdo_project = get_azure_devops_project() or ""
    targets = [
        (provider, project) for provider, project in (("jira", jira_project), ("azuredevops", azdo_project)) if project
    ]
    if not targets:
        return RosterResult((), "empty", ())

    path = _effective_db_path(db_path)
    _ensure_cache_schema(path)
    results: list[RosterSourceResult] = []
    with ThreadPoolExecutor(max_workers=len(targets), thread_name_prefix="team-roster") as executor:
        futures = {
            executor.submit(
                _fetch_source,
                provider,
                project,
                int(days),
                path,
                force_refresh,
                cache_ttl_seconds,
            ): provider
            for provider, project in targets
        }
        for future in as_completed(futures):
            results.append(future.result())
    # Preserve the legacy merge precedence: Jira wins a display-name/email
    # collision, regardless of which concurrent request completed first.
    results.sort(key=lambda result: (0 if result.provider == "jira" else 1, result.provider))

    merged: dict[str, RosterMember] = {}
    for result in results:
        for member in result.members:
            merge_key = member.email.casefold() if member.email else member.name.casefold()
            merged.setdefault(merge_key, member)
    members = tuple(sorted(merged.values(), key=lambda member: member.name.casefold()))
    warnings = tuple(result.warning for result in results if result.warning)
    statuses = {result.status for result in results}
    if statuses == {"failed"}:
        status = "failed"
    elif "failed" in statuses or "stale" in statuses:
        status = "partial"
    elif members:
        status = "complete"
    else:
        status = "empty"
    return RosterResult(members, status, tuple(results), warnings)
