#!/usr/bin/env python3
"""Write the Python-side facts the front end is checked against.

Two guards used to live in this repo's test suite and read TypeScript and CSS
out of ``frontend/``. With the front end in its own repo they cannot, and
deleting them would be the worst outcome of the split: both catch a failure
that is silent at runtime.

* **Accent modes.** Every mode ``web/brand.py`` names an accent for must have a
  ``[data-mode="…"]`` block in ``design/tokens.css``. One that does not simply
  renders the base accent — a page that looks fine and is wearing the wrong
  colour.
* **The refused-hold throttle.** ``sharing/live.py`` delays a refused poll by
  ``REFUSED_HOLD_SLEEP``; the browser's ``MIN_PARKED_MS`` has to sit above that
  or the client half of the throttle is dead code the server has already
  outrun.

So the values travel instead: this writes them to ``contracts/web/ui.json``,
the front end vendors it, and its own tests make the assertions against its own
files. The check ends up on the side that owns what is being checked, which is
where it belongs — and neither half can quietly stop being made.

Usage::

    uv run python scripts/gen_web_ui_contract.py          # write the file
    uv run python scripts/gen_web_ui_contract.py --check  # fail if stale (CI)

``make web-types`` runs this and the enums generator together.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from yeaboi.sharing.live import REFUSED_HOLD_SLEEP
from yeaboi.web.brand import MODE_LABELS, MODE_WORDMARKS, accent_mode

logger = logging.getLogger(__name__)

OUTPUT = Path(__file__).resolve().parent.parent / "contracts" / "web" / "ui.json"

#: Bumped when a *key* is added or changes meaning — not when a value moves.
SCHEMA = 2


def _accents() -> list[str]:
    """Every accent any named mode resolves to.

    Drawn from the modes rather than from ``_ACCENT_MODES`` directly, so a mode
    that borrows another's accent is covered by the same one entry and a mode
    that resolves to nothing is caught here rather than in the front end.
    """
    accents = set()
    for mode in set(MODE_LABELS) | set(MODE_WORDMARKS):
        accent = accent_mode(mode)
        if not accent:
            raise ValueError(f"{mode!r} resolves to no accent — brand.py and this contract disagree")
        accents.add(accent)
    return sorted(accents)


def _connector_accents() -> list[str]:
    """Every connector key the front end must own an identity for.

    Identifiers, never colours: the page's mark and accent live in the front
    end's own files, and this list is what its tests assert against — so a
    connector cannot reach the browser unstyled, and a removed one cannot leave
    a rule behind.
    """
    from yeaboi.connectors import registry

    return sorted(registry.accents())


def render() -> str:
    """Build the full contents of ``contracts/web/ui.json``."""
    document = {
        "$schema_version": SCHEMA,
        "$generated_by": "scripts/gen_web_ui_contract.py",
        # Each needs a `[data-mode="<accent>"]` block in design/tokens.css.
        "accent_modes": _accents(),
        # Each needs a `[data-connector="<key>"]` block and an icon in the
        # front end's connector set.
        "connector_accents": _connector_accents(),
        "timing": {
            # The server already delays a refused poll this long, so the
            # browser's parked-request threshold must exceed it.
            "refused_hold_sleep_ms": int(REFUSED_HOLD_SLEEP * 1000),
        },
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
            logger.info("gen_web_ui_contract: %s is up to date", OUTPUT.name)
            print(f"✓ {OUTPUT.name} is up to date")
            return 0
        logger.warning("gen_web_ui_contract: %s is stale", OUTPUT.name)
        print(f"✗ {OUTPUT.name} is stale — run: make web-types", file=sys.stderr)
        return 1

    if current == generated:
        print(f"✓ {OUTPUT.name} unchanged")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated, encoding="utf-8")
    logger.info("gen_web_ui_contract: wrote %s (%d bytes)", OUTPUT, len(generated))
    print(f"✓ wrote {OUTPUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
