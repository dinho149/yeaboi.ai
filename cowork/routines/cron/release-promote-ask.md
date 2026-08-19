# release promote ask

**Trigger** — cron `0 9 * * 1` (Mondays 09:00 UTC, after the digest)
**Summary** — reminds the human, once a week, that fleet work is waiting for a release batch
**Workstream** — none; this routine is the release channel's one standing reminder.
**Model** — `fast` ([models.md](../../models.md))

**The fleet's PRs do not merge; a human's batch does.** Fleet PRs accumulate open and gate-green,
and reach `main` — and users — only inside a `batch/<date>` PR that a human assembles with
`make batch-assemble`, hand-tests, and merges ([release-signoff.md](../../release-signoff.md)).
None of that is this routine's to do. **This routine reads and reminds, and that is all it can
do**: its grant holds `gh pr list` and `gh pr view` and no write verb at all — no `gh pr merge`,
no `gh pr ready`, no `gh pr edit`, no labels. The routine that asks may not answer, and under the
batch model the answer is a merge.

## Run

1. **Read what is waiting.**

   ```bash
   gh pr list --base main --state open --limit 100 --json number,title,labels,headRefName,isDraft,statusCheckRollup
   ```

   A PR is *fleet-lane* if it carries the `cowork` label or its head starts with an unattended
   prefix (`cowork/`, `feature/issue-`, `security/codeql-triage`, `ci-sentinel/` — the same list
   `scripts/release_lane.py` reads). A fleet PR is *waiting* if it is not a draft and every check
   on it reports success. Count the waiting ones; note the ones that are fleet-lane but red or
   draft separately — they are not ready and are not counted, but a red one is worth one line.

2. **Read the open batch, if any** — `gh pr list --label release:promotion --state open`.

3. **Decide whether to post.**

   - **Nothing waiting and no batch open → stop. Post nothing.** That is the ordinary state of a
     quiet week, and a weekly message that says "nothing to do" trains everyone to ignore the
     channel.
   - **A batch is already open** → one short reminder that it exists and is waiting on the
     hand-test, with its link. Do not restate its contents; the PR body carries them.
   - **Fleet PRs are waiting and no batch is open** → the reminder below.

4. **Remind, through `cowork-scribe`** — one message to `#yeaboi-claude`:

   ```slack
   🏷️ **Release batch waiting** — 5 fleet PRs are gate-green and unshipped

   - fix the retro export CSP (#301)
   - integration(gitlab): wizard step (#303)
   - …

   Assemble and test when it suits: `make batch-assemble`, then `make beta-check`.
   Your merge of the batch PR is what ships them — see cowork/release-signoff.md.
   ```

   Every number and title is copied from the `gh pr list` you just read, never restated from
   memory. No ✅/❌ footer and no parsed thread reply: there is nothing a reaction could safely
   do here — the next step is a local command only a human can run.

5. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do.

## Stop conditions

- **Nothing waiting → nothing posted.** Step 3 is the whole gate.
- **Never assemble, label, ready, or merge anything.** The grant already withholds every verb;
  this line is here so the closed allowlist covers the intent and not just the mechanics.
- **Never comment on the batch PR.** Its comments are the sign-off record —
  `scripts/beta_signoff.py` reads maintainer comments for `<!-- tested: … -->` markers — and a
  routine's noise in that stream is noise in an authorization channel, even though the
  `authorAssociation` filter would discard it.
- **One message, once.** A batch that waits two weeks gets one reminder each Monday, not an
  escalation.
