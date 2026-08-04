# daily digest

**Trigger** — cron `15 8 * * *` (daily 08:15 UTC, after marketing)
**Workstream** — none; this routine spans all fifteen.
**Model** — `standard` ([models.md](../../models.md))

The single decision point. It is the only routine that posts proposals to Slack.

## Run

1. **Collect** — `gh issue list --label cowork:proposal --state open --limit 100 --json
   number,title,labels,createdAt,body`.

2. **Rank across workstreams**, not within them. Impact over effort, with a deliberate thumb on the
   scale for: anything security-labelled, anything that unblocks another workstream, and anything a
   user would notice. Two items from the same workstream should not both be in the top three unless
   they genuinely earn it — breadth beats depth in a digest.

3. **Post one Slack message** to `#yeaboi-claude` via `cowork-scribe`:
   - top 5 proposals, one line each: workstream · title · why now · issue link
   - the count of everything else, by workstream
   - today's marketing draft link, if one was created
   - the reminder that approval is adding `claude-implement` to an issue

   One message. Never a thread per item, never a second message on the same day.

4. **Age out** — close any `cowork:proposal` issue open more than 14 days with the comment
   "closed unapproved after 14 days — re-file if still relevant". Never touch an issue that carries
   `claude-implement`, and never close one a human has commented on.

5. **Report the health line** — if any workstream has filed nothing in 21 days, say so in the digest.
   A silent scout is usually a broken scout, not a clean codebase.

## Stop conditions

- No open proposals and no marketing draft: post nothing at all. A digest that says "nothing today"
  every day trains everyone to ignore the channel.
