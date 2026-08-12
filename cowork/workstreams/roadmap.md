# roadmap

The smallest charter with the most obvious work: `roadmap` is `Exempt` on **four of five surfaces**.

**Owns** — `src/yeaboi/roadmap/` (6 files, 1.2k LOC: engine, ingest, export), the `roadmap` entry in
`_INTAKE_CARDS`, `tests/unit/test_roadmap_*.py`

**Skills** — `.claude/skills/mode-blueprints/SKILL.md`

**Cadence** — 12th of the month, 07:30 UTC

## Standing concerns

- **The four recorded gaps are this charter's standing job**, from `CAPABILITIES`:
  - no MCP tool — *"no `roadmap_analyze` tool yet — tracked follow-up gap (newer than the MCP surface)"*
  - no CLI — *"interactive source picker + intake handoff; a headless roadmap path is a tracked gap"*
  - no plugin skill — *"no plugin skill yet — tracked follow-up gap"*
  - no mode card — *this one is correct by design*: roadmap is a Planning intake card, not a
    top-level mode. Do not propose promoting it.

  Three of those four say "tracked" and have said so for a while. Keep proposing them with a concrete
  design until they are either built or the exemption is rewritten to say the gap is permanent. A
  charter's job is to stop "tracked follow-up" from meaning "never".
- **Ingest is the fragile part.** Roadmaps arrive as whatever the user has — a document, a board, a
  page. Every new source format needs a fallback that degrades to "I could not read this" rather than
  to a confidently wrong project list.
- **Ranking is a judgement**, and it feeds straight into which intake mode gets picked
  (`intake_mode_for`). A ranking change silently changes what plan a user gets; always propose.
- **Blueprint conformance** — engine-first, fallback usable, schema versioned.

## Auto lane, in practice

Broken tests, dead ingest paths, doc drift. Ranking, intake-mode selection, and any new source format
propose.

## Out of scope

The planning pipeline the intake hands off to (**planning**). Document fetching from
Notion/Confluence (**integrations**).

**integrations** may append a provider to `roadmap/ingest.py`'s `RoadmapSource` and `ingest_source()`
from a campaign run (`house-rules.md`, **Extends**) — a new source only; the ranking and the fallback
stay yours.
