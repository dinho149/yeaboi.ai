---
name: ci-and-release
description: CI/CD workflow internals, version management and auto-bump mechanics, PyPI publish flow, Dependabot auth quirks, and AWS Lightsail deployment. Use when modifying .github/workflows, releasing, versioning, or debugging CI/publish/Dependabot behaviour.
---

# CI, Versioning & Release

## Version Management

Version is **single-sourced in `pyproject.toml`** (`version = "…"`). `src/yeaboi/__init__.py` reads it at runtime from the installed package metadata (`importlib.metadata.version("yeaboi")`, with a `0.0.0+dev` fallback for uninstalled source trees). `__version__` is imported by `cli.py` for the `--version` flag. Package entry points: `yeaboi = "yeaboi.cli:main"` (canonical) and a one-release back-compat alias `scrum-agent = "yeaboi.cli:main"`. The PyPI distribution was renamed `scrum-agent` → `yeaboi`; a thin `scrum-agent` redirect package (`packaging/scrum-agent-shim/`) depends on `yeaboi` so existing installs migrate.

**Releasing is automatic on a version bump.** To ship a release: bump `version` in `pyproject.toml` (semver) and merge to `main`. On that push, `publish.yml` detects there's no `v<version>` tag yet and runs test → build → PyPI publish (OIDC) → creates the `v<version>` tag + GitHub Release. Merges that don't change the version are a no-op. Never tag manually — the workflow owns tagging.

**The bump itself is automated too (`auto-version.yml`).** On each PR, cheap deterministic guards run first (skip if the version was already changed in the PR, or if no `src/yeaboi/**` files changed and no `semver:*` label is present); otherwise Claude classifies the diff into a semver level and commits `chore: bump version to X.Y.Z [auto]` **to the PR branch** — so merging fires `publish.yml` with no manual step. Rules:
- **Bump on the PR branch, not `main`** — a workflow pushing to `main` with the default `GITHUB_TOKEN` would not re-trigger `publish.yml` (recursion suppression); the human merge does. This means no PAT is needed.
- **Override with a label**: `semver:major` / `semver:minor` / `semver:patch` forces the level; `release:skip` (or `semver:none`) suppresses the bump.
- **Manual bumps still work** — if you edit `version` yourself, the guard sees it already differs from `main` and leaves it alone.
- **Mechanics** live in `scripts/bump_version.py` (pure `bump()` + `make bump-patch|bump-minor|bump-major`); the LLM only chooses the level.
- **Known limitation**: two PRs branched off the same version can pick the same next version — whichever merges second finds the tag already exists and won't publish separately. Acceptable for this repo; the fix (post-merge serialized bump on `main`) would need a PAT to re-trigger `publish.yml`.

Distribution is PyPI-only (via `uv tool install` / `pipx install`); Homebrew is not supported because a required dependency (`sqlite-vec`) ships no sdist, so the `omardin14/homebrew-tap` formula is permanently disabled.

## CI/CD

