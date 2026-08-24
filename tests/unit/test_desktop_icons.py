"""The committed desktop icon set — present, correctly sized, correctly shaped.

``scripts/gen_desktop_icons.py`` renders it and needs Pillow, which lives in the
``charts`` extra and is absent from most lanes. These assertions read the file
headers directly instead, so the guard runs everywhere `make test` does: a
missing size only surfaces at package time otherwise, and electron-builder's
failure mode for a malformed ``.icns`` is an app that installs with a blank
dock icon.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from gen_desktop_icons import ICNS_TYPES, check, expected, icns_types, png_size  # noqa: E402


class TestTheSetIsComplete:
    def test_check_reports_no_problems(self):
        assert check() == []

    @pytest.mark.parametrize("path", sorted(expected()), ids=lambda p: p.name)
    def test_every_png_exists_at_its_declared_size(self, path: Path):
        assert path.exists(), f"{path.relative_to(ROOT)} is missing"
        assert png_size(path.read_bytes()) == expected()[path]

    def test_the_icns_carries_every_type_macos_looks_for(self):
        found = icns_types((ROOT / "desktop" / "build" / "icon.icns").read_bytes())
        assert set(found) == set(ICNS_TYPES)

    def test_the_ico_is_a_real_ico(self):
        # An .ico is a 6-byte header: reserved 0, type 1, then the image count.
        header = (ROOT / "desktop" / "build" / "icon.ico").read_bytes()[:6]
        assert header[:4] == b"\x00\x00\x01\x00"
        assert int.from_bytes(header[4:6], "little") > 0

    def test_the_set_is_committed_and_not_merely_present(self):
        """`.gitignore` carries an unanchored `build/` glob, which swallows
        `desktop/build/` unless it is re-included. On the machine that rendered
        them the icons are there either way; CI checks out an app with none."""
        tracked = subprocess.run(
            ["git", "ls-files", "desktop/build", "desktop/resources"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if tracked.returncode != 0:
            pytest.skip("not a git checkout")
        committed = {ROOT / line for line in tracked.stdout.split()}
        missing = sorted(str(path.relative_to(ROOT)) for path in expected() if path not in committed)
        assert not missing, f"rendered but never committed: {missing}"


class TestTheTrayIconsMatchWhatTheShellAsksFor:
    """`tray.ts` names these three files; a rename here breaks a silent path."""

    def test_the_template_pair_ships_beside_the_colour_icon(self):
        resources = ROOT / "desktop" / "resources"
        names = {path.name for path in resources.glob("duck-tray*.png")}
        assert names == {"duck-tray.png", "duck-trayTemplate.png", "duck-trayTemplate@2x.png"}

    def test_the_template_is_alpha_only(self):
        """A template image whose colours survived would render as a black box.

        Colour type 6 is RGBA and 4 is grey+alpha; either is fine as long as
        every visible pixel is black, which is what the generator's stencil
        produces. The cheap proxy here: the PNG must carry an alpha channel.
        """
        header = (ROOT / "desktop" / "resources" / "duck-trayTemplate.png").read_bytes()
        colour_type = header[25]
        assert colour_type in (4, 6), f"template PNG has colour type {colour_type}, which has no alpha"
