"""SQLite store for the Performance mode.

Persists each engineer's performance artifacts in the shared
~/.scrum-agent/sessions.db:
- ``performance_one_on_ones`` — every 1:1 prep + completion (serialized report + actions)
- ``performance_reviews``     — every 6-month review
- ``performance_notes``       — the lead's running free-text notes per engineer

Follows the exact patterns used by StandupStore / RetroStore (standup/store.py,
retro/store.py): a separate store class opening its own connection to the same DB,
autocommit mode, context-manager support, idempotent CREATE-IF-NOT-EXISTS schema.
The ``_PERFORMANCE_SCHEMA`` constant is also referenced by sessions.py's v8
migration so an existing DB gets the tables.

# See docs: "Session Management" — SQLite persistence, schema versioning
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from yeaboi.agent.state import OneOnOnePrep, OneOnOneRecord, SixMonthReview

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — referenced by sessions.py migration v8 AND created on store open
# ---------------------------------------------------------------------------

_PERFORMANCE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS performance_one_on_ones (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    engineer         TEXT NOT NULL,
    session_id       TEXT NOT NULL DEFAULT '',
    kind             TEXT NOT NULL DEFAULT 'prep',
    on_date          TEXT NOT NULL DEFAULT '',
    report_json      TEXT NOT NULL DEFAULT '',
    action_items_json TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS performance_reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    engineer     TEXT NOT NULL,
    session_id   TEXT NOT NULL DEFAULT '',
    period_start TEXT NOT NULL DEFAULT '',
    period_end   TEXT NOT NULL DEFAULT '',
    report_json  TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS performance_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    engineer   TEXT NOT NULL,
    note_text  TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);"""


# ---------------------------------------------------------------------------
# Serialisation helpers — report <-> JSON (same pattern as standup/store.py)
# ---------------------------------------------------------------------------


def _prep_to_json(prep: OneOnOnePrep) -> str:
    """Serialize a OneOnOnePrep to a JSON string (asdict turns tuples into lists)."""
    return json.dumps(asdict(prep), ensure_ascii=False)


def _dict_to_prep(d: dict) -> OneOnOnePrep:
    """Reconstruct a OneOnOnePrep, defaulting every field (backward-compat)."""
    return OneOnOnePrep(
        engineer=d.get("engineer", ""),
        date=d.get("date", ""),
        talking_points=tuple(d.get("talking_points", ())),
        feedback=tuple(d.get("feedback", ())),
        goals=tuple(d.get("goals", ())),
        gaps=tuple(d.get("gaps", ())),
        improvements=tuple(d.get("improvements", ())),
        carried_action_items=tuple(d.get("carried_action_items", ())),
        activity_summary=d.get("activity_summary", ""),
        warnings=tuple(d.get("warnings", ())),
    )


def _record_to_json(record: OneOnOneRecord) -> str:
    """Serialize a OneOnOneRecord to a JSON string."""
    return json.dumps(asdict(record), ensure_ascii=False)


def _dict_to_record(d: dict) -> OneOnOneRecord:
    """Reconstruct a OneOnOneRecord, defaulting every field (backward-compat)."""
    return OneOnOneRecord(
        engineer=d.get("engineer", ""),
        date=d.get("date", ""),
        transcript=d.get("transcript", ""),
        email_subject=d.get("email_subject", ""),
        email_summary=d.get("email_summary", ""),
        action_items=tuple(d.get("action_items", ())),
        highlights=tuple(d.get("highlights", ())),
        warnings=tuple(d.get("warnings", ())),
    )


def _review_to_json(review: SixMonthReview) -> str:
    """Serialize a SixMonthReview to a JSON string."""
    return json.dumps(asdict(review), ensure_ascii=False)


