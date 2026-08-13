# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Terminal-based AI Scrum Master agent built with LangGraph, LangChain, and Anthropic Claude (with OpenAI, Google, AWS Bedrock, and local Ollama as alternative providers). Two audiences behind one landing split: **Humans** (decomposes projects into epics, user stories, tasks, and sprint plans; standups, retros, poker, performance, reporting) and **Agents** (the `agentwatch` family — cost, daily digests, and security posture of the AI coding agents working across the SDLC, computed locally from Claude Code session logs).

## Commands

```bash
make test                 # Unit + integration + contract tests (full suite, no API keys needed)
make test-fast            # The whole unit lane, in parallel (~50s)
make test-scoped          # Only the areas the working tree touches + the always-run guards
make test-slow            # Integration + contract only — what CI's second test job runs
make test-v               # Full suite verbose
make test-all             # Everything including golden evaluators
make lint                 # Lint with ruff
make format               # Format with ruff
make run                  # Run the CLI (ARGS="--flag" to pass arguments)
make run-dry              # Run TUI with fake delays, no LLM calls
make eval                 # Run golden dataset evaluators
make contract             # Run contract tests (recorded API responses)
make smoke-test           # Live API smoke tests (requires real credentials)
make snapshot-update      # Update syrupy snapshot baselines after formatter changes
make budget-report        # Show prompt token counts for trend monitoring
make web                  # Build the front-end bundles (commit src/yeaboi/web/static after)
make web-check            # What CI runs: typecheck + rebuild + fail if the committed bundles are stale
make web-dev              # Vite dev server on :5399 with HMR, proxying /api to a dev board
make dev-board            # Seeded retro board on :5173 for front-end development
make dev-poker            # Seeded planning-poker board on :5273
make dev-deck             # Seeded reporting slide deck on :5373
make graph                # Generate agent graph visualisation PNG
make demo                 # Re-record docs/demo.cast.gz + docs/demo.gif (scripted, no interaction; needs agg)
make demo-render          # Re-render the GIF from the committed cast (theme/size tweaks, no re-record)
make build                # Build sdist + wheel into dist/
make publish              # Publish to PyPI
make record               # Re-record VCR cassettes against real APIs
make clean                # Remove build artifacts and caches
```

Run a single test: `uv run pytest tests/unit/test_state.py -v`
Run a single test class: `uv run pytest tests/unit/test_state.py::TestPriority -v`

**CI runs the tests a change touches, not all of them.** `scripts/test_scope.py` maps changed paths
onto areas — one per cowork workstream, so the fleet and CI share a vocabulary — and `ci.yml`'s
`scope` job feeds the result to every other job. Three rules make it safe: `ALWAYS` runs the ~30
guards that scan the repo rather than importing a module (surface parity, the workflow schema, the
committed bundles, the Go lockstep constants), `GLOBAL` forces the whole suite for anything reached
by everything (`conftest.py`, `sessions.py`, `ui/shared/`, `pyproject.toml`), and **any path the
registry does not recognise runs everything**. `tests/unit/test_test_scope.py` fails the build when
a source file is claimed by no area or a test file is selected by nothing — a selector's failure
mode is silence, so the totality check is the feature. The five required status checks
(`Unit tests`, `Integration & contract tests`, `Lint (ruff)`, `Format check (ruff)`, `Security
scan`) never carry an `if:`; scoping changes what they run, never whether they report.

Terminal GIFs for the README: `make demo` re-records `docs/demo.cast.gz` + `docs/demo.gif` from a scripted pty session — deterministic, no human interaction, verified for sanity before it exits (asciinema is not needed; install `agg` via `brew install agg`). `make demo-render` re-renders the GIF from the committed cast for theme/size tweaks without re-recording.

## Parallel Development (worktrees)

