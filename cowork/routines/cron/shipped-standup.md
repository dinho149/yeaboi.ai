# shipped standup

**Trigger** — cron `0 18 * * *` (daily 18:00 UTC, after the working day)
**Summary** — the day's standup: what cowork shipped end to end, what is building, what is stuck
**Workstream** — none; this routine reports on every workstream's output.
**Model** — `standard` ([models.md](../../models.md))

Security, bugs and chores merge without asking anybody. This is the message that makes that
legible: one post a day saying what actually shipped, what proved it, and which pre-release it is
in — so an unattended fleet is something you can read rather than something you have to trust.

It replaces two things that used to arrive separately. The morning schedule post, which announced
what *would* run and was the fleet's only mandatory message, is gone: what a routine is about to do
is much less interesting than what it did. And the per-PR ship note, one Slack message per merged
PR with batching explicitly forbidden, is now a line here — the record still exists, it is just no
longer a notification each time.

**One channel message. No thread replies.** The digest owns threads, because its replies are
inputs — `cron/slack-relay.md` parses them for ✅/❌. Nothing here is a decision, so nothing here is
reactable, and a reply shape that looked like the digest's would be parsed as one.

## Run

1. **What merged** — `gh pr list --state merged --search "merged:>=<the last post's UTC timestamp>" --json
   number,title,labels,mergedAt,url,body`. Keep the PRs carrying the `cowork` label **or** the
   `ci-sentinel` label — `ci-sentinel.yml` opens unattended fix PRs for a red `main` and labels
   them only `ci-sentinel`, so a `cowork`-only filter would silently omit exactly the merges
   nobody watched. (`codeql-triage` needs no special case: it labels `cowork` on purpose.) If you
   cannot establish when the last post was, use the last 24 hours; never guess a longer window,
   because a double-reported merge reads as a second change that never happened.

   **Use a full `YYYY-MM-DDTHH:MM:SSZ` timestamp, not a date.** `merged:>=2026-08-11` is
   date-granular and matches from midnight, so Wednesday's 18:00 post would re-report every merge
   Tuesday's already named — the exact double-report the sentence above forbids, arriving through
   the primary path rather than the fallback.

2. **The trace behind each one.** For each merged PR read the body and its checks
   (`gh pr checks <n>`, `gh pr view <n> --json reviewDecision,statusCheckRollup`) for the facts the
   second line carries: what proved it, how many tests ran, the review verdict, and the merge time.
   A `type:bug` PR carries its before/after regression run in the body — that is the fact worth
   printing for a bug, because it is the thing that let it merge unwatched. **Never invent one of
   these.** If a fact is not there, leave that clause out; a missing clause is honest and an
   invented one is the whole message's credibility.

3. **The beta** — `uv run python scripts/release_channel.py --manifest --json`. **`installable`**
   is the version named in the header and the footer, and it is what the reader installs to feel
   the day's work. If it is `null`, nothing has been published for this batch: say
   `no new pre-release` rather than printing a stale one.

   **Not `latest_prerelease`.** That field is what the *next* release-worthy merge would be
   numbered — a commit count, raised by every docs and chore merge, including ones that publish
   nothing. `installable` is backed by a `beta/X.Y.ZrcN` tag, and `publish-beta.yml` pushes the tag
   only after the PyPI upload returns. One of the two is a fact and the other is a forecast, and
   the footer is an install command.

4. **What is building** — `gh pr list --label cowork --state open --json number,title,labels,url`.
   One line each. A PR open more than 7 days, or one whose checks are red, belongs under **Stuck**
   with the reason, not under **Building** — the whole point of naming it is that a wedged
   workstream is silent otherwise.

