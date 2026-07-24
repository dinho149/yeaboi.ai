# TUI Mascot — Design Spec

**Date:** 2026-07-24
**Branch:** `feature/tui-mascot`
**Status:** Design refined after visual prototyping; spec under review

## Goal

Bring the website's pixel-duck mascot (green mallard in wayfarer sunglasses) into
the terminal app as a crisp, on-brand, characterful presence. Two deliverables,
sharing one sprite source so the duck can never drift into two different ducks:

1. **A small companion sprite** on the welcome (mode-select) screen — as small
   as possible while still reading as *the* mascot (multi-row, never a one-line
   ASCII face), keeping the signature sunglasses.
2. **A replaced, cleaner idle screensaver duck** — a crisp full-body mallard with
   idle life (bob, wing flap, glint), instead of today's noisy procedural one.

## Why the approach changed

The current `_screensaver.py` draws the large duck **procedurally** — overlapping
ellipses/polygons splatted onto a 42×30 grid, then half-block packed. Visual
prototyping (rendering the real output to PNG via Rich's SVG export + `qlmanage`
and inspecting it) showed this is intrinsically **noisy**: streaky outline rows,
speckled wing, lumpy sunglasses. Nudging coordinates produced only comedy
("Matrix sunnies"). The clean assets in the codebase (`_COMPACT_DUCK`) and the
brand favicon are clean for one reason: they are **explicit pixel grids**, not
math.

**Decision:** stop drawing the duck procedurally. Derive it from the finished
brand art (`docs/assets/duck-touch.png`, 180×180 RGBA) and ship it as frozen
pixel-grid data.

## Visual style (locked via prototyping)

- **Flat cartoon.** Every pixel is snapped to a small curated brand palette
  (~10 colours: near-black outline, two head greens, dark lens, belly/glint
  white, three body blue-greys, two oranges) so each region is one flat colour —
  no anti-aliased gradients. Prototype confirmed this reads as a clean cartoon
  mallard.
- **Facing left**, matching the existing screensaver/compact duck orientation.
- **Two shipped sizes:**
  - **Screensaver — full body, width ≈ 34 px** (~17 half-block text rows). Crisp
    at large terminal sizes.
  - **Companion — head-only crop, width ≈ 12 px** (~5 text rows). The
    sunglasses + bill carry the identity, so a head reads at roughly half the
    footprint of the smallest legible full body (≈ w16 / ~8 rows). This is the
    "as small as possible" answer.

## Sprite production pipeline (design-time, not runtime)

Runtime ships **no Pillow dependency and no image IO**. Production is a
design-time step whose *output* — frozen pixel data — is committed.

1. **Trace** `duck-touch.png`: crop to alpha bbox, (optionally crop to head
   region), flip to face left, snap every pixel to the curated palette, then
   downscale with NEAREST so blocks stay solid. (Prototype: `scratchpad/tui/duck.py`.)
2. **Hand-clean** the traced grid: remove stray speckle (auto-trace gets ~85% of
   the way; hand-cleaning the ~40 remaining stray pixels makes it crisp), unify
   region colours, tidy the sunglasses and bill edges. Done via the same
   render-and-view loop (render grid → SVG → PNG → inspect → fix).
3. **Freeze** the cleaned grids as module-level constants in `_mascot.py` — a
   compact string-art form keyed to a palette dict, exactly how `_FULL_DUCK` /
   `_COMPACT_DUCK` are frozen today. No runtime tracing.

The trace script stays in the repo (or scratchpad) as the documented way to
regenerate the base grid if the brand art changes, but it is a dev tool, not an
import.

## Architecture

### New module: `ui/shared/_mascot.py`

The single source of truth for "how the duck is drawn".

- `MASCOT_PALETTE` — the curated flat palette (letter → rgb), superseding
  `_screensaver._PALETTE`.
- **Frozen sprite data** — `DUCK_FULL` (screensaver, ~w34) and `DUCK_HEAD`
  (companion, ~w12) as pixel grids (rows of palette letters, `.` = transparent).
- Animation-frame variants layered on the base grid (see below).
- `_half_block_rows(grid)` — the ▀/▄ packer (moved here from `_screensaver.py`),
  turning a pixel grid into Rich `Text` rows.
- `render_full(frame) -> Group` and `render_head(frame) -> Group` — return the
  packed renderable for a given animation frame.

