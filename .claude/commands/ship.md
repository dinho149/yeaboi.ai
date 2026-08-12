---
description: Verify (independent review + full tests), commit, push, and open a PR for the current branch
---

Ship the current feature branch. Arguments (optional): $ARGUMENTS — may include `auto-merge` to enable auto-merge for low-risk changes (docs/chores/small fixes only).

Follow these steps **in order**. If any step fails, stop, report what failed, and fix it before continuing. Never skip the verification steps.

The contract this branch must satisfy is `cowork/definition-of-done.md` — the same ten items the cowork routines ship against. Read it; the steps below are how it is executed interactively.

1. **Sanity check** — run `git branch --show-current`. If on `main`, stop: create a feature branch first.

   **Linear (DoD item 1)** — spawn `cowork-scribe` to find the ticket for this branch on team `Yeaboi`, or create one if none exists — labelled `workstream:<name>` and in state **In Progress** (a found ticket sitting in Backlog/Todo is moved to In Progress: work on it is starting now). Keep the identifier (`YEA-NN`) for the PR body. Never write to Linear inline; the scribe owns every outbound format.

2. **Independent verification (fresh context, no author bias)** — spawn the `code-reviewer` subagent (defined in `.claude/agents/code-reviewer.md`) at the `deep` tier (`cowork/models.md`). Give it ONLY: (a) the output of `git diff main...HEAD`, (b) a one-paragraph description of what this branch was supposed to do — NOT this conversation's history. Its checklist (spec fit, skill-based conventions, correctness) lives in the agent definition. Resolve every finding it reports at `blocker` or `should-fix` severity before proceeding (fix it, or explain in the PR body why it's intentionally not addressed).

3. **Full test gate (DoD items 2–7)** — run `make test`, `make lint`, and `make security`. All must pass; `make test-fast` is not enough at ship time. If the branch touches `frontend/`, run `make web` and stage the rebuilt `src/yeaboi/web/static/`. If it adds a capability, confirm its `CAPABILITIES` row and `FeatureTip` exist.

4. **Commit** — stage the relevant changes and commit using repo conventions: lowercase imperative message (e.g. "add streaming output"), ending with the Co-Authored-By trailer from CLAUDE.md's Git Conventions.

5. **Push + PR** — `git push -u origin <branch>`, then `gh pr create` against `main` with:
   - Title: same style as the commit message.
   - Body: a Summary section (what and why), a Test plan section (what was run), the line `Closes YEA-NN` (the magic word is load-bearing: it is what makes the Linear GitHub integration attach the PR and move the ticket to Done on merge — a bare Linear URL does neither), a line for any DoD item that genuinely does not apply, and the standard "🤖 Generated with Claude Code" footer.
   - Then have `cowork-scribe` attach the PR to the Linear ticket and move it to **In Review**.

6. **Auto-merge (only if `auto-merge` was passed)** — confirm the change is genuinely low-risk (docs, chore, small fix; no `src/yeaboi/agent/`, schema, or workflow changes), then run `gh pr merge --auto --squash`. If it is not low-risk, say so and skip this step. The `Closes YEA-NN` line from step 5 carries the ticket to Done when the merge lands — no extra Linear step here.

7. **Hand off the review loop (DoD item 10)** — say plainly that the PR is **not done yet**, and why: `claude-review.yml` fires on `workflow_run` *after* CI succeeds, which is minutes from now, so at this moment its review does not exist. The `code-reviewer` pass in step 2 is not it — that one had no CI results, no diff-on-`main` context, and nobody else's eyes.

   Name the follow-up: `/pr-feedback <n>` once CI is green, or `/babysit-prs` across every open PR. Do not wait for it here; a `/ship` that blocks for ten minutes gets run less often.

   **On a branch you are shipping by hand, that review is advisory and the `pr-feedback` status stays green.** It runs once, it posts what it found, and it does not hold the merge — you are the person it would otherwise be arguing with. Read it anyway; that is the whole point of it existing. The gate enforces on the unattended lane (`cowork/…`, `feature/issue-…`, triage and sentinel branches, or anything labelled `cowork`), where nobody is on the other end.

   So step 6 does need the judgement it asks for: `gh pr merge --auto` waits on the required checks, and on a hand-shipped branch `pr-feedback` will be green whatever the review says. An auto-merge can outrun the review here — which is why step 6 is limited to changes that are genuinely low-risk.

8. **Report** — output the PR URL and a one-line status, ending with what is still outstanding: the pending review, and items 8–9.

DoD items 8 (Notion) and 9 (Slack) are **not** done here — the `pr-merged-close-loop` cowork routine fires them on merge, so a branch that never merges never announces itself. The Linear → Done transition on merge rides the `Closes YEA-NN` line via the GitHub integration; the routine only verifies and repairs it. Item 10 (review feedback) is not done here either, for the plainer reason that the feedback does not exist yet — `/ship` opens the PR, and answering what comes back is a separate sitting.
