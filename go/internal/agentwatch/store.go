package agentwatch

// SQLite store for the agentwatch tables — a port of
// src/yeaboi/agentwatch/store.py restricted to what the collector and the
// usage aggregation need. The report-history tables (agent_usage_reports,
// agent_standup_digests, agent_security_reports) are created by the shared
// DDL but NEVER written from Go — they are Python-only (contract rule 5).

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	_ "modernc.org/sqlite" // pure-Go SQLite driver, registered as "sqlite"
)

// currentSchemaVersion mirrors sessions.py CURRENT_SCHEMA_VERSION, pinned by
// tests/unit/test_gocore_packaging.py::TestSchemaGuardLockstep. A database
// whose schema_info version is newer than this must be refused (error 1001)
// — the Python side owns migrations, Go must never write ahead.
const currentSchemaVersion = 27

// ErrSchemaTooNew is the schema-guard sentinel; the RPC layer maps it to 1001.
var ErrSchemaTooNew = errors.New("sessions.db schema is newer than this yeaboi-core understands")

// agentwatchSchema is store._AGENTWATCH_SCHEMA verbatim.
const agentwatchSchema = `CREATE TABLE IF NOT EXISTS agent_ingest_files (
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
);`

// Store owns one SQLite connection to sessions.db. All writes flow through
// it single-threaded (contract: single writer, one connection).
type Store struct {
	db   *sql.DB
	conn *sql.Conn
	ctx  context.Context
}

// OpenStore opens sessions.db, applies the schema guard, mirrors the
// agent_sessions primary-key repair check, and runs the idempotent DDL —
// exactly like AgentWatchStore.__init__ plus the contract's version guard.
func OpenStore(dbPath string) (*Store, error) {
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, fmt.Errorf("open %s: %w", dbPath, err)
	}
	db.SetMaxOpenConns(1)
	ctx := context.Background()
	conn, err := db.Conn(ctx)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("connect %s: %w", dbPath, err)
	}
	s := &Store{db: db, conn: conn, ctx: ctx}
	if _, err := conn.ExecContext(ctx, "PRAGMA busy_timeout = 5000"); err != nil {
		s.Close()
		return nil, fmt.Errorf("busy_timeout: %w", err)
	}
	version, err := schemaVersion(ctx, conn)
	if err != nil {
		s.Close()
		return nil, err
	}
	if version > currentSchemaVersion {
		s.Close()
		return nil, fmt.Errorf("%w (schema_version %d > %d)", ErrSchemaTooNew, version, currentSchemaVersion)
	}
	if err := s.rebuildSessionsIfKeyedOnSessionID(); err != nil {
		s.Close()
		return nil, err
	}
	if err := s.execScript(agentwatchSchema); err != nil {
		s.Close()
		return nil, fmt.Errorf("apply schema: %w", err)
	}
	return s, nil
}

// schemaVersion reads the version the Python side actually records. sessions.py
// stores it in the schema_info table and never touches PRAGMA user_version, so
// the table is authoritative; MAX() tolerates the duplicate rows sessions.py
// dedupes on open. The pragma is read only when the table does not exist yet
// (a database no Python build has opened), where both sides agree on 0.
func schemaVersion(ctx context.Context, conn *sql.Conn) (int, error) {
	var version int
	err := conn.QueryRowContext(ctx, "SELECT COALESCE(MAX(schema_version), 0) FROM schema_info").Scan(&version)
	if err == nil {
		return version, nil
	}
	if !strings.Contains(err.Error(), "no such table") {
		return 0, fmt.Errorf("schema_info: %w", err)
	}
	if err := conn.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil {
		return 0, fmt.Errorf("user_version: %w", err)
	}
	return version, nil
}

// Close releases the connection. Safe to call twice.
func (s *Store) Close() {
	if s.conn != nil {
		_ = s.conn.Close()
		s.conn = nil
	}
	if s.db != nil {
		_ = s.db.Close()
		s.db = nil
	}
}

// execScript runs a multi-statement DDL script. The schema contains no
// semicolons outside statement boundaries, so a plain split is exact.
func (s *Store) execScript(script string) error {
	for _, stmt := range strings.Split(script, ";") {
		if strings.TrimSpace(stmt) == "" {
			continue
		}
		if _, err := s.conn.ExecContext(s.ctx, stmt); err != nil {
			return err
		}
	}
	return nil
}

