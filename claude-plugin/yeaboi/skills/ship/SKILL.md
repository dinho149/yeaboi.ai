---
name: ship
description: "Read the user's supervised plan-item → PR runs (yeaboi ship): which epics, stories or tasks from the plan were handed to a coding agent, what each run's diff/validation/cost looked like, whether the human approved it at the gate, and which PR it opened. Use when the user asks what ship ran, why a launch was denied (budget), what a run cost, or how a batch of stacked PRs is progressing. Launching a run is done by the user in the TUI or with `yeaboi ship run` — never from here."
---

# Ship run history with yeaboi

Ship is yeaboi's supervised plan-item → PR pipeline: an **epic, a story or a
task** from the plan is implemented by a coding agent (Claude Code headless) in
an isolated worktree, validated deterministically, and pushed as a PR only after
the human approves the diff at the gate. This skill READS that history; it never
starts a run.

1. **List runs** with `ship_history` (newest first, `limit` 1–100). Each run
   carries: `item_id` and `level` (`epic` | `story` | `task` — the level says
   what the id names, and `story_id` is a legacy mirror of `item_id` kept for
   older readers), `status` (`approved` = shipped with a PR; `rejected`,
   `failed`, `cancelled` are terminal without one; `awaiting_approval` means a
   human is being asked right now), the branch, `diff_stat`, the validation
   verdict, `cost_usd`, and `pr_url`.

   Runs sharing a `batch_id` are one **batch**: an epic shipped as one PR per
   story, each branch stacked on the one before it, numbered `batch_index` of
   `batch_total`, with `batch_item_id` naming the epic they came from. A batch
   stops at the first member that does not end approved, so a batch with fewer
   members than `batch_total` is paused, not broken — the user continues it by
   launching the same epic again. Report it as "N of M stories shipped" and say
   what stopped it.

2. **Answer "what's happening / why was I denied"** with `ship_status`: the
   latest run plus the user-global launch budget (active permits, hourly and
   daily counts, and the circuit breaker that opens for an hour after a
   provider quota error). A denial reason like `hourly-budget (2/2 in last
   hour)` names the exact fuse; relay it and when it resets, don't suggest
   retry loops.

3. **Be honest about what a status means.** `failed` with "the agent produced
   no changes" is the deterministic bridge speaking: the agent exited cleanly
   but produced no diff, so nothing proceeded — that is the system working.
   Validation `configured: false` means nothing was proven, not that checks
   passed. `cost_usd` is an estimate from local transcripts priced at public
   rates, not a bill.

4. **To start a run**, point the user at the Ship card in the TUI or
   `yeaboi ship run <ITEM> --repo <path> --check "make test"`, where `<ITEM>` is
   any epic, story or task id (`--level` disambiguates a colliding id). Adding
   `--split` to an epic ships one stacked PR per story instead of one PR for the
   whole epic. Both end at a human approval gate; there is deliberately no MCP
   launch — a run holds a live subprocess for many minutes and its gate is a
   terminal decision.

   A shipped epic's launch budget is real: the fuse allows two agent launches an
   hour, so `--split` on a large epic is a multi-hour affair by design. Say so
   rather than suggesting a retry loop.

## Error handling

Both tools return `{ok, llm_mode, warnings, data}`. If `ok` is false, relay
`error.message` and its `hint`. An empty history is normal before the first
run — say so rather than treating it as an error.
