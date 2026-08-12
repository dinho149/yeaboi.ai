"""Activity classification and coverage helpers for structured standup updates."""

from __future__ import annotations

from pathlib import PurePosixPath

from yeaboi.standup import collector

CATEGORY_TICKETING = "ticketing"
CATEGORY_CODE = "code"
CATEGORY_DOCUMENTATION = "documentation"
CATEGORIES = (CATEGORY_TICKETING, CATEGORY_CODE, CATEGORY_DOCUMENTATION)

COVERED = "covered"
PARTIAL = "partial"
FAILED = "failed"
NOT_CONFIGURED = "not_configured"

_DOC_EXTENSIONS = {".md", ".mdx", ".rst", ".adoc", ".asciidoc"}
_DOC_DIRECTORIES = {"docs", "documentation", "wiki"}
_DOC_FILENAMES = {"readme", "changelog", "contributing", "authors", "license"}


def is_documentation_path(path: str) -> bool:
    """Return whether a repository path conventionally represents documentation."""
    normalized = str(path or "").replace("\\", "/").strip("/")
    if not normalized:
        return False
    parsed = PurePosixPath(normalized)
    lowered_parts = tuple(part.lower() for part in parsed.parts)
    if any(part in _DOC_DIRECTORIES for part in lowered_parts[:-1]):
        return True
    stem = parsed.stem.lower()
    return parsed.suffix.lower() in _DOC_EXTENSIONS or (not parsed.suffix and stem in _DOC_FILENAMES)


def documentation_paths(item: dict) -> tuple[str, ...]:
    """Return the documentation paths attached to a repository activity event."""
    paths = item.get("changed_files") or ()
    return tuple(str(path) for path in paths if is_documentation_path(str(path)))


def is_repository_activity(item: dict) -> bool:
    return item.get("source") in {
        collector.SOURCE_GITHUB,
        collector.SOURCE_AZDO_REPOS,
        collector.SOURCE_LOCAL_GIT,
    } or item.get("kind") in {"commit", "pr", "review"}


def is_documentation_activity(item: dict) -> bool:
    if item.get("source") in {collector.SOURCE_CONFLUENCE, collector.SOURCE_NOTION}:
        return True
    return bool(documentation_paths(item))


def is_ticketing_activity(item: dict) -> bool:
    return item.get("source") in {collector.SOURCE_JIRA, collector.SOURCE_AZDO}


def is_code_activity(item: dict) -> bool:
    """Code includes repository events unless all known changed files are documentation."""
    if not is_repository_activity(item):
        return False
    paths = tuple(str(path) for path in (item.get("changed_files") or ()) if path)
    return not paths or any(not is_documentation_path(path) for path in paths)


def split_activity(items: list[dict]) -> dict[str, list[dict]]:
    """Split items into category-specific evidence lists.

    Mixed repository changes intentionally appear in both code and documentation.
    """
    return {
        CATEGORY_TICKETING: [item for item in items if is_ticketing_activity(item)],
        CATEGORY_CODE: [item for item in items if is_code_activity(item)],
        CATEGORY_DOCUMENTATION: [item for item in items if is_documentation_activity(item)],
    }


def category_sources(category: str, enabled_sources: set[str]) -> set[str]:
    """Return enabled collectors that can supply one output category."""
    if category == CATEGORY_TICKETING:
        return enabled_sources & {collector.SOURCE_JIRA, collector.SOURCE_AZDO}
    if category == CATEGORY_CODE:
        return enabled_sources & {
            collector.SOURCE_GITHUB,
            collector.SOURCE_AZDO_REPOS,
            collector.SOURCE_LOCAL_GIT,
        }
    if category == CATEGORY_DOCUMENTATION:
        # Repository collectors are documentation sources too when changed paths
        # identify docs files.
        return enabled_sources & {
            collector.SOURCE_CONFLUENCE,
            collector.SOURCE_NOTION,
            collector.SOURCE_GITHUB,
            collector.SOURCE_AZDO_REPOS,
        }
    raise ValueError(f"unknown standup category: {category}")


def coverage_states(enabled_sources: set[str], bundle: collector.ActivityBundle) -> tuple[tuple[str, str], ...]:
    """Compute report-wide coverage for ticketing, code, and documentation."""
    completed = {source for source, _count in bundle.counts}
    failed_sources = {source for source, _message in bundle.errors}
    partial_sources = {source for source, _message in bundle.partial_sources}
    if collector.SOURCE_AZDO in failed_sources:
        failed_sources.add(collector.SOURCE_AZDO_REPOS)
    states: list[tuple[str, str]] = []
    for category in CATEGORIES:
        expected = category_sources(category, enabled_sources)
        if not expected:
            state = NOT_CONFIGURED
        elif expected & completed:
            state = PARTIAL if expected & (failed_sources | partial_sources) else COVERED
        else:
            state = FAILED if expected & failed_sources else PARTIAL
        states.append((category, state))
    return tuple(states)


def empty_summary(category: str, coverage: str) -> str:
    """Return the explicit empty-state sentence for a category."""
    label = {"ticketing": "Ticketing", "code": "Code", "documentation": "Documentation"}[category]
    if coverage == NOT_CONFIGURED:
        return f"{label} sources not configured."
    if coverage == FAILED:
        return f"{label} activity unavailable because the selected sources failed."
    if coverage == PARTIAL:
        return f"No {category} activity detected from the sources that were successfully scanned; coverage was partial."
    if category == CATEGORY_CODE:
        return "No code activity detected in the selected repositories."
    return f"No {category} activity detected in the selected sources."


def is_empty_state(text: str) -> bool:
    """Whether ``text`` is one of the canonical *droppable* empty-state sentences.

    Exporters use this to drop per-member "No X activity detected…" footnotes:
    coverage is a report-wide fact the Details section already states once, and
    repeating it on every card buried the members who did something. Exact
    string match on purpose — bespoke prose ("Nothing merged, two reviews
    pending") must never be classified as sayable-by-a-machine and dropped.

    The FAILED sentence is deliberately NOT droppable: "activity unavailable
    because the selected sources failed" means *we could not look*, and a
    member folded into a "No activity detected" strip on a day Jira 401'd
    would be a positive claim about a named person that nobody verified.
    """
    stripped = (text or "").strip()
    return any(
        stripped == empty_summary(category, coverage)
        for category in CATEGORIES
        for coverage in (COVERED, PARTIAL, NOT_CONFIGURED)
    )
