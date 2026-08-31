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
import re
import time
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
    headline: str = ""
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


def _first_sentence(text: str) -> str:
    """The text up to its first sentence end — the headline fallback for an old entry."""
    head = re.split(r"(?<=[.!?])\s", text.strip(), maxsplit=1)[0].strip()
    return head.rstrip(".")


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
    summary = raw.get("summary", "") if isinstance(raw.get("summary"), str) else ""
    headline = raw.get("headline", "") if isinstance(raw.get("headline"), str) else ""
    return ChangelogEntry(
        version=raw["version"],
        date=raw.get("date", "") if isinstance(raw.get("date"), str) else "",
        # An entry written before headlines existed still needs a card title.
        headline=headline or _first_sentence(summary),
        summary=summary,
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


def read_seen_version() -> str:
    """The newest release the user has already read, or "" when they never have."""
    from yeaboi.paths import get_changelog_seen_path

    try:
        raw = json.loads(get_changelog_seen_path().read_text(encoding="utf-8"))
        version = raw.get("version", "") if isinstance(raw, dict) else ""
        return version if isinstance(version, str) else ""
    except FileNotFoundError:
        return ""  # never opened the page — the ordinary first run, not a failure
    except Exception:
        logger.warning("changelog seen-version unreadable", exc_info=True)
        return ""


def write_seen_version(version: str) -> None:
    """Record ``version`` as read. Best-effort — a failure only costs a repeat digest."""
    from yeaboi.paths import get_changelog_seen_path

    if not version:
        return
    try:
        get_changelog_seen_path().write_text(json.dumps({"version": version, "at": time.time()}), encoding="utf-8")
        logger.debug("changelog seen-version written: %s", version)
    except Exception:
        logger.warning("changelog seen-version not written", exc_info=True)


def entries_since(entries: list[ChangelogEntry], version: str) -> list[ChangelogEntry]:
    """The entries newer than ``version``, newest-first.

    Empty when ``version`` is missing or unparseable — a first run has nothing to
    catch up on, and an unreadable marker must never claim the whole ledger is new.
    """
    from yeaboi.update_check import parse_version

    seen = parse_version(version)
    if seen is None:
        return []
    newer = []
    for entry in entries:
        current = parse_version(entry.version)
        if current is not None and current > seen:
            newer.append(entry)
    return newer


def changelog_areas(entries: list[ChangelogEntry]) -> list[str]:
    """The area tags present in ``entries``, in the mode grid's own order."""
    present = {area for entry in entries for hl in entry.highlights for area in hl.areas}
    return [area for area in AREA_COLORS if area in present]


def filter_by_area(entries: list[ChangelogEntry], area: str) -> list[ChangelogEntry]:
    """Keep only the entries and highlights carrying ``area``; empty area keeps all."""
    if not area:
        return entries
    kept = []
    for entry in entries:
        highlights = tuple(hl for hl in entry.highlights if area in hl.areas)
        if highlights:
            kept.append(replace(entry, highlights=highlights))
    return kept


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
        if e.headline:
            lines.append("")
            lines.append(f"**{e.headline}**")
        if e.summary:
            lines.append("")
            lines.append(e.summary)
        for h in e.highlights:
            lines.append(f"- {h.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
