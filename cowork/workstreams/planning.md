# planning

The product's reason to exist — mode card `project-planning`, and the only capability whose engine
lives outside a `<mode>/engine.py` (`agent/headless.py:run_planning_pipeline`).

**Owns** — `src/yeaboi/agent/` (graph, `nodes.py` at 8.7k LOC, `state.py`, `llm.py`, `headless.py`,
`repo_signals.py`, `ceremony_history.py`), `src/yeaboi/prompts/`, `src/yeaboi/ui/session/`
(intake / review / editor screens), `sessions.py`, `persistence.py`, `questionnaire_io.py`,
`transcript.py` (the planning chat transcript exporter),
`json_exporter.py`, `prd_exporter.py` (the Product Requirements Document builder),
`ollama_control.py` (a provider's lifecycle, like `llm.py`), `mcp/tools_planning.py`,
`mcp/tools_sessions.py` (its `usage_get` is a read-only view over **tui-ux**'s page),
`src/yeaboi/ship/` (the plan's back half: a story driven through a supervised coding agent to a
PR — budget fuse, worktree isolation, driver, approval gate), `mcp/tools_ship.py`,
`ui/mode_select/_ship.py`, `claude-plugin/yeaboi/skills/plan-sprint/`,
`claude-plugin/yeaboi/skills/ship/`, `tests/unit/nodes/`, `tests/unit/prompts/`,
`tests/integration/`, `tests/golden/`

**Skills** — `.claude/skills/agent-and-state/SKILL.md`

**Cadence** — Mon 07:00 UTC, weekly

## Standing concerns

- **State-field discipline.** Every new field on `ScrumState` needs a frozen-dataclass default, a
  serialization round-trip test, and a schema version bump. This is the single most common source of
  silent session-restore corruption.
- **The node contract** — parse → fallback → format. A node that raises instead of falling back
  takes the whole graph down for one bad LLM response.
- **Prompt budget.** `make budget-report` tracks prompt token counts. A prompt that grows ~20% in one
  change is a finding even if it tests green.
- **Golden evaluators** (`make eval`) are the only check on output *quality*. They should grow when a
  prompt changes materially — a prompt edit with no eval change is a finding.
- **Provider parity** — Anthropic, OpenAI, Google, Bedrock, Ollama. A code path assuming
  Anthropic-only response shapes is a finding.
- **Four intake paths, one pipeline** — Small, Large, Roadmap, Offline (`_INTAKE_CARDS`). A change to
  intake must be checked against all four; Offline in particular round-trips through a markdown
  questionnaire and is easy to break silently.
- **The learning-first rule from `CLAUDE.md` bites hardest here**: a new LangGraph/LangChain concept
  needs a `# See docs: <section>` comment and an inline explanation on first use in a file.

## Auto lane, in practice

Broken or flaky node tests, dead graph edges, a missing round-trip test for an *existing* field,
`# See docs:` comments missing on already-shipped concepts. Prompts, state schema, and graph shape
always propose — they change what the agent says.

## Out of scope

**`tests/integration/test_repl.py` — never modify it.** It monkeypatches 10+ names and is uniquely
coupled. Roadmap intake logic is **roadmap**'s. The rest of `ui/` is **tui-ux**'s; you own
`ui/session/` only.
