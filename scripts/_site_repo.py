"""Locate the yeaboi-site checkout.

Some generators here cross the repo boundary, in both directions, because a
generator lives with the code it knows about while its output lives with the
surface that serves it:

* ``generate_graph_png.py`` and ``record_demo.py`` import yeaboi or drive its
  TUI, and write ``graph.png`` / ``demo.gif`` into the website.
* ``gen_duck_sprites.py``, ``gen_mascot_sprites.py`` and ``gen_desktop_icons.py``
  read the master duck art, which is a *served* asset of the website, and write
  renditions into this package, the front end and the desktop shell.

None of them runs on a PR — their outputs are committed and guarded — so needing
both checkouts is a cost paid by whoever changes the product or the brand, and
never by CI.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_HINT = """Set YEABOI_SITE to your yeaboi-site checkout, or clone it beside this repo:

    git clone git@github.com:yeaboi-ai/yeaboi-site.git
    YEABOI_SITE=/path/to/yeaboi-site make <target>
"""


def _main_checkout() -> Path:
    """This repo's main working tree — not the worktree the caller stands in.

    Worktrees live at ``<main>/.claude/worktrees/<name>``, so walking up from
    ``ROOT`` finds ``.claude/worktrees`` rather than the directory the sibling
    repos are cloned into.
    """
    try:
        common = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ROOT
    return Path(common).parent if common else ROOT


def site_root(*, required: bool = True) -> Path | None:
    """The yeaboi-site checkout: ``$YEABOI_SITE``, else a sibling of this repo."""
    if override := os.environ.get("YEABOI_SITE"):
        found = Path(override).expanduser().resolve()
        if not found.is_dir():
            raise SystemExit(f"YEABOI_SITE points at {found}, which is not a directory.\n\n{_HINT}")
    else:
        found = _main_checkout().parent / "yeaboi-site"

    # index.html rather than the directory: an empty dir left by a failed clone
    # would otherwise be accepted and the write would land nowhere useful.
    if (found / "index.html").is_file():
        return found
    if not required:
        return None
    raise SystemExit(f"no yeaboi-site checkout at {found}.\n\n{_HINT}")


def site_assets() -> Path:
    """The website's ``assets/`` — the master brand art lives here."""
    return site_root() / "assets"
