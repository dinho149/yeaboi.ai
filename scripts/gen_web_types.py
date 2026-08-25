#!/usr/bin/env python3
"""Write the boards' server-validated enums to ``contracts/web/enums.json``.

Both boards validate incoming values against tuples in ``retro/board.py`` and
``poker/board.py`` — a card grid, a reaction emoji, an avatar, a theme name, a
deck value. Those same sets have to exist in the browser to render a picker, and
a hand-written union is exactly the thing that silently rots when someone adds
an emoji. Generating it means a literal union can never disagree with the tuple
the server checks against.

This half emits **data only**: names, values, and the sentence each one is
documented with. Turning that into TypeScript is the front end's job
(``frontend/scripts/gen-enums.mjs``), which is what lets ``frontend/`` be a repo
with no Python in it — it vendors this file and renders ``types/enums.ts`` from
its own copy. Type names, JSDoc layout and ``as const`` are TypeScript spelling,
so none of them appear here.

Deliberately covers **only the enums**. State shapes are not generated: they
change more often, they carry semantics a codegen cannot express, and a wrong
generated interface is worse than an honest hand-written one. Their drift guard
is the fixture + ``satisfies`` pair in ``tests/unit/test_web_wire_shapes.py``.

Usage::

    uv run python scripts/gen_web_types.py          # write the file
    uv run python scripts/gen_web_types.py --check  # fail if it is stale (CI)

``make web-types`` runs this and the TypeScript half together.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from yeaboi.artifacts.edits import EDIT_OPS
from yeaboi.performance.evidence import (
    COVERAGE_STATES,
    EVIDENCE_SOURCE_LABELS,
    EVIDENCE_SOURCES,
    STAT_UNITS,
)
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
from yeaboi.standup.collector import ALL_SOURCES, source_label
from yeaboi.ui.shared._ansi_font import SHADOW_GLYPHS, render_shadow_text
from yeaboi.ui.shared._ascii_font import BLOCK_GLYPHS, render_ascii_text

logger = logging.getLogger(__name__)

OUTPUT = Path(__file__).resolve().parent.parent / "contracts" / "web" / "enums.json"

#: Bumped when a *block kind* is added or its keys change — i.e. when the
#: TypeScript renderer on the other side needs a new branch to read this file.
#: Not a version of the values; those change constantly and are diffed, not
#: negotiated.
SCHEMA = 1


def _tuple_const(name: str, values: Sequence[str], doc: str) -> dict:
    """A set the server validates against, and the union drawn from it."""
    return {"kind": "tuple", "name": name, "doc": [doc], "values": list(values)}


def _pairs(mapping: Mapping[str, object]) -> list[list[object]]:
    """A mapping as ordered ``[key, value]`` pairs rather than a JSON object.

    JavaScript reorders integer-like keys of an object to the front, so
    ``BLOCK_GLYPHS`` would come back with its digits ahead of its letters. An
    array of pairs is the same data with the order actually preserved.
    """
    return [[key, value] for key, value in mapping.items()]


def _label_map(name: str, keys: str, labels: Mapping[str, str], doc: str) -> dict:
    """Human-facing labels, keyed by the elements of the ``keys`` tuple."""
    return {"kind": "labels", "name": name, "doc": [doc], "keys": keys, "labels": _pairs(labels)}


def _table(name: str, value: str, entries: Mapping[str, object], doc: str) -> dict:
    """A lookup table. ``value`` is the TypeScript type of one entry.

    The one place a TypeScript spelling does appear in this file, because it is
    the only thing about these tables a reader of the data cannot infer: whether
    a two-element array is a fixed pair or a list, and whether null is allowed,
    are decisions rather than observations.
    """
    return {"kind": "table", "name": name, "doc": doc.split("\n"), "value": value, "entries": _pairs(entries)}


def _block_glyphs() -> dict:
    """The two-line block font, as a lookup table.

    Generated rather than hand-copied for the same reason as the enums: this is
    the product's display typeface, it is drawn character-by-character on both
    sides, and a hand-maintained copy would let a letter the terminal renders
    quietly render as a blank gap on the web.
    """
    return _table(
        "BLOCK_GLYPHS",
        "readonly [string, string]",
        BLOCK_GLYPHS,
        "The two-line block font, one entry per character: `[top, bottom]`.\n"
        "\n"
        "Mirrors `ui/shared/_ascii_font.py`, which is what the TUI sets every mode\n"
        "title in. Rendered by `<Wordmark>`; characters absent here become gaps,\n"
        "exactly as `render_ascii_text()` does.",
    )


# Words chosen to exercise every branch of the renderer, not to look nice:
# two real mode names, the brand, digits, an inter-word space (which has its own
# glyph), and a character with no glyph at all (the three-space gap fallback).
_WORDMARK_SAMPLES = ("retro", "poker", "yeaboi", "sprint 42", "n/a")


def _wordmark_samples() -> dict:
    """Python's own `render_ascii_text` output for a few words.

    The glyph table above is generated, so it cannot drift — but the *renderer*
    is a dozen lines duplicated in `render_ascii_text()` and `renderWordmark()`,
    and two implementations that each look correct in isolation is precisely how
    the join-code wire format ended up broken with both sides' tests green.

    So the expected values are produced by Python and asserted by TypeScript.
    There is one artifact, it is generated, and `--check` keeps it fresh; a
    renderer that diverges fails `Wordmark.test.tsx`, not a code review.
    """
    return _table(
        "WORDMARK_SAMPLES",
        "readonly [string, string]",
        {w: render_ascii_text(w) for w in _WORDMARK_SAMPLES},
        "`render_ascii_text()` output, straight from Python.\n"
        "\n"
        "Asserted by `Wordmark.test.tsx` so the TS renderer cannot drift from the\n"
        "terminal's. Not for runtime use.",
    )


def _shadow_glyphs() -> dict:
    """The six-row ANSI Shadow font, as a lookup table.

    Same reasoning as ``_block_glyphs``, with more at stake: this face is the one
    a teammate meets on the join gate, and it exists in Python only because
    ``tests/unit/test_ansi_font.py`` proves the table against 22 independently
    generated wordmarks. Copying it by hand into TypeScript would put a second,
    unproven copy on the surface that matters most.
    """
    return _table(
        "SHADOW_GLYPHS",
        "readonly string[]",
        SHADOW_GLYPHS,
        "The six-row ANSI Shadow font, one entry per character.\n"
        "\n"
        "Mirrors `ui/shared/_ansi_font.py`. Covers A-Z and space only; a word\n"
        "containing anything else has no setting in this face and the caller\n"
        "falls back to BLOCK_GLYPHS, which is why the renderer returns null\n"
        "rather than substituting a gap.",
    )


# Chosen to exercise the renderer, not to look nice: the brand (no pair of its
# letters kerns, so it is the pure-concatenation case), a mode name, the word
# whose L+Y nest by three columns, a two-word string with the un-kernable space,
# and a word with a digit — which has no setting at all and must come back null.
_SHADOW_SAMPLES = ("yeaboi", "retro", "analysis", "team retro", "sprint 42")


def _shadow_samples() -> dict:
    """Python's own ``render_shadow_text`` output for a few words.

    The kerning rule is the reason this exists. The glyph table is generated so it
    cannot drift, but ``_fit()`` is reimplemented in TypeScript, and a fitting bug
    is close to invisible: it reproduces most words exactly and quietly widens the
    handful whose letters nest. Pinning Python's output means that shows up in
    ``Wordmark.test.tsx`` rather than in a screenshot months later.
    """
    return _table(
        "SHADOW_SAMPLES",
        "readonly string[] | null",
        {w: render_shadow_text(w) for w in _SHADOW_SAMPLES},
        "`render_shadow_text()` output, straight from Python. `null` means the\n"
        "face cannot set that word. Not for runtime use.",
    )


def blocks() -> list[dict]:
    """Every declaration the browser gets, in emission order."""
    return [
        _tuple_const("RETRO_GRIDS", RETRO_GRIDS, "The four retro columns, in display order."),
        _label_map("RETRO_GRID_LABELS", "RETRO_GRIDS", RETRO_GRID_LABELS, "Human-facing column headings."),
        _tuple_const("CARRIED_STATUSES", CARRIED_STATUSES, "Statuses a carried-over action item can be set to."),
        _label_map("CARRIED_STATUS_LABELS", "CARRIED_STATUSES", CARRIED_STATUS_LABELS, "Carried-item status labels."),
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
        _tuple_const("ACTIVITY_SOURCES", ALL_SOURCES, "Every activity source a standup can collect from."),
        _label_map(
            "ACTIVITY_SOURCE_LABELS",
            "ACTIVITY_SOURCES",
            {source: source_label(source) for source in ALL_SOURCES},
            "How a source is named to a user. Generated so the report, the progress "
            'steps and the exports agree — "azdo_repos".title() reads as "Azdo Repos", '
            "which looks like a different source from the one the steps just named.",
        ),
        _tuple_const(
            "COVERAGE_STATES",
            COVERAGE_STATES,
            "What one evidence source contributed. Generated because two modes draw the "
            "same dot from the same word — standup's category coverage and performance's "
            "per-source coverage — and a hand-written copy in one of them would drift "
            "with nothing to notice.",
        ),
        _tuple_const(
            "EVIDENCE_SOURCES",
            EVIDENCE_SOURCES,
            "Every source a performance artifact can be grounded in.",
        ),
        _label_map(
            "EVIDENCE_SOURCE_LABELS",
            "EVIDENCE_SOURCES",
            EVIDENCE_SOURCE_LABELS,
            "How a source is named to a reader. Generated so the Markdown, the TUI and "
            "the export cannot call one source three different things.",
        ),
        _tuple_const(
            "STAT_UNITS",
            STAT_UNITS,
            "What a measured number IS — a bare count, a percentage, points, or days. Never how to draw it.",
        ),
        _block_glyphs(),
        _wordmark_samples(),
        _shadow_glyphs(),
        _shadow_samples(),
    ]


def render() -> str:
    """Build the full contents of ``contracts/web/enums.json``."""
    document = {
        "$schema_version": SCHEMA,
        "$generated_by": "scripts/gen_web_types.py",
        "blocks": blocks(),
    }
    # Indented and trailing-newline'd so a diff of it is readable: this file is
    # reviewed like source even though nobody writes it.
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit non-zero if the file is stale")
    args = parser.parse_args(argv)

    generated = render()
    current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""

    if args.check:
        if current == generated:
            logger.info("gen_web_types: %s is up to date", OUTPUT.name)
            print(f"✓ {OUTPUT.name} is up to date")
            return 0
        logger.warning("gen_web_types: %s is stale", OUTPUT.name)
        print(
            f"✗ {OUTPUT.name} is stale — a board enum changed.\n  Run: make web-types",
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
