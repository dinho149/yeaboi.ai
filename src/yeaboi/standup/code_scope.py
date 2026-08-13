"""Repository discovery and validation for Daily Standup code updates."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SOURCE_GITHUB = "github"
SOURCE_AZDO = "azure_devops"
CODE_SOURCES = (SOURCE_GITHUB, SOURCE_AZDO)


def validate_code_sources(sources: list[str] | tuple[str, ...] | None) -> list[str]:
    """Return stable provider keys, rejecting unknown values."""
    selected = list(dict.fromkeys(sources or ()))
    bad = [source for source in selected if source not in CODE_SOURCES]
    if bad:
        raise ValueError(f"unknown standup code source(s) {bad} — valid: {', '.join(CODE_SOURCES)}")
    return [source for source in CODE_SOURCES if source in selected]


def default_code_scope() -> tuple[list[str], list[str], list[str]]:
    """Backward-compatible first-run scope from the legacy environment config.

    GitHub is enabled by a bare ``GITHUB_TOKEN`` and not only by the legacy
    ``STANDUP_GITHUB_REPO``: a token with no repo used to mean "no code activity
    at all", which is indistinguishable from a quiet day. The *scope* behind that
    source is left empty here on purpose — this function is called on every
    engine and TUI path and must stay network-free, so owner discovery happens
    once, later, in ``engine._resolve_code_scope``.
    """
    from yeaboi.config import get_azure_devops_project, get_github_token, get_standup_github_repo

    github = [get_standup_github_repo().strip()] if get_standup_github_repo().strip() else []
    azdo_project = (get_azure_devops_project() or "").strip()
    sources = ([SOURCE_GITHUB] if github or get_github_token() else []) + ([SOURCE_AZDO] if azdo_project else [])
    return sources, github, ([azdo_project] if azdo_project else [])


def discover_github_repositories(limit: int = 200) -> list[str]:
    """List repositories visible to the configured GitHub identity."""
    from yeaboi.config import get_standup_github_repo

    repos: set[str] = set()
    try:
        from yeaboi.tools.github import _get_github_client, _take

        client = _get_github_client()
        for repo in _take(client.get_user().get_repos(sort="full_name"), limit):
            slug = (getattr(repo, "full_name", "") or "").strip()
            if slug:
                repos.add(slug)
    except Exception as exc:
        logger.warning("standup: GitHub repository discovery failed: %s", exc)
    legacy = get_standup_github_repo().strip()
    if legacy:
        from yeaboi.tools.github import _parse_repo

        repos.add(_parse_repo(legacy))
    return sorted(repos, key=str.lower)


# Repos are matched to an owner by ``pushed_at``, which does not move for a day
# of reviews or issue triage. Discovery therefore looks back further than the
# standup window it is serving; the report's own ``since`` still decides which
# items are shown, so the only effect is that a repo active last week stays in
# scope for today's review comments instead of vanishing.
_OWNER_ACTIVITY_DAYS = 14

# Caps for the owner fan-out, mirroring azure_devops._MAX_ACTIVITY_REPOS: the
# collector issues 3 API calls per repository, so an owner is exactly the kind of
# container that can stall a standup — a 500-repo org would be 1500 sequential
# calls on the critical path of a run whose whole lead time is 10 minutes. The
# per-owner cap keeps one busy org from starving the others; the total is the
# ceiling on the run. Both truncations are reported, never silent.
_MAX_REPOS_PER_OWNER = 10
_MAX_REPOS_TOTAL = 30

# The repo-exclude picker needs the FULL candidate set inside an owner, not just
# what would survive this run's activity/cap filters — a repo excluded during a
# quiet month, or one that has never been pushed to, must still be excludable.
# Only archived repositories are dropped (see list_owner_repositories below);
# this window only bounds how far back github_analysis_inventory looks, which
# the picker otherwise ignores.
_PICKER_LOOKBACK_DAYS = 3650

# The picker shows far more than any one run would ever scan (see
# expand_github_owners's tighter caps above), but it still walks the whole
# paginated repo list per owner, so an unbounded org turns "open the picker"
# into a long spinner and an unusable single-column list. These are generous
# on purpose — a repo just outside them is still excludable via
# STANDUP_GITHUB_REPO / config edit, only not from this screen.
_MAX_PICKER_REPOS_PER_OWNER = 200
_MAX_PICKER_REPOS_TOTAL = 500


def discover_github_owners(limit: int = 100) -> list[str]:
    """List the GitHub owners/organisations visible to the configured token.

    The standup analog of :func:`discover_azdo_projects` — a picked owner stands
    for *all* of its repositories, the same way a picked Azure project stands for
    all of the repos inside it. Delegates to ``github_list_owners``, which already
    unions the authenticated login, the user's organisations, and the owners of
    visible repositories so a fine-grained PAT that cannot list orgs still fills
    the picker.
    """
    try:
        from yeaboi.tools.github import github_list_owners

        return github_list_owners(limit=limit)
    except Exception as exc:
        logger.warning("standup: GitHub owner discovery failed: %s", exc)
        return []


def expand_github_owners(
    owners: list[str] | tuple[str, ...] | None,
    *,
    days: int,
    excluded: list[str] | tuple[str, ...] | None = None,
) -> tuple[list[str], list[str]]:
    """Resolve picked owners to the repositories worth scanning.

    Returns ``(repository_slugs, warnings)``. Expansion happens per run rather
    than at configure time, which is the whole point of picking an owner: a repo
    created this morning is covered by tonight's standup with nothing to re-tick.

    Archived and never-pushed repositories are dropped by
    ``github_analysis_inventory``; an owner whose listing failed outright comes
    back as a ``discovery_error`` entry, which becomes a warning rather than a
    repository — that entry carries the *owner* name, so treating it as a repo
    would send the collector looking for a repository called "acme".

    ``excluded`` (owner/repo slugs, case-insensitive) is dropped BEFORE the caps
    below are applied, so excluding a noisy repo frees its slot for another repo
    in the same owner rather than wasting it.

    The result is capped (see the constants above) and ordered most-recently-
    pushed first, so what survives a cap is the work most likely to be in today's
    standup rather than whatever GitHub happened to list first.
    """
    selected = [str(owner).strip() for owner in (owners or ()) if str(owner).strip()]
    if not selected:
        return [], []
    excluded_slugs = {str(repo).strip().lower() for repo in (excluded or ()) if str(repo).strip()}
    try:
        from yeaboi.tools.github import github_analysis_inventory

        inventory = github_analysis_inventory(selected, days=max(int(days), _OWNER_ACTIVITY_DAYS), include_trees=False)
    except Exception as exc:
        logger.warning("standup: GitHub owner expansion failed: %s", exc)
        return [], [f"GitHub repository discovery failed for {', '.join(selected)}: {exc}"]

    warnings: list[str] = []
    by_owner: dict[str, list[tuple[str, str]]] = {}
    excluded_count = 0
    for entry in inventory:
        if entry.get("discovery_error"):
            owner = str(entry.get("container") or entry.get("name") or "").strip()
            detail = str(entry.get("error") or "repository discovery failed").strip()
            warnings.append(f"GitHub owner {owner}: {detail}" if owner else f"GitHub: {detail}")
            continue
        if not entry.get("active"):
            continue
        slug = str(entry.get("name") or "").strip()
        if not slug:
            continue
        if slug.lower() in excluded_slugs:
            excluded_count += 1
            continue
        owner = str(entry.get("container") or slug.partition("/")[0]).strip()
        by_owner.setdefault(owner, []).append((str(entry.get("updated_at") or ""), slug))

    repositories: dict[str, str] = {}
    truncated: list[str] = []
    total_dropped = 0
    for owner in selected:
        found = by_owner.get(owner, [])
        # ISO-8601 sorts lexicographically; a repo with no recorded push sorts last.
        found.sort(key=lambda pair: pair[0], reverse=True)
        kept = 0
        for _pushed, slug in found:
            if kept >= _MAX_REPOS_PER_OWNER or len(repositories) >= _MAX_REPOS_TOTAL:
                total_dropped += 1
                continue
            if repositories.setdefault(slug.lower(), slug) == slug:
                kept += 1
        if kept < len(found):
            truncated.append(f"{owner} ({kept} of {len(found)})")
    if truncated:
        warnings.append(
            "GitHub coverage was capped at the "
            f"{_MAX_REPOS_PER_OWNER} most recently pushed repositories per owner "
            f"(max {_MAX_REPOS_TOTAL} in total) — {total_dropped} skipped: {', '.join(truncated)}"
        )
    logger.info(
        "standup: expanded %d GitHub owner(s) to %d active repository(ies) (%d dropped by cap, %d excluded)",
        len(selected),
        len(repositories),
        total_dropped,
        excluded_count,
    )
    return list(repositories.values()), warnings


def list_owner_repositories(
    owners: list[str] | tuple[str, ...] | None, *, days: int = _PICKER_LOOKBACK_DAYS
) -> tuple[dict[str, list[str]], list[str]]:
    """List every non-archived repository inside the given owners, for the repo-exclude picker.

    Unlike :func:`expand_github_owners` (which filters to a run's activity
    window and applies the scan caps), this surfaces the full candidate set —
    a repo should stay excludable even in a quiet week, and the picker must
    show everything that COULD be scanned, not just what would fit this run's
    budget. Only archived repositories and outright discovery failures are
    dropped.

    Returns ``(owner -> sorted repository slugs, warnings)``.
    """
    selected = [str(owner).strip() for owner in (owners or ()) if str(owner).strip()]
    if not selected:
        return {}, []
    try:
        from yeaboi.tools.github import github_analysis_inventory

        inventory = github_analysis_inventory(selected, days=max(1, int(days)), include_trees=False)
    except Exception as exc:
        logger.warning("standup: GitHub repository listing failed: %s", exc)
        return {}, [f"GitHub repository discovery failed for {', '.join(selected)}: {exc}"]

    warnings: list[str] = []
    by_owner: dict[str, list[str]] = {}
    for entry in inventory:
        if entry.get("discovery_error"):
            owner = str(entry.get("container") or entry.get("name") or "").strip()
            detail = str(entry.get("error") or "repository discovery failed").strip()
            warnings.append(f"GitHub owner {owner}: {detail}" if owner else f"GitHub: {detail}")
            continue
        if entry.get("skip_reason") == "archived repository":
            continue
        slug = str(entry.get("name") or "").strip()
        if not slug:
            continue
        owner = str(entry.get("container") or slug.partition("/")[0]).strip()
        by_owner.setdefault(owner, []).append(slug)

    truncated: list[str] = []
    total_dropped = 0
    total_kept = 0
    for owner, slugs in by_owner.items():
        ordered = sorted(dict.fromkeys(slugs), key=str.lower)
        kept: list[str] = []
        for slug in ordered:
            if len(kept) >= _MAX_PICKER_REPOS_PER_OWNER or total_kept >= _MAX_PICKER_REPOS_TOTAL:
                total_dropped += 1
                continue
            kept.append(slug)
            total_kept += 1
        if len(kept) < len(ordered):
            truncated.append(f"{owner} ({len(kept)} of {len(ordered)})")
        by_owner[owner] = kept
    if truncated:
        warnings.append(
            "GitHub repository listing was capped at "
            f"{_MAX_PICKER_REPOS_PER_OWNER} repositories per owner (max {_MAX_PICKER_REPOS_TOTAL} in "
            f"total) — {total_dropped} not shown: {', '.join(truncated)}"
        )
    return by_owner, warnings


def discover_azdo_projects(limit: int = 200) -> list[str]:
    """List Azure DevOps projects visible in the configured organization."""
    from yeaboi.config import get_azure_devops_org_url, get_azure_devops_project, get_azure_devops_token

    org_url = get_azure_devops_org_url()
    if not org_url:
        return []
    project_names: set[str] = set()
    try:
        from yeaboi.tools.azure_devops import _make_connection, _pin_client_base_url

        connection = _make_connection(org_url, get_azure_devops_token())
        core = _pin_client_base_url(connection.clients.get_core_client(), org_url)
        projects = list(core.get_projects() or [])
        preferred = (get_azure_devops_project() or "").strip().lower()
        projects.sort(
            key=lambda p: (
                str(getattr(p, "name", "")).lower() != preferred,
                str(getattr(p, "name", "")).lower(),
            )
        )
        for project in projects:
            project_name = (getattr(project, "name", "") or "").strip()
            if project_name:
                project_names.add(project_name)
                if len(project_names) >= limit:
                    break
    except Exception as exc:
        logger.warning("standup: Azure project discovery failed: %s", exc)
    return sorted(project_names, key=str.lower)


def discover_azdo_repositories(limit: int = 200) -> list[str]:
    """Legacy repository discovery retained for API compatibility."""
    from yeaboi.config import get_azure_devops_org_url, get_azure_devops_token

    org_url = get_azure_devops_org_url()
    if not org_url:
        return []
    repos: set[str] = set()
    try:
        from yeaboi.tools.azure_devops import _make_connection, _pin_client_base_url

        connection = _make_connection(org_url, get_azure_devops_token())
        git = _pin_client_base_url(connection.clients.get_git_client(), org_url)
        for project_name in discover_azdo_projects(limit=limit):
            for repo in git.get_repositories(project_name) or []:
                repo_name = (getattr(repo, "name", "") or "").strip()
                if repo_name:
                    repos.add(f"{project_name}/{repo_name}")
                    if len(repos) >= limit:
                        return sorted(repos, key=str.lower)
    except Exception as exc:
        logger.warning("standup: Azure repository discovery failed: %s", exc)
    return sorted(repos, key=str.lower)


def discover_code_repositories(sources: list[str]) -> dict[str, list[str]]:
    """Discover the pickable code scope: GitHub owners and Azure DevOps projects.

    Both sides return *containers*, not individual repositories, so the two
    pickers mean the same thing — tick the thing you own, get everything inside
    it. The GitHub key keeps its ``SOURCE_GITHUB`` name because it still answers
    "what can I choose for GitHub"; use :func:`discover_github_repositories` when
    a concrete repository list is genuinely wanted.
    """
    selected = validate_code_sources(sources)
    return {
        SOURCE_GITHUB: discover_github_owners() if SOURCE_GITHUB in selected else [],
        SOURCE_AZDO: discover_azdo_projects() if SOURCE_AZDO in selected else [],
    }
