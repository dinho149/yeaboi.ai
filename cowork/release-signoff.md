# release sign-off

**Summary** — the weekly ritual that turns accumulated pre-releases into an official version

Merging to `main` does not ship to users. Every release-worthy merge publishes a PyPI
**pre-release** (`X.Y.ZrcN`) that `pip install yeaboi` cannot see, and those accumulate until
somebody who has actually run the code says yes. This page is that person's job, start to finish.
It takes two commands and about twenty minutes.

Everything a machine can decide is already decided by the time an rc exists: `make test`,
`make lint`, `make parity` and `make web-check` all ran, an independent reviewer read the diff, and
`pr-feedback` held the merge until every finding was answered. What is left is the part no gate
covers — installing the real wheel and using it.

## The week

**Monday 09:00 UTC** — `cron/release-promote-ask.md` checks whether anything is pending. If the
version line has not moved since the last release, it stays silent. Otherwise it opens one
`release:promotion` issue and posts one Slack ask to `#yeaboi-claude`, with a thread reply to
react to.

**You, whenever suits** —

```bash
make beta-check
```

It **reports and records nothing** — that is `beta-sign-*`'s job, below. It prints four things:

- **`install`** — the exact `pip install --pre yeaboi==X.Y.ZrcN` that works. Backed by a
  `beta/X.Y.ZrcN` git tag, which `publish-beta.yml` pushes only after the upload succeeds.
- **the batch** — what changed, from `changelog_data.json`, newest version first.
- **TEST THIS WEEK** — the baseline (`install`, `boot`), and then **one section per track**.
- **NOT IN THIS RELEASE** — commits on `main` that are in no pre-release. They ride the next one.

### Two sections, because the fleet does two things

The fleet maintains what already exists, and it builds one provider integration a week. Those are
different things to sit down and exercise, so they are two sessions:

- **MAINTENANCE** — security, bugs, chores and docs. A checklist derived from *which paths this
  batch actually touched*, from the table in `scripts/release_surfaces.py`. Every row carries why
  it is there: these are the failures this repository has already shipped below its own test suite.
  A CSP that only breaks for the remote teammate. A launchd plist that only breaks at fire time. A
  Go sidecar that reverts to Python with CI fully green. Most weeks it is three or four rows.
- **INTEGRATION: `<provider>`** — one row per angle of the week's campaign, from
  `INTEGRATION_ANGLES` in the same file, mirroring the reach matrix in `integrations-map.md`. Angles
  the batch did not reach are printed as `··· <angle> — not wired in this batch` rather than
  omitted: an angle that vanishes reads as an angle that was not needed.

Which section a batch gets is decided by **paths**, not by who wrote the commit — if `tools/jira.py`
moved, somebody should drive Jira through the modes, whoever changed it and why. The
`integration(<provider>):` PR-title prefix is a second, corroborating signal, and it is the only one
that catches a campaign's reach angle, which touches no provider module at all. A forgotten prefix
therefore costs a redundant checklist row and never a wrong release.

**A track with nothing in it is never asked for.** A week with no campaign does not stop for an
integration session with an empty checklist — an empty checklist reads as "signed off" when it means
"never asked".

Install it, work through a section, and record that section:

```bash
make beta-sign-maintenance
make beta-sign-integration    # only when there is a campaign in the batch
```

Two targets and not `make beta-sign integration`, because Make reads a bare word as a second goal.
Each writes a per-track marker; the **last required one** also writes the bare `<!-- tested: -->`
marker that `publish.yml` reads. Then:

```bash
make beta-promote        # prompts, then labels the ask release:promote
```

`beta-promote` refuses while a required track is unsigned, and names which. `--yes` overrides and
says on the way past that it did.

or ✅ the Slack thread reply, which does the same thing through
`cron/slack-relay.md` → `scripts/cowork_relay.py` → the same `gh issue edit --add-label`.

`publish.yml` then cuts `X.Y.Z` **from the commit behind the pre-release you tested**, uploads it,
tags `vX.Y.Z` there, writes the GitHub Release, and closes the issue with a comment naming the
commit and anything left behind.

Not ready? ❌ closes the issue and means "not this week". Next Monday asks again.

## What each marker means

The promotion issue carries four kinds of HTML comment. Never write one by hand — every one is
rendered by `scripts/release_channel.py` or `scripts/beta_signoff.py`.

