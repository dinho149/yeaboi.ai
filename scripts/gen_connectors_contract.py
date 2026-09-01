#!/usr/bin/env python3
"""Write the connector identities the desktop is checked against.

``contracts/web/ui.json`` already carries this list for ``yeaboi-frontend``.
The desktop vendors ``contracts/v1/`` instead, and had no equivalent — which is
how a connector reached ``provider-icon.tsx`` with no mark and fell through to a
two-letter monogram, silently.

So the identities travel: this writes them here, the desktop vendors the
directory by sha, and its own suite asserts every key resolves to a real mark.
The check lands on the side that owns the icons, exactly as
``routes_manifest.json`` puts the route check on the side that owns the routes.

Identifiers, labels and one accent per connector — no markup, no presentation
beyond the accent the descriptor already declares.

Usage::

    uv run python scripts/gen_connectors_contract.py          # write the file
    uv run python scripts/gen_connectors_contract.py --check  # fail if stale (CI)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from yeaboi.connectors import registry
from yeaboi.connectors.spec import FAMILY_LABELS

logger = logging.getLogger(__name__)

SCHEMA = 3
OUTPUT = Path(__file__).resolve().parents[1] / "contracts" / "v1" / "connectors.json"


def render() -> str:
    """Build the full contents of ``contracts/v1/connectors.json``."""
    document = {
        "$schema_version": SCHEMA,
        "$generated_by": "scripts/gen_connectors_contract.py",
        # Every key needs a mark the desktop can draw — a real logomark, or a
        # deliberate family fallback. A monogram is what "we forgot" looks like.
        # Since schema 2 the legacy integrations ride too: the catalog shows the
        # whole roster, so the whole roster needs marks. ``managed_by`` says
        # whether a connect form belongs here ("connections") or deep-links to
        # Credentials/setup ("credentials").
        "connectors": [
            {
                "key": c.key,
                "label": c.label,
                "family": c.family,
                "family_label": FAMILY_LABELS.get(c.family, c.family.title()),
                "accent": c.accent,
                # Since schema 3: the emoji a surface may fall back to when it
                # has no logomark for the key.
                "glyph": c.mark,
                "managed_by": managed_by,
            }
            for connectors, managed_by in (
                # builtin_connectors, deliberately: a vendored contract must not
                # change with whatever custom connections this machine holds.
                (registry.builtin_connectors(), "connections"),
                (registry.legacy_entries(), "credentials"),
            )
            for c in connectors
        ],
    }
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit non-zero if the file is stale")
    args = parser.parse_args(argv)

    generated = render()
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""

    if args.check:
        if current == generated:
            logger.info("gen_connectors_contract: %s is up to date", OUTPUT.name)
            print(f"✓ {OUTPUT.name} is up to date")
            return 0
        logger.warning("gen_connectors_contract: %s is stale", OUTPUT.name)
        print(f"✗ {OUTPUT.name} is stale — run: make web-types", file=sys.stderr)
        return 1

    if current == generated:
        print(f"✓ {OUTPUT.name} unchanged")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated, encoding="utf-8")
    logger.info("gen_connectors_contract: wrote %s (%d bytes)", OUTPUT, len(generated))
    print(f"✓ wrote {OUTPUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
