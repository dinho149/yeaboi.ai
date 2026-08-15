---
description: Rebase the current worktree branch on latest main and re-verify
---

Bring the current feature branch up to date with `origin/main`.

1. Run `git branch --show-current`. If on `main`, just run `git pull --ff-only` and stop.
2. `git fetch origin`. Report the drift: `git rev-list --count HEAD..origin/main`.
3. If the working tree is dirty, stash first (`git stash push -u -m "sync-main autostash"`) and remember to pop at the end.
4. `git rebase origin/main` — **`origin/main`, never local `main`**, which in a worktree is routinely
   several commits behind and would rebase you onto a base that no longer exists upstream.
5. Resolve conflicts with the playbook below. Pop the stash if one was created, resolving the same way.
6. Re-verify on the new base: `make test-scoped` + `make lint`. If the rebase touched **any generated
   file** in the playbook, run `make ship-gate` instead — `test-scoped` cannot see a stale bundle, a
   stale wire fixture or a wheel that lost its static tree.
7. Report: how many commits the branch was behind, every conflict and how it was resolved, and the
   verification result.

## Conflict playbook

The old rule here was "prefer `main`'s version for files this branch didn't intentionally change".
For every **generated** file in this repo that is wrong: *both* sides are stale, and the file has to
be rebuilt rather than chosen. Taking either side of a minified bundle produces a tree that merges
green and reds `make web-check` on the next run.

**The rows below name a side by what it is, never by `--ours`/`--theirs`.** Those two flags
*invert* under rebase — `git rebase origin/main` replays your commits onto upstream, so `--ours` is
`origin/main` and `--theirs` is your own work, the opposite of what they mean in a merge. "Take the
upstream side" is unambiguous in both; "take theirs" is a coin flip.

| Conflicting path | Resolution |
|---|---|
| `src/yeaboi/web/static/**` | Never hand-resolve, never a `union` merge driver — it produces silently corrupt JS. `git checkout --theirs -- src/yeaboi/web/static && make web && git add src/yeaboi/web/static` (CLAUDE.md, Front End). Which side the flag picks does not matter here — `make web` overwrites it either way; the flag is only there to get git out of the conflicted state |
| `frontend/src/test/fixtures/**` | Take the upstream (`origin/main`) side, then `uv run pytest tests/unit/test_web_wire_shapes.py` regenerates them; commit what it wrote |
| `tests/unit/__snapshots__/*.ambr` | Take the upstream side, then `make snapshot-update` |
| `pyproject.toml` version line, `src/yeaboi/changelog_data.json` | Keep **`origin/main`'s** version and changelog, and drop your bump entirely. `auto-version.yml` re-bumps on the PR branch, so your side is redundant; and the changelog is prepend-only, which makes every pair of release-worthy PRs collide here by construction |
| `uv.lock` | Keep **`origin/main`'s**, then `uv lock`. Note the root `version` line tracks `pyproject.toml`, and `auto-version.yml` does *not* update it — so a disagreement here is expected, not a mistake to preserve |
| `CURRENT_SCHEMA_VERSION` | Renumber **yours** to max+1 in all four places: `sessions.py` + its inline history comment, `go/internal/agentwatch/store.go` + its comment, and the two hard-coded `== N` assertions in `tests/unit/test_agentwatch_store.py` and `tests/unit/test_analysis_sessions.py`. Never keep both branches on one number — the plausible resolution is the wrong one, and the second migration then silently never runs on an existing database |
| `CAPABILITIES` (`tests/unit/test_surface_parity.py`) and `_FEATURE_TIPS` (`src/yeaboi/ui/shared/_tips.py`) | Keep **both** rows, in **both** files. They are two-way bound, so a resolution that keeps one side only reds six separate checks in `test_surface_parity.py` |
| anything else | A genuine overlap — merge both intents and say in the report what you did and why |
