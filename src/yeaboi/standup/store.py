"""SQLite store for the Daily Standup mode.

Persists three things in the shared ~/.scrum-agent/sessions.db:
- ``standup_config``  — per-session schedule + delivery preferences
- ``standup_history`` — every run's serialized StandupReport + delivery status
- ``standup_updates`` — user-typed "my update" text, consumed verbatim by the engine

Follows the exact patterns used by TeamProfileStore (team_profile.py): a separate
store class opening its own connection to the same DB, autocommit mode, context
manager support, idempotent CREATE-IF-NOT-EXISTS schema. The schema constant is
also referenced by sessions.py's v6 migration so an existing DB gets the tables.

# See docs: "Session Management" — SQLite persistence, schema versioning
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.agent.state import ActivityEvidence, MemberUpdate, StandupReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — referenced by sessions.py migration v6 AND created on store open
# ---------------------------------------------------------------------------

_STANDUP_SCHEMA = """\
CREATE TABLE IF NOT EXISTS standup_config (
    session_id        TEXT PRIMARY KEY,
    enabled           INTEGER NOT NULL DEFAULT 0,
    time              TEXT NOT NULL DEFAULT '10:00',
    lead_minutes      INTEGER NOT NULL DEFAULT 10,
    timezone          TEXT NOT NULL DEFAULT '',
    weekdays          TEXT NOT NULL DEFAULT '1-5',
    delivery_channels TEXT NOT NULL DEFAULT '["terminal"]',
    repo_path         TEXT NOT NULL DEFAULT '',
    my_aliases        TEXT NOT NULL DEFAULT '',
    tracker_sources   TEXT NOT NULL DEFAULT '["jira"]',
    team_members      TEXT NOT NULL DEFAULT '[]',
    roster_configured INTEGER NOT NULL DEFAULT 0,
    code_sources      TEXT NOT NULL DEFAULT '[]',
    github_repositories TEXT NOT NULL DEFAULT '[]',
    azdo_projects     TEXT NOT NULL DEFAULT '[]',
    azdo_repositories TEXT NOT NULL DEFAULT '[]',
    code_scope_configured INTEGER NOT NULL DEFAULT 0,
    documentation_sources TEXT NOT NULL DEFAULT '[]',
    documentation_scope_configured INTEGER NOT NULL DEFAULT 0,
    automation_markers TEXT NOT NULL DEFAULT '',
    automation_handling TEXT NOT NULL DEFAULT 'exclude',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS standup_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    run_at          TEXT NOT NULL,
    standup_date    TEXT NOT NULL DEFAULT '',
    sprint_day      INTEGER NOT NULL DEFAULT 0,
    confidence_pct  INTEGER NOT NULL DEFAULT 0,
    report_json     TEXT NOT NULL DEFAULT '',
    delivery_status TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'success',
    error           TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS standup_updates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    standup_date TEXT NOT NULL,
    member       TEXT NOT NULL,
    update_text  TEXT NOT NULL DEFAULT '',
    images_json  TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL
);"""


# ---------------------------------------------------------------------------
# Serialisation helpers — StandupReport <-> JSON (same pattern as sessions.py)
# ---------------------------------------------------------------------------


def _standup_report_to_json(report: StandupReport) -> str:
    """Serialize a StandupReport to a JSON string.

    ``asdict`` recursively turns the frozen MemberUpdate tuples into dicts and
    the activity_counts tuple-of-tuples into nested lists; both are rebuilt with
    the correct types by ``_dict_to_standup_report``.
    """
    return json.dumps(asdict(report), ensure_ascii=False)


def _dict_to_evidence(items: object) -> tuple[ActivityEvidence, ...]:
    """Rebuild an evidence tuple from JSON-parsed dicts (missing → empty)."""
    if not isinstance(items, list):
        return ()
    return tuple(
        ActivityEvidence(
            kind=str(e.get("kind", "")),
            key=str(e.get("key", "")),
            title=str(e.get("title", "")),
            url=str(e.get("url", "")),
            repository=str(e.get("repository", "")),
            status=str(e.get("status", "")),
            timestamp=str(e.get("timestamp", "")),
            # One level deep in practice (commits under a PR); recursion keeps
            # the rebuild honest either way.
            children=_dict_to_evidence(e.get("children")),
        )
        for e in items
        if isinstance(e, dict)
    )


def _dict_to_standup_report(d: dict) -> StandupReport:
    """Reconstruct a StandupReport from a JSON-parsed dict.

    Uses ``.get()`` with defaults for every field so reports serialized by an
    older version (missing keys) still deserialize — see CLAUDE.md
    "Frozen dataclass backward compatibility".
    """
    members = tuple(
        MemberUpdate(
            name=m.get("name", ""),
            summary=m.get("summary", ""),
            blockers=m.get("blockers", ""),
            progress_note=m.get("progress_note", ""),
            outlook=m.get("outlook", ""),
            source=m.get("source", "inferred"),
            self_report=m.get("self_report", ""),
            # JSON turned each (label, url) tuple into a list — rebuild tuples.
            links=tuple((str(li[0]), str(li[1])) for li in m.get("links", ()) if len(li) == 2),
            activity_count=int(m.get("activity_count", 0)),
            code_summary=m.get("code_summary", ""),
            code_links=tuple((str(li[0]), str(li[1])) for li in m.get("code_links", ()) if len(li) == 2),
            code_activity_count=int(m.get("code_activity_count", 0)),
            documentation_summary=m.get("documentation_summary", ""),
            documentation_links=tuple(
                (str(li[0]), str(li[1])) for li in m.get("documentation_links", ()) if len(li) == 2
            ),
            documentation_activity_count=int(m.get("documentation_activity_count", 0)),
            ticketing_summary=m.get("ticketing_summary", ""),
            ticketing_links=tuple((str(li[0]), str(li[1])) for li in m.get("ticketing_links", ()) if len(li) == 2),
            ticketing_activity_count=int(m.get("ticketing_activity_count", 0)),
            ticketing_evidence=_dict_to_evidence(m.get("ticketing_evidence")),
            code_evidence=_dict_to_evidence(m.get("code_evidence")),
            documentation_evidence=_dict_to_evidence(m.get("documentation_evidence")),
        )
        for m in d.get("member_updates", ())
    )
    # JSON turned each (source, count) tuple into a [source, count] list — rebuild tuples.
    counts = tuple((str(c[0]), int(c[1])) for c in d.get("activity_counts", ()) if len(c) == 2)
    skipped = tuple((str(s[0]), str(s[1])) for s in d.get("skipped_sources", ()) if len(s) == 2)
    category_coverage = tuple((str(item[0]), str(item[1])) for item in d.get("category_coverage", ()) if len(item) == 2)
    return StandupReport(
        date=d.get("date", ""),
        session_id=d.get("session_id", ""),
        sprint_name=d.get("sprint_name", ""),
        sprint_day=d.get("sprint_day", 0),
        sprint_total_days=d.get("sprint_total_days", 0),
        confidence_pct=d.get("confidence_pct", 0),
        confidence_label=d.get("confidence_label", ""),
        confidence_rationale=d.get("confidence_rationale", ""),
        confidence_delta=int(d.get("confidence_delta", 0)),
        confidence_trend=d.get("confidence_trend", ""),
        team_summary=d.get("team_summary", ""),
        member_updates=members,
        activity_counts=counts,
        activity_window=d.get("activity_window", ""),
        skipped_sources=skipped,
        category_coverage=category_coverage,
        my_name=d.get("my_name", ""),
        warnings=tuple(d.get("warnings", ())),
        images=tuple(d.get("images", ())),
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class StandupStore:
    """SQLite-backed store for standup config, run history, and self-updates.

    Uses the same database as SessionStore (sessions.db) with dedicated standup
    tables. Follows the same patterns: autocommit mode, context-manager support,
    explicit close.

    # See docs: "Session Management" — SQLite persistence
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None  # autocommit
        self._conn.executescript(_STANDUP_SCHEMA)
        # Idempotent migration: add lead_minutes to standup_config tables created
        # before it existed (same try/except pattern SessionStore uses).
        try:
            self._conn.execute("ALTER TABLE standup_config ADD COLUMN lead_minutes INTEGER NOT NULL DEFAULT 10")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Idempotent migration: screenshots pasted (Ctrl+V) into "My Update" — a
        # JSON list of file paths under ~/.yeaboi/attachments/, attached to the
        # summary LLM call as multimodal image blocks at run time.
        try:
            self._conn.execute("ALTER TABLE standup_updates ADD COLUMN images_json TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Idempotent migration: the user's comma-separated identity aliases
        # (GitHub handle, Jira display name, …) for alias-aware activity attribution.
        try:
            self._conn.execute("ALTER TABLE standup_config ADD COLUMN my_aliases TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already exists
        # Idempotent migration: Standup owns an explicit tracker/member scope.
        # The configured bit distinguishes "not chosen yet" from a deliberate
        # self-only roster (an empty team_members list).
        for statement in (
            """ALTER TABLE standup_config
               ADD COLUMN tracker_sources TEXT NOT NULL DEFAULT '["jira"]'""",
            """ALTER TABLE standup_config
               ADD COLUMN team_members TEXT NOT NULL DEFAULT '[]'""",
            """ALTER TABLE standup_config
               ADD COLUMN roster_configured INTEGER NOT NULL DEFAULT 0""",
            """ALTER TABLE standup_config
               ADD COLUMN code_sources TEXT NOT NULL DEFAULT '[]'""",
            """ALTER TABLE standup_config
               ADD COLUMN github_repositories TEXT NOT NULL DEFAULT '[]'""",
            """ALTER TABLE standup_config
               ADD COLUMN azdo_projects TEXT NOT NULL DEFAULT '[]'""",
            """ALTER TABLE standup_config
               ADD COLUMN azdo_repositories TEXT NOT NULL DEFAULT '[]'""",
            """ALTER TABLE standup_config
               ADD COLUMN code_scope_configured INTEGER NOT NULL DEFAULT 0""",
            """ALTER TABLE standup_config
               ADD COLUMN documentation_sources TEXT NOT NULL DEFAULT '[]'""",
            """ALTER TABLE standup_config
               ADD COLUMN documentation_scope_configured INTEGER NOT NULL DEFAULT 0""",
            # Service-hook/bot detection (see standup/automation.py): the user's
            # custom comma-separated content markers, and whether detected
            # automation is excluded from member credit ('exclude') or left
            # alone ('off').
            """ALTER TABLE standup_config
               ADD COLUMN automation_markers TEXT NOT NULL DEFAULT ''""",
            """ALTER TABLE standup_config
               ADD COLUMN automation_handling TEXT NOT NULL DEFAULT 'exclude'""",
        ):
            try:
                self._conn.execute(statement)
            except sqlite3.OperationalError:
                pass  # column already exists

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> StandupStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    # ── Config ────────────────────────────────────────────────────────────

    def save_config(
        self,
        session_id: str,
        *,
        enabled: bool,
        time: str,
        weekdays: str,
        delivery_channels: list[str],
        lead_minutes: int = 10,
        timezone: str = "",
        repo_path: str = "",
        my_aliases: str = "",
        tracker_sources: list[str] | None = None,
        team_members: list[str] | None = None,
        roster_configured: bool = False,
        code_sources: list[str] | None = None,
        github_repositories: list[str] | None = None,
        azdo_projects: list[str] | None = None,
        azdo_repositories: list[str] | None = None,
        code_scope_configured: bool = False,
        documentation_sources: list[str] | None = None,
        documentation_scope_configured: bool = False,
        automation_markers: str = "",
        automation_handling: str = "exclude",
    ) -> None:
        """Insert or update the standup schedule/delivery config for a session.

        ``time`` is the STANDUP time (e.g. "10:00"); the scheduler fires
        ``lead_minutes`` earlier. ``my_aliases`` is the user's comma-separated
        identity list across tools (GitHub handle, Jira display name, …) used
        for alias-aware activity attribution. ``automation_markers`` /
        ``automation_handling`` tune service-hook detection (standup/automation.py).
        """
        now = self._now()
        channels_json = json.dumps(delivery_channels)
        tracker_sources_json = json.dumps(tracker_sources or ["jira"])
        team_members_json = json.dumps(team_members or [])
        code_sources_json = json.dumps(code_sources or [])
        github_repositories_json = json.dumps(github_repositories or [])
        azdo_projects_json = json.dumps(azdo_projects or [])
        azdo_repositories_json = json.dumps(azdo_repositories or [])
        documentation_sources_json = json.dumps(documentation_sources or [])
        logger.info(
            "Saving standup config: session=%s enabled=%s standup_time=%s lead=%d channels=%s",
            session_id,
            enabled,
            time,
            lead_minutes,
            delivery_channels,
        )
        self._conn.execute(
            """INSERT INTO standup_config
                   (session_id, enabled, time, lead_minutes, timezone, weekdays, delivery_channels,
                    repo_path, my_aliases, tracker_sources, team_members, roster_configured,
                    code_sources, github_repositories, azdo_projects, azdo_repositories, code_scope_configured,
                    documentation_sources, documentation_scope_configured,
                    automation_markers, automation_handling, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   enabled = excluded.enabled,
                   time = excluded.time,
                   lead_minutes = excluded.lead_minutes,
                   timezone = excluded.timezone,
                   weekdays = excluded.weekdays,
                   delivery_channels = excluded.delivery_channels,
                   repo_path = excluded.repo_path,
                   my_aliases = excluded.my_aliases,
                   tracker_sources = excluded.tracker_sources,
                   team_members = excluded.team_members,
                   roster_configured = excluded.roster_configured,
                   code_sources = excluded.code_sources,
                   github_repositories = excluded.github_repositories,
                   azdo_projects = excluded.azdo_projects,
                   azdo_repositories = excluded.azdo_repositories,
                   code_scope_configured = excluded.code_scope_configured,
                   documentation_sources = excluded.documentation_sources,
                   documentation_scope_configured = excluded.documentation_scope_configured,
                   automation_markers = excluded.automation_markers,
                   automation_handling = excluded.automation_handling,
                   updated_at = excluded.updated_at""",
            (
                session_id,
                int(enabled),
                time,
                int(lead_minutes),
                timezone,
                weekdays,
                channels_json,
                repo_path,
                my_aliases,
                tracker_sources_json,
                team_members_json,
                int(roster_configured),
                code_sources_json,
                github_repositories_json,
                azdo_projects_json,
                azdo_repositories_json,
                int(code_scope_configured),
                documentation_sources_json,
                int(documentation_scope_configured),
                automation_markers,
                automation_handling or "exclude",
                now,
                now,
            ),
        )

    def get_enabled_schedule_sessions(self) -> list[str]:
        """Session ids with an enabled schedule, most recently updated first.

        The hub's schedule card prefers one of these over the bare latest session
        so an already-installed schedule stays visible/editable (instead of the
        wizard silently creating a second schedule for a newer session).
        """
        rows = self._conn.execute(
            "SELECT session_id FROM standup_config WHERE enabled = 1 ORDER BY updated_at DESC"
        ).fetchall()
        return [r[0] for r in rows]

    def get_latest_configured_session(self) -> str | None:
        """Newest session whose roster was actually confirmed, or None.

        The standup page targets "the latest session" across every mode, so any
        other activity that opens a session leaves the saved standup setup on an
        older one. The schedule card already works around this for schedules
        (see :meth:`get_enabled_schedule_sessions`); this is the same escape
        hatch for the setup itself. ``roster_configured`` is the filter because
        a row written by a half-finished walk is not a setup worth offering —
        the caller still validates the remaining, conditionally-required flags.
        """
        row = self._conn.execute(
            "SELECT session_id FROM standup_config WHERE roster_configured = 1 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def load_config(self, session_id: str) -> dict | None:
        """Return the standup config for a session as a dict, or None if unset."""
        row = self._conn.execute(
            "SELECT session_id, enabled, time, timezone, weekdays, delivery_channels, repo_path, lead_minutes, "
            "my_aliases, tracker_sources, team_members, roster_configured, "
            "code_sources, github_repositories, azdo_projects, azdo_repositories, code_scope_configured, "
            "documentation_sources, documentation_scope_configured, automation_markers, automation_handling "
            "FROM standup_config WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            channels = json.loads(row[5]) if row[5] else ["terminal"]
        except (json.JSONDecodeError, TypeError):
            channels = ["terminal"]
        try:
            tracker_sources = json.loads(row[9]) if row[9] else ["jira"]
        except (json.JSONDecodeError, TypeError):
            tracker_sources = ["jira"]
        try:
            team_members = json.loads(row[10]) if row[10] else []
        except (json.JSONDecodeError, TypeError):
            team_members = []

        def _json_list(value) -> list:
            try:
                parsed = json.loads(value) if value else []
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []

        azdo_projects = _json_list(row[14])
        legacy_azdo_repositories = _json_list(row[15])
        if not azdo_projects and legacy_azdo_repositories:
            azdo_projects = list(
                dict.fromkeys(
                    project
                    for repository in legacy_azdo_repositories
                    for project, separator, _name in [str(repository).partition("/")]
                    if separator and project
                )
            )

        return {
            "session_id": row[0],
            "enabled": bool(row[1]),
            "time": row[2],
            "timezone": row[3],
            "weekdays": row[4],
            "delivery_channels": channels,
            "repo_path": row[6],
            "lead_minutes": row[7] if row[7] is not None else 10,
            "my_aliases": row[8] or "",
            "tracker_sources": tracker_sources,
            "team_members": team_members,
            "roster_configured": bool(row[11]),
            "code_sources": _json_list(row[12]),
            "github_repositories": _json_list(row[13]),
            "azdo_projects": azdo_projects,
            "azdo_repositories": legacy_azdo_repositories,
            "code_scope_configured": bool(row[16]),
            "documentation_sources": _json_list(row[17]),
            "documentation_scope_configured": bool(row[18]),
            "automation_markers": row[19] or "",
            "automation_handling": row[20] or "exclude",
        }

    # ── Self-reported updates ─────────────────────────────────────────────

    def save_my_update(
        self, session_id: str, standup_date: str, member: str, update_text: str, images: list[str] | None = None
    ) -> None:
        """Store a user-typed update for a member on a given date.

        A member submitting again for the same date overwrites the prior entry
        (delete-then-insert) so the latest text always wins.

        images: file paths of screenshots pasted into the update (Ctrl+V) —
            attached to the summary LLM call when the standup runs.
        """
        logger.info(
            "Saving self-reported update: session=%s date=%s member=%s images=%d",
            session_id,
            standup_date,
            member,
            len(images or []),
        )
        self._conn.execute(
            "DELETE FROM standup_updates WHERE session_id = ? AND standup_date = ? AND member = ?",
            (session_id, standup_date, member),
        )
        self._conn.execute(
            """INSERT INTO standup_updates (session_id, standup_date, member, update_text, images_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, standup_date, member, update_text, json.dumps(images or []), self._now()),
        )

    def get_my_updates(self, session_id: str, standup_date: str) -> dict[str, str]:
        """Return ``{member: update_text}`` for all self-reported updates on a date."""
        rows = self._conn.execute(
            "SELECT member, update_text FROM standup_updates WHERE session_id = ? AND standup_date = ?",
            (session_id, standup_date),
        ).fetchall()
        return {member: text for member, text in rows}

    def get_my_update_images(self, session_id: str, standup_date: str) -> dict[str, list[str]]:
        """Return ``{member: [image paths]}`` for self-reported updates on a date.

        Paths whose file no longer exists are pruned here so the engine only ever
        sees attachable screenshots (deleted files degrade silently).
        """
        rows = self._conn.execute(
            "SELECT member, images_json FROM standup_updates WHERE session_id = ? AND standup_date = ?",
            (session_id, standup_date),
        ).fetchall()
        out: dict[str, list[str]] = {}
        for member, images_json in rows:
            try:
                paths = json.loads(images_json) if images_json else []
            except (json.JSONDecodeError, TypeError):
                paths = []
            live = [p for p in paths if isinstance(p, str) and Path(p).exists()]
            if len(live) < len(paths):
                logger.warning("standup: %d pasted image(s) missing on disk for %s", len(paths) - len(live), member)
            if live:
                out[member] = live
        return out

    # ── Run history ───────────────────────────────────────────────────────

    def record_run(
        self,
        report: StandupReport,
        *,
        delivery_status: dict[str, bool] | None = None,
        status: str = "success",
        error: str = "",
    ) -> int:
        """Persist a completed standup run and return its history row id."""
        report_json = _standup_report_to_json(report)
        cursor = self._conn.execute(
            """INSERT INTO standup_history
                   (session_id, run_at, standup_date, sprint_day, confidence_pct,
                    report_json, delivery_status, status, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                report.session_id,
                self._now(),
                report.date,
                report.sprint_day,
                report.confidence_pct,
                report_json,
                json.dumps(delivery_status or {}),
                status,
                error,
            ),
        )
        logger.info("Recorded standup run: session=%s date=%s status=%s", report.session_id, report.date, status)
        return int(cursor.lastrowid or 0)

    def get_latest_report(self, session_id: str) -> StandupReport | None:
        """Return the most recent StandupReport for a session, or None."""
        row = self._conn.execute(
            "SELECT report_json FROM standup_history WHERE session_id = ? ORDER BY run_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_standup_report(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize standup report for %s: %s", session_id, exc)
            return None

    def get_previous_report(self, session_id: str, before_date: str) -> StandupReport | None:
        """Return the newest successful/partial report dated strictly BEFORE ``before_date``.

        Date-scoped (not run-scoped) so a same-day rerun never becomes
        "yesterday" — the engine uses this as the previous standup when
        comparing each member's update day-over-day.
        """
        row = self._conn.execute(
            "SELECT report_json FROM standup_history "
            "WHERE session_id = ? AND standup_date != '' AND standup_date < ? "
            "AND status IN ('success', 'partial') "
            "ORDER BY standup_date DESC, run_at DESC LIMIT 1",
            (session_id, before_date),
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_standup_report(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize previous standup report for %s: %s", session_id, exc)
            return None

    def get_history(self, session_id: str, limit: int = 30) -> list[dict]:
        """Return recent run metadata (newest first) for a session.

        Each row carries its ``id`` so callers (the saved-runs hub) can reopen or
        delete a specific run via ``get_run_by_id`` / ``delete_run``.
        """
        rows = self._conn.execute(
            "SELECT id, run_at, standup_date, sprint_day, confidence_pct, status "
            "FROM standup_history WHERE session_id = ? ORDER BY run_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            {
                "id": r[0],
                "run_at": r[1],
                "standup_date": r[2],
                "sprint_day": r[3],
                "confidence_pct": r[4],
                "status": r[5],
            }
            for r in rows
        ]

    def get_run_by_id(self, run_id: int) -> StandupReport | None:
        """Return the StandupReport for a single history row, or None if missing/corrupt."""
        row = self._conn.execute(
            "SELECT report_json FROM standup_history WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_standup_report(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize standup run id=%s: %s", run_id, exc)
            return None

    def delete_run(self, run_id: int) -> bool:
        """Delete a single standup history row. Returns True if a row was removed."""
        cursor = self._conn.execute("DELETE FROM standup_history WHERE id = ?", (run_id,))
        deleted = (cursor.rowcount or 0) > 0
        if deleted:
            logger.info("Deleted standup run id=%s", run_id)
        return deleted

    # ── Team-wide (cross-session) reads — used by ceremony_history to feed
    #    Planning / Analysis with the team's recent standups. standup_history has
    #    no project_name column, so these are recency-based (team-wide).

    def get_recent_reports(self, limit: int = 10) -> list[StandupReport]:
        """Return recent StandupReports across ALL sessions, newest first."""
        rows = self._conn.execute(
            "SELECT report_json FROM standup_history WHERE status = 'success' ORDER BY run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        reports: list[StandupReport] = []
        for row in rows:
            if not row[0]:
                continue
            try:
                reports.append(_dict_to_standup_report(json.loads(row[0])))
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                logger.warning("Failed to deserialize a standup report: %s", exc)
        return reports

    def get_all_history(self, limit: int = 100) -> list[dict]:
        """Return recent standup run metadata across ALL sessions (for cadence + the hub)."""
        rows = self._conn.execute(
            "SELECT id, session_id, run_at, standup_date, sprint_day, confidence_pct, status "
            "FROM standup_history ORDER BY run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "run_at": r[2],
                "standup_date": r[3],
                "sprint_day": r[4],
                "confidence_pct": r[5],
                "status": r[6],
            }
            for r in rows
        ]
