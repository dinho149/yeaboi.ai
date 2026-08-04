# standup

**Owns** — `src/yeaboi/standup/` (29 files, ~12.8k LOC: engine, store, habits, gaps, scheduler,
delivery, transcript intake, practice signals), `mcp/tools_standup.py`,
`claude-plugin/yeaboi/skills/standup/`, `tests/unit/test_standup_*.py`

**Skills** — `.claude/skills/mode-blueprints/SKILL.md`

**Cadence** — Wed 06:30 UTC, weekly

## Standing concerns

- **The suppress-only invariant** in relatedness matching: naming no ticket is not the same as having
  no ticket. Matching may only *suppress* a signal, never raise one. Any threshold change that could
  invert that is a finding.
- **Practice-signal precision.** The bad-habit detectors depend on four collector traps holding.
  A signal that fires on automation (service-hook comments under a member identity) is a bug, not a
  tuning problem.
- **Saved-setup applicability** — Generate offers the last setup instead of re-asking; each configure
  step has its own applicability rule. New setup fields must declare theirs.
- **Transcript intake** — bracketed paste destroys transcripts. Anything touching the paste path
  needs a test that pastes a multi-line transcript.
- **Scheduler** — the `yeaboi-standup` launchd wrapper quotes paths with spaces. Regressions here are
  silent until someone's standup does not fire.
- **Correction feedback** — `ShareDocument.corrections` is set only when the TUI passes
  `session_id` + `run_id`. A correctable share without both is an inert button.

## Auto lane, in practice

Broken tests, dead collector code, docs that describe the pre-transcript flow. Signal thresholds,
new detectors, and anything a reader sees always propose.

## Out of scope

The export bundle's markup and CSS (**web-ux**). Jira/AzDO fetching itself (**integrations**) —
standup owns how it is *interpreted*.
