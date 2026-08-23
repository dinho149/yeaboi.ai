# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Terminal-based AI Scrum Master agent built with LangGraph, LangChain, and Anthropic Claude (with OpenAI, Google, AWS Bedrock, and local Ollama as alternative providers). Two audiences behind one landing split: **Humans** (decomposes projects into epics, user stories, tasks, and sprint plans; standups, retros, poker, performance, reporting) and **Agents** (the `agentwatch` family — cost, daily digests, and security posture of the AI coding agents working across the SDLC, computed locally from Claude Code session logs).

## Commands

```bash
make ship-gate            # The full local gate /ship runs: lint + format-check + test + security + preflight
make test                 # Unit + integration + contract tests (parallel unit lane, then the serial slow lane)
make test-fast            # The whole unit lane, in parallel (~50s)
make test-scoped          # Only the areas the working tree touches + the always-run guards
make test-slow            # Integration + contract only — what CI's second test job runs
make test-v               # Full suite verbose
make test-all             # Everything including golden evaluators
make lint                 # Lint with ruff
make format               # Format with ruff (writes)
make format-check         # What CI's required "Format check (ruff)" job runs (asserts)
make preflight            # Run only the optional CI jobs this branch's diff needs (BASE=origin/main)
make package-check        # uv build + assert the wheel and sdist carry the committed bundles
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
onto areas (one per cowork workstream, so the fleet and CI share a vocabulary) and `ci.yml`'s `scope`
job feeds the result to every other job. Three rules make it safe: `ALWAYS` runs the ~30 guards that
scan the repo rather than importing a module; `GLOBAL` forces the whole suite for anything reached by
everything (`conftest.py`, `sessions.py`, `ui/shared/`, `pyproject.toml`); and **any path the registry
does not recognise runs everything**. The five required status checks never carry an `if:` — scoping
changes what they run, never whether they report. See `tests/unit/test_test_scope.py`, which fails the
build when a source file is claimed by no area or a test file is selected by nothing.

Terminal GIFs for the README: `make demo` re-records `docs/demo.cast.gz` + `docs/demo.gif` from a scripted pty session (deterministic, no interaction; needs `agg`); `make demo-render` re-renders the GIF from the committed cast for theme/size tweaks.

## Parallel Development (worktrees)

Each feature gets its own git worktree under `<main checkout>/.claude/worktrees/<name>` with its own branch, `.env`, uv venv, and pre-commit hooks. Never develop two features in one checkout. A new worktree is cut from latest `origin/main`; an existing local branch is left untouched, and a branch that exists only on `origin` is checked out tracking the remote — rebase it with `/sync-main`.

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
- **At ship time (`/ship`)**: the branch is committed and **rebased onto `origin/main` first** — a gate run on a stale base proves something about a tree that will never exist — and then an independent fresh-context agent reviews `git diff origin/main...HEAD` (spec-fit + conventions) **concurrently** with `make ship-gate` (`lint` → `format-check` → `test` → `security` → `preflight`). `.claude/commands/ship.md` has the procedure; `tests/unit/test_ship_gate.py` guards its shape.
- **In CI**: `claude-review.yml` posts an async code + security review once the full CI suite has passed on a PR (non-blocking; `ci.yml` remains the merge gate).

### Orchestration conventions

When driving multiple features at once, work as an **orchestrator**: one main session, one background agent per feature, each in its own worktree (`make wt-headless`). The orchestrator kicks off agents, tracks them, reviews **final diffs** (not intermediate steps), and runs `/ship` per feature when green. Use `make test-fast` in the inner loop; the full `make test` runs at ship time.

## Cowork (`cowork/`)

**The fleet does exactly three things: it maintains what exists, it builds one provider integration at a time, and it rewrites the codebase in Go one wave at a time — and one workstream watches it do all of it.** Seventeen standing workstreams, each with a charter in `cowork/workstreams/` naming the paths it owns. Fourteen of them are *maintenance* (one per mode, plus the cross-cutting tui-ux, web-ux, platform and security) and may only return `bug`, `chore`, `docs` or `security`; `integrations` and `go-migration` are the two that build. Routines are **account-scoped, not repo files** — `cowork/` is the versioned source of truth their prompts point at, and `make cowork-check` is the doctor that fails when the two disagree.

`cowork/definition-of-done.md` is the one contract, binding on routines *and* on `/ship`: Linear ticket, tests, lint, security, surface parity, observability, web bundles, Notion page, Slack post, review feedback.

| Read | For |
|---|---|
| `cowork/README.md` | **Start here** — the loop, the routine table, setup, and the deploy lifecycle |
| `cowork/house-rules.md` | The auto lane, the campaign lane, the migration lane, the proposal cap, the queue |
| `cowork/sweep-procedure.md` | What a sweep does, step by step |
| `cowork/models.md` | The only file that names a model — everything else names a tier |
| `cowork/hygiene-lenses.md` | The standing detectors a routine runs before scouting |
| `cowork/release-signoff.md` | The release batch: assemble, hand-test, merge |
| `cowork/migration/program.md` | The Go rewrite program, wave by wave |

`/cowork status | today | runs | deploy | run <name> | pause | resume | teardown` drives the fleet. Editing anything under `cowork/` is the `fleet` workstream's subject — read `cowork/workstreams/fleet.md` first, and note that the constitution (`house-rules.md`, `definition-of-done.md`, `sweep-procedure.md`, `models.md`, `crew.md`, and the three crew agents in `.claude/agents/`) sits outside every charter.

## Front End (`frontend/` → `src/yeaboi/web/static/`)

Every browser-facing page — the retro and poker live boards, the share gate, the reporting slide deck, and the ten static HTML exports — is built from TypeScript in `frontend/` with Vite, and the **built output is committed** to `src/yeaboi/web/static/`. That is what lets `pip install yeaboi` work with no Node and keeps `make test` pytest-only: the Python suite reads the committed bundles and never builds anything.

- **Edited anything under `frontend/`? Run `make web` and commit `src/yeaboi/web/static/` in the same commit.** CI's `web` job rebuilds and fails if they disagree. Never hand-resolve a merge conflict in the minified output (and never configure a `union` merge driver — it produces silently corrupt JS): `git checkout --theirs -- src/yeaboi/web/static && make web && git add src/yeaboi/web/static`.
- Bundles must stay **self-contained**: no CDN, no external `<link>`, no `eval`/`new Function`, no dynamic `import()`, classic IIFE not ESM — exports open over `file://` (where a `type="module"` script does not execute at all) and tunnel pages run under a strict CSP. `tests/unit/test_web_assets.py` enforces this statically, because CSP breakage is invisible on localhost and on a LAN and shows up only for the remote teammate.
- Python reaches the bundles only through `web/assets.py`; a served document's headers and CSPs come only from `web/security.py`; the masthead, frame title and accents come only from `web/brand.py`. No request handler writes its own headers, and no Python generates markup — every surface is React, and a payload carries text and numbers, never markup and never presentation (one documented exception, in the skill).

