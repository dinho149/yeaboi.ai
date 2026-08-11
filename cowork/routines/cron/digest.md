# daily digest

**Trigger** — cron `15 8 * * *` (daily 08:15 UTC). On Saturdays marketing starts 15 minutes earlier;
that is rarely enough for a `deep` read of a whole mode, so treat the draft link as usually arriving
in the *next* day's digest. On every other day there is no draft to carry at all.
**Summary** — the open proposals, bucketed by type, waiting on your approval
**Workstream** — none; this routine spans all sixteen.
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

3. **Post one Slack message** to `#yeaboi-claude` via `cowork-scribe`, in **standard Markdown** —
   the connector is not Slack mrkdwn, and `cowork-scribe`'s Slack format block says why that
   distinction is the difference between a bold heading and an italic one. This is the shape,
   literally:

   ```
   🔒 **Security** (1 open)

   1. **[security][web-ux]** [Third-party CDN script (unpkg lenis) on all 20 docs pages, no SRI](https://github.com/dinho149/yeaboi.ai/issues/172)
      — a compromised CDN can inject JS into every docs page

   ───────────────────────────

   🐛 **Bugs** (12 open — top 3)

   1. **[bug][platform]** [auto-version.yml rejects claude\[bot\] PRs](https://github.com/dinho149/yeaboi.ai/issues/146)
      — blocks version bumps on every cowork PR, stalling #145, #156, #184

   2. **[bug][poker]** [concurrent finalize can double-write tracker points](https://github.com/dinho149/yeaboi.ai/issues/143)
      — can silently corrupt a team's point tracker

   3. **[bug][web-ux]** [share gate 500s when the invite code carries a trailing space](https://github.com/dinho149/yeaboi.ai/issues/139)
      — locks a remote teammate out of every share

   ───────────────────────────

   💡 **Feature candidates** (7 open — oldest 3)

   1. **[bug][standup]** [Standup export drops the last member on a 12-person team](https://github.com/dinho149/yeaboi.ai/issues/131)
      — reported 34 days ago, still open

   2. **[improvement][usage]** [Usage page should total the month, not just the week](https://github.com/dinho149/yeaboi.ai/issues/128)
      — reported 41 days ago, still open

   3. **[feature][general]** [Let me pin a mode as the default on launch](https://github.com/dinho149/yeaboi.ai/issues/119)
      — reported 52 days ago, still open
   ```

   Every section in it is listed in full to its own heading's promise: three items under
   `— top 3`, three under `— oldest 3`. That is the point of the count, and an example that
   promised twelve and showed two would be teaching the anti-pattern the heading rule forbids.

   The rules that shape it:

   - **At most 3 proposals per type**, ranked by the step-2 order. The cap is a rule, not an
     artefact of the heading template: it is what keeps a fourteen-bug morning from burying the one
     security find, and it is what fixes the thread at eighteen proposal replies. `cowork/README.md`
     states the same cap; if you change one, change both.
   - **Headings** are `<emoji> **<Section>** (<n> open — top 3)`. Drop the `— top 3` when the bucket
     is listed in full, because `(2 open — top 3)` reads as a promise the section did not keep. The
     emoji is fixed per section (table below) so a returning reader finds a section by shape before
     reading a word; it is an anchor, and picking a fresh one each morning defeats the point.
   - **Items are an ordered list**, numbering restarting at `1.` in every section. The ordinal is a
     position in today's message, nothing more — the issue number lives in the link and in the thread
     reply. Put a blank line between items: that is what makes the list *loose* and gives a two-line
     item room to read as one item. Lines that are not list items at all fold into a single
     paragraph, which is exactly how this message used to render — three bugs in one grey block.
   - **Line one** is the tag, then the linked title:
     `**[type][workstream]** [<title without its tag>](<issue url>)`. The link wraps the title —
     never a URL trailing in parentheses after it, which is what crammed the old body.
     Escape any `[` or `]` surviving *inside* the link text as `\[` `\]` — issue titles routinely
     carry `claude[bot]`. Balanced brackets are legal in CommonMark link text, so `claude[bot]`
     usually survives unescaped; what kills the link is an *unbalanced* bracket, or a title whose
     bracketed run happens to match a real reference definition. Escaping is how you stop having to
     work out which case today's title is. The tag sits *outside* the link for the same reason —
     outside, it is not link text at all and needs no escaping, which is also why the unescaped
     `**[bug][platform]**` in the example above is safe where the same characters inside the
     parentheses would not be. The tag comes from the issue verbatim — never add a second one, and never strip it
     because the heading appears to make it redundant: the tag is what `cowork-scribe`'s title
     contract guarantees, and it is what survives the line being quoted anywhere else. For an old
     issue whose title lacks the tag, prepend it from the issue's labels.
   - **Line two is the why-now clause**, indented under the title and prefixed `— `. One clause, not
     a sentence chain — it owns a line now, which makes a long one more tempting and no more
     readable. If it wraps, it is too long.

     A plain newline is enough to get the second line, because a Slack message has no paragraph
     model to collapse it into — the text is rendered with the breaks it was sent with. That is the
     same fact the divider rule below leans on from the other side. It is, though, the one part of
     this shape that depends on the connector rather than on Markdown: if a digest ever arrives with
     the why-now folded onto the title's line, end line one with a trailing `\` (a CommonMark hard
     break) rather than re-deriving the cause. Nothing checks this statically.
   - **A divider between every section** — `───────────────────────────`, typed box-drawing
     characters. Not Markdown `---`: Slack has no horizontal-rule element for a converter to map it
     onto, so it is dropped silently or rendered as three literal dashes. Characters always survive.

   The emoji, one per section, fixed:

   | Section | Emoji |
   |---|---|
   | Security | 🔒 |
   | Bugs | 🐛 |
   | Features | ✨ |
   | Improvements | ⚡ |
   | Chores | 🧹 |
   | Docs | 📖 |
   | Feature candidates | 💡 |
   | Marketing | 📣 |
   | Blocked | 🚧 |
   | Silent | 🔇 |
   | Calibration | 📊 |

   None of them is ✅ or ❌. Those two are the approval verbs, and a reader who meets one as
   decoration has to stop and work out whether it means something.

   Then, each under its own divider, in this order after the type sections. The first two carry a
   heading; the remainder line and the reminder deliberately do not, for the reasons given:

   - 💡 **Feature candidates** — the oldest 3 `feature-candidate` issues, same two-line item shape,
     with the age as the why-now. These came from users, so they are listed separately rather than
     ranked against scout finds, and by age rather than by impact: a reported bug that has waited a
     month is the fact worth surfacing.

     **Their tag is built from different labels, because they are filed by a different system.** A
     `feature-candidate` issue comes from the in-app feedback form (`src/yeaboi/feedback.py`), which
     titles it `[<Type>] <title>` and labels it `type:<kind>` + `area:<area>` — there is **no
     `workstream:` label on it**, so the proposal rule's "prepend it from the issue's labels" has
     nothing to prepend. Use `**[<type>][<area>]**` from those two labels, and strip the `[<Type>] `
     prefix off the title, since that prefix *is* the first half of the tag. The second half is an
     **area**, not a workstream — they are different vocabularies that happen to share most of their
     words (`usage` and `settings` are areas but live under the `tui-ux` workstream), and mapping
     one onto the other would be inventing a fact the issue does not carry.
   - 📣 **Marketing** — this week's draft link, if one exists and no earlier digest has carried it.
   - the count of everything else, **by workstream** — no heading, one line under its own divider,
     because it is a footnote to the sections above and a heading would give it their weight. The
     other axis, on purpose: the headings already carry the per-type counts, so a per-type remainder
     would only restate them; the per-workstream remainder is the one place a reader learns which
     surface the backlog is piling up on. Omit the line entirely when the sections above listed
     everything — a remainder of zero is not a fact anyone needs.
   - the three health sections from step 5 — ⏳ **Approved, no PR yet**, 🚧 **Blocked on an open
     PR** and 🔇 **Silent 21+ days** — sharing one divider, because they are three parts of one
     health report and splitting them reads as unrelated topics. Approved-with-no-PR leads: it is
     the only one of the three where a human already said yes, so it is the only one where the
     silence is the fleet's fault rather than the backlog's. Then, on Mondays only, 📊 **Calibration** from step 6 under a divider
     of its own.
   - last, under a final divider, the reminder that approval is ✅ on an item's thread reply
     (relayed within the hour — or `/cowork run slack-relay` for right now) or adding
     `claude-implement` on GitHub, and rejection is ❌ or closing the issue. Its own divider because
     it is instructions rather than content, and it should not read as one more section of backlog.
     The digest still names the verbs, because a reader who has forgotten them has nowhere else to
     look.

   Then **one reply in that message's thread per listed item** — every listed proposal and every
   `feature-candidate` line — formatted `#<issue-number> — <verbatim title> — <issue link>`, the
   number leading so `cron/slack-relay.md` can parse it. **Plain text: no list marker, no emoji, no
   embedded link, no bold** — none of the formatting above applies here, because this line is parsed
   before it is read. The thread replies are what make a single reaction mean a single issue; without
   them ✅ on the digest would be ambiguous across every item it lists. Per-type sections raise the
   ceiling to eighteen proposal replies plus three `feature-candidate` replies — a longer thread, not
   a second message, and the reply shape is a parsed contract that does not change with the volume.

   One **channel-level** message, never a second on the same day — the channel-noise rule holds;
   the per-item replies live inside its thread, not in the channel.

   That rule is about *this* routine. Two other routines post channel-level messages on the same
   day — `cron/agents-standup.md` in the morning and `cron/shipped-standup.md` at 18:00 — and
   neither is a violation: they carry what *happened*, not what needs deciding. "Never a second on
   the same day" keeps the digest from splitting itself across two messages, which is what makes ✅
   on a thread reply mean one issue; a different routine posting a different kind of thing does not
   touch that.

   Keep the split clean in both directions. **Never report a merge here** — the standup owns the
   record, and a proposal list that also announces shipped work stops reading as a list of
   decisions. **And never let the standup carry a proposal**, for the same reason in reverse. The
   division is what a reader relies on: this message is asking you something, the standup is
   telling you something.

   Note what this routine no longer competes with. Security, bug and chore finds go straight to a
   PR now (`house-rules.md`), so those three sections are empty on most days and the queue this
   digest reports is mostly `feature` and `improvement` — the finds that genuinely need a human.
   That shrinkage is behaviour, not an edit: the sections stay declared, and an empty one is
   omitted exactly as before.

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

