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
- `1001` schema guard: the sessions.db schema version is newer than this
  binary understands (client must fall back, never write)
- `1000` internal failure (message is safe to log; it never contains
  transcript content)

## Methods

### core.hello

Params: `{}` →
`{"contract_version": 1, "name": "yeaboi-core", "version": "<binary semver>", "methods": ["agentwatch.refresh", "agentwatch.usage", "agentwatch.standup", "agentwatch.security", "standup.aggregate", "analysis.classify_markers", "analysis.score_code", "analysis.score_docs", "retro.build_export", "poker.build_export"]}`

Adding a method is additive and does NOT bump `contract_version`: an older
binary answers `-32601` for a method it lacks, which the client surfaces as a
`CoreError` and the engine downgrades to the Python path — the designed
degradation.

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

### standup.aggregate

The deterministic middle of the DAILY STANDUP pipeline (the human mode — not
`agentwatch.standup`, which is the Agents digest): identity closure → roster
filter → automation filter → category coverage → grouping → day-over-day
insights → practice detection → confidence → per-member skeletons. One pure
function of its params: Python collects activity and reads all session state
(collector, stores, tracker SDKs), sends everything as data, and overlays LLM
prose on the returned scaffold. See `standup.aggregate.json` and
`src/yeaboi/standup/aggregate.py` (`aggregate_standup` is the reference
implementation; `build_aggregate_inputs` builds the params).

- **DB-free.** Every standup table is report-history state — Python-only
  under rule 5. The sidecar never opens a database for this method; all
  DB-derived inputs (config subset, previous report projection, feedback
  excuses, history rows, self-report names) travel as params.
- **Two-pass adjudication.** Practice adjudication is an LLM seam inside
  detection, so the method is idempotent and two-pass: pass 1 returns
  `adjudication_cases`; the engine runs the (Python) adjudicator; when it
  drops any, pass 2 repeats the IDENTICAL params plus `dropped_case_ids` and
  returns `adjudication_cases: []`. Case ids are deterministic functions of
  the params, so pass 2 rebuilds the same cases and applies the drops;
  unknown ids are discarded by the same intersection Python applies.
- **No progress notifications** — the call is milliseconds of pure compute;
  Python wraps it in its own phase reporting.
- **Object key order is contractual.** Member-keyed result objects
  (`grouped`, `blocker_signals`, `yesterday`, `practices`) keep MEMBERS
  order; each projected item, skeleton, evidence row and the `progress`
  object keep the reference implementation's dict-literal key order — the
  Python client json.loads-es them into dicts whose order feeds the LLM
  prompt's `json.dumps` bytes.

### analysis.classify_markers

AI-marker classification over the TEAM ANALYSIS activity items: the adoption
signal (`aggregate_ai_markers`) plus every AI-marked evidence sample. A
separate method from `analysis.score_code` for a concurrency reason, not a
data one: the caller starts its footprint-insights LLM thread with
signal+samples *before* the change-metadata fetch, so classification must
return before the inputs of `score_code` even exist. Pure; no DB, no
progress. See `analysis.classify_markers.json` and
`src/yeaboi/analysis/aggregate.py` (`classify_markers` is the reference
implementation; `build_classify_inputs` builds the params).

### analysis.score_code

The deterministic tail of the TEAM ANALYSIS code pipeline: code-change
health (`analyse_changed_files` → `prioritize_actions` →
`changed_file_summary` → `coverage_notes`, gated by `health_enabled` with an
empty scaffold when off), the per-member activity tally, and
practice-hygiene scoring (`member_practices`). One pure function of its
params: Python fetches all tracker state (including the change-metadata
fan-out and its cache hits), sends everything as data, and overlays
provenance and LLM prose on the returned scaffold. No DB, no progress;
result key order is contractual. See `analysis.score_code.json` and
`src/yeaboi/analysis/aggregate.py` (`score_code` is the reference
implementation; `build_score_inputs` builds the params).

### analysis.score_docs

The deterministic tail of the TEAM ANALYSIS documentation pipeline: per-page
clarity/usefulness/disclosure scoring over every cache-miss body, aggregation
into the doc-quality signal, findings, the action plan, and the coaching
insights. One method where the code pipeline needed two, because the docs
path has no LLM anywhere — there is no concurrency to preserve. Page bodies
cross the wire as params and never appear in the result; the caller writes
the score cache by zipping the returned `assets` against its scoreable pages
(which is why an asset-count mismatch is treated as a malformed result), and
gates the always-computed `insights` on real coverage. Pure; no DB, no
progress. See `analysis.score_docs.json` and
`src/yeaboi/analysis/aggregate.py` (`score_docs` is the reference
implementation; `build_score_docs_inputs` builds the params).

