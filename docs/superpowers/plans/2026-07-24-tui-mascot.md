# TUI Mascot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the website's sunglasses-mallard mascot into the TUI as a crisp, animated full-body idle screensaver and a small head companion on the welcome screen.

**Architecture:** A new `ui/shared/_mascot.py` is the single sprite source. Three flat-colour pixel-grid layers (`base`/`wing`/`glasses`, traced offline from the 480px website art and committed as `_mascot_sprites.py`) are composited at per-frame offsets to animate wing-flap + glasses-bob; a hand-authored `DUCK_HEAD` grid gives the companion. The existing idle-takeover chokepoint (`MusicLive.get_renderable` → `build_screensaver`) and the welcome screen (`_build_mode_screen`) become thin adapters that call the mascot renderers. No runtime image IO.

**Tech Stack:** Python 3.11+, Rich (`Text`/`Group`/`Panel`/`Align`/`Table.grid`), half-block (▀/▄) rendering. Pillow is used **only** by the offline generator script (`scripts/`), never imported at runtime.

## Global Constraints

- Python 3.11+, ruff line-length 120; imports isort-ordered (stdlib, third-party, local).
- **No runtime Pillow / no image IO.** Pillow appears only in `scripts/gen_mascot_sprites.py`.
- **Never log per frame** (render-path functions emit no logging).
- Verification gate before any commit that touches `.py`: `make test` and `make lint` both clean. Fast inner loop: `make test-fast`.
- Palette is fixed and shared: the letter→rgb map in `scripts/gen_mascot_sprites.py` MUST equal `MASCOT_PALETTE` in `_mascot.py` exactly.
- Sprite orientation: **facing left**. Animation cycle length: **8 frames**.
- TUI-only flourish: no engine/MCP/CLI/plugin surface; if `tests/unit/test_surface_parity.py` fails, add `Exempt("TUI-only mascot flourish")`, do not invent surfaces.

---

## File Structure

- `scripts/gen_mascot_sprites.py` — **create.** Offline dev tool (Pillow). Reads `docs/assets/duck-{base,wing,glasses}.png`, traces → flat palette → NEAREST downscale → despeckle, writes `_mascot_sprites.py`. Not imported at runtime.
- `src/yeaboi/ui/shared/_mascot_sprites.py` — **create (generated).** Frozen `DUCK_BASE`, `DUCK_WING`, `DUCK_GLASSES` letter-grids (tuples of equal-length strings).
- `src/yeaboi/ui/shared/_mascot.py` — **create.** `MASCOT_PALETTE`, `DUCK_HEAD` literal, offset tables, `_shift`/`_compose`/`_bob`/`_pack`, `render_full(frame)`, `render_head(frame)`.
- `tests/unit/test_mascot.py` — **create.** Unit tests for the module + a data-integrity test over the generated sprites.
- `src/yeaboi/ui/shared/_screensaver.py` — **modify.** Delete the procedural duck + moved helpers; `build_screensaver` calls the mascot renderers.
- `tests/unit/test_screensaver.py` — **modify.** Keep passing; add size-tier + animation assertions.
- `src/yeaboi/ui/mode_select/screens/_screens.py` — **modify.** `_build_mode_screen` renders the head companion in the right-side whitespace when wide enough.
- `tests/test_mode_select.py` — **modify.** Companion present at wide size, absent at default/narrow, mode rows unshifted.

---

## Task 1: Sprite generator + frozen layer data

**Files:**
- Create: `scripts/gen_mascot_sprites.py`
- Create (by running it): `src/yeaboi/ui/shared/_mascot_sprites.py`
- Test: `tests/unit/test_mascot.py` (data-integrity portion)

**Interfaces:**
- Produces: module `yeaboi.ui.shared._mascot_sprites` exposing `DUCK_BASE`, `DUCK_WING`, `DUCK_GLASSES` — each a `tuple[str, ...]`, all three the same dimensions (same row count, same row length), every char in the 10 palette letters `koGgWLMSbr` or `.`.

