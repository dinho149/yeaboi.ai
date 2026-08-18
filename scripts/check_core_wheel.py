#!/usr/bin/env python3
"""Assert a built yeaboi-core wheel ships the sidecar and nothing else.

The core wheel exists to carry exactly one binary: ``yeaboi_core/bin/yeaboi-core``
(``.exe`` on Windows). Since W8 the same ``go/`` tree also builds the future
``yeaboi`` CLI (``go/cmd/yeaboi``) — hidden and unshipped until W19 (cutover),
per ``cowork/migration/program.md`` §2. A hatch-hook change that started
packaging it would ship the unfinished product a wave early, with every test
still green: nothing else reads the built wheel's file list.

Runs in ci.yml's ``go`` job against the wheel the smoke step builds, and
locally against any dist dir:

    uv build --wheel packaging/yeaboi-core -o dist-core
    python3 scripts/check_core_wheel.py dist-core

Standard library only, same reasoning as ``check_wheel_bundles.py``: it runs
against built artifacts, and a dependency resolve in front of it would make it
slower than the thing it guards.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import zipfile


def check(dist: pathlib.Path) -> list[str]:
    """Return a list of problems; empty means the wheels are good."""
    problems: list[str] = []

    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        problems.append(f"no wheel found in {dist}/")

    for wheel in wheels:
        binaries = [n for n in zipfile.ZipFile(wheel).namelist() if n.startswith("yeaboi_core/bin/")]
        if not binaries:
            problems.append(f"{wheel.name}: no binary under yeaboi_core/bin/ — the hatch hook stopped packaging it")
        strays = [n for n in binaries if not n.rsplit("/", 1)[1].startswith("yeaboi-core")]
        if strays:
            problems.append(
                f"{wheel.name}: unexpected binaries {strays} — the `yeaboi` CLI is hidden and "
                "unshipped until W19 (cowork/migration/program.md §2); only yeaboi-core belongs here"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dist", nargs="?", default="dist-core", help="directory holding the built core wheel(s)")
    args = parser.parse_args()

    problems = check(pathlib.Path(args.dist))
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    if not problems:
        print("core wheel ok: sidecar binary present, no stray binaries")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
