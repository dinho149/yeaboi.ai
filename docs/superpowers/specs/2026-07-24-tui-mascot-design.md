# TUI Mascot — Design Spec

**Date:** 2026-07-24
**Branch:** `feature/tui-mascot`
**Status:** Design proven via visual prototyping; ready for implementation plan

## Goal

Bring the website's pixel-duck mascot (green mallard in wayfarer sunglasses) into
the terminal app as a crisp, on-brand, animated presence. Two deliverables, one
shared sprite source:

1. **Small head companion** on the welcome (mode-select) screen — perched in the
   right-hand whitespace, ~16px wide, with a subtle idle animation.
2. **Full-body idle screensaver** — a crisp full mallard that replaces today's
   noisy procedural duck, with subtle idle animation.

## Why this approach (settled during prototyping)

Prototyping (render real output → SVG → PNG via `qlmanage` → inspect) established:

- The current procedural duck (`_high_resolution_duck`, ellipse/polygon fills →
  half-block pack) is intrinsically **noisy** and cannot be cleaned by tweaking
  coordinates. **Deleted.**
- The clean path is **tracing the website's own sprite art**, which already
  exists as **three separate layers** on a shared 480×509 canvas:
  `docs/assets/duck-base.png`, `duck-wing.png`, `duck-glasses.png`.
- Because wing and glasses are **separate layers**, animation is just
  compositing each layer at a per-frame offset — matching the site's own
  `wing-flap` / `glasses-bob` CSS rig. No fragile pixel-region detection.

## Visual style (locked)

- **Flat cartoon:** every traced pixel snapped to a curated ~10-colour brand
  palette (outline, two greens, lens, belly/glint white, three body blue-greys,
  two oranges) so each region is one flat colour.
- **Facing left**, matching existing orientation.
- **Sizes:** full body **w≈34** (~17 text rows) for the screensaver; head-only
  crop **w≈16** (~7 text rows) for the companion.
- **Constant lens glints** (one diagonal white mark per lens) on both, like the
  site sprite — static, not twinkling.

## Sprite production (design-time; output is committed data)

Runtime ships **no Pillow and no image IO**. Production is offline; the frozen
grids are committed. Prototype scripts live in `scratchpad/tui/` (`layers.py`,
`final.py`) and are the documented regeneration path.

Per layer / crop:
1. **Trace:** load PNG → flip left → snap each pixel to the palette → NEAREST
   downscale to target width (flatten-before-resize keeps blocks solid).
2. **Clean:** connected-component despeckle (drop islands < 3px), dissolve lone
   off-colour pixels, then **hand-verify** the grid via the render loop (the head
   grid was hand-corrected: removed a stray green in the shades band, a rogue
   orange under the bill, and two floating blue-grey pixels).
3. **Freeze:** store each cleaned grid as a module constant (rows of palette
   letters, `.` = transparent), exactly how `_FULL_DUCK`/`_COMPACT_DUCK` are
   frozen today.

## Architecture

### New module `ui/shared/_mascot.py`

Single source of truth for the sprite.

- `MASCOT_PALETTE` — curated flat palette (letter → rgb).
- Frozen grids: `DUCK_BASE`, `DUCK_WING`, `DUCK_GLASSES` (full-body layers,
  aligned on a shared grid) and `DUCK_HEAD` (clean head, glints baked in).
- `_half_block_rows(grid)` — ▀/▄ packer → Rich `Text` rows (moved from
  `_screensaver.py`).
- `_compose(*grids)` / `_shift(grid, dy)` — overlay layers; offset a layer.
- `render_full(frame) -> Group` — `compose(base, shift(wing, WING_OFF[frame]),
  shift(glasses, GLASS_OFF[frame]))` packed to rows. Wing-flap + glasses-bob.
- `render_head(frame) -> Group` — `DUCK_HEAD` with a gentle bob (blank rows on
  some frames). Glints are baked in (static).
- 8-frame cycles; offset tables are small module constants.

Pure/stateless: `(frame) -> Group`. No timing, IO, or state inside.

### Deletions in `_screensaver.py`

Remove `_high_resolution_duck`, `_fill_ellipse`, `_fill_polygon`,
`_inside_polygon`, `_FULL_DUCK`, `_COMPACT_DUCK`, `_duck_art`, `_pixel_line`, and
the local `_PALETTE`/`_half_block_rows` (now in `_mascot.py`). Keep
`IdleController`, idle plumbing, `build_screensaver`, captions/hints, size tiers.

### Surface 1 — screensaver (full duck)

`MusicLive.get_renderable()` (`_music_bar.py`) already swaps the whole screen for
`build_screensaver()` when `idle_controller.should_show()` (5-min idle, any
screen). **Unchanged.** `build_screensaver()` calls `_mascot.render_full(frame)`
where `frame = int(idle_controller.animation_elapsed()*8) % 8`, driven by
MusicLive's existing refresh thread. Size tiers: full at large sizes, head at the
compact tier, existing tiny `YEABOI` text label at the smallest.

### Surface 2 — welcome companion (head duck)

`_build_mode_screen()` (`ui/mode_select/screens/_screens.py`) renders
`_mascot.render_head(frame)` into the **right-hand whitespace** (modes are
left-aligned; the right half is empty). `frame = int(shimmer_tick * N) % 8`,
using the `shimmer_tick` the screen already receives and re-renders on — no new
timer. Placed via a fixed-width right region (e.g. a `Table.grid`/right-aligned
block) so the mode rows keep their width; the companion is **omitted below a
width/height threshold** so the menu is never squeezed.

## Data Flow

```
shimmer_tick (mode screen) ─────────────> frame ─> render_head ─> welcome right-side
animation_elapsed (IdleController) ─────> frame ─> render_full ─> build_screensaver ─> MusicLive.get_renderable (idle takeover)
```

No new threads/timers; both ride existing render loops.

## Error / Edge Handling

- **Narrow/short terminals:** companion omitted (menu keeps full width);
  screensaver falls back full → head → tiny label.
- **Music subtitle:** unaffected — companion sits in the Panel body; the
  screensaver path already replaces the whole renderable.
- No new state, persistence, or config toggle in v1.

## Testing (three-pillars)

- `tests/unit/ui/shared/test_mascot.py` (new):
  - `render_full(frame)` / `render_head(frame)` return `Group`s for all 8 frames;
    head is `>1` and `≤7` text rows; full matches its expected packed height.
  - Every palette letter used in each grid exists in `MASCOT_PALETTE`; all grid
    rows equal length.
  - Determinism: same frame → identical output.
  - `_compose`/`_shift` behaviour (overlay precedence; offset clamps in range).
- `test_screensaver.py`: passes after the extraction/deletion; assert a wing-lift
  frame differs from a rest frame.
- `ui/mode_select` render test: `_build_mode_screen(...)` returns a Panel of
  exactly `height` rows with the companion present at normal size and absent at a
  short/narrow size, with mode rows unshifted either way.

## Logging / Observability

Pure render-path functions → **no per-frame logging** (never-log-per-frame rule).
No log-dir/path changes, no LLM/API calls.

## Surface Parity

TUI-only visual flourish; nothing headless to expose. Not in the `CAPABILITIES`
registry, no `FeatureTip` — a deliberate, reasoned absence (cf. retro live board
TUI-only). If `test_surface_parity.py` disagrees, record
`Exempt("TUI-only mascot flourish")`.

## Out of Scope (v1)

- App-wide corner overlay across every screen.
- Reactive moods wired to accept/error/LLM events.
- Config toggle to disable the companion.
- Runtime image tracing / Pillow as a runtime dependency.
- Any surface other than welcome screen + idle screensaver.
