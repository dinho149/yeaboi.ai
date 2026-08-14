# PR opened — DoD audit

**Trigger** — GitHub event, pull request `opened` and `synchronized`
**Summary** — audits an opened PR against the ten-item Definition of Done
**Filters** — skip drafts; skip authors `dependabot[bot]` and `github-actions[bot]` unless the PR is labelled `cowork`
**Model** — `standard` ([models.md](../../models.md))

```json webhook
{"source": "github", "events": ["pull_request"], "filter": {"actions": ["opened", "synchronize"]}}
```

Keep that filter on **one line**. `scripts/cowork_setup.py` parses it with a single-line regex, and a
wrapped second line is silently dropped, taking the carve-out with it.

The `**Filters**` line and the `webhook` block are not two spellings of the same thing, and neither
one makes the other redundant. The block is the coarse gate the API applies before this routine wakes
up; the line is the judgement this routine applies once it has — there is no filter DSL that expresses
"unless the PR is labelled `cowork`", and a block that pretended to would silently drop the carve-out
the same way a wrapped line does. So **the `**Filters**` line is enforced by step 1 and nowhere
else** — the registered block gates on the action only, and the API never echoes a stored filter back,
so nothing can confirm even that much.

The carve-out exists because the `claude.yml` implement job opens PRs unattended from an issue a
human approved. If the action pushes as a bot, the authorship filter would drop the process audit on
exactly the PRs nobody watched being written. `claude-review.yml` carries the same one.

`claude-review.yml` already reviews the *code*. This checks the *process* — whether the ten items in
[definition-of-done.md](../../definition-of-done.md) are actually met.

## Run

1. `gh pr view <n> --json title,body,files,labels,author,isDraft` and `gh pr diff <n>`.

   **Apply the `**Filters**` line here, before anything else.** Exit silently — no comment, no
   issue — when `isDraft` is true, or when the author is `dependabot[bot]` or `github-actions[bot]`
   *and* the PR does not carry the `cowork` label. Nothing upstream does this: the webhook fires on
   every `opened` and `synchronize`, and auditing a draft against the ten items is a review of work
   somebody has explicitly said is not finished.
2. Walk the DoD. For each item, decide **met / unmet / not applicable**, using evidence from the diff
   and not from the PR description's claims:
   - **1 Linear** — a `Closes YEA-NN` line in the body: the magic word is what makes the Linear
     GitHub integration attach the PR and close the ticket on merge; a bare Linear URL does neither.
     Judge from the body only — the ticket's own state is not checked here (the scribe moves it to
     In Review just after the PR opens, so reading it on the `opened` event would race the writer)
   - **2 Tests** — new/changed functions in the diff that have no corresponding test change
   - **3–4 Lint + security** — the CI `lint` and `security` job conclusions
   - **5 Surface parity** — a new capability with no `CAPABILITIES` row, or a card/tool/flag added
     without one
   - **6 Observability** — a new user action with no `logger.info()`, or a hardcoded `~/.yeaboi`
   - **7 Web bundles** — `frontend/` changed but `src/yeaboi/web/static/` did not
   - **8–9 Notion + Slack** — **always "pending merge"**, never unmet. They are done by
     `pr-merged-close-loop`.
   - **10 Review feedback** — **always "pending merge"**, never unmet. The feedback does not exist
     yet when this runs; the `pr-feedback` status is what judges it, and this routine is one of the
     things it judges.
3. Post **one** comment via `cowork-scribe`: a checklist with a one-line reason for every unmet item
   and the exact file or command that would settle it. If everything is met or pending, post
   `DoD: all items met or pending merge.` and nothing more.
4. **End the comment with this exact line, on its own:**

   ```
   <!-- cowork-dod open=N -->
   ```

   where N counts the items you marked **unmet** — items you marked *met* or *pending merge* count
   zero, so a clean audit is `open=0`. The marker is both the anchor for step 5 and the verdict
   `scripts/pr_feedback.py` counts: while N is above zero and unanswered, the `pr-feedback` commit
   status blocks the merge. A comment posted without the `open=` part reads as *no verdict at all*
   rather than as a pass — the gate will not let a routine clear it by staying quiet.
5. On `synchronized`, **edit the existing comment** rather than adding another. Find it by its
   `<!-- cowork-dod` marker.

6. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do.

## Stop conditions

- This routine is advisory. Never request changes, never block, never fail a check, never merge.
  The `open=` count in step 4 is not an exception to that: you report a number, and
  `.github/workflows/pr-feedback.yml` decides what it means. Never inflate it to force attention,
  and never zero it to unblock someone.
- Never label a PR `claude-implement` or apply any label at all.
- Judge the diff, not the author. No commentary on style or approach — that is `claude-review.yml`'s job.