- [ ] **Step 1: Write the generator script**

Create `scripts/gen_mascot_sprites.py`:

```python
"""Regenerate the frozen mascot sprite layers from the website art.

Dev tool — NOT imported at runtime (keeps Pillow out of the shipped app).
Run from the repo root:  uv run python scripts/gen_mascot_sprites.py
"""

from pathlib import Path

from PIL import Image

# Letter -> rgb. MUST stay identical to MASCOT_PALETTE in _mascot.py.
PALETTE = {
    "k": (9, 14, 18),      # outline
    "o": (26, 32, 40),     # sunglass lens
    "G": (34, 158, 122),   # head green
    "g": (22, 110, 92),    # head green shadow
    "W": (232, 240, 238),  # glint / belly white
    "L": (150, 190, 190),  # body light blue-grey
    "M": (96, 140, 144),   # body mid blue-grey
    "S": (60, 100, 108),   # body shadow
    "b": (250, 176, 44),   # bill / feet
    "r": (228, 104, 22),   # orange shadow
}
LETTERS = list(PALETTE)
RGBS = [PALETTE[c] for c in LETTERS]
WIDTH = 34
ASSETS = Path("docs/assets")
OUT = Path("src/yeaboi/ui/shared/_mascot_sprites.py")


def nearest(r, g, b):
    return LETTERS[min(range(len(RGBS)), key=lambda i: (RGBS[i][0] - r) ** 2 + (RGBS[i][1] - g) ** 2 + (RGBS[i][2] - b) ** 2)]


def despeckle(grid, min_size=3):
    """Drop connected islands smaller than min_size (8-connectivity)."""
    h = len(grid)
    w = len(grid[0])
    seen = [[False] * w for _ in range(h)]
    keep = [[False] * w for _ in range(h)]
    for sy in range(h):
        for sx in range(w):
            if grid[sy][sx] == "." or seen[sy][sx]:
                continue
            stack = [(sx, sy)]
            comp = []
            seen[sy][sx] = True
            while stack:
                x, y = stack.pop()
                comp.append((x, y))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and grid[ny][nx] != ".":
                            seen[ny][nx] = True
                            stack.append((nx, ny))
            if len(comp) >= min_size:
                for x, y in comp:
                    keep[y][x] = True
    return ["".join(grid[y][x] if keep[y][x] else "." for x in range(w)) for y in range(h)]


def trace(name):
    im = Image.open(ASSETS / f"{name}.png").convert("RGBA").transpose(Image.FLIP_LEFT_RIGHT)
    w, h = im.size
    height = round(WIDTH * h / w)
    if height % 2:
        height += 1
    # Flatten (snap to palette) FIRST, then NEAREST downscale so blocks stay solid.
    src = im.load()
    flat = Image.new("RGBA", (w, h))
    fp = flat.load()
    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            fp[x, y] = (*PALETTE[nearest(r, g, b)], 255) if a >= 128 else (0, 0, 0, 0)
    small = flat.resize((WIDTH, height), Image.NEAREST).load()
    grid = []
    for y in range(height):
        grid.append("".join(nearest(*small[x, y][:3]) if small[x, y][3] >= 128 else "." for x in range(WIDTH)))
    return despeckle(grid)


def emit(name, grid):
    body = ",\n    ".join(f'"{row}"' for row in grid)
    return f"{name} = (\n    {body},\n)\n"


def main():
    parts = ["# AUTO-GENERATED by scripts/gen_mascot_sprites.py — do not edit by hand.\n\n"]
    for const, asset in (("DUCK_BASE", "duck-base"), ("DUCK_WING", "duck-wing"), ("DUCK_GLASSES", "duck-glasses")):
        parts.append(emit(const, trace(asset)))
        parts.append("\n")
    OUT.write_text("".join(parts))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the generator**

Run: `uv run python scripts/gen_mascot_sprites.py`
Expected: prints `wrote src/yeaboi/ui/shared/_mascot_sprites.py`; the file now defines `DUCK_BASE`, `DUCK_WING`, `DUCK_GLASSES`.

- [ ] **Step 3: Write the data-integrity test**

Create `tests/unit/test_mascot.py` with:

```python
from yeaboi.ui.shared import _mascot_sprites as sprites

