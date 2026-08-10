# yeaboi.ai — TUI/CLI to app

The contract for the autonomous build. Re-read this at the start of every turn.
It, not the chat log, is the source of truth.

## Brief

**Web first. Desktop (Tauri, wrapping the same bundles) as a later milestone.**

Creative direction, in the user's words: **bespoke, unique, atypical, fluid.**
Not a template. Not generic SaaS. Motion is part of the product, not decoration.

The user will do a design pass **after** the structure exists, and will correct
direction then. That fact dictates the build order below.

## Build order, and why

An agent building unsupervised regresses to the statistical mean of its training
data — which is the exact opposite of "bespoke and atypical". So the visual layer
is the one thing NOT to lock in early.

Therefore: **build the taste-neutral substrate first, keep the skin thin and
swappable, and let the human's pass land on a system that is cheap to re-skin.**

Concretely, the ordering rule is: *if a decision would be expensive to reverse
after a design pass, defer it; if it is invisible to a design pass, do it now.*

### Milestone 1 — Substrate (taste-neutral) — **DONE**
- [x] Server routing — `app/router.py`, a table not an if-chain; auth declared
      per route and asserted exhaustive by `PUBLIC_ROUTES`
- [x] Persistence — `app/store.py`, SQLite in its own `app.db`; reads scoped to
      the asker, no fetch-by-id, a foreign project 404s rather than 403s
- [x] Auth — `app/auth.py` one-time tokens + `app/sessions.py` HttpOnly cookie
      and CSRF double submit. **`TODO(auth)` is closed**: sign-in proves the
      address rather than trusting it.
- [x] Client data layer — `frontend/src/app/api.ts`
- [x] Client routing + shell — `router.ts`, `App.tsx`, deep-refresh via
      `SHELL_ROUTES`
- [x] Loading / empty / error as one typed union — `useAsync.ts`, `Slots.tsx`
- [x] Two design guards: no raw literals under `app/`, and no undefined token

- [x] Importing a TUI plan into the app (`app/importer.py`) — copies, never
      shares, so `yeaboi` keeps working offline

- [x] Delivery — `SmtpDeliverer` reuses the project's `STANDUP_SMTP_*` config
      and stdlib `smtplib`. Set `YEABOI_APP_BASE_URL` to switch it on.

### Milestone 2 — App chrome primitives (thin skin) — **DONE**
- [x] `Button` (3 variants), `Field`/`Input`/`Select`, `Modal`, `Toast`,
      `Skeleton`, `Tabs` — in `design/primitives/`, exported from the barrel
- [x] 22 behaviour + axe tests (`Chrome.test.tsx`)
- [x] The shell adopts them; its ad-hoc CSS is deleted
- [x] jsdom `<dialog>` shim in `test/setup.ts`

Contrast needed no new tests: `theme.test.ts` already audits every pair these
use across all five themes. `Nav`/`Menu` deferred — the rail is two links, and
building a menu primitive before a screen needs one is speculation.

### Milestone 3 — Archetype surfaces — **3 of 4**
- [x] **Chrome** — shell, rail, masthead, sign-in, project list (M1/M2)
- [x] **Structured document** — `plan`, `roadmap`, `standup`, `retro`,
      `anonymize` render through `export/Report.tsx` from a stored payload
- [x] **Quantitative dashboard** — `performance`, `profile`, `poker`,
      `reporting` — same path, same renderer
- [x] **Live / interactive** — wired as a **registry** (`rooms`): the app lists
      running boards and hands over. Option 1 below, chosen because options 2
      and 3 both need this table first, so it forecloses neither.

**Still open: whether to go further than a registry.** The boards are separate
`ThreadingHTTPServer`s with their own in-memory state, their own query-token
auth and a long-poll loop. They are *ceremonies*: one host, one room, gone when
the TUI closes. Bringing them inside the app is a genuine port — session
registry, auth reconciliation, and a decision about whether a board's state
becomes durable — not a route. It needs a product decision first:

