# PR merged — close the loop

**Trigger** — GitHub event, pull request `closed`
**Summary** — on merge: verify the Linear ticket reached Done, and write the Notion page
**Filters** — `is_merged` true only. A closed-unmerged PR does nothing here — and **step 1 is the
only thing that enforces it**, see below.
**Model** — `fast` ([models.md](../../models.md)) — every step reads a field and writes a field

```json webhook
{"source": "github", "events": ["pull_request"], "filter": {"actions": ["closed"]}}
```

**The block cannot express "merged".** The registered filter is `closed`, which GitHub sends for a
merged PR *and* for one somebody abandoned; `is_merged` is not a key this API's filter accepts, and
it never echoes a stored filter back, so nothing downstream can confirm what was registered either.
Every step below therefore runs on both, and step 1 is what tells them apart. Getting this wrong is
not a wasted run: it moves a Linear ticket to Done and writes a Notion page for work that was
thrown away.

This is where DoD item 8 is satisfied and item 1's final state is verified (and repaired when the
`Closes YEA-NN` magic word missed). Everything below runs through `cowork-scribe`.

**It posts nothing to Slack.** It used to: one ship note per merged PR, and the stop conditions
below forbade batching them, so a four-merge afternoon was four notifications. That is now one line
each in `cron/shipped-standup.md`, which reports the day's merges together with the pre-release they
landed in. The record did not go away — it stopped being an interruption. DoD item 9 names the
standup for exactly this reason.

## Run

1. `gh pr view <n> --json title,body,labels,mergedAt,author,files` and `gh pr diff <n>`.

   **If `mergedAt` is null, stop here.** Nothing else in this routine runs, nothing is posted, and
   nothing is written — the PR was closed without merging, and there is no loop to close. Exit
   silently: a closed-unmerged PR is a normal event and not worth a message.

2. **Linear** — verify and repair, not blind-write. Find the ticket from the `Closes YEA-NN` line
   in the PR body, the PR attachment, or by searching team `Yeaboi` for the branch name. The magic
   word normally already moved it: the GitHub integration closes a `Closes`-linked ticket on merge.
   If it is already Done, just comment with the merge commit. If it is not (the line was missing or
   typo'd), move it to Done and comment. If no ticket exists (a human shipped without one), create
   it in Done rather than skipping — the record matters more than the order.

3. **Notion** — only for user-facing change. Decide from the diff: does anything change what a user
   sees, types, or receives? If yes, create or update the page under 🤙 yeaboi
   (`3b01bf92-1b06-8163-af24-ea0a77641e17`) covering the affected mode or integration. Prefer
   updating an existing page over creating a near-duplicate; search first.
   Internal refactors, test-only changes, and dependency bumps get no Notion page.

4. If either step fails, complete the other and **say so on the Linear ticket** — a dead Notion
   connector must not swallow the record of the merge, and there is no Slack message left to carry
   the failure. The daily standup reads merged PRs from `gh`, not from this routine, so a failure
   here never hides the merge itself.

5. **Check in.** Whatever happened above — including nothing — close the run by following
   [check-in.md](../../check-in.md). It is the last thing you do.

## Stop conditions

- **Never post a Slack message.** The day's merges go out together in `cron/shipped-standup.md`; a
  message here would duplicate a line the reader is getting anyway, and per-merge notifications are
  the noise this fleet was reshaped to stop. The check-in in step 5 is not one: it is a thread reply
  under 📅, and it is what the old per-merge ship note was replaced *by*, not a return of it. A
  merge landing before 05:45 UTC has no 📅 thread yet and checks in to the run log alone — see
  [check-in.md](../../check-in.md).
- Never reopen, revert, or comment on the PR itself.
