# House rules

Binding on every cowork routine and every crew agent. Read this before your charter.

## The auto lane is narrow and closed

A find may skip the proposal step and go straight to a PR only when **every** condition holds:

- confined to the paths your charter declares
- no public API, schema, or state-field change
- no prompt change (`src/yeaboi/prompts/`)
- no new capability — i.e. it needs no `test_surface_parity.py` registry edit
- no user-visible copy or UX change

…**and** it falls in one of these categories, which is the complete list:

1. security patch to a known guardrail gap, or a `pip-audit` CVE — see the carve-out below
2. a flaky or outright broken test
3. dead code removal
4. documentation drift (docs that contradict the code)
5. lint / type-annotation cleanup

**Everything else proposes.** If you are arguing with yourself about whether something qualifies,
it does not. Marketing always proposes.

**Routine dependency bumps are not on this list**, though they look like they belong. Dependabot
opens those PRs and `dependabot-auto.yml` verifies and merges them; a builder bumping the same
dependency in its own branch produces a second PR that conflicts with the first. Nothing to
consolidate — the producer is a GitHub service, not a workflow. Leave the bump; a *usage* fix the
bump requires is ordinary work and proposes like anything else.

**A `pip-audit` CVE is the one exception**, because waiting for Dependabot's next scheduled run is
not an acceptable posture on a known exploitable version. It stays auto-lane, with one precondition:
check `gh pr list --author "app/dependabot" --state open` first, and if a PR already bumps that
dependency, **drive that PR instead of opening your own**. Two PRs raising the same pin is the exact
collision the paragraph above exists to prevent, and a CVE is not a reason to cause it.

## Guardrails

- **One open PR per workstream.** Start every run with
  `gh pr list --label "workstream:<name>" --state open`. If one is open, drive *that* PR to green
  and stop. Do not open a second.
- **Stay in your paths.** A find outside your charter's paths becomes a proposal issue labelled for
  the owning workstream. Never edit another workstream's files — this is what keeps two routines off
  `src/yeaboi/ui/mode_select/__init__.py` (14k LOC, the repo's worst merge surface).
- **Never apply `claude-implement`.** Only a human does. That label is the approval gate; a routine
  applying it would be approving its own work.
- **Never push to `main`, never merge a PR, never `--force`.**
- **One coherent change per run.** No grab-bags.
- **Nothing to do is a valid, common outcome.** Exit quietly — no issue, no Slack message. A routine
  that always finds something is a routine inventing work.
- **Label every PR** `cowork` and `workstream:<name>`. The `cowork` label is what guarantees
  `claude-review.yml` reviews it: that workflow skips `dependabot[bot]` and `github-actions[bot]`,
  and an unattended job's PR may carry one of those authors. An unlabelled PR can go unreviewed with
  nothing said about it.

## Verification is not optional

`make test` and `make lint` must pass before you commit — `make test-fast` is not enough at PR time.
The full contract is in [definition-of-done.md](definition-of-done.md).
