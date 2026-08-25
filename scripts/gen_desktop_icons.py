#!/usr/bin/env python3
"""Render the desktop app's icon set from the website's duck art.

The shell had one image — a 64px duck used as the tray icon — and packaging
needs a family: a macOS ``.icns``, a Windows ``.ico``, a Linux icon directory, a
menu-bar template, and a DMG backdrop. All of them are derived here from the
three 480x509 layers in the website's ``assets/`` (base, wing, glasses) that already
compose the duck on yeaboi.ai, so the dock icon and the landing page are the
same bird.

**Not a build step.** Pillow ships only in the ``charts`` extra, exactly as with
``scripts/gen_og_card.py`` and ``scripts/gen_duck_sprites.py``: the output is
committed and CI never re-renders it. ``--check`` therefore asserts *structure*
— the files exist, carry the right dimensions, and the ``.icns`` holds every
type macOS looks for — and never bytes, because a Pillow upgrade re-encoding a
PNG differently would otherwise redden unrelated PRs.
``tests/unit/test_desktop_icons.py`` runs the same assertions with no Pillow.

Usage::

    uv run --extra charts python scripts/gen_desktop_icons.py [--check]
"""

from __future__ import annotations

import argparse
import struct
import sys
from io import BytesIO
from pathlib import Path

# scripts/ is not a package, and these modules are also loaded by path in tests,
# where sys.path[0] is not this directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _sibling_repos import site_assets  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
# The master art is a served asset of the website, so it is resolved when it is
# needed rather than at import — tests/unit/test_desktop_icons.py imports this
# module to assert the committed icon set, with no sibling checkout in CI.
# See scripts/_site_repo.py.
BUILD = ROOT / "desktop" / "build"
RESOURCES = ROOT / "desktop" / "resources"

MASTER = 1024
#: Inset of the plate inside the canvas, and its corner radius, at 1024px.
#: macOS wants an app icon to sit in a rounded square with air around it;
#: Windows and Linux draw the same file unpadded and are happy either way.
PLATE_MARGIN = 64
PLATE_RADIUS = 208
#: How much of the plate the duck fills, and how far above centre it sits.
DUCK_SCALE = 0.74
DUCK_RISE = 0.03

# The plate is the one place the brand's dark palette meets the mascot's green.
# A green plate would swallow the duck's own head; midnight with a green wash
# keeps him legible at 16px, where the icon is mostly just a coloured square.
PLATE_TOP = (18, 43, 34)
PLATE_BOTTOM = (8, 16, 14)
DUCK_GREEN = (42, 170, 105)  # --duck, the canonical value in palette.css

#: PNG sizes in the Linux icon directory and behind the .ico.
PNG_SIZES = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: icns chunk type -> pixel size. Every one carries a PNG payload, which macOS
#: has accepted since 10.7; the `ic1x` types are the retina halves of the
#: smaller `icp` ones and a modern Finder expects them present.
ICNS_TYPES = {
    b"icp4": 16,
    b"icp5": 32,
    b"ic11": 32,
    b"ic12": 64,
    b"ic07": 128,
    b"ic13": 256,
    b"ic08": 256,
    b"ic14": 512,
    b"ic09": 512,
    b"ic10": 1024,
}

DMG_SIZE = (540, 380)
#: Where the app icon and the Applications alias sit in the DMG window; the
#: backdrop's arrow is drawn on the same line (mirrored in electron-builder.yml).
DMG_ICON_Y = 190
TRAY_SIZE = 32


def _duck():
    """The three website layers composited and cropped to the bird itself."""
    from PIL import Image

    art = site_assets()
    duck = Image.open(art / "duck-base.png").convert("RGBA")
    for layer in ("duck-wing.png", "duck-glasses.png"):
        duck = Image.alpha_composite(duck, Image.open(art / layer).convert("RGBA"))
    return duck.crop(duck.getbbox())


def _plate(size: int):
    """The rounded square the duck sits on: a vertical gradient, a green rim."""
    from PIL import Image, ImageDraw

    scale = size / MASTER
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        gradient.putpixel(
            (0, y),
            tuple(round(a + (b - a) * t) for a, b in zip(PLATE_TOP, PLATE_BOTTOM)) + (255,),
        )
    gradient = gradient.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    box = (
        round(PLATE_MARGIN * scale),
        round(PLATE_MARGIN * scale),
        size - round(PLATE_MARGIN * scale) - 1,
        size - round(PLATE_MARGIN * scale) - 1,
    )
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=round(PLATE_RADIUS * scale), fill=255)

    plate = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    plate.paste(gradient, (0, 0), mask)
    ImageDraw.Draw(plate).rounded_rectangle(
        box,
        radius=round(PLATE_RADIUS * scale),
        outline=DUCK_GREEN + (90,),
        width=max(round(6 * scale), 1),
    )
    return plate


