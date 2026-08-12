# retro sweep

**Trigger** — cron `30 7 5,19 * *` (5th and 19th, 07:30 UTC)
**Summary** — the retro tunnel and server lifecycle, and carried action items
**Workstream** — [`workstreams/retro.md`](../../workstreams/retro.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = retro`.

## Focus

- **Tunnel and process lifecycle** — trace every path that opens the loopback server or a tunnel and
  confirm each has a teardown that survives an exception and a TUI crash. A tunnel outliving its
  session leaves a team's unfiltered retro reachable; that is the top finding in this workstream.
- **Carried action items** — confirm `carried_action_items_for_session` still resolves across a
  session-identity or store-schema change. Orphaned actions are invisible until someone asks where
  last sprint's went.
- **Concurrency** on card creation and voting under simultaneous posts.
- **Grid values** come from `frontend/src/types/enums.ts` only, never also from a boot payload.

## Extra stop conditions

- The live board is a recorded TUI-only `Exempt`. Do not file proposals for a CLI or headless board.
- Anything that newly attributes a card — in the store, an export, or a log — proposes with that
  consequence stated. People say things in a retro they would not sign.
