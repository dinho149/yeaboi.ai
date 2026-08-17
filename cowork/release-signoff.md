# release sign-off

**Summary** — the ritual that turns the fleet's accumulated PRs into an official version

**The fleet's PRs do not merge; a human's batch does.** Every fleet PR — the `cowork` label or an
unattended branch prefix, the same line `scripts/release_lane.py` draws — waits open against
`main`, individually CI-green, reviewed, and `pr-feedback`-clean. None of it reaches `main`, and
none of it reaches users, until you fold the waiting PRs into one **batch PR**, install the
assembled build, exercise it, and merge. The merge is the sign-off. Your own PRs are untouched by
all of this: a human merge still cuts the official `X.Y.Z` on the spot, exactly as it always did.

This page is that person's job, start to finish. It takes three commands and about twenty minutes.
Everything a machine can decide is already decided by the time the batch exists: every constituent
passed `make test`, `make lint`, an independent review, and `pr-feedback` held it until every
finding was answered — and the batch PR's own CI runs on the assembled tree, the first time the
constituents are tested *together*. What is left is the part no gate covers — installing the real
wheel and using it.

## The week

**Monday 09:00 UTC** — `cron/release-promote-ask.md` checks whether anything is waiting. If no
gate-green fleet PRs exist and no batch is open, it stays silent. Otherwise it posts one Slack
reminder to `#yeaboi-claude` naming what is waiting. It is a reminder only: it cannot assemble,
label, or merge anything.

**You, whenever suits** —

```bash
make batch-assemble
```

It selects every open fleet PR that is gate-green (CI green **and** `pr-feedback` success — a red
or unreviewed PR is skipped and named), folds them into a `batch/<date>` branch — **one squash
commit per PR**, so `main`'s history stays one commit per item — pushes it, opens a **draft PR**
labelled `release:promotion`, and builds the wheel:

- A constituent that **conflicts** (with `main` or with an earlier constituent) is skipped,
  reported with the conflicting pair, and left open — its workstream rebases it and it joins the
  next batch. One bad item never blocks the batch.
- A constituent that **bumps the version** is skipped too: fleet PRs carry `semver:none` so that
  `auto-version.yml` bumps nothing on their branches. The batch PR is the one that gets the bump —
  it targets `main`, so the existing auto-version machinery fires on it, sees the whole batch as
  its diff, and pushes one bump + one `changelog_data.json` entry onto the batch branch. Put a
  `semver:*` label on the batch PR to override the level by hand.
- The `Closes #N` / `Closes YEA-NN` lines from every constituent are carried into the batch PR
  body, so queued issues and Linear tickets close on your merge exactly as they would have.

Then:

```bash
make beta-check
```

It **reports and records nothing** — that is `beta-sign-*`'s job, below. It prints the batch PR,
its head, its constituents, and **TEST THIS BATCH**: the baseline (`install`, `boot`) and one
section per track, derived from *which paths this batch actually touched*, from the table in
`scripts/release_surfaces.py`.

### Two sections, because the fleet does two things

The fleet maintains what already exists, and it builds one provider integration a week. Those are
different things to sit down and exercise, so they are two sessions:

- **MAINTENANCE** — security, bugs, chores and docs. A checklist derived from the batch's changed
  paths. Every row carries why it is there: these are the failures this repository has already
  shipped below its own test suite. A CSP that only breaks for the remote teammate. A launchd
  plist that only breaks at fire time. A Go sidecar that reverts to Python with CI fully green.
  Most weeks it is three or four rows.
- **INTEGRATION: `<provider>`** — one row per angle of the week's campaign, from
  `INTEGRATION_ANGLES` in the same file. Angles the batch did not reach are printed as
  `··· <angle> — not wired in this batch` rather than omitted: an angle that vanishes reads as an
  angle that was not needed.

Which section a batch gets is decided by **paths**, not by who wrote the commit. The
`integration(<provider>):` PR-title prefix is a second, corroborating signal, read off the batch
PR's constituent lines. **A track with nothing in it is never asked for** — an empty checklist
reads as "signed off" when it means "never asked".

Install the wheel `batch-assemble` built, work through a section, and record it:

```bash
make beta-sign-maintenance
make beta-sign-integration    # only when there is a campaign in the batch
```

Each writes a per-track marker comment on the batch PR, **pinned to the head sha you tested**; the
**last required one** also writes the bare completion marker. A signature counts only while that
sha IS the batch's head — any commit after it (a re-assembly, a late constituent) makes the
signature stale, because the tree it names is not the tree that would merge. Then:

```bash
make beta-promote        # verifies, flips the draft to ready, prints the merge. STOPS.
gh pr merge <n> --merge  # yours — the one command nothing here will run for you
```