Each feature gets its own git worktree under `<main checkout>/.claude/worktrees/<name>` with its own branch, `.env`, uv venv, and pre-commit hooks. Never develop two features in one checkout. Creating a worktree fetches `origin` and cuts the new branch from latest `origin/main` (and fast-forwards the main checkout's `main` when that is safe), so it does not inherit a stale local base. Reusing an existing local branch leaves it untouched, and a branch that exists only on `origin` is checked out tracking the remote branch rather than re-cut from main — the script reports how far behind it is; rebase it with `/sync-main`.

```bash
make wt-new NAME=my-feature       # create worktree off latest origin/main + open VS Code with claude auto-running
make wt-headless NAME=my-feature  # same, WITHOUT VS Code (for background-agent work)
make wt-issue ISSUE=123           # worktree from the branch of GitHub issue 123 (linked branch / closing PR); HEADLESS=1 to skip VS Code
make wt-list                      # list worktrees (branch, clean/dirty, path)
make wt-rm NAME=my-feature        # remove worktree dir + branch
```

Slash commands (in `.claude/commands/`): `/wt` (worktree ops from inside a session), `/sync-main` (rebase on latest main + re-verify), `/ship` (independent review → full tests → commit → push → PR), `/babysit-prs` (survey open PRs, spawn fix agents for red CI), `/migrate` (fan out a mechanical migration across many files via parallel worktree agents), `/cowork` (drive the standing workstreams — see below).

### Verification loop

- **Every turn (automatic)**: a Stop hook runs `make lint` + `make test-scoped` whenever a turn ends with dirty `.py` files, and a PostToolUse hook ruff-formats every edited `.py` file. Hook scripts live in `scripts/claude-hooks/`; wiring is in `.claude/settings.json`.
- **At ship time (`/ship`)**: an independent fresh-context agent reviews the diff against the task (spec-fit + conventions), then the full `make test` + `make lint` gate runs before commit/push/PR.
- **In CI**: `claude-review.yml` posts an async code + security review once the full CI suite has passed on a PR (non-blocking; `ci.yml` remains the merge gate).

### Orchestration conventions

When driving multiple features at once, work as an **orchestrator**: one main session, one background agent per feature, each in its own worktree (`make wt-headless`). The orchestrator kicks off agents, tracks them, reviews **final diffs** (not intermediate steps), and runs `/ship` per feature when green. Use `make test-fast` in the inner loop; the full `make test` runs at ship time.

## Cowork (`cowork/`)

**The fleet does exactly two things: it maintains what exists, and it builds one provider integration at a time.** Fifteen standing workstreams. Thirteen are *maintenance* sweeps — one per mode: **planning** (`agent/` + `prompts/` + `ui/session/`), standup, analysis, reporting, poker, retro, performance, roadmap, artifacts-sharing, **agents** (the agentwatch family + `pricing.py`; its daily routine posts the agent standup digest rather than sweeping), plus tui-ux (including the `usage` and `settings` pages), web-ux and platform — and they may only return `bug`, `chore`, `docs` or `security`. `security` sweeps twice weekly. **`integrations` is the one that builds**, weekdays, as a week-long campaign. Sweep cadence is tiered to surface size: weekly for large, fortnightly for mid, monthly for small. Routines are **account-scoped, not repo files**; `cowork/` is the versioned source of truth their prompts point at. Start at `cowork/README.md`.

- **`cowork/definition-of-done.md` is the one contract**, binding on routines *and* on `/ship`: Linear ticket, tests, lint, security, surface parity, observability, web bundles, Notion page, Slack post, review feedback. Items 2–7 gate the PR opening; item 10 gates the *merge* — the `pr-feedback` commit status stays red while a blocker/should-fix finding or an unresolved review thread is unanswered, which is the one part of the contract nothing used to enforce.
- **`cowork/house-rules.md`** holds the closed auto-lane allowlist and, beside it, **the campaign lane**. Everything not on either becomes a `cowork:proposal` GitHub issue; a human approves by adding `claude-implement`, which the existing `claude.yml` job then implements. GitHub issues are the queue — there is no other shared state between runs. A workstream may hold **two open proposals** (`PROPOSAL_CAP`, counted by `--proposal-slots` / `make cowork-slots`, never by eye), and an unreadable count is zero slots rather than two: a sweep that cannot see the queue files nothing, because the alternative is a backlog nobody can read filed by a fleet that never stops.
- Three crew agents in `.claude/agents/`: `cowork-scout` (read-only survey), `cowork-scribe` (**the only author of outbound comms to Linear/Slack/Notion/issues** — the `slack-relay` routine's acks and human-verb relays are the one other writer), `cowork-builder` (implements one item in its charter's paths).
- **`cowork/models.md` is the only file that names a model.** Everything else — routines, agents, `/ship`, `/migrate`, `/babysit-prs` — names a *tier* (`heavy`/`deep`/`standard`/`fast`/`inherit`); every agent stays `model: inherit` so the caller decides. The `.github/workflows/*.yml` jobs can't read a markdown table, so they read `vars.YEABOI_MODEL_*` repo variables with a `||` fallback pinned to prior behaviour. `tests/unit/test_cowork_models.py` fails if a model id is hardcoded anywhere else — including in `.claude/commands/` and `scripts/cowork_setup.py`.
- **The fleet reports what it shipped, not what it is about to run.** `cron/shipped-standup.md` posts one message at 18:00 UTC: the day's merged PRs with the trace behind each (what proved it, the review verdict, the merge time), what is still building, what is stuck, and the pre-release it all landed in. It replaced two things — a 05:45 schedule post that was the one routine forbidden from staying quiet, and the per-PR ship note that `pr-merged-close-loop.md` used to fire once per merge with batching explicitly forbidden. The schedule renderer survives on demand: `scripts/cowork_setup.py --agenda` matches every cron against the date, converts to `DISPLAY_TZ` (UTC stays the source of truth and rides along in brackets), and `make cowork-agenda` / `/cowork today` print it. Every routine file still carries a `**Summary**` line capped at 90 characters.
- **Security, bugs, chores and docs ship unattended, and so does a whole integration.** `cowork/house-rules.md`'s auto lane covers the first four — a bug enters it only on a regression test that fails before the fix and passes after, and behaviour may change while user-facing wording may not. What replaces the approval is the gate, not trust: an independent `code-reviewer` before the PR opens, `claude-review.yml` after CI, and `scripts/pr_feedback.py` splitting the reply marker in two — `<!-- addressed: claude-review fixed=2 answered=1 -->`. It refuses `answered=` from the PR's own author on an unattended branch, so **a machine may fix a finding and never dismiss one**; it accepts `fixed=` from that same author, because the reviewer's next read of the diff is what checks a claimed fix and nothing checks a claimed disagreement. And **the account is now required**, which it was not: a fix used to clear the gate the moment a re-review reported `open=0`, leaving the entire record of what an agent did about three findings as a number going down. `unaccounted_rounds()` holds the merge until a reply says what changed, and `silently_resolved()` does the same for a review thread the author resolved without answering — two conventions that lived only in an agent prompt until the lane started merging itself.
- **No sweep can produce a `feature` or an `improvement` at all.** `cowork-scout.md`'s vocabulary is four words wide (`SCOUT_TYPES`, parsed back out of the agent file), the opportunity pass is gone from every charter, and `marketing` went with it. Capability work has one home: **the campaign lane**. Monday, `cron/integrations-campaign.md` shortlists three providers — one of `ticketing`, `docs`, `code` or `ops` (cloud and SaaS the agent can scan, against the charter's read-only/attributable/answers-a-question admission test) — and files them as `integration:candidate` issues that the digest re-lists every morning until answered. A ✅ applies **`integration:approved`**, never `claude-implement`, because `claude.yml` fires a 110-turn implement job on that label and a candidate describes a week of work. The rest of the week runs unattended: three PRs — client + cassette + credential, then wizard + `_verify_*` + settings section, then per-mode wiring — and it is done when the provider's row in `cowork/integrations-map.md` has no bare `—` left. `cowork/integration-campaign.md` is the procedure. The campaign is the only lane that edits outside its charter, through an **`Extends`** grant that is by *site* and by *operation* (append a provider, nothing else), declared reciprocally in both charters and asserted by a test, and that never includes `ui/mode_select/__init__.py`.
- **Which lane merged decides whether a merge releases.** `publish-beta.yml` ships `X.Y.ZrcN` to PyPI on every release-worthy merge (`pip install yeaboi` cannot see it; `pip install --pre yeaboi` can). What happens next depends on the PR behind the commit: an **unattended** one — `cowork`-labelled, or an unattended branch prefix — stops there and accumulates, while a **human's** merge also fires `publish.yml` and cuts the official `X.Y.Z` on the spot. `scripts/release_lane.py` is the only place that decides, and it decides by importing `pr_feedback.py`'s `COWORK_LABEL`/`UNATTENDED_BRANCH_PREFIXES` rather than re-spelling them — a prefix added there and not here would turn a fleet merge into an official release with nothing to notice. **It is a trigger distinction, not a contents one**: one branch, one version line, so an official release carries whatever the fleet merged below it; the lane decides *when* a release is cut, never what is in it, and `left_behind` reports the rest. An unreadable lookup counts as unattended, because an rc that should have been a release rides the next one and PyPI has no delete. Accumulated pre-releases still promote the old way — `release:promote` on the weekly ask issue, a human's ✅ in Slack carried by the relay, or `make beta-promote`, which emits the identical argv by importing it from `cowork_relay`. All the arithmetic — the rc number (a commit count since the last final tag), the batch manifest, the refusal to number a version that went backwards — lives in `scripts/release_channel.py`, and **an rc string is never committed**: it is stamped into a throwaway checkout in the publish job, because `bump_version.py` rejects anything that is not `X.Y.Z`.
- **A published pre-release is tagged; the official release is cut from that tag, not from `main`.** `beta/X.Y.ZrcN` is pushed *after* the upload returns, so the tag means the file exists and pins the tree that produced it — a namespace inert to `last_final_tag()`'s `v*` glob. Two things follow. `installable` (newest `beta/*` tag) is the only field allowed in a `pip install --pre` line; `latest_prerelease` is `next_prerelease(HEAD)`, a forecast that every docs merge inflates past anything on PyPI, and quoting it hands out a 404. And `publish.yml` checks out the signed-off commit — `<!-- tested: -->` from a comment, else `<!-- beta: -->` from the body, else `main`, never a hard failure — so the tested rc and the published final are the same tree; whatever `main` has beyond it is *reported*, not silently included, which the old version-granularity drift check could not do. **The hand-test is two sessions, because the fleet does two things.** `make beta-check` reports only, printing one shared baseline and then a **MAINTENANCE** section (from `SURFACES`, gated on the batch's changed paths) and an **INTEGRATION: `<provider>`** section (from `INTEGRATION_ANGLES`, listing *every* angle and marking the ones this batch did not reach — an angle that vanishes reads as one that was never needed). Which section work lands in is decided by **paths**, with the `integration(<provider>):` PR-title prefix as a corroborating second signal that is the only thing catching a reach angle; a commit *trailer* cannot work here, because git reads trailers from the last paragraph only and `auto-version.yml` guarantees every release-worthy PR is multi-commit. `make beta-sign-maintenance` / `make beta-sign-integration` each write a `<!-- tested: … track=… -->` marker, and the **last required one** writes the bare `<!-- tested: … -->` marker — the two families are shaped so neither `publish.yml`'s grep nor `TESTED_RE` can match a tracked one, which is what makes a half-signed batch unpromotable and why `publish.yml` needed no edit. **A track with nothing in it is never required**, because an empty checklist reads as "signed off" when it means "never asked". **The full ritual, and every way a skipped week is deduped, is `cowork/release-signoff.md`.**
- **Setup is derived, never retyped.** `make cowork-setup` creates the thirty-two GitHub labels (from `workstreams/` plus the `type:*` vocabulary) and the four repo variables (from `models.md`); `/cowork deploy` registers all twenty-three routines, the webhook triggers that fire the event-driven ones, and the Linear labels — which need a Claude session because a routine is account-scoped with no CLI behind it. `make cowork-check` is the doctor — it fails when the README table, a routine file and the tier table disagree, and when a `src/yeaboi/*.py` module is claimed by no charter (a scout reads only its charter's paths, so an unclaimed file is one no routine ever opens). Neither would be noticed at run time. Connectors, the GitHub App and `AUTO_VERSION_PAT` stay manual and are reported on every run.
- **The fleet has a lifecycle, and Python owns every comparison in it.** `/cowork status | deploy | run <name> | pause | resume | teardown`. The command makes the API calls and hands the `RemoteTrigger list` response to `scripts/cowork_setup.py --triggers`, which builds the request bodies, diffs the six compared fields, and edits the README URL column itself — the model posts what it is given and never diffs twenty-three routines by eye. Two asymmetries are deliberate: **there is no delete** (teardown sets `enabled: false` and prints the URLs; it must not claim otherwise), and **`enabled` is reported but never reconciled**, so `deploy` cannot silently undo a `pause`. **A merge to `main` deploys itself**: `cron/cd-deploy.md` holds `RemoteTrigger` (the only routine besides `slack-relay` that does), is fired by a GitHub push webhook with a daily cron behind it, and runs the same reconcile under `--strict --no-create`. It never guesses what fired it — the API cannot scope a webhook to a branch, so it resets to `origin/main`, **re-reads its own file from that tree** (the copy that got it that far came from whatever branch was pushed), and always reconciles; the plan is the diff, and an empty one exits silently. `--strict` exits 2 on a suspicious plan, an unresolved `needs`, or more than `MASS_CHANGE_LIMIT` routines created and updated together. **It applies updates only**: a create races two runs against each other with no lock and no undo, so `--no-create` empties those bodies in Python and reports them for `/cowork deploy`. Webhook triggers cannot be read back, deduped or deleted, so one is posted **only** for a routine the same run just created — never by `cd-deploy`. `environment_id` is likewise not compared — it is per-machine, and comparing it would flag every teammate's fleet as drifted. **The fleet outgrew one page, so it is read in parts.** `RemoteTrigger list` returns twenty routines and a `next_cursor` the tool cannot send back, and a truncated page read alone is the one input that registers a second copy of a routine already firing — so `snapshot()` raises on it. The rest is read one at a time: a `get` per `trig_…` id in the README URL column, which is the ledger of everything a deploy registered (`recorded_triggers`), passed as further `--triggers` files. `Snapshot` then carries what such a read cannot prove, and the split is the point: an **update** is safe from a partial read (it only touches a routine it saw, and applying it twice writes the same value), a **create** is not, so one is blocked unless the README records no id for it — nothing of ours can hide past the boundary under a name no deploy ever used. The same blindness had a second victim: the relay resolved `pause`/`resume`/`run` by matching a name against that list, so the manifest carries each routine's `trigger_id` now and no fleet verb lists anything.

## Front End (`frontend/` → `src/yeaboi/web/static/`)

Every browser-facing page — the retro and poker live boards, the share gate, the reporting slide
deck, and the static HTML exports — is built from TypeScript in `frontend/` with Vite, and the
**built output is committed** to `src/yeaboi/web/static/`. That is what lets `pip install yeaboi`
work with no Node and keeps `make test` pytest-only: the Python suite reads the committed bundles
and never builds anything.

- Edited anything under `frontend/`? Run **`make web`** and commit `src/yeaboi/web/static/` in the
  same commit. CI's `web` job rebuilds and fails if they disagree.
- Bundles must stay self-contained: no CDN, no external `<link>`, no `eval`/`new Function`, no
  dynamic `import()`, classic IIFE not ESM. Exports open over `file://` (where a `type="module"`
  script does not execute at all) and tunnel pages run under a strict CSP.
  `tests/unit/test_web_assets.py` enforces all of this statically — CSP breakage is invisible on
  localhost and on a LAN, and shows up only for the remote teammate. Two carve-outs, both narrow and
  both asserted rather than assumed: every document carries one `<link rel="icon">` whose href is a
  `data:` URI (use `assert_self_contained()` from `tests/_pages.py`, which allows exactly that one),
  and the footer credit is an `<a>` to the project site — a place to go, not something the page
  loads, exempted by blanking a *single* occurrence so a second one still fails.
- **Merge conflicts in the minified output**: never hand-resolve, and never configure a `union`
  merge driver (it produces silently corrupt JS). Always:

  ```bash
  git checkout --theirs -- src/yeaboi/web/static && make web && git add src/yeaboi/web/static
  ```

- Python reaches the bundles only through `src/yeaboi/web/assets.py` (`read_asset`, `json_island`,
  `render_page`). Never read from `static/` directly. Two sibling leaf modules own the other halves
  of that boundary, and a surface that re-implements either is the drift this layout exists to stop:
  **`web/brand.py`** is the only place that builds a masthead payload (`build_chrome`), spells the
  terminal-frame title (`frame_title`), maps a mode to its accent (`accent_mode` — including
  `roadmap → planning` and `anonymize → analysis`) or names the byline (`DEFAULT_FOOTER`); and
  **`web/security.py`** is the only place a served document's headers and CSPs come from
  (`send_document`, `DOCUMENT_HEADERS`, `BOARD_CSP`/`GATE_CSP`/`ARTIFACT_CSP`/`EDIT_CSP`). No
  request handler writes its own headers.
- **Exports are inert unless a server is behind them.** `ARTIFACT_CSP` sets `connect-src 'none'`, so
  a written file or a finished share physically cannot make a request. Two shares talk back, and
  both are served under the *same* `EDIT_CSP` — identical to `ARTIFACT_CSP` but for
  `connect-src 'self'`, pinned by a test that diffs the two policies whole: an **editable** artifact
  (`OutputShareServer(editable=…)`), and a **correctable** standup, whose reader answers a practice
  signal (`ShareDocument.corrections`, set only when the TUI passes `session_id`+`run_id`). One
  policy rather than one each, because they differ in what they send and not at all in what they may
  reach. **The correctable half currently has no host**: both standup share paths went editable,
  and one document cannot have two writers — an editable share replays its own edit log, a practice
  vote rewrites the run beneath it. The path, its route and its tests stay because carrying a
  verdict *through* the edit log (a third op beside `OP_NOTE`/`OP_FIELD`) is what would let both
  live on one document; signals are answered from the TUI's Practices action until then. `export/actions.ts` (edits) and `export/vote.ts` (verdicts) are the only network code in
  the export bundle; gate any new control on the payload's capability flag — `edit`, `correctable` —
  or written exports render a button that does nothing. Post via `mutate('/api/…', {…})` with a
  literal path and body, and read via `payload.get("…")`, so `test_web_request_keys.py` keeps seeing
  the route.
- Server-validated tuples (grids, statuses, emojis, avatars, deck values) come from
  `frontend/src/types/enums.ts`, generated by `scripts/gen_web_types.py` with a `--check` in CI.
  **Never also ship them in a boot payload** — the island would win at runtime, so a stale bundle
  would render a board that disagrees with what the server accepts. Payloads carry only what a
  codegen cannot pin.
- **Every browser surface is React now** — both live boards, the reporting slide deck, the share
  gate, and all ten static exports. Their Python files are the shell plus a boot island, and no
  Python generates markup or a stylesheet any more: `html_theme` kept `escape`, `safe_url`, the
  trend-series normalisation, image embedding and `export_page`, and lost `EXPORT_CSS`,
  `html_page` and the dozen markup primitives. An exporter builds a payload of text and numbers;
  a component draws it.
- **Payload rules.** No markup crosses the wire, and no presentation either — the payload sends
  the word (`"high"`, `"done"`) or the number, never the colour. The one documented exception is
  the team profile's `Cell.tone`, because its thresholds are per-column *and* directional (80%
  completion is good, 80% spillover is not); it is still a word, and `Profile.tsx` gates it
  against `TONES` before it reaches a `var(--…)`.
