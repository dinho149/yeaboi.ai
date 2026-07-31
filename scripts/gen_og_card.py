#!/usr/bin/env python3
"""Render the 1200x630 Open Graph card served as docs/assets/og-card.png.

Every page on yeaboi.ai points ``og:image`` and ``twitter:image`` at this file,
so it is what a link to the site looks like in Slack, LinkedIn, X, iMessage and
Google Discover.

It exists because the obvious candidate does not work. ``docs/banner.jpg`` is
already deployed, but it renders the words **"Scrum AI"** — the superseded
product name — in neon, and it is 1200x400, outside the 1.91:1 band social
cards crop to. Shipping it would put the wrong brand on every share.

**Not a build step.** Pillow ships only in the ``charts`` extra, so a normal
``uv sync`` cannot run this — exactly the situation
``scripts/gen_duck_sprites.py`` is in, and it is deliberately absent from CI for
the same reason. The output is committed and guarded instead by
``tests/unit/test_site_seo.py``, which asserts the file exists, is 1200x630 and
is referenced by every page. A matplotlib upgrade could ship a different DejaVu
build and re-rendering would differ by a byte, so a ``--check`` in CI would fail
unrelated PRs.

Fonts come out of matplotlib's bundled DejaVu rather than the site's webfonts
(Geist / JetBrains Mono): matplotlib is the only reason Pillow is here at all,
so its fonts are guaranteed present, and nothing has to be committed.

Usage::

    uv run --extra charts python scripts/gen_og_card.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "docs" / "assets"
OUT = ASSETS / "og-card.png"

W, H = 1200, 630

# Straight from the site's own tokens in docs/assets/site.css, so the card and
# the page a click lands on are visibly the same product.
BG = (11, 12, 14)  # --bg        #0b0c0e
TEXT = (228, 230, 232)  # --text      #e4e6e8
MUTED = (154, 160, 168)  # --text-muted #9aa0a8
DIM = (98, 103, 111)  # --text-dim  #62676f
ACCENT = (140, 170, 255)  # --accent-bright #8caaff

logger = logging.getLogger(__name__)


def _font(name: str, size: int):
    import matplotlib
    from PIL import ImageFont

    path = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / name
    return ImageFont.truetype(str(path), size)


def _glow(size: tuple[int, int], colour: tuple[int, int, int], strength: int):
    """A soft radial accent behind the mascot.

    Drawn at full size and Gaussian-blurred rather than drawn small and
    upscaled: LANCZOS on a hard-edged ellipse keeps the edge, which renders as
    a visible angular polygon instead of a glow. The blur radius has to be a
    large fraction of the ellipse for the falloff to reach the frame edges,
    otherwise the mask boundary itself becomes the artifact.
    """
    from PIL import Image, ImageDraw, ImageFilter

    w, h = size
    mask = Image.new("L", size, 0)
    inset = w // 5
    ImageDraw.Draw(mask).ellipse((inset, inset, w - inset, h - inset), fill=strength)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=w // 6))
    return Image.new("RGB", size, colour), mask


def _duck(height: int):
    """Composite the three mascot layers at one identical scale.

    All three sprites are 480x509 and are drawn stacked, so they must be
    resized with the same box and the same filter — a half-pixel difference in
    scale puts the sunglasses on the duck's forehead. Same rule as
    gen_duck_sprites.py.
    """
    from PIL import Image

    layers = [Image.open(ASSETS / f"duck-{n}.png").convert("RGBA") for n in ("base", "wing", "glasses")]
    w0, h0 = layers[0].size
    size = (round(w0 * height / h0), height)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    for layer in layers:
        out.alpha_composite(layer.resize(size, Image.LANCZOS))
    return out


def build():
    from PIL import Image, ImageDraw

    card = Image.new("RGB", (W, H), BG)

    # Accent bloom behind the mascot, low and to the right.
    layer, mask = _glow((900, 900), ACCENT, 26)
    card.paste(layer, (W - 620, H - 560), mask)

    duck = _duck(360)
    card.paste(duck, (W - duck.width - 96, (H - duck.height) // 2 + 10), duck)

    draw = ImageDraw.Draw(card)
    x = 84

    draw.text((x, 150), "yeaboi", font=_font("DejaVuSans-Bold.ttf", 104), fill=TEXT)
    draw.text((x, 276), "an AI Scrum Master", font=_font("DejaVuSans.ttf", 44), fill=MUTED)
    draw.text((x, 330), "for your terminal", font=_font("DejaVuSans.ttf", 44), fill=MUTED)

    # The install command: the single most useful string on the card, and the
    # one that is currently wrong everywhere search engines quote it from.
    mono = _font("DejaVuSansMono-Bold.ttf", 26)
    cmd = "uv tool install yeaboi"
    pad = 18
    tw = draw.textlength(cmd, font=mono)
    box = (x, 430, x + tw + pad * 2, 430 + 26 + pad * 2)
    draw.rounded_rectangle(box, radius=10, fill=(19, 21, 25), outline=(38, 41, 47))
    draw.text((x + pad, 430 + pad - 2), cmd, font=mono, fill=ACCENT)

    small = _font("DejaVuSans.ttf", 22)
    draw.text((x, 528), "planning · standups · retros · poker · 1:1s · reports", font=small, fill=DIM)
    draw.text((x, 562), "MIT · Python 3.11+ · yeaboi.ai", font=small, fill=DIM)
    return card


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    card = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    card.save(OUT, "PNG", optimize=True, compress_level=9)
    kb = OUT.stat().st_size / 1024
    logger.info("✓ wrote %s (%dx%d, %.0f KB)", OUT.relative_to(ROOT), card.width, card.height, kb)
    if kb > 300:
        logger.warning("card is over 300 KB — some scrapers skip large images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
