# daily digest

**Trigger** — cron `15 8 * * *` (daily 08:15 UTC). On Saturdays marketing starts 15 minutes earlier;
that is rarely enough for a `deep` read of a whole mode, so treat the draft link as usually arriving
in the *next* day's digest. On every other day there is no draft to carry at all.
**Summary** — the open proposals, bucketed by type, waiting on your approval
**Workstream** — none; this routine spans all fifteen.
**Model** — `standard` ([models.md](../../models.md))

The single decision point. It is the only routine that posts proposals to Slack.

## Run

1. **Collect** — `gh issue list --label cowork:proposal --state open --limit 100 --json
   number,title,labels,createdAt,body`, and separately
   `gh issue list --label feature-candidate --state open --limit 100 --json
   number,title,labels,createdAt`.

   The second list is `feedback-remediation.yml`'s output — user-reported bugs and requests it has
   triaged and nominated. It waits on the same human verb as a proposal, so it belongs in the same
   digest. `backlog-groomer.yml` used to nudge these on its own weekly clock; retiring it in favour
   of one queue only works if that queue actually carries them.

2. **Bucket by type, then rank inside each bucket.** Group every open proposal by its `type:<kind>`
   label and rank within the group on impact over effort, with a deliberate thumb on the scale for
   anything that unblocks another workstream and anything a user would notice. Two items from the
   same workstream should not both make a bucket's top three unless they genuinely earn it — breadth
   beats depth in a digest, and without that nudge the bug bucket becomes three `platform` lines.

   The buckets are the point. A flat ranking over a queue that is half bugs lands on five bugs every
   morning: on one shared scale concrete breakage always outranks speculative value, so a feature or
   a chore never wins a slot it has to take from a production bug. A bucket does not ask it to. This
   file used to try to fix that with an instruction — rank non-bugs together with everything else, no
   discount for not being a bug — and an instruction cannot beat the scoring that causes the problem.

   Section order is fixed: **Security, Bugs, Features, Improvements, Chores, Docs**. Fixed on
   purpose — a reader scanning the same message every morning should not have to hunt for where the
   chores went. Security leads because it used to be a thumb on a flat scale and is now a section.

   **A type with no open proposals is omitted entirely**, heading and all. Six standing headings,
   most of them empty on most days, is exactly the "nothing today" fatigue the stop conditions exist
   to prevent. There is no `other` section: `scripts/cowork_setup.py` records that `type:other` is
   the feedback system's fallback and is never emitted by a cowork scout, so an `other` or untyped
   proposal falls into the remainder count below.

3. **Post one Slack message** to `#yeaboi-claude` via `cowork-scribe`:
   - up to **3 proposals per type**, each type under a bold heading carrying its open count —
     `*Bugs* (12 open — top 3)`, and drop the `— top 3` when the bucket is listed in full, because
     `(2 open — top 3)` reads as a promise the section did not keep — and each proposal one line
     underneath: `<issue title> — why-now
     clause (issue link)`. The title comes from the issue verbatim and already leads with its
     `[type][workstream]` tag — never add a second one, and never strip the tag because the heading
     appears to make it redundant: the tag is what `cowork-scribe`'s title contract guarantees, and
     it is what survives the line being quoted anywhere else. For an old issue whose title lacks the
     tag, prepend it from the issue's labels. The why-now is one clause, not a sentence chain — if a
     line wraps twice it is too long
   - the count of everything else, **by workstream** — the other axis, on purpose. The headings above
     already carry the per-type counts, so a per-type remainder would only restate them; the
     per-workstream remainder is the one place a reader learns which surface the backlog is piling
     up on. Omit the line entirely when the sections above listed everything — a remainder of zero
     is not a fact anyone needs
   - the oldest 3 `feature-candidate` issues, under their own heading — these came from users, so
     they are listed separately rather than ranked against scout finds, and by age rather than by
     impact: a reported bug that has waited a month is the fact worth surfacing
   - this week's marketing draft link, if one exists and no earlier digest has carried it
   - the reminder that approval is ✅ on an item's thread reply (relayed within the hour — or
     `/cowork run slack-relay` for right now) or adding `claude-implement` on GitHub, and rejection
     is ❌ or closing the issue — the digest still names the verbs, because a reader who has
     forgotten them has nowhere else to look

   Then **one reply in that message's thread per listed item** — every listed proposal and every
   `feature-candidate` line — formatted `#<issue-number> — <verbatim title> — <issue link>`, the
   number leading so `cron/slack-relay.md` can parse it. The thread replies are what make a single
   reaction mean a single issue; without them ✅ on the digest would be ambiguous across every item
   it lists. Per-type sections raise the ceiling to eighteen proposal replies plus three
   `feature-candidate` replies — a longer thread, not a second message, and the reply shape is a
   parsed contract that does not change with the volume.

   One **channel-level** message, never a second on the same day — the channel-noise rule holds;
   the per-item replies live inside its thread, not in the channel.

   That rule is about *this* routine. `cron/day-ahead.md` posts one other channel-level message
   each morning, three hours earlier, and it is not a violation: it carries the day's schedule,
   not findings. The two rules that look like they forbid it both survive. "Never a second on the
   same day" keeps the digest from splitting itself across two messages, which is what makes ✅ on
   a thread reply mean one issue — a different routine posting a different kind of thing does not
   touch that. And "a digest that says nothing today every day trains everyone to ignore the
   channel" is about *absence of findings*, which is why this routine stays silent on an empty
   queue. A schedule is never absent: on a Sunday with no sweeps, "no sweeps today" is the fact
   being asked for, and a reminder you cannot rely on arriving is not a reminder. Keep them
   separate — never fold the schedule in here, and never let the day-ahead post carry a proposal.

