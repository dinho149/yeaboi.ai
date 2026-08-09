# yeaboi-core RPC contract v1

The Go sidecar (`yeaboi-core`) speaks newline-delimited JSON (one object per
line) over stdio — a JSON-RPC 2.0 subset. The Python client
(`src/yeaboi/gocore/client.py`) spawns it on first use and keeps it running.

## Framing

- Request:  `{"jsonrpc": "2.0", "id": <int>, "method": "<name>", "params": {…}}`
- Response: `{"jsonrpc": "2.0", "id": <int>, "result": {…}}` or
  `{"jsonrpc": "2.0", "id": <int>, "error": {"code": <int>, "message": "<str>"}}`
- Notification (server → client, no id):
  `{"jsonrpc": "2.0", "method": "progress", "params": {"request_id": <int>, "event": {…}}}`

`event` is exactly one `analysis_component` lifecycle dict (see
`progress.json` and `src/yeaboi/analysis/progress.py`) — the Python client
forwards it verbatim to the engine's `on_progress` callback, so the TUI's
phase checklist works unchanged.

Every successful result carries `"contract_version": 1`. The client sends
`core.hello` after spawn and falls back to the Python implementation when the
version does not match or the handshake fails.

## Error codes

- `-32601` method not found
- `-32602` invalid params
- `1001` schema guard: the SQLite `PRAGMA user_version` is newer than this
  binary understands (client must fall back, never write)
- `1000` internal failure (message is safe to log; it never contains
  transcript content)

## Methods

### core.hello

Params: `{}` →
`{"contract_version": 1, "name": "yeaboi-core", "version": "<binary semver>", "methods": ["agentwatch.refresh", "agentwatch.usage", "agentwatch.standup", "agentwatch.security"]}`

### agentwatch.refresh

Ingest new/changed Claude Code transcripts into the store (the collector
port). See `agentwatch.refresh.json`.

### agentwatch.usage

`agentwatch.refresh` plus the deterministic usage aggregation: prices every
(model, session) pair and returns an `AgentUsageReport`-shaped artifact with
empty `insights`/`recommendations`/`generated_at` — prose and stamping stay
Python-side. See `agentwatch.usage.json`.

### agentwatch.standup

`agentwatch.refresh` plus the LOCAL half of the standup digest: session
summaries, window totals, the no-local-sessions coverage note. The tracker
leg, all prose, delivery and history stay Python-side; `window_start` /
`digest_date` are computed by Python and travel as params. See
`agentwatch.standup.json`.

### agentwatch.security

`agentwatch.refresh` (with `reset_cursors` for the engine's `deep=True`) plus
the whole deterministic security report: stored-finding mapping, the settings
audit and MCP inventory over the config roots passed as params, ranking and
posture. Only the LLM `summary`/`recommendations` and `generated_at` stay
Python-side. See `agentwatch.security.json`.

## Semantics the Go side must preserve

These mirror `src/yeaboi/agentwatch/collector.py`, `store.py`,
`engine.py::run_agent_usage` and `src/yeaboi/pricing.py`; the parity suite
(`tests/parity/`) runs both implementations over the same fixtures and diffs
canonical JSON.

1. **Privacy.** No transcript content is ever stored, returned, logged, or
   put in a warning/error message. Findings are (pattern label, file, line).
   Ingest-failure warnings carry the failure *class* only.
2. **Usage dedup.** Usage counts once per `requestId`, tool_use blocks once
   per block id; the dedup is whole-file, and a changed file's rollup
   *replaces* the previous one (keyed on `source_path`).
3. **Cursor.** Skip a file when (size, mtime) match AND the stored first-line
   SHA-256 matches (empty stored hash counts as a match). Cursors record the
   pre-parse stat. Prune DB state for files gone from disk — but never when a
   root failed to scan.
4. **Schema guard.** Execute the same `CREATE TABLE IF NOT EXISTS` DDL as
   `store.py`; refuse (error 1001) when `PRAGMA user_version` >
   27 (`sessions.py CURRENT_SCHEMA_VERSION` at contract v1). Mirror the
   `agent_sessions` primary-key repair check.
5. **Single writer.** The active implementation is the sole writer of the
   agentwatch tables; the report-history tables (`agent_*_reports`,
   `agent_standup_digests`) are Python-only and never touched by Go.
6. **Rounding.** Where Python `round(x, 4)` stamps a float into the artifact,
   Go must use banker's rounding (round-half-to-even), matching Python 3.
7. **Windowing.** A session belongs to the day of its `ended_at` (first 10
   chars); the window has no upper bound; `since` filters
   `ended_at >= period_start` exactly as `store.list_sessions(since=…)`.
8. **Config-document order.** The security audit's findings, MCP records and
   duplicate-name notes iterate JSON objects in *document* order (Python dicts
   preserve it), so the Go side must decode configs order-preserving — a plain
   Go map loses the ordering the ranking's stable sort depends on. Blobs that
   are re-serialized before pattern-matching (`hooks`, MCP `env`) must render
   exactly like Python `json.dumps` defaults (ASCII-escaped, `", "`/`": "`
   separators, document order).
9. **Config-value quoting.** Where a settings finding's `detail` embeds a
   config value via Python `!r`, Go must reproduce `repr()` for the quoted
   value (quote choice, escapes). This applies to CONFIG values only — the
   privacy rule (1) still bars transcript content everywhere.
