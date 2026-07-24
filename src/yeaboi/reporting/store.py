"""SQLite store for the Reporting mode.

Persists each generated delivery report in the shared ~/.scrum-agent/sessions.db:
- ``reporting_history`` — every run's serialized DeliveryReport (narrative + evidence)

Follows the exact patterns used by RetroStore (retro/store.py): a separate store
class opening its own connection to the same DB, autocommit mode, context-manager
support, idempotent CREATE-IF-NOT-EXISTS schema. The ``_REPORTING_SCHEMA`` constant
is also referenced by sessions.py's v9 migration so an existing DB gets the table.

# See docs: "Session Management" — SQLite persistence, schema versioning
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.agent.state import DeliveredItem, DeliveryReport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — referenced by sessions.py migration v9 AND created on store open
# ---------------------------------------------------------------------------

_REPORTING_SCHEMA = """\
CREATE TABLE IF NOT EXISTS reporting_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL DEFAULT '',
    run_at       TEXT NOT NULL,
    period       TEXT NOT NULL DEFAULT '',
    period_end   TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    item_count   INTEGER NOT NULL DEFAULT 0,
    report_json  TEXT NOT NULL DEFAULT ''
);"""


# ---------------------------------------------------------------------------
# Serialisation helpers — DeliveryReport <-> JSON (same pattern as retro/store.py)
# ---------------------------------------------------------------------------


def _report_to_json(report: DeliveryReport) -> str:
    """Serialize a DeliveryReport to a JSON string (asdict recurses into items)."""
    return json.dumps(asdict(report), ensure_ascii=False)


def _dict_to_report(d: dict) -> DeliveryReport:
    """Reconstruct a DeliveryReport from a JSON-parsed dict.

    Uses ``.get()`` with defaults for every field so reports serialized by an older
    version (missing keys) still deserialize — see CLAUDE.md "Frozen dataclass
    backward compatibility". JSON turns each tuple into a list, so themes/metrics/
    emoji_theme are rebuilt back into tuples-of-tuples.
    """
    items = tuple(
        DeliveredItem(
            key=it.get("key", ""),
            title=it.get("title", ""),
            status=it.get("status", ""),
            source=it.get("source", ""),
            assignee=it.get("assignee", ""),
        )
        for it in d.get("delivered_items", ())
    )
    themes = tuple((str(t[0]), tuple(str(o) for o in t[1])) for t in d.get("themes", ()) if len(t) == 2)
    metrics = tuple((str(m[0]), str(m[1])) for m in d.get("metrics", ()) if len(m) == 2)
    emoji_theme = tuple((str(e[0]), str(e[1])) for e in d.get("emoji_theme", ()) if len(e) == 2)
    return DeliveryReport(
        period_label=d.get("period_label", ""),
        period_start=d.get("period_start", ""),
        period_end=d.get("period_end", ""),
        project_name=d.get("project_name", ""),
        sprint_names=tuple(d.get("sprint_names", ())),
        headline=d.get("headline", ""),
        executive_summary=d.get("executive_summary", ""),
        themes=themes,
        highlights=tuple(d.get("highlights", ())),
        metrics=metrics,
        delivered_items=items,
        emoji_theme=emoji_theme,
        warnings=tuple(d.get("warnings", ())),
        generated_at=d.get("generated_at", ""),
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ReportingStore:
    """SQLite-backed store for generated delivery reports.

    Uses the same database as SessionStore (sessions.db) with a dedicated
    ``reporting_history`` table. Follows the same patterns as RetroStore:
    autocommit mode, context-manager support, explicit close.

    # See docs: "Session Management" — SQLite persistence
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None  # autocommit
        self._conn.executescript(_REPORTING_SCHEMA)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> ReportingStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    # ── Run history ───────────────────────────────────────────────────────

    def record_run(self, report: DeliveryReport, *, session_id: str = "") -> int:
        """Persist a generated delivery report and return its history row id."""
        cursor = self._conn.execute(
            """INSERT INTO reporting_history
                   (session_id, run_at, period, period_end, project_name, item_count, report_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                self._now(),
                report.period_label,
                report.period_end,
                report.project_name,
                len(report.delivered_items),
                _report_to_json(report),
            ),
        )
        logger.info(
            "Recorded delivery report: session=%s period=%s items=%d",
            session_id,
            report.period_label,
            len(report.delivered_items),
        )
        return int(cursor.lastrowid or 0)

    def get_latest_report(self, session_id: str = "") -> DeliveryReport | None:
        """Return the most recent DeliveryReport (optionally for a session), or None."""
        if session_id:
            row = self._conn.execute(
                "SELECT report_json FROM reporting_history WHERE session_id = ? ORDER BY run_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT report_json FROM reporting_history ORDER BY run_at DESC LIMIT 1"
            ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_report(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize delivery report for %s: %s", session_id, exc)
            return None

    def get_history(self, session_id: str = "", limit: int = 30) -> list[dict]:
        """Return recent delivery-report run metadata (newest first).

        Each row carries its ``id`` so the saved-runs hub can reopen or delete a
        specific run via ``get_run_by_id`` / ``delete_run``.
        """
        if session_id:
            rows = self._conn.execute(
                "SELECT id, run_at, period, period_end, project_name, item_count FROM reporting_history "
                "WHERE session_id = ? ORDER BY run_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, run_at, period, period_end, project_name, item_count FROM reporting_history "
                "ORDER BY run_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"id": r[0], "run_at": r[1], "period": r[2], "period_end": r[3], "project_name": r[4], "item_count": r[5]}
            for r in rows
        ]

    def get_all_history(self, limit: int = 100) -> list[dict]:
        """Return recent delivery-report run metadata across ALL sessions (for the hub).

        Reporting piggybacks on the latest planning session, so the hub lists runs
        across every session (matching how Analysis lists all saved sessions).
        """
        rows = self._conn.execute(
            "SELECT id, session_id, run_at, period, period_end, project_name, item_count FROM reporting_history "
            "ORDER BY run_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "run_at": r[2],
                "period": r[3],
                "period_end": r[4],
                "project_name": r[5],
                "item_count": r[6],
            }
            for r in rows
        ]

    def get_run_by_id(self, run_id: int) -> DeliveryReport | None:
        """Return the DeliveryReport for a single history row, or None if missing/corrupt."""
        row = self._conn.execute(
            "SELECT report_json FROM reporting_history WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_report(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize delivery report run id=%s: %s", run_id, exc)
            return None

    def delete_run(self, run_id: int) -> bool:
        """Delete a single delivery-report history row. Returns True if a row was removed."""
        cursor = self._conn.execute("DELETE FROM reporting_history WHERE id = ?", (run_id,))
        deleted = (cursor.rowcount or 0) > 0
        if deleted:
            logger.info("Deleted delivery report run id=%s", run_id)
        return deleted
