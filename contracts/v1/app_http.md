# Desktop backend HTTP contract (v1)

The wire between the Electron shell and `yeaboi app`. Pinned by
`tests/unit/test_app_wire.py`; the desktop main process (yeaboi-desktop's `src/main/`)
is the only intended client. Changing a key or a route shape here is a
contract change — update both sides and this file in the same PR.

## Startup handshake

`yeaboi app [--port N]` binds `127.0.0.1` and prints **exactly one** line to
stdout, then nothing else ever:

```
YEABOI_APP_READY {"pid":12345,"schema":31,"token":"…","url":"http://127.0.0.1:52341","version":"3.25.0"}
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
| GET | `/api/meta/capabilities` | the TUI card inventory verbatim: `{categories, solo, modes, agents, intake}` — `modes` is the Team menu, `solo` the Solo menu |
| GET | `/api/meta/tips` | `{tips: [{key, text, mode_key, is_new, is_beta, surfaces, worlds}]}` — `worlds` names the landing worlds (`solo`/`team`/`agents`) a tip is true in; the Solo home drops the Team-only ones |
| GET | `/api/meta/changelog` | `{entries: [{version, date, headline, summary, highlights[{text, areas, surfaces}]}], areas: [{name, color}]}` |
| GET | `/api/meta/privacy` | `{headline, statement: [str…], groups: [{key, title}], switches: [{key, env, on_value}], egress: [{key, group, what, where, when, default, off_switch}]}` — the privacy statement and egress-disclosure table, serialized verbatim from `yeaboi.privacy` (the copy owner every surface renders). Carries no capability and is never gated behind one: disclosure must always answer |
| GET | `/api/system/check` | `{summary, categories: [{key, title, blurb}], checks: [{key, label, status, detail, hint, feature, category}]}` — the optional-feature doctor. `status` ∈ `ok\|missing\|unsupported\|unknown`; `category` is a `categories[].key` and gives the section a row renders under, in that list's order. Icons are presentation and stay per-surface — the payload carries keys and titles only. **Offline by policy**: every probe is a filesystem/PATH/config read or a loopback-only socket — opening the page causes no egress and never triggers the cloudflared download, so a GET per open is safe |
| GET | `/api/tools` | `{available, tools: [name…]}` — the MCP inventory the dispatcher serves |
| POST | `/api/tool/{name}` | body `{"arguments": {...}, "op_id"?: "..."}` → the MCP envelope verbatim: `{ok, llm_mode, warnings, data}` or `{ok:false, error:{type,message}, hint?}`. 404 unknown tool, 503 when the `mcp` extra is missing |
| GET | `/api/events` | SSE; see below |
| POST | `/api/ops/{op_id}/cancel` | `{cancelled: true, op_id}`; 404 unknown op |
| POST | `/api/shutdown` | `{ok: true}`, then the process exits |

Errors are always `{"error": "<message>"}` with 400 (bad input), 401, 404,
405 (right path, wrong method), 503.

A changelog entry's `headline` is its one-line title: at most 60 characters,
naming what the release lets the reader do. It is always present — the loader
falls back to the summary's first sentence for an entry written before headlines
existed.

A changelog highlight's `surfaces` names which product surfaces it applies to,
from the fixed vocabulary `tui`, `desktop`, `web`. The key is always present on
the wire — the loader coerces a missing or invalid tag in the bundled data to
all three — and clients filter to their own surface.

`areas` is the accent table, not a filter: one `{name, color}` row per area tag an
entry may carry, `color` an `rgb(r,g,b)` string matching that mode's colour in the
mode grid. A client colours a tag by looking its name up here and falls back to a
neutral chip for a name it does not find — the desktop's own release ledger uses
area names this backend has never heard of.

`/api/meta/tips` carries the same vocabulary but, unlike the changelog, filters
at the source: the tip registry tags every tip with the surfaces it is true on,
and this backend is the desktop app's, so the payload is **already** the
`desktop` list. A tip naming a terminal keycap or a CLI flag never crosses the
wire. `surfaces` is still serialised, so the tag is legible to a reader, but the
client has nothing left to decide — filtering on it again is a no-op.

Two consequences for a client. `key` is not unique: one capability may carry
several tips. And the terminal's list is a different list, not a subset — so an
index into this one means nothing anywhere else.

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
| POST | `/api/settings/connection/verify` | body `{kind, …fields}` → `{ok, message}`. `kind` is any `verify_kind` the `/api/connections` rows report — the legacy literals (`github`, `jira`, `confluence`, `notion`, `elevenlabs`, `tavus`) plus every descriptor-verified connector key (`datadog`, `grafana`, `sentry`, `gitlab`, `custom_*`, …), and the accepted field names are that kind's declared verify fields (e.g. `token`, `app_key`, `base_url`, `email`, `space_key`). Omitted fields fall back to saved values, so a stored credential can be re-checked without echoing it — but a stored secret only travels to the stored host: a caller-supplied `base_url`/`email` requires **every** secret verify field of that kind in the same request (Datadog: `token` and `app_key`), and a supplied `base_url` must be https (400 otherwise; network, up to ~10s) |
| GET | `/api/connections` | the integration catalog: `{connectors: [{key, label, summary, detail, family, family_label, section, connected, read_only, managed_by, kind, docs_url, glyph, icon, accent, verify_kind, auth_env, auth_methods: [{key, label, summary, recommended, warning, setup_url, envs}], fields: [{env, label, secret, required, is_set, choices, default, placeholder, hint, help_url, help_scope, auth_method}]}], families, connected}`. `?all=1` is the browse view: every connector that could be added, plus the built-in integrations (GitHub, Jira, Azure DevOps Boards, Confluence, Notion, Slack, ElevenLabs, Tavus) as `managed_by: "credentials"` rows; the default lists only connected connector-layer rows — the view a Credentials-side "your integrations" panel renders. `kind` is a custom connection's kind (`api`/`webhook`/`mcp`); built-in and legacy rows send `""`. `icon` is a custom connection's uploaded icon — a server-validated `data:image/(png\|jpeg\|webp);base64,` URI, never SVG, decoded size ≤ 64KB; `""` everywhere else (built-in marks ship with the client). `managed_by` says where configuring happens — `"connections"` rows carry their own add flow (write the fields via `/api/settings/set`, then probe `verify_kind`), `"credentials"` rows deep-link to Settings ▸ Credentials/setup and their `verify_kind` is `""` when no probe exists (Slack, Azure DevOps). **Never carries a field value** — a field reports only whether it is set. `auth_methods` is empty for a connector with one way in, and a field's `auth_method` is empty when every method needs it; a client that ignores both keys renders exactly as it did before they existed. Exactly one method carries `recommended: true`, and every other carries a non-empty `warning` — a method yeaboi cannot bound says so on itself |
| POST | `/api/connections/custom` | save one user-created connection. Body: descriptor JSON — `{key: "custom_…", label, family, summary, detail?, docs_url?, glyph, accent, kind: api\|webhook\|mcp, auth_scheme: bearer\|basic\|header, header_name?, probe_path, probe_ok_status, webhook_verify?: token\|hmac, events?: {path, items_key, kind, title_path, ref_path?, severity_path?, status_path?, url_path?, started_at_path?, service_path?}, extra_fields?: [{label, env_suffix, secret?, header_name?, hint?}], icon_data?}` — **never a credential** (values are typed afterwards through `/api/settings/set`, exactly like a built-in connector's fields). An `mcp` kind stores a streamable-HTTP MCP server URL and optional bearer token (derived envs, values typed afterwards via `/api/settings/set`), verifies with the MCP initialize + tools/list handshake, and gathers nothing. A `webhook` kind requires the events mapping, ignores the HTTP-shape fields, and mints its delivery secret server-side — returned once as `webhook_secret` on the created row (`/api/webhooks/{key}/url` can show it again). `extra_fields` (`api` kind only, ≤ 4) declares extra credentials/config beyond the auth scheme — a Datadog-style app key beside the api key: `env_suffix` is UPPER_SNAKE and derives the env (`YEABOI_CUSTOM_<KEY>_<SUFFIX>`), `secret` defaults true, and a `header_name` sends the value as that request header on probe and fetch. `icon_data` is an optional icon in the `icon` row key's data-URI shape (png/jpeg/webp only, never SVG, ≤ 64KB decoded — the validator refuses the rest). The runtime validator is the gate: every problem comes back joined in a 400 `{"error": …}`. Success returns the new catalog row in the `/api/connections` row shape (`managed_by: "connections"`) |
| POST | `/api/connections/custom/draft` | body `{description}` → `{ok, draft, problems}`: one LLM pass from a plain-language service description to a candidate descriptor (same shape as the create body). Never saves — a draft with problems pre-fills the create form. The model proposes identity, look and shape only — any kind, `extra_fields` included; never a credential value, an env name, verify wiring or `icon_data` (network + one LLM call) |
| POST | `/api/connections/custom/{key}/delete` | remove one user-created connection — the descriptor AND its stored env values in the same act (a definition-less credential is an orphan). `{deleted: key}`; 404 for a key that is not a custom connection |
| GET | `/api/webhooks/status` | `{running, port, started_at, tunnel_url, connections: [{key, label, last_received_at}]}` — the inbound webhook receiver's state and per-connection delivery liveness (`last_received_at` is `""` while a connection waits for its first delivery). Offline: a local read, no probe |
| POST | `/api/webhooks/start` | bind the loopback receiver (fixed port, default 8642, `YEABOI_WEBHOOK_PORT` overrides — a conflict is a 503, never a port walk: a webhook URL a user pasted into a vendor's console must stay true) in this process → the status shape. The receiver itself is a **separate loopback server** and never joins this wire's auth: its own auth is per-connection (`X-Yeaboi-Token`, or a Stripe-shaped `X-Yeaboi-Signature` HMAC with a ±5 min replay window) |
| POST | `/api/webhooks/stop` | stop the receiver and any tunnel → the status shape |
| POST | `/api/webhooks/share` | open a cloudflared quick tunnel to the receiver → the status shape with `tunnel_url` set. The hostname **rotates on every share and expires on its own** — fit for testing a sender, not a durable endpoint (503 when the tunnel cannot open; the local receiver keeps running) |
| GET | `/api/webhooks/{key}/url` | `{key, url, tunnel_url, verify, header, secret, running, last_received_at}` — where one webhook connection's deliveries go and how they authenticate. **The one route that returns the secret whole**; showing it is a local, deliberate act, the same posture as a board's host URL. 404 for a key that is not a webhook connection |
| GET | `/api/settings/access/state` | the Cloudflare Access doctor, offline: `{logged_in, cert_path, jwt_installed, missing_keys}`. Cheap by construction — it does **not** resolve the cloudflared binary, because doing so downloads ~38 MB on first use and re-hashes the cached copy every call; a missing binary surfaces from the share itself |
| POST | `/api/settings/access/verify` | no body → `{ok, message}`. Runs the same preflight a board runs before publishing (`assume_mode`, so it answers before Share Mode is switched on); fetches the team's JWKS, so up to a few seconds of network |
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
| POST | `/api/chat/sessions` | body `{description, intake_mode?: "small_project"\|"smart", solo?: false}` → 201 with the session view. An absent `intake_mode` is classified from the description. `solo: true` opens a one-person intake (the Solo world): the team questions are defaulted to one developer and there is no member picker |
| GET | `/api/chat/sessions/{project_id}` | the session view; 404 when no such conversation is open or stored |
| POST | `/api/chat/sessions/{project_id}/send` | body `{text, images?: [..]}` → a chunked NDJSON turn; 409 while a turn is already running |
| GET | `/api/chat/sessions/{project_id}/questions` | `{questions: [{number, label, answer, remaining, skipped}], total, completed, derived}` |
| POST | `/api/chat/sessions/{project_id}/size` | body `{mode: "small_project"\|"smart"}` → `{changed, mode, reopened?}`; 409 in dry-run |
| POST | `/api/chat/sessions/{project_id}/attachments` | body `{image: base64, mime: "image/png"\|"image/jpeg", index}` → `{path, chip}`; 413 over 4.5 MB |

`images` on `send` is the **composer's whole attachment list, in order** — not
the images to send. Which of them travel is decided from the text, by the
surviving `[image #N]` chips, so deleting a chip detaches its image here
exactly as it does in the terminal. A client that posts attachments without
writing their chips into the text sends no images at all.

`questions` lists what this run actually asks — the essential gaps still open
plus everything already answered, never the whole 30-question bank. `derived`
is false when the gap derivation failed; the list is then answers only, and a
client must say so rather than present it as the plan. It backs three
affordances the terminal keeps apart: `/questions`, `/form`, and a bare
`/edit`. Re-asking one is an ordinary turn — `send` the literal `edit N`.

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

## Niko routes

Niko, the global assistant. Chrome rather than a page: the panel opens over
whatever route is showing, which is why its parity row claims the
`action:ask-niko` pseudo-path rather than a route of its own.

**Read-only end to end.** Niko's tool surface holds no write tool, so nothing
under `/api/niko/` changes anything — a turn reads stores and may *suggest* a
route. That is the guardrail; there is no confirmation step because there is
nothing to confirm.

| Method | Path | Notes |
|---|---|---|
| POST | `/api/niko/conversations` | → 201 with the conversation view (no body needed) |
| GET | `/api/niko/conversations` | `{conversations: [{id, title, messages, created_at, updated_at}]}`, newest-used first, capped at 30 |
| GET | `/api/niko/conversations/{conversation_id}` | the conversation view; 404 when unknown |
| POST | `/api/niko/conversations/{conversation_id}/send` | body `{question, route?, user_name?}` → a chunked NDJSON turn; 400 on an empty question, 404 when unknown, 409 while a turn is already running |
| POST | `/api/niko/conversations/{conversation_id}/delete` | archives it → `{archived: true, id}`; 404 when unknown |
| GET | `/api/niko/suggestions` | `?route=` → `{route, suggestions: [{label, prompt, icon}]}` — the chips the empty panel offers |

`delete` is a POST because this router serves GET and POST only, and it
*archives* rather than purges: a conversation is a record of what the user was
told. The terminal's saved-conversations hub does the permanent delete.

`route` on `send` is where the user is (`/agents/usage`, `/team/retro`) and
colours the answer toward that screen. Omit it rather than guessing — Niko says
it does not know which screen rather than inventing one. `user_name` is what to
call them; the shell reads it from its own identity file.

The **conversation view** is
`{id, title, created_at, updated_at, messages: [{id, role, content, route, created_at, tool_calls: [{tool_name, ok, error}]}]}`.
Tool calls are stored and replayed on purpose: a conversation replayed without
them shows an answer with no visible reason for it.

A **turn** streams these line types, in order: `op` first, then any number of
`token`/`assistant`/`tool_call`/`tool_result`/`navigate`, terminated by `done`,
`cancelled` or `error`. Consumers must ignore unknown types.

| Line | Shape |
|---|---|
| `op` | `{type: "op", op_id}` — cancel through `POST /api/ops/{op_id}/cancel` |
| `token` | `{type: "token", text}` — one slice of the answer as it is generated |
| `assistant` | `{type: "assistant", text}` — the finished answer, always sent |
| `tool_call` | `{type: "tool_call", tool_name, tool_input}` |
| `tool_result` | `{type: "tool_result", tool_name, ok, error}` |
| `navigate` | `{type: "navigate", route}` — a suggestion; the window may push it |
| `done` | `{type: "done", conversation_id, route, warnings: [..]}` |
| `cancelled` | `{type: "cancelled"}` |
| `error` | `{type: "error", message}` — classified human text, never a raw SDK string |

`assistant` always arrives even when `token`s streamed, and carries the same
text: a provider that cannot stream sends only `assistant`, so a client that
renders tokens must replace them with it rather than appending.

A `done` with an `AI answers unavailable` warning means no model was reachable
and the answer is a local signpost built from the card and route registries —
not an answer from the user's data. Say so rather than presenting it as one.

## Dashboard routes (M6)

The two run-and-read modes. Their read-only pieces are MCP tools already
(`standup_history`, `standup_config_get`/`_set`, `standup_members`,
`standup_repositories`, `standup_review`, `standup_gaps`,
`standup_practice_feedback`, `team_roster`, `team_profile_get`), reached over
`POST /api/tool/{name}`; the routes below are what MCP has no shape for.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/standup/dashboard` | query `session_id?` (blank = the most recent session), `run_id?` (open one past run instead of the latest) → the whole dashboard in one read |
| POST | `/api/standup/run` | body `{session_id, deliver?: false, solo?: false, project_id?}` → a chunked NDJSON run. `deliver: false` builds the report without posting it anywhere. `project_id` (a `proj-<8hex>` projects-table row id) scopes the run to that project; blank inherits the session's own link, an unknown id is a 400. `solo: true` is a one-person run (the Solo world): self-only roster, no tracker roster discovery, first-person summary; the stored report carries `solo` so the dashboard drops its team card |
| POST | `/api/standup/runs/{run_id}/delete` | drop one run from the saved-runs hub; 404 when unknown |
| GET | `/api/standup/schedule` | query `session_id` → the saved schedule plus the installed reminder offset |
| POST | `/api/standup/schedule` | body `{session_id, enabled, time, weekdays, lead_minutes, delivery_channels, remind_after, solo?: false}` → `{message, schedule}`; saves the config **and** installs or removes the OS jobs. `solo` is not saved — it rides on the installed job's command line, so the scheduled run is a one-person standup |
| GET | `/api/analysis/options` | what a setup wizard may offer on this machine |
| POST | `/api/analysis/steps` | a partial selection → `{steps, grid, run}`: which steps still apply, the component rows they may offer, and the payload the answers would run. `solo: true` in the answers marks a Solo-world wizard: the `members` step never applies and stale member picks coerce out of `run` |
| GET | `/api/analysis/profiles` | the saved team profiles |
| GET | `/api/analysis/result/{team_id}` | one stored profile plus the cards it earned; 404 when unknown. `?solo=1` drops the Team Members card from `cards` |
| POST | `/api/analysis/run` | the setup wizard's payload (plus an optional `project_id`) → a chunked NDJSON run |

The **standup dashboard** is
`{session_id, session_name, my_name, run_id, history, cards: [{key, title, member}], report, config, schedule, review, nudge, gap_issues, active: [name]}`.
`history` is the saved-runs hub — every run this session has done, newest first.
`cards` is the card vocabulary both surfaces share: `summary`, `my_update`,
`team`, `member:<name>`, `conflicts`, `production`, `activity`, `gaps`,
`schedule`, `notices` — computed per report, because a card with nothing in it
would advertise a feature rather than report a result. `production` carries
`report.ops_signals`: bounded per-source counts over a WIDER window than the
rest of the report, each signal stating its own `window_start`/`window_end`,
and attributable to nobody — an ops signal has no author field to carry one. `active` names the members
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
`{grid: {delivery, code, docs, ops}, features: [{key, label}], features_available,
steps, depths, default_depth, window_presets, default_window_days}`.
The `ops` row is the connected monitoring connectors, so it is empty on every
machine that has never connected one — which is what makes the `operational`
feature unselectable rather than merely disappointing. Its result is team-wide
counts and a per-30-day rate, never anything attributable to a person.
The run body is the wizard's answers:
`{source?, project_key?, team_name?, sprint_count?, features?, components?,
members_map?, analysis_scope?, depth?, window_days?, model?, project_id?}`.
`project_id` (a `proj-<8hex>` projects-table row id) is the edge the terminal
draws after an analysis: on `done` the run has created an analysis session
linked to that project and recorded the profile it produced as the project's
`default_analysis_profile_id`, so the next scoped plan seeds it. Blank creates
no session, exactly as before; an unknown id is a 400.

A **run** streams: `op` first, then `progress` (and, for standup, `run_id`
once its history row exists), terminated by `done`, `cancelled` or `error`.

| Line | Shape |
|---|---|
| `op` | `{type, op_id}` |
| `progress` | `{type, phase}` — one pipeline phase, as user-facing text |
| `run_id` | `{type, run_id}` — standup only; the history row this run writes |
| `done` | `{type, report}` (standup) or `{type, result, session_id?}` (analysis — `session_id` only on a run with a `project_id`: the session it created and linked) |
| `cancelled` | `{type}` — analysis only; nothing was persisted |
| `error` | `{type, message}` — a classified, one-line failure |

An analysis run is cancellable through `POST /api/ops/{op_id}/cancel`, which
sets the engine's cancel event. **A standup run is not**: `run_standup` has no
cancel seam, so its `op` line exists only to join progress to a run, and
cancelling it does nothing.

## Live boards

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/boards` | every board this process is hosting |
| POST | `/api/boards/retro` | open a retro board for the latest session; 409 when there is none |
| POST | `/api/boards/poker` | open a poker table over an already-fetched ticket list |
| GET | `/api/boards/{board_id}` | one board's host controls and current contents |
| GET | `/api/boards/{board_id}/host` | the private host link — main process only |
| POST | `/api/boards/{board_id}/link` | try the secure link again after a failure |
| GET | `/api/boards/{board_id}/invite` | the one link a teammate gets, code in the fragment |
| POST | `/api/boards/{board_id}/actions` | draft this retro's action items (one LLM call) |
| POST | `/api/boards/{board_id}/close` | end the board and flush it to its mode's store |
| GET | `/api/poker/options` | what a poker setup wizard may offer on this machine |
| GET | `/api/poker/sprints` | one source's sprint list, plus which row the cursor starts on |
| GET | `/api/poker/types` | the ticket-type toggles for one source, pre-checked to its defaults |
| POST | `/api/poker/tickets` | fetch the tickets one scope would estimate |

A **board snapshot** is
`{board_id, kind, title, session_id, project_name, started_at, share_url,
display_code, link, state}`. `kind` is `retro` or `poker`. `state` is the
board's own contents — `{grids, carried}` for a retro, the poker table snapshot
for poker. It carries no secret, so anything may draw it.

**The host link is private, and has a route to itself.** `GET
/api/boards/{id}/host` answers `{host_url}`, which carries the admin token that
makes its holder the host. It is deliberately not a snapshot field: exactly one
caller wants it — the shell's main process, opening the board window — and
everything else that lists or draws a board would only be carrying a token
around. The Electron proxy refuses to relay this path on the renderer's behalf
(`api-proxy.ts`'s `MAIN_ONLY`), so the token stays in main. It must never be
handed out as an invite: `/api/boards/{id}/invite` is what a teammate gets, and
it is empty until the tunnel lands — before then there is no address that works
for a reader.

A **link** is `{state, status, url, failed, expired, starting, notice}`, the
same shape on a board and on a share. `state` is `idle`, `starting`, `ready`,
`failed` or `off` (`off` = `YEABOI_NO_TUNNEL`; the board still works on
loopback for the host). `notice` is non-empty only for a time-critical event —
the expiry warning, or the expiry itself — and a surface renders it *above* its
own status text, because that is the one message a sticky action result must
not swallow.

Closing a board is what records the ceremony: `{closed, board_id, run_id}`,
`run_id` being the row written to the mode's store.

## Export, share, anonymize

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/export/destinations` | the menu for one mode: `{key, label, description, blocked, local}` |
| POST | `/api/export` | send one stored artifact to a destination |
| GET | `/api/shares` | every share this process is publishing |
| POST | `/api/shares` | publish one stored artifact behind an access code |
| GET | `/api/shares/{share_id}` | one share's link, code and edit count |
| GET | `/api/shares/{share_id}/invite` | one link carrying the access code |
| POST | `/api/shares/{share_id}/discard` | drop corrections from the document (the log keeps them) |
| POST | `/api/shares/{share_id}/close` | stop sharing; `{commit}` decides whether corrections are kept |
| GET | `/api/artifacts/kinds` | what each artifact kind can do: `{kind, export, share, anonymize, edit}` |
| GET | `/api/artifacts/{kind}/edits` | a kind's editable fields plus one artifact's recorded corrections |
| POST | `/api/anonymize` | mask one artifact, streamed as NDJSON |

All four take the same **artifact reference**: `{kind, session_id, run_id}`.
`kind` is `standup`, `retro`, `analysis`, `poker`, `reporting`, `performance`
or `roadmap`. A team profile is addressed by its team id in `session_id`, a
performance artifact by its engineer's name in `session_id`, and a roadmap by
its saved id in `run_id`.

Not every kind can do all four, and `/api/artifacts/kinds` is what says so —
a surface reads it rather than keeping its own table, so it never offers an
action the backend would refuse. **Poker exports and nothing else**: it has no
share document in any surface, because the estimates go back to the tracker
rather than out as a page. A team profile, a roadmap and the performance
artifacts share read-only — corrections with nowhere to be written back to
would be collected and dropped when the tunnel closed. Only a standup, a retro
or a delivery report is correctable.

`copy` is a **local** destination: the export returns `{destination, title,
markdown}` and performs nothing. A clipboard belongs to whatever is in front of
the person, not to a background process. `blocked` on a destination is the
Setup hint shown instead of failing after the click; `POST /api/export` refuses
a blocked destination with 409.

A **share snapshot** is `{share_id, kind, title, session_id, run_id, started_at,
share_url, display_code, editable, edits, editors, link}`. `edits` is the delta
recorded *in this session*, not the total — a reopened share replays its whole
log before anyone joins. `close` carries `{commit}`, defaulting to **false**:
keeping somebody else's corrections is the host's decision, not a consequence of
closing a window. A commit appends a corrected row; the generated original
survives, which is what makes a revert mean anything.

An **anonymize** run streams `op`, `progress`, then
`done: {note, replacements: [[original, placeholder]], warnings, result}`. The
*surface* applies the replacements to what it is already showing — masking is a
view over the same data, never a second copy of it. The pass never fails closed:
an LLM failure comes back as a warning over the deterministic seed mask.

## Reporting

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/reporting/options` | periods, configured sources, palettes, the deck style and its vocabulary |
| GET | `/api/reporting/sprints` | the quarter's sprints for `?session_id=`, pre-checked |
| POST | `/api/reporting/window` | the window a set of checked sprints makes |
| POST | `/api/reporting/run` | one delivery report, streamed as NDJSON. The body also takes an optional `solo` (false): a one-person report — first-person narrative, never "the team" |
| POST | `/api/reporting/style` | persist the deck style, or `{reset: true}` |
| POST | `/api/reporting/fit` | how many extra slides fitting everything costs |
| POST | `/api/reporting/export` | the styled deck outputs a plain export cannot write |

`report_delivery`, `reporting_history` and `reporting_export` are MCP tools, so
the report itself is already reachable headlessly. These routes are what MCP has
no shape for.

A **period** is `last_week`, `last_sprint`, `last_month`, `quarter` or `window`.
Only `quarter` earns the sprint multi-select and only `window` earns the two
dates — `/api/reporting/options` says which, so no surface keeps its own copy of
the rule. `window` refuses without both dates; a reversed or non-ISO range is a
400 naming the field.

`/api/reporting/window` is a round-trip for the same reason: which selection
leaves the quarter's plain label and which makes it `(custom)`, and the fact
that the window never runs past today, are one answer on every surface.

A **run** streams `op`, `progress`, then `done: {report, delivered}`; cancelling
the op raises at the next stage boundary, before anything is persisted, and the
stream ends `cancelled`. The run body also takes an optional `project_id`
(a `proj-<8hex>` projects-table row id): a scoped run frames itself with
that project's latest sprint plan; blank inherits the session's own link. It
also takes an optional `context_deps` (a list drawn from retro, standup, plan,
performance, analysis): the run's context-source toggles — omitted/null
inherits the project default, `[]` is an incognito run (no cross-mode
context). The standup run takes the same optional `project_id` (blank inherits
the session's own link, so an unlinked session runs team-wide exactly as before
projects existed; an unknown id is a 400) but no `context_deps` — its toggles
live in the session's saved standup config (`standup_config_set`'s
`context_deps`).

A delivery report carries `production`: one row per ops roll-up over the
report's **own** period (`kind`, `source`, `family`, `count`, `resolved`,
`severity`, `services`, `samples`, `window`) — the same row shape the standup
payload uses, so one component draws both. Empty unless an ops vendor is
connected, and never folded into the supporting-signals corroboration sentence:
an incident qualifies delivery rather than supporting it. The deck's Production
slide is an ordinary `list` slide, dropped by `include_production: false`.

`/api/reporting/fit` answers `{extra_slides, style}`. `extra_slides: 0` means
there is nothing to ask — the style that comes back is the one to export with.
Otherwise the surface asks, and posts `{expand}` to `/api/reporting/export`.
The saved preference stays `ask`: the answer applies to that export only.
Markdown and HTML come from `/api/export` like every other kind; the slide deck
and the `.pptx` are styled, so they come from here. `pptx_only` without
python-pptx is a 503 naming the extra that installs it.

## Performance

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/performance/roster` | who can be reviewed, and the status line under each name |
| GET | `/api/performance/engineer/{name}` | everything on file for one engineer |

The three workflows and the note are MCP tools (`perf_one_on_one_prep`,
`perf_one_on_one_complete`, `perf_six_month_review`, `perf_note_add`) — each is
a single LLM call with no progress or cancel seam, which is what the dispatcher
serves well. These two routes are the parts MCP has no shape for.

The roster is the people who did work on the board; with no tracker reachable it
falls back to the saved plan's team members. `latest` on an engineer is the
artifact a result screen opens — **review beats completion beats prep**, which
is usefulness order, not recency.

## Roadmap intake

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/roadmap/options` | the three sources and whether each is configured |
| GET | `/api/roadmap/saved` | the saved roadmaps, as the project list shows them |
| GET | `/api/roadmap/saved/{roadmap_id}` | one saved roadmap and its analysis |
| POST | `/api/roadmap/analyze` | one roadmap analysis, streamed as NDJSON |
| POST | `/api/roadmap/plan` | what Plan This hands to the planning chat |

The roadmap has no MCP tool and no CLI flag; both are tracked gaps older than
this surface. An unconfigured source stays offered — the hint names the setting
that fixes it, because hiding the option hides the fix.

`analyze` takes `{source_type, locator, roadmap_id}` and answers `op`,
`progress`, then `done: {analysis, roadmap_id}` — `roadmap_id` is the row it
inserted or updated. A `local` source outside the allowed paths is a **403 up
front** naming the path, not a sandbox failure discovered mid-analysis. The
engine never raises on a bad roadmap: an ingest or LLM failure comes back as an
analysis carrying warnings, so `error` on this stream means the process broke.

`plan` answers `{intake_mode, description}` — which projects are large enough
for the full intake is a backend decision, not a renderer one.

## Ship

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/ship/stories` | the latest saved plan's stories, and the default repo |
| POST | `/api/ship/target` | resolve a typed path to the repo a run will touch |
| GET | `/api/ship/runs` | every run this app session has launched |
| POST | `/api/ship/runs` | start one supervised run |
| GET | `/api/ship/runs/{key}` | one run's phases, gate and result |
| POST | `/api/ship/runs/{key}/gate` | approve or reject the diff |
| POST | `/api/ship/runs/{key}/cancel` | wind the run down cooperatively |

`ship_history` and `ship_status` stay MCP-read-only. Launching is not a tool: a
run holds a coding-agent subprocess for many minutes and the gate is a human
decision — which a human-owned desktop app satisfies. Unlike every other engine
call, a supervised run does **not** take the process-wide engine lock: it parks
at its gate until a person answers, and holding the lock there would stop the
chat, the dashboards and every tool for as long as the diff went unread.

**Known narrowing — the desktop ships stories only.** The terminal picker can
target an epic, a story or a task, and can split an epic into one stacked PR per
story (`ship.scope`, `engine.run_ship_batch`). These routes offer the story
level alone: `/api/ship/stories` lists stories and a run is always
`run_ship(level="story")`. Neither parity registry can see this — one checks
route paths, the other terminal constructs — so it is written down here.

**A ship run does not stream.** It lives in the backend and a surface polls
`GET /api/ship/runs/{key}`, because a renderer reload must not be able to
abandon a coding agent mid-diff. A snapshot is `{key, run_id, story_id,
story_title, repo, check_command, started_at, finished, cancelling, phases,
gate, result, failure, board}`. `key` is this process's handle; `run_id` is the
engine's own, and it is empty until the engine mints it — a gate is only ever
read by `run_id`, never by "the newest row", so a surface can never open a gate
over a diff its user did not launch.

`target` resolves to the git **toplevel**, which is where every write lands and
what the sandbox must have granted — the typed path is never what gets checked.
A repo outside the allowed paths is a 403 before the run, not a failure deep
inside a worktree write after real money has been spent.

The gate answers `{taken, resolution}`. `taken: false` is not an error: the
store's compare-and-swap means another surface answered first, so the honest
move is to re-read rather than retry.

## Ceremonies and the inbound Slack lane

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/ceremonies` | what is declared, when each fires, and where store and OS disagree |
| POST | `/api/ceremonies` | declare one and install its job |
| POST | `/api/ceremonies/{name}/enabled` | pause or resume, job and all |
| POST | `/api/ceremonies/{name}/remove` | forget it and tear its job down |
| POST | `/api/ceremonies/{name}/run` | fire one now, streamed as NDJSON |
| GET | `/api/slack` | the two-way lane's status, its identity links and what it applied |
| POST | `/api/slack/link` | bind a Slack id to a roster name, or drop one |
| POST | `/api/slack/poll` | read the Slack window once and apply what is new |

`ceremonies_list`, `ceremonies_history`, `slack_inbound_history` and
`slack_identities_list` are the MCP reads. Declaring, pausing and linking are
native for the reason those tools do not exist: declaring installs a launchd or
crontab job that outlives the session and spends money unattended, and linking
decides whose name goes on somebody else's report. Both are decisions for a
human at a machine they own.

`drift` is the load-bearing field: the store says what is declared, the OS says
what will fire, and nothing else in the app would ever mention the gap. A pause
removes the **job** and keeps the declaration — a paused ceremony that still
fires is the bug users report.

`run` answers `progress` lines then `done: {run, summary}`. There is **no `op`
line**: `run_ceremony` takes no cancel event, and a Cancel button over a run
nothing can stop would be a lie. Running one from here is not "scheduled" — the
staleness and monthly-cap guards answer questions an unattended fire raises, and
a human pressing Run now at 14:00 means it.

`poll` is offered as a button for the reason the engine has no `scheduled` flag:
a poll reads a fixed 48-hour window, everything it applies is free and
idempotent, and a poll that declines (no token, an empty allowlist, another poll
already running) is not a failure.

## The Agents family

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/agents/modes` | the four modes and how fresh each saved report is |
| GET | `/api/agents/{kind}/latest` | the last saved report, for an instant open; `?project_id=` scopes it (see below) |
| POST | `/api/agents/{kind}/run` | one fresh pass, streamed as NDJSON; body `{project_id?}` scopes it |
| POST | `/api/agents/{kind}/export` | write the report, or hand back its Markdown |

`kind` is one of `usage`, `advisor`, `standup`, `security`. Every mode's run and
history is an MCP tool already; what is native is the shape of the page. A pass
scans every session log on the machine, so a surface opens on the last saved
report and refreshes behind it — which needs the last artifact on its own and
the fresh one as a stream. Export is native because these four artifacts write
through `agentwatch/export.py` rather than the shared exporter, so `/api/export`
cannot reach them; `copy` is answered as data, never performed.

A run answers `component` lines — the `analysis_component` dicts the phase
checklist draws, which is every phase these engines emit today — then `done:
{kind, report}`. Anything that is not one arrives as a `progress` line carrying
a plain phase, so a mode that grows a bare-string step still reaches the
surface. No `op` line — the agentwatch engines take no cancel event, and backing
out is free: the pass finishes and stores its report either way.

**Scoping to a project.** `usage`, `advisor` and `standup` take a `project_id`
(the `proj-<8hex>` id of *Projects and sessions* below) and resolve it to the
project's `repo_path` setting: only sessions whose project directory is that
absolute path or sits under it (a worktree counts — never a basename match)
are read. `security` ignores it and stays machine-wide. Saved reports carry
no project, so a scoped `latest` answers `{report: null, as_of: "",
scoped_to: <repo_path>}` and the surface runs fresh; `run` echoes the same
`scoped_to` on its `done` line. Both answer `scoped_to: ""` when unscoped. An
unknown project is a 404; a project with no `repo_path` yet is a 400 naming
`yeaboi project set-defaults <id> --repo <path>` (the same key
`/api/projects/{project_id}/defaults` takes).

Provenance has no routes here. `provenance_audit` and `provenance_trace` are
request/response reads with no progress, no cancel and no page-shaped gap, so
the desktop's audit and trace explorer goes through the dispatcher like every
other tool-served capability.

## The shell's own furniture

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/ambience` | duck, music, saver and pet preferences, plus the station and screensaver catalogues |
| POST | `/api/ambience` | persist any subset of them |
| GET | `/api/beta` | the one-time entry gates and which have been acknowledged |
| POST | `/api/beta/{mode_key}/ack` | record that a gate was accepted |
| GET | `/api/feedback/options` | the feedback vocabularies, the attachment caps, and which route Submit will take |
| POST | `/api/feedback` | file the issue, or hand back a pre-filled browser URL |
| POST | `/api/feedback/polish` | one LLM rewrite of the draft, for review |
| POST | `/api/feedback/attachments` | keep one screenshot or log file, and return its path |
| GET | `/api/consent` | sandbox-consent requests still waiting on an answer |
| POST | `/api/consent/{req_id}` | `allow_once`, `allow_always` or `deny` |

None of these carries a `capability`. There is no ambience engine and no
ambience MCP tool because none of it is work anyone would ask an agent to do;
feedback has none on purpose, since filing an issue on a public repository under
the user's own token is not something an arbitrary tool client should do on
their behalf.

`/api/ambience` serves music as a **catalogue and a preference only**. The
terminal hands a station URL to `ffplay`; the desktop hands the same URL to an
`<audio>` element and needs no binary, so playback state lives in the renderer
and never round-trips. A bad channel index is refused rather than clamped, and
`true` is not accepted as an index — `bool` is an `int` in Python, and silently
selecting station 1 is worse than a 400.

`saver` is the same shape for the same reason: `idle_seconds`, the `styles`
catalogue (key → display name) and the chosen `style`, set with `saver_style`.
Only the surface drawing the screensaver knows how — the desktop renders every
style on a canvas from its own theme tokens, and the terminal understands just
`off`, drawing its ducks for anything else. So a style the reader cannot render
is not an error: `off` is the one value both surfaces must honour. `style` is
clamped to the default on the way out and refused on the way in, matching the
channel index.

`polish` never submits and never fails: `polished` is null when no LLM is
configured or the call failed, `status` says why, and the draft the person wrote
is what stands.

### The feedback bodies

`/api/feedback` and `/api/feedback/polish` take the same object:

```json
{
  "kind": "Bug", "area": "planning",
  "title": "Planning poker discards a vote",
  "description": "What I did, expected, and got instead.",
  "image_paths": ["/Users/you/.yeaboi/attachments/feedback/poker-room-a1b2c3d4.png"],
  "text_paths": ["/Users/you/.yeaboi/attachments/feedback/yeaboi-e5f6a7b8.log"]
}
```

`kind` and `area` are validated against the vocabularies `options` serves;
`title` is clamped to 250 characters and `description` to 20 000. Both path
lists are optional and default to empty.

**Every path must be one `/api/feedback/attachments` returned** — it is resolved
and required to sit inside the feedback attachments directory, and anything else
is a 400 rather than a silent drop. This is not ceremony: `polish` reads those
files and sends their contents to a model, so an unchecked path would read any
file on the machine. A path that passes the check but no longer exists is
dropped, because the report is still worth filing. At most six attachments,
across both lists.

Paths are absolute, exactly as `attachments` returned them — a `~` is not
expanded, and a path is checked against the kind its list names, so a `.png`
sent in `text_paths` is a 400.

`attachments` takes `{"name": "app.log", "mime": "text/plain", "data": "<base64>"}`
and answers `{"path", "name", "kind", "bytes", "lines"}` — `kind` is `image` or
`text`, and `lines` appears for text only. Base64 in JSON rather than multipart,
because there is no multipart parser on this server and the desktop's proxy
sends JSON bodies only. An unaccepted mime is a 400 and an oversized file a 413;
the ceilings are `max_image_bytes` and `max_text_bytes` from `options`.

`name` is the reporter's own filename, and the file is **stored under it** with
a short unique suffix (`app.log` → `app-3f2a91bc.log`), because the stored name
is what the issue body shows.

The two kinds reach GitHub differently, which is why they are two lists. Images
cannot be uploaded through GitHub's REST API at all, so the body lists their
local paths with a hint to drag them onto the issue. Text files are inlined into
the body inside a collapsed `<details>` block, tail first — except on the browser
path, whose 6 KB pre-filled URL cannot carry a log, where they are named instead.
The inlined text is redacted and home-relativized first — it is the one thing a
report publishes that the reporter did not type — and the whole inline share is
capped well under GitHub's 65 536-character body limit.

## The front page

The desktop home draws a newspaper: the yeaboi column (release notes, and the
posts and videos yeaboi.ai lists), then AI, engineering and research headlines
from a curated set of outlets. The backend does every fetch, so the desktop
stays loopback-only; the desktop draws a headline, its source and a link, and
never article text. `summary` is the outlet's own teaser, at most 240 characters.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/news` | `?refresh=1` forces a fetch. `{enabled, refreshing, schema, generated_at, stale, lead: item \| null, sections: [{column, title, items: [item]}], sources: [{id, name, home_url, column, ok, fetched_at, error, item_count}]}` — `item` is `{id, title, url, source_id, source_name, published, summary, image_url, kind, topic, persona, column}` |

- `column` is one of `yeaboi`, `ai`, `engineering`, `research`; `kind` one of
  `article`, `video`, `release`, `post`; `topic` one of `security`, `policy`,
  `compute`, `media`, `models`, `research`, `tooling`, `howto`, `general`;
  `persona` one of the desktop's eight duck ids (`engineer`, `teacher`,
  `martial`, `chef`, `astronaut`, `dj`, `detective`, `wizard`). `published` is
  ISO 8601 with an offset, or `""` when the outlet gave none. `image_url` is
  the outlet's own picture URL or `null`; the desktop does not draw it.
