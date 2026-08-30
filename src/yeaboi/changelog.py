"""Bundled changelog loader — reads the AI-written release notes shipped in the package.

The data lives in ``src/yeaboi/changelog_data.json`` (bundled automatically by
hatchling, same mechanism as ``performance/references/``). Entries are written
by the auto-version CI workflow at release time — there is no runtime LLM or
network call here. Each highlight is tagged with the feature areas it touches,
which the TUI colour-codes with the matching mode accents.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, replace
from importlib import resources

# A highlight with no surface tag applies to all three — backend/engine work is
# the common case. The vocabulary itself is shared with the tips registry.
from yeaboi.surfaces import ALL_SURFACES, VALID_SURFACES

logger = logging.getLogger(__name__)

_DATA_FILENAME = "changelog_data.json"

# Fixed area vocabulary — mirrors the mode cards ("agents" covers the whole
# Agents category). Anything else coerces to "general".
VALID_AREAS = frozenset(
    {"analysis", "planning", "standup", "retro", "performance", "reporting", "usage", "settings", "agents", "general"}
)

# One accent per area, matching each mode's colour in the mode-select grid so the
# changelog tags read as the same feature the user already knows by colour.
AREA_COLORS: dict[str, str] = {
    "analysis": "rgb(100,180,100)",
    "planning": "rgb(110,140,220)",
    "standup": "rgb(200,100,180)",
    "retro": "rgb(80,190,190)",
    "performance": "rgb(220,110,90)",
    "reporting": "rgb(140,120,230)",
    "usage": "rgb(220,160,60)",
    "settings": "rgb(160,160,180)",
    "agents": "rgb(90,160,210)",
    "general": "rgb(160,160,180)",
}


@dataclass(frozen=True)
class ChangelogHighlight:
    """One shipped change, tagged with the feature areas and surfaces it touches."""

    text: str = ""
    areas: tuple[str, ...] = ()
    surfaces: tuple[str, ...] = ALL_SURFACES


@dataclass(frozen=True)
class ChangelogEntry:
    """One released version's user-facing notes."""

    version: str = ""
    date: str = ""
    summary: str = ""
    highlights: tuple[ChangelogHighlight, ...] = ()


def _coerce_areas(raw: object) -> tuple[str, ...]:
    """Validate area tags; unknown or malformed tags become 'general'."""
    if not isinstance(raw, list):
        return ("general",)
    areas = []
    for area in raw:
        if isinstance(area, str) and area in VALID_AREAS:
            areas.append(area)
        else:
            areas.append("general")
    # Dedupe while preserving order
    return tuple(dict.fromkeys(areas)) or ("general",)


def _coerce_surfaces(raw: object) -> tuple[str, ...]:
    """Validate surface tags; missing, malformed, or empty means all surfaces."""
    if not isinstance(raw, list):
        return ALL_SURFACES
    surfaces = [s for s in raw if isinstance(s, str) and s in VALID_SURFACES]
    return tuple(dict.fromkeys(surfaces)) or ALL_SURFACES


def _parse_entry(raw: object) -> ChangelogEntry | None:
    """Parse one raw JSON entry; None (skipped) when malformed."""
    if not isinstance(raw, dict) or not isinstance(raw.get("version"), str) or not raw.get("version"):
        return None
    highlights = []
    for item in raw.get("highlights") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]:
            highlights.append(
                ChangelogHighlight(
                    text=item["text"],
                    areas=_coerce_areas(item.get("areas")),
                    surfaces=_coerce_surfaces(item.get("surfaces")),
                )
            )
    return ChangelogEntry(
        version=raw["version"],
        date=raw.get("date", "") if isinstance(raw.get("date"), str) else "",
        summary=raw.get("summary", "") if isinstance(raw.get("summary"), str) else "",
        highlights=tuple(highlights),
    )


def load_changelog() -> list[ChangelogEntry]:
    """Load the bundled changelog, newest-first. Gracefully [] on any problem."""
    try:
        raw_text = (resources.files("yeaboi") / _DATA_FILENAME).read_text(encoding="utf-8")
        data = json.loads(raw_text)
        raw_entries = data.get("entries", []) if isinstance(data, dict) else []
    except Exception:
        logger.warning("changelog data missing or unreadable", exc_info=True)
        return []

    entries = [entry for entry in (_parse_entry(raw) for raw in raw_entries) if entry is not None]
    logger.debug("changelog loaded: %d entries", len(entries))
    return entries


def filter_for_surface(entries: list[ChangelogEntry], surface: str) -> list[ChangelogEntry]:
    """Keep only the entries and highlights that apply to ``surface``.

    An entry with no highlights at all carries no surface information and is
    kept whole — absence means everywhere, same as an untagged highlight.
    """
    filtered = []
    for entry in entries:
        if not entry.highlights:
            filtered.append(entry)
            continue
        highlights = tuple(h for h in entry.highlights if surface in h.surfaces)
        if highlights:
            filtered.append(replace(entry, highlights=highlights))
    return filtered


def build_changelog_text(entries: list[ChangelogEntry] | None = None) -> str:
    """Render the changelog as a copy-pasteable Markdown report.

    Powers the Usage-style "Copy to clipboard" action on the Changelog page (it has
    no on-disk export). Loads the bundled changelog when ``entries`` is not supplied.
    """
    if entries is None:
        entries = load_changelog()
    if not entries:
        return "# yeaboi — Changelog\n\n(no changelog available)\n"

    lines: list[str] = ["# yeaboi — Changelog", ""]
    for e in entries:
        header = f"## {e.version}"
        if e.date:
            header += f" — {e.date}"
        lines.append(header)
        if e.summary:
            lines.append("")
            lines.append(e.summary)
        for h in e.highlights:
            lines.append(f"- {h.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
