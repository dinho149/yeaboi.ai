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

## Settings routes (M4)

Reads are masked: a secret field's `value` is a `abcd••••`-style preview and
the raw credential **never** appears in any response — secrets are write-only
over this wire. Writes are allowlisted to the engine's field registry
(`yeaboi.settings.engine`); an unknown key or an off-list choice value is a 400.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/settings` | `{fields: [{env, label, section, secret, value, is_set, choices, choice_labels, active_choice, default, action, help_url, help_scope}], sections, config_path, voice: {state, detail, devices}}` |
| GET | `/api/settings/providers` | the setup-wizard catalog: `{providers, anthropic_auth_modes, token_help}` |
| POST | `/api/settings/set` | body `{key, value}` (`""` clears) → `{ok, key, message, restart_required}` |
| POST | `/api/settings/allowed-paths` | body `{paths: [..]}` → same write shape |
| POST | `/api/settings/data-dir` | body `{value, move?: bool}` → same write shape with `restart_required: true` |
| POST | `/api/settings/provider/verify` | body `{provider, credential, model?}` → `{ok, message}` (network, up to ~8s) |
| POST | `/api/settings/provider/models` | body `{provider, credential}` → `{models, default, hints}` (discovered-first merge) |
| POST | `/api/settings/signin/start` | spawn `claude setup-token` → `{started, message}` |
| GET | `/api/settings/signin` | poll → `{active, url?, awaiting_code?, done?, ok?, saved?, message?}`; on first token sighting the credential is persisted before `saved: true` is reported — the token itself is never in the body |
| POST | `/api/settings/signin/code` | body `{code}` → `{ok: true}`; 404 with no session |
| POST | `/api/settings/signin/cancel` | stop and discard the session → `{ok: true}` |

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

## Chat routes (M5)

The planning conversation. Sessions live in the backend (one `ChatSession` per
project id, the same project store the TUI resumes from), so a reloaded window
rejoins the conversation it left rather than restarting it.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/chat/sessions` | body `{description, intake_mode?: "small_project"\|"smart"}` → 201 with the session view. An absent `intake_mode` is classified from the description. |
| GET | `/api/chat/sessions/{project_id}` | the session view; 404 when no such conversation is open or stored |
| POST | `/api/chat/sessions/{project_id}/send` | body `{text, images?: [..]}` → a chunked NDJSON turn; 409 while a turn is already running |

The **session view** is
`{project_id, stage, opening, transcript: [<event>], question: {question_text, choices, multi_select, auto_submit, prior_art, suggestion, progress, phase_label, current_question, preamble_lines}}`.
`opening` is the description until it has been sent as the conversation's
first turn — a client that skips it leaves the intake with nothing to plan.
`stage` is one of `intake`, `review`, `pipeline`, `epic`, `capacity`, `spike`,
`chat` — the one predicate every surface routes on.

A **turn** streams these line types, in order: `op` first, then any number of
`token`/`assistant`/`question`/`await_confirm`/`artifact`, terminated by
`done`, `cancelled` or `error`. Consumers must ignore unknown types.

| Line | Shape |
|---|---|
| `op` | `{type, op_id}` — cancel the turn with `POST /api/ops/{op_id}/cancel` |
| `token` | `{type, text}` — a chunk of the reply as it forms |
| `assistant` | `{type, text}` — the finished reply, as prose |
| `user` | `{type, text}` — replay only |
| `question` | `{type, text, number}` — an intake question, decorated for chat |
| `await_confirm` | `{type, kind, prompt}` — an artifact card plus the line asking for a verdict |
| `artifact` | `{type, kind}` — a card rendered from state (replay only) |
| `done` | `{type, stage}` — the turn landed; `stage` is the new one |
| `cancelled` | `{type}` — the turn was cancelled; state is unchanged |
| `error` | `{type, message}` — a classified, one-line provider/integration failure |

## Dashboard routes (M6)

The two run-and-read modes. Their read-only pieces are MCP tools already
(`standup_history`, `standup_config_get`/`_set`, `standup_members`,
`standup_repositories`, `standup_review`, `standup_gaps`,
`standup_practice_feedback`, `team_roster`, `team_profile_get`), reached over
`POST /api/tool/{name}`; the routes below are what MCP has no shape for.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/standup/dashboard` | query `session_id?` (blank = the most recent session) → the whole dashboard in one read |
| POST | `/api/standup/run` | body `{session_id, deliver?: false}` → a chunked NDJSON run. `deliver: false` builds the report without posting it anywhere |
| GET | `/api/standup/schedule` | query `session_id` → the saved schedule plus the installed reminder offset |
| POST | `/api/standup/schedule` | body `{session_id, enabled, time, weekdays, lead_minutes, delivery_channels, remind_after}` → `{message, schedule}`; saves the config **and** installs or removes the OS jobs |
| GET | `/api/analysis/options` | what a setup wizard may offer on this machine |
| POST | `/api/analysis/steps` | a partial selection → `{steps, grid, run}`: which steps still apply, the component rows they may offer, and the payload the answers would run |
| GET | `/api/analysis/profiles` | the saved team profiles |
| GET | `/api/analysis/result/{team_id}` | one stored profile plus the cards it earned; 404 when unknown |
| POST | `/api/analysis/run` | the setup wizard's payload → a chunked NDJSON run |

The **standup dashboard** is
`{session_id, session_name, my_name, cards: [{key, title, member}], report, config, schedule, review, nudge, gap_issues, active: [name]}`.
`cards` is the card vocabulary both surfaces share: `summary`, `my_update`,
`team`, `member:<name>`, `conflicts`, `activity`, `gaps`, `schedule`,
`notices` — computed per report, because a card with nothing in it would
advertise a feature rather than report a result. `active` names the members
with attributed activity; a report saved before activity counts existed falls
back to its summary text rather than reading as all-quiet.

An **analysis result** is `{team_id, cards: [{key, title}], profile, examples}`.
The card keys are `velocity`, `team`, `estimation`, `workflow`, `writing`,
`trends`, `recommendations`, `code-health`, `ai-adoption`, `documentation`,
`insights`; the delivery cards appear iff a tracker profile exists and the
global scan cards iff that scan ran.

A wizard asks `/api/analysis/steps` rather than deciding for itself, so the
terminal and the desktop walk the same steps: a second copy of the rules is a
second thing to drift. It carries the answers so far plus `model_offered`
(whether a local model can be picked — the caller owns that probe).

**Analysis options** is
`{grid: {delivery, code, docs}, features: [{key, label}], features_available,
steps, depths, default_depth, window_presets, default_window_days}`.
The run body is the wizard's answers:
`{source?, project_key?, team_name?, sprint_count?, features?, components?,
members_map?, analysis_scope?, depth?, window_days?, model?}`.

A **run** streams: `op` first, then `progress` (and, for standup, `run_id`
once its history row exists), terminated by `done`, `cancelled` or `error`.

| Line | Shape |
|---|---|
| `op` | `{type, op_id}` |
| `progress` | `{type, phase}` — one pipeline phase, as user-facing text |
| `run_id` | `{type, run_id}` — standup only; the history row this run writes |
| `done` | `{type, report}` (standup) or `{type, result}` (analysis) |
| `cancelled` | `{type}` — analysis only; nothing was persisted |
| `error` | `{type, message}` — a classified, one-line failure |

An analysis run is cancellable through `POST /api/ops/{op_id}/cancel`, which
sets the engine's cancel event. **A standup run is not**: `run_standup` has no
cancel seam, so its `op` line exists only to join progress to a run, and
cancelling it does nothing.