- `lead` is the story the engine put at the top (the newest yeaboi post or
  video under a week old, else the newest AI headline) and is not repeated in
  its section.
- **Stale-while-revalidate.** The cached paper (30-minute TTL) is answered at
  once. `stale: true` means it has expired and a refresh is running
  (`refreshing: true`) — ask again in a few seconds. A refresh that fails
  keeps the last paper; an outlet that fails keeps its last headlines and
  reports `ok: false` with an `error`.
- `enabled: false` (`YEABOI_NEWS=off`, Settings ▸ Privacy) answers the yeaboi
  column from the bundled changelog alone and nothing leaves the machine. Every
  outlet is named in `GET /api/meta/privacy` under the `news` row.

## Consent

The asking half is not a route. `fs_policy` in interactive mode queues a
`ConsentRequest` for every sandbox denial; `app/consent.py` drains that queue on
its own thread and publishes `consent_request` on the ambient feed. There is no
turn to be between here — a denial can come from a tool call, a native route, a
board thread or a run that is streaming NDJSON at the time — which is why it is
polled rather than drained after each handler.

The raise still happens: the access that triggered the request has already
failed, and consent is for the retry, exactly as in the TUI. `granted` in the
answer is what the sandbox now believes, not what the person clicked.

Two routes check a path **before** using it — `POST /api/roadmap/analyze` for a
local file and `POST /api/ship/runs` for a repository — because discovering the
refusal later means a sandbox traceback mid-stream, or a coding agent failing
deep inside a worktree write after spending real money. Those two call
`fs_policy.request_consent`, which queues the request without raising: they
still answer 403, and the modal is open by the time it arrives, so answering it
makes the retry work. `GET`-shaped probes (`/api/ship/target`, which validates a
repo path as it is typed) deliberately do not ask — a modal per keystroke is not
consent, it is a nag.

