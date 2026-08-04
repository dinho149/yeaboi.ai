# retro

**Owns** — `src/yeaboi/retro/` (8 files, 2.5k LOC: engine, board, server, tunnel, store),
`mcp/tools_retro.py`, `tests/unit/test_retro_*.py`

**Skills** — `.claude/skills/mode-blueprints/SKILL.md`

**Cadence** — 5th and 19th of the month, 07:30 UTC

## Standing concerns

- **The live board is a documented TUI-only `Exempt`** — "live board is TUI-only by design; history
  stays readable via `retro_history`". Proposals to add a CLI or headless board are already-answered
  questions. Do not re-file them.
- **Tunnel lifecycle.** The board runs on loopback and is exposed through a temporary tunnel. A
  tunnel that outlives its session, or a process that leaks after the TUI exits, is the highest-value
  finding here — it leaves a team's unfiltered retro reachable.
- **Grid values are server-validated** and generated into `frontend/src/types/enums.ts` — never also
  shipped in a boot payload.
- **Carried action items** cross sessions (`carried_action_items_for_session`). A change to session
  identity or store schema can silently orphan them; that is invisible until someone asks why last
  sprint's actions vanished.
- **Concurrency** — teammates add cards simultaneously. Any new mutation must be safe under
  simultaneous posts.
- **Anonymity expectations.** People say things in a retro they would not sign. Anything that newly
  attributes a card, in the store or an export, is a proposal with that consequence stated.

## Auto lane, in practice

Broken tests, dead code, doc drift, a leaked-process guard with a test. Board behaviour, card
attribution, and the action-item flow always propose.

## Out of scope

The `retro` React bundle and its CSP (**web-ux**). Tunnel *access control* — `sharing/access.py` and
`gate.py` are **security**'s.
