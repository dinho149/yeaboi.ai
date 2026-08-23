"""Resolve one engineer to every handle they work under.

Performance used to match activity with a bare lowercase name equality, so an
engineer whose commits are authored under an email or a VCS login was invisible
to their own 1:1 prep. Standup already solved this: ``_normalize_author`` plus a
two-pass name → email → local-part closure over the observed items.

This reuses those helpers rather than growing a second, subtly different matcher
(``standup/aggregate.py`` already imports them cross-module, so the underscore is
soft). One rule is NOT inherited: ``_build_alias_map``'s ``my_name`` branch folds
in the *local machine's* git config, which belongs to the lead running the tool,
never to the engineer being reviewed — so ``my_name`` is deliberately never passed.

Matching stays conservative — exact normalized strings only, no fuzzy or
substring matching — because a wrong attribution puts someone else's work in a
performance review.

# See docs: "Daily Standup" — identity closure, activity attribution
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)


def normalize(name: str) -> frozenset[str]:
    """The normalized alias strings for one raw name/email. Never raises."""
    from yeaboi.standup.engine import _normalize_author

    return frozenset(_normalize_author(name))


def resolve_aliases(
    engineer: str,
    *,
    items: Iterable[Mapping] = (),
    extra: Iterable[str] = (),
) -> frozenset[str]:
    """Every handle ``engineer`` is known by, closed over the emails seen on ``items``.

    ``items`` are raw activity dicts (the collector shape); any that carry an
    ``author_email`` alongside a name already matching the engineer contribute
    that email — and its local part — as further aliases.
    """
    if not (engineer or "").strip():
        return frozenset()

    from yeaboi.standup.engine import _build_alias_map, _enrich_aliases_from_items

    # No ``my_name``: that branch would attach the lead's git identity to the
    # engineer being reviewed (see module docstring).
    alias_map = _build_alias_map([engineer])
    for handle in extra:
        alias_map[engineer] |= set(normalize(handle))

    rows = [dict(item) for item in items]
    if rows:
        _enrich_aliases_from_items(alias_map, rows)

    aliases = frozenset(alias_map.get(engineer, set()))
    logger.debug("performance identity: %s → %d alias(es)", engineer, len(aliases))
    return aliases


def roster_handles(engineer: str, *, jira_project: str = "", azdo_project: str = "", db_path=None) -> tuple[str, ...]:
    """The tracker identity + email the roster holds for ``engineer``.

    Best-effort and cache-backed (``team_roster``'s 15-minute TTL): a person's
    tracker email is usually the handle their commits and wiki edits are authored
    under, so seeding it is what connects those to them. Returns () on any
    failure — no credentials, no network, no cache is simply less matching.
    """
    try:
        from yeaboi.performance.roster import fetch_roster

        for member in fetch_roster(jira_project=jira_project, azdo_project=azdo_project, db_path=db_path):
            if normalize(member.name) & normalize(engineer):
                return tuple(h for h in (member.external_id, member.email) if h)
    except Exception:  # noqa: BLE001 — alias seeding is best-effort
        logger.debug("performance identity: roster lookup failed (non-fatal)", exc_info=True)
    return ()


def matches(name: str, aliases: frozenset[str]) -> bool:
    """Whether ``name`` identifies the engineer whose alias set this is."""
    return bool(aliases) and bool(normalize(name) & aliases)
