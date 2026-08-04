# standup sweep

**Trigger** — cron `30 6 * * 3` (Wed 06:30 UTC)
**Workstream** — [`workstreams/standup.md`](../../workstreams/standup.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = standup`.

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
