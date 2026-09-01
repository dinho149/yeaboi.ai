"""SQLite store for the Solo world's Weekly Review.

Persists each generated review in the shared sessions.db:
- ``weekly_review_history`` — every run's serialized WeeklyReview

Same shape as ReportingStore: its own connection to the same DB, autocommit,
context-manager support, idempotent CREATE-IF-NOT-EXISTS schema. The
``_WEEKLY_REVIEW_SCHEMA`` constant is also what sessions.py's v32 migration
runs, so an existing DB gets the table.

# See docs: "Session Management" — SQLite persistence, schema versioning
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from yeaboi.agent.state import DeliveredItem, ReviewAction, WeeklyReview, annotations_from

logger = logging.getLogger(__name__)

_WEEKLY_REVIEW_SCHEMA = """\
CREATE TABLE IF NOT EXISTS weekly_review_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL DEFAULT '',
    project_id     TEXT NOT NULL DEFAULT '',
    run_at         TEXT NOT NULL,
    week_label     TEXT NOT NULL DEFAULT '',
    week_start     TEXT NOT NULL DEFAULT '',
    week_end       TEXT NOT NULL DEFAULT '',
    project_name   TEXT NOT NULL DEFAULT '',
    action_count   INTEGER NOT NULL DEFAULT 0,
    report_json    TEXT NOT NULL DEFAULT '',
    origin         TEXT NOT NULL DEFAULT 'generated',
    edited_from_id INTEGER NOT NULL DEFAULT 0
);"""

_HISTORY_COLUMNS = "id, session_id, project_id, run_at, week_label, week_start, week_end, project_name, action_count"


# ---------------------------------------------------------------------------
# Serialisation — WeeklyReview <-> JSON
# ---------------------------------------------------------------------------


def _review_to_json(review: WeeklyReview) -> str:
    return json.dumps(asdict(review), ensure_ascii=False)


def _actions_from(value: object) -> tuple[ReviewAction, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        ReviewAction(
            id=str(a.get("id", "")),
            text=str(a.get("text", "")),
            status=str(a.get("status", "pending") or "pending"),
            origin=str(a.get("origin", "ai") or "ai"),
            week_label=str(a.get("week_label", "")),
        )
        for a in value
        if isinstance(a, dict)
    )


def _dict_to_weekly_review(d: dict) -> WeeklyReview:
    """Rebuild a WeeklyReview from an ``asdict`` tree; every key optional."""
    items = tuple(
        DeliveredItem(
            key=it.get("key", ""),
            title=it.get("title", ""),
            status=it.get("status", ""),
            source=it.get("source", ""),
            assignee=it.get("assignee", ""),
        )
        for it in d.get("delivered_items", ())
        if isinstance(it, dict)
    )
    return WeeklyReview(
        week_label=d.get("week_label", ""),
        week_start=d.get("week_start", ""),
        week_end=d.get("week_end", ""),
        project_id=d.get("project_id", ""),
        project_name=d.get("project_name", ""),
        session_id=d.get("session_id", ""),
        my_name=d.get("my_name", ""),
        standup_dates=tuple(str(x) for x in d.get("standup_dates", ())),
        standup_lines=tuple(str(x) for x in d.get("standup_lines", ())),
        confidence_start=int(d.get("confidence_start", 0) or 0),
        confidence_end=int(d.get("confidence_end", 0) or 0),
        confidence_label=d.get("confidence_label", ""),
        sprint_name=d.get("sprint_name", ""),
        sprint_day=int(d.get("sprint_day", 0) or 0),
        sprint_total_days=int(d.get("sprint_total_days", 0) or 0),
        delivered_items=items,
        planned_story_count=int(d.get("planned_story_count", 0) or 0),
        plan_status=d.get("plan_status", ""),
        plan_line=d.get("plan_line", ""),
        summary=d.get("summary", ""),
        went_well=tuple(str(x) for x in d.get("went_well", ())),
        to_change=tuple(str(x) for x in d.get("to_change", ())),
        actions=_actions_from(d.get("actions")),
        carried_actions=_actions_from(d.get("carried_actions")),
        warnings=tuple(str(x) for x in d.get("warnings", ())),
        generated_at=d.get("generated_at", ""),
        annotations=annotations_from(d.get("annotations")),
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class WeeklyReviewStore:
    """SQLite-backed store for generated weekly reviews.

    # See docs: "Session Management" — SQLite persistence
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None  # autocommit
        self._conn.executescript(_WEEKLY_REVIEW_SCHEMA)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    def __enter__(self) -> WeeklyReviewStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Run history ───────────────────────────────────────────────────────

    def record_run(self, review: WeeklyReview, *, origin: str = "generated", edited_from_id: int = 0) -> int:
        """Persist a review and return its history row id."""
        cursor = self._conn.execute(
            """INSERT INTO weekly_review_history
                   (session_id, project_id, run_at, week_label, week_start, week_end, project_name,
                    action_count, report_json, origin, edited_from_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                review.session_id,
                review.project_id,
                self._now(),
                review.week_label,
                review.week_start,
                review.week_end,
                review.project_name,
                len(review.actions),
                _review_to_json(review),
                origin,
                edited_from_id,
            ),
        )
        logger.info(
            "Recorded weekly review: week=%s project=%s actions=%d carried=%d",
            review.week_label,
            review.project_id or "-",
            len(review.actions),
            len(review.carried_actions),
        )
        return int(cursor.lastrowid or 0)

    def _session_clause(self, session_ids: tuple[str, ...] | None) -> tuple[str, tuple]:
        """``session_ids`` is the hard filter a ProjectScope resolves to; an
        empty tuple matches nothing, ``None`` matches every session."""
        if session_ids is None:
            return "", ()
        if not session_ids:
            return " WHERE 0", ()
        slots = ", ".join("?" for _ in session_ids)
        return f" WHERE session_id IN ({slots})", tuple(session_ids)

    def _load(self, row, *, what: str) -> WeeklyReview | None:
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_weekly_review(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize weekly review %s: %s", what, exc)
            return None

    def get_latest_report(self, session_ids: tuple[str, ...] | None = None) -> WeeklyReview | None:
        """The newest review (within ``session_ids`` when given), or None."""
        where, params = self._session_clause(session_ids)
        row = self._conn.execute(
            f"SELECT report_json FROM weekly_review_history{where} ORDER BY run_at DESC, id DESC LIMIT 1",  # noqa: S608 — placeholders, not values
            params,
        ).fetchone()
        return self._load(row, what="latest")

    def get_recent_reports(self, limit: int = 10, session_ids: tuple[str, ...] | None = None) -> list[WeeklyReview]:
        """Recent reviews newest first, hard-filtered to ``session_ids`` when given."""
        where, params = self._session_clause(session_ids)
        rows = self._conn.execute(
            f"SELECT report_json FROM weekly_review_history{where} ORDER BY run_at DESC, id DESC LIMIT ?",  # noqa: S608 — placeholders, not values
            (*params, limit),
        ).fetchall()
        reviews = [self._load(r, what="recent") for r in rows]
        return [r for r in reviews if r is not None]

    def _history_rows(self, rows) -> list[dict]:
        return [
            {
                "id": r[0],
                "session_id": r[1],
                "project_id": r[2],
                "run_at": r[3],
                "week_label": r[4],
                "week_start": r[5],
                "week_end": r[6],
                "project_name": r[7],
                "action_count": r[8],
            }
            for r in rows
        ]

    def get_history(self, session_id: str = "", limit: int = 30) -> list[dict]:
        """Run metadata for one session (or every session when blank), newest first."""
        if session_id:
            rows = self._conn.execute(
                f"SELECT {_HISTORY_COLUMNS} FROM weekly_review_history "  # noqa: S608 — column list is a constant
                "WHERE session_id = ? ORDER BY run_at DESC, id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {_HISTORY_COLUMNS} FROM weekly_review_history ORDER BY run_at DESC, id DESC LIMIT ?",  # noqa: S608
                (limit,),
            ).fetchall()
        return self._history_rows(rows)

    def get_all_history(self, limit: int = 100, session_ids: tuple[str, ...] | None = None) -> list[dict]:
        """Run metadata across sessions for the hub, hard-filtered to ``session_ids`` when given."""
        where, params = self._session_clause(session_ids)
        rows = self._conn.execute(
            f"SELECT {_HISTORY_COLUMNS} FROM weekly_review_history{where} ORDER BY run_at DESC, id DESC LIMIT ?",  # noqa: S608
            (*params, limit),
        ).fetchall()
        return self._history_rows(rows)

    def get_run_by_id(self, run_id: int) -> WeeklyReview | None:
        row = self._conn.execute("SELECT report_json FROM weekly_review_history WHERE id = ?", (run_id,)).fetchone()
        return self._load(row, what=f"run id={run_id}")

    def delete_run(self, run_id: int) -> bool:
        cursor = self._conn.execute("DELETE FROM weekly_review_history WHERE id = ?", (run_id,))
        deleted = (cursor.rowcount or 0) > 0
        if deleted:
            logger.info("Deleted weekly review run id=%s", run_id)
        return deleted