`beta-promote` refuses while a required track is unsigned at the current head, and names which.
`--yes` overrides and says on the way past that it did. It also refuses a batch PR that would
classify as a fleet merge — a stray `cowork` label on it would make your merge release *nothing*,
silently.

**Merge with `--merge`, never squash.** The batch branch is one tidy commit per constituent plus
the version bump; a merge commit is what keeps `git log <prev-tag>..<tag>` — the release notes,
the announcement, the next batch's manifest — reading one item per line. A squash collapses the
week into a single commit and silently degrades all three.

Your merge is a human-lane push to `main`, so `publish.yml` tests the merged tree, publishes the
official `X.Y.Z`, tags `v X.Y.Z`, and writes the GitHub Release — from exactly the tree you
tested, plus only the metadata-only version-bump commit. Afterwards, close the constituents:

```bash
uv run python scripts/batch_assemble.py --close <batch-pr-number>
```

It refuses on an unmerged batch — closing constituents of a batch that never shipped reads as a
pile of rejections to the next sweep's dedupe pass.

**The batch PR's gate is the hand-test, not a re-review.** Every constituent was reviewed and
gated individually; requiring a fresh review of the assembled diff would be a rubber stamp with a
reviewer's name on it. Do not "fix" the batch by adding one.

## What each marker means

Markers live as comments on the batch PR. Never write one by hand — `make beta-sign-*` renders
them. Only a maintainer's comments count: `beta_signoff.py` filters on `authorAssociation`
(OWNER / MEMBER / COLLABORATOR), and that filter is the whole authorization — the batch is an
open PR on a public repository, and anybody can comment on it.

| marker | written by | means |
|---|---|---|
| `<!-- tested: <sha> track=<track> -->` | `make beta-sign-<track>` | one session, run, at that head |
| `<!-- tested: <sha> -->` | the last required `beta-sign-*` | **every required track** is signed at that head |

The per-track marker is shaped so the bare regex cannot match it — ` -->` must follow the sha
directly — and that is the mechanism rather than an accident: a half-signed batch physically
cannot look complete to any reader that only ever learned the bare marker.

## If you skip a week

Nothing breaks, and nothing merges without you.

- **The reminder does not nag.** One Slack line on Mondays when something is waiting; silence
  otherwise.
- **The waiting PRs keep their gates.** Each stays individually green (or goes red, visibly, on
  its own PR) — nothing rots invisibly inside a branch.
- **A skipped week makes a bigger batch**, and a bigger batch has more conflict surface at
  assembly. The mitigation is that conflicts land in the assembler's report, named per PR, and a
  conflicted item simply waits for its workstream's rebase — the batch still ships without it.
- **A re-assembled batch needs a re-sign.** Signatures pin the head sha, so adding late work to a
  batch honestly invalidates the sign-off on the old tree. Sign after the batch is final.

## Escape hatches

```bash
# assemble locally without pushing or opening anything
uv run python scripts/batch_assemble.py --dry-run

# release main's HEAD directly — your own merges normally make this unnecessary
gh workflow run publish.yml -f version=X.Y.Z

# print the checklist for an arbitrary set of paths
uv run python scripts/release_surfaces.py src/yeaboi/cli.py frontend/src/app.tsx
```

The `workflow_dispatch` hatch releases whatever `main`'s HEAD is. Under the batch model that is
already a human-verified line, so its old warning ("not a pinned pre-release") no longer applies —
the caveat now is that it bypasses nothing *except* your own judgement about whether `main` is
ready to be the release.

## Manual prerequisites

These fail invisibly and no routine can fix them:

- **Merge commits must be allowed** in the repository's settings (Settings → Pull Requests). The
  batch merge uses `--merge`; if only squash is enabled, the model silently degrades into
  per-item history loss and mis-counted release notes.
- `pr-feedback` must be a required status check on the `main-branch` ruleset, or fleet PRs are
  gate-green without any review having run — and `batch-assemble` will happily fold them in.
- `publish-beta.yml` still needs **its own PyPI trusted-publisher entry** (workflow
  `publish-beta.yml`, environment `pypi`) while it remains in service. Under the batch model the
  pre-release channel is redundant — fleet work is tested from the locally built wheel, and every
  merge to `main` is verified — and it is slated for retirement once a full batch cycle has run.

## Related

- [definition-of-done.md](definition-of-done.md) — what has to be true before a PR opens at all
- [house-rules.md](house-rules.md) — which work merges how, and why the fleet never merges
- `cron/release-promote-ask.md` · `cron/shipped-standup.md` · `scripts/batch_assemble.py`
