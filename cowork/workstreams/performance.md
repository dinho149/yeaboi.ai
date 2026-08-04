# performance

**BETA.** Mode card `performance` carries `badge: BETA_LABEL`, and every MCP tool is beta-gated
through `_with_beta` in `mcp/tools_performance.py`.

**Owns** — `src/yeaboi/performance/` (9 files, 1.9k LOC: engine, store, delivery),
`mcp/tools_performance.py`, `claude-plugin/yeaboi/skills/performance/`, `src/yeaboi/beta.py`,
`tests/unit/test_performance_*.py`

**Skills** — `.claude/skills/mode-blueprints/SKILL.md`

**Cadence** — 10th and 24th of the month, 07:30 UTC

## Standing concerns

- **This mode writes about people.** 1:1 prep, 1:1 summaries, and 6-month reviews are drawn from real
  delivery data about named engineers. Anything that makes an inference harsher, more certain, or
  less attributable to evidence is a proposal with that framing stated plainly — never auto lane.
- **Small samples are the norm here**, not the exception. One engineer over one sprint is a handful
  of data points. Every metric needs a path that declines to conclude rather than one that renders a
  confident judgement.
- **Beta labelling rules** — `src/yeaboi/beta.py` stays import-free; beta keeps `available: True`;
  the caveat never goes in an engine warning, because `--strict` would then fail on it. A beta notice
  that leaked into a warning is a real finding.
- **Notes are durable and personal.** `perf_note_add` writes something a manager may quote a year
  later. Store-schema changes need round-trip tests and a migration that cannot drop a note.
- **Delivery** — `performance/delivery.py` sends this content somewhere. Confirm redaction applies
  and that no name or note reaches a log.

## Auto lane, in practice

Broken tests, dead code, doc drift, a missing round-trip test for an existing store field. Every
change to what the mode *says about a person* proposes, without exception.

## Out of scope

The performance export's markup (**web-ux**). Tracker fetching (**integrations**).
