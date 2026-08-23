"""Apply an anonymize replacement map to a mode's *native* data — in place.

The anonymize engine (``engine.run_anonymize``) returns an ``AnonymizedOutput`` whose
``replacements`` field is the ``(original -> placeholder)`` set it masked. The TUI used
to throw away every mode's card UI and show that engine output as a raw-Markdown review
screen; instead we now re-render each mode's *own* screen with only the sensitive words
swapped. This module is the seam: it takes that replacement map and applies it to the
two shapes a result screen renders from —

  * a frozen artifact (StandupReport / RetroReport / RoadmapAnalysis / TeamProfile) —
    ``mask_artifact`` walks ``asdict(artifact)``, masks every string leaf, and rebuilds
    the dataclass via the mode's existing ``_dict_to_*`` reconstructor; the native
    screen builder is fed the masked artifact and renders exactly as before.
  * a pre-rendered ``list[str]`` (performance / reporting ``detail_lines``, planning
    ``content_lines``) — ``mask_lines`` maps the masker over each line.

Everything is pure/deterministic and LLM-free (the LLM already ran in the engine), so it
lives here as headless, unit-tested logic rather than inside the TUI.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import asdict

Replacements = Sequence[tuple[str, str]]


# ---------------------------------------------------------------------------
# Core text masker (generalises engine._apply_seed_mask)
# ---------------------------------------------------------------------------


def apply_replacements(text: str, replacements: Replacements) -> str:
    """Literal-replace each ``original`` with its ``placeholder`` in ``text``.

    Mirrors the engine's seed masker (``engine._apply_seed_mask``): case-insensitive
    with word-ish boundaries (``(?<!\\w)…(?!\\w)`` — also fires around the dots/hyphens
    in hostnames and issue keys where ``\\b`` would not), and **longest original first**
    so ``"Acme Payments"`` is masked before the substring ``"Acme"``. Safe on any string
    — used for on-screen artifact fields, plaintext lines, and the exported Markdown, so
    what you see and what you export are masked identically.
    """
    if not text or not replacements:
        return text
    for original, placeholder in sorted(replacements, key=lambda p: len(p[0]), reverse=True):
        if not original:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(original)}(?!\w)", re.IGNORECASE)
        text = pattern.subn(placeholder, text)[0]
    return text


def masked_note(result) -> str:
    """The line a surface shows while it is displaying masked data.

    Empty for ``None`` (real data), so a screen renders exactly as it would
    without a mask; a count-carrying line otherwise. The "review before sharing"
    half is the point — a mask is a starting position, not a guarantee.
    """
    if result is None:
        return ""
    return f"Anonymized · {len(result.replacements)} masked — review before sharing"


def mask_lines(lines: Sequence[str], replacements: Replacements) -> list[str]:
    """Mask every line of a pre-rendered ``detail_lines`` / ``content_lines`` list."""
    if not replacements:
        return list(lines)
    return [apply_replacements(line, replacements) for line in lines]


def mask_obj(value, replacements: Replacements):
    """Deep-mask an arbitrary JSON-like structure (dict / list / str leaves).

    For side data a screen renders alongside its artifact — e.g. the analysis screen's
    ``examples`` dict of sample stories — where masking every string leaf keeps the
    on-screen samples consistent with the masked artifact. Dict *keys* and non-strings
    are left untouched.
    """
    if not replacements:
        return value
    return _deep_mask(value, replacements)


# ---------------------------------------------------------------------------
# Frozen-artifact masker
# ---------------------------------------------------------------------------


def _deep_mask(value, replacements: Replacements):
    """Recursively mask every string leaf of an ``asdict`` tree, preserving shape.

    ``asdict`` yields dict / list / str / number / None (enums pass through as-is), so we
    only recurse those containers and mask ``str`` leaves; everything else is returned
    unchanged. The per-mode reconstructor rebuilds the tuples-of-dataclasses afterwards.
    """
    if isinstance(value, str):
        return apply_replacements(value, replacements)
    if isinstance(value, dict):
        return {k: _deep_mask(v, replacements) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_deep_mask(v, replacements) for v in value)
    return value


def _reconstructor_for(cls):
    """Return the ``dict -> dataclass`` rebuilder for a known result artifact, or None.

    Delegates to :func:`yeaboi.artifacts.registry.reconstructor_for`, which owns
    the mapping. It used to be spelled out here, and being a second copy is how
    it went wrong: the performance artifacts were never added, so masking a 1:1
    prep or a six-month review returned it **unmasked** — the one path where a
    silent no-op is worst, since the caller's whole reason for asking was that
    they were about to publish it.
    """
    from yeaboi.artifacts.registry import reconstructor_for

    return reconstructor_for(cls)


def mask_artifact(artifact, replacements: Replacements):
    """Return a copy of ``artifact`` with every string field masked.

    ``asdict`` → deep-mask string leaves → the mode's own reconstructor. Unknown artifact
    types (no registered reconstructor) are returned unmasked rather than raising, so a
    new mode never crashes the anonymize path before it's wired in.
    """
    if not replacements:
        return artifact
    reconstruct = _reconstructor_for(type(artifact))
    if reconstruct is None:
        return artifact
    return reconstruct(_deep_mask(asdict(artifact), replacements))
