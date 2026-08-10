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

### Milestone 1 — Substrate (taste-neutral, do now)
- Routing + app shell architecture (structure, not styling)
- Client state + data layer against the existing Python API
- Persistence: projects/sessions beyond the current file-scoped model
- Auth beyond the share gate
- Loading / empty / error states as *structural* slots, unstyled

### Milestone 2 — App chrome primitives (thin skin)
The vocabulary that does not exist yet: `Button`, `Input`, `Field`, `Select`,
`Modal`, `Toast`, `Nav`, `Menu`, `EmptyState`, `Skeleton`, `Tabs`.
Build them **token-driven and unopinionated**, so restyling is a token edit.

### Milestone 3 — Archetype surfaces
Wire the four archetypes to real routes:
document · dashboard · live/interactive · chrome.
Use `~/yeaboi-design-inventory/` as the content fixtures — real worst-case data.

### Milestone 4 — Motion layer ("fluid")
GSAP (skills installed: gsap-core, gsap-timeline, gsap-scrolltrigger,
gsap-react, gsap-performance). View transitions, shared-element movement between
routes, no hard page loads. Must respect `prefers-reduced-motion` via
`gsap.matchMedia()`.

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
