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

from yeaboi.artifacts.edits import EDIT_OPS
from yeaboi.poker.board import DUEL_STATUSES, POKER_DECK, POKER_PHASES
from yeaboi.retro.board import (
    AVATARS,
    CARRIED_STATUS_LABELS,
    CARRIED_STATUSES,
    REACTION_EMOJIS,
    RETRO_GRID_LABELS,
    RETRO_GRIDS,
    RETRO_THEMES,
)
from yeaboi.ui.shared._ansi_font import SHADOW_GLYPHS, render_shadow_text
from yeaboi.ui.shared._ascii_font import BLOCK_GLYPHS, render_ascii_text

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
 * These are the sets the *server* validates against (a value from a participant
 * is rejected unless it is in one of them), so a literal union that disagreed
 * with one would let the client offer something the board will always refuse.
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


def _block_glyphs() -> str:
    """Emit the two-line block font as a TS lookup table.

    Generated rather than hand-copied for the same reason as the enums: this is
    the product's display typeface, it is drawn character-by-character on both
    sides, and a hand-maintained copy would let a letter the terminal renders
    quietly render as a blank gap on the web.
    """
    rows = "".join(
        f"  {json.dumps(ch)}: {json.dumps(lines, ensure_ascii=False)},\n" for ch, lines in BLOCK_GLYPHS.items()
    )
    return (
        "/**\n"
        " * The two-line block font, one entry per character: `[top, bottom]`.\n"
        " *\n"
        " * Mirrors `ui/shared/_ascii_font.py`, which is what the TUI sets every mode\n"
        " * title in. Rendered by `<Wordmark>`; characters absent here become gaps,\n"
        " * exactly as `render_ascii_text()` does.\n"
        " */\n"
        f"export const BLOCK_GLYPHS: Record<string, readonly [string, string]> = {{\n{rows}}};\n"
    )


# Words chosen to exercise every branch of the renderer, not to look nice:
# two real mode names, the brand, digits, an inter-word space (which has its own
# glyph), and a character with no glyph at all (the three-space gap fallback).
_WORDMARK_SAMPLES = ("retro", "poker", "yeaboi", "sprint 42", "n/a")


def _wordmark_samples() -> str:
    """Emit Python's own `render_ascii_text` output for a few words.

    The glyph table above is generated, so it cannot drift — but the *renderer*
    is a dozen lines duplicated in `render_ascii_text()` and `renderWordmark()`,
    and two implementations that each look correct in isolation is precisely how
    the join-code wire format ended up broken with both sides' tests green.

    So the expected values are produced by Python and asserted by TypeScript.
    There is one artifact, it is generated, and `--check` keeps it fresh; a
    renderer that diverges fails `Wordmark.test.tsx`, not a code review.
    """
    rows = "".join(
        f"  {json.dumps(w)}: {json.dumps(render_ascii_text(w), ensure_ascii=False)},\n" for w in _WORDMARK_SAMPLES
    )
    return (
        "/**\n"
        " * `render_ascii_text()` output, straight from Python.\n"
        " *\n"
        " * Asserted by `Wordmark.test.tsx` so the TS renderer cannot drift from the\n"
        " * terminal's. Not for runtime use.\n"
        " */\n"
        f"export const WORDMARK_SAMPLES: Record<string, readonly [string, string]> = {{\n{rows}}};\n"
    )


def _shadow_glyphs() -> str:
    """Emit the six-row ANSI Shadow font as a TS lookup table.

    Same reasoning as ``_block_glyphs``, with more at stake: this face is the one
    a teammate meets on the join gate, and it exists in Python only because
    ``tests/unit/test_ansi_font.py`` proves the table against 22 independently
    generated wordmarks. Copying it by hand into TypeScript would put a second,
    unproven copy on the surface that matters most.
    """
    rows = "".join(
        f"  {json.dumps(ch)}: {json.dumps(lines, ensure_ascii=False)},\n" for ch, lines in SHADOW_GLYPHS.items()
    )
    return (
        "/**\n"
        " * The six-row ANSI Shadow font, one entry per character.\n"
        " *\n"
        " * Mirrors `ui/shared/_ansi_font.py`. Covers A-Z and space only; a word\n"
        " * containing anything else has no setting in this face and the caller\n"
        " * falls back to BLOCK_GLYPHS, which is why the renderer returns null\n"
        " * rather than substituting a gap.\n"
        " */\n"
        f"export const SHADOW_GLYPHS: Record<string, readonly string[]> = {{\n{rows}}};\n"
    )


# Chosen to exercise the renderer, not to look nice: the brand (no pair of its
# letters kerns, so it is the pure-concatenation case), a mode name, the word
# whose L+Y nest by three columns, a two-word string with the un-kernable space,
# and a word with a digit — which has no setting at all and must come back null.
_SHADOW_SAMPLES = ("yeaboi", "retro", "analysis", "team retro", "sprint 42")


def _shadow_samples() -> str:
    """Emit Python's own ``render_shadow_text`` output for a few words.

    The kerning rule is the reason this exists. The glyph table is generated so it
    cannot drift, but ``_fit()`` is reimplemented in TypeScript, and a fitting bug
    is close to invisible: it reproduces most words exactly and quietly widens the
    handful whose letters nest. Pinning Python's output means that shows up in
    ``Wordmark.test.tsx`` rather than in a screenshot months later.
    """
    rows = "".join(
        f"  {json.dumps(w)}: {json.dumps(render_shadow_text(w), ensure_ascii=False)},\n" for w in _SHADOW_SAMPLES
    )
    return (
        "/**\n"
        " * `render_shadow_text()` output, straight from Python. `null` means the\n"
        " * face cannot set that word. Not for runtime use.\n"
        " */\n"
        f"export const SHADOW_SAMPLES: Record<string, readonly string[] | null> = {{\n{rows}}};\n"
    )


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
        _tuple_const(
            "POKER_PHASES", POKER_PHASES, "Where a ticket's round is: voting \u2192 revealed, optionally via a duel."
        ),
        _tuple_const(
            "DUEL_STATUSES",
            DUEL_STATUSES,
            "The open floor's lifecycle. The browser renders a different panel for each.",
        ),
        _tuple_const(
            "EDIT_OPS",
            EDIT_OPS,
            "What one correction does to a shared artifact. Server-validated, so it is "
            "generated rather than shipped in a boot payload — a payload would win at "
            "runtime and let a stale bundle offer an op the server rejects.",
        ),
        _block_glyphs(),
        _wordmark_samples(),
        _shadow_glyphs(),
        _shadow_samples(),
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
