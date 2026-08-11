# House rules

Binding on every cowork routine and every crew agent. Read this before your charter.

## The auto lane

Security, bugs and chores ship without asking. That is the point of the lane: the maintainer's
attention is a scarce resource and none of those three needs it. What replaces the approval is not
trust — it is a gate that a machine cannot open on its own (see **The gate** below).

A find may skip the proposal step and go straight to a PR only when **every** condition holds:

- confined to the paths your charter declares
- no public API, schema, or state-field change
- no prompt change (`src/yeaboi/prompts/`)
- no new capability — i.e. it needs no `test_surface_parity.py` registry edit
- no change to user-facing wording or labels

That last one is the line, and it is narrower than it looks: **behaviour may change, copy may
not.** Correcting a wrong total, guarding a crash, or wiring up a button that does nothing are all
inside the lane even though a user would notice. Rewording the label above that total is not. The
distinction is that behaviour has a right answer the tests can hold, and wording is a matter of
taste — which is exactly the kind of judgement the proposal queue exists to put in front of a
human.

…**and** it falls in one of these categories, which is the complete list:

1. security patch to a known guardrail gap, or a `pip-audit` CVE — see the carve-out below
2. a **bug**, with a regression test that fails before the fix and passes after — see below
3. a flaky or outright broken test
4. dead code removal, or a refactor with no behaviour delta
5. documentation drift (docs that contradict the code)
6. lint / type-annotation cleanup
7. a CodeQL alert whose rule id is on the `auto` list in
   [`.github/codeql/triage-policy.yml`](../.github/codeql/triage-policy.yml) — belongs to
   `codeql-triage.yml`, not to a routine; see the carve-out below

**A bug enters the lane on a failing test, not on an argument.** Write the regression test first,
run it against unfixed `main` and watch it fail, then fix and watch it pass, and paste both runs
into the PR body. A bug you cannot reproduce that way is one whose shape you do not yet understand
well enough to fix unwatched — file it as a proposal. This is the whole admission ticket for
category 2, and it is what makes "is this really a bug?" a mechanical question instead of a
judgement.

**Everything else proposes.** If you are arguing with yourself about whether something qualifies,
it does not. Marketing always proposes.

**`feature` and `improvement` finds always propose.** The opportunity pass in `cowork-scout.md`
widens what scouts *look for*, not what builders may *ship unasked* — a user-facing opportunity is
exactly the judgement call the proposal queue exists to put in front of a human. No opportunity ever
qualifies for the auto lane.

**Routine dependency bumps are not on this list**, though they look like they belong. Dependabot
opens those PRs and `dependabot-auto.yml` verifies and merges them; a builder bumping the same
dependency in its own branch produces a second PR that conflicts with the first. Nothing to
consolidate — the producer is a GitHub service, not a workflow. Leave the bump; a *usage* fix the
bump requires is ordinary work and rides category 2 like any other bug.

**A `pip-audit` CVE is the one exception**, because waiting for Dependabot's next scheduled run is
not an acceptable posture on a known exploitable version. It stays auto-lane, with one precondition:
check `gh pr list --author "app/dependabot" --state open` first, and if a PR already bumps that
dependency, **drive that PR instead of opening your own**. Two PRs raising the same pin is the exact
collision the paragraph above exists to prevent, and a CVE is not a reason to cause it.

**CodeQL alerts belong to a workflow, not a routine, and category 7 takes three exemptions from the
guardrails below.** They are listed here rather than assumed, because each one is a rule this file
otherwise enforces:

- *One coherent change per run* — a batch of same-rule mechanical fixes **is** one coherent change.
  Twenty-five `uses:` lines pinned to their current SHAs is one review, read once; splitting it
  across twenty-five runs is twenty-five reviews of the same decision. The batch is capped
  (`max_batch` in the policy file) and anything over the cap is reported by number, never dropped
  silently.
- *One open PR per workstream* — the triage PR does not count against `security`'s. It carries its
  own single-PR guard: the survey step exits early when a `security/codeql-triage` branch already
  has one open, so the mechanism is preserved, just not shared with the sweep's.
- *Stay in your paths* — alerts land wherever the code is, so a per-charter boundary would leave
  most of them unowned by anyone. What bounds the risk instead is the **rule id**: only rules whose
  fix is mechanical, local and provably behaviour-preserving are on the `auto` list, and the policy
  file records the prescribed fix for each.

