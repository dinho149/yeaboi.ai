# web-ux sweep

**Trigger** — cron `0 7 * * 4` (Thu 07:00 UTC)
**Workstream** — [`workstreams/web-ux.md`](../../workstreams/web-ux.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = web-ux`.

## Focus

- **Bundle freshness** — run `make web-check`. Stale committed bundles are auto lane.
- **Self-containment** — run `tests/unit/test_web_assets.py` and read what it does *not* cover. A new
  external reference that slipped past it is the highest-value finding here, because CSP breakage is
  invisible on localhost and only appears for the remote teammate.
- **The silent direction** — `test_web_request_keys.py` catches request keys no handler reads.
  Re-check each `actions.ts` against its handler by hand once a month; a `payload.get(key, default)`
  that quietly returns the default is exactly the bug class this guard exists for.
- **Payload purity** — grep boot payloads for anything that is markup, a colour, or a tuple already
  generated into `frontend/src/types/enums.ts`.
- **Site health** — `make site-check` green; check `docs/` pages against the code they describe.

## Extra stop conditions

- Any `frontend/` change must be committed **with** its rebuilt `src/yeaboi/web/static/`. If
  `make web` cannot run in this environment, propose instead of building.