def master():
    """The 1024px icon every other size is resampled from."""
    from PIL import Image, ImageChops, ImageFilter

    icon = _plate(MASTER)
    duck = _duck()
    inner = MASTER - 2 * PLATE_MARGIN
    width = round(inner * DUCK_SCALE)
    height = round(duck.height * width / duck.width)
    duck = duck.resize((width, height), Image.LANCZOS)

    top = (MASTER - height) // 2 - round(MASTER * DUCK_RISE)
    left = (MASTER - width) // 2

    # A soft drop shadow, clipped to the plate so it never spills onto the
    # transparent corners.
    shadow = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    shadow.paste(Image.new("RGBA", duck.size, (0, 0, 0, 120)), (left, top + 18), duck)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    shadow.putalpha(ImageChops.multiply(shadow.getchannel("A"), icon.getchannel("A")))

    icon = Image.alpha_composite(icon, shadow)
    icon.paste(duck, (left, top), duck)
    return icon


def tray_template(size: int = TRAY_SIZE):
    """The menu-bar duck for macOS: alpha only, since the OS paints the pixels.

    A flat silhouette loses the glasses and the wing, so the bird's own
    luminance is folded into the alpha — dark feathers stay opaque, highlights
    thin out, and the shape still reads at 20pt.
    """
    from PIL import Image, ImageChops

    duck = _tray_duck(size)
    # Dark feathers stay opaque, highlights thin out; the alpha channel keeps
    # the silhouette so nothing bleeds outside the bird.
    weight = duck.convert("L").point(lambda value: 255 - round(value * 0.55))
    stencil = Image.new("RGBA", duck.size, (0, 0, 0, 255))
    stencil.putalpha(ImageChops.multiply(duck.getchannel("A"), weight))
    return _square(stencil)


def tray_colour(size: int = TRAY_SIZE):
    """The tray duck for Windows and Linux, which draw the icon as given."""
    return _square(_tray_duck(size))


def _tray_duck(size: int):
    """The duck scaled to fit a square tray slot — he is taller than he is wide."""
    from PIL import Image

    duck = _duck()
    return duck.resize((round(duck.width * size / duck.height), size), Image.LANCZOS)


