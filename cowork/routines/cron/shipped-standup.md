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

3. **The beta** — `uv run python scripts/release_channel.py --manifest --json`. `latest_prerelease`
   is the version named in the footer, and it is what the reader installs to feel the day's work.
   If it is `null`, nothing bumped the version: say `no new pre-release` rather than printing a
   stale one.

4. **What is building** — `gh pr list --label cowork --state open --json number,title,labels,url`.
   One line each. A PR open more than 7 days, or one whose checks are red, belongs under **Stuck**
   with the reason, not under **Building** — the whole point of naming it is that a wedged
   workstream is silent otherwise.

5. **Post**, through `cowork-scribe`:

```
🤙 cowork — Tue 11 Aug

Shipped 3 → beta 3.6.0rc9
  · [security] pinned 4 unpinned action SHAs
      codeql · 12 tests · review clean · merged 14:02
  · [bug] standup confidence wrong on day 1
      regression test added · review clean · merged 16:40
  · [chore] dropped 3 dead helpers in retro/

Building  poker point write-back races (PR #241)
Stuck     none

pip install --pre yeaboi==3.6.0rc9
```

The `[type]` tag comes from the PR's `type:<kind>` label, which
[house-rules.md](../../house-rules.md) requires on every cowork PR. A PR without one ships untagged
rather than unmentioned — report it with a bare `·` and no tag, so the missing label is visible
instead of papered over.

## Stop conditions

- **Nothing shipped, nothing building, nothing stuck → post nothing.** This routine is *not* exempt
  from the fleet's "exit quietly" rule the way the old schedule post was. A daily message that says
  "no changes today" every weekend is how a channel gets muted, and a muted channel is worse than
  no channel — the one day it matters, nobody looks.
- **One channel message per day, never a second**, and never a thread reply.
- **Never invent a number, a time, a verdict or a version.** Every one of them is readable from
  `gh` or from `release_channel.py`; a fact you cannot read is a clause you omit.
- **Report nothing about proposals.** Those are decisions and they belong to `cron/digest.md`. This
  post is the record of what already happened, and mixing the two is what makes a reader stop
  telling them apart.
