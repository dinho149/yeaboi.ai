"""GitLab tools — 3 read-only + 1 write (with user-confirmation guard in docstring).

# See docs: "Tools" — tool types, @tool decorator, risk levels
#
# This module mirrors tools/notion.py's shape exactly: a single
# ``_MISSING_CONFIG_MSG`` source of truth, one client factory that returns None
# rather than raising when credentials are absent, one error-message translator
# keyed on HTTP status, and ``@tool``-decorated functions that return plain
# strings. Read tools are low-risk (they fetch project context for the LLM to
# reason about during intake and planning); the single write tool
# (``gitlab_create_issue``) is high-risk and carries the explicit "only call
# after the user confirms" docstring note that routes it through the
# ``human_review`` graph node.
#
# Why python-gitlab?
# It is the maintained, official-in-practice SDK for the GitLab REST API: it
# wraps projects/issues/files as typed objects, handles pagination, and raises
# structured exceptions carrying ``response_code``. That matches how every other
# integration here talks to its service through a per-integration SDK (PyGithub
# for GitHub, atlassian-python-api for Jira/Confluence, notion-client for
# Notion), so error handling and client construction stay consistent.
#
# Auth: a GitLab personal access token (GITLAB_TOKEN) — a plain header token, no
# OAuth callback, which is what makes GitLab viable for a terminal app. Scope
# ``read_api`` covers the three read tools; ``api`` is needed for issue
# creation. GITLAB_URL is optional and defaults to https://gitlab.com so
# self-hosted instances work without code changes.
#
# ONE real divergence from GitHub's module: GitHub's tools degrade to
# unauthenticated access for public repos, so ``github_read_repo`` works with no
# token at all. These tools deliberately do NOT. gitlab.com will serve anonymous
# reads of *public* projects, but private projects and most self-hosted instances
# reject them — so degrading would give a scan that silently succeeds for public
# repos and 401s for everything else, with no way for the user to tell which
# happened. A missing token is therefore treated as "not configured" and returns
# _MISSING_CONFIG_MSG: one predictable behaviour and one actionable instruction.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import gitlab
import gitlab.exceptions
from langchain_core.tools import tool

from yeaboi.config import get_gitlab_token, get_gitlab_url

logger = logging.getLogger(__name__)

# Shown whenever the GitLab token is missing — single source of truth for the message.
_MISSING_CONFIG_MSG = "Error: GitLab is not configured. Ensure GITLAB_TOKEN is set in your .env file."

# Truncate file/README content at this many characters to avoid flooding the LLM
# context — same ceiling as the GitHub and Notion readers.
_MAX_CONTENT_CHARS = 8_000

# How many tree entries to show in the repo summary before truncating. A GitLab
# monorepo root can hold hundreds of paths; the LLM only needs the shape.
_MAX_TREE_ENTRIES = 40

# GitLab rejects a per_page above 100 — the ceiling an LLM-supplied limit is
# clamped to before it reaches the API.
_MAX_ISSUES_PER_PAGE = 100

# Candidate README paths, tried in order. GitLab exposes ``readme_url`` on a
# project but not the file contents, so the file has to be fetched by path.
_README_CANDIDATES = ("README.md", "README.rst", "README.txt", "README", "readme.md")

# Key config/manifest files to highlight in the repo tree summary — mirrors the
# same list in tools/github.py so both platforms surface the same signals.
_KEY_FILES = {
    "package.json",
    "pyproject.toml",
    "setup.py",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".gitlab-ci.yml",
    "README.md",
    "CONTRIBUTING.md",
    "Makefile",
    "requirements.txt",
    ".env.example",
    "tsconfig.json",
}


def _parse_project(url: str) -> str:
    """Extract a GitLab project path ('group/project' or 'group/sub/project') from a URL.

    GitLab differs from GitHub in one way that matters here: projects can nest
    arbitrarily deep in subgroups, so the path is NOT always exactly two
    segments. Everything after the host is kept, minus the trailing ``.git`` and
    minus GitLab's web-UI suffixes (``/-/tree/main``, ``/-/issues``, …), which
    always begin with the ``/-/`` separator.

    Handles:
    - https://gitlab.com/group/project
    - https://gitlab.example.com/group/subgroup/project.git
    - https://gitlab.com/group/project/-/tree/main
    - group/project  (already a path — returned unchanged)
    """
    url = url.strip().rstrip("/")
    if "://" in url:
        parsed = urlparse(url)
        url = (parsed.path or "").strip("/")
    # GitLab separates the project path from web-UI routes with "/-/".
    if "/-/" in url:
        url = url.split("/-/", 1)[0]
    if url.endswith(".git"):
        url = url[:-4]
    return url.strip("/")


def _make_gitlab_client(timeout_seconds: int | None = None) -> gitlab.Gitlab | None:
    """Return an authenticated GitLab client, or None if the token is missing.

    Returning None instead of raising is the shape every tool here relies on:
    each tool checks for None and returns ``_MISSING_CONFIG_MSG``, so an
    unconfigured integration produces a readable instruction rather than a
    traceback in the middle of the ReAct loop.
    """
    token = get_gitlab_token()
    if not token:
        logger.warning("GitLab client not created — missing GITLAB_TOKEN")
        return None
    url = get_gitlab_url()
    logger.debug("Creating GitLab client for %s", url)
    kwargs = {"timeout": timeout_seconds} if timeout_seconds is not None else {}
    client = gitlab.Gitlab(url=url, private_token=token, **kwargs)
    logger.debug("GitLab client created successfully")
    return client


def _gitlab_error_msg(e: Exception) -> str:
    """Return a user-friendly message for common GitLab HTTP error codes.

    python-gitlab exceptions carry the HTTP status on ``response_code``; the
    codes below are the ones a misconfigured token actually produces, so each
    maps to the specific fix rather than a generic failure.
    """
    code = getattr(e, "response_code", 0) or 0
    if code == 401:
        return "Error: GitLab authentication failed. Check GITLAB_TOKEN in .env."
    if code == 403:
        return "Error: GitLab permission denied. The token needs the 'read_api' scope (or 'api' to create issues)."
    if code == 404:
        return f"Error: GitLab project or resource not found — check the URL and GITLAB_URL. ({e})"
    if code == 429:
        return "Error: GitLab rate limit reached. Wait a moment and try again."
    return f"Error: GitLab API error {code}: {e}" if code else f"Error: GitLab API error: {e}"


def _format_tree(entries: list) -> list[str]:
    """Render repository tree entries as readable lines, key files marked with ★.

    Entries are the raw dicts python-gitlab returns from ``repository_tree`` —
    each with ``name`` and ``type`` ("tree" for a directory, "blob" for a file).
    Directories get a trailing slash so the LLM can tell structure from content.
    """
    lines: list[str] = []
    for entry in entries[:_MAX_TREE_ENTRIES]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        if not name:
            continue
        is_dir = entry.get("type") == "tree"
        marker = " ★" if name in _KEY_FILES else ""
        lines.append(f"  {name}{'/' if is_dir else ''}{marker}")
    if len(entries) > _MAX_TREE_ENTRIES:
        lines.append(f"  … ({len(entries) - _MAX_TREE_ENTRIES} more entries)")
    return lines


@tool
def gitlab_read_repo(repo_url: str) -> str:
    """Read a GitLab project's metadata and top-level file tree.

    Use this during project analysis to ground the scrum plan in the real
    codebase: it returns the project name, description, default branch,
    visibility, last activity, topics, and the root directory listing with key
    manifest files marked. Accepts a full GitLab URL (gitlab.com or self-hosted)
    or a bare 'group/project' path, including nested subgroups.
    """
    # See docs: "The ReAct Loop" — this is the Action step; the returned string is the Observation
    logger.debug("gitlab_read_repo called: repo_url=%r", repo_url)
    client = _make_gitlab_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    path = _parse_project(repo_url)
    if not path:
        return "Error: Provide a GitLab project URL or 'group/project' path."

    try:
        logger.info("GitLab API: fetching project %s", path)
        project = client.projects.get(path)
        default_branch = getattr(project, "default_branch", "") or "main"

        lines = [
            f"=== GitLab project: {getattr(project, 'path_with_namespace', path)} ===",
            f"Name: {getattr(project, 'name', '')}",
            f"Description: {getattr(project, 'description', '') or '(none)'}",
            f"URL: {getattr(project, 'web_url', '')}",
            f"Default branch: {default_branch}",
            f"Visibility: {getattr(project, 'visibility', 'unknown')}",
            f"Last activity: {getattr(project, 'last_activity_at', 'unknown')}",
            f"Stars: {getattr(project, 'star_count', 0)} | Forks: {getattr(project, 'forks_count', 0)}",
        ]
        topics = getattr(project, "topics", None) or []
        if topics:
            lines.append(f"Topics: {', '.join(str(t) for t in topics)}")

        # repository_tree without recursion returns the root listing only —
        # enough shape for the LLM without paging an entire monorepo.
        entries = project.repository_tree(ref=default_branch, get_all=False)
        lines.append("")
        lines.append("Root contents:")
        tree_lines = _format_tree(list(entries or []))
        lines.extend(tree_lines or ["  (empty)"])

        logger.info("GitLab API: read project %s (%d root entries)", path, len(entries or []))
        return "\n".join(lines)

    except gitlab.exceptions.GitlabError as e:
        logger.error("GitLab API error in read_repo for %s: %s", path, e)
        return _gitlab_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in gitlab_read_repo for %s: %s", path, e)
        return f"Error: {e}"


@tool
def gitlab_read_readme(repo_url: str) -> str:
    """Read a GitLab project's README as plain text.

    The README is usually the densest single source of project purpose, setup
    steps, and architecture notes — read it before decomposing a project into
    epics. Tries the common README filenames on the default branch and truncates
    at 8 000 characters. Accepts a full GitLab URL or a 'group/project' path.
    """
    logger.debug("gitlab_read_readme called: repo_url=%r", repo_url)
    client = _make_gitlab_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    path = _parse_project(repo_url)
    if not path:
        return "Error: Provide a GitLab project URL or 'group/project' path."

    try:
        logger.info("GitLab API: fetching README for %s", path)
        project = client.projects.get(path)
        ref = getattr(project, "default_branch", "") or "main"

        # GitLab has no "get the README" endpoint (unlike GitHub's), so each
        # candidate name is tried in turn; a 404 just means "try the next one".
        for candidate in _README_CANDIDATES:
            try:
                blob = project.files.get(file_path=candidate, ref=ref)
            except gitlab.exceptions.GitlabError as e:
                if (getattr(e, "response_code", 0) or 0) == 404:
                    continue
                raise
            content = blob.decode()
            if isinstance(content, bytes):
                content = content.decode("utf-8", errors="replace")
            truncated = len(content) > _MAX_CONTENT_CHARS
            if truncated:
                content = content[:_MAX_CONTENT_CHARS]
            logger.info("GitLab API: read %s from %s (%d chars)", candidate, path, len(content))
            suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
            return f"=== README ({candidate}) — {path} ===\n\n{content}{suffix}"

        logger.info("GitLab API: no README found in %s", path)
        return f"No README found in GitLab project '{path}'."

    except gitlab.exceptions.GitlabError as e:
        logger.error("GitLab API error in read_readme for %s: %s", path, e)
        return _gitlab_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in gitlab_read_readme for %s: %s", path, e)
        return f"Error: {e}"


@tool
def gitlab_list_issues(repo_url: str, state: str = "opened", limit: int = 20) -> str:
    """List issues in a GitLab project — the backlog the sprint plan must respect.

    Use this to see what the team already has queued before proposing new work,
    and as standup evidence for what is in flight. state is one of "opened"
    (default), "closed", or "all". Returns issue number, title, state, labels,
    assignee, and URL for up to limit issues, newest first.
    """
    logger.debug("gitlab_list_issues called: repo_url=%r, state=%r, limit=%d", repo_url, state, limit)
    client = _make_gitlab_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    path = _parse_project(repo_url)
    if not path:
        return "Error: Provide a GitLab project URL or 'group/project' path."

    if state not in ("opened", "closed", "all"):
        return f"Error: state must be 'opened', 'closed', or 'all' (got '{state}')."

    try:
        # GitLab caps per_page at 100 and errors above it; clamp rather than
        # letting an over-eager LLM-supplied limit turn into a 400.
        page_size = max(1, min(limit, _MAX_ISSUES_PER_PAGE))
        logger.info("GitLab API: listing %s issues for %s (limit=%d)", state, path, page_size)
        project = client.projects.get(path)
        # get_all=False keeps python-gitlab on a single page — the LLM needs a
        # sample of the backlog, not every issue in a five-year-old project.
        issues = project.issues.list(state=state, per_page=page_size, get_all=False)
        issues = list(issues or [])[:page_size]

        if not issues:
            return f"No {state} issues found in GitLab project '{path}'."

        lines = [f"{state.capitalize()} issues in GitLab project '{path}':", ""]
        for issue in issues:
            iid = getattr(issue, "iid", "?")
            title = getattr(issue, "title", "(untitled)")
            lines.append(f"#{iid}: {title}")
            labels = getattr(issue, "labels", None) or []
            if labels:
                lines.append(f"  Labels: {', '.join(str(label) for label in labels)}")
            author = getattr(issue, "author", None) or {}
            assignee = getattr(issue, "assignee", None) or {}
            who = (assignee or {}).get("name") or (assignee or {}).get("username") or ""
            if who:
                lines.append(f"  Assignee: {who}")
            elif author:
                lines.append(f"  Author: {author.get('name') or author.get('username') or 'unknown'}")
            url = getattr(issue, "web_url", "")
            if url:
                lines.append(f"  URL: {url}")
            lines.append("")

        logger.info("GitLab API: listed %d %s issues for %s", len(issues), state, path)
        lines.append(f"({len(issues)} issues shown)")
        return "\n".join(lines)

    except gitlab.exceptions.GitlabError as e:
        logger.error("GitLab API error in list_issues for %s: %s", path, e)
        return _gitlab_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in gitlab_list_issues for %s: %s", path, e)
        return f"Error: {e}"


@tool
def gitlab_create_issue(repo_url: str, title: str, description: str = "", labels: str = "") -> str:
    """Create an issue in a GitLab project from a generated user story or task.

    Only call this after the user has explicitly confirmed they want to write to GitLab.
    The token needs the 'api' scope (the read-only 'read_api' scope is not enough).
    labels is an optional comma-separated string ("backend, sprint-1"). Returns the
    new issue's number and URL on success.
    """
    logger.debug("gitlab_create_issue called: repo_url=%r, title=%r", repo_url, title)
    client = _make_gitlab_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    path = _parse_project(repo_url)
    if not path:
        return "Error: Provide a GitLab project URL or 'group/project' path."

    if not title.strip():
        return "Error: Provide a title for the issue."

    try:
        logger.info("GitLab API: creating issue in %s: %r", path, title)
        project = client.projects.get(path)
        payload: dict = {"title": title.strip(), "description": description}
        label_list = [label.strip() for label in labels.split(",") if label.strip()]
        if label_list:
            payload["labels"] = label_list

        issue = project.issues.create(payload)
        iid = getattr(issue, "iid", "?")
        url = getattr(issue, "web_url", "")
        logger.info("GitLab API: created issue #%s in %s", iid, path)
        return f"Created GitLab issue #{iid} in {path}: '{title}'\nURL: {url}"

    except gitlab.exceptions.GitlabError as e:
        logger.error("GitLab API error in create_issue for %s: %s", path, e)
        return _gitlab_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in gitlab_create_issue for %s: %s", path, e)
        return f"Error: {e}"