- `make dev-board` / `dev-poker` / `dev-deck` run seeded surfaces against the real Python side;
  restart them after `make web`, since `read_asset` is cached.
- **Two guards sit on the Python/TS boundary**, one per direction. `test_web_wire_shapes.py` drives
  real boards through a real round, writes the snapshots to `frontend/src/test/fixtures/`, and
  `wire.ts` asserts each one `satisfies` its interface in `types/board.ts` — so a dropped response
  field fails `npm run typecheck`. `test_web_request_keys.py` parses the request bodies out of each
  `actions.ts` and requires every key to be one the handler reads — that direction fails *silently*
  (`payload.get(key, default)` just returns the default), which is how a 60-second duel turn
  became 90 with nothing reported. The deck's payload rides the same response-direction guard —
  an export is a file, so a dropped field surfaces months later as a blank slide with no server
  and no log to look at.

## REQUIRED: Go sidecar dual maintenance

Two Python surfaces are mirrored line-for-line in the Go sidecar (`go/`), with byte-level
parity enforced by `tests/parity/` (`make parity`, and the `parity` CI job):

- the **agentwatch** family (`src/yeaboi/agentwatch/{collector,store,engine,security_checks}.py`
  ↔ `go/internal/agentwatch/`),
- the **standup deterministic core** (`src/yeaboi/standup/{aggregate,references,relatedness,
  habits,automation,insights,confidence,categories}.py` + the engine's evidence helpers
  ↔ `go/internal/standup/`), and
