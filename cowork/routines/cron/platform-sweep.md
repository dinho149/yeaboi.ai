# platform sweep

**Trigger** — cron `0 7 * * 5` (Fri 07:00 UTC)
**Summary** — surface parity and workflow health — the run that catches TUI-only features
**Workstream** — [`workstreams/platform.md`](../../workstreams/platform.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = platform`.

## Lenses

Run these before the scout and hand it the output — see
[hygiene-lenses.md](../../hygiene-lenses.md).

- `dead-code` — `paths.py` and `config.py` are excluded by policy and that exclusion is the
  interesting one: an export there with no caller is a mode that has not adopted the convention,
  and the fix is at the caller. If you want to file that, it is a `docs` or `chore` proposal
  against the owning workstream, not a deletion here.
- `assertion-free-tests` — `tests/parity/` is the byte-parity gate; a parity test that asserts
  nothing is a gate that has silently stopped being one.
- `layering` — this charter *declares* `paths-through-paths-py`, and every other sweep now runs it
  over its own files. What you see here is only the boundary crossed inside `platform`'s own paths,
  and `paths.py` itself is exempt because it *is* the boundary. Silence here with a find in
  **tui-ux** is the invariant working, not the lens missing something.
- `duplication` — propose only. `cli.py` is where two subcommands get written from one another, and
  the lens has no opinion on whether they should be one function; that is the reading you are being
  handed.

## Focus

- **Surface parity audit** — this is the run that catches what everyone else shipped TUI-only. Diff
  the week's merges against `CAPABILITIES` in `tests/unit/test_surface_parity.py`. A capability with
  no MCP tool, no CLI flag, or no plugin skill and no `Exempt("reason")` is a proposal for the
  workstream that shipped it, not for you.
- **Workflow health** — for each of the 12 workflows: has it run recently
  (`gh run list --workflow <file> --limit 1`)? A scheduled workflow with no runs in a month has
  silently stopped. Check `smoke.yml`'s `if: false` is still deliberate and that `AUTO_VERSION_PAT`
  is still doing its job (without it, Claude Review stops receiving `workflow_run` events).
- **Action versions** — pinned actions that have moved on.
- **Packaging** — `make build`, then confirm the wheel contains the web bundles and nothing it
  should not. Hatchling loads `.gitignore` as build excludes, so an unanchored glob can silently drop
  files.
- **Paths** — grep for hardcoded `~/.yeaboi` outside `paths.py`.
