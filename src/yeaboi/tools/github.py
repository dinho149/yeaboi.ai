"""GitHub read-only tools for fetching repo context.

# See docs: "Tools" — tool types, @tool decorator, risk levels
#
# All four tools are read-only (low risk) — they fetch public data from the
# GitHub API and return it as a string for the LLM to reason about. The LLM
# uses these tools in the ReAct loop (Thought → Action → Observation) to
# ground its scrum planning in the actual codebase.
#
# Why PyGithub instead of raw requests?
# PyGithub wraps the REST API with typed objects, handles pagination, and
# raises structured exceptions (GithubException, RateLimitExceededException).
# This makes error handling predictable across all four tools.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import github
from langchain_core.tools import tool

from yeaboi.config import get_github_token

logger = logging.getLogger(__name__)
_GITHUB_DETAIL_SEMAPHORE = threading.BoundedSemaphore(4)

# Truncate file/README content at this many characters to avoid flooding the LLM context.
_MAX_CONTENT_CHARS = 8_000

# Key config/manifest files to highlight in the repo tree summary.
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
    ".github",
    "README.md",
    "README.rst",
    "CONTRIBUTING.md",
    "Makefile",
    "requirements.txt",
    ".env.example",
    "tsconfig.json",
    "webpack.config.js",
    "vite.config.ts",
    "vite.config.js",
}


def _parse_repo(url: str) -> str:
    """Extract 'owner/repo' from a GitHub URL or pass through if already a slug.

    Handles:
    - https://github.com/owner/repo
    - http://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo/
    - owner/repo  (already a slug — returned unchanged)
    """
    url = url.strip().rstrip("/")
    if url.startswith(("https://github.com/", "http://github.com/")):
        url = url.split("github.com/", 1)[1]
    if url.endswith(".git"):
        url = url[:-4]
    # Strip any trailing path segments (e.g. /tree/main, /issues)
    parts = url.split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}"
    return url


def _get_github_client() -> github.Github:
    """Create a PyGithub client, using GITHUB_TOKEN if available."""
    token = get_github_token()
    # PyGithub accepts token=None for unauthenticated access (lower rate limits).
    # See docs: "Tools" — authentication pattern
    if not token:
        logger.warning("No GITHUB_TOKEN set — using unauthenticated access (60 req/hr)")
    logger.debug("Creating GitHub client (authenticated=%s)", bool(token))
    return github.Github(auth=github.Auth.Token(token) if token else None)


def github_analysis_inventory(
    owners: list[str] | tuple[str, ...],
    days: int = 120,
    *,
    include_trees: bool = True,
) -> list[dict]:
    """Discover every repository in configured owners/orgs for Analysis mode.

    PyGithub paginates lazily, so iterating the returned lists avoids the old
    first-100/one-repository bias.  Recently active repositories also include a
    deterministic tree inventory used by repository-health analysis.
    """
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    client = _get_github_client()
    out: list[dict] = []
    seen: set[str] = set()
    for owner_name in owners:
        try:
            try:
                owner = client.get_organization(owner_name)
            except Exception:
                owner = client.get_user(owner_name)
            repos = owner.get_repos()
            for repo in repos:
                slug = str(getattr(repo, "full_name", "") or "").strip()
                if not slug or slug.lower() in seen:
                    continue
                seen.add(slug.lower())
                pushed = getattr(repo, "pushed_at", None) or getattr(repo, "updated_at", None)
                if pushed is not None and pushed.tzinfo is None:
                    pushed = pushed.replace(tzinfo=UTC)
                archived = bool(getattr(repo, "archived", False))
                # Relevance: archived repos and repos with no recorded push are
                # never scanned (unknown-pushed used to count as active, pulling
                # dead repos into every run); the skip reason feeds coverage notes.
                skip_reason = ""
                if archived:
                    skip_reason = "archived repository"
                elif pushed is None:
                    skip_reason = "no recorded push activity"
                active = not skip_reason and pushed >= cutoff
                paths: list[str] = []
                tree_error = ""
                if include_trees and active and not bool(getattr(repo, "empty", False)):
                    try:
                        tree = repo.get_git_tree(sha=getattr(repo, "default_branch", "") or "HEAD", recursive=True)
                        paths = [str(item.path) for item in tree.tree if getattr(item, "type", "") == "blob"]
                        if bool(getattr(tree, "truncated", False)):
                            tree_error = "GitHub tree response was truncated"
                    except Exception as exc:
                        tree_error = str(exc)
                out.append(
                    {
                        "provider": "github",
                        "container": owner_name,
                        "name": slug,
                        "url": getattr(repo, "html_url", "") or "",
                        "default_branch": getattr(repo, "default_branch", "") or "",
                        "archived": archived,
                        "updated_at": pushed.isoformat() if pushed else "",
                        "active": active,
                        "skip_reason": skip_reason,
                        "paths": paths,
                        "error": tree_error,
                    }
                )
        except Exception as exc:
            out.append(
                {
                    "provider": "github",
                    "container": owner_name,
                    "name": owner_name,
                    "active": True,
                    "paths": [],
                    "error": f"repository discovery failed: {exc}",
                    "discovery_error": True,
                }
            )
    return out


@tool
def github_read_repo(repo_url: str, max_depth: int = 2) -> str:
    """Read the repository file tree and return a structured summary.

    Returns the top-level directory structure (up to max_depth), detected tech
    stack files (package.json, pyproject.toml, Dockerfile, etc.), and language
    breakdown. Use this first to understand a project's structure before reading
    individual files.
    """
    # See docs: "The ReAct Loop" — this is the Action step; the result is the Observation
    logger.debug("github_read_repo called: repo_url=%r, max_depth=%d", repo_url, max_depth)
    try:
        slug = _parse_repo(repo_url)
        g = _get_github_client()
        repo = g.get_repo(slug)

        # get_git_tree with recursive=True fetches the full tree in one API call.
        # We filter to max_depth to avoid overwhelming the LLM with deep paths.
        tree = repo.get_git_tree(sha="HEAD", recursive=True)

        lines: list[str] = [f"Repository: {slug}", f"Default branch: {repo.default_branch}", ""]

        # Separate directories and files, filtered to max_depth
        dirs: set[str] = set()
        files_at_depth: list[str] = []
        key_files_found: list[str] = []

        for item in tree.tree:
            parts = item.path.split("/")
            depth = len(parts)
            name = parts[-1]

            if depth <= max_depth:
                if item.type == "tree":
                    dirs.add(item.path)
                else:
                    files_at_depth.append(item.path)

            # Collect key files regardless of depth
            if name in _KEY_FILES or item.path in _KEY_FILES:
                key_files_found.append(item.path)

        # Build a simple indented tree from top-level items
        lines.append("File tree (top level):")
        top_level = sorted({p.split("/")[0] for p in [i.path for i in tree.tree]})
        for entry in top_level[:50]:  # cap at 50 top-level entries
            lines.append(f"  {entry}/")

        if key_files_found:
            lines.append("")
            lines.append("Key files detected:")
            for kf in sorted(key_files_found):
                lines.append(f"  {kf}")

        # Language breakdown from GitHub's language API
        try:
            languages = repo.get_languages()
            if languages:
                total = sum(languages.values())
                lines.append("")
                lines.append("Languages:")
                for lang, bytes_count in sorted(languages.items(), key=lambda x: -x[1])[:5]:
                    pct = bytes_count / total * 100
                    lines.append(f"  {lang}: {pct:.1f}%")
        except github.GithubException:
            logger.debug("github_read_repo: language data unavailable — skipping", exc_info=True)

        lines.append("")
        lines.append(
            f"Stars: {repo.stargazers_count}  Forks: {repo.forks_count}  Open issues: {repo.open_issues_count}"
        )
        if repo.description:
            lines.append(f"Description: {repo.description}")

        logger.debug("github_read_repo completed for %s", slug)
        return "\n".join(lines)

    except github.RateLimitExceededException:
        logger.warning("GitHub rate limit exceeded in github_read_repo")
        return "GitHub rate limit reached. Add a GITHUB_TOKEN to .env to raise limits from 60 to 5 000 req/hr."
    except github.GithubException as e:
        logger.error("GitHub API error in github_read_repo: %s", e)
        return f"Error: {e.data.get('message', str(e)) if isinstance(e.data, dict) else str(e)}"
    except Exception as e:
        logger.error("Unexpected error in github_read_repo: %s", e)
        return f"Error: {e}"


@tool
def github_read_file(repo_url: str, file_path: str) -> str:
    """Fetch the raw contents of a specific file from a GitHub repository.

    Use this after github_read_repo identifies an important file (e.g. README,
    package.json, Dockerfile, main source file). Truncates at 8 000 characters
    with a note if the file is larger.
    """
    logger.debug("github_read_file called: repo=%r, path=%r", repo_url, file_path)
    try:
        slug = _parse_repo(repo_url)
        g = _get_github_client()
        repo = g.get_repo(slug)

        # get_contents() returns a ContentFile with base64-encoded content.
        # It raises UnknownObjectException (404) if the file does not exist.
        content_file = repo.get_contents(file_path)

        # content_file may be a list if file_path is a directory — guard against it.
        if isinstance(content_file, list):
            entries = [f.path for f in content_file]
            return "Path is a directory. Contents:\n" + "\n".join(f"  {e}" for e in entries)

        decoded = content_file.decoded_content.decode("utf-8", errors="replace")

        truncated = False
        if len(decoded) > _MAX_CONTENT_CHARS:
            decoded = decoded[:_MAX_CONTENT_CHARS]
            truncated = True

        logger.debug("github_read_file fetched %s (%d bytes)", file_path, content_file.size)
        header = f"File: {file_path} ({content_file.size} bytes)\n\n"
        suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
        return header + decoded + suffix

    except github.RateLimitExceededException:
        logger.warning("GitHub rate limit exceeded in github_read_file")
        return "GitHub rate limit reached. Add a GITHUB_TOKEN to .env to raise limits from 60 to 5 000 req/hr."
    except github.GithubException as e:
        logger.error("GitHub API error in github_read_file: %s", e)
        return f"Error: {e.data.get('message', str(e)) if isinstance(e.data, dict) else str(e)}"
    except Exception as e:
        logger.error("Unexpected error in github_read_file: %s", e)
        return f"Error: {e}"


@tool
def github_list_issues(repo_url: str, state: str = "open", max_issues: int = 20) -> str:
    """List issues and pull requests from a GitHub repository.

    Returns issue number, title, labels, and first 200 characters of the body
    for up to max_issues results. Use this to understand current work in progress,
    known bugs, and planned features that should inform the scrum plan.
    state: 'open' (default), 'closed', or 'all'.
    """
    logger.debug("github_list_issues called: repo=%r, state=%s, max=%d", repo_url, state, max_issues)
    try:
        slug = _parse_repo(repo_url)
        g = _get_github_client()
        repo = g.get_repo(slug)

        # get_issues() returns a PaginatedList — slicing triggers lazy pagination.
        # state must be "open", "closed", or "all" (validated by PyGithub).
        issues = repo.get_issues(state=state)

        lines: list[str] = [f"Issues ({state}) for {slug}:", ""]

        count = 0
        for issue in issues:
            if count >= max_issues:
                break
            labels = ", ".join(label.name for label in issue.labels)
            label_str = f" [{labels}]" if labels else ""
            pr_tag = " [PR]" if issue.pull_request else ""
            body_preview = ""
            if issue.body:
                body_preview = issue.body[:200].replace("\n", " ").strip()
                if len(issue.body) > 200:
                    body_preview += "..."

            lines.append(f"#{issue.number}{pr_tag}: {issue.title}{label_str}")
            if body_preview:
                lines.append(f"  {body_preview}")
            count += 1

        if count == 0:
            lines.append(f"No {state} issues found.")
        else:
            lines.append("")
            note = "; increase max_issues to see more" if count >= max_issues else ""
            lines.append(f"({count} issues shown{note})")

        logger.debug("github_list_issues returned %d issues for %s", count, slug)
        return "\n".join(lines)

    except github.RateLimitExceededException:
        logger.warning("GitHub rate limit exceeded in github_list_issues")
        return "GitHub rate limit reached. Add a GITHUB_TOKEN to .env to raise limits from 60 to 5 000 req/hr."
    except github.GithubException as e:
        logger.error("GitHub API error in github_list_issues: %s", e)
        return f"Error: {e.data.get('message', str(e)) if isinstance(e.data, dict) else str(e)}"
    except Exception as e:
        logger.error("Unexpected error in github_list_issues: %s", e)
        return f"Error: {e}"


@tool
def github_read_readme(repo_url: str) -> str:
    """Fetch the README and CONTRIBUTING docs from a GitHub repository.

    Returns the decoded README content (truncated at 8 000 chars) and
    CONTRIBUTING.md if present. Use this to understand the project's purpose,
    architecture, and contribution guidelines.
    """
    logger.debug("github_read_readme called: repo=%r", repo_url)
    try:
        slug = _parse_repo(repo_url)
        g = _get_github_client()
        repo = g.get_repo(slug)

        sections: list[str] = []

        # get_readme() finds README.md, README.rst, README.txt, etc. automatically.
        # Raises UnknownObjectException if no README exists.
        try:
            readme = repo.get_readme()
            content = readme.decoded_content.decode("utf-8", errors="replace")
            truncated = False
            if len(content) > _MAX_CONTENT_CHARS:
                content = content[:_MAX_CONTENT_CHARS]
                truncated = True
            header = f"=== README ({readme.path}) ===\n\n"
            suffix = f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]" if truncated else ""
            sections.append(header + content + suffix)
        except github.GithubException:
            sections.append("=== README ===\n\nNo README found in this repository.")

        # Try to fetch CONTRIBUTING.md — not always present, so handle 404 gracefully.
        try:
            contributing = repo.get_contents("CONTRIBUTING.md")
            if not isinstance(contributing, list):
                contrib_content = contributing.decoded_content.decode("utf-8", errors="replace")
                if len(contrib_content) > _MAX_CONTENT_CHARS:
                    contrib_content = contrib_content[:_MAX_CONTENT_CHARS]
                    contrib_content += f"\n\n[Truncated at {_MAX_CONTENT_CHARS} characters]"
                sections.append(f"\n=== CONTRIBUTING.md ===\n\n{contrib_content}")
        except github.GithubException:
            logger.debug("github_read_readme: no CONTRIBUTING.md — skipping")

        logger.debug("github_read_readme completed for %s", slug)
        return "\n".join(sections)

    except github.RateLimitExceededException:
        logger.warning("GitHub rate limit exceeded in github_read_readme")
        return "GitHub rate limit reached. Add a GITHUB_TOKEN to .env to raise limits from 60 to 5 000 req/hr."
    except github.GithubException as e:
        logger.error("GitHub API error in github_read_readme: %s", e)
        return f"Error: {e.data.get('message', str(e)) if isinstance(e.data, dict) else str(e)}"
    except Exception as e:
        logger.error("Unexpected error in github_read_readme: %s", e)
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Recent-activity helpers for Daily Standup mode
# ---------------------------------------------------------------------------
# Plain functions (not @tool) the standup collector calls directly. They return
# structured data and degrade gracefully to [] on any error/missing repo — a
# standup must never crash because GitHub is unreachable.
# See docs: "Daily Standup" — recent-activity collection


def _since_dt(days: int, since=None):
    """Return the UTC window-start datetime for GitHub API filters.

    ``since`` (a tz-aware datetime, e.g. the standup's previous-working-day
    midnight) wins when given; otherwise fall back to ``days`` days ago.
    """
    from datetime import UTC, datetime, timedelta

    if since is not None:
        return since.astimezone(UTC)
    return datetime.now(UTC) - timedelta(days=int(days))


def _author_type(user) -> str:
    """Return "bot" when a GitHub user object is an app/bot account, else "".

    GitHub marks integrations with ``type == "Bot"`` and their logins end in
    ``[bot]`` — free provider metadata the standup's automation filter uses
    (see standup/automation.py).
    """
    login = getattr(user, "login", "") or ""
    if (getattr(user, "type", "") or "").lower() == "bot" or login.lower().endswith("[bot]"):
        return "bot"
    return ""


def _raise_if_github_auth(e: Exception) -> None:
    """Re-raise a GitHub 401/403 credential error as a StandupSourceError.

    Rate-limit 403s are handled separately by the caller; this only fires for
    bad/expired credentials so the standup can tell the user to fix GITHUB_TOKEN.
    """
    status = getattr(e, "status", 0)
    if isinstance(e, github.BadCredentialsException) or status == 401:
        from yeaboi.standup.errors import StandupSourceError

        raise StandupSourceError("github", "authentication failed — check GITHUB_TOKEN")


def _github_changed_files(
    value,
    *,
    metadata_cache=None,
    object_key: str = "",
    revision: str = "",
) -> list[str]:
    """Best-effort changed paths for a commit or PR; never hides its activity."""

    def _fetch() -> list[str]:
        try:
            with _GITHUB_DETAIL_SEMAPHORE:
                files = value.get_files() if hasattr(value, "get_files") else getattr(value, "files", ())
            return [
                str(getattr(file, "filename", "") or "")
                for file in list(files or ())[:100]
                if getattr(file, "filename", "")
            ]
        except Exception as exc:
            logger.debug("github changed-file lookup failed: %s", exc)
            return []

    if metadata_cache is not None and object_key and revision:
        return list(
            metadata_cache.get_or_compute(
                "github",
                "changed_files",
                object_key,
                revision,
                _fetch,
                cache_empty=False,
                replace_revisions=object_key.rpartition(":")[2].startswith("#"),
            )
        )
    return _fetch()


_MAX_CHANGED_FILE_LOOKUPS = 25

# Caps for the analysis/standup activity scan: PyGithub pages ~30 items per HTTP
# request, so an unbounded iteration over a high-churn repo's 120-day window can
# cost hundreds of round-trips per repo. Mirrors azure_devops.py's scan caps
# (_MAX_REPO_COMMITS/_MAX_REPO_PRS there); a full-cap result is disclosed as a
# "truncated" coverage note by the analysis collector.
_MAX_REPO_COMMITS = 300
_MAX_REPO_PRS = 100


def github_recent_commits(
    repo_url: str,
    days: int = 1,
    since=None,
    metadata_cache=None,
    *,
    include_changed_files: bool = True,
) -> list[dict]:
    """Return commits pushed to the default branch since the window start.

    The window is ``since → now`` when ``since`` (tz-aware datetime) is given,
    else the last ``days`` days. Each item: {author, kind='commit', title, body,
    timestamp, key(sha)}. ``body`` is the commit message body (Co-Authored-By /
    AI-tool trailers). Returns [] when the repo can't be read (no token /
    not found / rate-limited).
    """
    logger.info("github_recent_commits: repo=%r days=%d since=%s", repo_url, days, since)
    try:
        slug = _parse_repo(repo_url)
        repo = _get_github_client().get_repo(slug)
        commits = repo.get_commits(since=_since_dt(days, since))
        items: list[dict] = []
        for index, c in enumerate(commits):
            if index >= _MAX_REPO_COMMITS:
                logger.info("github_recent_commits: capped at %d commits for %s", _MAX_REPO_COMMITS, slug)
                break
            commit = c.commit
            author = commit.author.name if commit.author else ""
            email = (getattr(commit.author, "email", "") or "") if commit.author else ""
            full = commit.message or ""
            lines = full.splitlines()
            msg = lines[0] if lines else ""
            body = "\n".join(lines[1:]).strip()  # message body: Co-Authored-By / AI-tool trailers live here
            ts = commit.author.date.isoformat()[:19] if commit.author and commit.author.date else ""
            items.append(
                {
                    "author": author,
                    "author_email": email,
                    "kind": "commit",
                    "title": msg,
                    "body": body,
                    "timestamp": ts,
                    "key": c.sha[:8],
                    "commit_id": c.sha,
                    "url": getattr(c, "html_url", "") or "",
                    "changed_files": (
                        _github_changed_files(
                            c,
                            metadata_cache=metadata_cache,
                            object_key=f"{slug}:{c.sha}",
                            revision=c.sha,
                        )
                        if include_changed_files and index < _MAX_CHANGED_FILE_LOOKUPS
                        else []
                    ),
                }
            )
        logger.info("github_recent_commits: %d commit(s) in last %d day(s)", len(items), days)
        return items
    except github.RateLimitExceededException:
        logger.warning("github_recent_commits skipped — rate limit reached")
        return []
    except Exception as e:
        _raise_if_github_auth(e)
        logger.warning("github_recent_commits failed: %s", e)
        return []


# Standup-path bounds for branch-commit expansion: the daily feed only needs a
# taste of unmerged feature work. The exhaustive analysis path lifts both caps.
_MAX_PR_COMMIT_LOOKUPS = 10
_MAX_COMMITS_PER_PR = 60


def _pr_branch_commit_items(pr, cutoff, *, limit: int | None = None) -> list[dict]:
    """In-window commits on a PR's branch — feature work invisible on the default branch.

    Best-effort: any failure yields [] for this PR only. The collector's dedupe
    pass drops shas that already arrived via the default-branch scan.
    """
    try:
        commits = list(pr.get_commits()[:limit] if limit else pr.get_commits())
    except Exception as e:
        logger.debug("github pr #%s commit lookup failed: %s", getattr(pr, "number", "?"), e)
        return []
    items: list[dict] = []
    for c in commits:
        commit = c.commit
        when = commit.author.date if commit.author else None
        if when is None or when.tzinfo is None or when < cutoff:
            continue
        full = commit.message or ""
        lines = full.splitlines()
        msg = lines[0] if lines else ""
        body = "\n".join(lines[1:]).strip()
        items.append(
            {
                "author": commit.author.name if commit.author else "",
                "author_email": (getattr(commit.author, "email", "") or "") if commit.author else "",
                "kind": "commit",
                "title": f"{msg} (PR #{pr.number})",
                "body": body,
                "timestamp": commit.author.date.isoformat()[:19],
                "key": c.sha[:8],
                "commit_id": c.sha,
                "url": getattr(c, "html_url", "") or "",
                # The parent PR event carries its complete changed-file scope;
                # avoid an API call for every branch commit.
                "changed_files": [],
            }
        )
    return items


def github_recent_prs(
    repo_url: str,
    days: int = 1,
    since=None,
    metadata_cache=None,
    *,
    include_changed_files: bool = True,
    exhaustive: bool = False,
) -> list[dict]:
    """Return pull requests updated since the window start, plus their branch commits.

    The window is ``since → now`` when ``since`` (tz-aware datetime) is given,
    else the last ``days`` days. Each PR item: {author, kind='pr', title, body,
    branch, status, timestamp, key(#num)} (``body`` is the PR description,
    ``branch`` the source branch name). For the newest in-window PRs (open or
    merged, capped at _MAX_PR_COMMIT_LOOKUPS × _MAX_COMMITS_PER_PR) the PR's
    branch commits are also emitted as kind='commit' items so unmerged
    feature-branch work is visible. ``exhaustive=True`` (the analysis path)
    lifts the branch-commit caps and additionally emits kind='review'/'comment'
    discussion items per PR; the default keeps the bounded standup behaviour —
    the standup collector fetches reviews separately via github_recent_reviews,
    so emitting them here would duplicate every review in the feed. Returns []
    on any error. Sorted by updated desc; stops once older than the window.
    """
    logger.info("github_recent_prs: repo=%r days=%d since=%s", repo_url, days, since)
    try:
        slug = _parse_repo(repo_url)
        repo = _get_github_client().get_repo(slug)
        cutoff = _since_dt(days, since)

        def _list_prs():
            # Always slice: without the cap this materialises EVERY PR the repo
            # has ever had (paged ~30/request) before the cutoff break can fire.
            pulls = repo.get_pulls(state="all", sort="updated", direction="desc")
            return list(pulls[:_MAX_REPO_PRS])

        prs = (
            metadata_cache.memoize(("github", "pull_requests", slug), _list_prs)
            if metadata_cache is not None
            else _list_prs()
        )
        items: list[dict] = []
        file_lookups = 0
        commit_lookups = 0
        for pr in prs:
            updated = pr.updated_at
            # updated_at may be naive; compare in UTC terms defensively.
            if updated is not None and updated.tzinfo is not None and updated < cutoff:
                break
            status = "merged" if pr.merged else pr.state
            ts = updated.isoformat()[:19] if updated else ""
            revision = updated.isoformat() if updated else str(getattr(pr, "head", "") or "")
            changed_files = (
                _github_changed_files(
                    pr,
                    metadata_cache=metadata_cache,
                    object_key=f"{slug}:#{pr.number}",
                    revision=revision,
                )
                if include_changed_files and file_lookups < _MAX_CHANGED_FILE_LOOKUPS
                else []
            )
            file_lookups += 1
            items.append(
                {
                    "author": pr.user.login if pr.user else "",
                    "kind": "pr",
                    "title": pr.title or "",
                    "body": getattr(pr, "body", "") or "",  # PR description — AI-drafted summaries / trailers live here
                    # Source branch — cloud agents (Codex, Copilot coding agent)
                    # name their branches "codex/…"/"copilot/…", a strong AI marker.
                    "branch": getattr(getattr(pr, "head", None), "ref", "") or "",
                    "status": status,
                    "timestamp": ts,
                    "key": f"#{pr.number}",
                    "pr_id": pr.number,
                    "url": getattr(pr, "html_url", "") or "",
                    "changed_files": changed_files,
                }
            )
            if exhaustive:
                try:
                    for review in pr.get_reviews():
                        submitted = getattr(review, "submitted_at", None)
                        if submitted and submitted.tzinfo is not None and submitted < cutoff:
                            continue
                        items.append(
                            {
                                "author": getattr(getattr(review, "user", None), "login", "") or "",
                                "kind": "review",
                                "title": f"Reviewed PR #{pr.number}: {pr.title or ''}",
                                "body": getattr(review, "body", "") or "",
                                "status": str(getattr(review, "state", "") or "").lower(),
                                "timestamp": submitted.isoformat()[:19] if submitted else ts,
                                "key": f"review:{getattr(review, 'id', '')}",
                                "pr_id": pr.number,
                                "url": getattr(review, "html_url", "") or getattr(pr, "html_url", "") or "",
                            }
                        )
                    for comment in pr.get_issue_comments():
                        updated_comment = getattr(comment, "updated_at", None)
                        if updated_comment and updated_comment.tzinfo is not None and updated_comment < cutoff:
                            continue
                        items.append(
                            {
                                "author": getattr(getattr(comment, "user", None), "login", "") or "",
                                "kind": "comment",
                                "title": f"Commented on PR #{pr.number}: {pr.title or ''}",
                                "body": getattr(comment, "body", "") or "",
                                "timestamp": updated_comment.isoformat()[:19] if updated_comment else ts,
                                "key": f"comment:{getattr(comment, 'id', '')}",
                                "pr_id": pr.number,
                                "url": getattr(comment, "html_url", "") or getattr(pr, "html_url", "") or "",
                            }
                        )
                except Exception as exc:
                    logger.debug("github PR #%s review/comment lookup failed: %s", pr.number, exc)
            if status in ("open", "merged") and (exhaustive or commit_lookups < _MAX_PR_COMMIT_LOOKUPS):
                commit_lookups += 1
                items.extend(_pr_branch_commit_items(pr, cutoff, limit=None if exhaustive else _MAX_COMMITS_PER_PR))
        logger.info("github_recent_prs: %d item(s) in last %d day(s)", len(items), days)
        return items
    except github.RateLimitExceededException:
        logger.warning("github_recent_prs skipped — rate limit reached")
        return []
    except Exception as e:
        _raise_if_github_auth(e)
        logger.warning("github_recent_prs failed: %s", e)
        return []


def github_recent_reviews(repo_url: str, days: int = 1, since=None, metadata_cache=None) -> list[dict]:
    """Return timestamped PR reviews and inline review comments in the window."""
    logger.info("github_recent_reviews: repo=%r days=%d since=%s", repo_url, days, since)
    try:
        slug = _parse_repo(repo_url)
        repo = _get_github_client().get_repo(slug)
        cutoff = _since_dt(days, since)

        def _list_prs():
            return list(repo.get_pulls(state="all", sort="updated", direction="desc")[:100])

        prs = (
            metadata_cache.memoize(("github", "pull_requests", slug), _list_prs)
            if metadata_cache is not None
            else _list_prs()
        )
        recent_prs = []
        for pr in prs:
            updated = pr.updated_at
            if updated is not None and updated.tzinfo is not None and updated < cutoff:
                break
            recent_prs.append(pr)

        def _reviews_for_pr(index_pr) -> list[dict]:
            index, pr = index_pr
            updated = pr.updated_at
            out: list[dict] = []
            try:
                revision = updated.isoformat() if updated else str(getattr(pr, "head", "") or "")
                changed_files = (
                    _github_changed_files(
                        pr,
                        metadata_cache=metadata_cache,
                        object_key=f"{slug}:#{pr.number}",
                        revision=revision,
                    )
                    if index < _MAX_CHANGED_FILE_LOOKUPS
                    else []
                )
                with _GITHUB_DETAIL_SEMAPHORE:
                    reviews = list(pr.get_reviews())
                for review in reviews:
                    submitted = getattr(review, "submitted_at", None)
                    if submitted is None or submitted.tzinfo is None or submitted < cutoff:
                        continue
                    user = getattr(review, "user", None)
                    state = (getattr(review, "state", "") or "reviewed").lower()
                    out.append(
                        {
                            "author": getattr(user, "login", "") or "",
                            "author_type": _author_type(user),
                            "kind": "review",
                            "title": f"{state} PR #{pr.number}: {pr.title or ''}",
                            "body": getattr(review, "body", "") or "",
                            "status": state,
                            "timestamp": submitted.isoformat()[:19],
                            "key": f"review-{getattr(review, 'id', '')}",
                            "url": getattr(review, "html_url", "") or getattr(pr, "html_url", "") or "",
                            "changed_files": changed_files,
                        }
                    )
                for comment in pr.get_review_comments():
                    created = getattr(comment, "created_at", None)
                    if created is None or created.tzinfo is None or created < cutoff:
                        continue
                    user = getattr(comment, "user", None)
                    # Appending to `out` (NOT the enclosing `items`, which is unbound
                    # until the pool.map below runs) — the old `items.append` raised
                    # NameError inside the worker and silently dropped every inline
                    # review comment via the broad except.
                    out.append(
                        {
                            "author": getattr(user, "login", "") or "",
                            "author_type": _author_type(user),
                            "kind": "review",
                            "title": f"reviewed code on PR #{pr.number}: {pr.title or ''}",
                            "body": getattr(comment, "body", "") or "",
                            "status": "commented",
                            "timestamp": created.isoformat()[:19],
                            "key": f"review-comment-{getattr(comment, 'id', '')}",
                            "url": getattr(comment, "html_url", "") or getattr(pr, "html_url", "") or "",
                            "changed_files": changed_files,
                        }
                    )
            except Exception as exc:
                logger.warning("github_recent_reviews: PR #%s failed: %s", getattr(pr, "number", "?"), exc)
            return out

        with ThreadPoolExecutor(
            max_workers=min(4, max(1, len(recent_prs))),
            thread_name_prefix="standup-github-reviews",
        ) as pool:
            items = [item for batch in pool.map(_reviews_for_pr, enumerate(recent_prs)) for item in batch]
        logger.info("github_recent_reviews: %d review event(s)", len(items))
        return items
    except github.RateLimitExceededException:
        logger.warning("github_recent_reviews skipped — rate limit reached")
        return []
    except Exception as exc:
        _raise_if_github_auth(exc)
        logger.warning("github_recent_reviews failed: %s", exc)
        return []


def github_changed_files(repo_url: str, activity: list[dict]) -> list[dict]:
    """Fetch files changed by already-scoped commits and authored PRs.

    This deliberately runs *after* member filtering. Commit files are
    high-confidence authored changes; whole-PR files are lower-confidence because
    a PR may contain commits from collaborators.
    """
    slug = _parse_repo(repo_url)
    repo = _get_github_client().get_repo(slug)
    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for item in activity:
        try:
            if item.get("kind") == "commit" and item.get("commit_id"):
                origin = str(item["commit_id"])
                files = getattr(repo.get_commit(origin), "files", ()) or ()
                attribution = "authored_commit"
                confidence = "high"
            elif item.get("kind") == "pr" and item.get("pr_id"):
                origin = f"pr:{item['pr_id']}"
                files = repo.get_pull(int(item["pr_id"])).get_files()
                attribution = "authored_pr"
                confidence = "medium"
            else:
                continue
            for changed in files:
                path = str(getattr(changed, "filename", "") or "")
                dedupe = (origin, path, attribution)
                if not path or dedupe in seen:
                    continue
                seen.add(dedupe)
                patch = getattr(changed, "patch", None)
                out.append(
                    {
                        "provider": "github",
                        "container": slug.split("/", 1)[0],
                        "repository": slug,
                        "path": path,
                        "status": str(getattr(changed, "status", "") or "modified"),
                        "additions": int(getattr(changed, "additions", 0) or 0),
                        "deletions": int(getattr(changed, "deletions", 0) or 0),
                        "patch": patch if isinstance(patch, str) else "",
                        "truncated": patch is None,
                        "author": item.get("author", ""),
                        "author_email": item.get("author_email", ""),
                        "attribution": attribution,
                        "confidence": confidence,
                        "change_id": origin,
                        "url": item.get("url", ""),
                        "error": "" if patch is not None else "provider did not return a text patch",
                    }
                )
        except Exception as exc:
            out.append(
                {
                    "provider": "github",
                    "container": slug.split("/", 1)[0],
                    "repository": slug,
                    "path": str(item.get("key", "unknown change")),
                    "status": "failed",
                    "author": item.get("author", ""),
                    "attribution": "authored_commit" if item.get("kind") == "commit" else "authored_pr",
                    "confidence": "high" if item.get("kind") == "commit" else "medium",
                    "change_id": str(item.get("commit_id") or f"pr:{item.get('pr_id', '')}"),
                    "url": item.get("url", ""),
                    "error": str(exc),
                }
            )
    return out