1. **Link out** — the app lists a running board and hands over. Cheapest,
   keeps two auth models.
2. **Embed** — iframe the existing server. Fast, but two CSPs and two sessions.
3. **Port** — boards become app routes over the app's store. Correct, largest.

The post-ceremony *reports* already render as artifacts, so the read path is
covered; what is missing is the live room.

### Milestone 4 — Motion layer ("fluid") — **foundation done**
- [x] `motion.ts` — `MOTION` tokens, `entranceVars` (pure), `enter`, `enterList`
- [x] `useMotion.ts` hooks; project list and artifact view adopt them
- [x] 16 tests, both reduced-motion branches asserted
- [x] Vite `renderChunk` strips GSAP's doc URL so the bundle keeps no external
      origin and the fetch guard keeps its teeth
- [x] Shared-element / FLIP transitions — a project row travels into the
      detail heading via `data-flip-id`. `flushSync` makes the route swap
      synchronous, which is the assumption FLIP rests on and which
      `flip.test.tsx` pins because it fails silently.

**Cost, stated plainly:** `app.js` 95 KB → 191 KB (core + Flip). The entrances
CSS could have done; the shared-element move it could not, because the two
elements live in different subtrees. If the design pass wants less motion, this
is one dependency to remove.

### Milestone 5 — Desktop (Tauri)
The existing self-contained IIFE constraint means bundles already run without a
server, so most of this is done accidentally.

## Invariants — do not violate

- **No raw colour, font stack, or spacing literal outside `frontend/src/design/`.**
  This is what keeps the re-skin cheap. Audited baseline: 137 hex literals, 95 in
  `palette.css`, 42 in `tokens.css`, and 4 legitimate exceptions elsewhere.
- Edited `frontend/`? Run `make web` and commit `src/yeaboi/web/static/` in the
  **same commit**. CI fails otherwise.
- Bundles stay self-contained: no CDN, no external `<link>`, no `eval`, no
  dynamic `import()`, classic IIFE not ESM.
- Server-validated tuples come from `types/enums.ts` codegen — never also ship
  them in a boot payload.
- No markup and no presentation crosses the wire. Payloads carry the word or the
  number, never the colour.
- Merge conflicts in minified output: never hand-resolve.
  `git checkout --theirs -- src/yeaboi/web/static && make web && git add src/yeaboi/web/static`
- New primitives ship with an axe test and enter the contrast matrix across all
  five themes (WCAG AA: 4.5:1 text, 3:1 non-text).

## Verification gate — every milestone

```
make lint && make test && make web-check
```
All three green, then commit. `make web-check` is non-negotiable: it fails on
stale committed bundles, which is how an autonomous run silently corrupts a repo.

## Working agreement

- Branch `app-shell`. Commit freely. **Do not merge to `main`** — the user merges.
- Do not redesign existing surfaces without being asked; add alongside.
- When genuinely blocked on taste, build the structural version, leave a `TODO(design)`,
  and keep moving. Do not stall waiting for input.

## Open questions (do not block on these)

- Tokens vs thesis: unanswered. Assume **thesis is up for grabs** but do not
  destroy the terminal identity until told — keep it swappable instead.
- Do the 5 themes stay user-facing in the app, or does the app pin one?


## Where this stands

**Done:** milestones 1, 2, 3 (all four archetypes), and the motion foundation.
12 commits on `app-shell`; `main` untouched.

**Open, in the order I would take them:**

1. **Milestone 5, Tauri** — the only named milestone left. The self-contained
   IIFE bundles already run without a server, so much of it is done
   accidentally; it needs a Rust toolchain, which is a real install to agree to.
2. **Live rooms beyond a registry** — embed or port, if a registry is not
   enough. Needs the product decision recorded under Milestone 3.
3. **Hosting** — `app/importer.py` reads `~/.yeaboi` on the server's own disk.
   Correct single-tenant, wrong the moment it is hosted for someone else.
4. **A `yeaboi app` CLI command** — `serve()` exists and nothing calls it, so
   the app is reachable only from Python.
