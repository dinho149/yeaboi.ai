# wave merged — announce it

**Trigger** — GitHub event, pull request `closed`
**Summary** — on a merged migration wave PR: post the wave's Slack line with the new bar
**Filters** — merged wave PRs only; everything else exits silently. Step 1 is the cheap gate;
the renderer is the strict one — `merged_pr_facts` refuses a non-wave (label without the
`cowork/migration-w<N>` branch, bar the sanctioned #224) by exiting non-zero, and the stop
condition below catches that.
**Workstream** — [`workstreams/go-migration.md`](../../workstreams/go-migration.md)
**Model** — `fast` ([models.md](../../models.md)) — it reads two fields, runs one script, and
posts what it printed

**The registered webhook (declared at the end of this file) cannot express "merged", and cannot
filter by label.** The API's filter speaks actions only, so this fires for every closed PR in
the repo, most of which are not waves. That is fine — the run is a read and an exit. Step 1
filters the obvious cases cheaply; the renderer independently refuses anything that is not a
merged wave, so getting step 1 wrong costs a wasted script run, never a false 🌊 announcement.

## Run

1. `gh pr view <n> --json labels,mergedAt,title,url`. **Stop silently unless** `mergedAt` is
   non-null **and** the labels include `workstream:go-migration`.
   [`events/pr-merged-close-loop.md`](pr-merged-close-loop.md) fires on the same event and
   owns Linear and Notion; this routine owns exactly one Slack message and nothing else.
2. `git fetch origin main && git reset --hard FETCH_HEAD` — the bar is rendered from this
   tree's program doc, and the checkbox this merge flipped is on `origin/main`, not
   necessarily in the checkout this session was handed. A stale tree announces "Wave 7
   merged" above a bar that does not include wave 7.
3. `uv run python scripts/migration_progress.py --wave-merged --pr <n>`.
4. Hand the `lines` array to `cowork-scribe` and post it as **one channel-level message** to
   `#yeaboi-claude` (`C0BMADQQN1Z`), the lines joined by newlines, verbatim — the contract is
   [`cron/day-ahead.md`](../cron/day-ahead.md)'s: rendered, not composed.
5. **Check in.** Follow [check-in.md](../../check-in.md). It is the last thing you do.

The message it posts (the numbers are the renderer's, never yours):

```slack
🌊 **Go Migration** — Wave 7 merged · Fri 22 Aug
▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱ 7/19 waves · 1/13 program wave-PRs merged

[migration(w7): retro/poker export builders #231](https://github.com/dinho149/yeaboi.ai/pull/231) merged with its parity gate green · yeaboi-core is at 0.5.0.

Next: W8, Foundations: config/paths, 85 env vars, CLI parser skeleton — [the program of record](https://github.com/dinho149/yeaboi.ai/blob/main/cowork/migration/program.md)
```

## Stop conditions

- Closed-unmerged, or any PR without the `workstream:go-migration` label → exit silently; a
  normal event, not worth a line.
- The renderer failing → post nothing, report in the run log; never hand-compose the bar.
- Never comment on, reopen, or relabel the PR. Never touch Linear or Notion —
  `pr-merged-close-loop` already runs on this event and owns both.

## What fires it

Declared last rather than under the header (where its three siblings carry theirs) because the
template linter (`TestSlackTemplates.FENCE`) reads fences in document order, and this is the one
routine that carries both a webhook block and a ```slack template with nothing between them —
`scripts/cowork_setup.py` finds the block wherever it sits.

```json webhook
{"source": "github", "events": ["pull_request"], "filter": {"actions": ["closed"]}}
```
