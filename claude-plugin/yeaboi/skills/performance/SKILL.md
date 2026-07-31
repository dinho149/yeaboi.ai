---
name: performance
description: "(beta) Manage engineers with yeaboi: 1:1 prep from real delivery data, 1:1 completion summaries with tracked action items, periodic performance reviews, and quick notes. Use when the user wants to prepare for a 1:1, write up a held 1:1, draft a performance review, or record an observation about an engineer."
---

# Performance workflows with yeaboi

> **Beta.** Performance mode is in beta — its output is not yet verified against real delivery data.
> Treat every 1:1 prep, summary and review as a draft to edit, not a verdict, and
> say so when you present it to the user.

1. **Know the roster.** Call `perf_roster` to list the engineers derived from
   recent Jira/Azure DevOps assignees — these are the names every other tool
   accepts. If it's empty, no tracker is configured (point at `yeaboi --setup`).

2. **Pick the workflow** from what the user asked:
   - **Prep a 1:1** → `perf_one_on_one_prep` with the engineer's name. Present
     the talking points, feedback, goals and growth areas conversationally —
     it already folds in the open action items from their previous 1:1.
   - **Write up a held 1:1** → ask for the notes/transcript (pasted text), then
     `perf_one_on_one_complete`. Leave `deliver` false — you present the
     summary; only set `deliver: true` (emails it via the configured SMTP) if
     the user explicitly asks. The action items are tracked and carried into
     the next prep automatically.
   - **Draft a review** → `perf_six_month_review` (set `period_months` if they
     name a different window). It synthesises past 1:1s, delivery history and
     the competency framework. Frame the output as a draft for the lead to
     edit, never a verdict.
   - **Record an observation** → `perf_note_add` with the engineer and the
     note. Notes feed future preps and reviews.

3. **Surface `warnings`** (the beta caveat, tracker 401s, missing SMTP, LLM
   fallback) so the user knows what informed — or didn't inform — the output.
   Every perf tool now returns the beta caveat as its first warning.

4. **Sensitive data.** This is personnel material: keep it in the conversation,
   don't post it anywhere external unless explicitly asked. Exports auto-save
   under `~/.yeaboi/exports/performance/<engineer>/`.

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint` (usually credentials — `yeaboi --setup`); don't
retry blindly. `llm_mode: "fallback"` means no LLM was reachable and the artifact
is a deterministic skeleton — suggest `yeaboi --setup`.