| marker | written by | read by | means |
|---|---|---|---|
| `<!-- promote: X.Y.Z -->` | the ask routine | `publish.yml` | the version the human was asked about |
| `<!-- beta: beta/X.Y.ZrcN -->` | the ask routine | `publish.yml` | the pre-release the ask is about |
| `<!-- tested: beta/X.Y.ZrcN track=<track> -->` | `make beta-sign-<track>` | `beta_signoff.py` only | one session, run |
| `<!-- tested: beta/X.Y.ZrcN -->` | the last `beta-sign-*` | `publish.yml`, next week's ask | **every required track** is signed |

**The per-track marker is shaped so that neither `publish.yml` nor `TESTED_RE` can match it**, and
that is the mechanism rather than an accident. Both require ` -->` immediately after the digits, so
a `track=…` marker is invisible to them — which means a half-signed batch physically cannot be
promoted by a workflow that only ever learned to read one marker, and `publish.yml` needed no edit
at all.

It also means **an existing bare marker keeps meaning what it meant**: "I ran this build and signed
the whole thing off", which under the split is exactly the completion marker. A bare marker seeds
*every* track's floor, so nothing written before the split has to be reinterpreted.

`publish.yml` prefers `tested:` over `beta:` over `main`, most-trusted first, and never fails for
want of one — a missing marker means less pinning, not no release.

## If you skip a week

Nothing breaks, and nothing is asked twice.

- **The ask does not repeat itself.** If an ask is already open and nothing new has been published
  since it was written, Monday's routine posts nothing. One live issue, one Slack prompt, one
  decision.
- **When something *has* shipped**, the routine closes the stale ask, opens a fresh one, and — if
  you signed one off — leads with **only what is new since the rc you signed off on**. The part you
  already reviewed is still there, collapsed. You re-test the new surface, not all of it.
- **A track you already signed stays signed while nothing of its shape lands.** Sign integration at
  `rc7`, and if `rc8` and `rc9` are maintenance-only, integration is still covered at `rc9` — you
  are not asked to re-run a checklist for work that did not change. One integration commit anywhere
  in between and you are, because the carry is refused the moment *either* signal says something
  landed.
- **If you already tested the newest build**, the ask says so and prints no checklist. Everything
  merged since is on `main` and in nothing installable — there is a promotion to make, and nothing
  new to check before making it.
- **A ✅ on last week's Slack message does nothing.** `is_promotion` in `cowork_relay.py` reads
  issue state, and a closed ask routes to `ask` rather than `promote`. That hole is real —
  `publish.yml`'s guard fires on the `labeled` event and never looks at state — and it is closed on
  the relay side because the stale artifact lives in Slack, where the "never leave two open" rule
  cannot reach it.
- **Two ✅s do not release twice.** The second run finds the version already tagged, comments
  `already released`, closes the duplicate ask and exits green. It is a race, not a fault.

## The two versions, and why only one is installable

`release_channel.py` reports both. They are not the same thing and only one belongs in front of a
human.

- **`installable`** — the newest `beta/*` tag. A tag exists only if the PyPI upload returned, so
  this is a file somebody can download.
- **`latest_prerelease`** — `next_prerelease(HEAD)`: what the *next* release-worthy merge would be
  numbered. It is a commit count since the last final tag, so every docs, CI and chore merge raises
  it — including all the ones that publish nothing.

Quote the second as an install command and it 404s, for the one person who did what was asked.
Routines quote `installable`.

## Escape hatches

```bash
# what really exists on PyPI for this batch
uv run python scripts/release_channel.py --published

# the batch as the ask sees it (add --since beta/X.Y.ZrcN for the delta)
uv run python scripts/release_channel.py --manifest --json

# promote with no issue at all — cuts main's HEAD, NOT a pinned pre-release
gh workflow run publish.yml -f version=X.Y.Z

# force a pre-release for a push that did not move the version line
gh workflow run publish-beta.yml -f force=true

# print the checklist for an arbitrary set of paths
uv run python scripts/release_surfaces.py src/yeaboi/cli.py frontend/src/app.tsx
```

## Manual prerequisites

These fail invisibly and no routine can fix them:

- `publish-beta.yml` needs **its own PyPI trusted-publisher entry** on the `yeaboi` project
  (workflow `publish-beta.yml`, environment `pypi`), alongside `publish.yml`'s. Without it every
  pre-release dies at the upload step with `invalid-publisher`.
- `pr-feedback` must be a required status check on the `main-branch` ruleset, or the unattended
  merge lane is unarmed.

## Related

- [definition-of-done.md](definition-of-done.md) — what has to be true before a PR opens at all
- [house-rules.md](house-rules.md) — which work merges unattended and which asks first
- `cron/release-promote-ask.md` · `cron/slack-relay.md` · `cron/shipped-standup.md`