## Awareness

`app/awareness.py` publishes `notice` events onto the same feed for the things
that happen while nobody is looking: a ceremony that fired from an OS job, and a
ship run that reached its approval gate. Both are polled, because neither
happens in this process.

| Field | Meaning |
|---|---|
| `kind` | `ceremony_ran`, `ceremony_failed`, `ship_gate` |
| `quip` | the line the duck says, ≤ 40 characters |
| `sticky` | stays up until answered instead of fading |
| `route` | the desktop route that answers it |

Only `ship_gate` is sticky: a ceremony that fired is news, an unapproved diff is
a question, and a question that fades out unanswered is worse than one never
asked. The first poll after start announces nothing — it records where things
stand, so launching the app does not greet you with a week of stale news.

Things you did and watched happen are **not** here. A run's own NDJSON stream
already says when it finished, and the renderer's duck quips off that; the
ambient feed is for what the window missed.

## Dictation (M11)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/voice` | `{state, detail, model, model_cached, device, install: {available, blocked, size_mb, command}, max_bytes}` |
| POST | `/api/voice/offer` | body `{enabled}` — take or withdraw the standing install offer |
| POST | `/api/voice/install` | NDJSON: `op`, then `stage` lines, then `done`/`error`; cancellable |
| POST | `/api/voice/transcribe` | body `{audio: "<base64>", mime}` → `{text}` |

