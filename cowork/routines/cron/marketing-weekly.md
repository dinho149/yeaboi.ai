# marketing weekly

**Trigger** — cron `0 8 * * 6` (Saturdays 08:00 UTC)
**Summary** — drafts this week's marketing subject from the rotation, as a Notion page
**Workstream** — [`workstreams/marketing.md`](../../workstreams/marketing.md)
**Model** — `deep` ([models.md](../../models.md)) — this routine drafts inline instead of delegating,
so the prose *is* the output

This routine does **not** follow the sweep procedure — it writes no code and opens no PR.

It ran daily until the arithmetic was written down: seven subjects on a weekday rotation re-drafts
every subject every seventh day, forever, against a codebase that moves far slower than that. Its own
stop condition would then fire most mornings — so the most expensive routine in the fleet would spend
a `deep` read of a whole mode to conclude it had nothing to say. Weekly gives each subject seven
weeks of real change to describe, and Saturday keeps it clear of every sweep.

## Run

1. Read [`workstreams/marketing.md`](../../workstreams/marketing.md) and take this week's subject
   from the rotation table. The rotation advances one row per run: find the most recent
   `Draft — …` page under 🤙 yeaboi and take the row after that subject.
2. Read the source files and the docs page listed for that subject. Read the **code first**; the
   docs page is the claim, the code is the truth.
3. Check `gh issue list --label "workstream:marketing" --state closed --limit 30` and the last 8
   Notion drafts. Do not re-draft an angle already covered.
4. Draft 600–1,000 words: one concrete problem, one concrete mechanism, honest about limits, no
   invented numbers or quotes.
5. Spawn `cowork-scribe` to create it under 🤙 yeaboi as `Draft — <subject> — <YYYY-MM-DD>`.
6. If the read turned up a genuine contradiction between the docs and the code, file **one**
   `cowork:proposal` issue for the owning workstream. That is the only issue this routine may file.

Do not post to Slack — the digest carries the draft link.

## Stop conditions

- Nothing new to say about this week's subject is a valid outcome. Write nothing rather than padding.
  Advance the rotation anyway, so one quiet subject does not stall the other six behind it.
- Never edit a file in the repo.