def _dict_to_review(d: dict) -> SixMonthReview:
    """Reconstruct a SixMonthReview, defaulting every field (backward-compat)."""
    return SixMonthReview(
        engineer=d.get("engineer", ""),
        period_start=d.get("period_start", ""),
        period_end=d.get("period_end", ""),
        strengths=tuple(d.get("strengths", ())),
        areas_for_improvement=tuple(d.get("areas_for_improvement", ())),
        achievements=tuple(d.get("achievements", ())),
        goals=tuple(d.get("goals", ())),
        overall=d.get("overall", ""),
        framework_used=d.get("framework_used", ""),
        warnings=tuple(d.get("warnings", ())),
    )


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class PerformanceStore:
    """SQLite-backed store for per-engineer 1:1s, reviews, and lead notes.

    Uses the same database as SessionStore (sessions.db) with dedicated
    performance tables. Follows the same patterns as StandupStore / RetroStore:
    autocommit mode, context-manager support, explicit close.

    # See docs: "Session Management" — SQLite persistence
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None  # autocommit
        self._conn.executescript(_PERFORMANCE_SCHEMA)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> PerformanceStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    # ── 1:1 prep ──────────────────────────────────────────────────────────

    def record_prep(self, prep: OneOnOnePrep, *, session_id: str = "") -> int:
        """Persist a 1:1 prep and return its row id."""
        cursor = self._conn.execute(
            """INSERT INTO performance_one_on_ones
                   (engineer, session_id, kind, on_date, report_json, action_items_json, created_at)
               VALUES (?, ?, 'prep', ?, ?, ?, ?)""",
            (
                prep.engineer,
                session_id,
                prep.date,
                _prep_to_json(prep),
                json.dumps(list(prep.carried_action_items)),
                self._now(),
            ),
        )
        logger.info("Recorded 1:1 prep: engineer=%s date=%s", prep.engineer, prep.date)
        return int(cursor.lastrowid or 0)

    # ── 1:1 completion ────────────────────────────────────────────────────

    def record_completion(self, record: OneOnOneRecord, *, session_id: str = "") -> int:
        """Persist a completed 1:1 (transcript + email summary + actions)."""
        cursor = self._conn.execute(
            """INSERT INTO performance_one_on_ones
                   (engineer, session_id, kind, on_date, report_json, action_items_json, created_at)
               VALUES (?, ?, 'completion', ?, ?, ?, ?)""",
            (
                record.engineer,
                session_id,
                record.date,
                _record_to_json(record),
                json.dumps(list(record.action_items)),
                self._now(),
            ),
        )
        logger.info("Recorded 1:1 completion: engineer=%s date=%s", record.engineer, record.date)
        return int(cursor.lastrowid or 0)

    def get_open_action_items(self, engineer: str) -> tuple[str, ...]:
        """Return the action items from the engineer's most recent 1:1 completion.

        This is what closes the Prep↔Completion loop: run_one_on_one_prep() calls
        this to seed the next prep with what was agreed last time. Empty tuple when
        the engineer has no recorded completion yet.
        """
        row = self._conn.execute(
            "SELECT action_items_json FROM performance_one_on_ones "
            "WHERE engineer = ? AND kind = 'completion' ORDER BY created_at DESC LIMIT 1",
            (engineer,),
        ).fetchone()
        if row is None or not row[0]:
            return ()
        try:
            items = json.loads(row[0])
            return tuple(str(i) for i in items) if isinstance(items, list) else ()
        except (json.JSONDecodeError, TypeError):
            return ()

    def get_recent_completions(self, engineer: str, limit: int = 12) -> list[OneOnOneRecord]:
        """Return the engineer's recent completed 1:1s, newest first."""
        rows = self._conn.execute(
            "SELECT report_json FROM performance_one_on_ones "
            "WHERE engineer = ? AND kind = 'completion' ORDER BY created_at DESC LIMIT ?",
            (engineer, limit),
        ).fetchall()
        out: list[OneOnOneRecord] = []
        for row in rows:
            if not row[0]:
                continue
            try:
                out.append(_dict_to_record(json.loads(row[0])))
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                logger.warning("Failed to deserialize a 1:1 completion: %s", exc)
        return out

    def get_latest_prep(self, engineer: str) -> OneOnOnePrep | None:
        """Return the engineer's most recent 1:1 prep, or None."""
        row = self._conn.execute(
            "SELECT report_json FROM performance_one_on_ones "
            "WHERE engineer = ? AND kind = 'prep' ORDER BY created_at DESC LIMIT 1",
            (engineer,),
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_prep(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize latest prep for %s: %s", engineer, exc)
            return None

    def get_one_on_one_by_id(self, run_id: int) -> tuple[str, OneOnOnePrep | OneOnOneRecord] | None:
        """Return ``(kind, artifact)`` for a single 1:1 row, or None if missing/corrupt.

        The ``performance_one_on_ones`` table holds both preps and completions, so the
        stored ``kind`` selects which dataclass the JSON deserializes into — the
        saved-runs hub uses ``kind`` to pick the right formatter.
        """
        row = self._conn.execute(
            "SELECT kind, report_json FROM performance_one_on_ones WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None or not row[1]:
            return None
        kind = row[0] or "prep"
        try:
            data = json.loads(row[1])
            artifact = _dict_to_record(data) if kind == "completion" else _dict_to_prep(data)
            return (kind, artifact)
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize 1:1 run id=%s: %s", run_id, exc)
            return None

    def delete_one_on_one(self, run_id: int) -> bool:
        """Delete a single 1:1 (prep or completion) row. Returns True if removed."""
        cursor = self._conn.execute("DELETE FROM performance_one_on_ones WHERE id = ?", (run_id,))
        deleted = (cursor.rowcount or 0) > 0
        if deleted:
            logger.info("Deleted 1:1 run id=%s", run_id)
        return deleted

    # ── 6-month review ────────────────────────────────────────────────────

    def record_review(self, review: SixMonthReview, *, session_id: str = "") -> int:
        """Persist a 6-month review and return its row id."""
        cursor = self._conn.execute(
            """INSERT INTO performance_reviews
                   (engineer, session_id, period_start, period_end, report_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                review.engineer,
                session_id,
                review.period_start,
                review.period_end,
                _review_to_json(review),
                self._now(),
            ),
        )
        logger.info(
            "Recorded 6-month review: engineer=%s period=%s..%s",
            review.engineer,
            review.period_start,
            review.period_end,
        )
        return int(cursor.lastrowid or 0)

    def get_latest_review(self, engineer: str) -> SixMonthReview | None:
        """Return the engineer's most recent 6-month review, or None."""
        row = self._conn.execute(
            "SELECT report_json FROM performance_reviews WHERE engineer = ? ORDER BY created_at DESC LIMIT 1",
            (engineer,),
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_review(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize latest review for %s: %s", engineer, exc)
            return None

    def get_review_by_id(self, run_id: int) -> SixMonthReview | None:
        """Return the SixMonthReview for a single review row, or None if missing/corrupt."""
        row = self._conn.execute(
            "SELECT report_json FROM performance_reviews WHERE id = ?",
            (run_id,),
        ).fetchone()
        if row is None or not row[0]:
            return None
        try:
            return _dict_to_review(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            logger.warning("Failed to deserialize review run id=%s: %s", run_id, exc)
            return None

    def delete_review(self, run_id: int) -> bool:
        """Delete a single 6-month review row. Returns True if removed."""
        cursor = self._conn.execute("DELETE FROM performance_reviews WHERE id = ?", (run_id,))
        deleted = (cursor.rowcount or 0) > 0
        if deleted:
            logger.info("Deleted review run id=%s", run_id)
        return deleted

    # ── Notes ─────────────────────────────────────────────────────────────

    def add_note(self, engineer: str, note_text: str) -> int:
        """Append a free-text note the lead recorded about an engineer."""
        cursor = self._conn.execute(
            "INSERT INTO performance_notes (engineer, note_text, created_at) VALUES (?, ?, ?)",
            (engineer, note_text, self._now()),
        )
        logger.info("Recorded performance note: engineer=%s len=%d", engineer, len(note_text or ""))
        return int(cursor.lastrowid or 0)

    def get_notes(self, engineer: str, limit: int = 50) -> list[dict]:
        """Return the engineer's notes, newest first (each row carries its ``id``)."""
        rows = self._conn.execute(
            "SELECT id, note_text, created_at FROM performance_notes "
            "WHERE engineer = ? ORDER BY created_at DESC LIMIT ?",
            (engineer, limit),
        ).fetchall()
        return [{"id": r[0], "note_text": r[1], "created_at": r[2]} for r in rows]

    def delete_note(self, note_id: int) -> bool:
        """Delete a single performance note row. Returns True if removed."""
        cursor = self._conn.execute("DELETE FROM performance_notes WHERE id = ?", (note_id,))
        deleted = (cursor.rowcount or 0) > 0
        if deleted:
            logger.info("Deleted performance note id=%s", note_id)
        return deleted

    # ── Per-engineer saved-runs hub ───────────────────────────────────────────

    def get_engineer_history(self, engineer: str, limit: int = 100) -> list[dict]:
        """Return every saved artifact for an engineer, newest first, for the hub.

        Merges the three per-engineer tables (1:1 preps + completions, 6-month
        reviews, notes) into one list of lightweight rows. Each row carries a
        ``kind`` (``prep`` | ``completion`` | ``review`` | ``note``), its table
        ``id``, ``created_at``, and a short ``title`` — enough for the run-hub list
        to render and, on open/delete, dispatch to the right getter/deleter by kind.
        """
        rows: list[dict] = []
        for r in self._conn.execute(
            "SELECT id, kind, on_date, created_at FROM performance_one_on_ones "
            "WHERE engineer = ? ORDER BY created_at DESC LIMIT ?",
            (engineer, limit),
        ).fetchall():
            kind = r[1] or "prep"
            label = "1:1 Prep" if kind == "prep" else "1:1 Summary"
            rows.append({"kind": kind, "id": r[0], "created_at": r[3], "title": f"{label} — {r[2] or r[3][:10]}"})
        for r in self._conn.execute(
            "SELECT id, period_start, period_end, created_at FROM performance_reviews "
            "WHERE engineer = ? ORDER BY created_at DESC LIMIT ?",
            (engineer, limit),
        ).fetchall():
            span = f"{r[1]}..{r[2]}" if (r[1] or r[2]) else r[3][:10]
            rows.append({"kind": "review", "id": r[0], "created_at": r[3], "title": f"6-Month Review — {span}"})
        for r in self._conn.execute(
            "SELECT id, note_text, created_at FROM performance_notes "
            "WHERE engineer = ? ORDER BY created_at DESC LIMIT ?",
            (engineer, limit),
        ).fetchall():
            snippet = (r[1] or "").strip().replace("\n", " ")
            if len(snippet) > 48:
                snippet = snippet[:47] + "…"
            rows.append({"kind": "note", "id": r[0], "created_at": r[2], "title": f"Note — {snippet or r[2][:10]}"})
        rows.sort(key=lambda d: d["created_at"], reverse=True)
        return rows[:limit]

    # ── Team-wide (cross-engineer) reads — used by performance/context.py to
    #    feed Planning / Analysis with per-engineer open actions + focus areas.

    def get_all_open_action_items(self) -> dict[str, tuple[str, ...]]:
        """Return ``{engineer: open action items}`` for every engineer with a 1:1.

        Uses each engineer's most recent completion only (one round of actions per
        person), so the planning feed reflects the latest agreed next steps.
        """
        rows = self._conn.execute(
            "SELECT engineer, action_items_json, created_at FROM performance_one_on_ones "
            "WHERE kind = 'completion' ORDER BY created_at DESC"
        ).fetchall()
        out: dict[str, tuple[str, ...]] = {}
        for engineer, actions_json, _created in rows:
            if engineer in out:  # already have this engineer's newest → skip older
                continue
            try:
                items = json.loads(actions_json) if actions_json else []
            except (json.JSONDecodeError, TypeError):
                items = []
            out[engineer] = tuple(str(i) for i in items) if isinstance(items, list) else ()
        return out

    def get_recent_reviews(self, limit: int = 20) -> list[SixMonthReview]:
        """Return recent 6-month reviews across all engineers, newest first."""
        rows = self._conn.execute(
            "SELECT report_json FROM performance_reviews ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out: list[SixMonthReview] = []
        for row in rows:
            if not row[0]:
                continue
            try:
                out.append(_dict_to_review(json.loads(row[0])))
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                logger.warning("Failed to deserialize a review: %s", exc)
        return out