**The microphone is the window's, not Python's.** In the terminal, dictation is
capture *and* transcription and both belong to Python: PortAudio opens the
device, negotiates a format it will accept, and hands WAV frames to Whisper.
Here the window captures — `getUserMedia` picks the device, draws the level
meter, and owns the OS permission prompt — and Python transcribes what arrives.

Three things follow, and they are the whole reason this section is short:

- `state` comes from `voice.transcription_state()`, not `voice_state()`. The
  question is "can this machine transcribe", and answering it with the terminal's
  question would refuse a machine that can, then install an audio backend to fix
  a problem it does not have.
- The install fetches `voice.TRANSCRIBE_PACKAGES` (`faster-whisper` alone), not
  the `voice` extra. `state`, the size estimate, the sticky no-wheel verdict and
  the "never" answer are all the shared ones, so declining here stops the
  terminal asking too.
- `device` is the `VOICE_DEVICE` preference passed through **unresolved**. The
  terminal resolves that name against PortAudio's device list and the window
  against Chromium's; the name is the shared part, and neither surface may
  resolve it for the other.

The blob is a container the stdlib cannot read (webm/opus, or mp4), so it goes
to `transcribe_media` — faster-whisper's PyAV path, which the poker duel already
uses — rather than the WAV path the terminal records into. It travels base64 in
a JSON body because the IPC proxy carries JSON and nothing else; `max_bytes` is
the decoded ceiling, set below the server's own body cap with room for base64's
third, and a body dropped for exceeding that cap answers 413 rather than
reporting a silent take.