- the **team-analysis scoring core** (`src/yeaboi/analysis/{aggregate,code_health,coverage,
  practices}.py` + `ai_usage.py`'s classifier block — the marker tables,
  `_classify_ai_*`, `aggregate_ai_markers`, `_activity_bucket`, `_collect_samples`
  ↔ `go/internal/analysis/`).

Python is the reference implementation. Any behavior change in those files MUST be mirrored
in the Go twin (each Go file names its Python twin in its header) — otherwise `make parity`
fails and the change cannot merge. Purely additive Python work that the sidecar does not
serve (new prose, new store columns, rendering) is exempt; when in doubt, run `make parity`.

**One constant outside those files couples the two languages**: `sessions.py`'s
`CURRENT_SCHEMA_VERSION`, mirrored by `currentSchemaVersion` in
`go/internal/agentwatch/store.go`. Go refuses a database newer than it understands rather
than writing behind Python's migrations, so bumping the schema without raising the Go
ceiling makes the sidecar refuse every upgraded database — the agentwatch family silently
reverts to the Python path with CI fully green. `tests/unit/test_gocore_packaging.py`
fails on the drift; raise the Go constant once the new migration is mirrored (or leave it
deliberately, and say why, when the sidecar must not write behind it).

## Code Style

- Python 3.11+, ruff for linting/formatting (line-length 120)
- Imports sorted by ruff (isort rules: stdlib, third-party, local)
- Tests in `tests/`, source in `src/yeaboi/`

