#!/usr/bin/env python3
"""Generate the TypeScript mirror of the boards' server-validated enums.

Both boards validate incoming values against tuples in ``retro/board.py`` and
``poker/board.py`` — a card grid, a reaction emoji, an avatar, a theme name, a
deck value. Those same sets have to exist in the browser to render a picker, and
today they get there by string-substituting ``__AVATARS__`` into a JS template.

Once the boards are React, they arrive in the boot payload — but the *types*
still have to be written down somewhere, and a hand-written union is exactly the
thing that silently rots when someone adds an emoji. Generating it means a
literal union can never disagree with the tuple the server checks against.

Deliberately generates **only the enums**. State shapes are not generated: they
change more often, they carry semantics a codegen cannot express, and a wrong
generated interface is worse than an honest hand-written one. The drift guard
for state shapes is the JSON fixture + ``satisfies`` check described in the plan.

Usage::

    uv run python scripts/gen_web_types.py          # write the file
    uv run python scripts/gen_web_types.py --check  # fail if it is stale (CI)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from yeaboi.poker.board import POKER_DECK
from yeaboi.retro.board import (
    AVATARS,
    CARRIED_STATUS_LABELS,
    CARRIED_STATUSES,
    REACTION_EMOJIS,
    RETRO_GRID_LABELS,
    RETRO_GRIDS,
    RETRO_THEMES,
)

logger = logging.getLogger(__name__)

OUTPUT = Path(__file__).resolve().parent.parent / "frontend" / "src" / "types" / "enums.ts"

HEADER = """/*
 * GENERATED FILE — do not edit.
 *
 * Regenerate with `uv run python scripts/gen_web_types.py` after changing any of
 * the server-validated tuples in retro/board.py or poker/board.py. CI runs the
 * same script with --check and fails if this file is stale.
 *
 * Only the enums are generated. State shapes are hand-written in ./board.ts,
 * because they carry semantics a codegen cannot express — and a confidently
 * wrong generated interface is worse than an honest hand-written one.
 *
 * These are the sets the *server* validates against (a value from a LAN peer is
 * rejected unless it is in one of them), so a literal union that disagreed with
 * one would let the client offer something the board will always refuse.
 */
"""


def _tuple_const(name: str, values: Sequence[str], doc: str) -> str:
    """Emit `export const NAME = [...] as const` plus its element-type alias."""
    items = ", ".join(json.dumps(v, ensure_ascii=False) for v in values)
    type_name = "".join(part.capitalize() for part in name.lower().split("_"))
    return (
        f"/** {doc} */\nexport const {name} = [{items}] as const;\nexport type {type_name} = (typeof {name})[number];\n"
    )


def _label_map(name: str, key_type: str, labels: Mapping[str, str], doc: str) -> str:
    """Emit a `Record<KeyType, string>` of human-facing labels."""
    body = "".join(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},\n" for k, v in labels.items())
    return f"/** {doc} */\nexport const {name}: Record<{key_type}, string> = {{\n{body}}};\n"


def render() -> str:
    """Build the full contents of enums.ts."""
    blocks = [
        HEADER,
        _tuple_const("RETRO_GRIDS", RETRO_GRIDS, "The four retro columns, in display order."),
        _label_map("RETRO_GRID_LABELS", "RetroGrids", RETRO_GRID_LABELS, "Human-facing column headings."),
        _tuple_const("CARRIED_STATUSES", CARRIED_STATUSES, "Statuses a carried-over action item can be set to."),
        _label_map("CARRIED_STATUS_LABELS", "CarriedStatuses", CARRIED_STATUS_LABELS, "Carried-item status labels."),
        _tuple_const("RETRO_THEMES", RETRO_THEMES, "Palettes the host may broadcast. Mirrors palette.css."),
        _tuple_const("REACTION_EMOJIS", REACTION_EMOJIS, "The only emoji a card reaction may use."),
        _tuple_const("AVATARS", AVATARS, "Avatars a participant may choose."),
        _tuple_const("POKER_DECK", POKER_DECK, "Planning-poker card values, in deck order."),
    ]
    return "\n".join(blocks)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit non-zero if the file is stale")
    args = parser.parse_args(argv)

    generated = render()
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""

    if args.check:
        if current == generated:
            logger.info("gen_web_types: %s is up to date", OUTPUT.name)
            print(f"✓ {OUTPUT.relative_to(Path.cwd())} is up to date")
            return 0
        logger.warning("gen_web_types: %s is stale", OUTPUT.name)
        print(
            f"✗ {OUTPUT.name} is stale — a board enum changed.\n  Run: uv run python scripts/gen_web_types.py",
            file=sys.stderr,
        )
        return 1

    if current == generated:
        print(f"✓ {OUTPUT.name} unchanged")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generated, encoding="utf-8")
    logger.info("gen_web_types: wrote %s (%d bytes)", OUTPUT, len(generated))
    print(f"✓ wrote {OUTPUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
