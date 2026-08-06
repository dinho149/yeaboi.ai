# Definition of Done

The single contract. Every cowork routine and every `/ship` run must satisfy all ten items.
Nothing is "done" because the code works — it is done when the loop is closed.

| # | Item | How it is checked |
|---|---|---|
| 1 | **Linear ticket** exists on team `Yeaboi`, labelled `workstream:<name>`, with the PR attached, `Closes YEA-NN` in the PR body, and a state that tracks reality — In Progress while building, In Review from PR open, Done on merge | scribe: `save_issue` (state + `links`); attach + merge close via the Linear GitHub integration reading `Closes YEA-NN` |
| 2 | **Tests** — a unit test per new function (happy + error), render tests for every `_build_*_screen`, mock tests for LLM-dependent code (success / error fallback / code fences), round-trip tests for new state fields | `make test` |
| 3 | **Lint** | `make lint` |
| 4 | **Security** — ruff SAST + `pip-audit` clean, CodeQL not regressed | `make security` |
| 5 | **Surface parity** — new capability registered in `CAPABILITIES` (+ `PARAM_PAIRS`) in `tests/unit/test_surface_parity.py`, plus a `FeatureTip` in `src/yeaboi/ui/shared/_tips.py`; or a recorded `Exempt("reason")` | `make test` fails without it |
| 6 | **Observability** — the three pillars from `CLAUDE.md`: `logger.info()` on every user action, log paths from `paths.py`, tests | review |
| 7 | **Web bundles** — anything under `frontend/` ⇒ `make web` and the rebuilt `src/yeaboi/web/static/` committed in the *same* commit | CI `web` job |
| 8 | **Notion** — page created or updated under 🤙 yeaboi for any user-facing change | scribe |
| 9 | **Slack** — one `#yeaboi-claude` post: what shipped, PR link, Linear link | scribe |
| 10 | **Review feedback** — every finding the PR's reviewers rated blocker or should-fix is fixed or answered, and every human review thread is resolved by someone who replied to it | the `pr-feedback` commit status (`scripts/pr_feedback.py`), required by the `main-branch` ruleset |

## Rules

- **Items 1, 8 and 9 are always done by the `cowork-scribe` agent**, never inline. See [crew.md](crew.md).
- **Item 1 happens first**, before any code is written — which means at **approval**, not at proposal.
  The auto lane opens the ticket before the builder starts. The propose lane opens it when
  `claude-implement` lands, because that is when code starts. A proposal is not work in flight; it is
  a question, and most of them are answered no.

  Filing a ticket per proposal cost four writes across two trackers for every idea, and left a dead
  ticket behind each of the ~70% that age out unapproved. GitHub issues are the queue — Linear
  carries work, not candidates.
- **Items 8 and 9 happen on merge**, not on PR open — driven by the `pr-merged-close-loop` routine.
- **The ticket's state is part of item 1**, not an optional courtesy: the scribe opens it In
  Progress and moves it to In Review when the PR is attached; the merge → Done transition belongs
  to the Linear GitHub integration, fired by the `Closes YEA-NN` line every PR body must carry.
  `pr-merged-close-loop` verifies that transition and repairs it when the magic word missed — a
  ticket lingering in In Review after its PR merged is a bug in the loop, not a cosmetic detail.
- Items 2–7 are the *gate*: they block the PR. A PR that cannot pass them is not opened; the finding
  is filed as a proposal instead.
- **Item 10 gates the merge, not the open** — it is the one item that cannot be satisfied before the
  PR exists. `claude-review.yml` fires on `workflow_run` after CI succeeds, so its review arrives
  minutes after `/ship` has already exited, and a human's comment can arrive days later. Answering it
  is a second sitting: `/pr-feedback <n>`, or `/babysit-prs fix` across every open PR.

  This item is the youngest of the ten and the only one that was ever *silently* skipped rather than
  argued about. Five things comment on a PR here and, before the `pr-feedback` status existed, nothing
  read a word of any of it back — the reviewers are all deliberately advisory, so findings landed on
  the timeline and PRs merged straight past them. The rest of the contract is enforced by `make test`
  and CI; this one had nothing behind it at all, which is precisely why it needed a required check
  rather than another sentence in a markdown file.

  It is answered, not obeyed: a finding you disagree with is closed by a reply saying why, which the
  next review pass reads and stops raising. What is not allowed is silence.
- **Exemptions are recorded, not assumed.** If an item genuinely does not apply (e.g. item 7 on a
  Python-only change), say so in the PR body in one line. Silence is not an exemption.

## Targets

| System | Target | ID |
|---|---|---|
| Linear | team **Yeaboi** | `a324293a-0fd3-41d3-8730-58192a1babeb` |
| Slack | **#yeaboi-claude** | `C0BMADQQN1Z` |
| Notion | page **🤙 yeaboi** | `3b01bf92-1b06-8163-af24-ea0a77641e17` |
