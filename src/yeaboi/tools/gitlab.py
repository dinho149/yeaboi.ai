"""GitLab tools — 3 read-only + 1 write (with user-confirmation guard in the docstring).

# See docs: "Tools" — tool types, @tool decorator, risk levels
#
# This module mirrors tools/github.py: the same read surface (repo tree, README,
# issues) so the intake repo-scan path can treat GitLab exactly like GitHub, plus
# one write tool. Read tools are low-risk (fetch project context for the LLM to
# reason about during project analysis); the write tool (create_issue) is
# high-risk and carries an explicit "only call after the user confirms" docstring
# note — the agent graph routes it through ``human_review`` before it executes.
#
# Why this integration exists at all: ``tools/__init__.py::detect_platform``
# already returns "GitLab", and intake question 16 already offers GitLab as a
# code-host option — but ``agent/nodes.py::_scan_repo_context`` used to bail with
# "GitLab not yet supported", so a user who answered "GitLab" got a silently
# degraded intake with no repository context. These tools close that gap.
#
# Why python-gitlab?
# It is the official, maintained SDK; it wraps the v4 REST API with typed
# objects, handles pagination, and — critically for this project — takes the
# instance URL as a constructor argument, so self-hosted GitLab works with no
# extra code. This mirrors how the rest of the project talks to external
# services through a per-integration SDK (PyGithub for GitHub,
# atlassian-python-api for Confluence/Jira, notion-client for Notion).
#
# Auth: a GitLab personal access token (GITLAB_TOKEN) sent as a private token.
# The only optional extra is GITLAB_URL, which defaults to https://gitlab.com and
# is set to point at a self-hosted instance (Notion has NOTION_ROOT_PAGE_ID in
# the same "one optional scoping var" slot).
#
# ONE deliberate divergence from GitHub: PyGithub is happy unauthenticated (60
# req/hr) so tools/github.py degrades to anonymous access, but these tools
# REQUIRE a token and return _MISSING_CONFIG_MSG without one. GitLab's
# unauthenticated API is far more restricted, most GitLab projects that matter
# for sprint planning are private or self-hosted, and an anonymous 404 is
# indistinguishable from "project is private" — which would surface to the user
# as a confusing "not found" rather than "you need a token".
"""

import logging

import gitlab
from gitlab.exceptions import GitlabAuthenticationError, GitlabError, GitlabHttpError
from langchain_core.tools import tool

from yeaboi.config import get_gitlab_token, get_gitlab_url

logger = logging.getLogger(__name__)

# Shown whenever the GitLab token is missing — single source of truth for the message.
_MISSING_CONFIG_MSG = "Error: GitLab is not configured. Ensure GITLAB_TOKEN is set in your .env file."

# Truncate file content at this many characters to avoid flooding the LLM context.
# Matches the cap in tools/github.py and tools/notion.py.
# See docs: "Tools" — scoping tool output for LLM relevance
_MAX_CONTENT_CHARS = 8_000

# Files that signal the tech stack — surfaced by gitlab_read_repo so the planner
# can infer the toolchain without reading every file. Mirrors github.py's list,
# plus GitLab's own CI config.
_KEY_FILES: frozenset[str] = frozenset(
    {
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "build.gradle",
        "Gemfile",
        "composer.json",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        ".gitlab-ci.yml",
        "README.md",
        "README.rst",
        "CONTRIBUTING.md",
        "Makefile",
        ".env.example",
        "tsconfig.json",
        "vite.config.ts",
        "vite.config.js",
    }
)

# GitLab issue states, as the REST API spells them. Note "opened", NOT "open" —
# passing GitHub's spelling returns an unfiltered list rather than an error, so
# we validate rather than forward blindly.
_ISSUE_STATES: frozenset[str] = frozenset({"opened", "closed", "all"})


def _parse_project(url: str) -> str:
    """Extract the 'namespace/project' path from a GitLab URL, or pass a slug through.

    GitLab differs from GitHub in two ways that matter here:

    1. **Nested groups** — a project can live arbitrarily deep
       (``group/subgroup/team/project``), so we cannot just keep the first two
       path segments the way ``github._parse_repo`` does.
    2. **The ``/-/`` separator** — GitLab inserts it before repo-relative paths
       (``group/project/-/tree/main``), which gives us an unambiguous place to
       cut off trailing UI paths.

    Handles:
    - https://gitlab.com/group/project
    - https://gitlab.com/group/subgroup/project
    - https://gitlab.example.com/group/project.git  (self-hosted)
    - https://gitlab.com/group/project/-/tree/main
    - group/project  (already a slug — returned unchanged)
    """
    from urllib.parse import urlparse

    text = url.strip()

    # Strip the scheme + host when present. Parsing rather than substring-matching
    # on "gitlab.com" is what makes self-hosted instances work at all.
    if "://" in text:
        try:
            text = urlparse(text).path
        except ValueError:
            return ""

    # Everything after "/-/" is GitLab UI routing (tree, blob, issues), not the path.
    text = text.split("/-/", 1)[0]
    text = text.strip("/")
    if text.endswith(".git"):
        text = text[:-4]
    return text


