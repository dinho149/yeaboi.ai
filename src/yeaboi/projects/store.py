"""SQLite store for projects.

Persists project rows in the shared ~/.yeaboi/data/sessions.db:
- ``projects`` — one row per project; sessions link to it via
  ``sessions_meta.project_id`` (owned by sessions.py, migration v31)

Follows the exact patterns used by RetroStore (retro/store.py): a separate
store class opening its own connection to the same DB, autocommit mode,
context manager support, idempotent CREATE-IF-NOT-EXISTS schema. The schema
constant is also referenced by sessions.py's v31 migration so an existing DB
gets the table.

``settings_json`` reserves two keys read elsewhere: ``default_analysis_profile_id``
(seeds a scoped planning run's team profile) and ``default_context_deps``
(the context-source toggles a scoped run inherits — read by scope.resolve_scope).

# See docs: "Session Management" — SQLite persistence, schema versioning
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — referenced by sessions.py migration v31 AND created on store open
# ---------------------------------------------------------------------------

PROJECTS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS projects (
    project_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    last_active   TEXT NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0
);"""


def new_project_id() -> str:
    """Mint a project id: ``proj-<8hex>``.

    Not the legacy planning-TUI uuid4 "project_id" (persistence.py /
    projects.json) — that is a disjoint id space this store never touches.
    """
    return f"proj-{secrets.token_hex(4)}"


def _row_to_dict(row: tuple) -> dict:
    settings: dict = {}
    try:
        parsed = json.loads(row[3] or "{}")
        if isinstance(parsed, dict):
            settings = parsed
    except json.JSONDecodeError:
        logger.warning("Corrupt settings_json for project %s; treating as empty", row[0])
    return {
        "project_id": row[0],
        "name": row[1],
        "description": row[2],
        "settings": settings,
        "created_at": row[4],
        "last_active": row[5],
        "archived": bool(row[6]),
    }


class ProjectStore:
    """SQLite-backed store for project rows.

    Uses the same database as SessionStore (sessions.db) with a dedicated
    ``projects`` table. Follows the same patterns: autocommit mode,
    context-manager support, explicit close.

    # See docs: "Session Management" — SQLite persistence
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None  # autocommit
        # Self-healing: the CLI and MCP tools open this store without ever
        # constructing a SessionStore, so create the table here too.
        self._conn.executescript(PROJECTS_SCHEMA)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> ProjectStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Writes ────────────────────────────────────────────────────────────

    def create(self, name: str, description: str = "") -> dict:
        """Create a project and return its row as a dict."""
        project_id = new_project_id()
        now = self._now()
        self._conn.execute(
            """INSERT INTO projects (project_id, name, description, settings_json,
                                     created_at, last_active, archived)
               VALUES (?, ?, ?, '{}', ?, ?, 0)""",
            (project_id, name, description, now, now),
        )
        logger.info("Created project %s (%s)", project_id, name)
        return {
            "project_id": project_id,
            "name": name,
            "description": description,
            "settings": {},
            "created_at": now,
            "last_active": now,
            "archived": False,
        }

    def touch(self, project_id: str) -> None:
        """Bump ``last_active`` — called whenever a session links or runs."""
        self._conn.execute(
            "UPDATE projects SET last_active = ? WHERE project_id = ?",
            (self._now(), project_id),
        )

    def archive(self, project_id: str) -> bool:
        """Archive a project (hidden from default listings). True if a row changed."""
        cursor = self._conn.execute(
            "UPDATE projects SET archived = 1 WHERE project_id = ?",
            (project_id,),
        )
        archived = (cursor.rowcount or 0) > 0
        if archived:
            logger.info("Archived project %s", project_id)
        return archived

    def set_settings(self, project_id: str, settings: dict) -> None:
        """Replace the project's settings dict wholesale (callers merge)."""
        self._conn.execute(
            "UPDATE projects SET settings_json = ?, last_active = ? WHERE project_id = ?",
            (json.dumps(settings, ensure_ascii=False), self._now(), project_id),
        )
        logger.info("Updated settings for project %s (%d key(s))", project_id, len(settings))

    # ── Reads ─────────────────────────────────────────────────────────────

    def get(self, project_id: str) -> dict | None:
        """Return a project row as a dict, or None if not found."""
        row = self._conn.execute(
            "SELECT project_id, name, description, settings_json, created_at, last_active, archived "
            "FROM projects WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return None if row is None else _row_to_dict(row)

    def list_projects(self, include_archived: bool = False) -> list[dict]:
        """Return projects ordered by ``last_active`` descending."""
        rows = self._conn.execute(
            "SELECT project_id, name, description, settings_json, created_at, last_active, archived "
            "FROM projects WHERE archived <= ? ORDER BY last_active DESC",
            (1 if include_archived else 0,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def get_settings(self, project_id: str) -> dict:
        """Return the project's settings dict; empty for a missing project."""
        project = self.get(project_id)
        return project["settings"] if project else {}
