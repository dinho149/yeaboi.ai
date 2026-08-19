# web-ux sweep

**Trigger** — cron `0 7 * * 4` (Thu 07:00 UTC)
**Summary** — bundle freshness, self-containment, and the two Python/TS wire guards
**Workstream** — [`workstreams/web-ux.md`](../../workstreams/web-ux.md)

Follow [sweep-procedure.md](../../sweep-procedure.md) with `workstream = web-ux`.

## Lenses

Run these before the scout and hand it the output — see
[hygiene-lenses.md](../../hygiene-lenses.md).

- `dead-code` — the export builders and chart primitives, which accumulate helpers a redesign
  stopped calling.
- `assertion-free-tests`
- `layering` — this charter *declares* `static-through-assets`, `headers-through-security` and
  `chrome-through-brand`, and every other sweep runs them over its own paths. What you see here is
  only the boundary crossed inside `web-ux`'s own files, which is the smallest and least likely
  place for it. The lens working looks like the other twelve sweeps staying silent.
- `duplication` — propose only.

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
