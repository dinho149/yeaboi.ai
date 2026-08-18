"""Regenerate the committed foundations goldens after a deliberate change.

Usage: ``uv run python -m tests.parity.foundations.regen``

Deletes goldens whose fixture no longer exists, dumps every current fixture
in a fresh sandbox, and rewrites the files under
``tests/parity/goldens/foundations/`` — then rewrites the argv golden at
``tests/parity/goldens/cli/args.json`` from ``argvectors.VECTORS`` and the
help screens under ``tests/parity/goldens/cli/help/`` from ``helpdump``.
Mirror the behaviour change into ``go/internal/home`` (or ``go/cmd/yeaboi``
+ ``go/internal/argview``) first — their golden tests replay these files,
so a regenerated golden the Go port cannot reproduce fails ``make go-test``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tests.parity.foundations import argdump, changelogdump, helpdump, matrix


def main() -> None:
    matrix.GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    names = {f.name for f in matrix.FIXTURES}
    for stale in matrix.GOLDENS_DIR.glob("*.json"):
        if stale.stem not in names:
            stale.unlink()
            print(f"deleted stale {stale}")
    for fixture in matrix.FIXTURES:
        with tempfile.TemporaryDirectory() as tmp:
            golden = matrix.golden_for(fixture, Path(tmp) / "sandbox")
        matrix.golden_path(fixture).write_text(matrix.render_golden(golden), encoding="utf-8")
        print(f"wrote {matrix.golden_path(fixture)}")

    argdump.GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    argdump.GOLDEN_PATH.write_text(argdump.render_golden(), encoding="utf-8")
    print(f"wrote {argdump.GOLDEN_PATH}")

    changelogdump.PARSED_GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    changelogdump.PARSED_GOLDEN.write_text(changelogdump.render_golden(), encoding="utf-8")
    print(f"wrote {changelogdump.PARSED_GOLDEN}")

    helpdump.GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    screens = helpdump.build_screens()
    for stale in helpdump.GOLDENS_DIR.glob("*.txt"):
        if stale.name not in screens:
            stale.unlink()
            print(f"deleted stale {stale}")
    for name, text in screens.items():
        (helpdump.GOLDENS_DIR / name).write_text(text, encoding="utf-8")
        print(f"wrote {helpdump.GOLDENS_DIR / name}")


if __name__ == "__main__":
    main()