None of that removes a gate: the triage PR goes through **The gate** below like everything else,
and merges via `gh pr merge --auto` so the ruleset is what decides. A wrong fix does not merge; it
sits red.

## The gate

The lane is wide; what keeps it safe is the merge path, and none of it is discretionary.

- **`make test` and `make lint` pass before the commit** — `make test-fast` is not enough at PR
  time. The full contract is in [definition-of-done.md](definition-of-done.md), items 2–7.
- **An independent `code-reviewer` reads the diff** before the PR opens. The builder never reviews
  its own work.
- **`claude-review.yml` reviews the PR** once CI is green, and the `pr-feedback` status counts what
  it found.
- **A machine may fix a finding; it may never dismiss one.** `scripts/pr_feedback.py` refuses an
  `<!-- addressed: … -->` marker from the PR's own author on an unattended PR, so the only way to
  clear one is to push a fix and let the reviewer report `open=0` itself. A finding you disagree
  with ends the auto lane for that find: file it as a proposal and let a human answer. Before this
  was enforced, the routine that opened the PR could also declare the review of it answered — the
  applicant holding the key.
- **The merge waits on the ruleset**, which decides, not on any routine. `pr-feedback` must be a
  required status check on the `main-branch` ruleset for that sentence to be true; it is a manual
  setup step. Every workflow that would arm `--auto` checks for it first and refuses when it is
  absent, rather than merging on CI alone and calling it reviewed.
- **Nothing ships to users on merge.** A merge to `main` publishes a PyPI *pre-release*;
  `pip install yeaboi` is unaffected. The accumulated batch becomes an official version only when a
  human promotes it — see [`routines/cron/release-promote-ask.md`](routines/cron/release-promote-ask.md).
  This is the last backstop: a wrong fix that survives everything above still has to get past a
  person who has been running it.

## Throughput is already bounded — do not add a throttle

One `auto` find per run and one open PR per workstream (see Guardrails) together cap a heavy
weekday at roughly eight merges and a typical one at two to four. That is the intended volume, and
it is bounded by structure rather than by a counter someone has to tune. A new limit on top would
mostly express nervousness, and the honest answer to nervousness here is the gate above.

## Guardrails

- **One open PR per workstream.** Start every run with
  `gh pr list --label "workstream:<name>" --state open`. If one is open, drive *that* PR to green
  and stop. Do not open a second.
- **Stay in your paths.** A find outside your charter's paths becomes a proposal issue labelled for
  the owning workstream. Never edit another workstream's files — this is what keeps two routines off
  `src/yeaboi/ui/mode_select/__init__.py` (14k LOC, the repo's worst merge surface).
- **Never apply `claude-implement`.** Only a human does. That label is the approval gate; a routine
  applying it would be approving its own work. The one carve-out is `routines/cron/slack-relay.md`,
  which applies the label (or closes an issue) **only** as the relay of a ✅/❌ reaction from a human
  on its written allowlist, and records who and the message link on the issue. The human is
  approving; the relay is transport — it never applies the label to anything no allowlisted human
  reacted to.
- **Never push to `main`, never `--force`, and never merge a PR yourself.** `gh pr merge --auto`
  is not a merge — it hands the decision to the ruleset, which merges only once every required
  check is green. Arming it is allowed; clicking merge, bypassing a check, or pushing to `main`
  is not, and no widening of the auto lane changes that.
- **One coherent change per run.** No grab-bags.
- **Nothing to do is a valid, common outcome.** Exit quietly — no issue, no Slack message. A routine
  that always finds something is a routine inventing work.
- **Label every PR** `cowork`, `workstream:<name>`, and the find's `type:<type>` — the daily
  standup takes its `[type]` tag from the PR's labels. The `cowork` label is also what guarantees
  `claude-review.yml` reviews it: that workflow skips `dependabot[bot]` and `github-actions[bot]`,
  and an unattended job's PR may carry one of those authors. A truncated run that pushes without
  labelling no longer goes unreviewed — both that workflow and `scripts/pr_feedback.py` also
  recognise the `cowork/` branch namespace — but it does ship untagged in the standup, so label it.

## Verification is not optional

`make test` and `make lint` must pass before you commit — `make test-fast` is not enough at PR time.
The full contract is in [definition-of-done.md](definition-of-done.md).
