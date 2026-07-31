"""Reporting presentation palettes — built-ins plus user-defined custom themes.

A palette is a name → role → hex-color map, and this module is the only place
they are written down: the deck ships them in its boot payload and the ``.pptx``
renderer reads them directly. (The deck used to carry a duplicate set as CSS
``[data-theme]`` blocks, which is what the drift guard in
``test_reporting_themes.py`` was for.) Users add their own palettes in
``~/.yeaboi/data/reporting_themes.json`` (path from ``paths.get_reporting_themes_path``)
and pick them anywhere a theme name is accepted: the TUI theme screen, the HTML
slide deck (T key cycles them too), the .pptx export, ``yeaboi report --theme``,
and the MCP tools.

JSON schema — top-level object, one entry per custom theme::

    {
      "corporate": {
        "bg1": "#101418",     ← deck background (darkest)
        "bg2": "#1c2733",     ← background gradient highlight
        "fg": "#eef3f8",      ← body text
        "muted": "#93a3b4",   ← secondary text
        "accent": "#2f81f7",  ← primary brand color
        "accent2": "#79b8ff"  ← bright variant (big numbers, gradients)
      }
    }

Colors must be ``#RRGGBB``. Missing roles fall back to the midnight value; a name
that shadows a built-in is skipped. Loading is tolerant — a malformed file logs a
warning and contributes nothing, it never crashes a report run.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# The color roles every palette defines. Six, and no more: the deck derives its
# panel, hairline and dim tiers from `fg` and `bg1` in CSS (frontend/src/deck/
# deck.css), so someone hand-writing a palette picks a handful of colours they
# can reason about rather than a dozen they cannot.
ROLE_KEYS = ("bg1", "bg2", "fg", "muted", "accent", "accent2")

# The canonical hexes. Both presentation surfaces read them from here.
BUILTIN_PALETTES: dict[str, dict[str, str]] = {
    "midnight": {
        "bg1": "#0d1117",
        "bg2": "#161b2e",
        "fg": "#e6edf3",
        "muted": "#9aa4b2",
        "accent": "#8c78e6",
        "accent2": "#b8a6ff",
    },
    "aurora": {
        "bg1": "#04121a",
        "bg2": "#0a2a2a",
        "fg": "#e8fff6",
        "muted": "#8fc9be",
        "accent": "#28c2a0",
        "accent2": "#6ff0d0",
    },
    "sunset": {
        "bg1": "#1a0d16",
        "bg2": "#3a1424",
        "fg": "#fff1e8",
        "muted": "#d9a08f",
        "accent": "#f0784e",
        "accent2": "#ffb27a",
    },
    "mono": {
        "bg1": "#0b0b0c",
        "bg2": "#1c1c1f",
        "fg": "#f4f4f5",
        "muted": "#a1a1aa",
        "accent": "#d4d4d8",
        "accent2": "#ffffff",
    },
}

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def load_custom_palettes() -> dict[str, dict[str, str]]:
    """Read the user's custom palettes; tolerant of every malformed shape.

    Returns {} when the file is absent/unreadable/invalid — never raises. Invalid
    entries (bad name, bad hex, shadowing a built-in) are skipped with a warning;
    missing roles are filled from the midnight palette so a partial theme still works.
    """
    from yeaboi.paths import get_reporting_themes_path

    path = get_reporting_themes_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("reporting themes: could not read %s: %s", path, e)
        return {}
    if not isinstance(raw, dict):
        logger.warning("reporting themes: %s must contain a JSON object of name → roles", path)
        return {}

    palettes: dict[str, dict[str, str]] = {}
    for name, roles in raw.items():
        name = str(name).strip().lower()
        if not _NAME_RE.match(name):
            logger.warning("reporting themes: skipping invalid theme name %r", name)
            continue
        if name in BUILTIN_PALETTES:
            logger.warning("reporting themes: %r shadows a built-in theme — skipped", name)
            continue
        if not isinstance(roles, dict):
            logger.warning("reporting themes: theme %r must map roles to #RRGGBB colors — skipped", name)
            continue
        palette = dict(BUILTIN_PALETTES["midnight"])
        ok = True
        for role, value in roles.items():
            if role not in ROLE_KEYS:
                logger.warning("reporting themes: %s: unknown role %r (ignored)", name, role)
                continue
            value = str(value).strip()
            if not _HEX_RE.match(value):
                logger.warning("reporting themes: %s.%s: %r is not a #RRGGBB color — theme skipped", name, role, value)
                ok = False
                break
            palette[role] = value.lower()
        if ok:
            palettes[name] = palette
    return palettes


def all_palettes() -> dict[str, dict[str, str]]:
    """Every selectable palette — built-ins first (stable order), then customs sorted by name."""
    palettes = {name: dict(roles) for name, roles in BUILTIN_PALETTES.items()}
    custom = load_custom_palettes()
    for name in sorted(custom):
        palettes[name] = custom[name]
    return palettes


def all_theme_names() -> tuple[str, ...]:
    """Every selectable theme name, built-ins first."""
    return tuple(all_palettes())


def is_valid_theme(name: str) -> bool:
    """True when ``name`` is a built-in or a currently-defined custom theme."""
    return name in BUILTIN_PALETTES or name in load_custom_palettes()


def get_palette(name: str) -> dict[str, str]:
    """The role → hex map for ``name``; unknown names fall back to midnight."""
    if name in BUILTIN_PALETTES:
        return dict(BUILTIN_PALETTES[name])
    custom = load_custom_palettes()
    if name in custom:
        return custom[name]
    return dict(BUILTIN_PALETTES["midnight"])