5. **Report the health lines** — if any workstream has filed nothing in 21 days, say so in the
   digest. A silent scout is usually a broken scout, not a clean codebase.

   Separate the three ways work goes quiet, under three headings — ⏳ **Approved, no PR yet**,
   🚧 **Blocked on an open PR** and 🔇 **Silent 21+ days** — each an ordered list, one item per
   entry, in the same two-line shape as a proposal. These were one prose paragraph, which is why
   nobody read past the first name.

   The lines carry different facts, because the states are different facts:

   - **Approved, no PR yet** — an issue carrying `claude-implement` with no PR behind it.
     `gh issue list --label claude-implement --state open --json number,title` for the candidates,
     then per issue `gh pr list --search '<n> in:body' --state all --json number` to see whether one
     was ever opened. Line one is `**#<n>** <title>`; line two is `— approved <k> days ago, <state>`,
     where state is `no branch` or `branch pushed, no PR` — check
     `git ls-remote --heads origin 'refs/heads/feature/issue-<n>-*'` to tell those apart, because
     they fail differently: nothing started, versus a run truncated between its push and its
     `gh pr create`.

     **`<k>` is days since the label landed, not since the issue was filed.** Only the timeline
     carries that:
     `gh api repos/{owner}/{repo}/issues/<n>/timeline --jq '[.[] | select(.event == "labeled" and
     .label.name == "claude-implement")] | last | .created_at'`. `createdAt` from `gh issue list` is
     the filing date, which for a proposal approved after a fortnight in the queue overstates the
     delay by that fortnight — and the "more than a day is a broken lane" reading below depends on
     the number meaning what it says.

     This is the only section here about an issue rather than a workstream, and it exists because
     that window had no owner. The approval is the moment a proposal stops being a question, and
     from then on nothing watches it: step 4 above is forbidden from ageing these out, so a build
     that never started leaves an issue no routine will ever mention again. Issue #172 sat in this
     state from 2026-08-09 — the implement job it triggered exited green having written nothing —
     and the fleet's only report of it was the same three lines of Slack it had already posted.
     Anything listed here for more than a day is a broken lane, not a slow one.
   - **Blocked** — line one is the workstream and its linked PR, `**<workstream>** [PR #123 —
     <title>](<pr url>)`; line two is `— ` and what is actually holding that PR up, from the
     `pr_feedback.py` run below (red CI, or *n* unanswered findings).
   - **Silent** — a silent workstream has no PR and nothing blocking it, which is the whole problem,
     so there is nothing to link. Line one is `**<workstream>**` and the date it last filed
     (`— last filed <YYYY-MM-DD>`, or `never`); line two is `— ` and the number of days that is,
     which is the number the 21-day line is about. Do not invent a blocker to fill the shape: "no
     open PR, nothing filed in 34 days" is the finding.

   Run `gh pr list --label cowork --state open --json number,labels,createdAt,url` first: a
   workstream with an open PR is **blocked**, not silent — it is forbidden from scouting until that
   PR merges. Name it and its PR, and do not count it against
   the 21-day line. Silence with no open PR is the case worth worrying about.

   Say *what* is blocking each one, because the two causes need different people. Run
   `uv run python scripts/pr_feedback.py --pr <n>` per blocked PR: red CI is a machine problem an
   agent can pick up, while unanswered review feedback is waiting on a judgment somebody has to make.
   A workstream stalled three weeks on two unanswered findings reads as "blocked on PR #123" without
   this, which is indistinguishable from a slow build and gets treated like one.

6. **Report the calibration line** under a 📊 **Calibration** heading — for each workstream, the
   approval rate over the last 90 days:
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
