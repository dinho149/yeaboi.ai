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

    def latest_report(self, kind: str) -> dict | None:
        """The newest saved report row of one kind, or None when history is empty.

        Newest regardless of ``origin`` — an edited report is still the last
        saved state the user expects to see when a page opens instantly.
        """
        rows = self.list_reports(kind, limit=1)
        return rows[0] if rows else None


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


def _dict_to_standup_digest(d: dict):
    from yeaboi.agent.state import (
        AgentRepoActivityRow,
        AgentSessionSummary,
        AgentStandupDigest,
        annotations_from,
    )

    return AgentStandupDigest(
        digest_date=str(d.get("digest_date", "")),
        window_start=str(d.get("window_start", "")),
        window_end=str(d.get("window_end", "")),
        sessions_worked=int(d.get("sessions_worked", 0)),
        total_cost_usd=float(d.get("total_cost_usd", 0.0)),
        agents_seen=_str_tuple(d.get("agents_seen")),
        session_summaries=tuple(
            AgentSessionSummary(
                session_id=str(s.get("session_id", "")),
                source=str(s.get("source", "")),
                project=str(s.get("project", "")),
                branch=str(s.get("branch", "")),
                models=_str_tuple(s.get("models")),
                turns=int(s.get("turns", 0)),
                cost_usd=float(s.get("cost_usd", 0.0)),
                top_tools=_pair_tuple(s.get("top_tools")),
                started_at=str(s.get("started_at", "")),
                ended_at=str(s.get("ended_at", "")),
            )
            for s in d.get("session_summaries") or ()
            if isinstance(s, dict)
        ),
        repo_activity=tuple(
            AgentRepoActivityRow(
                source=str(r.get("source", "")),
                repo=str(r.get("repo", "")),
                kind=str(r.get("kind", "")),
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                author=str(r.get("author", "")),
                status=str(r.get("status", "")),
                agent_marker=str(r.get("agent_marker", "")),
            )
            for r in d.get("repo_activity") or ()
            if isinstance(r, dict)
        ),
        highlights=_str_tuple(d.get("highlights")),
        in_flight=_str_tuple(d.get("in_flight")),
        attention_items=_str_tuple(d.get("attention_items")),
        narrative=str(d.get("narrative", "")),
        coverage_notes=_str_tuple(d.get("coverage_notes")),
        warnings=_str_tuple(d.get("warnings")),
        generated_at=str(d.get("generated_at", "")),
        annotations=annotations_from(d.get("annotations")),
    )


def _dict_to_security_report(d: dict):
    from yeaboi.agent.state import (
        AgentSecurityReport,
        McpServerRecord,
        SecurityFinding,
        annotations_from,
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
        warnings=_str_tuple(d.get("warnings")),
        generated_at=str(d.get("generated_at", "")),
        annotations=annotations_from(d.get("annotations")),
    )


_REHYDRATORS = {
    "usage": _dict_to_usage_report,
    "standup": _dict_to_standup_digest,
    "security": _dict_to_security_report,
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