def _make_gitlab_client() -> gitlab.Gitlab | None:
    """Return an authenticated GitLab client, or None if the token is missing.

    GitLab authenticates with a single personal access token passed as
    ``private_token``; the instance URL is a constructor argument, which is what
    makes self-hosted instances work without any per-call branching.
    """
    token = get_gitlab_token()
    if not token:
        logger.warning("GitLab client not created — missing config (GITLAB_TOKEN unset)")
        return None
    url = get_gitlab_url()
    logger.debug("Creating GitLab client for %s", url)
    client = gitlab.Gitlab(url=url, private_token=token)
    logger.debug("GitLab client created successfully")
    return client


def _gitlab_error_msg(e: Exception) -> str:
    """Return a user-friendly message for common GitLab HTTP error codes.

    python-gitlab hangs the HTTP status off ``response_code`` on its error
    classes, so one mapping covers get/create/list failures alike.
    """
    code = getattr(e, "response_code", 0) or 0
    if code == 401:
        return "Error: GitLab authentication failed. Check GITLAB_TOKEN in .env."
    if code == 403:
        return "Error: GitLab permission denied. The token needs the 'read_api' scope (or 'api' to create issues)."
    if code == 404:
        return f"Error: GitLab project not found — verify the URL, and that the token can see it. ({e})"
    if code == 429:
        return "Error: GitLab rate limit reached. Wait a moment and try again."
    return f"Error: GitLab API error {code}: {e}" if code else f"Error: {e}"


def _get_project(client: gitlab.Gitlab, project_url: str):
    """Resolve a project URL/slug to a python-gitlab Project object.

    Raises the SDK's own exceptions — every caller already maps those through
    ``_gitlab_error_msg``, so wrapping them here would only lose the status code.
    """
    path = _parse_project(project_url)
    if not path:
        raise ValueError("Provide a GitLab project URL or 'namespace/project' path.")
    logger.debug("GitLab API call: projects.get(%r)", path)
    project = client.projects.get(path)
    logger.debug("GitLab API call succeeded: resolved project %r", path)
    return project


def _read_text_file(project, filename: str, ref: str) -> str | None:
    """Return a repo file's decoded text, or None when it does not exist.

    A missing file is an ordinary outcome (not every project has a README), so
    this collapses the SDK's 404 into None rather than raising.
    """
    try:
        logger.debug("GitLab API call: files.get(%r)", filename)
        blob = project.files.get(file_path=filename, ref=ref)
        content = blob.decode().decode("utf-8", errors="replace")
        logger.debug("GitLab API call succeeded: read %r (%d chars)", filename, len(content))
        return content
    except GitlabError:
        logger.debug("GitLab file %r not found — skipping", filename, exc_info=True)
        return None


def _truncate(content: str) -> str:
    """Cap content at _MAX_CONTENT_CHARS, appending a marker when it was cut."""
    if len(content) <= _MAX_CONTENT_CHARS:
        return content
    return content[:_MAX_CONTENT_CHARS] + f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]"


