# analysis sweep

**Trigger** — cron `30 6 * * 4` (Thu 06:30 UTC)
**Summary** — the team-analysis metrics, the AI-usage markers, and doc scoring
**Workstream** — [`workstreams/analysis.md`](../../workstreams/analysis.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = analysis`.

## Focus

- **Marker precision** — read the AI-usage detectors (Codex / agent / branch markers) and look for
  patterns that over-match. A false positive here discredits the whole per-member table.
- **Small-sample honesty** — every metric must have a path that says "not enough data" rather than
  rendering a confident percentage. Enumerate metrics, enumerate honesty paths, report the gap.
- **Repo targeting** — confirm scanning still follows AzDO PR + commit activity, not alphabetical
  order.
- **Payload discipline** — confirm `Cell.tone` is still the only presentation field crossing the
  wire, and that `Profile.tsx` still gates it against `TONES`.
- **Dual-source separation** — a Jira + AzDO run must keep the two visibly distinct in output.
