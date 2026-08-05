# web-ux

**Owns** — `frontend/` (5 bundles: deck, export, gate, poker, retro), `src/yeaboi/web/assets.py` and
`brand.py`, `html_theme.py`, `html_exporter.py`, `charts.py`, `names.py` (the join-screen word
lists both boards ship), `docs/` (the yeaboi.ai site), `scripts/dev_*.py`, `gen_web_types.py`,
`gen_site_seo.py`, `gen_og_card.py`

**`web/security.py` is not yours** — it belongs to **security**, whole. It used to be split here as
"the markup side", which was the one overlapping claim in fifteen charters and the one place two
routines could have collided on a file. A header or CSP change is a security proposal.

**Cadence** — Thu 07:00 UTC

## Standing concerns

- **`make web` and commit the bundles.** Any `frontend/` edit must ship the rebuilt
  `src/yeaboi/web/static/` in the *same* commit. CI's `web` job fails otherwise.
- **Bundles stay self-contained** — no CDN, no external `<link>`, no `eval`/`new Function`, no
  dynamic `import()`, classic IIFE not ESM. Exports open over `file://` where a `type="module"`
  script does not execute at all. `tests/unit/test_web_assets.py` enforces this statically, because
  CSP breakage is invisible on localhost and only shows up for the remote teammate.
- **Two carve-outs, both asserted**: one `<link rel="icon">` with a `data:` URI (use
  `assert_self_contained()`), and the footer credit `<a>` — exempted by blanking a *single*
  occurrence so a second one still fails.
- **No markup and no presentation crosses the wire.** Payloads send the word (`"high"`, `"done"`) or
  the number, never the colour. The single documented exception is `Cell.tone`.
- **Server-validated tuples come from `frontend/src/types/enums.ts`**, generated with a `--check` in
  CI. Never *also* ship them in a boot payload — the island would win at runtime.
- **The two boundary guards.** `test_web_wire_shapes.py` catches dropped response fields;
  `test_web_request_keys.py` catches request keys no handler reads — that direction fails *silently*,
  which is how a 60-second duel turn became 90 with nothing reported.
- **Merge conflicts in minified output**: never hand-resolve, never a `union` merge driver. Always
  `git checkout --theirs -- src/yeaboi/web/static && make web && git add src/yeaboi/web/static`.
- **`read_asset` is `lru_cache`d** — restart dev servers after `make web`.
- **Site SEO** — `make site-check` must stay green; the terms *not* to chase are recorded in the SEO
  notes.

## Auto lane, in practice

Stale committed bundles, a broken frontend test, dead components, `gen_site_seo.py --check` drift.
Visual design, copy, layout, and anything on the public site always propose.

## Opportunity space

Where a `[feature]`/`[improvement]` find is most likely to be real here: parity gaps with the TUI
surface (something the terminal shows that the shared page silently lacks), share and export flows
that dead-end (a reader who cannot get from a board to the thing it references), and static exports
a user has to hand-edit after the fact. The evidence bar in `cowork-scout.md` applies — name the
friction, the gap, or the repeated step.

## Out of scope

CSP *policy* and header construction (**security**) — you own the markup it protects. Marketing copy
(**marketing**, which drafts to Notion and never edits `docs/index.html`).
