# platform sweep

**Trigger** — cron `0 7 * * 5` (Fri 07:00 UTC)
**Workstream** — [`workstreams/platform.md`](../../workstreams/platform.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = platform`.

## Focus

- **Surface parity audit** — this is the run that catches what everyone else shipped TUI-only. Diff
  the week's merges against `CAPABILITIES` in `tests/unit/test_surface_parity.py`. A capability with
  no MCP tool, no CLI flag, or no plugin skill and no `Exempt("reason")` is a proposal for the
  workstream that shipped it, not for you.
- **Workflow health** — for each of the 13 workflows: has it run recently
  (`gh run list --workflow <file> --limit 1`)? A scheduled workflow with no runs in a month has
  silently stopped. Check `smoke.yml`'s `if: false` is still deliberate and that `AUTO_VERSION_PAT`
  is still doing its job (without it, Claude Review stops receiving `workflow_run` events).
- **Action versions** — pinned actions that have moved on.
- **Packaging** — `make build`, then confirm the wheel contains the web bundles and nothing it
  should not. Hatchling loads `.gitignore` as build excludes, so an unanchored glob can silently drop
  files.
- **Paths** — grep for hardcoded `~/.yeaboi` outside `paths.py`.
