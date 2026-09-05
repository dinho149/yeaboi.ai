---
name: agents-usage
description: "(beta) See what the user's AI coding agents cost with yeaboi: per-model, per-project and per-source token/cost breakdowns plus a daily trend, computed from local agent session logs (Claude Code). Use when the user asks what their agents cost, where agent spend went, or for an agent usage/cost report."
---

# Agent usage workflows with yeaboi

> **Beta.** The Agents modes are in beta — costs are estimates from local
> session logs and public rate tables, not the provider's bill. Present every
> number as an estimate to verify, not an invoice, and say so.

1. **Run the report** with `agents_usage`. Defaults cover the last 30 days of
   every project and source; narrow with:
   - `window_days` — how far back to look (1–365).
   - `project` — substring of a project directory name (e.g. "webapp").
   - `source` — the telemetry source label (currently `claude_code`).

2. **Present the result** conversationally: lead with the total and the window,
   then the top models and projects. Read `billing_kind` first: on
   `subscription` the total is what the same work would have cost on the API
   and is included in the plan — say "API-equivalent", never "you spent".
   `cache_cost_share` is the part of the estimate that was prompt-cache
   traffic (usually most of it — context size drives the bill).
   `unknown_model_cost_share` above 0 means part of the total was priced at a
   fallback tier — say which share. Tokens are attributed to the day they
   were spent, so `daily_trend` is real per-day traffic. The
   `insights`/`recommendations` prose is the model's read over the
   deterministic aggregates; the numbers themselves never come from the LLM.

3. **Compare across runs** with `agents_usage_history` (newest first) instead of
   recomputing — each row is a previously generated report.

4. **Surface `warnings`** (the beta caveat, "no sessions found", LLM fallback)
   so the user knows what informed — or didn't inform — the output. A report of
   $0.00 with a "no local agent sessions" warning means the machine has no
   Claude Code history to read, not that agents are free.

5. **Privacy.** Only aggregates are read into the report; session transcripts
   are never copied or uploaded. Exports auto-save under
   `~/.yeaboi/exports/agentwatch/usage/`.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`; don't retry blindly. `llm_mode: "fallback"`
means no LLM was reachable — the numbers are still real, only the
insights/recommendations prose fell back to deterministic lines.
