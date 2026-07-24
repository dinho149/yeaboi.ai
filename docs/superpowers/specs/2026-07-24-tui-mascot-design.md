# TUI Mascot — Design Spec

**Date:** 2026-07-24
**Branch:** `feature/tui-mascot`
**Status:** Approved scope, spec under review

## Goal

Bring the website's pixel-duck mascot (green mallard in sunglasses) into the
terminal app as a persistent, characterful presence — not just a screensaver
Easter egg. Two concrete deliverables:

1. **A small companion sprite** that lives on the welcome (mode-select) screen —
   "as small as possible while still maintaining its proper image", multi-row
   (explicitly NOT a one-line ASCII face), with its signature sunglasses.
2. **An enriched idle screensaver** — the full-size duck already renders with
   sunglasses, a glint sweep, a foot shuffle and a bob; add more life (wing
   flap, idle behaviours, caption variety) so a long idle feels animated rather
   than looping four frames.

Both must share one sprite source so the duck can never drift into two different
ducks.

## Current State (what already exists)

`src/yeaboi/ui/shared/_screensaver.py` already contains:

- `_PALETTE` — the mallard palette (green body, teal shadow, orange bill/feet,
  black outline, dark sunglasses lenses `S`, glint white `W`).
- `_high_resolution_duck(frame)` — draws the full duck on a 42×30 pixel canvas
  via `_fill_ellipse`/`_fill_polygon`, then packs to ~15 text rows with
  `_half_block_rows` (▀/▄ compress two pixel rows per text row). **Already has
  two sunglass lenses, bridge, temple arm, glint sweep, foot shuffle, bob.**
- `_FULL_DUCK` / `_COMPACT_DUCK` — string-art fallbacks rendered two columns per
  source char via `_pixel_line`/`_duck_art`; also already carry sunglasses.
- `build_screensaver(*, width, height, elapsed)` — size-aware frame picker:
  high-res if width≥46 & height≥19, compact string-art if width≥22 & height≥13,
  else a tiny `<(o )___ YEABOI` label. `frame = int(elapsed*8) % 8`.
- `IdleController` — thread-safe idle state (5-min timeout) driving the
  full-screen idle takeover.

`MusicLive.get_renderable()` (`_music_bar.py`) is the app-wide chokepoint that
swaps in `build_screensaver()` when `idle_controller.should_show()`.

`_build_mode_screen()` (`ui/mode_select/screens/_screens.py`) centres the mode
rows, reserving empty `mid_top` whitespace above them — the companion's home.

## Architecture

### New module: `ui/shared/_mascot.py`

Factor the reusable sprite core out of `_screensaver.py` into `_mascot.py`:

- `MASCOT_PALETTE` — moved from `_screensaver._PALETTE` (single source of truth).
- Pixel-canvas primitives (`_fill_ellipse`, `_fill_polygon`, `_inside_polygon`,
  `_half_block_rows`) — moved here; both the screensaver and the companion draw
  on canvases and pack with the same half-block compressor.
- `render_duck(size, frame) -> Group` — the one duck renderer, parameterised by
  size. `size="full"` reproduces today's 42×30 high-res duck; `size="mini"`
  draws the small companion on a compact canvas (target ~10–12 pixel rows → **5–6
  text rows** after half-block packing, ~18–22 columns wide) keeping the round
  head, sunglasses (two lenses + bridge), bill and body legible. One row is
  explicitly rejected per the requirement.
- `mini_duck(frame) -> Group` and `full_duck(frame) -> Group` thin wrappers.

`_screensaver.py` keeps `IdleController`, the idle plumbing, `build_screensaver`,
captions/hints, and the size-tier decision — it imports the sprite from
`_mascot.py` instead of owning the pixel art. `_music_bar.py` is untouched.

This keeps one clear boundary: `_mascot.py` = "how a duck is drawn at a given
size/frame"; `_screensaver.py` = "when/where the idle duck takes over the
screen"; the mode screen = "a small resting duck in the header whitespace".

### Companion on the welcome screen

