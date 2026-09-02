---
name: weekly-review
description: "(beta) Review a solo developer's own week with yeaboi: what went well, what to change, and whether they are on track against their sprint plan, from their own standups and delivered work. Use when a user with no team asks how their week went, wants a Friday wrap-up, or wants to carry last week's actions forward."
---

# Weekly Review with yeaboi (Solo world)

> **Beta.** Weekly Review is in beta — it drafts a self-assessment from the user's
> own standups, tracker and plan. Present it as notes to edit, not a verdict.

1. **See what is open.** Call `weekly_review_history` first. Its `carried` list is
   last review's still-open actions, each with an `id`. If the user says what
   happened to any of them, collect `{id: "done" | "dropped" | "pending"}`.

2. **Run the review.** Call `weekly_review_run`, passing `carried_statuses` with
   those marks. `week_end` (YYYY-MM-DD) reviews a past week; blank is this week
   so far. `project_id` scopes the reads to one project; blank reads everything.
   `context_deps` toggles the cross-mode sources (`standup`, `plan`); an empty
   list is incognito.

3. **Present it in the user's own voice.** Lead with `plan_line` (the deterministic
   on-track sentence), then `summary`, then went well / to change, then the new
   `actions`. Show `carried_actions` with the statuses recorded this week. Surface
   `warnings` — no standups this week, no plan on file — so the user knows the
   coverage.

4. **Export.** `weekly_review_export` writes the review to Markdown under
   `~/.yeaboi/exports/solo/` (0 = the latest, or a `run_id` from history).

## Error handling

Every tool returns `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`; don't retry blindly. `llm_mode: "fallback"` means
no LLM was reachable and the review is the deterministic skeleton (plan line,
delivered titles, blockers) — suggest `yeaboi --setup`.
