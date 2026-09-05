"""SQLite store for the agentwatch (Agents) family.

Persists monitored-agent telemetry and the family's report history in the
shared sessions.db:

- ``agent_ingest_files``      — per-file ingest cursor (skip-unchanged, detect rotation, resume offset)
- ``agent_sessions``          — one rollup row per monitored agent session (aggregates only)
- ``agent_session_days``      — the same rollup split per calendar day and model (windows + trend)
- ``agent_seen_requests``     — every (message id, request id) already counted, and by which file
- ``agent_security_findings`` — security signals as (pattern, file, line) references
- ``agent_usage_reports`` / ``agent_security_reports`` / ``agent_advisor_reports``
  — saved artifacts, one row per run. ``agent_standup_digests`` is retained for
  history and downgrade only: the Agent Standup mode was withdrawn and no code
  reads the table.

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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
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
    last_ingested_at TEXT NOT NULL DEFAULT '',
    -- Where the last parse stopped, so an appended transcript resumes there.
    byte_offset      INTEGER NOT NULL DEFAULT 0,
    line_count       INTEGER NOT NULL DEFAULT 0,
    -- The last request counted, with its usage: a message still streaming at
    -- the offset finishes in the next chunk, and its final line must replace
    -- (not add to) what the placeholder line already contributed.
    tail_request_json TEXT NOT NULL DEFAULT '',
    -- 1 when every line of the file has been through the security scan. A
    -- cost-only parse leaves it 0, and the next security pass reparses the
    -- file in full rather than trusting the cursor.
    security_scanned INTEGER NOT NULL DEFAULT 0
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
-- One row per (file, UTC day, model): the usage window and the daily trend
-- read these, so a session that ran across midnight lands on both days
-- instead of whole on the day it ended. recorded_cost_usd is the sum of any
-- costUSD the transcript itself carried (older CLIs); 0 means price from tokens.
CREATE TABLE IF NOT EXISTS agent_session_days (
    source_path       TEXT NOT NULL,
    day               TEXT NOT NULL,
    model             TEXT NOT NULL,
    input             INTEGER NOT NULL DEFAULT 0,
    output            INTEGER NOT NULL DEFAULT 0,
    cache_write_5m    INTEGER NOT NULL DEFAULT 0,
    cache_write_1h    INTEGER NOT NULL DEFAULT 0,
    cache_read        INTEGER NOT NULL DEFAULT 0,
    calls             INTEGER NOT NULL DEFAULT 0,
    web_search_calls  INTEGER NOT NULL DEFAULT 0,
    web_fetch_calls   INTEGER NOT NULL DEFAULT 0,
    -- input/output of the requests whose prompt crossed the long-context
    -- threshold (a subset of input/output), surcharged by pricing.py.
    premium_input     INTEGER NOT NULL DEFAULT 0,
    premium_output    INTEGER NOT NULL DEFAULT 0,
    recorded_cost_usd REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (source_path, day, model)
);
CREATE INDEX IF NOT EXISTS idx_agent_session_days_day ON agent_session_days(day);
-- Global dedup: a request already counted from one file is skipped in every
-- other (a copied or restored transcript). First file to claim a key keeps it.
CREATE TABLE IF NOT EXISTS agent_seen_requests (
    key         TEXT PRIMARY KEY,
    source_path TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_seen_requests_path ON agent_seen_requests(source_path);
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
    -- Where the match sat (command, heredoc, write-input, tool-result, …), the
    -- file that context pointed at, the message timestamp and tool, and a
    -- short redacted snippet with the matched span masked — what lets a row
    -- say what happened without opening the transcript.
    context     TEXT NOT NULL DEFAULT '',
    target      TEXT NOT NULL DEFAULT '',
    at          TEXT NOT NULL DEFAULT '',
    tool        TEXT NOT NULL DEFAULT '',
    snippet     TEXT NOT NULL DEFAULT '',
    UNIQUE(category, pattern, source_path, line_no)
);
-- Fixes a person applied from a finding: the button pressed, where it wrote,
-- and what came of it. The finding itself is answered through a dismissal;
-- this is the audit trail beside it.
CREATE TABLE IF NOT EXISTS agent_security_fixes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fix_id      TEXT NOT NULL DEFAULT '',
    key         TEXT NOT NULL DEFAULT '',
    pattern     TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT '',
    target      TEXT NOT NULL DEFAULT '',
    applied_at  TEXT NOT NULL DEFAULT '',
    outcome     TEXT NOT NULL DEFAULT '',
    pr_url      TEXT NOT NULL DEFAULT ''
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
-- Retained for history and downgrade; the Agent Standup mode was withdrawn
-- and nothing reads or writes this table any more.
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
);
-- Additive vs the sidecar: the Go store never reads or writes this table
-- (the advisor pipeline is Python-only; see advisor.py), and store open runs
-- this script idempotently, so existing DBs gain it without a version bump.
CREATE TABLE IF NOT EXISTS agent_advisor_reports (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start   TEXT NOT NULL DEFAULT '',
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
        self._ensure_cursor_columns()
        self._ensure_finding_columns()
        self._rebuild_days_if_missing()

    def _rebuild_days_if_missing(self) -> None:
        """Forget the cursors when the rollups predate the per-day table.

        An upgraded database has an ``agent_sessions`` row and an unchanged
        cursor for every transcript, so a refresh would skip them all and the
        usage window — which reads ``agent_session_days`` only — would report
        nothing. Both tables are caches derived from the transcripts; clearing
        the cursors makes the next refresh rebuild them, the same repair the
        session-id rekey does.
        """
        try:
            has_sessions = self._conn.execute("SELECT 1 FROM agent_sessions LIMIT 1").fetchone() is not None
            has_days = self._conn.execute("SELECT 1 FROM agent_session_days LIMIT 1").fetchone() is not None
            if has_sessions and not has_days:
                self._conn.execute("DELETE FROM agent_ingest_files")
                self._conn.execute("DELETE FROM agent_seen_requests")
                logger.info("agentwatch store: rollups predate the per-day table — cursors cleared for a rebuild")
        except sqlite3.DatabaseError as exc:
            logger.warning("agentwatch store: could not check the per-day table: %s", exc)

    def _ensure_cursor_columns(self) -> None:
        """Add the resume columns to a cursor table created before they existed."""
        try:
            have = {row[1] for row in self._conn.execute("PRAGMA table_info(agent_ingest_files)").fetchall()}
            for column, decl in (
                ("byte_offset", "INTEGER NOT NULL DEFAULT 0"),
                ("line_count", "INTEGER NOT NULL DEFAULT 0"),
                ("tail_request_json", "TEXT NOT NULL DEFAULT ''"),
                ("security_scanned", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if column not in have:
                    self._conn.execute(f"ALTER TABLE agent_ingest_files ADD COLUMN {column} {decl}")
        except sqlite3.DatabaseError as exc:
            logger.warning("agentwatch store: could not widen the cursor table: %s", exc)

    def _ensure_finding_columns(self) -> None:
        """Add the context columns to a findings table created before they existed."""
        try:
            have = {row[1] for row in self._conn.execute("PRAGMA table_info(agent_security_findings)").fetchall()}
            for column in ("context", "target", "at", "tool", "snippet"):
                if column not in have:
                    decl = f"ALTER TABLE agent_security_findings ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                    self._conn.execute(decl)
        except sqlite3.DatabaseError as exc:
            logger.warning("agentwatch store: could not widen the findings table: %s", exc)

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

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Batch writes into one explicit transaction on the autocommit connection.

        The connection runs in autocommit (``isolation_level = None``), so a
        cold ``collector.refresh()`` used to pay one fsync per statement —
        ~1,500 transactions over a large corpus. Wrapping a batch in
        BEGIN…COMMIT collapses that to one. Rolls back if the exception passes
        through the ``with`` block — which is NOT how ``refresh()`` uses it:
        it holds the batch open across loop iterations on an ``ExitStack``, and
        ``ExitStack.close()`` calls ``__exit__(None, None, None)``, so an
        unwind there COMMITS the files completed so far. That is deliberate (a
        file's rollup and its cursor land in the same batch, so committed work
        is consistent and an interrupted file simply reparses next run), but do
        not read this line as a rollback guarantee for that caller.
        Every other caller keeps autocommit semantics untouched. Not reentrant:
        SQLite has no nested BEGIN, and nothing here nests batches.
        """
        self._conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    def __del__(self) -> None:
        self.close()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Ingest cursor ─────────────────────────────────────────────────────

    def get_cursor(self, path: str) -> dict | None:
        """Return the stored cursor for a source file, or None."""
        row = self._conn.execute(
            "SELECT source, size, mtime, first_line_sha, byte_offset, line_count, tail_request_json, "
            "security_scanned FROM agent_ingest_files WHERE path = ?",
            (path,),
        ).fetchone()
        if row is None:
            return None
        return {
            "source": row[0],
            "size": row[1],
            "mtime": row[2],
            "first_line_sha": row[3],
            "byte_offset": int(row[4] or 0),
            "line_count": int(row[5] or 0),
            "tail_request": _loads(row[6] or "", {}),
            "security_scanned": bool(row[7]),
        }

    def set_cursor(
        self,
        path: str,
        *,
        source: str,
        size: int,
        mtime: float,
        first_line_sha: str,
        byte_offset: int = 0,
        line_count: int = 0,
        tail_request: dict | None = None,
        security_scanned: bool = False,
    ) -> None:
        """Upsert the ingest cursor for a source file."""
        self._conn.execute(
            """INSERT INTO agent_ingest_files
                   (path, source, size, mtime, first_line_sha, last_ingested_at,
                    byte_offset, line_count, tail_request_json, security_scanned)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   source = excluded.source, size = excluded.size, mtime = excluded.mtime,
                   first_line_sha = excluded.first_line_sha, last_ingested_at = excluded.last_ingested_at,
                   byte_offset = excluded.byte_offset, line_count = excluded.line_count,
                   tail_request_json = excluded.tail_request_json, security_scanned = excluded.security_scanned""",
            (
                path,
                source,
                size,
                mtime,
                first_line_sha,
                self._now(),
                int(byte_offset),
                int(line_count),
                json.dumps(tail_request, sort_keys=True) if tail_request else "",
                1 if security_scanned else 0,
            ),
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

    def get_session(self, source_path: str) -> dict | None:
        """One transcript's rollup row (parsed JSON columns), or None."""
        self._conn.row_factory = sqlite3.Row
        try:
            row = self._conn.execute("SELECT * FROM agent_sessions WHERE source_path = ?", (source_path,)).fetchone()
        finally:
            self._conn.row_factory = None
        if row is None:
            return None
        d = dict(row)
        d["model_usage"] = _loads(d.pop("model_usage_json", "{}"), {})
        d["tool_counts"] = _loads(d.pop("tool_counts_json", "{}"), {})
        return d

    def replace_session_days(self, source_path: str, rows: dict) -> None:
        """Write one file's per-day rollup, replacing whatever it had.

        ``rows`` is ``{day: {model: usage}}`` in the collector's usage-key
        vocabulary (input, output, cache_write_5m, cache_write_1h, cache_read,
        calls, web_search_calls, web_fetch_calls, recorded_cost_usd).
        """
        self._conn.execute("DELETE FROM agent_session_days WHERE source_path = ?", (source_path,))
        self._conn.executemany(
            """INSERT INTO agent_session_days
                   (source_path, day, model, input, output, cache_write_5m, cache_write_1h, cache_read,
                    calls, web_search_calls, web_fetch_calls, premium_input, premium_output, recorded_cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    source_path,
                    day,
                    model,
                    int(u.get("input", 0)),
                    int(u.get("output", 0)),
                    int(u.get("cache_write_5m", 0)),
                    int(u.get("cache_write_1h", 0)),
                    int(u.get("cache_read", 0)),
                    int(u.get("calls", 0)),
                    int(u.get("web_search_calls", 0)),
                    int(u.get("web_fetch_calls", 0)),
                    int(u.get("premium_input", 0)),
                    int(u.get("premium_output", 0)),
                    float(u.get("recorded_cost_usd", 0.0)),
                )
                for day, by_model in rows.items()
                for model, u in by_model.items()
            ],
        )

    def merge_session_days(self, source_path: str, rows: dict) -> None:
        """Add one chunk's per-day rollup onto what the file already has (resume)."""
        self._conn.executemany(
            """INSERT INTO agent_session_days
                   (source_path, day, model, input, output, cache_write_5m, cache_write_1h, cache_read,
                    calls, web_search_calls, web_fetch_calls, premium_input, premium_output, recorded_cost_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_path, day, model) DO UPDATE SET
                   input = input + excluded.input, output = output + excluded.output,
                   cache_write_5m = cache_write_5m + excluded.cache_write_5m,
                   cache_write_1h = cache_write_1h + excluded.cache_write_1h,
                   cache_read = cache_read + excluded.cache_read, calls = calls + excluded.calls,
                   web_search_calls = web_search_calls + excluded.web_search_calls,
                   web_fetch_calls = web_fetch_calls + excluded.web_fetch_calls,
                   premium_input = premium_input + excluded.premium_input,
                   premium_output = premium_output + excluded.premium_output,
                   recorded_cost_usd = recorded_cost_usd + excluded.recorded_cost_usd""",
            [
                (
                    source_path,
                    day,
                    model,
                    int(u.get("input", 0)),
                    int(u.get("output", 0)),
                    int(u.get("cache_write_5m", 0)),
                    int(u.get("cache_write_1h", 0)),
                    int(u.get("cache_read", 0)),
                    int(u.get("calls", 0)),
                    int(u.get("web_search_calls", 0)),
                    int(u.get("web_fetch_calls", 0)),
                    int(u.get("premium_input", 0)),
                    int(u.get("premium_output", 0)),
                    float(u.get("recorded_cost_usd", 0.0)),
                )
                for day, by_model in rows.items()
                for model, u in by_model.items()
            ],
        )

    def list_session_days(self, *, since: str = "", until: str = "") -> list[dict]:
        """Per-(file, day, model) usage rows joined with their session's metadata.

        ``since``/``until`` bound the *day* (inclusive / exclusive), so a
        window sees only the tokens spent inside it.
        """
        query = (
            "SELECT d.source_path, d.day, d.model, d.input, d.output, d.cache_write_5m, d.cache_write_1h, "
            "d.cache_read, d.calls, d.web_search_calls, d.web_fetch_calls, d.premium_input, d.premium_output, "
            "d.recorded_cost_usd, "
            "s.session_id, s.source, s.project_path "
            "FROM agent_session_days d LEFT JOIN agent_sessions s ON s.source_path = d.source_path WHERE 1=1"
        )
        params: list[str] = []
        if since:
            query += " AND d.day >= ?"
            params.append(since)
        if until:
            query += " AND d.day < ?"
            params.append(until)
        query += " ORDER BY d.day, d.source_path, d.model"
        columns = (
            "source_path",
            "day",
            "model",
            "input",
            "output",
            "cache_write_5m",
            "cache_write_1h",
            "cache_read",
            "calls",
            "web_search_calls",
            "web_fetch_calls",
            "premium_input",
            "premium_output",
            "recorded_cost_usd",
            "session_id",
            "source",
            "project_path",
        )
        out = []
        for row in self._conn.execute(query, params).fetchall():
            d = dict(zip(columns, row))
            d["session_id"] = d["session_id"] or ""
            d["source"] = d["source"] or ""
            d["project_path"] = d["project_path"] or ""
            out.append(d)
        return out

    def claim_request_keys(self, source_path: str, keys: list[str]) -> set[str]:
        """Claim request keys for a file; return the ones another file already holds.

        The file's own previous claims are released first, so a full reparse
        re-claims cleanly and a resumed chunk only adds.
        """
        duplicates: set[str] = set()
        for key in keys:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO agent_seen_requests (key, source_path) VALUES (?, ?)", (key, source_path)
            )
            if cursor.rowcount == 0:
                owner = self._conn.execute(
                    "SELECT source_path FROM agent_seen_requests WHERE key = ?", (key,)
                ).fetchone()
                if owner and owner[0] != source_path:
                    duplicates.add(key)
        return duplicates

    def release_request_keys(self, source_path: str) -> None:
        """Forget a file's request claims (before a full reparse or on prune)."""
        self._conn.execute("DELETE FROM agent_seen_requests WHERE source_path = ?", (source_path,))

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
        context: str = "",
        target: str = "",
        at: str = "",
        tool: str = "",
        snippet: str = "",
    ) -> None:
        """Record one security signal.

        Location + pattern + where the match sat. ``snippet`` is the only
        transcript-derived text: already redacted by the collector, with the
        matched span masked, and capped at 120 characters.
        """
        self._conn.execute(
            """INSERT OR IGNORE INTO agent_security_findings
                   (category, severity, pattern, source_path, line_no, session_id, detail, created_at,
                    context, target, at, tool, snippet)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                category,
                severity,
                pattern,
                source_path,
                line_no,
                session_id,
                detail,
                self._now(),
                context,
                target,
                at,
                tool,
                snippet[:120],
            ),
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

    def list_findings_for_key(self, *, category: str, pattern: str, source_path: str, context: str = "") -> list[dict]:
        """Every stored row behind one grouped finding, in line order."""
        query = "SELECT * FROM agent_security_findings WHERE category = ? AND pattern = ? AND source_path = ?"
        params: list[str] = [category, pattern, source_path]
        if context:
            query += " AND context = ?"
            params.append(context)
        query += " ORDER BY line_no"
        self._conn.row_factory = sqlite3.Row
        try:
            rows = self._conn.execute(query, params).fetchall()
        finally:
            self._conn.row_factory = None
        return [dict(row) for row in rows]

    def findings_without_context(self) -> int:
        """Rows scanned before the collector recorded where a match sat."""
        row = self._conn.execute("SELECT COUNT(*) FROM agent_security_findings WHERE context = ''").fetchone()
        return int(row[0]) if row else 0

    def reset_security_scanned(self) -> None:
        """Make the next security pass rescan every file (cost cursors stay)."""
        self._conn.execute("UPDATE agent_ingest_files SET security_scanned = 0")

    def reset_cursors(self) -> None:
        """Forget every ingest cursor so the next refresh reparses everything."""
        self._conn.execute("DELETE FROM agent_ingest_files")
        self._conn.execute("DELETE FROM agent_seen_requests")

    # ── Security fixes ────────────────────────────────────────────────────

    def record_fix(
        self, *, fix_id: str, key: str, pattern: str, kind: str, target: str, outcome: str, pr_url: str = ""
    ) -> None:
        """Append one applied fix to the audit trail."""
        self._conn.execute(
            """INSERT INTO agent_security_fixes (fix_id, key, pattern, kind, target, applied_at, outcome, pr_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (fix_id, key, pattern, kind, target, self._now(), outcome, pr_url),
        )

    def list_fixes(self, *, key: str = "", limit: int = 100) -> list[dict]:
        """Applied fixes, newest first, optionally for one finding key."""
        query = "SELECT * FROM agent_security_fixes"
        params: list = []
        if key:
            query += " WHERE key = ?"
            params.append(key)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        self._conn.row_factory = sqlite3.Row
        try:
            rows = self._conn.execute(query, params).fetchall()
        finally:
            self._conn.row_factory = None
        return [dict(row) for row in rows]

    def known_source_paths(self) -> list[str]:
        """Every source path the store currently holds state for."""
        rows = self._conn.execute(
            "SELECT path FROM agent_ingest_files "
            "UNION SELECT source_path FROM agent_sessions "
            "UNION SELECT source_path FROM agent_security_findings "
            "UNION SELECT source_path FROM agent_session_days"
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
        self._conn.execute("DELETE FROM agent_session_days WHERE source_path = ?", (source_path,))
        self._conn.execute("DELETE FROM agent_seen_requests WHERE source_path = ?", (source_path,))

    # ── Report history (shared shape for the three kinds) ─────────────────

    def record_report(self, kind: str, artifact: object, *, key_date: str = "") -> int:
        """Persist one report artifact under its kind's table; return the row id.

        ``kind`` is "usage" / "security" / "advisor"; ``key_date`` fills the
        kind-specific date column (period_start, scan_date).
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

    def replace_latest_report(self, kind: str, artifact: object) -> bool:
        """Overwrite the newest saved report's payload in place; False when history is empty.

        A dismissal, a fix or a fold does not make a new run — it changes what
        the last run means. Appending a row per keystroke would fill the
        history with one "scan" per click.
        """
        table, _date_col = _REPORT_TABLES[kind]
        row = self._conn.execute(f"SELECT id FROM {table} ORDER BY id DESC LIMIT 1").fetchone()  # noqa: S608
        if row is None:
            return False
        payload = json.dumps(asdict(artifact), ensure_ascii=False)  # type: ignore[call-overload]
        self._conn.execute(f"UPDATE {table} SET report_json = ? WHERE id = ?", (payload, int(row[0])))  # noqa: S608
        return True

    def latest_report(self, kind: str) -> dict | None:
        """The newest saved report row of one kind, or None when history is empty.

        Newest regardless of ``origin`` — an edited report is still the last
        saved state the user expects to see when a page opens instantly.
        """
        rows = self.list_reports(kind, limit=1)
        return rows[0] if rows else None


_REPORT_TABLES: dict[str, tuple[str, str]] = {
    "usage": ("agent_usage_reports", "period_start"),
    "security": ("agent_security_reports", "scan_date"),
    "advisor": ("agent_advisor_reports", "period_start"),
}


def _loads(raw: str, default: dict) -> dict:
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, dict) else default


# ---------------------------------------------------------------------------
# Report rehydration — stored JSON payload → the frozen artifact dataclass
# ---------------------------------------------------------------------------
#
# record_report stores asdict() JSON, so nested dataclasses come back as dicts
# and tuples as lists. The TUI's instant-open path (and the capped renderers,
# which go through dataclasses.replace) need the real dataclass back. Same
# convention as standup/store.py's _dict_to_standup_report: every field via
# .get() with the dataclass default, so a payload written by an older version
# still loads. Deliberately NOT registered in artifacts/registry — rehydration
# for display, not for the editable-artifact surface.


def _str_tuple(value) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(v) for v in value)


def _pair_tuple(value) -> tuple[tuple[str, str], ...]:
    """Rebuild (a, b) string pairs that JSON flattened into 2-item lists."""
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple((str(p[0]), str(p[1])) for p in value if isinstance(p, (list, tuple)) and len(p) == 2)


def _dict_to_usage_report(d: dict):
    from yeaboi.agent.state import (
        AgentUsageBreakdownRow,
        AgentUsageReport,
        DailyUsagePoint,
        ModelUsageRow,
        annotations_from,
    )

    def _breakdown(rows) -> tuple[AgentUsageBreakdownRow, ...]:
        return tuple(
            AgentUsageBreakdownRow(
                key=str(r.get("key", "")),
                sessions=int(r.get("sessions", 0)),
                input_tokens=int(r.get("input_tokens", 0)),
                output_tokens=int(r.get("output_tokens", 0)),
                cost_usd=float(r.get("cost_usd", 0.0)),
            )
            for r in rows or ()
            if isinstance(r, dict)
        )

    return AgentUsageReport(
        period_start=str(d.get("period_start", "")),
        period_end=str(d.get("period_end", "")),
        session_count=int(d.get("session_count", 0)),
        total_cost_usd=float(d.get("total_cost_usd", 0.0)),
        total_input_tokens=int(d.get("total_input_tokens", 0)),
        total_output_tokens=int(d.get("total_output_tokens", 0)),
        total_cache_write_tokens=int(d.get("total_cache_write_tokens", 0)),
        total_cache_read_tokens=int(d.get("total_cache_read_tokens", 0)),
        unknown_model_cost_share=float(d.get("unknown_model_cost_share", 0.0)),
        pricing_as_of=str(d.get("pricing_as_of", "")),
        billing_kind=str(d.get("billing_kind", "")),
        cache_cost_share=float(d.get("cache_cost_share", 0.0)),
        window_days=int(d.get("window_days", 0)),
        by_model=tuple(
            ModelUsageRow(
                model=str(r.get("model", "")),
                input_tokens=int(r.get("input_tokens", 0)),
                output_tokens=int(r.get("output_tokens", 0)),
                cache_write_tokens=int(r.get("cache_write_tokens", 0)),
                cache_read_tokens=int(r.get("cache_read_tokens", 0)),
                calls=int(r.get("calls", 0)),
                cost_usd=float(r.get("cost_usd", 0.0)),
                known_pricing=bool(r.get("known_pricing", True)),
            )
            for r in d.get("by_model") or ()
            if isinstance(r, dict)
        ),
        by_project=_breakdown(d.get("by_project")),
        by_source=_breakdown(d.get("by_source")),
        daily_trend=tuple(
            DailyUsagePoint(
                date=str(r.get("date", "")),
                cost_usd=float(r.get("cost_usd", 0.0)),
                input_tokens=int(r.get("input_tokens", 0)),
                output_tokens=int(r.get("output_tokens", 0)),
                sessions=int(r.get("sessions", 0)),
            )
            for r in d.get("daily_trend") or ()
            if isinstance(r, dict)
        ),
        insights=_str_tuple(d.get("insights")),
        recommendations=_str_tuple(d.get("recommendations")),
        warnings=_str_tuple(d.get("warnings")),
        generated_at=str(d.get("generated_at", "")),
        annotations=annotations_from(d.get("annotations")),
    )


def _dict_to_security_report(d: dict):
    from yeaboi.agent.state import (
        AgentSecurityReport,
        McpServerRecord,
        SecurityFinding,
        SecurityFix,
        SecurityIssue,
        annotations_from,
    )

    def fixes(rows) -> tuple:
        return tuple(
            SecurityFix(
                id=str(x.get("id", "")),
                kind=str(x.get("kind", "")),
                label=str(x.get("label", "")),
                target=str(x.get("target", "")),
                detail=str(x.get("detail", "")),
                scope=str(x.get("scope", "")),
            )
            for x in rows or ()
            if isinstance(x, dict)
        )

    return AgentSecurityReport(
        scan_date=str(d.get("scan_date", "")),
        posture=str(d.get("posture", "")),
        sessions_scanned=int(d.get("sessions_scanned", 0)),
        files_scanned=int(d.get("files_scanned", 0)),
        secrets_found=int(d.get("secrets_found", 0)),
        findings=tuple(
            SecurityFinding(
                severity=str(f.get("severity", "info")),
                category=str(f.get("category", "")),
                title=str(f.get("title", "")),
                location=str(f.get("location", "")),
                line_no=int(f.get("line_no", 0)),
                pattern=str(f.get("pattern", "")),
                detail=str(f.get("detail", "")),
                remediation=str(f.get("remediation", "")),
                occurrences=int(f.get("occurrences", 1)),
                key=str(f.get("key", "")),
                scopes=_str_tuple(f.get("scopes")),
                verdict=str(f.get("verdict", "")),
                verdict_reason=str(f.get("verdict_reason", "")),
                context=str(f.get("context", "")),
                target=str(f.get("target", "")),
                snippet=str(f.get("snippet", "")),
                at=str(f.get("at", "")),
                session_id=str(f.get("session_id", "")),
                project_label=str(f.get("project_label", "")),
                sessions=int(f.get("sessions", 1)),
                fixes=fixes(f.get("fixes")),
            )
            for f in d.get("findings") or ()
            if isinstance(f, dict)
        ),
        mcp_servers=tuple(
            McpServerRecord(
                name=str(m.get("name", "")),
                scope=str(m.get("scope", "")),
                transport=str(m.get("transport", "")),
                target=str(m.get("target", "")),
                flags=_str_tuple(m.get("flags")),
            )
            for m in d.get("mcp_servers") or ()
            if isinstance(m, dict)
        ),
        settings_flags=_str_tuple(d.get("settings_flags")),
        summary=str(d.get("summary", "")),
        recommendations=_str_tuple(d.get("recommendations")),
        finding_keys=_str_tuple(d.get("finding_keys")),
        new_findings=_str_tuple(d.get("new_findings")),
        resolved_findings=_str_tuple(d.get("resolved_findings")),
        dismissed_count=int(d.get("dismissed_count", 0)),
        hidden_info_count=int(d.get("hidden_info_count", 0)),
        posture_reason=str(d.get("posture_reason", "")),
        pattern_totals=_pair_tuple(d.get("pattern_totals")),
        issues=tuple(
            SecurityIssue(
                id=str(i.get("id", "")),
                category=str(i.get("category", "")),
                pattern=str(i.get("pattern", "")),
                title=str(i.get("title", "")),
                why=str(i.get("why", "")),
                verdict=str(i.get("verdict", "")),
                severity=str(i.get("severity", "")),
                signals=int(i.get("signals", 0)),
                sessions=int(i.get("sessions", 0)),
                files=int(i.get("files", 0)),
                last_seen=str(i.get("last_seen", "")),
                finding_keys=_str_tuple(i.get("finding_keys")),
                fixes=fixes(i.get("fixes")),
            )
            for i in d.get("issues") or ()
            if isinstance(i, dict)
        ),
        verdict_counts=tuple(
            (str(pair[0]), int(pair[1]))
            for pair in d.get("verdict_counts") or ()
            if isinstance(pair, (list, tuple)) and len(pair) == 2
        ),
        verdict_line=str(d.get("verdict_line", "")),
        warnings=_str_tuple(d.get("warnings")),
        generated_at=str(d.get("generated_at", "")),
        annotations=annotations_from(d.get("annotations")),
    )


def _dict_to_advisor_report(d: dict):
    from yeaboi.agent.state import (
        AgentAdvisorReport,
        VolatileFileSignal,
        WasteLineItem,
        annotations_from,
    )

    return AgentAdvisorReport(
        period_start=str(d.get("period_start", "")),
        period_end=str(d.get("period_end", "")),
        session_count=int(d.get("session_count", 0)),
        files_audited=int(d.get("files_audited", 0)),
        total_cost_usd=float(d.get("total_cost_usd", 0.0)),
        read_calls=int(d.get("read_calls", 0)),
        read_bytes=int(d.get("read_bytes", 0)),
        tool_bytes_total=int(d.get("tool_bytes_total", 0)),
        recoverable_usd=float(d.get("recoverable_usd", 0.0)),
        recoverable_share=float(d.get("recoverable_share", 0.0)),
        effective_input_rate_per_mtok=float(d.get("effective_input_rate_per_mtok", 0.0)),
        unknown_rate_share=float(d.get("unknown_rate_share", 0.0)),
        pricing_as_of=str(d.get("pricing_as_of", "")),
        line_items=tuple(
            WasteLineItem(
                mechanism=str(i.get("mechanism", "")),
                label=str(i.get("label", "")),
                calls=int(i.get("calls", 0)),
                content_bytes=int(i.get("content_bytes", 0)),
                est_tokens=int(i.get("est_tokens", 0)),
                est_usd=float(i.get("est_usd", 0.0)),
                share_of_read_bytes=float(i.get("share_of_read_bytes", 0.0)),
                recoverable=bool(i.get("recoverable", True)),
                note=str(i.get("note", "")),
            )
            for i in d.get("line_items") or ()
            if isinstance(i, dict)
        ),
        residency_median=int(d.get("residency_median", 0)),
        residency_p90=int(d.get("residency_p90", 0)),
        gaps_over_5m=int(d.get("gaps_over_5m", 0)),
        gaps_over_1h=int(d.get("gaps_over_1h", 0)),
        sessions_with_gap=int(d.get("sessions_with_gap", 0)),
        volatile_signals=tuple(
            VolatileFileSignal(
                location=str(s.get("location", "")),
                counts=_pair_tuple(s.get("counts")),
                total=int(s.get("total", 0)),
            )
            for s in d.get("volatile_signals") or ()
            if isinstance(s, dict)
        ),
        alignment_score=int(d.get("alignment_score", 100)),
        insights=_str_tuple(d.get("insights")),
        recommendations=_str_tuple(d.get("recommendations")),
        warnings=_str_tuple(d.get("warnings")),
        generated_at=str(d.get("generated_at", "")),
        annotations=annotations_from(d.get("annotations")),
    )


_REHYDRATORS = {
    "usage": _dict_to_usage_report,
    "security": _dict_to_security_report,
    "advisor": _dict_to_advisor_report,
}


def report_from_payload(kind: str, payload: object):
    """Rebuild a stored report payload into its artifact dataclass, or None.

    None (rather than a half-built artifact) for an unknown kind, a corrupt
    payload, or the empty dict ``_loads`` yields for bad JSON — the caller's
    cold-start path is the right fallback for all three.
    """
    rehydrate = _REHYDRATORS.get(kind)
    if rehydrate is None or not isinstance(payload, dict) or not payload:
        return None
    try:
        return rehydrate(payload)
    except Exception as exc:  # noqa: BLE001 — a bad row must not break the page
        logger.warning("agentwatch: could not rehydrate stored %s report: %s", kind, exc)
        return None