`_build_mode_screen()` gains a small mascot block rendered from
`mini_duck(frame)` placed in the top whitespace (`mid_top` region), horizontally
centred, above the mode rows. It animates off the existing `shimmer_tick` render
clock (no new timer): `frame = int(shimmer_tick * N) % FRAMES`. It occupies a
fixed small row budget (≤6 rows) that is subtracted from `mid_top` so the mode
rows never shift or get pushed off-screen. When the terminal is too short to
spare the rows (small `inner_h`), the companion is omitted — mode rows always
win the space budget.

### Enriched screensaver behaviours

Extend `render_duck(size="full", …)` frame set (and the compact fallback) with:

- **Wing flap** — the pale wing already exists as a polygon; add a raised-wing
  variant on one or two frames so the duck visibly flaps rather than only
  bobbing.
- **Idle behaviour cycle** — over a longer period (driven by
  `animation_elapsed`, not just the 8-frame loop) cycle through a few resting
  behaviours: the existing glint sweep, an occasional wing flap, and an
  occasional "look" (head/eye shift). Kept subtle — this is a calm idle duck.
- **Caption variety** — rotate the idle caption between a small set (e.g.
  "YEABOI · chilling", "YEABOI · zzz", "YEABOI · vibing") on a slow timer so a
  multi-minute idle isn't a single frozen string. Hint stays "press any key".

No change to the idle-takeover mechanism, timeout, or `MusicLive` chokepoint.

## Data Flow

```
shimmer_tick (mode screen render loop) ─┐
                                        ├─> frame index ─> render_duck("mini") ─> welcome header
animation_elapsed (IdleController) ─────┘                └> render_duck("full") ─> build_screensaver ─> MusicLive.get_renderable (idle)
```

`_mascot.py` is pure/stateless: given `(size, frame)` it returns a Rich `Group`.
All animation timing stays in the callers' existing clocks.

## Error / Edge Handling

- **Small terminals:** companion omitted when row budget can't be spared;
  screensaver keeps its existing size tiers (high-res → compact → tiny label).
- **No new state, no persistence, no config toggle** in v1.
- **Music bar / subtitle:** unaffected — the companion is inside the Panel body,
  not the border; the screensaver path already replaces the whole renderable.

## Testing (per the three-pillars rule)

- `tests/unit/ui/shared/test_mascot.py` (new):
  - `render_duck("mini", frame)` returns a `Group` of the expected small row
    count (>1 and ≤6) for every frame; columns within budget.
  - `render_duck("full", frame)` unchanged row count vs. today (regression
    guard on the packed height).
  - Palette keys referenced by the art all exist in `MASCOT_PALETTE`.
  - Frame index is stable/deterministic for a given input (pure function).
- `test_screensaver.py` (existing): keep passing after the extraction; add a
  wing-flap/behaviour-cycle assertion (a flap frame differs from a rest frame)
  and caption-rotation coverage.
- Welcome screen render test: `_build_mode_screen(...)` still returns a Panel of
  exactly `height` rows with the companion present at a normal size and absent at
  a short height (no layout shift of the mode rows).

## Logging / Observability

Pure rendering functions on the hot render path → **no per-frame logging** (the
never-log-per-frame rule). No log-dir or path changes. No LLM/API calls.

## Surface Parity

This is a **TUI-only visual flourish** (a mascot rendered in the terminal). It is
not a headless capability — there is nothing to expose via an engine, MCP tool,
CLI flag, or plugin skill. It therefore does **not** enter the `CAPABILITIES`
registry; no `FeatureTip` is required. This is a deliberate, reasoned absence
consistent with e.g. the retro live board being TUI-only. No surface-parity test
changes expected; if `test_surface_parity.py` proves otherwise, record an
`Exempt("TUI-only mascot flourish")` rather than inventing surfaces.

## Out of Scope (v1)

- App-wide corner overlay following you across every screen (Rich can't free-
  overlay a floating sprite cleanly without touching ~30 builders).
- Reactive moods wired to accept/error/LLM events (happy/thinking/oops faces).
- Config toggle to disable the companion.
- Mascot on any surface other than welcome screen + idle screensaver.

These are candidates for a v2 once the shared sprite system exists.
