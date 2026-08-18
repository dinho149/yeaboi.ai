"""Regenerate the committed persistence goldens after a deliberate change.

Usage: ``uv run python -m tests.parity.persistence.regen``

Builds every fixture fresh, migrates it through the real ``SessionStore``,
and rewrites the post-migration dumps under
``tests/parity/goldens/persistence/``. Mirror the sessions.py behaviour
change into ``go/internal/sessions`` first (W9 phase 2 on) — the Go arm
replays the same fixtures, so a regenerated golden the Go port cannot
reproduce fails ``make parity``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tests.parity.persistence import dump as dump_mod
from tests.parity.persistence import make_fixture

GOLDENS_DIR = Path(__file__).parent.parent / "goldens" / "persistence"


def golden_path(fixture: make_fixture.Fixture) -> Path:
    return GOLDENS_DIR / f"{fixture.name}.json"


def build_golden(fixture: make_fixture.Fixture) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sessions.db"
        fixture.build(db)
        return dump_mod.migrate_and_dump(db)


def main() -> None:
    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    names = {f.name for f in make_fixture.FIXTURES}
    for stale in GOLDENS_DIR.glob("*.json"):
        if stale.stem not in names:
            stale.unlink()
            print(f"deleted stale {stale}")
    for fixture in make_fixture.FIXTURES:
        golden_path(fixture).write_text(dump_mod.render(build_golden(fixture)), encoding="utf-8")
        print(f"wrote {golden_path(fixture)}")


if __name__ == "__main__":
    main()