Everything else — the CSPs and what makes an export inert, the export capability flags, the `enums.ts` codegen rule, the payload rules, and the two Python/TS wire guards — is in the **`web-frontend`** skill. Read it before touching `frontend/`, `src/yeaboi/web/`, or any exporter.

## REQUIRED: Go sidecar dual maintenance

Three Python surfaces are mirrored line-for-line in the Go sidecar (`go/`), with byte-level parity enforced by `tests/parity/` (`make parity`, and the `parity` CI job). Each Go file names its Python twin in its header.

| Family | Python (the reference) | Go twin |
|---|---|---|
| agentwatch | `agentwatch/{collector,store,engine,security_checks}.py` | `go/internal/agentwatch/` |
| standup core | `standup/{aggregate,references,relatedness,habits,automation,insights,confidence,categories}.py` + the engine's evidence helpers | `go/internal/standup/` |
| analysis core | `analysis/{aggregate,code_health,coverage,practices}.py` + `ai_usage.py`'s classifier block (the marker tables, `_classify_ai_*`, `aggregate_ai_markers`, `_activity_bucket`, `_collect_samples`) + `doc_quality.py`'s scoring pieces (`_AI_DISCLOSURE_CONTEXT`, `_MIN_DOC_SAMPLE`/`doc_small_sample`, `_has_ai_disclosure`, `_CLEAR_MIN`/`_UNCLEAR_MAX`, `_count_syllables`, `_clarity_metrics`, `_usefulness_metrics`, `_analyse_page_asset`, `_aggregate_doc_assets`, `_doc_findings`, `_prioritize_doc_actions`, `_fallback_doc_quality_insights` — the read/cache plumbing around them is NOT mirrored) + `tools/team_learning.py`'s `_insight_item`/`_INSIGHT_MAX_ITEMS` | `go/internal/analysis/` |

