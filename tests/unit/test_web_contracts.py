"""The Python half of everything generated for the browser.

Two artifacts leave this repo for **yeaboi-frontend**, which vendors them by
sha: ``contracts/web/enums.json`` (the tuples the boards validate against) and
``contracts/web/ui.json`` (the accents and timings its own tests assert with).
This module is what keeps them fresh.

**Deliberately its own file.** These checks used to sit in
``test_web_frontend_guards.py``, which skipped itself whole when ``frontend/``
was absent — fine while the front end was a sibling directory, and silently
fatal the moment it was not. Nothing here reads a front-end file, so nothing
here can be skipped by one going missing.

The chain has two links and only this one runs in the Python suite. A tuple
that moved without the contract following fails here; a contract that moved
without the TypeScript following puts ``contracts/web/`` in the diff, which is
what selects the front-end repo's own ``--check``.

There is no Python re-implementation of the TypeScript renderer to close that
in one place: a second implementation of the thing this split exists to have
one of is the drift, not the fix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts" / "web"

sys.path.insert(0, str(ROOT / "scripts"))

import gen_web_types  # noqa: E402
import gen_web_ui_contract  # noqa: E402


class TestTheContractsAreCurrent:
    @pytest.mark.parametrize(
        "module",
        [gen_web_types, gen_web_ui_contract],
        ids=lambda m: m.OUTPUT.name,
    )
    def test_it_matches_its_generator(self, module) -> None:
        """Stale means the browser is working from something this repo no
        longer believes: a union that disagrees with what the server accepts,
        or a threshold compared against a number that moved."""
        assert module.OUTPUT.is_file(), "run: make web-types"
        assert module.OUTPUT.read_text(encoding="utf-8") == module.render(), (
            f"{module.OUTPUT.name} is stale — run: make web-types"
        )

    def test_both_land_where_the_front_end_vendors_from(self) -> None:
        """`contracts-sync` there copies this directory by path. A generator
        writing anywhere else produces a contract nobody receives."""
        for module in (gen_web_types, gen_web_ui_contract):
            assert module.OUTPUT.parent == CONTRACTS, f"{module.OUTPUT} is outside contracts/web/"


class TestTheWireFixturesAreVendorable:
    """The third artifact, written by ``test_web_wire_shapes.py`` rather than a
    generator — and the one most easily left behind, because nothing imports it
    from Python at all."""

    def test_the_fixtures_are_where_the_front_end_looks(self) -> None:
        fixtures = sorted((CONTRACTS / "fixtures").glob("*.json"))
        assert fixtures, "no wire fixtures — run: uv run pytest tests/unit/test_web_wire_shapes.py"

    def test_every_fixture_is_readable_json(self) -> None:
        """A half-written fixture would fail the other repo's typecheck with a
        parse error naming a file its authors have never edited."""
        for path in (CONTRACTS / "fixtures").glob("*.json"):
            json.loads(path.read_text(encoding="utf-8"))
