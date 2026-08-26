# repo-notes — yeaboi

The facts the shared `/ship` and `/sync-main` deliberately do not hardcode, because the front-end,
desktop, site and tooling repos answer them differently. The procedures live in the `yeaboi-devkit`
plugin; this is what they read.

## Commit

Commit with **`SKIP=unit-tests`**. That pre-commit hook is `make test-scoped`, which the Stop hook
already ran at the end of the last turn and which `/ship`'s gate is about to run in full. Three runs
of the same tests is how a gate becomes one people pass with `--no-verify`. gitleaks, ruff and
`ruff-format` still run — those catch something the gate does not.

Trailer:

```
Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
```

## Gate

`make ship-gate` = `lint` → `format-check` → `test` → `security` → `preflight`, fail-fast, in one
`make` invocation so `lint` resolves once for itself and for `security`.

`preflight` is the half `make test` cannot cover: `scripts/preflight.py` runs the optional CI jobs
this branch's diff needs — front-end bundles, the desktop app, golden evaluators, the wheel's
contents, cross-version compat, actionlint — decided by `scripts/test_scope.py`, printing
every job it skipped and why.

**A new capability needs three registry edits or `make test` fails**, each with a message naming the
exact edit: a `CAPABILITIES` row in `tests/unit/test_surface_parity.py`, a `FeatureTip` in
`src/yeaboi/ui/shared/_tips.py`, and — if it records runs — a saved-sessions hub. See CLAUDE.md,
*REQUIRED: Surface Parity*.

Changed a board tuple, an accent or a timing the browser is checked against? `make web-types` and commit `contracts/web/` — **yeaboi-frontend** vendors that directory and its CI runs the other half of the check.

Changed `pyproject.toml`'s `requires-python` or a `[project.urls]` entry? `make site-contract` and
commit `contracts/site.json` — the **yeaboi-site** repo vendors it, and `test_site_contract.py`
fails until you do.

## After the push

**The branch is stale again about a minute later, by design.** `auto-version.yml` pushes a
`chore: bump version … [auto]` commit onto the PR *branch*, touching `pyproject.toml` and
`src/yeaboi/changelog_data.json` — and *not* `uv.lock`, which then disagrees with both. Any later
push from the worktree must `git pull --rebase` first, and must **never** force-push over that
commit.

## Conflict playbook

The rows below name a side by what it is, never by `--ours`/`--theirs`: those flags *invert* under
rebase, so "take the upstream side" is unambiguous and "take theirs" is a coin flip.

| Conflicting path | Resolution |
|---|---|
| `contracts/web/**` | Generated. Take either side, then `make web-types` (for `enums.json`/`ui.json`) or re-run `tests/unit/test_web_wire_shapes.py` (for `fixtures/`) and commit what it writes |
| `contracts/web/fixtures/**` | Take the upstream (`origin/main`) side, then `uv run pytest tests/unit/test_web_wire_shapes.py` regenerates them; commit what it wrote |
| `tests/unit/__snapshots__/*.ambr` | Take the upstream side, then `make snapshot-update` |
| `desktop/src/renderer/routes.json` and `contracts/v1/routes_manifest.json` | Merge both route sets, then `npm run gen-manifest` in `desktop/`; the manifest is generated from the JSON, never edited |
| `pyproject.toml` version line, `src/yeaboi/changelog_data.json` | Keep **`origin/main`'s** and drop your bump entirely. `auto-version.yml` re-bumps on the PR branch, and the changelog is prepend-only — which makes every pair of release-worthy PRs collide here by construction |
| `uv.lock` | Keep **`origin/main`'s**, then `uv lock`. Its root `version` line tracks `pyproject.toml` and `auto-version.yml` does *not* update it, so a disagreement there is expected, not a mistake to preserve |
| `CURRENT_SCHEMA_VERSION` | Renumber **yours** to max+1 in all three places: `src/yeaboi/sessions.py` and its inline history comment, and the two hard-coded `== N` assertions in `tests/unit/test_agentwatch_store.py` and `tests/unit/test_analysis_sessions.py`. Never keep both branches on one number — the plausible resolution is the wrong one, and the second migration then silently never runs on an existing database |
| `CAPABILITIES` (`tests/unit/test_surface_parity.py`) and `_FEATURE_TIPS` (`src/yeaboi/ui/shared/_tips.py`) | Keep **both** rows, in **both** files. They are two-way bound, so keeping one side reds six separate checks |
| `.tooling-rev` | Take whichever sha is newer in the tooling repo, then `make tooling-check`. Never merge the two lines |
| anything else | A genuine overlap — merge both intents and say in the report what you did and why |

## Unattended lane

The `pr-feedback` gate **enforces** on `cowork/…` and `feature/issue-…` branches, triage and sentinel
branches, and anything labelled `cowork` — where nobody is on the other end. Everywhere else it is
advisory and stays green. A human reviewer's unresolved thread or a `Request changes` review holds
the check on any branch.

`/pr-feedback` and `/babysit-prs`, and the `pr-fixer` / `pr-responder` agents, live in this repo's
`.claude/` rather than in the shared plugin: they drive `scripts/pr_feedback.py`, and the
`pull_request_target` workflow that runs it is not portable yet.
