# Desktop backend HTTP contract (v1)

The wire between the Electron shell and `yeaboi app`. Pinned by
`tests/unit/test_app_wire.py`; the desktop main process (`desktop/src/main/`)
is the only intended client. Changing a key or a route shape here is a
contract change — update both sides and this file in the same PR.

## Startup handshake

`yeaboi app [--port N]` binds `127.0.0.1` and prints **exactly one** line to
stdout, then nothing else ever:

```
YEABOI_APP_READY {"pid":12345,"schema":30,"token":"…","url":"http://127.0.0.1:52341","version":"3.25.0"}
```

- JSON keys: `url`, `token`, `pid`, `schema` (sessions.py `CURRENT_SCHEMA_VERSION`),
  `version` (the yeaboi package version). Compact separators, sorted keys.
- The same payload is written to `~/.yeaboi/run/app-handshake.json` (0600) so a
  restarted shell can re-attach; liveness = `GET /api/health` answering with the
  recorded `pid`.
- A second `yeaboi app` against the same tree detects the live instance,
  re-prints **its** handshake, and exits 0 (idempotent respawn).

## Auth

Every route except `/api/health` requires `Authorization: Bearer <token>`.
The token never appears in URLs. Missing/wrong token → `401 {"error":"unauthorized"}`.

## Routes (M1)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | unauthenticated; `{ok, pid, version, schema}` |
| GET | `/api/meta/version` | `{version, schema_version, python, platform}` |
| GET | `/api/meta/capabilities` | the TUI card inventory verbatim: `{categories, modes, agents, intake}` |
| GET | `/api/meta/tips` | `{tips: [{key, text, mode_key, is_new, is_beta}]}` |
| GET | `/api/meta/changelog` | `{entries: [{version, date, summary, highlights[{text, areas}]}]}` |
| GET | `/api/tools` | `{available, tools: [name…]}` — the MCP inventory the dispatcher serves |
| POST | `/api/tool/{name}` | body `{"arguments": {...}, "op_id"?: "..."}` → the MCP envelope verbatim: `{ok, llm_mode, warnings, data}` or `{ok:false, error:{type,message}, hint?}`. 404 unknown tool, 503 when the `mcp` extra is missing |
| GET | `/api/events` | SSE; see below |
| POST | `/api/ops/{op_id}/cancel` | `{cancelled: true, op_id}`; 404 unknown op |
| POST | `/api/shutdown` | `{ok: true}`, then the process exits |

Errors are always `{"error": "<message>"}` with 400 (bad input), 401, 404,
405 (right path, wrong method), 503.

## Event feed (SSE)

`GET /api/events` holds one `text/event-stream` response open. Frames:

- `: connected` on open, `: ping` every 15 s when idle (comment frames)
- `data: {"type": "...", "seq": N, "ts": <unix>, ...}` per event

Event types grow over time; consumers must ignore unknown types. M1 defines:

- `progress` — `{op_id, tool, progress, total, message}` republished from a
  tool call's `ctx.report_progress` (only when the call carried an `op_id`)

Planned (later milestones): `consent_request`, `run_id`, `notification`,
board/tunnel lifecycle, ceremony outcomes.

## Streaming responses

Request-scoped streams (chat send, engine runs — from M5) return chunked
bodies of NDJSON, one JSON object per line, terminated by a `{"type":"done"}`
or `{"type":"error"}` line. The ambient SSE feed is never used for
request-scoped data.
