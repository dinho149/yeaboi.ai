---
description: Answer the unresolved review feedback on a PR and clear the pr-feedback gate
---

Work through the review feedback on a pull request until nothing is unanswered.
Arguments (optional): $ARGUMENTS — a PR number; defaults to the current branch's PR.

Five things comment on a PR here and, until the `pr-feedback` gate existed, nothing
ever read any of it back. This is the procedure that reads it back. It is the one
place that job is written down; `/ship`, `/babysit-prs` and `cowork/sweep-procedure.md`
all point here rather than restating it.

1. **Look** — `make pr-feedback PR=<n>` (or `uv run python scripts/pr_feedback.py --pr <n> --json`
   when you want to iterate). It prints every open item: unanswered findings per
   producer, unresolved human threads, and a requested-changes review.

   Three states are not failures and need no work: `pending` means the review has
   not run yet (wait for CI); a draft or a Dependabot PR is out of scope by design;
   and "Claude Review never posted a verdict" means the *workflow* did not run —
   check `gh run list --workflow "Claude Review"` before touching the code, because
   nothing in the diff will fix it.

2. **Read what was actually said** — `gh pr view <n> --comments`. The verdict tells
   you how many findings are open; only the comments tell you what they are. Never
   answer a finding you have not read.

3. **Decide fix or answer, per item.** Fix when the finding is right. Answer when it
   is wrong, already handled elsewhere, or genuinely out of scope for this PR. Both
   are legitimate. Fixing everything to turn a check green is the worse failure: it
   lands unrequested changes on a branch someone already reviewed, and it teaches
   the reviewer that findings are cheap.

4. **Fixes** — minimal change, `make test` + `make lint`, commit with the repo's
   conventions and push to the same branch. Nothing else is needed: the push re-runs
   CI, which re-runs the review, which reports `open=0`, and the gate clears itself.

5. **Answers** — one comment per producer covering every finding you are not fixing,
   each with a one-line reason, ending with that producer's marker on its own line:

   ```
   <!-- addressed: claude-review -->
   ```

   The marker only counts if the comment is **newer than the verdict it answers**, so
   post it after the review, not before. If you push again afterwards, the next review
   pass will have read your reply and should stop reporting the finding at all.

   **On an unattended PR this step does not exist.** A cowork PR, a `feature/issue-N-…`
   branch, a triage or sentinel branch — `scripts/pr_feedback.py` refuses an ack from
   the PR's own author there, so the marker is inert and the gate stays red. That is
   deliberate: the account that wrote the change also has write access, so without it
   the applicant would be holding the key. Fix the finding (step 4) or hand the PR back
   to a human as a proposal. Only step 4 clears an unattended PR.

6. **Human threads** — reply in the thread, then resolve it:

   ```bash
   gh api graphql -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{isResolved}}}' -F id=<threadId>
   ```

   The thread ids are in the `--json` output. Reply first, always.

7. **Confirm** — re-run step 1 and report the final verdict with the PR URL.

For several PRs at once, or to run this unattended, use `/babysit-prs fix` — it spawns
the `pr-responder` subagent (`.claude/agents/pr-responder.md`) per PR in its own worktree.

## Rules

- **Never resolve a thread you did not answer**, and never write an `<!-- addressed: -->`
  marker without the reply that justifies it. The marker means "there is a reason below".
  Closing feedback silently is the behaviour this gate exists to stop.
- **A machine never answers its own review.** On an unattended PR the only way to clear a
  finding is to fix it; disagreement is escalated to a human, never asserted. This one is
  enforced in code rather than trusted, because it is the single rule standing between an
  unattended merge and no review at all.
- **The `feedback-override` label is a last resort and a human's call** — for a gate that
  has genuinely gone wrong (a review that errored, a producer that changed format), not
  for feedback that is inconvenient. It is recorded in the sticky comment, so an
  overridden PR never looks like a clean one.
- If a finding is real but beyond this PR, say so in the reply and file it as an issue
  with the owning `workstream:` label rather than dropping it.
