#!/usr/bin/env python3
"""Bump the single-source project version in ``pyproject.toml``.

The version lives only in ``pyproject.toml`` (``src/yeaboi/__init__.py`` reads
it from the installed package metadata), and it is always a plain ``X.Y.Z``: the
*next final*, whether or not it has been released yet.

Bumping this line is what makes a merge release-worthy. ``publish-beta.yml`` then
publishes a PyPI **pre-release** — ``X.Y.ZrcN``, numbered by
``scripts/release_channel.py`` and stamped into a throwaway checkout, never
committed — and those accumulate until a human promotes the batch, at which point
``publish.yml`` publishes the plain ``X.Y.Z`` and tags it. Merging no longer ships
to users on its own.

``bump()`` below rejects anything that is not ``X.Y.Z``, and that refusal is
load-bearing rather than fussy: ``auto-version.yml`` runs this script on every
release-worthy PR, so an rc string committed to ``main`` would break the next PR's
bump, and the one after, with no obvious cause.

This helper keeps the arithmetic deterministic and testable so the ``auto-version``
GitHub workflow only has to *choose the level* — the LLM never hand-computes a
version. It's equally handy for humans:

    python scripts/bump_version.py minor     # 1.5.0 -> 1.6.0, rewrites pyproject
    python scripts/bump_version.py --current # print current version, no change
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# The version line publish.yml greps: `^version = "X.Y.Z"`. Match exactly that so
# the single source of truth (and that grep) keep working.
_VERSION_RE = re.compile(r'^version = "([^"]+)"', re.MULTILINE)
_LEVELS = ("major", "minor", "patch")
_DEFAULT_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def bump(version: str, level: str) -> str:
    """Return ``version`` bumped by semver ``level`` (major | minor | patch).

    A ``major`` bump zeroes minor+patch; a ``minor`` bump zeroes patch. Expects a
    plain ``X.Y.Z`` version (the format this project uses).
    """
    if level not in _LEVELS:
        raise ValueError(f"level must be one of {_LEVELS}, got {level!r}")
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"expected an X.Y.Z version, got {version!r}")
    major, minor, patch = (int(p) for p in parts)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def read_current(pyproject: Path = _DEFAULT_PYPROJECT) -> str:
    """Return the current version string from ``pyproject.toml``."""
    match = _VERSION_RE.search(pyproject.read_text())
    if not match:
        raise SystemExit(f'no `version = "..."` line found in {pyproject}')
    return match.group(1)


def write_version(new_version: str, pyproject: Path = _DEFAULT_PYPROJECT) -> None:
    """Rewrite the version line in ``pyproject.toml`` to ``new_version``."""
    text = pyproject.read_text()
    new_text, count = _VERSION_RE.subn(f'version = "{new_version}"', text, count=1)
    if count != 1:
        raise SystemExit(f"could not update the version line in {pyproject}")
    pyproject.write_text(new_text)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bump the version in pyproject.toml")
    parser.add_argument("level", nargs="?", choices=_LEVELS, help="semver level to bump")
    parser.add_argument("--current", action="store_true", help="print the current version and exit")
    args = parser.parse_args(argv)

    current = read_current()
    if args.current:
        print(current)
        return
    if not args.level:
        parser.error("a level (major|minor|patch) is required unless --current is given")

    new_version = bump(current, args.level)
    write_version(new_version)
    print(new_version)


if __name__ == "__main__":
    main()