5. **Who did not run** — `uv run python scripts/cowork_setup.py --agenda` lists what was scheduled
   today. Read today's 📅 thread and take the routine name off every check-in in it. A routine goes
   under 🔴 **Did not run**, with the time it was due, only when all three hold:

   - it was **due after 05:45 UTC**, when 📅 goes up. A run that fires earlier has no thread to
     reply to and checks in to its run log instead — `cd-deploy` at 04:00 every day, plus any push
     or GitHub event overnight. Reporting those would put a false 🔴 in this post every morning;
   - it was **already due** by the time you post, so nothing scheduled for this evening counts, and
     `slack-relay`'s hourly window counts only up to now;
   - it left **no check-in at all**. The relay checks in on its first fire of the day and whenever
     it acted, so one line from it is a full pass, not a partial one.

   This is the one failure the fleet cannot report on itself. A run that dies before its last step
   cannot post a check-in — on 2026-08-06 the security sweep died on `Authentication error` after
   one turn and nothing said so for a week — so the *absence* of a reply is the only evidence there
   is, and something has to be looking for it. Say nothing when the two lists agree, which is most
   days.

6. **Post**, through `cowork-scribe`:

```slack
🚢 **Shipped** — Tue 11 Aug · 3 merged → `3.6.0rc9`

1. **[security]** [pin 4 unpinned action SHAs](https://github.com/dinho149/yeaboi.ai/pull/238)
   — codeql clean · 12 tests · review clean · merged 14:02
2. **[bug]** [standup confidence wrong on day 1](https://github.com/dinho149/yeaboi.ai/pull/239)
   — regression test added, failed before and passes after · review clean · merged 16:40
3. **[chore]** [drop 3 dead helpers in retro/](https://github.com/dinho149/yeaboi.ai/pull/240)
   — review clean · merged 17:15
───────────────────────────

🔨 **Building** (1)

1. [poker point write-back races](https://github.com/dinho149/yeaboi.ai/pull/241)
   — opened 2 days ago, CI green
───────────────────────────

🚧 **Stuck** (1)

1. [doc drift in POKER_DECK](https://github.com/dinho149/yeaboi.ai/pull/145)
   — CI green, no review posted in 7 days
───────────────────────────

🔴 **Did not run** (1)

1. `06:00` **security-sweep** — due, never checked in
───────────────────────────

`pip install --pre yeaboi==3.6.0rc9`
```

Every merged item links to its PR. The old shape named what shipped and gave a reader nowhere to
click, which meant "what proved it" could only ever be as good as the one clause beside it.

**Omit a section that is empty**, heading and all — `🚧 **Stuck** (0)` is a line whose only content
is that it has none. If nothing is stuck, the reader learns that from its absence, the same way the
digest drops an empty type.

**A divider is the only separator that survives a list.** Slack renders `1.` items as a list block
and eats the blank line that ends it, so a heading written after a list arrives glued to the final
item and reads as part of it. The divider is not blank, so it survives — it lands against the last
item, which is what a divider is for, and the blank line *after* it is kept. This was measured
against `#yeaboi-claude`, not assumed.

**Do not align columns with spaces.** The old shape set `Building` and `Stuck` as a two-column
table padded with spaces; Slack renders in a proportional font, so that alignment was never visible
to anybody — the same fact `scripts/cowork_setup.py` records about the agenda. Bold headings and
list markers are the only structure this dialect actually has.

The `[type]` tag comes from the PR's `type:<kind>` label, which
[house-rules.md](../../house-rules.md) requires on every cowork PR. A PR without one ships untagged
rather than unmentioned — report it with the link and no tag, so the missing label is visible
instead of papered over.

7. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do.

## Stop conditions

- **Nothing shipped, nothing building, nothing stuck, nobody missing → post nothing.** This routine
  is *not* exempt from the fleet's "exit quietly" rule the way the old schedule post was. A daily
  message that says "no changes today" every weekend is how a channel gets muted, and a muted
  channel is worse than no channel — the one day it matters, nobody looks.
- **A no-show on its own is worth the post.** A quiet day where every routine ran is silence; a
  quiet day where one of them never started is the fault this post exists to catch, and it would
  otherwise be reported by nothing at all.
- **One channel message per day, never a second.** The check-in in step 7 is this routine's own
  reply under 📅 and is not that message.
- **Never invent a number, a time, a verdict or a version.** Every one of them is readable from
  `gh` or from `release_channel.py`; a fact you cannot read is a clause you omit.
- **Report nothing about proposals.** Those are decisions and they belong to `cron/digest.md`. This
  post is the record of what already happened, and mixing the two is what makes a reader stop
  telling them apart.
