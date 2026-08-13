"""The repository inventory analysis persists for other modes to read.

Analysis walks every repo in the configured GitHub owners and Azure DevOps
projects on each run, and until now threw the result away — so nothing could
later answer "what does this repo do" without walking the estate again.

This module owns two things and nothing else: the **stable key** a repository
is known by across runs, and the **row shape** that gets persisted. Both the
producer (``analysis.ai_usage``) and the consumer (``agent.prior_art``) import
from here, because a key format duplicated in two places is a key format that
drifts, and a drifted key means a repository the user already rejected gets
offered to them again.

Deliberately a pure leaf: no I/O, no config, no imports from either side.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The key this inventory rides under inside a team profile's examples blob.
INVENTORY_KEY = "repository_inventory"

# Ceiling on persisted rows. A large estate would otherwise inflate every
# profile's examples_json without bound. Truncation is reported, never silent.
MAX_INVENTORY_ROWS = 300

# Fields kept per repository. `paths` is deliberately absent: a recursive tree
# is thousands of strings per repo, and the consumer fetches trees only for the
# handful of candidates that survive ranking.
_ROW_FIELDS = (
    "key",
    "provider",
    "container",
    "name",
    "url",
    "default_branch",
    "updated_at",
    "archived",
    "active",
    "skip_reason",
    "description",
    "languages",
)


def repo_key(provider: str, container: str, name: str) -> str:
    """Stable cross-run handle for one repository.

    ``github:acme/platform-auth`` / ``azdo:Payments/checkout-api``. Built from
    identity only — never a display label, never a URL (which carries a branch
    and a host that both change). GitHub rows already carry ``owner/repo`` in
    ``name``; Azure rows carry a bare repo name and the project in
    ``container``, so the slug is joined only when it isn't already qualified.
    """
    provider = (provider or "").strip().lower()
    container = (container or "").strip()
    name = (name or "").strip()
    slug = name if "/" in name else f"{container}/{name}" if container else name
    return f"{provider}:{slug}".strip().lower()


def normalise(rows: list[dict] | tuple[dict, ...] | None) -> list[dict]:
    """Reduce raw provider inventory rows to the persisted shape.

    Drops the discovery-error sentinels (they name an owner, not a repository)
    and anything without a resolvable key. Sorted most-recently-pushed first so
    a truncated list keeps the repos most likely to matter. Never raises: a
    malformed row is skipped, because a bad inventory must not fail an
    analysis run that has already done all its real work.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for row in rows or []:
        try:
            if not isinstance(row, dict) or row.get("discovery_error"):
                continue
            provider = str(row.get("provider", "") or "")
            container = str(row.get("container", "") or "")
            name = str(row.get("name", "") or "")
            if not provider or not name:
                continue
            key = repo_key(provider, container, name)
            if not key or key in seen:
                continue
            seen.add(key)
            languages = [str(lang) for lang in (row.get("languages") or []) if str(lang).strip()]
            out.append(
                {
                    "key": key,
                    "provider": provider,
                    "container": container,
                    "name": name,
                    "url": str(row.get("url", "") or ""),
                    "default_branch": str(row.get("default_branch", "") or ""),
                    "updated_at": str(row.get("updated_at", "") or ""),
                    "archived": bool(row.get("archived", False)),
                    "active": bool(row.get("active", False)),
                    "skip_reason": str(row.get("skip_reason", "") or ""),
                    "description": str(row.get("description", "") or "").strip(),
                    "languages": languages,
                }
            )
        except Exception:  # pragma: no cover — one bad row must not cost the rest
            logger.debug("repo_inventory: skipping malformed row", exc_info=True)
    # ISO-8601 sorts lexicographically, so a plain string sort is a date sort.
    out.sort(key=lambda entry: entry.get("updated_at", ""), reverse=True)
    if len(out) > MAX_INVENTORY_ROWS:
        logger.info(
            "repo_inventory: keeping %d of %d repositories (most recently pushed first)",
            MAX_INVENTORY_ROWS,
            len(out),
        )
        out = out[:MAX_INVENTORY_ROWS]
    return out
