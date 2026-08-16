---
name: agents-advisor
description: "(beta) Find the recoverable share of the user's AI-agent spend with yeaboi: Read-waste mechanisms (identical re-reads, subset re-reads, write read-backs, line-number scaffolding), context-residency stats, cache-death gaps, and volatile content in prompt-prefix files, computed locally from agent session logs. Use when the user asks how to cut agent costs, why their agent bill is high, whether their prompt cache is working, or for an agent efficiency/waste report."
---

# Agent advisor workflows with yeaboi

> **Beta.** The Agents modes are in beta — recoverable figures are estimates of
> opportunity computed from local session logs (tokens ≈ bytes/4, priced at the
> window's blended input rate), never promised savings. Present them that way.

1. **Run the audit** with `agents_advisor_run`. The default covers the last 30
   days; narrow with `window_days` (1–365).

2. **Present the result** conversationally: lead with `recoverable_usd` against
   `total_cost_usd` and the window, then the largest `line_items` by
   `share_of_read_bytes`. A line item with `recoverable: false` (stale re-reads)
   is sized as context, not counted in the headline — keep that distinction if
   you mention it. `unknown_rate_share` above 0 means part of the pricing used a
   fallback tier — say which share.

3. **Explain the cache-health half** when it matters: `alignment_score` below
   100 means volatile-shaped content (UUIDs, timestamps, JWT-shaped strings,
   hex digests) was found in prompt-prefix files (`volatile_signals` names the
   files and kinds, never the content) — an *indicator* the prompt-cache prefix
   churns, not proof. `gaps_over_5m` counts likely cache-death windows;
   `residency_median` is how many turns a Read stays in context.

4. **Compare across runs** with `agents_advisor_history` (newest first) instead
   of recomputing.

5. **Surface `warnings`** (the beta caveat, "no sessions found", skipped
   transcripts, LLM fallback). Every mechanism count is a floor — an unreadable
   transcript under-reports rather than failing the run.

6. **Privacy.** Transcripts and CLAUDE.md files are read on the user's machine
   only; the report carries counts, byte totals and file paths — never content.
   Exports auto-save under `~/.yeaboi/exports/agentwatch/advisor/`.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`; don't retry blindly. `llm_mode: "fallback"`
means no LLM was reachable — the figures are still real, only the
insights/recommendations prose fell back to deterministic lines.
