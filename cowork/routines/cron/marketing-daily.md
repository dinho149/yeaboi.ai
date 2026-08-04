# marketing daily

**Trigger** — cron `0 8 * * *` (daily 08:00 UTC)
**Workstream** — [`workstreams/marketing.md`](../../workstreams/marketing.md)
**Model** — `deep` ([models.md](../../models.md)) — this routine drafts inline instead of delegating,
so the prose *is* the output

This routine does **not** follow the sweep procedure — it writes no code and opens no PR.

## Run

1. Read [`workstreams/marketing.md`](../../workstreams/marketing.md) and take today's subject from
   the weekday rotation table.
2. Read the source files and the docs page listed for that subject. Read the **code first**; the
   docs page is the claim, the code is the truth.
3. Check `gh issue list --label "workstream:marketing" --state closed --limit 30` and the last 14
   Notion drafts. Do not re-draft an angle already covered.
4. Draft 600–1,000 words: one concrete problem, one concrete mechanism, honest about limits, no
   invented numbers or quotes.
5. Spawn `cowork-scribe` to create it under 🤙 yeaboi as `Draft — <subject> — <YYYY-MM-DD>`.
6. If the read turned up a genuine contradiction between the docs and the code, file **one**
   `cowork:proposal` issue for the owning workstream. That is the only issue this routine may file.

Do not post to Slack — the digest carries the draft link.

## Stop conditions

- Nothing new to say about today's subject is a valid outcome. Write nothing rather than padding.
- Never edit a file in the repo.