_LAYERS = ("DUCK_BASE", "DUCK_WING", "DUCK_GLASSES")
_VALID = set("koGgWLMSbr.")


def test_layers_exist_and_are_string_tuples():
    for name in _LAYERS:
        grid = getattr(sprites, name)
        assert isinstance(grid, tuple) and grid, f"{name} empty"
        assert all(isinstance(row, str) for row in grid)


def test_layers_share_dimensions():
    grids = [getattr(sprites, n) for n in _LAYERS]
    heights = {len(g) for g in grids}
    assert len(heights) == 1, f"layer heights differ: {heights}"
    widths = {len(row) for g in grids for row in g}
    assert len(widths) == 1, f"row widths differ: {widths}"


def test_layers_use_only_palette_letters():
    for name in _LAYERS:
        for row in getattr(sprites, name):
            assert set(row) <= _VALID, f"{name} has invalid chars: {set(row) - _VALID}"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/unit/test_mascot.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/gen_mascot_sprites.py src/yeaboi/ui/shared/_mascot_sprites.py tests/unit/test_mascot.py
git commit -m "feat(mascot): generate frozen sprite layers from website art"
```

---

## Task 2: `_mascot.py` — palette, head grid, renderers

**Files:**
- Create: `src/yeaboi/ui/shared/_mascot.py`
- Test: `tests/unit/test_mascot.py` (append)

**Interfaces:**
- Consumes: `DUCK_BASE`, `DUCK_WING`, `DUCK_GLASSES` from `_mascot_sprites`.
- Produces:
  - `MASCOT_PALETTE: dict[str, tuple[int, int, int]]` (letters `koGgWLMSbr`).
  - `render_full(frame: int) -> rich.console.Group` — composited animated full duck.
  - `render_head(frame: int) -> rich.console.Group` — animated head companion.
  - `FRAMES: int = 8`.

- [ ] **Step 1: Write the module**

Create `src/yeaboi/ui/shared/_mascot.py`:

```python
"""Single source of truth for the Yeaboi duck mascot sprite.

# See docs: "TUI system" — the mascot renders as chunky half-block (▀/▄) pixel
# art. Full-body layers (base/wing/glasses) are traced offline and frozen in
# _mascot_sprites.py; the small head companion is hand-authored below. Animation
# is pure: compose the layers at a per-frame offset. No timing or IO lives here.
"""

from __future__ import annotations

from rich.console import Group
from rich.text import Text

from yeaboi.ui.shared._mascot_sprites import DUCK_BASE, DUCK_GLASSES, DUCK_WING

FRAMES = 8

# Letter -> rgb. MUST equal PALETTE in scripts/gen_mascot_sprites.py.
MASCOT_PALETTE: dict[str, tuple[int, int, int]] = {
    "k": (9, 14, 18),
    "o": (26, 32, 40),
    "G": (34, 158, 122),
    "g": (22, 110, 92),
    "W": (232, 240, 238),
    "L": (150, 190, 190),
    "M": (96, 140, 144),
    "S": (60, 100, 108),
    "b": (250, 176, 44),
    "r": (228, 104, 22),
}

# Hand-authored, hand-cleaned head companion (glints baked in as constant "W").
DUCK_HEAD: tuple[str, ...] = (
    "......kkkk......",
    ".....GGGGGG.....",
    "....oGGGGGGG....",
    "...oGGGGGGGGo...",
    "...kkkkkWkkkWkk.",
    "...gggkWkkkWkkk.",
    "...gGGkkkkkkkkk.",
    "...gGGGkkkbbkk..",
    "...ggGGGGbbbbbkk",
    "...kggGGrrrbbbbk",
    "....kggggkkkkk..",
    ".....ggggg......",
    ".....GGGGGG.....",
    "....ggGGGGG.....",
)

