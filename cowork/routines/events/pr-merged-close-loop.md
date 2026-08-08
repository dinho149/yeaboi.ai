# PR merged — close the loop

**Trigger** — GitHub event, pull request `closed`
**Summary** — on merge: Linear to Done, the Slack ship note, the Notion page
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
not a wasted run: it moves a Linear ticket to Done, posts a ship note to `#yeaboi-claude` and writes
a Notion page for work that was thrown away.

This is where DoD items 8 and 9 are satisfied, and item 1's final state is verified (and repaired
when the `Closes YEA-NN` magic word missed). Everything below runs through `cowork-scribe`.

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

4. **Slack** — one message to `#yeaboi-claude`, led by the same tag the proposal carried:
   `[type][workstream] <what shipped, one sentence>`, then the PR link, the Linear link, and the
   Notion link when there is one. Take the type from the PR's own `type:*` label — step 1 already
   fetched the labels, and both lanes put it there (sweep step 5 for auto, `claude.yml` step 9 for
   approved proposals). If the PR has none (a human shipped without one), drop the type bracket
   rather than guessing — the scribe never invents a classification.

5. If any of the three fails, complete the others and post what failed in the Slack message. A dead
   Notion connector must not swallow the ship note.

## Stop conditions

- One Slack message per merged PR. If several merge at once, still one message each — never batch,
  because these are the record, not a digest.
- Never reopen, revert, or comment on the PR itself.
