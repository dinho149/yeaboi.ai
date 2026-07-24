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
    """Backward-compatible first-run scope from the legacy environment config."""
    from yeaboi.config import get_azure_devops_project, get_standup_github_repo

    github = [get_standup_github_repo().strip()] if get_standup_github_repo().strip() else []
    azdo_project = (get_azure_devops_project() or "").strip()
    sources = ([SOURCE_GITHUB] if github else []) + ([SOURCE_AZDO] if azdo_project else [])
    return sources, github, ([azdo_project] if azdo_project else [])


def discover_github_repositories(limit: int = 200) -> list[str]:
    """List repositories visible to the configured GitHub identity."""
    from yeaboi.config import get_standup_github_repo

    repos: set[str] = set()
    try:
        from yeaboi.tools.github import _get_github_client

        client = _get_github_client()
        for repo in client.get_user().get_repos(sort="full_name")[:limit]:
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


def discover_azdo_projects(limit: int = 200) -> list[str]:
    """List Azure DevOps projects visible in the configured organization."""
    from yeaboi.config import get_azure_devops_org_url, get_azure_devops_project, get_azure_devops_token

    org_url = get_azure_devops_org_url()
    if not org_url:
        return []
    project_names: set[str] = set()
    try:
        from yeaboi.tools.azure_devops import _make_connection

        connection = _make_connection(org_url, get_azure_devops_token())
        core = connection.clients.get_core_client()
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
        from yeaboi.tools.azure_devops import _make_connection

        connection = _make_connection(org_url, get_azure_devops_token())
        git = connection.clients.get_git_client()
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
    """Discover GitHub repositories and Azure DevOps projects."""
    selected = validate_code_sources(sources)
    return {
        SOURCE_GITHUB: discover_github_repositories() if SOURCE_GITHUB in selected else [],
        SOURCE_AZDO: discover_azdo_projects() if SOURCE_AZDO in selected else [],
    }
