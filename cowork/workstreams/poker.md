# poker

**Owns** — `src/yeaboi/poker/` (9 files, 3.4k LOC: engine, board, server, tickets, export),
`mcp/tools_poker.py`, `tests/unit/test_poker_*.py`

**Skills** — `.claude/skills/mode-blueprints/SKILL.md`

**Cadence** — 4th and 18th of the month, 07:30 UTC

## Standing concerns

- **The live session is TUI-hosted by design.** `scrum-poker` carries a recorded skill `Exempt`:
  "live voting session is TUI-hosted by design; history stays readable via `poker_history`". A
  proposal to add a headless or CLI voting path is an already-answered question — do not re-file it.
- **Concurrency on the board server.** Multiple browsers voting at once is the whole feature. Any
  new mutation needs to be safe under simultaneous posts; races here show up as a lost vote, which
  nobody notices until the points are wrong.
- **Point write-back to Jira / AzDO must be idempotent.** Re-running a sync must not double-write or
  clobber a manually-edited estimate. This is the highest-consequence path in the mode — it writes to
  someone's real board.
- **Deck values are server-validated** and generated into `frontend/src/types/enums.ts`. They must
  **not** also ship in a boot payload; the island would win at runtime, so a stale bundle would offer
  cards the server rejects.
- **Request keys fail silently** — `test_web_request_keys.py` exists because a `payload.get(key,
  default)` quietly returning a default is how a 60-second duel turn became 90 with nothing reported.
  Check `poker`'s `actions.ts` against its handler by hand.
- **Blueprint conformance** — engine-first, schema versions bumped, fallback usable.

## Auto lane, in practice

Broken tests, dead code, doc drift. Voting rules, deck values, timers, and write-back behaviour
always propose.

## Out of scope

The `poker` React bundle and its board CSP (**web-ux**). Jira/AzDO client mechanics
(**integrations**) — you own how points are *written*, not how the client works.
