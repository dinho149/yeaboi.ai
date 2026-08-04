---
description: Check all open PRs, surface CI failures, and spawn fix agents for red ones
---

Babysit the open pull requests so finished work doesn't pile up. Arguments (optional): $ARGUMENTS — `fix` to auto-spawn fix agents, otherwise report-only.

1. **Survey** — `gh pr list --state open` then `gh pr checks <number>` for each. Build a table: PR, branch, CI status (green/red/pending), review status, mergeable.

2. **Green PRs** — list them as ready to merge. Do NOT merge anything yourself unless the PR carries the `auto-merge` label (then `gh pr merge --auto --squash` is allowed).

3. **Red PRs** — for each failing PR, fetch the failing run's log (`gh run view <run-id> --log-failed`) and summarize the root cause in one line.
   - If `fix` was passed: for each red PR, create a headless worktree (`make wt-headless NAME=fix-pr-<number>`) and spawn the `pr-fixer` subagent (defined in `.claude/agents/pr-fixer.md`) at the `standard` tier (`cowork/models.md`) in the background, passing the branch name, the failure summary, the failing log excerpt, and the worktree path. Its procedure (reproduce → minimal fix → verify → push to same branch) lives in the agent definition. Track the agents, report when they finish, and remove each worktree (`make wt-rm`) once its PR is green.
   - Otherwise: just report the failures with their causes.

4. **Stale PRs** — flag PRs behind `main` by many commits or inactive for days; suggest `/sync-main` on their worktree.

5. **Report** — end with a compact status table and the list of actions taken or recommended.