## REQUIRED: Learning-First Development

This is the developer's first AI agent. These are NOT optional — follow them on every implementation task.

1. **ALWAYS add `# See docs: <section name>` comments** when introducing a LangGraph or LangChain concept for the first time in a file. Cross-reference the relevant page at https://yeaboi.ai/docs/ (or the local `docs/docs/` source) so the developer can look up the theory.
2. **ALWAYS explain LangGraph/LangChain concepts in code comments** on first use — what a reducer does, why `add_messages` exists, what `StateGraph` expects, what `bind_tools` does, etc. Do NOT assume familiarity with these frameworks.
3. **ALWAYS explain architectural decisions** in your response — when choosing between approaches, state the trade-offs and why this approach was chosen.

Key docs sections to reference:
- "Architecture" (`architecture.html`) — four layers, three design principles, agent graph, TUI system
- "The ReAct Loop" (`architecture.html`) — Thought → Action → Observation pattern
- "Agentic Blueprint Reference" (`architecture.html`) — core graph setup, two core nodes, wiring, tools, memory, streaming
- "Prompt Construction" (`architecture.html`) — ARC framework, few-shot, chain-of-thought, flipped prompt
- "Session Management" (`session-management.html`) — SQLite persistence, --resume, session IDs
- "Guardrails" (`architecture.html`) — input guardrails (4 layers), output guardrails (4 layers), human-in-the-loop
- "Tools" (`tools.html`) — 35 tools, tool types, risk levels
- "Scrum Standards" (`scrum-standards.html`) — story format, acceptance criteria, story points, DoD, discipline tagging

