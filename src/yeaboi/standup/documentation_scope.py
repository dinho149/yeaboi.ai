"""Saved/override selection for Standup documentation providers."""

from __future__ import annotations

DOCUMENTATION_SOURCES = ("confluence", "notion")


def validate_documentation_sources(sources: list[str] | tuple[str, ...]) -> list[str]:
    invalid = [source for source in sources if source not in DOCUMENTATION_SOURCES]
    if invalid:
        raise ValueError(f"unknown documentation source(s) {invalid} — valid: {', '.join(DOCUMENTATION_SOURCES)}")
    return list(dict.fromkeys(sources))


def default_documentation_sources(*, confluence_space: str, notion_root: str) -> list[str]:
    selected: list[str] = []
    if confluence_space:
        selected.append("confluence")
    if notion_root:
        selected.append("notion")
    return selected
