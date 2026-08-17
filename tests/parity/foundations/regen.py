"""Regenerate the committed foundations goldens after a deliberate change.

Usage: ``uv run python -m tests.parity.foundations.regen``

Deletes goldens whose fixture no longer exists, dumps every current fixture
in a fresh sandbox, and rewrites the files under
``tests/parity/goldens/foundations/``. Mirror the behaviour change into
``go/internal/home`` first — its golden test replays these files, so a
regenerated golden the Go port cannot reproduce fails ``make go-test``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tests.parity.foundations import matrix


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


if __name__ == "__main__":
    main()
