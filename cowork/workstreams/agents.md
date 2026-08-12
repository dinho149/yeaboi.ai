# agents

**Owns** — `src/yeaboi/agentwatch/` (engine, store, collector, security_checks, render, export),
`src/yeaboi/pricing.py`, `mcp/tools_agentwatch.py`, `prompts/agentwatch.py`,
`ui/mode_select/_agents.py`, `ui/mode_select/screens/_screens_agents.py`,
`ui/mode_select/screens/_screens_category.py`, `claude-plugin/yeaboi/skills/agents-usage/`,
`claude-plugin/yeaboi/skills/agents-standup/`, `claude-plugin/yeaboi/skills/agents-security/`,
`tests/unit/test_agentwatch_*.py`, `tests/unit/test_pricing.py`, `tests/unit/test_category_screen.py`

**Skills** — `.claude/skills/mode-blueprints/SKILL.md`

**Cadence** — daily digest (weekdays 06:15 UTC); no sweep yet — the family is new, and a sweep over
a week-old surface invents findings (see the cadence rule in the README).

## Standing concerns

- **The privacy invariant is the product.** No transcript text ever reaches the store, an export, or
  a rendered screen — session rows are aggregates, security findings are (pattern, file, line). The
  planted-secret tests enforce it; any new column, export field, or log line that could carry
  transcript content is the highest-value finding in this workstream.
- **Numbers are deterministic, prose is LLM.** Every figure on a dashboard is computed in the
  engine; the LLM writes insights/narrative/summary *about* finished aggregates. A path where model
  output can change a number is a bug, not a style choice.
- **requestId dedup.** Claude Code splits one API response across JSONL lines with identical usage;
  the collector counts once per requestId via full-file reparse. Any "optimisation" to partial
  offsets reintroduces double-counting.
- **Pricing honesty.** `pricing.py` is a dated snapshot (`PRICING_AS_OF` travels on artifacts);
  unknown models price at the fallback tier and are flagged. Rates changed upstream = update the
  table AND the date, never silently.
- **Read-only on other tools' state.** The fs_policy rules for `~/.claude` are RO; agentwatch must
  never write into another tool's directory.
- **Detection is a lower bound.** Standup/security wording must never present absence of evidence as
  idleness or safety ("indicator, not an audit").

## Auto lane, in practice

Broken tests, stale pricing-table comments, docs drift. New detectors, new pricing rows, threshold
changes, and anything that alters a rendered number always propose.

## Out of scope

Repo-side agent-identity detection lives in `analysis/ai_usage.py` (**analysis** owns it; agents
consumes it). The human standup's collectors and delivery (**standup**). The export bundle's markup
(**web-ux**).
