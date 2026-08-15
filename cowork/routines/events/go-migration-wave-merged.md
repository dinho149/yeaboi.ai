# wave merged — announce it

**Trigger** — GitHub event, pull request `closed`
**Summary** — on a merged migration wave PR: post the wave's Slack line with the new bar
**Filters** — merged `workstream:go-migration` PRs only; everything else exits silently — and
**step 1 is the only thing that enforces it**, see below.
**Workstream** — [`workstreams/go-migration.md`](../../workstreams/go-migration.md)
**Model** — `fast` ([models.md](../../models.md)) — it reads two fields, runs one script, and
posts what it printed

**The registered webhook (declared at the end of this file) cannot express "merged", and cannot
filter by label.** The API's filter speaks actions only, so this fires for every closed PR in
the repo, most of which are not waves. That is fine — the run is a read and an exit — but it
means step 1 is the whole gate: getting it wrong posts a 🌊 announcement for an abandoned PR,
or for a dependabot bump.

## Run

1. `gh pr view <n> --json labels,mergedAt,title,url`. **Stop silently unless** `mergedAt` is
   non-null **and** the labels include `workstream:go-migration`.
   [`events/pr-merged-close-loop.md`](pr-merged-close-loop.md) fires on the same event and
   owns Linear and Notion; this routine owns exactly one Slack message and nothing else.
2. `uv run python scripts/migration_progress.py --wave-merged --pr <n>`.
3. Hand the `lines` array to `cowork-scribe` and post it as **one channel-level message** to
   `#yeaboi-claude` (`C0BMADQQN1Z`), the lines joined by newlines, verbatim — the contract is
   [`cron/day-ahead.md`](../cron/day-ahead.md)'s: rendered, not composed.
4. **Check in.** Follow [check-in.md](../../check-in.md). It is the last thing you do.

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