Workflows in `.github/workflows/`:

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci.yml` | Every push | Lint + test |
| `auto-version.yml` | PR | Claude classifies the diff and commits a `chore: bump version…` to the PR branch (skips docs/chore-only PRs; `semver:*` / `release:skip` labels override) |
| `publish.yml` | Push to `main` | if `pyproject.toml` version has no tag yet: test → build → PyPI publish (OIDC) → tag + GitHub Release (else no-op) |
| `claude-review.yml` | CI workflow succeeds on a PR (`workflow_run`) | Async Claude code + security review comment; only fires when all CI checks passed (no tokens burned on red PRs); advisory only, never blocks merge (skips drafts, bots, and Dependabot PRs) |
| `dependabot-auto.yml` | CI workflow succeeds on a Dependabot PR (`workflow_run`) | Claude verifies each bump (release notes vs our actual usage), posts a `SAFE-TO-MERGE` / `NEEDS-HUMAN` verdict comment, and enables auto-merge for safe ones. Pip **majors** and minor+ bumps of TUI/agent-critical packages (`rich`, `sqlite-vec`, `langgraph`, `langchain*`, `anthropic`) always get the `needs-human` label instead. Auto-merge waits on the required checks, so nothing red can land |
| `pr-feedback.yml` | PR opened/synced, any comment or review, and Claude Review completing (`workflow_run`) | Posts the **`pr-feedback` commit status** — red while a blocker/should-fix finding or an unresolved human review thread is unanswered. DoD item 10, and the one gate that is *not* advisory: every other commenter on a PR here is explicitly forbidden from blocking, which is how work merged past review comments for months. No Claude call — `scripts/pr_feedback.py` counts machine-readable verdict markers the reviewers stamp on their own comments. Escape hatch: the `feedback-override` label |
| `smoke.yml` | Weekly cron | Live API smoke tests |
| `codeql.yml` | Push/PR to `main` + weekly cron (Tue) | CodeQL deep static analysis (`security-extended`), `python` + `actions` languages; non-blocking (not in the ruleset), findings land in the Security tab / PR annotations. Free only while the repo is public. Its concurrency group is keyed on the **commit** (`github.event.pull_request.head.sha || github.sha`), not the starter template's `github.ref`: `auto-version.yml`'s bump push lands ~1 min after the human's, inside the python leg's ~2.5 min runtime, so a ref-keyed group cancelled the first run after `actions` had uploaded SARIF but before `python` did — and Code Scanning renders that half-upload as a grey `neutral` check indistinguishable from "Skipped". Pinned by `tests/unit/test_workflow_concurrency.py` |
| `claude.yml` | `@claude` mention, or `claude-implement` label on an issue | On-demand Claude Code assistance; the label triggers an implementation run that opens a PR |
| `flaky-test-hunter.yml` | Weekly cron + manual | Deterministic detector reruns the suite 5× + scans CI history; if flakes found, Claude (Haiku) files/updates `[bug][<owner>] flaky: …` issues into the **cowork queue** (`cowork:proposal` + `workstream:<owner>` + `type:bug` + `flaky-test`), so the daily digest ranks them alongside everything else. Issues, not fix PRs — a human escalates via `claude-implement`. The one detector cowork could not absorb: flakiness lives in CI run history, which a file-reading scout cannot see |
| `ci-sentinel.yml` | CI fails on `main` (`workflow_run`) | Claude diagnoses the red main build and opens a `ci-sentinel/…` fix PR (label `ci-sentinel`) or a `ci-red-main` issue; never pushes main. The `head_branch == 'main'` filter + open-PR dedupe prevent self-retrigger |
| `feedback-remediation.yml` | Nightly cron | A bash pre-step collects fresh (untriaged, human-authored) issues → Claude (Sonnet, `claude-code-action`) classifies each, applies the `triaged` cursor + `type:*`/`area:*` labels, and routes — up to 3 actionable bugs get `feature-candidate`, overflow → `feedback:fix-queued`, features → `feature-candidate`, vague → comment + `feedback:needs-info`, noise → `feedback:noise` (never closes); Monday digest. **Never applies `claude-implement`** — it used to, capped at 3/run, which contradicted the human-only approval gate in `cowork/house-rules.md` that six other files restate. Runs on the App `CLAUDE_CODE_OAUTH_TOKEN` (no `ANTHROPIC_API_KEY` — an earlier Agent-SDK draft was rewired off the SDK because the SDK is barred from subscription auth). `workflow_dispatch` defaults to dry-run, which also strips all write tools |

Merge gating: the `main-branch` ruleset requires the five ci.yml checks (Unit tests, Integration & contract tests, Lint, Format check, Security scan) **plus `pr-feedback`** to pass before **any** PR can merge; auto-merge (enabled repo-wide) fires only when they're green. Golden evaluators stay non-blocking by design.

**`pr-feedback` has to be added to that ruleset by hand, and nothing in this repo can do it.** The workflow posts a commit status either way, so a missing entry does not fail loudly — it fails by the check going red on a PR that then merges anyway, which is indistinguishable at a glance from the check working. Verify with:

```bash
gh api repos/:owner/:repo/rulesets --jq '.[] | select(.name=="main-branch") | .id'
gh api repos/:owner/:repo/rulesets/<id> --jq '.rules[] | select(.type=="required_status_checks")'
```

`pr-feedback` must appear in `required_status_checks_policy.required_status_checks[].context`. It belongs in the same manual-prerequisite bucket as `AUTO_VERSION_PAT` and the Claude GitHub App: set once, invisible when absent, and load-bearing. A PR that is genuinely stuck behind a broken gate is cleared with the `feedback-override` label rather than by removing the requirement.

### When a Claude workflow fails

Seven workflows share **one** credential, `CLAUDE_CODE_OAUTH_TOKEN` in the repo's *Actions* secret store. When it goes bad they all fail at once, and because `anthropics/claude-code-action` hides SDK output by default most of them fail *silently* — a bare `is_error: true` with no reason. Read the signature before touching prompts or models:

| Signature in the run log | Means |
|---|---|
| `num_turns: 1`, `duration_ms` ≈ 2000, `total_cost_usd: 0`, `apiKeySource: "none"` | **auth**, not the model |
| `"api_error_status": 401` / `"error": "authentication_failed"` | the token is expired, revoked, or the wrong kind |
| a green check on a PR that edits the workflow file itself | the action **skipped** — it requires the file to be byte-identical to the copy on `main`, and reports the skip as success |

Fix: `claude setup-token` on a machine logged into the Claude subscription, then `gh secret set CLAUDE_CODE_OAUTH_TOKEN`. The value must be a Claude Code OAuth token (`sk-ant-oat…`), **not** a Console API key (`sk-ant-api…`) — the API rejects a Console key in that secret with the same `401 OAuth access token is invalid`, so "I rotated it and it still fails" usually means the wrong kind of token was pasted. A Console key belongs in `ANTHROPIC_API_KEY`, which `auto-version.yml` and `claude-review.yml` also read. `CLAUDE_CODE_OAUTH_TOKEN` is *believed* to win when both are set — meaning you'd clear it to fall through — but that precedence has never been exercised here and is an assumption, not an observation.

`auto-version.yml` preflights the credential's *shape* before spending a turn, and annotates a 401 with the remediation. That preflight is plain shell, so unlike the action it still runs on a PR that edits the workflow. It deliberately does not validate the prefix against an allowlist — it only rejects shapes that cannot work — so a future change to Anthropic's token format won't red every PR.

**2026-07-30 incident, for calibration:** this exact 401 took every Claude workflow down and was misdiagnosed twice — first as a stale Haiku alias (PR #120 pinned the dated model id, which changed nothing), then as a token that needed rotating (it was rotated, and still failed). The tell was there the whole time, behind `show_full_output`.

Dependabot notes: updates arrive **grouped** (one weekly PR per ecosystem; security updates grouped too — see `.github/dependabot.yml`). Pip Dependabot PRs carry the `semver:patch` label so merging one publishes a patch release — a merged dependency/CVE fix reaches PyPI users instead of sitting unreleased. Three mechanics to know:
- **Auth via `workflow_run`, not the Dependabot secret store.** Dependabot-triggered runs can only read a *separate* Dependabot secrets store, which the Claude GitHub App does **not** populate (it provisions `CLAUDE_CODE_OAUTH_TOKEN` only into the *Actions* store). So `dependabot-auto.yml` triggers on `workflow_run` (after CI) instead of on Dependabot's `pull_request` event — a `workflow_run` job runs from the default branch with the normal Actions secrets, using the App's token directly. **No Dependabot secret needs to be created or kept in sync.** The PR is resolved from the CI run's head SHA; Claude derives the bumped packages from the PR title + diff (no `fetch-metadata`, which needs the avoided Dependabot context).
- **Labels must pre-exist.** Dependabot only *applies* labels that already exist in the repo — `dependencies`/`security`/`ci`/`semver:patch` are created; if one is deleted, Dependabot silently skips it.
- **Release trigger.** A merge performed by the default `GITHUB_TOKEN` does not trigger `publish.yml` (same recursion suppression as the version bump), so an auto-merged pip bump's release simply defers to the next human push to `main` (an optional `AUTO_MERGE_TOKEN` PAT in the Actions store would make it publish immediately).

There is no Homebrew tap auto-update: the `omardin14/homebrew-tap` formula is disabled (see Version Management) and `publish.yml` no longer dispatches to it.

## Deployment (AWS Lightsail)

yeaboi is deployed on AWS Lightsail via the OpenClaw blueprint:
- OpenClaw comes pre-installed on the Lightsail instance
- Uses Amazon Bedrock (Claude Sonnet 4.6) via IAM instance role — no API key needed
- Bedrock IAM setup script: `curl -s https://d25b4yjpexuuj4.cloudfront.net/scripts/lightsail/setup-lightsail-openclaw-bedrock-role.sh | bash -s -- <instance-name> <region>`
- The setup wizard auto-detects the AWS region from `~/.aws/config` and the Bedrock model from OpenClaw's `models.json`
- See README section "Deploy on AWS Lightsail (OpenClaw)" for full guide
