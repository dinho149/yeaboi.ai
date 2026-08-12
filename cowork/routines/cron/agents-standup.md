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

2. Post ONE message to `#yeaboi-claude`. Costs are estimates and the engine's own wording already
   says so — carry that through rather than restating a number as fact:

   ```slack
   🧭 **Agents** — Tue 11 Aug · 74 sessions · ~$690 across 3 models

   Agents worked mainly in the go migration and the cowork fleet, with most of the spend on a
   single long review-followup session.

   ⭐ **Highlights** (3)

   1. [go-review-followups](https://github.com/dinho149/yeaboi.ai/pull/221) — 51 turns, wave 5 merged
   2. Bash and Edit dominated tool use, at 378 and 260 calls
   3. Three models in one session, the heaviest one carrying the cost
   ───────────────────────────

   ⚠️ **Needs attention** (2)

   1. One session ran to ~$299 alone — worth a look before it becomes the shape of every review
   2. `yeaboi-go-migration` has no local history in this environment
   ───────────────────────────

   _Local-session coverage is partial here: this run saw trackers only._
   ```

   `⭐` and `⚠️` are this message's two section anchors, fixed. Cap each list at 5. The narrative
   is the engine's, not yours.

3. If the digest is empty (`sessions_worked == 0` and no `repo_activity`), post the **degenerate
   form** — a title line and nothing else, as `cowork-scribe.md` allows:

   ```slack
   🧭 **Agents** — Tue 11 Aug · no sessions recorded in this environment
   ```

   Absence of evidence is not idleness — never phrase it as "the agents did nothing", and never
   pad the line into a four-line message with empty sections to look like the normal shape. One
   quiet line is the honest report.

## Environment note

Local-session coverage depends on where this routine runs: a cloud environment sees no
`~/.claude` history, so its digest is tracker-only (the coverage notes will say so). Run it on a
machine environment with local agent history for the full picture — `environment_id` is
per-machine, which is exactly why it is never reconciled by `/cowork deploy`.

## Stop conditions

- Do not run the engine twice; one `agents standup` invocation is the whole job.
- Do not file issues, create tickets, or touch the repo — this routine only posts to Slack.
- If the CLI exits non-zero, post the degenerate form with the error's first line and nothing else:
  `🧭 **Agents** — Tue 11 Aug · standup engine failed: <first line>`. No sections, no footer, no
  speculation about the cause.