4. **Age out** — close any `cowork:proposal` issue open more than 14 days with the comment
   "closed unapproved after 14 days — re-file if still relevant". Never touch an issue that carries
   `claude-implement`.

   A human comment does **not** exempt an issue. Commenting "not now" is the natural way to say no,
   and exempting commented issues would make every explicit rejection immortal while silence — the
   weaker signal — was the only thing that worked. Closing is the rejection; both dedup passes then
   suppress the find permanently.

   **Never age out a `feature-candidate` issue.** Those were written by a person about their own
   experience, not generated by a scout, and closing someone's bug report on a timer is not triage.
   They stay open until a human acts.

5. **Report the health line** — if any workstream has filed nothing in 21 days, say so in the digest.
   A silent scout is usually a broken scout, not a clean codebase.

   Separate the two ways a workstream goes quiet. Run `gh pr list --label cowork --state open --json
   number,labels,createdAt,url` first: a workstream with an open PR is **blocked**, not silent — it
   is forbidden from scouting until that PR merges. Name it and its PR, and do not count it against
   the 21-day line. Silence with no open PR is the case worth worrying about.

   Say *what* is blocking each one, because the two causes need different people. Run
   `uv run python scripts/pr_feedback.py --pr <n>` per blocked PR: red CI is a machine problem an
   agent can pick up, while unanswered review feedback is waiting on a judgment somebody has to make.
   A workstream stalled three weeks on two unanswered findings reads as "blocked on PR #123" without
   this, which is indistinguishable from a slow build and gets treated like one.

6. **Report the calibration line** — for each workstream, the approval rate over the last 90 days:
   proposals that received `claude-implement` ÷ proposals filed. One
   `gh issue list --label "workstream:<name>" --state all --limit 200 --json number,labels,createdAt`
   per workstream — pass the limit, because `gh` defaults to 30 and a truncated set makes a
   90-day rate the routine has no way to know is wrong. No new state, nothing stored between runs. Name any workstream sitting at **0
   approvals across 10 or more proposals**, and any at **100% across 5 or more**.

   Everything else in this system measures whether a routine *filed* something. This is the only
   thing that asks whether what it filed was worth filing. A workstream rejected twenty times running
   is not healthy because it was busy — its charter is pointed at the wrong thing, and the fix is to
   edit `workstreams/<name>.md`, not to wait for a better week. One approved every single time is
   under-reaching and should be proposing more.

   Report it weekly, on Mondays, not daily — an approval rate that moves by one issue a day is noise,
   and a number nobody can act on every morning is a number everybody stops reading.

## Stop conditions

- No open proposals, no open `feature-candidate` issues, and no marketing draft: post nothing at all.
  A digest that says "nothing today" every day trains everyone to ignore the channel.
- The calibration line alone is not a reason to post. On a Monday with an otherwise empty queue,
  stay quiet — a health metric is worth reading beside work, not instead of it.
