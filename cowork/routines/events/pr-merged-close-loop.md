# PR merged — close the loop

**Trigger** — GitHub event, pull request `closed`
**Filters** — `is_merged` true only. A closed-unmerged PR does nothing here.
**Model** — `fast` ([models.md](../../models.md)) — every step reads a field and writes a field

This is where DoD items 1 (final state), 8, and 9 are satisfied. Everything below runs through
`cowork-scribe`.

## Run

1. `gh pr view <n> --json title,body,labels,mergedAt,author,files` and `gh pr diff <n>`.

2. **Linear** — find the ticket from the PR body link, or by searching team `Yeaboi` for the branch
   name. Move it to Done and comment with the merge commit. If no ticket exists (a human shipped
   without one), create it in Done rather than skipping — the record matters more than the order.

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
