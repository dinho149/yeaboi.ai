"""Local git activity — a lightweight recent-commit reader for Daily Standup mode.

Unlike the other tool modules (github/jira/azdo) this needs no SDK or credentials:
it shells out to ``git log`` in a local working copy. Used by the standup collector
to round out the activity picture when the team's code lives in a local clone.

Plain functions (not @tool) — the standup collector calls them directly. They
degrade gracefully to [] on any error so a standup never crashes.

# See docs: "Daily Standup" — recent-activity collection
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def git_subprocess_env() -> dict[str, str]:
    """Environment for spawned git commands with repo-targeting vars removed.

    When this process itself runs inside a git hook (e.g. the pre-commit test
    stage), git exports GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE to the hook.
    A child ``git -C <path> …`` inherits them and silently targets the *hook's*
    repository instead of ``<path>`` — for a mutating command that can corrupt
    the outer repo's index. Every git subprocess in this codebase (and in
    tests) must pass ``env=git_subprocess_env()``.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


# Unit-separator delimited format: full SHA, author name, author email, ISO date,
# subject, body. %x1f is the ASCII unit separator (field delimiter) — safe against
# commit messages containing commas/pipes/tabs. %x1e (record separator) terminates each
# commit so the multi-line body (%b, where Co-Authored-By / AI-tool trailers live) can't
# be confused with the next record. The SHA (%H) gives each commit a unique key (used for
# dedup and to reference/link the commit); the email lets the standup engine match this
# commit to a tracker member whose display name differs from the git author name.
_LOG_FORMAT = "%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1e"


def _origin_commit_url_base(repo_path: str) -> str:
    """Best-effort web URL base for the repo's ``origin`` remote, or "".

    Reads ``remote.origin.url`` and normalizes the common forms so a caller can
    build ``f"{base}/commit/{sha}"``:
    - ``git@github.com:owner/repo(.git)``      → ``https://github.com/owner/repo``
    - ``https://github.com/owner/repo(.git)``  → ``https://github.com/owner/repo``
    - ``https://dev.azure.com/org/proj/_git/repo`` → unchanged (Azure commit path is
      ``/commit/<sha>`` under the repo)

    Returns "" for an unknown host or any failure — the caller then falls back to a
    SHA-only reference. Never raises.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(Path(repo_path).expanduser()), "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=git_subprocess_env(),
        )
    except (FileNotFoundError, subprocess.SubprocessError) as e:  # pragma: no cover - defensive
        logger.debug("origin-url lookup failed to run git: %s", e)
        return ""
    if proc.returncode != 0:
        return ""
    raw = proc.stdout.strip()
    if not raw:
        return ""
    # git@host:owner/repo(.git) → https://host/owner/repo
    m = re.match(r"^git@([^:]+):(.+?)(?:\.git)?/?$", raw)
    if m:
        host, path = m.group(1), m.group(2)
        if "github.com" in host or "dev.azure.com" in host or "visualstudio.com" in host:
            return f"https://{host}/{path}"
        return ""
    # https/http URL → strip trailing .git and credentials in userinfo
    m = re.match(r"^https?://(?:[^@/]+@)?(.+?)(?:\.git)?/?$", raw)
    if m:
        rest = m.group(1)
        if any(h in rest for h in ("github.com", "dev.azure.com", "visualstudio.com")):
            return f"https://{rest}"
    return ""


def local_git_recent_commits(repo_path: str, days: int = 1, since=None) -> list[dict]:
    """Return commits in ``repo_path`` authored since the window start.

    The window is ``since → now`` when ``since`` (a datetime) is given — git
    parses the ISO form directly — else the last ``days`` days. Each item:
    {author, kind='commit', title, body, timestamp, key(short sha), url}. ``body``
    is the commit message body (where Co-Authored-By / AI-tool trailers appear),
    empty when the commit has no body. ``url`` is a best-effort web link derived
    from the ``origin`` remote (empty when the remote host is unknown; a derived
    link may 404 for a commit that isn't pushed). Returns [] when the path is not a
    git repo, git is unavailable, or the command fails.
    """
    logger.info("local_git_recent_commits: repo_path=%r days=%d since=%s", repo_path, days, since)
    if not repo_path:
        return []
    path = Path(repo_path).expanduser()
    if not path.is_dir():
        logger.warning("local_git_recent_commits skipped — not a directory: %s", path)
        return []
    since_arg = f"--since={since.isoformat()}" if since is not None else f"--since={int(days)} days ago"
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "log",
                since_arg,
                f"--pretty=format:{_LOG_FORMAT}",
                "--no-merges",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=git_subprocess_env(),
        )
    except (FileNotFoundError, subprocess.SubprocessError) as e:
        logger.warning("local_git_recent_commits failed to run git: %s", e)
        return []

    if proc.returncode != 0:
        # Non-zero: not a repo, bad path, etc. stderr is logged, not raised.
        logger.warning("local_git_recent_commits: git exited %d: %s", proc.returncode, proc.stderr.strip()[:200])
        return []

    # Derive the remote web-URL base once (best-effort); "" → SHA-only references.
    # Skip the extra git call entirely when there are no commits to link.
    url_base = _origin_commit_url_base(str(path)) if proc.stdout.strip() else ""

    items: list[dict] = []
    # Records are %x1e-terminated (bodies are multi-line, so we can't split on newlines).
    for record in proc.stdout.split("\x1e"):
        if not record.strip():
            continue
        # maxsplit=5 → the body (last field) keeps any embedded %x1f-free newlines.
        parts = record.lstrip("\n").split("\x1f", 5)
        if len(parts) < 5:
            continue
        sha, author, email, iso_date, subject = parts[0], parts[1], parts[2], parts[3], parts[4]
        body = parts[5].strip() if len(parts) > 5 else ""
        items.append(
            {
                "author": author,
                "author_email": email,
                "kind": "commit",
                "title": subject,
                "body": body,
                "timestamp": iso_date[:19],
                "key": sha[:8],
                "url": f"{url_base}/commit/{sha}" if url_base and sha else "",
            }
        )
    logger.info("local_git_recent_commits: %d commit(s) in last %d day(s)", len(items), days)
    return items