@tool
def gitlab_read_repo(project_url: str, max_depth: int = 2) -> str:
    """Read a GitLab project's file tree and return a structured summary.

    Returns the project description, default branch, top-level directory
    structure, detected tech-stack files (package.json, pyproject.toml,
    .gitlab-ci.yml, etc.), and the language breakdown. Use this first to
    understand a project's structure before reading individual files.
    Accepts a full GitLab URL or a 'namespace/project' path.
    """
    # See docs: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("gitlab_read_repo called: project_url=%r, max_depth=%d", project_url, max_depth)
    client = _make_gitlab_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    try:
        project = _get_project(client, project_url)
        path = project.path_with_namespace
        default_branch = project.default_branch or "main"

        lines: list[str] = [f"Project: {path}", f"Default branch: {default_branch}", ""]

        # recursive=True + get_all=True walks the whole tree in one paginated call,
        # matching github_read_repo's single get_git_tree(recursive=True).
        logger.debug("GitLab API call: repository_tree(recursive=True)")
        tree = project.repository_tree(recursive=True, get_all=True, ref=default_branch)
        logger.debug("GitLab API call succeeded: %d tree entries", len(tree))

        depth_limit = max(1, int(max_depth))
        dirs_in_scope: set[str] = set()
        key_files_found: list[str] = []
        for item in tree:
            item_path = item.get("path", "")
            if not item_path:
                continue
            parts = item_path.split("/")
            # Directories only, down to depth_limit — the file list at full depth
            # would swamp the LLM context on any real project.
            if item.get("type") == "tree" and len(parts) <= depth_limit:
                dirs_in_scope.add(item_path)
            # Key files are collected at ANY depth: a pyproject.toml nested in a
            # monorepo package is exactly as informative as one at the root.
            if parts[-1] in _KEY_FILES or item_path in _KEY_FILES:
                key_files_found.append(item_path)

        lines.append(f"File tree (directories, {depth_limit} level(s) deep):")
        for entry in sorted(dirs_in_scope)[:50]:  # cap so a wide repo cannot flood context
            lines.append(f"  {entry}/")
        if len(dirs_in_scope) > 50:
            lines.append(f"  … and {len(dirs_in_scope) - 50} more")

        if key_files_found:
            lines.append("")
            lines.append("Key files detected:")
            for kf in sorted(key_files_found):
                lines.append(f"  {kf}")

        # Language breakdown — a separate endpoint, and one a restricted token may
        # not reach, so a failure here must not lose the tree we already have.
        try:
            logger.debug("GitLab API call: languages()")
            languages = project.languages()
            if languages:
                lines.append("")
                lines.append("Languages:")
                for lang, pct in sorted(languages.items(), key=lambda x: -x[1])[:5]:
                    lines.append(f"  {lang}: {pct:.1f}%")
        except GitlabError:
            logger.debug("gitlab_read_repo: language data unavailable — skipping", exc_info=True)

        lines.append("")
        lines.append(
            f"Stars: {project.star_count}  Forks: {project.forks_count}  "
            f"Open issues: {getattr(project, 'open_issues_count', 0)}"
        )
        if project.description:
            lines.append(f"Description: {project.description}")

        logger.debug("gitlab_read_repo completed for %s", path)
        return "\n".join(lines)

    except ValueError as e:
        logger.error("gitlab_read_repo bad input %r: %s", project_url, e)
        return f"Error: {e}"
    except (GitlabAuthenticationError, GitlabHttpError, GitlabError) as e:
        logger.error("GitLab API error in gitlab_read_repo for %r: %s", project_url, e)
        return _gitlab_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in gitlab_read_repo for %r: %s", project_url, e)
        return f"Error: {e}"


@tool
def gitlab_read_readme(project_url: str) -> str:
    """Fetch the README and CONTRIBUTING docs from a GitLab project.

    Returns the decoded README content (truncated at 8 000 chars) and
    CONTRIBUTING.md if present. Use this to understand the project's purpose,
    architecture, and contribution guidelines.
    Accepts a full GitLab URL or a 'namespace/project' path.
    """
    logger.debug("gitlab_read_readme called: project_url=%r", project_url)
    client = _make_gitlab_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    try:
        project = _get_project(client, project_url)
        ref = project.default_branch or "main"
        sections: list[str] = []

        # GitLab has no "find the README whatever its extension" endpoint the way
        # PyGithub's get_readme() does, so we try the common spellings in order.
        for candidate in ("README.md", "README.rst", "README.txt", "README"):
            content = _read_text_file(project, candidate, ref)
            if content is not None:
                sections.append(f"=== README ({candidate}) ===\n\n{_truncate(content)}")
                break
        else:
            sections.append("=== README ===\n\nNo README found in this project.")

        contributing = _read_text_file(project, "CONTRIBUTING.md", ref)
        if contributing is not None:
            sections.append(f"\n=== CONTRIBUTING.md ===\n\n{_truncate(contributing)}")

        logger.debug("gitlab_read_readme completed for %s", project.path_with_namespace)
        return "\n".join(sections)

    except ValueError as e:
        logger.error("gitlab_read_readme bad input %r: %s", project_url, e)
        return f"Error: {e}"
    except (GitlabAuthenticationError, GitlabHttpError, GitlabError) as e:
        logger.error("GitLab API error in gitlab_read_readme for %r: %s", project_url, e)
        return _gitlab_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in gitlab_read_readme for %r: %s", project_url, e)
        return f"Error: {e}"


