# shipped standup

**Trigger** — cron `0 18 * * *` (daily 18:00 UTC, after the working day)
**Summary** — the evening standup: one post per area that moved, what proved it, what is stuck
**Workstream** — none; this routine speaks for every workstream, one message each.
**Model** — `standard` ([models.md](../../models.md))

Security, bugs and chores are built without asking anybody, and wait gate-green for the release
batch. This is the message that makes that legible: a post saying what moved, what proved it, and
whether it has shipped or is still waiting for a batch — so an unattended fleet is something you
can read rather than something you have to trust.

**It is one message per area, not one message.** Until 2026-08-16 this was a single roll-up grouped
by *type*, and a fix in `analysis/` arrived as a `[bug]` line between a go-migration wave and a
platform chore. Twelve of the seventeen workstreams have no other voice in the channel — the
maintenance sweeps post nothing, ever — so that roll-up was the only place they were ever heard,
and it was the place they were least legible. Now each area gets its own message under its own
glyph from [README.md](../../README.md)'s table, and every one of them stands alone.

**You compose nothing.** `scripts/cowork_evening.py` renders every line, the same contract
`--agenda` and `scripts/migration_progress.py` have. A fan-out means N messages rather than one,
and a model deciding *how many* and *which* is a model that can post the same area twice or drop
one silently. The count is arithmetic over labels; you post what you are handed.

**One channel message per area. No thread replies, anywhere.** The digest owns threads, because its
replies are inputs — `cron/slack-relay.md` parses them for ✅/❌. Nothing here is a decision, so
nothing here is reactable, and a reply shape that looked like the digest's would be parsed as one.

## Run

1. **Find the window.** Read `#yeaboi-claude` and take the UTC timestamp of the last message this
   routine posted. If you cannot establish it, use the last 24 hours; never guess a longer window,
   because a double-reported merge reads as a second change that never happened.

2. **Read today's check-ins.** Open the thread under today's 📅 message and take the routine name
   off every reply in it. That list is step 3's `--checked-in` argument, and it is the half of the
   fleet-health diff a script cannot reach — Slack is not on its egress.

   If there is no 📅 message at all, pass no `--checked-in` and post no 🩺. A missing 📅 is
   `day-ahead`'s fault to report, and an empty check-in list read as seventeen no-shows would put
   the loudest false alarm in the fleet at the top of the channel.

3. **Render**, from the repo root:

   ```bash
   uv run python scripts/cowork_evening.py \
     --since <the timestamp from step 1, as YYYY-MM-DDTHH:MM:SSZ> \
     --checked-in "<comma-separated routine names from step 2>"
   ```

   The JSON carries `posts` (one entry per area that moved, each with `workstream`, `glyph` and
   `lines`), `health` (the 🩺 message, or `null`), and `payload.warnings`.

4. **Read `payload.warnings` before posting anything.** They are the run's account of what it could
   not see. A failed read of merged PRs means every post below undercounts, and a quiet evening and
   a blind one arrive looking identical. If a warning says the counts cannot be trusted, post the
   messages anyway and say so in your check-in — never silently, and never with a caveat typed into
   the channel, which is a line no future reader will know was unusual.

5. **Post each `posts[].lines` block verbatim**, one channel message each, in the order given.
   Join the lines with newlines and add nothing — no preamble, no "and in other areas", no message
   introducing the others. Each one is about one area and is read on its own.

   The shape, for reading rather than for copying — every value below is a placeholder:

   ```slack
   🔬 **Analysis** — Sun 16 Aug · 2 merged → `3.9.0rc14`

   1. **[bug]** [AI-marker regexes lack word boundaries](https://github.com/dinho149/yeaboi.ai/pull/271)
      — regression test added, failed before and passes after · review clean · merged 14:02
   2. **[chore]** [drop 3 dead helpers in analysis/](https://github.com/dinho149/yeaboi.ai/pull/272)
      — merged 17:15
   ───────────────────────────

   🔨 **Building** (1)

   1. [small-sample honesty gate](https://github.com/dinho149/yeaboi.ai/pull/273)
      — opened today
   ───────────────────────────

   `pip install --pre yeaboi==3.9.0rc14`
   ```

   Rules the shape does not carry, all of them enforced in the renderer rather than by you:

   - **The title glyph is the area's**, from [README.md](../../README.md)'s table. It is the same
     glyph in every message that speaks for that area, which is what makes a post identifiable from
     its notification preview. 🔐 is not one of them — it belongs to the security *disclosure*
     lane, and security's area glyph is 🦺.
   - **A missing clause is honest and an invented one is the whole message's credibility.** A
     review verdict, a check result, a merge time, a regression run: read or omitted, never
     guessed. `ci green`/`ci red` appear only when the commit actually carried statuses — a commit
     with none reads back as *pending*, which means "I was not told" and never "in flight".
   - **Stuck is three things, not one**: open past seven days, red checks, or a standing
     `changes requested`. All three are computed by the script; the boundary is
     `STUCK_DAYS` and it is unchanged by the fan-out.
   - **A divider is the only separator that survives a list.** Slack renders `1.` items as a list
     block and eats the blank line that ends it, so a heading written after a list arrives glued to
     the final item. This was measured against `#yeaboi-claude`, not assumed.
   - **A PR with no `type:` label ships untagged rather than unmentioned**, and one with no
     `workstream:` label is reported under 🪛 **Fleet** with the gap named. Both make a missing
     label visible instead of papering over it.

6. **Post `health.lines` last**, if it is not `null` — the one message with no area, because a
   routine that never ran has none:

   ```slack
   🩺 **Fleet health** — Sun 16 Aug · 1 routine never ran

   1. `07:00` **security-sweep** — due, never checked in
   ```

   This is the one failure the fleet cannot report on itself. A run that dies before its last step
   cannot post a check-in — on 2026-08-06 the security sweep died on `Authentication error` after
   one turn and nothing said so for a week — so the *absence* of a reply is the only evidence there
   is. It is a channel message rather than a reply under 📅 for exactly that reason: a thread reply
   does not notify, and this is the section nobody would go looking for.

7. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do, and it is a thread reply under
   📅, not one of the messages above.

## Stop conditions

- **No area moved and nobody is missing → post nothing.** This routine is *not* exempt from the
  fleet's "exit quietly" rule the way the old schedule post was. A daily message that says "no
  changes today" every weekend is how a channel gets muted, and a muted channel is worse than no
  channel — the one day it matters, nobody looks. `posts` empty and `health` null is that day.
- **A no-show on its own is worth the post.** A quiet day where every routine ran is silence; a
  quiet day where one of them never started is the fault this post exists to catch.
- **One message per area, and never a second for the same area.** `posts` holds one entry per
  workstream by construction; if you find yourself about to post two under one glyph, something is
  wrong with the run and the right move is to post neither and say so in your check-in.
- **Never invent a number, a time, a verdict or a version**, and never retype one. Every figure in
  every block came out of the renderer; a figure that looks wrong is a bug in `cowork_evening.py`
  and the fix is a PR against it, not a correction typed into Slack where nobody will see it was
  corrected.
- **Report nothing about proposals.** Those are decisions and they belong to `cron/digest.md`. This
  post is the record of what already happened, and mixing the two is what makes a reader stop
  telling them apart. Work a sweep *filed* reaches the reader through 🗳️; work it *opened* is a
  🔨 **Building** line here.
