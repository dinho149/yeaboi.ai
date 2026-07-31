"""Reporting presentation style — persisted deck/pptx customization beyond the palette.

Where themes.py answers "what colors", this module answers everything else about how
the exported HTML deck and .pptx look: title/heading color overrides, font family and
size, layout density (one slide per outcome theme vs themes grouped as cards), and
which optional sections appear. The style is persisted in
``~/.yeaboi/data/reporting_prefs.json`` (path from ``paths.get_reporting_prefs_path``)
and edited from the Reporting page's Style screen; the MCP ``reporting_export`` tool
accepts per-call overrides.

JSON schema — a ``deck_style`` envelope (a flat dict is also accepted)::

    {
      "deck_style": {
        "title_color": "",              ← palette role, "#RRGGBB", or "" = theme default
        "heading_color": "accent2",
        "font_family": "modern",        ← modern | classic | mono | rounded
        "font_scale": "normal",         ← compact | normal | large
        "layout": "detailed",           ← detailed (slide per theme) | compact (theme cards)
        "content_fit": "ask",           ← ask | expand (add slides, keep all bullets) | tight (fixed grid, may trim)
        "max_bullets": 6,
        "include_items_table": true,    ← .pptx delivered-items appendix
        "include_signals": true,        ← supporting-signals corroboration footnote
        "include_highlights": true,
        "include_thanks": true,
        "slide_numbers": false,
        "footer_text": ""
      }
    }

Loading is tolerant — unknown keys are ignored and every invalid value falls back to
its default, so a hand-edited file (or an untrusted MCP ``style`` dict) never crashes
an export. The default ``DeckStyle()`` reproduces the historical deck output exactly.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from dataclasses import dataclass

from yeaboi.reporting.themes import _HEX_RE

logger = logging.getLogger(__name__)

# Font presets: one label for the TUI, a real typeface name for python-pptx, and the
# matching CSS stack for the HTML deck.
#
# Every preset names a design token rather than repeating a stack, so a deck
# picks up the same faces as every other yeaboi page — including the fact that
# those stacks name yeaboi.ai's own Geist / JetBrains Mono first, which a
# hand-copied list here would not.
#
# "classic" and "rounded" are still departures from the *body* face; they are
# just declared in the design layer (`--font-serif`, `--font-rounded` in
# tokens.css) instead of here. A preset is a user-facing choice, so neither was
# collapsed into "modern" — that would have made two shipped options silently
# identical to a third.
#
# The `pptx` column is untouched by any of this: PowerPoint has no custom
# properties, so it needs a real typeface name, and a deck and the .pptx built
# from the same report still agree about which face was chosen.
FONT_PRESETS: dict[str, dict[str, str]] = {
    "modern": {
        "label": "Modern",
        "pptx": "Calibri",
        "css": "var(--font-sans)",
    },
    "classic": {
        "label": "Classic serif",
        "pptx": "Georgia",
        "css": "var(--font-serif)",
    },
    "mono": {
        "label": "Mono",
        "pptx": "Consolas",
        "css": "var(--font-mono)",
    },
    "rounded": {
        "label": "Rounded",
        "pptx": "Trebuchet MS",
        "css": "var(--font-rounded)",
    },
}

FONT_SCALES: dict[str, float] = {"compact": 0.85, "normal": 1.0, "large": 1.15}

LAYOUTS = ("detailed", "compact")

# How overflowing content is handled: "expand" adds slides so nothing is trimmed
# (max_bullets becomes a page size), "tight" keeps the fixed grid and trims with
# "… and N more", and "ask" lets the TUI offer the extra slides at export time —
# non-interactive surfaces (engine/CLI/MCP) resolve "ask" to "expand".
CONTENT_FITS = ("ask", "expand", "tight")
CONTENT_FIT_LABELS = {"ask": "ask at export", "expand": "add slides", "tight": "fixed"}

# Palette roles a color override may name (background roles excluded — text on bg1
# would be invisible).
COLOR_ROLES = ("accent", "accent2", "fg", "muted")

# Values the TUI Space-cycles through; free values are clamped to 2..10.
MAX_BULLET_CHOICES = (3, 4, 5, 6, 8, 10)
_BULLETS_MIN, _BULLETS_MAX = 2, 10

_FOOTER_MAX = 120


@dataclass(frozen=True)
class DeckStyle:
    """Presentation style for the HTML deck + .pptx. Defaults == historical output."""

    title_color: str = ""  # palette role | "#RRGGBB" | "" = theme default
    heading_color: str = ""  # palette role | "#RRGGBB" | "" = theme default
    font_family: str = "modern"  # FONT_PRESETS key
    font_scale: str = "normal"  # FONT_SCALES key
    layout: str = "detailed"  # "detailed" (slide per theme) | "compact" (cards)
    content_fit: str = "ask"  # CONTENT_FITS: "ask" | "expand" | "tight"
    max_bullets: int = 6  # per slide (detailed) / per card (compact); page size when expanding
    include_items_table: bool = True  # .pptx delivered-items appendix
    include_signals: bool = True  # supporting-signals corroboration footnote
    include_highlights: bool = True
    include_thanks: bool = True
    slide_numbers: bool = False
    footer_text: str = ""  # "" = no custom footer


DEFAULT_STYLE = DeckStyle()

# Ordered UI spec — the single source of truth the Style screen and its runner share.
# (field, label, kind) with kind ∈ {"color", "choice", "bool", "int", "text"}.
STYLE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("title_color", "Title color", "color"),
    ("heading_color", "Heading color", "color"),
    ("font_family", "Font", "choice"),
    ("font_scale", "Text size", "choice"),
    ("layout", "Layout", "choice"),
    ("content_fit", "Content fit", "choice"),
    ("max_bullets", "Max bullets", "int"),
    ("include_items_table", "Delivered-items appendix", "bool"),
    ("include_signals", "Supporting-signals footnote", "bool"),
    ("include_highlights", "Highlights slide", "bool"),
    ("include_thanks", "Thank-you slide", "bool"),
    ("slide_numbers", "Slide numbers", "bool"),
    ("footer_text", "Footer text", "text"),
)


def _clean_color(value: object) -> str:
    """A palette role name or lowercased #RRGGBB hex; anything else → "" (theme default)."""
    text = str(value).strip()
    if text in COLOR_ROLES:
        return text
    if _HEX_RE.match(text):
        return text.lower()
    return ""