Python is the reference implementation. **Any behaviour change in those files MUST be mirrored in the Go twin** — otherwise `make parity` fails and the change cannot merge. Purely additive Python work that the sidecar does not serve (new prose, new store columns, rendering) is exempt; when in doubt, run `make parity`.

**One constant outside those files couples the two languages**: `sessions.py`'s `CURRENT_SCHEMA_VERSION`, mirrored by `currentSchemaVersion` in `go/internal/agentwatch/store.go`. Go refuses a database newer than it understands rather than writing behind Python's migrations, so bumping the schema without raising the Go ceiling makes the sidecar refuse every upgraded database — the agentwatch family silently reverts to the Python path with CI fully green. `tests/unit/test_gocore_packaging.py` fails on the drift; raise the Go constant once the new migration is mirrored (or leave it deliberately, and say why, when the sidecar must not write behind it).

## Code Style

- Python 3.11+, ruff for linting/formatting (line-length 120)
- Imports sorted by ruff (isort rules: stdlib, third-party, local)
- Tests in `tests/`, source in `src/yeaboi/`

### Comments

**Legible code first.** Before writing a comment, try to make it unnecessary: a
clearer name, a smaller function, an extracted constant, an earlier return. A
comment is what is left over when the code genuinely cannot say it itself.

When one is needed:

- **Short and concise** — a line or two, not a paragraph and not a section.
- **Say what the code does**, or what a caller must know to use it safely.
- **No war notes.** No history of the bug that was fixed, no record of what was
  tried first, no reasoning about alternatives considered, no narration of the
  problem being solved. That goes in the commit message or the PR.

Much of the existing tree predates this and reads the other way. Match the rule,
not the neighbours; trim a long comment when you are editing the code under it,
but do not sweep files you are not otherwise touching.

## REQUIRED: Learning-First Development

This is the developer's first AI agent. These are NOT optional — follow them on every implementation task.

1. **ALWAYS add `# See docs: <section name>` comments** when introducing a LangGraph or LangChain concept for the first time in a file. Cross-reference the relevant page at https://yeaboi.ai/docs/ (or the local `docs/docs/` source) so the developer can look up the theory.
2. **ALWAYS explain LangGraph/LangChain concepts in code comments** on first use — what a reducer does, why `add_messages` exists, what `StateGraph` expects, what `bind_tools` does, etc. Do NOT assume familiarity with these frameworks. This is the one carve-out from the comment rule above: a sentence naming the concept plus the `# See docs:` pointer, not a tutorial in the source.
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

At ship time run `make ship-gate` instead: same two, plus `format-check` (a *required* CI check with
no other local twin), `security`, and the `preflight` jobs this diff needs.

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
  provenance/                        — tamper-evident decision chain + conflicts vocabulary (recorded by standup/performance)
  ship/                              — supervised story → PR pipeline: budget fuse, worktree isolation, agent driver, approval gate
  ceremonies/                        — the clock any mode runs on: OS-job installer, guards, delivery channels
  slack/                             — the inbound half of that clock: anchors, a closed grammar, the poller, the ledger
  pricing.py                         — the per-model LLM rate table (cache-aware); every cost estimate goes through it
  mcp/                               — stdio MCP server (yeaboi-mcp; 57 tools over the engines)
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
| `web-frontend` | `frontend/`, `src/yeaboi/web/`, any exporter, a share or live-board surface |
| `logging` | logging calls, log files, `logging_setup.py` |
| `ci-and-release` | `.github/workflows`, versioning, releasing, Dependabot, deployment |
| `project-map` | full module map, CLI flags/subcommands, env vars, app flow, the MCP server + plugin |

## Git Conventions

- **Commit messages**: lowercase imperative (e.g. "add streaming output", "fix import sorting")
- **Branch naming**: `feature/<description>` for feature work
- **PRs**: feature branches merge to `main` via pull request
- Include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` on AI-assisted commits
