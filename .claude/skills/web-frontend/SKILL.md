---
name: web-frontend
description: Browser-surface conventions — the frontend/ → src/yeaboi/web/static/ build-and-commit seam, self-contained bundle rules and CSP, the assets.py/brand.py/security.py boundary, enums.ts codegen, payload rules, and the two Python/TS wire guards. Use when touching frontend/, src/yeaboi/web/, any exporter, or a share/board surface.
---

# Web Front End (`frontend/` → `src/yeaboi/web/static/`)

Every browser-facing page — the retro and poker live boards, the share gate, the reporting slide
deck, and the static HTML exports — is built from TypeScript in `frontend/` with Vite, and the
**built output is committed** to `src/yeaboi/web/static/`. That is what lets `pip install yeaboi`
work with no Node and keeps `make test` pytest-only: the Python suite reads the committed bundles
and never builds anything.

## The build seam

- Edited anything under `frontend/`? Run **`make web`** and commit `src/yeaboi/web/static/` in the
  same commit. CI's `web` job rebuilds and fails if they disagree.
- `make dev-board` / `dev-poker` / `dev-deck` run seeded surfaces against the real Python side;
  restart them after `make web`, since `read_asset` is cached.
- **Merge conflicts in the minified output**: never hand-resolve, and never configure a `union`
  merge driver (it produces silently corrupt JS). Always:

  ```bash
  git checkout --theirs -- src/yeaboi/web/static && make web && git add src/yeaboi/web/static
  ```

## Self-contained bundles

Bundles must stay self-contained: no CDN, no external `<link>`, no `eval`/`new Function`, no
dynamic `import()`, classic IIFE not ESM. Exports open over `file://` (where a `type="module"`
script does not execute at all) and tunnel pages run under a strict CSP.
`tests/unit/test_web_assets.py` enforces all of this statically — CSP breakage is invisible on
localhost and on a LAN, and shows up only for the remote teammate. Two carve-outs, both narrow and
both asserted rather than assumed: every document carries one `<link rel="icon">` whose href is a
`data:` URI (use `assert_self_contained()` from `tests/_pages.py`, which allows exactly that one),
and the footer credit is an `<a>` to the project site — a place to go, not something the page
loads, exempted by blanking a *single* occurrence so a second one still fails.

## The Python boundary

Python reaches the bundles only through `src/yeaboi/web/assets.py` (`read_asset`, `json_island`,
`render_page`). Never read from `static/` directly — **not even by path**, because where the bundles
live is resolved rather than fixed. `_static_dir()` tries `$YEABOI_WEB_STATIC` (a sibling checkout's
Vite `dist/`, for developing the front end against a running board), then an installed
`yeaboi_web_assets` (how they will reach a `pip install` once `frontend/` is its own repo), then
`static/` beside the module. Resolution happens once at import, so a rebuild needs a restart; a
set-but-wrong `$YEABOI_WEB_STATIC` raises rather than quietly serving the in-tree copy. The favicon
does **not** follow that path: it is not Vite output, it is generated from the website's duck art by
this repo's `gen_duck_sprites.py`, and it stays package data of `yeaboi`.

Two sibling leaf modules own the other halves
of that boundary, and a surface that re-implements either is the drift this layout exists to stop:
**`web/brand.py`** is the only place that builds a masthead payload (`build_chrome`), spells the
terminal-frame title (`frame_title`), maps a mode to its accent (`accent_mode` — including
`roadmap → planning` and `anonymize → analysis`) or names the byline (`DEFAULT_FOOTER`); and
**`web/security.py`** is the only place a served document's headers and CSPs come from
(`send_document`, `DOCUMENT_HEADERS`, `BOARD_CSP`/`GATE_CSP`/`ARTIFACT_CSP`/`EDIT_CSP`). No
request handler writes its own headers.

## Exports are inert unless a server is behind them

`ARTIFACT_CSP` sets `connect-src 'none'`, so a written file or a finished share physically cannot
make a request. Two shares talk back, and both are served under the *same* `EDIT_CSP` — identical
to `ARTIFACT_CSP` but for `connect-src 'self'`, pinned by a test that diffs the two policies whole:
an **editable** artifact (`OutputShareServer(editable=…)`), and a **correctable** standup, whose
reader answers a practice signal (`ShareDocument.corrections`, set only when the TUI passes
`session_id`+`run_id`). One policy rather than one each, because they differ in what they send and
not at all in what they may reach.