`stage` names the same four steps the terminal animates — `install`, `download`
(with a real byte `fraction`), `load`, then `done` — because both surfaces drive
one installer and a person who has seen one should recognise the other. A failed
model download is a `done` with a `warning`, not an `error`: the packages are in,
so the weights simply arrive lazily on the first dictation.

## Solo

The Solo world shares every engine with Team; what it owns is the welcome's
"where am I" strip. The terminal builds it once when the Solo menu is entered,
and this route serves the same snapshot to the desktop's Solo home — one
builder (`yeaboi.solo.today.build_today_snapshot`), so the two surfaces never
disagree about today.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/solo/today` | `?project_id=` narrows the standup and plan reads to that project ('' = newest of everything). Returns the `TodaySnapshot` fields verbatim, text and numbers only: `project_id`, `project_name`, `standup_date`, `standup_summary`, `standup_blockers`, `sprint_name`, `sprint_day`, `sprint_total_days`, `confidence_pct`, `confidence_label`, `confidence_trend`, `next_story_id`, `next_story_title`, `next_sprint_name`, `plan_session_id`, `plan_scoped`, `spend_usd`, `spend_sessions`, `spend_known`, `warnings`. An empty string or zero is the honest empty state (no standup yet, no plan yet); `warnings` lists the sources that could not be read. The spend is the last agentwatch ingest's, never a fresh scan |
| GET | `/api/solo/review` | `?session_id=&project_id=` scope the reads (blank = everything). `{latest: {run_id, review} \| null, history: [{id, session_id, project_id, run_at, week_label, week_start, week_end, project_name, action_count}], carried: [ReviewAction], beta_notice}` — `carried` is last review's still-open actions with the `id`s a run's `carried_statuses` takes; `beta_notice` is the gate copy |
| GET | `/api/solo/review/runs/{run_id}` | one saved review: `{run_id, review}`; 404 when unknown |
| POST | `/api/solo/review/run` | body `{session_id?, project_id?, context_deps?: [tokens] \| null, week_end?: "YYYY-MM-DD", carried_statuses?: {action_id: "done" \| "dropped" \| "pending" \| "carried"}}` → a chunked NDJSON run in the standup's line shapes: `{type: "op", op_id}` first, then `{type: "progress", phase}` per engine phase (`scope, standups, plan, delivery, carried, model, save`), then `{type: "done", run_id, review}` or `{type: "error", message}`. One `progress` line per phase, in that order; 400 when `week_end` is not an ISO date. Not cancellable — the engine has no cancel seam. The review is stored and exported to Markdown |
| POST | `/api/solo/review/runs/{run_id}/delete` | drop one review from the saved-runs hub: `{deleted, run_id}`; 404 when unknown |

**Weekly Review** is the Solo world's own capability — a self-review of the
week (went well, to change, on track against the plan, actions carried forward)
over the user's own standups, delivered tickets and sprint plan. The desktop
renders it at `/solo/review` (hub) and `/solo/review/report?id=` (one saved
run). Export stays on the MCP tool (`/api/tool/weekly_review_export`).

## Projects and sessions

A project is the durable way to work: every run inside it shares context
through `ProjectScope`. A session is the other way — one run of one mode,
unscoped. These routes are the projects engine's verbs on the wire, plus the
one read no engine owns: the union of every mode's saved runs.

`{project_id}` here is the engine's `proj-<8hex>` id from the `projects` table
in sessions.db. It is **unrelated** to the `{project_id}` segment of
`/api/chat/sessions/{project_id}`, which is the planning chat's own handle.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/projects` | `?include_archived=` (`1`/`true`/`yes`/`on`; default off). `{projects: [row]}`, most recently active first. A row is `{project_id, name, description, settings, created_at, last_active, archived, session_count}` |
| POST | `/api/projects` | body `{name, description?}` → the new row (no `session_count`). A blank name is a 400 |
| GET | `/api/projects/{project_id}` | the row plus `session_ids` (the linked planning/analysis sessions, newest first); 404 when unknown |
| GET | `/api/projects/{project_id}/sessions` | `?mode=&limit=` → `{sessions: [row]}` — the project's runs across every mode (see the row shape below); 404 when the project is unknown |
| POST | `/api/projects/{project_id}/defaults` | body `{defaults: {…}}` → `{project_id, settings}` (the merged settings). Accepted keys: `default_analysis_profile_id`, `default_context_deps`, `repo_path` (an absolute path — the repo the Agents world scopes to). An unknown key, an empty object, or a `repo_path` that is not an absolute path (or is the filesystem root) is a 400; an unknown project a 404 |
| GET | `/api/sessions/recent` | `?limit=&mode=&project_id=` → `{sessions: [row]}` — the newest runs across every mode, machine-wide or one project's |

A **sessions row** is `{session_id, run_id, mode, title, created_at, last_modified, project_id}`:

- `mode` is one of `planning`, `analysis`, `standup`, `retro`, `reporting`,
  `ship`, `review`. Planning and analysis rows are `sessions_meta` sessions and
  carry `run_id: ""`; every other row is one saved run of that mode's store,
  and `run_id` is that store's own id (the standup/retro/reporting/review
  history row as a string, the ship run id).
- `title` is the same label the terminal lists — the planning session's
  display name, `Standup — <date>`, `Retro — <date>`, `Report — <period>`,
  `Ship — <item> · <status>`, `Week <label>`.
- `project_id` is the project the run's planning session is linked to, `""`
  when unscoped.
- Newest `last_modified` first; `limit` defaults to 20 and `0` means every
  row. A mode with no saved runs is simply absent — nothing is invented. An
  unknown `mode` is a 400; an unknown `project_id` on `/api/sessions/recent`
  is an empty list.
