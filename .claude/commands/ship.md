---
description: Verify (independent review + full tests), commit, push, and open a PR for the current branch
---

Ship the current feature branch. Arguments (optional): $ARGUMENTS — may include `auto-merge` to enable auto-merge for low-risk changes (docs/chores/small fixes only).

Follow these steps **in order**. If any step fails, stop, report what failed, and fix it before continuing. Never skip the verification steps.

The contract this branch must satisfy is `cowork/definition-of-done.md` — the same nine items the cowork routines ship against. Read it; the steps below are how it is executed interactively.

1. **Sanity check** — run `git branch --show-current`. If on `main`, stop: create a feature branch first.

   **Linear (DoD item 1)** — spawn `cowork-scribe` to find the ticket for this branch on team `Yeaboi`, or create one if none exists. Never write to Linear inline; the scribe owns every outbound format.

2. **Independent verification (fresh context, no author bias)** — spawn the `code-reviewer` subagent (defined in `.claude/agents/code-reviewer.md`) at the `deep` tier (`cowork/models.md`). Give it ONLY: (a) the output of `git diff main...HEAD`, (b) a one-paragraph description of what this branch was supposed to do — NOT this conversation's history. Its checklist (spec fit, skill-based conventions, correctness) lives in the agent definition. Resolve every finding it reports at `blocker` or `should-fix` severity before proceeding (fix it, or explain in the PR body why it's intentionally not addressed).

3. **Full test gate (DoD items 2–7)** — run `make test`, `make lint`, and `make security`. All must pass; `make test-fast` is not enough at ship time. If the branch touches `frontend/`, run `make web` and stage the rebuilt `src/yeaboi/web/static/`. If it adds a capability, confirm its `CAPABILITIES` row and `FeatureTip` exist.

4. **Commit** — stage the relevant changes and commit using repo conventions: lowercase imperative message (e.g. "add streaming output"), ending with the Co-Authored-By trailer from CLAUDE.md's Git Conventions.

5. **Push + PR** — `git push -u origin <branch>`, then `gh pr create` against `main` with:
   - Title: same style as the commit message.
   - Body: a Summary section (what and why), a Test plan section (what was run), the Linear link, a line for any DoD item that genuinely does not apply, and the standard "🤖 Generated with Claude Code" footer.
   - Then have `cowork-scribe` attach the PR to the Linear ticket.

6. **Auto-merge (only if `auto-merge` was passed)** — confirm the change is genuinely low-risk (docs, chore, small fix; no `src/yeaboi/agent/`, schema, or workflow changes), then run `gh pr merge --auto --squash`. If it is not low-risk, say so and skip this step.

7. **Report** — output the PR URL and a one-line status.

DoD items 8 (Notion) and 9 (Slack) are **not** done here — the `pr-merged-close-loop` cowork routine fires them on merge, so a branch that never merges never announces itself.