# Per-frame vertical offsets (pixels). Positive = lift the layer up.
WING_OFF = (0, 1, 2, 2, 1, 0, 0, 0)   # gentle wing flap
GLASS_OFF = (0, 0, 0, 1, 1, 1, 0, 0)  # slow glasses bob
HEAD_BOB = (0, 0, 1, 1, 1, 0, 0, 0)   # head breathing bob (shift down)


def _style(letter: str) -> str | None:
    rgb = MASCOT_PALETTE.get(letter)
    return None if rgb is None else f"rgb({rgb[0]},{rgb[1]},{rgb[2]})"


def _shift(grid: tuple[str, ...], dy: int) -> tuple[str, ...]:
    """Lift a layer up by dy pixels (content from below), transparent fill."""
    if dy <= 0:
        return grid
    blank = "." * len(grid[0])
    return tuple(grid[y + dy] if y + dy < len(grid) else blank for y in range(len(grid)))


def _bob(grid: tuple[str, ...], up: int) -> tuple[str, ...]:
    """Shift a whole sprite down by prepending `up` transparent rows."""
    if up <= 0:
        return grid
    return ("." * len(grid[0]),) * up + tuple(grid)


def _compose(*grids: tuple[str, ...]) -> tuple[str, ...]:
    """Overlay grids in order; later grids paint over earlier ones."""
    h = max(len(g) for g in grids)
    w = max(len(g[0]) for g in grids)
    out = []
    for y in range(h):
        row = ["."] * w
        for g in grids:
            if y >= len(g):
                continue
            line = g[y]
            for x in range(min(w, len(line))):
                if line[x] != ".":
                    row[x] = line[x]
        out.append("".join(row))
    return tuple(out)


def _pack(rows: tuple[str, ...]) -> list[Text]:
    """Compress two pixel rows into each terminal row using ▀/▄ half-blocks."""
    out: list[Text] = []
    width = len(rows[0]) if rows else 0
    for y in range(0, len(rows), 2):
        top = rows[y]
        bot = rows[y + 1] if y + 1 < len(rows) else "." * width
        line = Text()
        for x in range(width):
            t = _style(top[x])
            b = _style(bot[x]) if x < len(bot) else None
            if t is None and b is None:
                line.append(" ")
            elif t == b:
                line.append("█", style=t)
            elif t and b:
                line.append("▀", style=f"{t} on {b}")
            elif t:
                line.append("▀", style=t)
            else:
                line.append("▄", style=b)
        out.append(line)
    return out


def render_full(frame: int) -> Group:
    """Full-body idle duck: wing-flap + glasses-bob for the given frame."""
    f = frame % FRAMES
    grid = _compose(DUCK_BASE, _shift(DUCK_WING, WING_OFF[f]), _shift(DUCK_GLASSES, GLASS_OFF[f]))
    return Group(*_pack(grid))


def render_head(frame: int) -> Group:
    """Small head companion: a gentle breathing bob (glints are baked in)."""
    f = frame % FRAMES
    return Group(*_pack(_bob(DUCK_HEAD, HEAD_BOB[f])))
```

- [ ] **Step 2: Append unit tests**

Add to `tests/unit/test_mascot.py`:

```python
from rich.console import Group

from yeaboi.ui.shared import _mascot
from yeaboi.ui.shared._mascot import FRAMES, MASCOT_PALETTE, render_full, render_head


def test_head_grid_rows_equal_length_and_valid_letters():
    valid = set(MASCOT_PALETTE) | {"."}
    widths = {len(r) for r in _mascot.DUCK_HEAD}
    assert widths == {16}
    for row in _mascot.DUCK_HEAD:
        assert set(row) <= valid


def test_render_full_returns_group_for_all_frames():
    for f in range(FRAMES):
        g = render_full(f)
        assert isinstance(g, Group)
        assert len(g.renderables) == 18  # 36 pixel rows -> 18 half-block rows


