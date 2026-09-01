"""Guards for ``contracts/v1/connectors.json`` — the identities the desktop vendors.

The desktop draws a connector's mark from its key and falls through to a
two-letter monogram on a miss, silently. This file is what travels so its own
suite can assert every key has a real mark; staleness here would be invisible
from over there, so the freshness assertion lives on this side.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from yeaboi.connectors import registry
from yeaboi.connectors.spec import ACCENT_RE

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "v1" / "connectors.json"
_MODULE_PATH = ROOT / "scripts" / "gen_connectors_contract.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("gen_connectors_contract", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_connectors_contract"] = module
    spec.loader.exec_module(module)
    return module


gen = _load_generator()
DOCUMENT = json.loads(CONTRACT.read_text(encoding="utf-8"))
ROWS = DOCUMENT["connectors"]


def test_the_contract_is_not_stale():
    assert CONTRACT.read_text(encoding="utf-8") == gen.render(), (
        "contracts/v1/connectors.json is stale — run: make web-types"
    )


def test_it_names_every_connector_and_only_those():
    # Two-way, like every other registry check: a removed connector must not
    # leave a rule behind in the desktop's icon table either. Since schema 2
    # the legacy integrations ride too — the catalog shows the whole roster,
    # so the desktop needs a mark for the whole roster.
    expected = [c.key for c in registry.all_connectors()] + [c.key for c in registry.legacy_entries()]
    assert [row["key"] for row in ROWS] == expected


def test_every_row_carries_what_a_mark_needs():
    for row in ROWS:
        assert row["label"].strip(), f"{row['key']} has no label to fall back to"
        assert row["family"].strip(), f"{row['key']} has no family glyph to fall back to"
        assert row["glyph"].strip(), f"{row['key']} carries no emoji to fall back to"
        assert ACCENT_RE.match(row["accent"]), f"{row['key']} accent {row['accent']!r} is not rgb(r,g,b)"


def test_it_carries_no_presentation():
    # Identifiers, labels, one accent, one emoji and where configuring happens.
    # A payload carries text and numbers, never markup — an SVG path here would
    # put the desktop's icons in this repo.
    allowed = {"key", "label", "family", "family_label", "accent", "glyph", "managed_by"}
    for row in ROWS:
        assert set(row) == allowed, f"{row['key']} carries {sorted(set(row) - allowed)}"
        assert row["managed_by"] in ("connections", "credentials")