def style_to_dict(style: DeckStyle) -> dict:
    """The JSON-ready dict form of ``style`` (inverse of ``style_from_dict``)."""
    return dataclasses.asdict(style)


def style_from_dict(raw: object) -> DeckStyle:
    """Build a DeckStyle from an untrusted dict, tolerating every malformed shape.

    Unknown keys are ignored; each invalid value falls back to that field's default.
    Never raises — MCP callers and hand-edited prefs files both land here.
    """
    if not isinstance(raw, dict):
        return DEFAULT_STYLE
    d = DEFAULT_STYLE
    try:
        bullets = int(raw.get("max_bullets", d.max_bullets))
    except (TypeError, ValueError):
        bullets = d.max_bullets
    font = str(raw.get("font_family", d.font_family)).strip().lower()
    scale = str(raw.get("font_scale", d.font_scale)).strip().lower()
    layout = str(raw.get("layout", d.layout)).strip().lower()
    fit = str(raw.get("content_fit", d.content_fit)).strip().lower()
    return DeckStyle(
        title_color=_clean_color(raw.get("title_color", d.title_color)),
        heading_color=_clean_color(raw.get("heading_color", d.heading_color)),
        font_family=font if font in FONT_PRESETS else d.font_family,
        font_scale=scale if scale in FONT_SCALES else d.font_scale,
        layout=layout if layout in LAYOUTS else d.layout,
        content_fit=fit if fit in CONTENT_FITS else d.content_fit,
        max_bullets=max(_BULLETS_MIN, min(_BULLETS_MAX, bullets)),
        include_items_table=bool(raw.get("include_items_table", d.include_items_table)),
        include_signals=bool(raw.get("include_signals", d.include_signals)),
        include_highlights=bool(raw.get("include_highlights", d.include_highlights)),
        include_thanks=bool(raw.get("include_thanks", d.include_thanks)),
        slide_numbers=bool(raw.get("slide_numbers", d.slide_numbers)),
        footer_text=str(raw.get("footer_text", d.footer_text)).strip()[:_FOOTER_MAX],
    )