def test_render_head_row_count_in_range():
    for f in range(FRAMES):
        g = render_head(f)
        assert isinstance(g, Group)
        assert 6 <= len(g.renderables) <= 8  # 14 px (+bob) -> 7..8 text rows


def test_frame_index_is_deterministic():
    a = render_full(2).renderables
    b = render_full(2).renderables
    assert [t.plain for t in a] == [t.plain for t in b]


def test_wing_flap_changes_a_frame():
    rest = [t.plain for t in render_full(0).renderables]
    lifted = [t.plain for t in render_full(3).renderables]  # WING_OFF[3]=2
    assert rest != lifted


def test_palette_matches_generator():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("_gen", Path("scripts/gen_mascot_sprites.py"))
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    assert gen.PALETTE == MASCOT_PALETTE
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/unit/test_mascot.py -v`
Expected: all pass (9 tests total). If `test_render_full_returns_group_for_all_frames` reports a row count other than 18, update the literal `18` to the actual `len(render_full(0).renderables)` (it is `ceil(height/2)` where `height` is the generated layer height) — do NOT change the sprite.

- [ ] **Step 4: Lint**

Run: `make lint`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/yeaboi/ui/shared/_mascot.py tests/unit/test_mascot.py
git commit -m "feat(mascot): add sprite renderers (full duck + head companion)"
```

---

## Task 3: Wire the screensaver to the mascot

**Files:**
- Modify: `src/yeaboi/ui/shared/_screensaver.py`
- Test: `tests/unit/test_screensaver.py`

**Interfaces:**
- Consumes: `render_full`, `render_head` from `_mascot`.
- Produces: unchanged public `build_screensaver(*, width, height, elapsed=None) -> RenderableType`.

- [ ] **Step 1: Read the current file**

Read `src/yeaboi/ui/shared/_screensaver.py`. Identify the block from `_PALETTE = {` (~line 152) through the end of `_duck_art` (~line 352), and `build_screensaver` (~line 355+). `IdleController` and the idle helpers above `_PALETTE` stay.

- [ ] **Step 2: Delete the procedural sprite code**

Delete these definitions entirely: `_PALETTE`, `_FULL_DUCK`, `_COMPACT_DUCK`, `_fill_ellipse`, `_inside_polygon`, `_fill_polygon`, `_half_block_rows`, `_high_resolution_duck`, `_pixel_line`, `_duck_art`. Remove now-unused imports (`Align` stays; `Group`/`Text` still used by `build_screensaver`).

- [ ] **Step 3: Add the mascot import**

Near the top imports add:

```python
from yeaboi.ui.shared._mascot import render_full, render_head
```

- [ ] **Step 4: Rewrite `build_screensaver`**

Replace the body's art selection so it uses the mascot renderers (keep the caption/hint/centering and the tiny-label fallback):

```python
def build_screensaver(*, width: int, height: int, elapsed: float | None = None) -> RenderableType:
    """Build a size-aware animated saver frame without mutating app content."""
    elapsed = idle_controller.animation_elapsed() if elapsed is None else elapsed
    frame = int(elapsed * 8) % 8

    if width >= 46 and height >= 19:
        art = render_full(frame)
    elif width >= 22 and height >= 13:
        art = render_head(frame)
    else:
        if width >= 20:
            label = "<(o )___ YEABOI"
        elif width >= 12:
            label = "<(o )_ YEABOI"
        else:
            label = "YEABOI"[:width]
        line = Text(label, style="bold rgb(42,170,105)")
        return Align.center(line, vertical="middle", height=max(1, height))

    caption = Text("YEABOI · chilling", style="bold rgb(105,220,235)", justify="center")
    hint = Text("press any key", style="rgb(95,105,115)", justify="center")
    content = Group(art, caption, hint)
    return Align.center(content, vertical="middle", height=max(1, height))
```

- [ ] **Step 5: Update the screensaver tests**

In `tests/unit/test_screensaver.py`, remove any test that references deleted internals (`_high_resolution_duck`, `_duck_art`, `_FULL_DUCK`, `_COMPACT_DUCK`, `_PALETTE`, `_half_block_rows`). Add:

