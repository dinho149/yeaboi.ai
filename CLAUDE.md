# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Terminal-based AI Scrum Master agent built with LangGraph, LangChain, and Anthropic Claude (with OpenAI, Google, AWS Bedrock, and local Ollama as alternative providers). Decomposes projects into epics, user stories, tasks, and sprint plans. Deployed on AWS Lightsail via OpenClaw with Bedrock.

## Commands

```bash
make test                 # Unit + integration + contract tests (full suite, no API keys needed)
make test-fast            # Unit tests only (< 3s, tight edit-test loop)
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
make graph                # Generate agent graph visualisation PNG
make build                # Build sdist + wheel into dist/
make publish              # Publish to PyPI
make record               # Re-record VCR cassettes against real APIs
make clean                # Remove build artifacts and caches
```

Run a single test: `uv run pytest tests/unit/test_state.py -v`
Run a single test class: `uv run pytest tests/unit/test_state.py::TestPriority -v`

Terminal GIFs for the README: `asciinema rec docs/demo.cast -c "yeaboi --dry-run"` → `agg docs/demo.cast docs/demo.gif --theme github-dark` (install via `brew install asciinema agg`).

## Parallel Development (worktrees)

Each feature gets its own git worktree under `<main checkout>/.claude/worktrees/<name>` with its own branch, `.env`, uv venv, and pre-commit hooks. Never develop two features in one checkout.

```bash
make wt-new NAME=my-feature       # create worktree + open VS Code with claude auto-running
make wt-headless NAME=my-feature  # create worktree WITHOUT VS Code (for background-agent work)
make wt-list                      # list worktrees (branch, clean/dirty, path)
make wt-rm NAME=my-feature        # remove worktree dir + branch
```

Slash commands (in `.claude/commands/`): `/wt` (worktree ops from inside a session), `/sync-main` (rebase on latest main + re-verify), `/ship` (independent review → full tests → commit → push → PR), `/babysit-prs` (survey open PRs, spawn fix agents for red CI), `/migrate` (fan out a mechanical migration across many files via parallel worktree agents).

### Verification loop

- **Every turn (automatic)**: a Stop hook runs `make lint` + `make test-fast` whenever a turn ends with dirty `.py` files, and a PostToolUse hook ruff-formats every edited `.py` file. Hook scripts live in `scripts/claude-hooks/`; wiring is in `.claude/settings.json`.
- **At ship time (`/ship`)**: an independent fresh-context agent reviews the diff against the task (spec-fit + conventions), then the full `make test` + `make lint` gate runs before commit/push/PR.
- **In CI**: `claude-review.yml` posts an async code + security review once the full CI suite has passed on a PR (non-blocking; `ci.yml` remains the merge gate).

### Orchestration conventions

When driving multiple features at once, work as an **orchestrator**: one main session, one background agent per feature, each in its own worktree (`make wt-headless`). The orchestrator kicks off agents, tracks them, reviews **final diffs** (not intermediate steps), and runs `/ship` per feature when green. Use `make test-fast` in the inner loop; the full `make test` runs at ship time.

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

yeaboi ships on **six surfaces**: the TUI, CLI flags/subcommands, the Python engines, the MCP server, the Claude Code plugin skills, and the OpenClaw skill. Features MUST NOT land TUI-only. This is machine-enforced by `tests/unit/test_surface_parity.py` — a declarative capability registry plus discovery checks over engines, MCP tools, `_MODE_CARDS`, `build_parser()`, and plugin skills.

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
  standup/ retro/ performance/ reporting/ roadmap/ analysis/  — standalone modes (shared blueprint)
  mcp/                               — stdio MCP server (yeaboi-mcp; 25 tools over the engines)
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
| `project-map` | full module map, CLI flags/subcommands, env vars, app flow, the MCP server + plugin, OpenClaw product skill |

Note: `.claude/skills/` holds **dev-workflow** conventions; `src/yeaboi/skills/` (symlinked as `skills/`) is the **shipped OpenClaw product skill** — don't confuse them.

## Git Conventions

- **Commit messages**: lowercase imperative (e.g. "add streaming output", "fix import sorting")
- **Branch naming**: `feature/<description>` for feature work
- **PRs**: feature branches merge to `main` via pull request
- Include `Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>` on AI-assisted commits
