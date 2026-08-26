#!/usr/bin/env python3
"""Downscale the duck renditions that get inlined into the web output.

Two outputs, same reason: everything yeaboi serves or writes is one
self-contained file, so any image on it is embedded as a ``data:`` URI rather
than fetched. Bytes here are bytes in every board page and every exported report.

**The mascot sprite.** The source art (``assets/duck-{base,wing,glasses}.png``
in the yeaboi-site checkout) is 480x509 and 199 KB across the three layers —
about 265 KB once base64'd. That is fine for the website, which fetches it over
HTTP and caches it, and
completely wrong for us. This writes a 128px-wide rendition, which is 2x the
64px the duck is drawn at. All three layers get identical dimensions and
identical resampling, because they are composited on top of each other — a
half-pixel difference in scale puts the sunglasses on the duck's forehead.

**The favicon.** ``assets/duck-favicon.png`` is 64x64 and 7 KB, which is
~9.4 KB of base64 in every document we emit for an icon a browser draws at 16px.
The 32px rendition is the 2x asset and costs about a sixth of that.

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

# scripts/ is not a package, and these modules are also loaded by path in tests,
# where sys.path[0] is not this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _sibling_repos import frontend_src, site_assets  # noqa: E402

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
# The master art is a served asset of the website; resolved on use rather than
# at import, so this module stays importable with no sibling checkout.
# See scripts/_site_repo.py.
# The three composited layers are consumed by the front end, so they land in a
# yeaboi-frontend checkout. Resolved lazily, inside render(), so importing this
# module — which the tests do — never demands a second checkout.

# The favicon is read by Python, not bundled by Vite, so it lands in the package
# rather than under frontend/. Not in web/static/ — that directory is the Vite
# output and `test_static_dir_holds_only_bundles` rejects anything that is not a
# .js or .css bundle.
FAVICON_NAME = "duck-favicon.png"
FAVICON_OUTPUT = ROOT / "src" / "yeaboi" / "web" / "favicon.png"

# 2x the 16px a browser draws a tab icon at. Retina tabs and the bookmark bar
# use the 32; going to 64 doubles the base64 in every document for a size
# nothing asks for.
FAVICON_WIDTH = 32

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
    """Render every layer and the favicon. Returns {output path: PNG bytes}."""
    out: dict[Path, bytes] = {}
    for layer in LAYERS:
        source = site_assets() / f"duck-{layer}.png"
        if not source.exists():
            raise FileNotFoundError(f"missing duck layer: {source}")
        out[frontend_src() / "assets" / "duck" / f"{layer}.png"] = _resize(source, TARGET_WIDTH)
    favicon_source = site_assets() / FAVICON_NAME
    if not favicon_source.exists():
        raise FileNotFoundError(f"missing favicon source: {favicon_source}")
    # Same pipeline as the layers, deliberately. The 64px source has ~1,800
    # distinct colours — it is shaded art, not a pixel grid — so the LANCZOS
    # and quantise reasoning in `_resize` applies unchanged. Nearest-neighbour
    # would jag the outline at the exact size where the outline is the whole
    # picture.
    out[FAVICON_OUTPUT] = _resize(favicon_source, FAVICON_WIDTH)
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

    for path in {p.parent for p in rendered}:
        path.mkdir(parents=True, exist_ok=True)
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