## REQUIRED: Verification

After every code change, ALWAYS run:
1. `make test` — all tests must pass
2. `make lint` — must be clean

Do NOT commit until both pass.

## REQUIRED: Observability & Test Coverage

Every new feature MUST include all three pillars before it can be considered complete:

1. **Logging** — every user action gets `logger.info()` (entry, exit, key decisions); every LLM call logs via `_llm_invoke()`/`track_usage()`; every external API call logs start + result; every error path logs at `warning`/`error` with context. Handler setup, log directories, and the never-log-per-frame rule live in the `logging` skill — Read it when adding logging.
2. **Log directory** — all paths come from `src/yeaboi/paths.py`; never hardcode `Path.home() / ".yeaboi"`. Each mode logs to its own directory under `~/.yeaboi/logs/` (see the `logging` skill).
3. **Tests** — every new function gets at least one unit test (happy path + error case); every `_build_*_screen` gets render tests; every LLM-dependent function gets mock tests (success, error fallback, code fences); every new state field gets serialization round-trip tests; secret/sensitive rendering must be tested for masking. Tests live in `tests/unit/` — one file per source module.

## REQUIRED: Surface Parity

yeaboi ships on **five surfaces**: the TUI, CLI flags/subcommands, the Python engines, the MCP server, and the Claude Code plugin skills. Features MUST NOT land TUI-only. This is machine-enforced by `tests/unit/test_surface_parity.py` — a declarative capability registry plus discovery checks over engines, MCP tools, `_MODE_CARDS`, `build_parser()`, and plugin skills.

