#!/usr/bin/env python3
"""Downscale the duck mascot layers for inlining into the web bundles.

The source art (``docs/assets/duck-{base,wing,glasses}.png``) is 480x509 and
199 KB across the three layers — about 265 KB once base64'd. That is fine for
the docs site, which fetches it over HTTP and caches it, and completely wrong
for us: every bundle is self-contained, so the sprite is embedded as a ``data:``
URI in the board *and* in each of the ten exported report files.

This writes a 128px-wide rendition, which is 2x the 64px the duck is drawn at.
All three layers get identical dimensions and identical resampling, because they
are composited on top of each other — a half-pixel difference in scale puts the
sunglasses on the duck's forehead.

**Not a build step.** Pillow ships only in the ``charts`` extra, and both
``pip install yeaboi`` and ``make web`` have to work without it. The outputs are
committed; this is here so the next person can regenerate them, and so the
parameters are written down rather than remembered.

Usage::

    uv run --extra charts python scripts/gen_duck_sprites.py
    uv run --extra charts python scripts/gen_duck_sprites.py --check
"""

from __future__ import annotations

import argparse
import logging
import sys
from io import BytesIO
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "docs" / "assets"
OUTPUT_DIR = ROOT / "frontend" / "src" / "assets" / "duck"

# The rig draws the duck at 64px wide (docs/assets/landing.css `.duck-rig`), so
# 128 is a 2x asset — crisp on every display without paying for 4x.
TARGET_WIDTH = 128

# The source has ~25,700 distinct colours, almost all of them anti-aliasing
# between a much smaller set of flat fills. Quantising to 64 is visually
# indistinguishable at 64px and takes the base layer from 20.6 KB to 3.5 KB —
# the single biggest lever on what the exports have to carry.
PALETTE_COLOURS = 64

LAYERS = ("base", "wing", "glasses")


def _resize(source: Path, width: int) -> bytes:
    """Downscale one layer and return the encoded PNG bytes."""
    from PIL import Image  # imported lazily: only present with the charts extra

    with Image.open(source) as image:
        rgba = image.convert("RGBA")
        # Height is derived from the *base* layer's aspect ratio for all three,
        # not from each file's own — they are already the same size, and
        # rounding each independently could differ by a pixel and shear the
        # composite.
        height = round(rgba.height * width / rgba.width)
        # LANCZOS, not NEAREST: the source is shaded rather than a strict pixel
        # grid (there is no block size for which every block is one flat
        # colour), so nearest-neighbour drops detail and jags the outline.
        small = rgba.resize((width, height), Image.Resampling.LANCZOS)
        # FASTOCTREE rather than the default MEDIANCUT because it is the only
        # Pillow quantiser that keeps the alpha channel — these layers are
        # composited, so a hard-edged matte would box the wing in black.
        indexed = small.quantize(colors=PALETTE_COLOURS, method=Image.Quantize.FASTOCTREE)


    buffer = BytesIO()
    # optimize + max compression: this is written once and shipped forever.
    indexed.save(buffer, format="PNG", optimize=True, compress_level=9)
    return buffer.getvalue()


def build() -> dict[Path, bytes]:
    """Render every layer. Returns {output path: PNG bytes}."""
    out: dict[Path, bytes] = {}
    for layer in LAYERS:
        source = SOURCE_DIR / f"duck-{layer}.png"
        if not source.exists():
            raise FileNotFoundError(f"missing duck layer: {source}")
        out[OUTPUT_DIR / f"{layer}.png"] = _resize(source, TARGET_WIDTH)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit non-zero if the sprites are stale")
    args = parser.parse_args(argv)

    rendered = build()

    if args.check:
        stale = [p for p, data in rendered.items() if not p.exists() or p.read_bytes() != data]
        if not stale:
            print(f"✓ {len(rendered)} duck sprites up to date")
            return 0
        names = ", ".join(p.name for p in stale)
        print(
            f"✗ duck sprites are stale ({names})\n  Run: uv run --extra charts python scripts/gen_duck_sprites.py",
            file=sys.stderr,
        )
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for path, data in rendered.items():
        path.write_bytes(data)
        total += len(data)
        print(f"✓ {path.relative_to(ROOT)}  {len(data) / 1024:.1f} KB")
    logger.info("gen_duck_sprites: wrote %d layers, %d bytes total", len(rendered), total)
    print(f"  total {total / 1024:.1f} KB  (~{total * 4 / 3 / 1024:.1f} KB base64)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