def _square(image):
    """Centre an image on a transparent square — tray slots are square."""
    from PIL import Image

    side = max(image.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(image, ((side - image.width) // 2, (side - image.height) // 2), image)
    return canvas


def dmg_background():
    """The DMG window backdrop: brand dark, the duck, and the drag arrow."""
    from PIL import Image, ImageDraw

    width, height = DMG_SIZE
    background = Image.new("RGBA", (width, height), PLATE_BOTTOM + (255,))
    draw = ImageDraw.Draw(background)
    for y in range(height):
        t = y / (height - 1)
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(round(a + (b - a) * t) for a, b in zip(PLATE_TOP, PLATE_BOTTOM)) + (255,),
        )
    duck = _duck()
    small = duck.resize((64, round(duck.height * 64 / duck.width)), Image.LANCZOS)
    background.paste(small, (width // 2 - small.width // 2, 12), small)
    draw.text((width // 2, 104), "yeaboi", font=_font(30), fill=(228, 236, 232), anchor="mm")

    # The arrow sits on the icon centreline — DMG_ICON_Y, the same y the two
    # icons are placed at in electron-builder.yml.
    draw.line([(width // 2 - 34, DMG_ICON_Y), (width // 2 + 30, DMG_ICON_Y)], fill=DUCK_GREEN + (210,), width=4)
    draw.polygon(
        [
            (width // 2 + 46, DMG_ICON_Y),
            (width // 2 + 28, DMG_ICON_Y - 12),
            (width // 2 + 28, DMG_ICON_Y + 12),
        ],
        fill=DUCK_GREEN + (210,),
    )
    return background.convert("RGB")


def _font(size: int):
    """DejaVu out of matplotlib — the same font source ``gen_og_card.py`` uses,
    so nothing has to be committed and nothing has to be installed."""
    import matplotlib
    from PIL import ImageFont

    return ImageFont.truetype(str(Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans.ttf"), size)


def _png_bytes(image, size: int) -> bytes:
    from PIL import Image

    buffer = BytesIO()
    image.resize((size, size), Image.LANCZOS).save(buffer, format="PNG")
    return buffer.getvalue()


def _icns(image) -> bytes:
    """An .icns container. The format is a magic word, a length, then chunks."""
    chunks = b""
    for kind, size in ICNS_TYPES.items():
        payload = _png_bytes(image, size)
        chunks += kind + struct.pack(">I", len(payload) + 8) + payload
    return b"icns" + struct.pack(">I", len(chunks) + 8) + chunks


def write_all() -> list[Path]:
    from PIL import Image

    icon = master()
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "icons").mkdir(exist_ok=True)
    written: list[Path] = []

    icon.save(BUILD / "icon.png")
    written.append(BUILD / "icon.png")
    (BUILD / "icon.icns").write_bytes(_icns(icon))
    written.append(BUILD / "icon.icns")
    icon.save(BUILD / "icon.ico", format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    written.append(BUILD / "icon.ico")
    for size in PNG_SIZES:
        path = BUILD / "icons" / f"{size}x{size}.png"
        icon.resize((size, size), Image.LANCZOS).save(path)
        written.append(path)

    dmg_background().save(BUILD / "dmg-background.png")
    written.append(BUILD / "dmg-background.png")
    dmg_background().resize((DMG_SIZE[0] * 2, DMG_SIZE[1] * 2), Image.LANCZOS).save(BUILD / "dmg-background@2x.png")
    written.append(BUILD / "dmg-background@2x.png")

    RESOURCES.mkdir(parents=True, exist_ok=True)
    # Rendered at each size rather than upscaled: a menu-bar icon is 20pt of
    # detail and an upscaled @2x throws away the retina it exists for.
    tray_template(TRAY_SIZE).save(RESOURCES / "duck-trayTemplate.png")
    tray_template(TRAY_SIZE * 2).save(RESOURCES / "duck-trayTemplate@2x.png")
    tray_colour().save(RESOURCES / "duck-tray.png")
    written += [
        RESOURCES / "duck-trayTemplate.png",
        RESOURCES / "duck-trayTemplate@2x.png",
        RESOURCES / "duck-tray.png",
    ]
    return written


def expected() -> dict[Path, tuple[int, int]]:
    """Every committed file and the size it must carry."""
    files = {
        BUILD / "icon.png": (MASTER, MASTER),
        BUILD / "dmg-background.png": DMG_SIZE,
        BUILD / "dmg-background@2x.png": (DMG_SIZE[0] * 2, DMG_SIZE[1] * 2),
        RESOURCES / "duck-tray.png": (TRAY_SIZE, TRAY_SIZE),
        RESOURCES / "duck-trayTemplate.png": (TRAY_SIZE, TRAY_SIZE),
        RESOURCES / "duck-trayTemplate@2x.png": (TRAY_SIZE * 2, TRAY_SIZE * 2),
    }
    for size in PNG_SIZES:
        files[BUILD / "icons" / f"{size}x{size}.png"] = (size, size)
    return files


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height out of a PNG's IHDR — no image library needed."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def icns_types(data: bytes) -> list[bytes]:
    """The chunk types in an .icns, in file order."""
    if data[:4] != b"icns":
        raise ValueError("not an icns")
    total = struct.unpack(">I", data[4:8])[0]
    offset, found = 8, []
    while offset < min(total, len(data)):
        kind = data[offset : offset + 4]
        length = struct.unpack(">I", data[offset + 4 : offset + 8])[0]
        if length < 8:
            raise ValueError(f"icns chunk {kind!r} claims {length} bytes")
        found.append(kind)
        offset += length
    return found


def check() -> list[str]:
    problems = []
    for path, size in expected().items():
        if not path.exists():
            problems.append(f"missing {path.relative_to(ROOT)}")
            continue
        actual = png_size(path.read_bytes())
        if actual != size:
            problems.append(f"{path.relative_to(ROOT)} is {actual[0]}x{actual[1]}, expected {size[0]}x{size[1]}")
    icns = BUILD / "icon.icns"
    if not icns.exists():
        problems.append("missing desktop/build/icon.icns")
    else:
        missing = set(ICNS_TYPES) - set(icns_types(icns.read_bytes()))
        if missing:
            problems.append(f"icon.icns is missing {', '.join(sorted(t.decode() for t in missing))}")
    if not (BUILD / "icon.ico").exists():
        problems.append("missing desktop/build/icon.ico")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="assert the committed set instead of rendering it")
    args = parser.parse_args()
    if args.check:
        problems = check()
        for problem in problems:
            print(f"✗ {problem}")
        if problems:
            print("run: uv run --extra charts python scripts/gen_desktop_icons.py")
            return 1
        print(f"✓ desktop icon set is complete ({len(expected()) + 2} files)")
        return 0
    for path in write_all():
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