The contract:

1. **New mode / feature → engine first.** Implement the pipeline as a headless engine (`src/yeaboi/<mode>/engine.py`, parse → fallback → format, frozen-dataclass artifacts). The TUI, CLI, and MCP are thin adapters over it.
2. **Propagate to every surface** (or record a reasoned exemption): an MCP tool in `src/yeaboi/mcp/tools_*.py`, a CLI flag/subcommand in `cli.py`, a TUI card + handler, and — for user-facing workflows — a plugin skill in `claude-plugin/yeaboi/skills/`.
3. **Register it.** Add/extend the capability row in `CAPABILITIES` (and `PARAM_PAIRS` for engine-backed MCP tools) in `tests/unit/test_surface_parity.py`. Until you do, `make test` fails with a message naming the exact edit.
   - **Also add a discoverability tip.** Every capability needs a `FeatureTip` in `src/yeaboi/ui/shared/_tips.py` (`_FEATURE_TIPS`), keyed by the capability name — with a `mode_key` when it owns a `_MODE_CARDS` card so the welcome-screen jump-into-feature key (`g`) lands on it. `TestTips` enforces this two-way; opt out with a `TIP_EXEMPT` entry (reason required). Flag a just-shipped feature with `is_new=True` and clear it a release or two later.
4. **New engine params must reach the MCP tool.** The param-parity check compares the engine signature against the tool schema; expose the new param or add it to `HIDDEN_PARAMS` with a reason. `db_path`/`today`/`on_progress`/`dry_run` are injection seams, always hidden.
5. **Deliberate absences use `Exempt("reason")`** — e.g. the retro live board is TUI-only by design. Exemptions are visible, reviewed gaps, not silent ones.
6. **Removals count too.** Every check is two-way set equality: deleting a tool/card/skill without updating the registry also fails.

