# reporting

**Owns** — `src/yeaboi/reporting/` (14 files, 3.8k LOC: engine, presentation, themes, pptx),
`mcp/tools_reporting.py`, `claude-plugin/yeaboi/skills/delivery-report/`,
`tests/unit/test_reporting_*.py`

**Skills** — `.claude/skills/mode-blueprints/SKILL.md`

**Cadence** — 3rd and 17th of the month, 07:30 UTC

## Standing concerns

- **An export is a file with no server and no log behind it.** A dropped payload field surfaces
  months later as a blank slide that nobody can debug. The deck payload rides the response-direction
  wire guard (`test_web_wire_shapes.py`) — confirm every new deck field is in the fixture.
- **Native `.pptx` and the HTML deck must agree.** A field rendered in one and not the other is a
  finding; they are two renderers over one payload, not two features.
- **Custom palettes** must survive both renderers and stay legible in light and dark. A palette that
  only works in the deck is half-shipped.
- **Range selection** — last sprint, last month, last week, custom range. Off-by-one at range
  boundaries is the recurring bug class here; every range needs a test that pins the clock.
- **Blueprint conformance** — engine-first (parse → fallback → format), TUI/CLI/MCP as thin adapters.
  Logic that migrated into a `_build_*_screen` is drift.
- **Fallback must be usable** — `llm_mode: "fallback"` produces a deterministic skeleton report, not
  an empty shell. Test it.

## Auto lane, in practice

Broken tests, dead theme code, doc drift, a missing wire fixture for an existing field. Slide layout,
copy, palettes, and report structure always propose — a stakeholder deck is the most visible thing
yeaboi produces.

## Out of scope

The `deck` bundle's React components and CSS (**web-ux**). Tracker fetching (**integrations**).

**integrations** may append a provider to `reporting/activity.py`'s `SOURCE_COMPONENTS` and
`_canonical_source()` from a campaign run (`house-rules.md`, **Extends**) — a new source and its
spelling only.