// rebuildSessionsIfKeyedOnSessionID drops an agent_sessions table left over
// from the first-cut session_id primary key — the port of
// AgentWatchStore._rebuild_sessions_if_keyed_on_session_id. The table is a
// pure cache derived from the transcripts, so the repair drops it and clears
// the ingest cursors; the next refresh rebuilds both.
func (s *Store) rebuildSessionsIfKeyedOnSessionID() error {
	rows, err := s.conn.QueryContext(s.ctx, "PRAGMA table_info(agent_sessions)")
	if err != nil {
		return nil // mirror: sqlite3.Error → return (table missing is the normal path)
	}
	pkNames := map[string]bool{}
	count := 0
	for rows.Next() {
		var cid, notnull, pk int
		var name, ctype string
		var dflt any
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			rows.Close()
			return err
		}
		count++
		if pk != 0 {
			pkNames[name] = true
		}
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return err
	}
	if count == 0 {
		return nil
	}
	if len(pkNames) == 1 && pkNames["source_path"] {
		return nil
	}
	script := "DROP TABLE IF EXISTS agent_sessions"
	if hasIngest, err := s.hasTable("agent_ingest_files"); err != nil {
		return err
	} else if hasIngest {
		if _, err := s.conn.ExecContext(s.ctx, script); err != nil {
			return err
		}
		_, err = s.conn.ExecContext(s.ctx, "DELETE FROM agent_ingest_files")
		return err
	}
	_, err = s.conn.ExecContext(s.ctx, script)
	return err
}

func (s *Store) hasTable(name string) (bool, error) {
	row := s.conn.QueryRowContext(s.ctx, "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", name)
	var one int
	err := row.Scan(&one)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return true, nil
}

// Begin/Commit batch writes into one explicit transaction (the connection is
// otherwise autocommit, exactly like the Python store).
func (s *Store) Begin() error {
	_, err := s.conn.ExecContext(s.ctx, "BEGIN")
	return err
}

func (s *Store) Commit() error {
	_, err := s.conn.ExecContext(s.ctx, "COMMIT")
	return err
}

// ── Ingest cursor ─────────────────────────────────────────────────────────

// Cursor is one stored ingest cursor.
type Cursor struct {
	Source       string
	Size         int64
	Mtime        float64
	FirstLineSha string
}

// GetCursor returns the stored cursor for a source file, or nil.
func (s *Store) GetCursor(path string) (*Cursor, error) {
	row := s.conn.QueryRowContext(s.ctx,
		"SELECT source, size, mtime, first_line_sha FROM agent_ingest_files WHERE path = ?", path)
	var c Cursor
	err := row.Scan(&c.Source, &c.Size, &c.Mtime, &c.FirstLineSha)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &c, nil
}

// SetCursor upserts the ingest cursor for a source file.
func (s *Store) SetCursor(path, source string, size int64, mtime float64, firstLineSha string) error {
	_, err := s.conn.ExecContext(s.ctx,
		`INSERT INTO agent_ingest_files (path, source, size, mtime, first_line_sha, last_ingested_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   source = excluded.source, size = excluded.size, mtime = excluded.mtime,
                   first_line_sha = excluded.first_line_sha, last_ingested_at = excluded.last_ingested_at`,
		path, source, size, mtime, firstLineSha, nowISO())
	return err
}

// ResetCursors forgets every ingest cursor so the next refresh reparses all.
func (s *Store) ResetCursors() error {
	_, err := s.conn.ExecContext(s.ctx, "DELETE FROM agent_ingest_files")
	return err
}

// ── Session rollups ───────────────────────────────────────────────────────

// UpsertSession inserts or replaces one transcript file's rollup row. The
// conflict target is source_path — one row per file, never per session_id.
// The JSON columns are encoded exactly as Python's
// json.dumps(value, sort_keys=True) would write them.
func (s *Store) UpsertSession(
	sessionID, source, sourcePath, projectPath, gitBranch, cliVersion, startedAt, endedAt string,
	turns int,
	modelUsage map[string]map[string]int64,
	toolCounts map[string]int64,
) error {
	_, err := s.conn.ExecContext(s.ctx,
		`INSERT INTO agent_sessions
                   (session_id, source, source_path, project_path, git_branch, cli_version,
                    started_at, ended_at, turns, model_usage_json, tool_counts_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_path) DO UPDATE SET
                   session_id = excluded.session_id, source = excluded.source,
                   project_path = excluded.project_path, git_branch = excluded.git_branch,
                   cli_version = excluded.cli_version, started_at = excluded.started_at,
                   ended_at = excluded.ended_at, turns = excluded.turns,
                   model_usage_json = excluded.model_usage_json,
                   tool_counts_json = excluded.tool_counts_json, updated_at = excluded.updated_at`,
		sessionID, source, sourcePath, projectPath, gitBranch, cliVersion,
		startedAt, endedAt, turns, pyJSONDumpsUsage(modelUsage), pyJSONDumpsCounts(toolCounts), nowISO())
	return err
}

// SessionRow is one session rollup with its JSON columns parsed.
type SessionRow struct {
	SourcePath  string
	SessionID   string
	Source      string
	ProjectPath string
	GitBranch   string
	CliVersion  string
	StartedAt   string
	EndedAt     string
	Turns       int64
	ModelUsage  map[string]map[string]int64
	ToolCounts  map[string]int64
}

