# agents standup

**Trigger** — cron `15 6 * * 1-5` (weekdays 06:15 UTC)
**Summary** — the daily agent digest: what the AI agents shipped, spent, and left open
**Workstream** — [`workstreams/agents.md`](../../workstreams/agents.md)

Not a sweep — this routine *runs the product* rather than surveying the code. It composes
nothing itself: the engine builds the digest, the routine posts it.

## Run

1. From the repo root, run:

   ```bash
   uv run python -m yeaboi.cli agents standup --tracker-sources github --format json
   ```

   The JSON on stdout is an `AgentStandupDigest`: narrative, highlights, in-flight agent PRs,
   attention items, per-session and per-tracker evidence rows, coverage notes, warnings.

2. Post ONE message to `#yeaboi-claude`: the `narrative`, then `highlights` and `attention_items`
   as short bullet lists (cap 5 each), then any `coverage_notes` as one italic line. Present costs
   as estimates — the digest's own wording already does.

3. If the digest is empty (`sessions_worked == 0` and no `repo_activity`), post the one-line
   "quiet day" version: the window plus the coverage notes. Absence of evidence is not idleness —
   never phrase it as "the agents did nothing".

## Environment note

Local-session coverage depends on where this routine runs: a cloud environment sees no
`~/.claude` history, so its digest is tracker-only (the coverage notes will say so). Run it on a
machine environment with local agent history for the full picture — `environment_id` is
per-machine, which is exactly why it is never reconciled by `/cowork deploy`.

## Stop conditions

- Do not run the engine twice; one `agents standup` invocation is the whole job.
- Do not file issues, create tickets, or touch the repo — this routine only posts to Slack.
- If the CLI exits non-zero, post the error's first line to `#yeaboi-claude` instead of a digest.
