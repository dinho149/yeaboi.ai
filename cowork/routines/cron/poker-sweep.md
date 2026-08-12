# poker sweep

**Trigger** — cron `30 7 4,18 * *` (4th and 18th, 07:30 UTC)
**Summary** — planning-poker point write-back, board concurrency, and the live surface
**Workstream** — [`workstreams/poker.md`](../../workstreams/poker.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = poker`.

## Focus

- **Write-back idempotence** — re-running a point sync must not double-write or clobber a manually
  edited estimate. This path writes to someone's real board; it is the highest-consequence code in
  the mode.
- **Concurrency** — every board mutation checked for safety under simultaneous posts from several
  browsers. A lost vote is invisible until the points are wrong.
- **Request keys** — read `frontend/src/poker`'s `actions.ts` against its handler by hand. This
  direction fails silently: `payload.get(key, default)` just returns the default.
- **Deck values** — confirm they come from `frontend/src/types/enums.ts` only, and are not *also* in
  a boot payload where a stale bundle would offer cards the server rejects.

## Extra stop conditions

- The live voting session is a recorded TUI-only `Exempt`. Do not file proposals for a headless or
  CLI voting path — that question is already answered.