### retro.build_export

One retro document — the Markdown artifact plus the HTML export args — as one
pure function. Python freezes the report, the history rows, the editable flag
and a single `now()` capture into the params; the sidecar rebuilds the report
through the store-deserializer semantics and renders both artifacts from one
call, so the two files can never disagree about which side built them. The
HTML shell (`export_page`) and every filesystem write stay Python-side.
Result key order is contractual — `args` is json.dumps-ed into the page boot
payload. No progress notifications: milliseconds of pure compute. See
`retro.build_export.json` and `src/yeaboi/retro/export.py`
(`build_retro_export` is the reference implementation;
`build_retro_export_inputs` builds the params).

### poker.build_export

The sibling of `retro.build_export` for one poker document. No `editable`
param — poker has no editable share. Tracker URLs pass the same `safe_url`
allowlist as the Markdown twin (one allowlist, both artifacts); a skipped
ticket's `final` is forced null even over a stale `final_points`. See
`poker.build_export.json` and `src/yeaboi/poker/export.py`
(`build_poker_export` is the reference implementation;
`build_poker_export_inputs` builds the params).

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
   `store.py`; refuse (error 1001) when the version in the `schema_info`
   table — where `sessions.py` records it; Python never sets
   `PRAGMA user_version`, which is read only as a fallback for a database
   no Python build has opened — is newer than the binary's
   `currentSchemaVersion` (`go/internal/agentwatch/store.go`), which a
   unit test (`test_gocore_packaging.py::TestSchemaGuardLockstep`) keeps
   equal to `sessions.py CURRENT_SCHEMA_VERSION`. Mirror the
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

Rules 10–12 were added with `standup.aggregate` (they mirror
`src/yeaboi/standup/{engine,aggregate,references,relatedness,habits,
automation,insights,confidence,categories}.py`):

10. **Unicode regex semantics.** Python's `\b`/`\w` are unicode; RE2's are
    ASCII. Ported patterns keep their `\b` in the RE2 source and post-filter
    matches with a unicode word-boundary check; lookbehinds (which RE2 lacks)
    are emulated by checking the preceding rune against the exact Python
    class (ASCII `[A-Za-z0-9]` for `AB#n`, unicode `\w` plus `#` for bare
    `#n`). `str.lower()` goes through a helper that preserves the U+0130 (İ)
    full lowercase mapping. Accepted, documented deviations: `\d`/`\s`/`\S`
    are treated as ASCII (no real tracker emits non-ASCII digits), and
    `[\w.-]`-style classes approximate `\w` as `[\p{L}\p{N}_]`.
11. **Change-handle hashing.** `habits.change_handle`'s subject fallback is
    `sha1(normalize_commit_subject(subject).encode("utf-8", "replace"))[:16]`
    and must match byte-for-byte — feedback excuses are keyed on it, and a
    drifted handle silently re-fires signals the team already excused (the
    worst failure mode this contract guards against).
12. **String formatting.** Python `f"{x:.0f}"` is a correctly-rounded
    half-even fixed conversion (Go's strconv 'f' formatting matches);
    `int(round(x))` is banker's rounding; `str(list-of-strings)` renders with
    Python `repr` elements (`['a', 'b']`) where a yesterday entry is
    flattened into text.

Rule 13 was added with `retro.build_export` / `poker.build_export` (it
mirrors `src/yeaboi/{retro,poker}/export.py`, `artifacts/render.py`,
`html_theme.py`'s pure helpers, `markdown_convert.md_table_cell` and
`artifacts/paths.escape_value`):

13. **Document-text semantics.** Duel transcripts split on
    `str.splitlines()`'s universal-terminator set (`\r\n` once, NEL/LS/PS
    included), not on `\n`; `md_table_cell`'s whitespace collapse is no-arg
    `str.split()` — unicode whitespace, empties dropped. Numbers that pass
    through `float()` (`_float_or_none`, trend values) are WIDENED and
    render through `repr(float)` — a wire `3` leaves as `3.0`, never an echo
    of the wire literal — while numbers that never pass through `float()`
    (reaction counts, via `int()`) stay integers. `escape_value` reproduces
    `urllib.parse.quote(value, safe="")` exactly (UTF-8 bytes, uppercase
    hex, the unreserved set) plus the explicit `.` → `%2E` pass. Privacy:
    the exports package imports no logging facility — card text, ticket
    summaries, voter names and transcripts cross the wire as params and
    never appear in a log line or an error message; `safe_url`'s
    Python-side warning on a dropped URL is an accepted Python-only
    deviation (the Go side drops silently).
