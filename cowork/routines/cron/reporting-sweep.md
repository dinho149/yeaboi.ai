# reporting sweep

**Trigger** — cron `30 7 3,17 * *` (3rd and 17th, 07:30 UTC)
**Workstream** — [`workstreams/reporting.md`](../../workstreams/reporting.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = reporting`.

## Focus

- **Renderer agreement** — diff what the `.pptx` renderer emits against what the HTML deck renders
  from the same payload. A field in one and not the other is the finding this sweep exists for.
- **Wire fixtures** — every deck payload field must appear in `frontend/src/test/fixtures/`. An
  export is a file with no server and no log, so a dropped field surfaces months later as a blank
  slide nobody can debug.
- **Range boundaries** — walk last-sprint, last-month, last-week and custom range for off-by-one at
  the edges. Every range needs a test that pins the clock (fixtures rewrite themselves otherwise).
- **Palette legibility** in light and dark, in both renderers.
- **Fallback** — confirm `llm_mode: "fallback"` still produces a usable deterministic report.

## Extra stop conditions

- Rotate the focus: renderer agreement on the 3rd, ranges and palettes on the 17th. Both in one run
  is a grab-bag.