```python
from rich.align import Align

from yeaboi.ui.shared._screensaver import build_screensaver


def test_screensaver_large_uses_full_duck():
    saver = build_screensaver(width=60, height=22, elapsed=0.0)
    assert isinstance(saver, Align)  # renders without error at the large tier


def test_screensaver_compact_tier_renders():
    saver = build_screensaver(width=30, height=15, elapsed=0.0)
    assert isinstance(saver, Align)


def test_screensaver_tiny_tier_renders():
    saver = build_screensaver(width=10, height=4, elapsed=0.0)
    assert isinstance(saver, Align)


def test_screensaver_animates_between_frames():
    from rich.console import Console

    def rendered(elapsed):
        con = Console(width=60, height=22, record=True, file=open("/dev/null", "w"))
        con.print(build_screensaver(width=60, height=22, elapsed=elapsed))
        return con.export_text()

    assert rendered(0.0) != rendered(0.375)  # frame 0 vs frame 3 (wing lifted)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/unit/test_screensaver.py tests/unit/test_mascot.py -v`
Expected: all pass. Fix any lingering references to deleted names.

- [ ] **Step 7: Full gate**

Run: `make test && make lint`
Expected: both clean. (`_music_bar.py` is untouched; its idle path now renders the new duck.)

- [ ] **Step 8: Commit**

```bash
git add src/yeaboi/ui/shared/_screensaver.py tests/unit/test_screensaver.py
git commit -m "refactor(mascot): screensaver renders the traced duck, drop procedural art"
```

---

## Task 4: Welcome-screen head companion

**Files:**
- Modify: `src/yeaboi/ui/mode_select/screens/_screens.py`
- Test: `tests/test_mode_select.py`

**Interfaces:**
- Consumes: `render_head` from `_mascot`.
- Produces: `_build_mode_screen(...)` unchanged signature; adds a right-column companion when `width >= 92 and height >= 18`.

- [ ] **Step 1: Add imports and constants**

At the top of `_screens.py` add:

```python
from rich.table import Table

from yeaboi.ui.shared._mascot import render_head
```

Near the other module constants add:

```python
# The companion perches in the right-hand whitespace. Only shown when the panel
# is wide enough that the longest mode title (block-font) still fits on the left,
# and tall enough to seat the ~7-row duck; otherwise the menu keeps full width.
_COMPANION_MIN_WIDTH = 92
_COMPANION_MIN_HEIGHT = 18
_COMPANION_COLS = 18
```

- [ ] **Step 2: Write the failing test**

In `tests/test_mode_select.py` add:

```python
from rich.console import Console

from yeaboi.ui.mode_select.screens._screens import _build_mode_screen


def _text_of(panel, width, height):
    con = Console(width=width, height=height, record=True, file=open("/dev/null", "w"))
    con.print(panel)
    return con.export_text()


def test_companion_present_when_wide():
    panel = _build_mode_screen(0, width=110, height=30, shimmer_tick=0.0)
    # the duck uses half-block glyphs not present in the block-font menu
    assert "▀" in _text_of(panel, 110, 30) or "▄" in _text_of(panel, 110, 30)


def test_companion_absent_when_narrow():
    panel = _build_mode_screen(0, width=80, height=26, shimmer_tick=0.0)
    text = _text_of(panel, 80, 26)
    assert "▀" not in text and "▄" not in text


def test_mode_screen_exact_height_with_companion():
    panel = _build_mode_screen(0, width=110, height=30, shimmer_tick=0.0)
    text = _text_of(panel, 110, 30)
    assert len(text.splitlines()) == 30
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_mode_select.py::test_companion_present_when_wide -v`
Expected: FAIL (no half-block glyph yet).

- [ ] **Step 4: Add the companion in `_build_mode_screen`**

In `_build_mode_screen`, locate where `content` (the `Group(...)`) is built and the final `return Panel(content, ...)`. Replace the return with:

```python
    frame = int(shimmer_tick * 6) % 8
    if width >= _COMPANION_MIN_WIDTH and height >= _COMPANION_MIN_HEIGHT:
        companion = Align.center(
            Group(render_head(frame), Text(""), Text("chilling", style="rgb(120,130,140)", justify="center")),
            vertical="middle",
        )
        layout = Table.grid(expand=True)
        layout.add_column(ratio=1)
        layout.add_column(width=_COMPANION_COLS)
        layout.add_row(content, companion)
        body: RenderableType = layout
    else:
        body = content

    return Panel(
        body,
        border_style="white",
        box=rich.box.ROUNDED,
        expand=True,
        height=height,
        padding=(1, 2),
    )
```

Ensure `Align`, `Group`, `Text`, and `RenderableType` are imported at the top of the file (add `from rich.console import Group, RenderableType` and `from rich.align import Align` / `from rich.text import Text` if not already present).

- [ ] **Step 5: Run the companion tests**

Run: `uv run pytest tests/test_mode_select.py -v`
Expected: the three new tests pass. If `test_mode_screen_exact_height_with_companion` fails on height, the `Table.grid` inside a fixed-height `Panel` is fine — the Panel enforces height; debug by confirming `height=height` is still passed to `Panel`.

- [ ] **Step 6: Full gate**

Run: `make test && make lint`
Expected: both clean. If a pre-existing `_build_mode_screen` test rendered at width ≥ 92 and now sees the duck, update that test's expected text or lower its render width to 80.

- [ ] **Step 7: Commit**

```bash
git add src/yeaboi/ui/mode_select/screens/_screens.py tests/test_mode_select.py
git commit -m "feat(mascot): perch the head companion on the welcome screen"
```

---

## Task 5: Surface-parity check + manual verification

**Files:**
- Modify (only if the check requires): `tests/unit/test_surface_parity.py`

**Interfaces:** none (verification task).

- [ ] **Step 1: Run the full suite**

Run: `make test`
Expected: green. If `tests/unit/test_surface_parity.py` fails demanding a capability/tip for the mascot, add an exemption rather than inventing surfaces — the mascot is a visual flourish, not a headless capability.

- [ ] **Step 2: Add a parity exemption if required**

Only if Step 1 flagged it, add to the appropriate exemption structure in `tests/unit/test_surface_parity.py`:

```python
Exempt("TUI-only mascot flourish")
```

Re-run `make test` to confirm green.

- [ ] **Step 3: Manual eyeball (optional but recommended)**

Regenerate the preview and view the two surfaces exactly as shipped:

```bash
uv run python -c "from rich.console import Console; from yeaboi.ui.shared._screensaver import build_screensaver; Console().print(build_screensaver(width=60, height=22, elapsed=0.0))"
```

Confirm the full duck renders crisply with sunglasses and glints. For the welcome companion, run the TUI (`make run-dry`) at a ≥110-col terminal and confirm the duck perches on the right without squeezing the menu.

- [ ] **Step 4: Final gate + commit any parity change**

Run: `make test && make lint`
Expected: both clean.

```bash
git add -A
git commit -m "test(mascot): surface-parity exemption for TUI-only mascot" || echo "nothing to commit"
```

---

## Self-Review Notes (author)

- **Spec coverage:** `_mascot.py` (Task 2) ← architecture/module; layered trace + freeze (Task 1) ← production pipeline; screensaver full duck (Task 3) ← surface 1; welcome companion (Task 4) ← surface 2; three-pillars tests distributed across Tasks 1–4; surface-parity exemption (Task 5). Logging: none by design (render path). No runtime Pillow: generator isolated in `scripts/`.
- **Placeholder scan:** all steps carry real code or exact commands; row-count literals (18; 6–8) have a documented adjust-if-different fallback keyed to generated height, not a TODO.
- **Type consistency:** `render_full`/`render_head` names, `frame:int`, `MASCOT_PALETTE`, `DUCK_BASE/WING/GLASSES`, offset tuples, and the `92/18/18` companion constants are used consistently across tasks and match the spec.
