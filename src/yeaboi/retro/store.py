"""SQLite store for the Retro mode.

Persists each completed retrospective in the shared ~/.scrum-agent/sessions.db:
- ``retro_history`` — every run's serialized RetroReport (all cards + participants)

Follows the exact patterns used by StandupStore (standup/store.py): a separate
store class opening its own connection to the same DB, autocommit mode, context
manager support, idempotent CREATE-IF-NOT-EXISTS schema. The schema constant is
also referenced by sessions.py's v7 migration so an existing DB gets the table.

# See docs: "Session Management" — SQLite persistence, schema versioning
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.agent.state import RetroCard, RetroReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — referenced by sessions.py migration v7 AND created on store open
# ---------------------------------------------------------------------------

_RETRO_SCHEMA = """\
CREATE TABLE IF NOT EXISTS retro_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    run_at       TEXT NOT NULL,
    retro_date   TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    card_count   INTEGER NOT NULL DEFAULT 0,
    report_json  TEXT NOT NULL DEFAULT ''
);"""


# ---------------------------------------------------------------------------
# Serialisation helpers — RetroReport <-> JSON (same pattern as standup/store.py)
# ---------------------------------------------------------------------------


def _retro_report_to_json(report: RetroReport) -> str:
    """Serialize a RetroReport to a JSON string (asdict recurses into RetroCard)."""
    return json.dumps(asdict(report), ensure_ascii=False)


def _dict_to_retro_report(d: dict) -> RetroReport:
    """Reconstruct a RetroReport from a JSON-parsed dict.

    Uses ``.get()`` with defaults for every field so reports serialized by an
    older version (missing keys) still deserialize — see CLAUDE.md
    "Frozen dataclass backward compatibility".
    """

    def _card(c: dict) -> RetroCard:
        return RetroCard(
            id=c.get("id", ""),
            grid=c.get("grid", ""),
            text=c.get("text", ""),
            author=c.get("author", ""),
            created_at=c.get("created_at", ""),
            origin=c.get("origin", "web"),
            # JSON turned each (emoji, count) tuple into an [emoji, count] list — rebuild tuples.
            reactions=tuple((str(r[0]), int(r[1])) for r in c.get("reactions", ()) if len(r) == 2),
            status=c.get("status", ""),
        )

    cards = tuple(_card(c) for c in d.get("cards", ()))
    # Carried action items are RetroCards too (last sprint's items + their statuses).
    carried = tuple(_card(c) for c in d.get("carried_action_items", ()))
    return RetroReport(
        date=d.get("date", ""),
        session_id=d.get("session_id", ""),
        project_name=d.get("project_name", ""),
        sprint_name=d.get("sprint_name", ""),
        cards=cards,
        participants=tuple(d.get("participants", ())),
        generated_at=d.get("generated_at", ""),
        carried_action_items=carried,
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class RetroStore:
    """SQLite-backed store for completed retrospectives.

    Uses the same database as SessionStore (sessions.db) with a dedicated
    ``retro_history`` table. Follows the same patterns: autocommit mode,
    context-manager support, explicit close.

    # See docs: "Session Management" — SQLite persistence
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None  # autocommit
        self._conn.executescript(_RETRO_SCHEMA)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> RetroStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    # ── Run history ───────────────────────────────────────────────────────

    def record_run(self, report: RetroReport) -> int:
        """Persist a completed retro and return its history row id."""
        report_json = _retro_report_to_json(report)
        cursor = self._conn.execute(
            """INSERT INTO retro_history
                   (session_id, run_at, retro_date, project_name, card_count, report_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                report.session_id,
                self._now(),
                report.date,
                report.project_name,
                len(report.cards),
                report_json,
            ),
        )
        logger.info(
            "Recorded retro run: session=%s date=%s cards=%d",
            report.session_id,
            report.date,
            len(report.cards),
        )
        return int(cursor.lastrowid or 0)

    def get_latest_report(self, session_id: str) -> RetroReport | None:
        """Return the most recent RetroReport for a session, or None."""
        row = self._conn.execute(
            "SELECT report_json FROM retro_history WHERE session_id = ? ORDER BY run_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_retro_report(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize retro report for %s: %s", session_id, exc)
            return None

    def get_history(self, session_id: str, limit: int = 30) -> list[dict]:
        """Return recent retro run metadata (newest first) for a session.

        Each row carries its ``id`` so the saved-runs hub can reopen or delete a
        specific run via ``get_run_by_id`` / ``delete_run``.
        """
        rows = self._conn.execute(
            "SELECT id, run_at, retro_date, project_name, card_count FROM retro_history "
            "WHERE session_id = ? ORDER BY run_at DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [
            {"id": r[0], "run_at": r[1], "retro_date": r[2], "project_name": r[3], "card_count": r[4]} for r in rows
        ]

    def get_run_by_id(self, run_id: int) -> RetroReport | None:
        """Return the RetroReport for a single history row, or None if missing/corrupt."""
        row = self._conn.execute(
            "SELECT report_json FROM retro_history WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_retro_report(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize retro run id=%s: %s", run_id, exc)
            return None

    def delete_run(self, run_id: int) -> bool:
        """Delete a single retro history row. Returns True if a row was removed."""
        cursor = self._conn.execute("DELETE FROM retro_history WHERE id = ?", (run_id,))
        deleted = (cursor.rowcount or 0) > 0
        if deleted:
            logger.info("Deleted retro run id=%s", run_id)
        return deleted

    # ── Team-wide (cross-session) reads — used by ceremony_history to feed
    #    Planning / Analysis with the team's recent retros regardless of which
    #    session they ran under. See docs: "Session Management".

    def get_recent_reports(self, limit: int = 5, project_name: str = "") -> list[RetroReport]:
        """Return recent RetroReports across ALL sessions, newest first.

        When ``project_name`` is given, rows matching it sort first (project-first),
        then by recency — so a plan for project X sees X's retros ahead of others'.
        """
        if project_name:
            # (project_name = ?) is 1 for matches, 0 otherwise → matches sort first.
            rows = self._conn.execute(
                "SELECT report_json FROM retro_history ORDER BY (project_name = ?) DESC, run_at DESC LIMIT ?",
                (project_name, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT report_json FROM retro_history ORDER BY run_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        reports: list[RetroReport] = []
        for row in rows:
            if not row[0]:
                continue
            try:
                reports.append(_dict_to_retro_report(json.loads(row[0])))
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                logger.warning("Failed to deserialize a retro report: %s", exc)
        return reports

    def get_all_history(self, limit: int = 100) -> list[dict]:
        """Return recent retro run metadata across ALL sessions (for cadence + the hub)."""
        rows = self._conn.execute(
            "SELECT id, session_id, run_at, retro_date, project_name, card_count FROM retro_history "
            "ORDER BY run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "run_at": r[2],
                "retro_date": r[3],
                "project_name": r[4],
                "card_count": r[5],
            }
            for r in rows
        ]