def resolve_color(value: str, palette: dict[str, str], default: str) -> str:
    """Resolve a style color ("" | role | hex) to a concrete hex against ``palette``."""
    if not value:
        return default
    if value in palette:
        return palette[value]
    if _HEX_RE.match(value):
        return value.lower()
    return default


def cap_items(items, n: int) -> list[str]:
    """At most ``n`` items, appending an "… and N more" overflow marker when trimmed."""
    items = [str(item) for item in items]
    if len(items) <= n:
        return items
    return items[:n] + [f"… and {len(items) - n} more"]


# A sentence fragment shorter than this merges into its neighbour — protects
# abbreviation splits ("e.g.", "Q3.") from becoming their own slide bullet.
_MIN_POINT_CHARS = 40


def summary_points(text: str, *, max_points: int = 6) -> list[str]:
    """Split a summary paragraph into sentence-level points for slide rendering.

    The executive summary arrives as one prose paragraph; as a single text blob it
    is unreadable on a slide. Splitting on sentence boundaries gives both deck
    renderers scannable points. Tiny fragments merge into the previous point, and
    anything past ``max_points`` merges into the last one — content is never dropped.
    """
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]
    points: list[str] = []
    for sentence in sentences:
        if points and len(sentence) < _MIN_POINT_CHARS:
            points[-1] = f"{points[-1]} {sentence}"
        else:
            points.append(sentence)
    if len(points) > max_points:
        points[max_points - 1 :] = [" ".join(points[max_points - 1 :])]
    return points


def load_deck_style() -> DeckStyle:
    """Read the persisted deck style; missing/unreadable/invalid → defaults, never raises."""
    from yeaboi.paths import get_reporting_prefs_path

    path = get_reporting_prefs_path()
    if not path.exists():
        return DEFAULT_STYLE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("reporting style: could not read %s: %s", path, e)
        return DEFAULT_STYLE
    if isinstance(raw, dict) and isinstance(raw.get("deck_style"), dict):
        raw = raw["deck_style"]
    return style_from_dict(raw)


def save_deck_style(style: DeckStyle) -> None:
    """Persist ``style`` to the prefs file; a write failure logs a warning, never raises."""
    from yeaboi.paths import get_reporting_prefs_path

    path = get_reporting_prefs_path()
    try:
        path.write_text(json.dumps({"deck_style": style_to_dict(style)}, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("reporting style: could not write %s: %s", path, e)
        return
    logger.info("reporting style: saved preferences to %s", path)


def style_summary(style: DeckStyle) -> str:
    """One short line for the picker status row: "default" or the deviations."""
    if style == DEFAULT_STYLE:
        return "default"
    parts: list[str] = []
    if style.title_color:
        parts.append(f"title {style.title_color}")
    if style.heading_color:
        parts.append(f"headings {style.heading_color}")
    if style.font_family != DEFAULT_STYLE.font_family:
        parts.append(FONT_PRESETS[style.font_family]["label"].lower())
    if style.font_scale != DEFAULT_STYLE.font_scale:
        parts.append(style.font_scale)
    if style.layout != DEFAULT_STYLE.layout:
        parts.append(f"{style.layout} layout")
    if style.content_fit != DEFAULT_STYLE.content_fit:
        parts.append(f"fit {style.content_fit}")
    if style.max_bullets != DEFAULT_STYLE.max_bullets:
        parts.append(f"≤{style.max_bullets} bullets")
    if not style.include_items_table:
        parts.append("no appendix")
    if not style.include_signals:
        parts.append("no signals")
    if not style.include_highlights:
        parts.append("no highlights")
    if not style.include_thanks:
        parts.append("no thanks")
    if style.slide_numbers:
        parts.append("numbered")
    if style.footer_text:
        parts.append("footer")
    return " · ".join(parts) if parts else "default"
