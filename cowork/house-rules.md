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

1. security patch to a known guardrail gap or a CVE from `pip-audit`
2. a flaky or outright broken test
3. dead code removal
4. documentation drift (docs that contradict the code)
5. dependency bump
6. lint / type-annotation cleanup

**Everything else proposes.** If you are arguing with yourself about whether something qualifies,
it does not. Marketing always proposes.

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
- **Label every PR** `cowork` and `workstream:<name>`, so `claude-review.yml` picks it up (it only
  skips `dependabot[bot]` and `github-actions[bot]`).

## Verification is not optional

`make test` and `make lint` must pass before you commit — `make test-fast` is not enough at PR time.
The full contract is in [definition-of-done.md](definition-of-done.md).