@tool
def gitlab_list_issues(project_url: str, state: str = "opened", max_issues: int = 20) -> str:
    """List issues from a GitLab project.

    Returns issue number, title, labels, and the first 200 characters of the
    description for up to max_issues results. Use this to understand work in
    progress, known bugs, and planned features that should inform the scrum plan.
    state: 'opened' (default), 'closed', or 'all' — note GitLab spells it
    'opened', not 'open'.
    """
    logger.debug("gitlab_list_issues called: project=%r, state=%s, max=%d", project_url, state, max_issues)
    client = _make_gitlab_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    # GitLab silently returns an unfiltered list for an unknown state rather than
    # erroring, so an unvalidated "open" would quietly include closed issues.
    normalised = state.strip().lower()
    if normalised not in _ISSUE_STATES:
        logger.warning("gitlab_list_issues rejected state=%r", state)
        return f"Error: state must be one of {sorted(_ISSUE_STATES)} (GitLab spells it 'opened', not 'open')."

    try:
        project = _get_project(client, project_url)
        path = project.path_with_namespace

        # per_page caps the response server-side; without get_all=True python-gitlab
        # returns just the first page, which is exactly the bound we want.
        logger.debug("GitLab API call: issues.list(state=%s, per_page=%d)", normalised, max_issues)
        issues = project.issues.list(state=normalised, per_page=max(1, min(int(max_issues), 100)))
        logger.debug("GitLab API call succeeded: %d issues", len(issues))

        lines: list[str] = [f"Issues ({normalised}) for {path}:", ""]

        count = 0
        for issue in issues:
            if count >= max_issues:
                break
            labels = ", ".join(issue.labels or [])
            label_str = f" [{labels}]" if labels else ""
            body_preview = ""
            if issue.description:
                body_preview = issue.description[:200].replace("\n", " ").strip()
                if len(issue.description) > 200:
                    body_preview += "..."

            lines.append(f"#{issue.iid}: {issue.title}{label_str}")
            if body_preview:
                lines.append(f"  {body_preview}")
            count += 1

        if count == 0:
            lines.append(f"No {normalised} issues found.")
        else:
            lines.append("")
            note = "; increase max_issues to see more" if count >= max_issues else ""
            lines.append(f"({count} issues shown{note})")

        logger.debug("gitlab_list_issues returned %d issues for %s", count, path)
        return "\n".join(lines)

    except ValueError as e:
        logger.error("gitlab_list_issues bad input %r: %s", project_url, e)
        return f"Error: {e}"
    except (GitlabAuthenticationError, GitlabHttpError, GitlabError) as e:
        logger.error("GitLab API error in gitlab_list_issues for %r: %s", project_url, e)
        return _gitlab_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in gitlab_list_issues for %r: %s", project_url, e)
        return f"Error: {e}"


@tool
def gitlab_create_issue(project_url: str, title: str, description: str = "", labels: str = "") -> str:
    """Create an issue in a GitLab project (e.g. to push a planned story to the board).

    Only call this after the user has explicitly confirmed they want to create the issue.
    labels is an optional comma-separated string ("bug, sprint-1") — GitLab creates
    any label that does not exist yet. The token needs the 'api' scope; the
    read-only 'read_api' scope is not sufficient to write.
    Returns the new issue's number, title, and URL on success.
    """
    logger.info("gitlab_create_issue called: project=%r, title=%r", project_url, title)
    client = _make_gitlab_client()
    if client is None:
        return _MISSING_CONFIG_MSG

    if not title.strip():
        return "Error: Provide a title for the issue."

    try:
        project = _get_project(client, project_url)
        payload: dict = {"title": title.strip(), "description": description}
        label_list = [label.strip() for label in labels.split(",") if label.strip()]
        if label_list:
            payload["labels"] = label_list

        logger.info("GitLab API call: issues.create on %s", project.path_with_namespace)
        issue = project.issues.create(payload)
        logger.info(
            "GitLab API call succeeded: created issue #%s in %s",
            issue.iid,
            project.path_with_namespace,
        )
        return f"Created GitLab issue #{issue.iid}: '{issue.title}'\nURL: {issue.web_url}"

    except ValueError as e:
        logger.error("gitlab_create_issue bad input %r: %s", project_url, e)
        return f"Error: {e}"
    except (GitlabAuthenticationError, GitlabHttpError, GitlabError) as e:
        logger.error("GitLab API error in gitlab_create_issue for %r: %s", project_url, e)
        return _gitlab_error_msg(e)
    except Exception as e:
        logger.error("Unexpected error in gitlab_create_issue for %r: %s", project_url, e)
        return f"Error: {e}"
