# standup sweep

**Trigger** — cron `30 6 * * 3` (Wed 06:30 UTC)
**Summary** — practice-signal precision and the suppress-only relatedness invariant
**Workstream** — [`workstreams/standup.md`](../../workstreams/standup.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = standup`.

## Lenses

Run these before the scout and hand it the output — see
[hygiene-lenses.md](../../hygiene-lenses.md).

- `dead-code` — the deterministic core is excluded by policy (it has a Go twin), so this reads the
  fetchers, the review path and the exporters, which have no twin and the most churn.
- `assertion-free-tests`
- `layering`
- `duplication` — four exporters and four renderers grew alongside each other here, and this is the
  charter where the clones the lens finds are most likely to be one function. Propose only.

## Focus

- **Signal precision** — read the practice-signal detectors and the four collector traps they depend
  on. Any detector that could fire on automation (service-hook comments under a member identity) is
  the highest-value finding in this workstream.
- **The suppress-only invariant** — walk the relatedness-matching thresholds and confirm no path lets
  matching *raise* a signal.
- **Applicability rules** — every saved-setup configure step declares one. A step added without one
  is a finding.
- **Feedback loop** — practice feedback (thumbs up/down) must reach the store and change future runs.
  Confirm the path end to end, including `ShareDocument.corrections` requiring both `session_id` and
  `run_id`.