**The correctable half currently has no host**: both standup share paths went editable, and one
document cannot have two writers — an editable share replays its own edit log, a practice vote
rewrites the run beneath it. The path, its route and its tests stay because carrying a verdict
*through* the edit log (a third op beside `OP_NOTE`/`OP_FIELD`) is what would let both live on one
document; signals are answered from the TUI's Practices action until then.

`export/actions.ts` (edits) and `export/vote.ts` (verdicts) are the only network code in the export
bundle; gate any new control on the payload's capability flag — `edit`, `correctable` — or written
exports render a button that does nothing. Post via `mutate('/api/…', {…})` with a literal path and
body, and read via `payload.get("…")`, so `test_web_request_keys.py` keeps seeing the route.

## Payloads

- Server-validated tuples (grids, statuses, emojis, avatars, deck values) come from
  `frontend/src/types/enums.ts` — see *Generated artifacts* below. **Never also ship them in a boot
  payload**: the island would win at runtime, so a stale bundle would render a board that disagrees
  with what the server accepts. Payloads carry only what a codegen cannot pin.
- **Every browser surface is React** — both live boards, the reporting slide deck, the share gate,
  and all ten static exports. Their Python files are the shell plus a boot island, and no Python
  generates markup or a stylesheet any more: `html_theme` kept `escape`, `safe_url`, the
  trend-series normalisation, image embedding and `export_page`, and lost `EXPORT_CSS`, `html_page`
  and the dozen markup primitives. An exporter builds a payload of text and numbers; a component
  draws it.
- **No markup crosses the wire, and no presentation either** — the payload sends the word
  (`"high"`, `"done"`) or the number, never the colour. The one documented exception is the team
  profile's `Cell.tone`, because its thresholds are per-column *and* directional (80% completion is
  good, 80% spillover is not); it is still a word, and `Profile.tsx` gates it against `TONES`
  before it reaches a `var(--…)`.

## Generated artifacts (`contracts/web/`)

Two artifacts are written by Python and read by TypeScript, and both live in `contracts/web/` rather
than under `frontend/` — so that when the front end becomes its own repo it vendors them by sha
instead of importing Python that will not be there. `src/` reaches them only through the
`@contracts` alias (`vite.config.ts` + `tsconfig.json` `paths`, which must agree); repointing at a
vendored copy is those two lines and nothing else.

- **`enums.json`** — `scripts/gen_web_types.py` writes the data (names, values, docs);
  `frontend/scripts/gen-enums.mjs` renders `src/types/enums.ts` from it. Every TypeScript decision —
  `as const`, how a type alias is spelled, JSDoc layout — belongs to the `.mjs`; the JSON is data.
  Lookup tables travel as `[key, value]` **pairs**, not objects, because JS hoists integer-like keys
  and `BLOCK_GLYPHS` would come back with its digits first.
- **`fixtures/`** — the wire snapshots, written by `test_web_wire_shapes.py`. See below.

**Run `make web-types` after changing a board tuple**, and commit both halves. The check is split
across two jobs and neither alone is enough: the Python suite asserts the contract is fresh (in
`ALWAYS`, so nothing can dodge it), and CI's `web` job runs `gen-enums.mjs --check`. A regenerated
contract puts `contracts/web/` in the diff, which is what triggers that job — that is the link that
closes the chain, so keep `contracts/web/` in the `web` job's paths in `scripts/test_scope.py`.

## The two wire guards

One per direction. `test_web_wire_shapes.py` drives real boards through a real round, writes the
snapshots to `contracts/web/fixtures/`, and `wire.ts` asserts each one `satisfies` its interface
in `types/board.ts` — so a dropped response field fails `npm run typecheck`.
`test_web_request_keys.py` parses the request bodies out of each `actions.ts` and requires every key
to be one the handler reads — that direction fails *silently* (`payload.get(key, default)` just
returns the default), which is how a 60-second duel turn became 90 with nothing reported. The deck's
payload rides the same response-direction guard — an export is a file, so a dropped field surfaces
months later as a blank slide with no server and no log to look at.
