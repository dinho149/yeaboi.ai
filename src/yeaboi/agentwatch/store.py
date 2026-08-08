"""SQLite store for the agentwatch (Agents) family.

Persists monitored-agent telemetry and the family's report history in the
shared sessions.db:

- ``agent_ingest_files``      — per-file ingest cursor (skip-unchanged, detect rotation)
- ``agent_sessions``          — one rollup row per monitored agent session (aggregates only)
- ``agent_security_findings`` — security signals as (pattern, file, line) references
- ``agent_usage_reports`` / ``agent_standup_digests`` / ``agent_security_reports``
                              — saved artifacts, one row per run

The privacy invariant lives at this layer: **no transcript text is ever
stored**. Session rows carry counts and metadata; security findings carry a
pattern label and a location, never the matched content. Tests plant a secret
in fixture transcripts and scan every stored value for it.

Follows the exact patterns of PerformanceStore (performance/store.py): a
separate store class opening its own connection to the same DB, autocommit,
context-manager support, idempotent CREATE-IF-NOT-EXISTS schema. The
``_AGENTWATCH_SCHEMA`` constant is also referenced by sessions.py's v27
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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema — referenced by sessions.py migration v27 AND created on store open
# ---------------------------------------------------------------------------

_AGENTWATCH_SCHEMA = """\
CREATE TABLE IF NOT EXISTS agent_ingest_files (
    path             TEXT PRIMARY KEY,
    source           TEXT NOT NULL DEFAULT '',
    size             INTEGER NOT NULL DEFAULT 0,
    mtime            REAL NOT NULL DEFAULT 0,
    -- Hash of the first line: a same-path file whose head changed was
    -- replaced/rotated, not appended to, so it needs a full reparse even if
    -- size and mtime look plausible.
    first_line_sha   TEXT NOT NULL DEFAULT '',
    last_ingested_at TEXT NOT NULL DEFAULT ''
);
-- Keyed on source_path, NOT session_id: a rollup is computed per transcript
-- file, and one sessionId can legitimately appear in two files (a session
-- resumed from a different cwd, a moved repo, a copied backup). Keying on
-- session_id made the second file REPLACE the first, so one file's tokens
-- vanished from every cost total — and which file won depended on scan order
-- and on which one had changed, so the reported spend oscillated between runs.
CREATE TABLE IF NOT EXISTS agent_sessions (
    source_path      TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL DEFAULT '',  -- indexed, deliberately not unique
    source           TEXT NOT NULL DEFAULT '',
    project_path     TEXT NOT NULL DEFAULT '',
    git_branch       TEXT NOT NULL DEFAULT '',
    cli_version      TEXT NOT NULL DEFAULT '',
    started_at       TEXT NOT NULL DEFAULT '',
    ended_at         TEXT NOT NULL DEFAULT '',
    turns            INTEGER NOT NULL DEFAULT 0,
    -- {model: {input, output, cache_write_5m, cache_write_1h, cache_read, calls}}
    model_usage_json TEXT NOT NULL DEFAULT '{}',
    -- {tool_name: count}
    tool_counts_json TEXT NOT NULL DEFAULT '{}',
    updated_at       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_session ON agent_sessions(session_id);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_ended ON agent_sessions(ended_at);
CREATE TABLE IF NOT EXISTS agent_security_findings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL DEFAULT '',
    severity    TEXT NOT NULL DEFAULT 'info',
    pattern     TEXT NOT NULL DEFAULT '',
    source_path TEXT NOT NULL DEFAULT '',
    line_no     INTEGER NOT NULL DEFAULT 0,
    session_id  TEXT NOT NULL DEFAULT '',
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT '',
    UNIQUE(category, pattern, source_path, line_no)
);
CREATE TABLE IF NOT EXISTS agent_usage_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start   TEXT NOT NULL DEFAULT '',
    period_end     TEXT NOT NULL DEFAULT '',
    report_json    TEXT NOT NULL DEFAULT '',
    origin         TEXT NOT NULL DEFAULT 'generated',
    edited_from_id INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_standup_digests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    on_date        TEXT NOT NULL DEFAULT '',
    report_json    TEXT NOT NULL DEFAULT '',
    origin         TEXT NOT NULL DEFAULT 'generated',
    edited_from_id INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_security_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_date      TEXT NOT NULL DEFAULT '',
    report_json    TEXT NOT NULL DEFAULT '',
    origin         TEXT NOT NULL DEFAULT 'generated',
    edited_from_id INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL
);"""


class AgentWatchStore:
    """SQLite-backed store for monitored-agent telemetry and reports.

    Uses the same database as SessionStore (sessions.db) with dedicated
    agentwatch tables. Same lifecycle patterns as PerformanceStore:
    autocommit mode, context-manager support, explicit close.

    # See docs: "Session Management" — SQLite persistence
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.isolation_level = None  # autocommit
        self._rebuild_sessions_if_keyed_on_session_id()
        self._conn.executescript(_AGENTWATCH_SCHEMA)

    def _rebuild_sessions_if_keyed_on_session_id(self) -> None:
        """Drop an ``agent_sessions`` table left over from the session_id key.

        ``CREATE TABLE IF NOT EXISTS`` cannot change an existing table's primary
        key, and the first cut of this schema keyed rollups on ``session_id``
        (which silently dropped a duplicate-id file's tokens). The table is a
        pure cache derived from the transcripts, so the repair is to drop it and
        clear the ingest cursors — the next ``refresh()`` rebuilds both. Runs on
        every open and costs one pragma query when the shape is already right.
        """
        try:
            cols = self._conn.execute("PRAGMA table_info(agent_sessions)").fetchall()
        except sqlite3.Error:  # pragma: no cover - table missing is the normal path
            return
        if not cols:
            return
        # PRAGMA table_info columns: (cid, name, type, notnull, dflt_value, pk)
        pk_names = {row[1] for row in cols if row[5]}
        if pk_names == {"source_path"}:
            return
        logger.info("agentwatch: rebuilding agent_sessions (was keyed on %s)", ", ".join(sorted(pk_names)) or "nothing")
        self._conn.executescript(
            "DROP TABLE IF EXISTS agent_sessions;\nDELETE FROM agent_ingest_files;"
            if self._has_table("agent_ingest_files")
            else "DROP TABLE IF EXISTS agent_sessions;"
        )

    def _has_table(self, name: str) -> bool:
        row = self._conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
        return row is not None

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> AgentWatchStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    # ── Ingest cursor ─────────────────────────────────────────────────────

    def get_cursor(self, path: str) -> dict | None:
        """Return the stored cursor for a source file, or None."""
        row = self._conn.execute(
            "SELECT source, size, mtime, first_line_sha FROM agent_ingest_files WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        return {"source": row[0], "size": row[1], "mtime": row[2], "first_line_sha": row[3]}

    def set_cursor(self, path: str, *, source: str, size: int, mtime: float, first_line_sha: str) -> None:
        """Upsert the ingest cursor for a source file."""
        self._conn.execute(
            """INSERT INTO agent_ingest_files (path, source, size, mtime, first_line_sha, last_ingested_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   source = excluded.source, size = excluded.size, mtime = excluded.mtime,
                   first_line_sha = excluded.first_line_sha, last_ingested_at = excluded.last_ingested_at""",
            (path, source, size, mtime, first_line_sha, self._now()),
        )

    # ── Session rollups ───────────────────────────────────────────────────

    def upsert_session(
        self,
        session_id: str,
        *,
        source: str,
        source_path: str,
        project_path: str,
        git_branch: str,
        cli_version: str,
        started_at: str,
        ended_at: str,
        turns: int,
        model_usage: dict,
        tool_counts: dict,
    ) -> None:
        """Insert or replace one transcript file's rollup row.

        The conflict target is ``source_path`` — one row per file, never per
        ``session_id`` (see the schema comment): a rollup is derived from one
        file, so replacing on session_id would drop a duplicate-id file's
        tokens from every total.
        """
        self._conn.execute(
            """INSERT INTO agent_sessions
                   (session_id, source, source_path, project_path, git_branch, cli_version,
                    started_at, ended_at, turns, model_usage_json, tool_counts_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_path) DO UPDATE SET
                   session_id = excluded.session_id, source = excluded.source,
                   project_path = excluded.project_path, git_branch = excluded.git_branch,
                   cli_version = excluded.cli_version, started_at = excluded.started_at,
                   ended_at = excluded.ended_at, turns = excluded.turns,
                   model_usage_json = excluded.model_usage_json,
                   tool_counts_json = excluded.tool_counts_json, updated_at = excluded.updated_at""",
            (
                session_id,
                source,
                source_path,
                project_path,
                git_branch,
                cli_version,
                started_at,
                ended_at,
                turns,
                json.dumps(model_usage, sort_keys=True),
                json.dumps(tool_counts, sort_keys=True),
                self._now(),
            ),
        )

    def list_sessions(self, *, since: str = "", until: str = "") -> list[dict]:
        """Return session rollups (parsed JSON columns), newest first.

        ``since``/``until`` filter on ``ended_at`` (ISO strings compare
        lexicographically), so an open window returns everything.
        """
        query = "SELECT * FROM agent_sessions WHERE 1=1"
        params: list[str] = []
        if since:
            query += " AND ended_at >= ?"
            params.append(since)
        if until:
            query += " AND ended_at < ?"
            params.append(until)
        query += " ORDER BY ended_at DESC"
        self._conn.row_factory = sqlite3.Row
        try:
            rows = self._conn.execute(query, params).fetchall()
        finally:
            self._conn.row_factory = None
        out = []
        for row in rows:
            d = dict(row)
            d["model_usage"] = _loads(d.pop("model_usage_json", "{}"), {})
            d["tool_counts"] = _loads(d.pop("tool_counts_json", "{}"), {})
            out.append(d)
        return out

    # ── Security findings ─────────────────────────────────────────────────

    def delete_findings_for_path(self, source_path: str) -> None:
        """Drop a file's findings before a reparse (they are re-derived)."""
        self._conn.execute("DELETE FROM agent_security_findings WHERE source_path = ?", (source_path,))

    def add_finding(
        self,
        *,
        category: str,
        severity: str,
        pattern: str,
        source_path: str,
        line_no: int,
        session_id: str = "",
        detail: str = "",
    ) -> None:
        """Record one security signal. Location + pattern only — never content."""
        self._conn.execute(
            """INSERT OR IGNORE INTO agent_security_findings
                   (category, severity, pattern, source_path, line_no, session_id, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (category, severity, pattern, source_path, line_no, session_id, detail, self._now()),
        )

    def list_findings(self, *, category: str = "") -> list[dict]:
        """Return stored findings, optionally filtered by category."""
        query = "SELECT * FROM agent_security_findings"
        params: list[str] = []
        if category:
            query += " WHERE category = ?"
            params.append(category)
        query += " ORDER BY source_path, line_no"
        self._conn.row_factory = sqlite3.Row
        try:
            rows = self._conn.execute(query, params).fetchall()
        finally:
            self._conn.row_factory = None
        return [dict(row) for row in rows]

    def reset_cursors(self) -> None:
        """Forget every ingest cursor so the next refresh reparses everything."""
        self._conn.execute("DELETE FROM agent_ingest_files")

    def known_source_paths(self) -> list[str]:
        """Every source path the store currently holds state for."""
        rows = self._conn.execute(
            "SELECT path FROM agent_ingest_files "
            "UNION SELECT source_path FROM agent_sessions "
            "UNION SELECT source_path FROM agent_security_findings"
        ).fetchall()
        return [str(row[0]) for row in rows if row[0]]

    def forget_source_path(self, source_path: str) -> None:
        """Drop every trace of one transcript: cursor, rollup and findings.

        Used when a transcript has been deleted from disk. Without this a user
        who remediates a leaked secret by deleting the transcript keeps seeing
        the finding for ever — ``delete_findings_for_path`` only fires on a
        reparse, which a vanished file never gets.
        """
        self._conn.execute("DELETE FROM agent_ingest_files WHERE path = ?", (source_path,))
        self._conn.execute("DELETE FROM agent_sessions WHERE source_path = ?", (source_path,))
        self._conn.execute("DELETE FROM agent_security_findings WHERE source_path = ?", (source_path,))

    # ── Report history (shared shape for the three kinds) ─────────────────

    def record_report(self, kind: str, artifact: object, *, key_date: str = "") -> int:
        """Persist one report artifact under its kind's table; return the row id.

        ``kind`` is "usage" / "standup" / "security"; ``key_date`` fills the
        kind-specific date column (period_start, on_date, scan_date).
        """
        table, date_col = _REPORT_TABLES[kind]
        payload = json.dumps(asdict(artifact), ensure_ascii=False)  # type: ignore[call-overload]
        cursor = self._conn.execute(
            f"INSERT INTO {table} ({date_col}, report_json, created_at) VALUES (?, ?, ?)",  # noqa: S608
            (key_date, payload, self._now()),
        )
        return int(cursor.lastrowid or 0)

    def list_reports(self, kind: str, *, limit: int = 20) -> list[dict]:
        """Return saved reports of one kind, newest first, JSON parsed."""
        table, date_col = _REPORT_TABLES[kind]
        rows = self._conn.execute(
            f"SELECT id, {date_col}, report_json, origin, created_at FROM {table} "  # noqa: S608
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": row[0],
                "key_date": row[1],
                "report": _loads(row[2], {}),
                "origin": row[3],
                "created_at": row[4],
            }
            for row in rows
        ]


_REPORT_TABLES: dict[str, tuple[str, str]] = {
    "usage": ("agent_usage_reports", "period_start"),
    "standup": ("agent_standup_digests", "on_date"),
    "security": ("agent_security_reports", "scan_date"),
}


def _loads(raw: str, default: dict) -> dict:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, dict) else default
