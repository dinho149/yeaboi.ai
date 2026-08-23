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
| GET | `/api/standup/dashboard` | query `session_id?` (blank = the most recent session), `run_id?` (open one past run instead of the latest) → the whole dashboard in one read |
| POST | `/api/standup/run` | body `{session_id, deliver?: false}` → a chunked NDJSON run. `deliver: false` builds the report without posting it anywhere |
| POST | `/api/standup/runs/{run_id}/delete` | drop one run from the saved-runs hub; 404 when unknown |
| GET | `/api/standup/schedule` | query `session_id` → the saved schedule plus the installed reminder offset |
| POST | `/api/standup/schedule` | body `{session_id, enabled, time, weekdays, lead_minutes, delivery_channels, remind_after}` → `{message, schedule}`; saves the config **and** installs or removes the OS jobs |
| GET | `/api/analysis/options` | what a setup wizard may offer on this machine |
| POST | `/api/analysis/steps` | a partial selection → `{steps, grid, run}`: which steps still apply, the component rows they may offer, and the payload the answers would run |
| GET | `/api/analysis/profiles` | the saved team profiles |
| GET | `/api/analysis/result/{team_id}` | one stored profile plus the cards it earned; 404 when unknown |
| POST | `/api/analysis/run` | the setup wizard's payload → a chunked NDJSON run |

The **standup dashboard** is
`{session_id, session_name, my_name, run_id, history, cards: [{key, title, member}], report, config, schedule, review, nudge, gap_issues, active: [name]}`.
`history` is the saved-runs hub — every run this session has done, newest first.
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

## Live boards

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/boards` | every board this process is hosting |
| POST | `/api/boards/retro` | open a retro board for the latest session; 409 when there is none |
| POST | `/api/boards/poker` | open a poker table over an already-fetched ticket list |
| GET | `/api/boards/{board_id}` | one board's host controls and current contents |
| POST | `/api/boards/{board_id}/link` | try the secure link again after a failure |
| GET | `/api/boards/{board_id}/invite` | the one link a teammate gets, code in the fragment |
| POST | `/api/boards/{board_id}/actions` | draft this retro's action items (one LLM call) |
| POST | `/api/boards/{board_id}/close` | end the board and flush it to its mode's store |
| GET | `/api/poker/options` | what a poker setup wizard may offer on this machine |
| GET | `/api/poker/sprints` | one source's sprint list, plus which row the cursor starts on |
| GET | `/api/poker/types` | the ticket-type toggles for one source, pre-checked to its defaults |
| POST | `/api/poker/tickets` | fetch the tickets one scope would estimate |

A **board snapshot** is
`{board_id, kind, title, session_id, project_name, started_at, host_url,
share_url, display_code, link, state}`. `kind` is `retro` or `poker`.
`state` is the board's own contents — `{grids, carried}` for a retro, the
poker table snapshot for poker.

**`host_url` is private.** It carries the admin token that makes its holder the
host; it is returned because opening the board window needs it, and it must
never be handed out as an invite. `/api/boards/{id}/invite` is what a teammate
gets, and it is empty until the tunnel lands — before then there is no address
that works for a reader.

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
| POST | `/api/reporting/run` | one delivery report, streamed as NDJSON |
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
stream ends `cancelled`.

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
run holds a coding-agent subprocess for many minutes behind the engine lock, and
the gate is a human decision — which a human-owned desktop app satisfies.

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
| GET | `/api/agents/{kind}/latest` | the last saved report, for an instant open |
| POST | `/api/agents/{kind}/run` | one fresh pass, streamed as NDJSON |
| POST | `/api/agents/{kind}/export` | write the report, or hand back its Markdown |

`kind` is one of `usage`, `advisor`, `standup`, `security`. Every mode's run and
history is an MCP tool already; what is native is the shape of the page. A pass
scans every session log on the machine, so a surface opens on the last saved
report and refreshes behind it — which needs the last artifact on its own and
the fresh one as a stream. Export is native because these four artifacts write
through `agentwatch/export.py` rather than the shared exporter, so `/api/export`
cannot reach them; `copy` is answered as data, never performed.

A run answers `progress` lines (bare phase strings), `component` lines (the
`analysis_component` dicts the phase checklist draws), then `done: {kind,
report}`. No `op` line — the agentwatch engines take no cancel event, and
backing out is free: the pass finishes and stores its report either way.

Provenance has no routes here. `provenance_audit` and `provenance_trace` are
request/response reads with no progress, no cancel and no page-shaped gap, so
the desktop's audit and trace explorer goes through the dispatcher like every
other tool-served capability.
