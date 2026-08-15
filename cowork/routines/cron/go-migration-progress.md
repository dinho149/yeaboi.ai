# go migration progress

**Trigger** — cron `30 8 * * 2` (Tue 08:30 UTC, after the digest)
**Summary** — the weekly Go-migration progress post: waves merged, in flight, blocked
**Workstream** — [`workstreams/go-migration.md`](../../workstreams/go-migration.md)
**Model** — `fast` ([models.md](../../models.md))

The migration's weekly TELL. Every judgement in the message — the bar, the counts, the
in-flight line, a blocked wave — is made in tested Python; this routine's contract is
[`cron/day-ahead.md`](day-ahead.md)'s: **rendered, not composed**. If a line looks wrong, the
finding is a bug in `scripts/migration_progress.py` and the fix is a PR against it — never a
correction typed into a Slack message.

## Run

1. `git fetch origin main && git reset --hard FETCH_HEAD` — the same first step as
   `cron/cd-deploy.md`, for the same reason: the bar is rendered from this tree's program doc
   and core version, and a checkout that predates the latest merge posts last week's numbers
   with total confidence.
2. `uv run python scripts/migration_progress.py --weekly`.
3. Hand the `lines` array to `cowork-scribe` and post it as **one channel-level message** to
   `#yeaboi-claude` (`C0BMADQQN1Z`), the lines joined by newlines, nothing added, nothing
   re-worded, nothing re-counted.
4. **Check in.** Follow [check-in.md](../../check-in.md). It is the last thing you do.

The message it posts (the numbers are the renderer's, never yours):

```slack
🐹 **Go Migration** — 6 of 19 waves shipped · Tue 19 Aug
▰▰▰▰▰▰▱▱▱▱▱▱▱▱▱▱▱▱▱ 6/19 waves · 0/13 program wave-PRs merged

🚧 **In flight** (1)

1. [migration(w7): retro/poker export builders #231](https://github.com/dinho149/yeaboi.ai/pull/231)
   — open since Fri 15 Aug

───────────────────────────

📦 **Shipped** — [yeaboi-core 0.4.0](https://pypi.org/project/yeaboi-core/) on PyPI · 36 parity tests on `main`

Next wave and the full plan: [the program of record](https://github.com/dinho149/yeaboi.ai/blob/main/cowork/migration/program.md)
```

## Post every Tuesday

Including a week where nothing moved — a progress bar that only appears when it grows is a
progress bar nobody can trust. A blocked wave is named on its own line by the renderer; that
line is the reason this post exists.

## Stop conditions

- `--weekly` exits non-zero, or `lines` is empty: post nothing and report the failure in the
  run log. A wrong bar is worse than no bar.
- Never edit a file, open an issue, or touch Linear or the program doc — this routine holds no
  `Write` or `Edit` grant, and the one thing it may spawn is the scribe.
