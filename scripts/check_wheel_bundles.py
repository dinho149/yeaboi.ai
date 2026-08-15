#!/usr/bin/env python3
"""Assert the built wheel and sdist carry the committed front-end bundles.

`pip install yeaboi` must work with no Node, which means the Vite output in
``src/yeaboi/web/static/`` has to survive packaging. It has silently not, before:
the bundles are committed but a packaging config change can drop them from the
wheel with every test still green, because the Python suite reads them from the
source tree.

This lived inline in ``.github/workflows/ci.yml``'s ``package`` job, where it was
unrunnable locally — so the only way to fail it was to open the PR. Both that job
and ``make package-check`` call this module now, so there is one assertion rather
than two copies drifting apart.

Standard library only, and no yeaboi import: it runs against ``dist/``, not
against the checkout, and putting a dependency resolve in front of it would make
the local gate slower than the thing it is guarding.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import tarfile
import zipfile

# The two bundles every install path needs. Not the whole set: this is a
# "packaging dropped the static tree" tripwire, not an inventory — an inventory
# would need updating with every new surface and would rot into being deleted.
REQUIRED = ("yeaboi/web/static/export.js", "yeaboi/web/static/export.css")


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
    names = set(zipfile.ZipFile(wheel).namelist())
    for want in REQUIRED:
        if want not in names:
            problems.append(f"wheel {wheel.name} is missing {want}")

    sdist = sdists[-1]
    with tarfile.open(sdist) as tf:
        members = {m.name.split("/", 1)[1] for m in tf.getmembers() if "/" in m.name}
    # The sdist carries the TS sources too, so a release is reproducible.
    for want in sorted({"src/" + n for n in REQUIRED} | {"frontend/package.json"}):
        if want not in members:
            problems.append(f"sdist {sdist.name} is missing {want}")

    if not problems:
        print(f"✓ {wheel.name} and {sdist.name} carry the bundles")
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
