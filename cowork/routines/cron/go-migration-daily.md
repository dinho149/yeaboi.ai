# go migration daily

**Trigger** — cron `0 17 * * *` (17:00 UTC, daily)
**Summary** — the daily Go-migration report: what landed, what is moving, and how to check it
**Workstream** — [`workstreams/go-migration.md`](../../workstreams/go-migration.md)
**Model** — `fast` ([models.md](../../models.md))

The migration's daily TELL, and the fleet's only recurring channel message. Every judgement in
it — the bar, the counts, what landed, the in-flight line — is made in tested Python; this
routine's contract is [`cron/day-ahead.md`](day-ahead.md)'s: **rendered, not composed**. If a
line looks wrong, the finding is a bug in `scripts/migration_progress.py` and the fix is a PR
against it — never a correction typed into a Slack message.

**It asks for nothing.** The lane merges its own waves into `chore/go-migration` behind the
parity gate, so there is no approval to give and no queue to drain; the one PR that reaches
`main` is a human's, at W19. A post from here that contains a question is a bug in the
renderer, not a judgement call for the run.

`0 17` on purpose: the campaign fires at `40 7`, so a wave that lands from that run is nine
hours old by the time this reads it and is reported the same day rather than the next. The
renderer's window is the trailing 24 hours rather than "since the last post" — it keeps no state
between runs — so a day this routine misses under-reports rather than double-reports, and the
bar beside it is right either way.

## Run

1. `git fetch origin && git reset --hard origin/chore/go-migration` — the bar is rendered from
   this tree's program doc and core version, and **the checkboxes live on the integration
   branch, not on `main`**, for the whole program. A checkout of `main` posts a bar stuck at
   the pilot baseline with total confidence. If the branch does not exist yet, fall back to
   `origin/main`: before the first wave the two agree.
2. `uv run python scripts/migration_progress.py --daily`.
3. Hand the `lines` array to `cowork-scribe` and post it as **one channel-level message** to
   `#yeaboi-claude` (`C0BMADQQN1Z`), the lines joined by newlines, nothing added, nothing
   re-worded, nothing re-counted.
4. **Check in.** Follow [check-in.md](../../check-in.md). It is the last thing you do.

The message it posts (the numbers are the renderer's, never yours):

```slack
🐹 **Go Migration** — 1 landed · Mon 24 Aug
▰▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱ 7/19 waves · 1/13 program wave-PRs merged

📦 **Landed** (1)

1. [W7 — Retro/poker export builders](https://github.com/dinho149/yeaboi.ai/pull/231)
   — proved by existing parity harness

───────────────────────────

🚧 **Next up** — W8, Foundations: config/paths, 85 env vars, CLI parser skeleton

───────────────────────────

🧪 **How to test** — everything the fleet has migrated so far lives on one branch

`git fetch origin chore/go-migration && git switch chore/go-migration`
`make parity` — the Go port and the Python original must agree byte for byte
`YEABOI_GO=0 yeaboi` vs `YEABOI_GO=1 yeaboi` — same numbers, same screens, either way

Next wave and the full plan: [the program of record](https://github.com/dinho149/yeaboi.ai/blob/main/cowork/migration/program.md)
```

## Post every day

Including a day where nothing landed — a progress bar that only appears when it grows is a
progress bar nobody can trust, and "nothing landed" on three consecutive days is exactly the
signal a human should act on. The renderer says `nothing landed` in the title and prints the
bar and the how-to-test block regardless; a stalled wave is named on its own line.

The how-to-test block is the same three commands every day, on purpose. It is a standing
invitation to check the branch, not a checklist that grows — the per-wave specifics are the
`— proved by …` clause under each landed wave, which the program's own §3 **Gate** column
wrote.

## Stop conditions

- `--daily` exits non-zero, or `lines` is empty: post nothing and report the failure in the
  run log. A wrong bar is worse than no bar.
- **Never post twice**, and never split the report across messages — the counts and what they
  count belong in one place.
- Never edit a file, open an issue, or touch Linear or the program doc — this routine holds no
  `Write` or `Edit` grant, and the one thing it may spawn is the scribe.
