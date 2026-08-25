#!/usr/bin/env python3
"""Assert the built wheel declares the dependencies an install cannot work without.

`pip install yeaboi` must produce a working app, and the one thing that can be
missing with every test still green is a dependency the *source tree* satisfies
another way. ``yeaboi-web-assets`` is exactly that: the suite resolves it from
the installed venv, so a packaging change that dropped it from the metadata
would leave the wheel building, installing, and rendering nothing on every board
and every export.

This used to assert the bundles were *inside* the wheel. They are not any more —
they ship in ``yeaboi-web-assets``, built and published from **yeaboi-frontend**,
whose own ``make wheel-check`` asserts that half. What is left on this side is
the declaration that links the two.

Standard library only, and no yeaboi import: it runs against ``dist/``, not
against the checkout, and putting a dependency resolve in front of it would make
the local gate slower than the thing it is guarding.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import tarfile
import zipfile

# Distributions with no working fallback if the metadata loses them. A tripwire,
# not an inventory: an inventory of every dependency would need updating
# constantly and would rot into being deleted.
REQUIRED_DEPS = ("yeaboi-web-assets",)


def check(dist: pathlib.Path) -> list[str]:
    """Return a list of problems; empty means the artifacts are good."""
    problems: list[str] = []

    wheels = sorted(dist.glob("*.whl"))
    sdists = sorted(dist.glob("*.tar.gz"))
    if not wheels:
        problems.append(f"no wheel in {dist}/ — run `uv build` first")
    if not sdists:
        problems.append(f"no sdist in {dist}/ — run `uv build` first")
    if problems:
        return problems

    wheel = wheels[-1]
    with zipfile.ZipFile(wheel) as zf:
        metadata = next((n for n in zf.namelist() if n.endswith(".dist-info/METADATA")), None)
        if metadata is None:
            return [f"wheel {wheel.name} has no METADATA"]
        # The distribution name only. A specifier follows it with no space
        # (`yeaboi-web-assets<2,>=1`), so this stops at the first character that
        # cannot be part of a name rather than splitting on whitespace. Matching
        # the name alone is deliberate: a version bound moving is a decision,
        # and pinning it here would fail for the one reason it should not.
        requires = {
            match.group(1).lower()
            for line in zf.read(metadata).decode("utf-8").splitlines()
            if (match := re.match(r"Requires-Dist:\s*([A-Za-z0-9._-]+)", line))
        }
    for want in REQUIRED_DEPS:
        if want.lower() not in requires:
            problems.append(f"wheel {wheel.name} does not require {want}")

    sdist = sdists[-1]
    with tarfile.open(sdist) as tf:
        members = {m.name.split("/", 1)[1] for m in tf.getmembers() if "/" in m.name}
    for want in ("pyproject.toml", "src/yeaboi/__init__.py"):
        if want not in members:
            problems.append(f"sdist {sdist.name} is missing {want}")

    if not problems:
        print(f"✓ {wheel.name} requires {', '.join(REQUIRED_DEPS)}; {sdist.name} is complete")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", default="dist", help="directory holding the built wheel and sdist")
    args = parser.parse_args(argv)

    problems = check(pathlib.Path(args.dist))
    for problem in problems:
        print(f"✗ {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