`_screensaver.py` keeps `IdleController`, idle plumbing, `build_screensaver`,
captions/hints and the size-tier decision; it imports sprites from `_mascot.py`
instead of drawing them. The procedural `_high_resolution_duck`,
`_fill_ellipse`, `_fill_polygon`, `_inside_polygon`, `_FULL_DUCK`,
`_COMPACT_DUCK`, `_duck_art`, `_pixel_line` are **deleted**. `_music_bar.py` is
untouched.

### Animation frames

Kept simple and hand-authored as small deltas over the base grid (no procedural
motion):

- **Full (screensaver):** a gentle vertical **bob** (blank row inserted on some
  frames), a **wing flap** (one alternate wing-position grid), and a **glint**
  sweep across the sunglasses (a moving W pixel). ~6–8 frames driven by
  `idle_controller.animation_elapsed()`.
- **Head (companion):** a subtle idle — a slow glint on the shades and a 1-row
  bob — driven by the mode screen's existing `shimmer_tick`. Minimal; it should
  feel alive, not busy.

Caption variety on the screensaver ("YEABOI · chilling" / "· zzz" / "· vibing")
rotates on a slow timer. Hint stays "press any key".

### Companion on the welcome screen

`_build_mode_screen()` renders `render_head(frame)` in the top whitespace
(`mid_top` region), horizontally centred, above the mode rows, on a fixed row
budget (≤6 rows) subtracted from `mid_top` so mode rows never shift. Omitted when
the terminal is too short to spare the rows. Animated off the existing
`shimmer_tick` (no new timer).

### Size tiers (screensaver)

`build_screensaver` keeps its tier logic but backed by the new sprites:
full-body `DUCK_FULL` at large sizes; the head sprite (or a scaled full body) at
the compact tier; the existing tiny `YEABOI` text label at the smallest tier.

## Data Flow

```
shimmer_tick (mode screen) ─────> frame ─> render_head  ─> welcome header
animation_elapsed (IdleController) ─> frame ─> render_full ─> build_screensaver ─> MusicLive.get_renderable (idle)
```

`_mascot.py` is pure/stateless: `(frame)` → Rich `Group`. All timing stays in the
callers' existing clocks. No new state, persistence, or config toggle in v1.

## Error / Edge Handling

- **Small terminals:** companion omitted when the row budget can't be spared;
  screensaver falls back head → tiny label as today.
- **Music bar / subtitle:** unaffected — companion sits inside the Panel body;
  the screensaver path already replaces the whole renderable.

## Testing (three-pillars)

- `tests/unit/ui/shared/test_mascot.py` (new):
  - `render_full(frame)` / `render_head(frame)` return a `Group`; head is `>1`
    and `≤6` text rows for every frame; full matches its expected packed height.
  - Every palette letter used in `DUCK_FULL` / `DUCK_HEAD` (and frame variants)
    exists in `MASCOT_PALETTE`; every grid row is equal length.
  - Frame index is deterministic (pure function).
- `test_screensaver.py`: keep passing after the extraction/deletion; assert a
  flap frame differs from a rest frame and captions rotate.
- Welcome-screen render test: `_build_mode_screen(...)` returns a Panel of
  exactly `height` rows with the companion present at normal height and absent at
  a short height (no mode-row shift either way).
- Optional dev aid: a script that renders sprites to SVG/PNG for eyeballing is a
  scratch tool, not a committed test.

## Logging / Observability

Pure render-path functions → **no per-frame logging** (never-log-per-frame rule).
No log-dir/path changes, no LLM/API calls.

## Surface Parity

TUI-only visual flourish; nothing headless to expose. Does **not** enter the
`CAPABILITIES` registry and needs no `FeatureTip` — a deliberate, reasoned
absence (cf. the retro live board being TUI-only). If `test_surface_parity.py`
disagrees, record `Exempt("TUI-only mascot flourish")` rather than inventing
surfaces.

## Out of Scope (v1)

- App-wide corner overlay following you across every screen (Rich can't
  free-overlay cleanly without touching ~30 builders).
- Reactive moods wired to accept/error/LLM events.
- Config toggle to disable the companion.
- Runtime image tracing / Pillow as a runtime dependency.
- The mascot on any surface other than welcome screen + idle screensaver.