The MCP server internals and the module map (including `mcp/`, `roadmap/`, `analysis/`, `agent/headless.py`) are in the `project-map` skill; per-mode blueprints (including Roadmap Intake) are in `mode-blueprints`.

## Project Structure (top level)

```
src/yeaboi/
  cli.py / config.py / paths.py      — entry point, env/config, all filesystem paths
  sessions.py / persistence.py       — SQLite session store, state serialization, schema versioning
  agent/                             — ScrumState, graph wiring, node functions, LLM factory, headless.py
  prompts/                           — one factory function per prompt (ARC framework)
  tools/                             — @tool-decorated integrations (GitHub, Jira, AzDO, Confluence, Notion, …)
  standup/ retro/ poker/ performance/ reporting/ roadmap/ analysis/  — standalone modes (shared blueprint)
  agentwatch/                        — the Agents family: usage/standup/security engines over local agent-session telemetry
  pricing.py                         — the per-model LLM rate table (cache-aware); every cost estimate goes through it
  mcp/                               — stdio MCP server (yeaboi-mcp; 47 tools over the engines)
  repl/                              — legacy REPL for CLI-flag-driven flows
  ui/                                — full-screen TUI (mode_select, provider_select, session, shared)
  input_guardrails.py / output_guardrails.py / formatters.py / *_exporter.py / *_sync.py
tests/
  unit/ (one file per module; nodes/ split by node)  integration/  contract/  smoke/  golden/  fixtures/
```

Conventions: agent logic in `agent/`, prompts separate in `prompts/`, tools separate in `tools/`; re-export public APIs from `__init__.py`; `_`-prefixed files inside `repl/`/`ui/` subpackages are internal. The full annotated module map is in the `project-map` skill.

## Testing (essentials)

- One test file per source module; group related tests in classes; `monkeypatch` away filesystem/network/delays
- Test happy path + edge cases; node tests live in `tests/unit/nodes/`
- **Never modify `tests/integration/test_repl.py`** (uniquely coupled — monkeypatches 10+ names)
- Pytest markers: `slow`, `eval`, `vcr`, `smoke`
- Full testing conventions (fixtures, helpers, the pty TUI smoke test) are in the `agent-and-state` skill

## Detailed Conventions (lazy-loaded skills)

Deep reference lives in `.claude/skills/` and loads on demand in interactive sessions. In CI/headless contexts, Read the SKILL.md for any area your change touches:

| Skill | Load when touching… |
|---|---|
| `tui-standards` | `ui/`, any `_build_*_screen`, themes, shared components |
| `agent-and-state` | `agent/`, `prompts/`, `tools/`, state fields, `sessions.py`, tests |
| `mode-blueprints` | `standup/`, `retro/`, `performance/`, `reporting/`, `roadmap/`, or adding a new mode |
| `logging` | logging calls, log files, `logging_setup.py` |
| `ci-and-release` | `.github/workflows`, versioning, releasing, Dependabot, deployment |
| `project-map` | full module map, CLI flags/subcommands, env vars, app flow, the MCP server + plugin |

## Git Conventions

- **Commit messages**: lowercase imperative (e.g. "add streaming output", "fix import sorting")
- **Branch naming**: `feature/<description>` for feature work
- **PRs**: feature branches merge to `main` via pull request
- Include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` on AI-assisted commits