// ListSessions returns session rollups, newest first. since filters
// ended_at >= since (ISO strings compare lexicographically), so an empty
// since returns everything — the exact list_sessions(since=…) semantics.
func (s *Store) ListSessions(since string) ([]SessionRow, error) {
	query := "SELECT source_path, session_id, source, project_path, git_branch, cli_version, " +
		"started_at, ended_at, turns, model_usage_json, tool_counts_json FROM agent_sessions WHERE 1=1"
	args := []any{}
	if since != "" {
		query += " AND ended_at >= ?"
		args = append(args, since)
	}
	query += " ORDER BY ended_at DESC"
	rows, err := s.conn.QueryContext(s.ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []SessionRow
	for rows.Next() {
		var r SessionRow
		var usageJSON, countsJSON string
		if err := rows.Scan(&r.SourcePath, &r.SessionID, &r.Source, &r.ProjectPath, &r.GitBranch,
			&r.CliVersion, &r.StartedAt, &r.EndedAt, &r.Turns, &usageJSON, &countsJSON); err != nil {
			return nil, err
		}
		r.ModelUsage = loadsUsage(usageJSON)
		r.ToolCounts = loadsCounts(countsJSON)
		out = append(out, r)
	}
	return out, rows.Err()
}

// loadsUsage mirrors store._loads for the model_usage column: bad JSON or a
// non-object yields the empty default. Numeric values pass through Python's
// int() view (floats truncate toward zero).
func loadsUsage(raw string) map[string]map[string]int64 {
	var parsed map[string]map[string]json.Number
	if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
		return map[string]map[string]int64{}
	}
	out := make(map[string]map[string]int64, len(parsed))
	for model, bucket := range parsed {
		mb := make(map[string]int64, len(bucket))
		for k, n := range bucket {
			mb[k] = jsonNumberToInt(n)
		}
		out[model] = mb
	}
	return out
}

func loadsCounts(raw string) map[string]int64 {
	var parsed map[string]json.Number
	if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
		return map[string]int64{}
	}
	out := make(map[string]int64, len(parsed))
	for k, n := range parsed {
		out[k] = jsonNumberToInt(n)
	}
	return out
}

func jsonNumberToInt(n json.Number) int64 {
	if i, err := strconv.ParseInt(string(n), 10, 64); err == nil {
		return i
	}
	f, err := strconv.ParseFloat(string(n), 64)
	if err != nil {
		return 0
	}
	return int64(f)
}

// ── Security findings ─────────────────────────────────────────────────────

// DeleteFindingsForPath drops a file's findings before a reparse.
func (s *Store) DeleteFindingsForPath(sourcePath string) error {
	_, err := s.conn.ExecContext(s.ctx, "DELETE FROM agent_security_findings WHERE source_path = ?", sourcePath)
	return err
}

// AddFinding records one security signal. Location + pattern only — never
// content (privacy rule 1).
func (s *Store) AddFinding(category, severity, pattern, sourcePath string, lineNo int, sessionID string) error {
	_, err := s.conn.ExecContext(s.ctx,
		`INSERT OR IGNORE INTO agent_security_findings
                   (category, severity, pattern, source_path, line_no, session_id, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		category, severity, pattern, sourcePath, lineNo, sessionID, "", nowISO())
	return err
}

// Finding is one stored security finding (used by tests).
type Finding struct {
	Category   string
	Severity   string
	Pattern    string
	SourcePath string
	LineNo     int64
	SessionID  string
}

// ListFindings returns stored findings ordered by (source_path, line_no).
func (s *Store) ListFindings() ([]Finding, error) {
	rows, err := s.conn.QueryContext(s.ctx,
		"SELECT category, severity, pattern, source_path, line_no, session_id "+
			"FROM agent_security_findings ORDER BY source_path, line_no")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Finding
	for rows.Next() {
		var f Finding
		if err := rows.Scan(&f.Category, &f.Severity, &f.Pattern, &f.SourcePath, &f.LineNo, &f.SessionID); err != nil {
			return nil, err
		}
		out = append(out, f)
	}
	return out, rows.Err()
}

// KnownSourcePaths returns every source path the store holds state for.
func (s *Store) KnownSourcePaths() ([]string, error) {
	rows, err := s.conn.QueryContext(s.ctx,
		"SELECT path FROM agent_ingest_files "+
			"UNION SELECT source_path FROM agent_sessions "+
			"UNION SELECT source_path FROM agent_security_findings")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []string
	for rows.Next() {
		var p sql.NullString
		if err := rows.Scan(&p); err != nil {
			return nil, err
		}
		if p.Valid && p.String != "" {
			out = append(out, p.String)
		}
	}
	return out, rows.Err()
}

// ForgetSourcePath drops every trace of one transcript: cursor, rollup and
// findings — the delete-the-file remediation path.
func (s *Store) ForgetSourcePath(sourcePath string) error {
	if _, err := s.conn.ExecContext(s.ctx, "DELETE FROM agent_ingest_files WHERE path = ?", sourcePath); err != nil {
		return err
	}
	if _, err := s.conn.ExecContext(s.ctx, "DELETE FROM agent_sessions WHERE source_path = ?", sourcePath); err != nil {
		return err
	}
	_, err := s.conn.ExecContext(s.ctx, "DELETE FROM agent_security_findings WHERE source_path = ?", sourcePath)
	return err
}
