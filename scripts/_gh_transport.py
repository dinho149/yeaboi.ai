#!/usr/bin/env python3
"""One GitHub transport for every script in ``scripts/``.

The scripts here that talk to GitHub — ``pr_feedback.py`` (the PR review gate)
foremost — used to do it with ``subprocess.run(["gh", ...])``.

That is fine on a developer's machine and fine on an Actions runner, and it is
broken in unattended cloud sessions, which are handed a GitHub *token* and no
CLI, so every one of those calls failed.

So: `gh` when it is there, the REST API with ``GH_TOKEN``/``GITHUB_TOKEN`` when
it is not, and one shared module rather than a copy per caller.

**stdlib only.** Nothing from ``src/yeaboi`` and nothing off PyPI, so every
caller stays runnable in a checkout with no environment built. That is a
constraint on this file specifically, not a general one.

Callers keep their own reporting. This module returns results and never prints:
``pr_feedback.py`` prints to stderr and exits non-zero, and a shared printer
would have to please every caller.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

GITHUB_API = "https://api.github.com"

# A page is 100 items and this many pages is 2000. Every collection this module
# reads — a repo's labels, a PR's comments, its review events — is far below
# that, so hitting the cap means something is wrong rather than large.
MAX_PAGES = 20

_TIMEOUT = 30


@dataclass(frozen=True)
class ApiResult:
    """A call's outcome, shaped like the ``returncode``/``stderr`` pair the `gh`
    branch inspects so both halves of a caller's operation read the same."""

    ok: bool
    data: object = None
    error: str = ""


# --- the CLI half ------------------------------------------------------------

# This module's single process seam — EVERY spawn in this file goes through it,
# `gh` and `git` alike, which is what makes replacing this one name total. Named
# so it can be replaced without touching
# the shared `subprocess` module — patching `subprocess.run` itself would patch it
# for every other test in the suite, which is the trap `tests/conftest.py`'s
# `_no_real_package_install` documents for the installer.
#
# It exists because the test suite reached the real `gh` once and wrote to the
# real repository: a migration test stubbed the REST seam and asserted on the
# calls, which was complete until the function under it grew a `gh` branch, and
# then `gh issue edit 7 --add-label …` plus four comments landed on a merged PR.
# `_no_real_gh_calls` blocks this name.
_run = subprocess.run


def gh(*args: str) -> subprocess.CompletedProcess[str]:
    """One `gh` call, reporting a missing binary the way `gh` reports a failure.

    127 rather than an exception, matching the shell: every caller branches on
    ``returncode`` already, and a `gh` call can now be reached on a machine with
    no `gh` at all — `resolve_slug()` answers from the git remote, so code that
    used to stop for want of a slug now runs on. A FileNotFoundError there is a
    traceback out of a script whose whole contract is to degrade with a remedy.
    """
    try:
        return _run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError as error:
        return subprocess.CompletedProcess(["gh", *args], 127, "", str(error))


def gh_ready() -> bool:
    """Whether the `gh` CLI is both installed and authenticated.

    Reports nothing: "no `gh`" is only a degradation once the REST transport has
    also declined, and which of the two answered is the caller's story to tell.
    """
    if shutil.which("gh") is None:
        return False
    return gh("auth", "status").returncode == 0


# --- the REST half -----------------------------------------------------------


def github_token() -> str | None:
    """The token the REST transport authenticates with.

    ``GH_TOKEN`` first, matching `gh`'s own precedence, so a machine that sets
    both gets the same identity either way. (``src/yeaboi/config.py`` reads only
    ``GITHUB_TOKEN`` — that is the library's environment, not this one.)
    """
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or None


# This module's HTTP seam, and the exact counterpart of `_run` above: every
# request in this file goes through it, which is what lets a guard replace one
# name and be total. `_run` was named for this reason after a test suite ran real
# `gh` writes against the real repository; the REST branch had the identical
# exposure and no seam to block, so `tests/conftest.py` could guard the CLI half
# and nothing at all of the half that a routine session actually uses.
_urlopen = urllib.request.urlopen


def api(method: str, path: str, body: dict | None = None) -> ApiResult:
    """One REST call against `GITHUB_API`. Never raises; never logs the token.

    The URL is a literal scheme and host with a caller-supplied path appended,
    so the S310 audit — "check for permitted schemes" — has a fixed answer here.
    """
    token = github_token()
    if not token:
        return ApiResult(False, error="no GH_TOKEN or GITHUB_TOKEN in the environment")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "yeaboi-scripts",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(  # noqa: S310 - GITHUB_API is a literal https:// host
        f"{GITHUB_API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
    )
    try:
        with _urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310 - see above
            raw = response.read().decode()
        parsed = json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as error:
        # The body carries GitHub's own message — "Resource not accessible by
        # integration" for a scope gap, which is the failure a token-shaped setup
        # actually hits and the one worth reading. The request headers carry the
        # token and are never surfaced.
        try:
            detail = error.read().decode()[:400]
        except Exception:
            detail = ""
        return ApiResult(False, error=f"HTTP {error.code} on {method} {path}" + (f": {detail}" if detail else ""))
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return ApiResult(False, error=f"{method} {path} failed: {error}")
    except (ValueError, UnicodeDecodeError) as error:
        # A 2xx body that is not JSON is what an egress proxy's HTML error page
        # looks like — a plausible shape in exactly the cloud session this
        # transport exists for. A failure, not a traceback.
        return ApiResult(False, error=f"{method} {path} returned a body that is not JSON: {error}")
    return ApiResult(True, parsed)


def api_paged(path: str, key: str | None = None) -> ApiResult:
    """Every page of a list endpoint, concatenated.

    `gh` was asked for one oversized page (``--limit 200``) or told to
    ``--paginate``. REST caps a page at 100 and pages explicitly, so this does.

    ``key`` names the wrapper field for endpoints answering with an object
    rather than a bare array — ``/actions/variables`` is the one that does.
    """
    items: list = []
    joiner = "&" if "?" in path else "?"
    for page in range(1, MAX_PAGES + 1):
        result = api("GET", f"{path}{joiner}per_page=100&page={page}")
        if not result.ok:
            return result
        batch = result.data
        if key is not None and isinstance(batch, dict):
            batch = batch.get(key)
        if not isinstance(batch, list):
            return ApiResult(False, error=f"GET {path} returned no list of results")
        items.extend(batch)
        if len(batch) < 100:
            return ApiResult(True, items)
    # Ran out of pages with a full one still in hand. Reported as a failure
    # rather than returned short: callers turn a partial list into "these do not
    # exist", which is the "an empty set read as truth" trap in a slower form.
    return ApiResult(False, error=f"GET {path} did not end within {MAX_PAGES} pages")


def graphql(query: str, variables: dict) -> ApiResult:
    """One GraphQL call, through whichever transport this machine has.

    GraphQL is not optional to support: `pr_feedback.py` reads review *threads*,
    and whether a thread is resolved exists in the v4 schema and nowhere in v3.

    Unlike the REST helpers this one routes itself, because `gh api graphql`
    takes a shape (`-f query=…`, `-F var=…`) that no caller should have to know.
    Errors in the payload are failures here: a GraphQL error arrives inside a
    200, so a caller checking only the status code would read a half-answer as a
    whole one.
    """
    if gh_available():
        args = ["api", "graphql", "-f", f"query={query}"]
        for name, value in variables.items():
            args += ["-F", f"{name}={value}"]
        result = gh(*args)
        if result.returncode != 0:
            return ApiResult(False, error=result.stderr.strip() or "unknown gh error")
        try:
            payload = json.loads(result.stdout or "null")
        except json.JSONDecodeError:
            return ApiResult(False, error="gh api graphql returned non-JSON")
    else:
        answer = api("POST", "/graphql", {"query": query, "variables": variables})
        if not answer.ok:
            return answer
        payload = answer.data
    if not isinstance(payload, dict):
        return ApiResult(False, error="graphql returned no object")
    if payload.get("errors"):
        first = payload["errors"][0] if isinstance(payload["errors"], list) and payload["errors"] else {}
        message = first.get("message") if isinstance(first, dict) else ""
        return ApiResult(False, error=f"graphql: {message or 'query rejected'}")
    return ApiResult(True, payload)


def gh_available() -> bool:
    """Whether `gh` is on PATH at all.

    Deliberately not `gh_ready()`: this is the question "which shape should the
    request take", and an installed-but-logged-out `gh` still fails in a way the
    caller reports, whereas guessing REST for it would send the request with a
    different identity than the rest of the run.
    """
    return shutil.which("gh") is not None


# --- who we are talking about ------------------------------------------------

# `resolve_slug()`'s memo. A distinct sentinel, because None is a real answer —
# "this checkout has no GitHub remote" — and caching a miss matters as much as
# caching a hit: without it, every miss re-runs the whole lookup.
_UNRESOLVED = object()
_SLUG: object = _UNRESOLVED


def reset_slug_cache() -> None:
    """Forget the memo. For a long-lived process — the test suite — that must not
    carry one run's repository into the next."""
    global _SLUG
    _SLUG = _UNRESOLVED


def resolve_slug(root: Path | None = None) -> str | None:
    """``owner/name`` for the repository a checkout points at, resolved once.

    Three sources, cheapest first and independent of transport, because the
    transport selection itself needs the answer. `gh` when it is there, the
    ``GITHUB_REPOSITORY`` a workflow exports, and otherwise the `origin` remote.

    The remote is what makes the REST transport work in a routine session: step
    1 of `cron/cd-deploy.md` runs `git fetch origin main`, so a remote is
    guaranteed there even though `gh` is not.
    """
    global _SLUG
    if _SLUG is not _UNRESOLVED:
        return _SLUG  # type: ignore[return-value]
    _SLUG = _resolve_slug(root)
    return _SLUG  # type: ignore[return-value]


def _resolve_slug(root: Path | None) -> str | None:
    if gh_available():
        result = gh("repo", "view", "--json", "nameWithOwner")
        if result.returncode == 0:
            try:
                slug = json.loads(result.stdout or "{}").get("nameWithOwner")
            except json.JSONDecodeError:
                slug = None
            if slug:
                return slug
    from_env = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if from_env.count("/") == 1:
        return from_env
    return _slug_from_remote(root)


# The host is *anchored*, not searched for. `"github.com" in url` also matches
# `https://github.com.example.net/o/n`, which is a different host entirely — the
# slug parsed out of it would then be sent, with this machine's token, to
# whatever `api.github.com` says it is. CodeQL calls this incomplete URL
# substring sanitization and is right to.
#
# Both forms GitHub hands out: `git@github.com:owner/name.git` and
# `https://github.com/owner/name(.git)`, with or without the suffix, plus the
# `ssh://` spelling of the first.
_REMOTE = re.compile(
    r"^(?:https://|ssh://(?:[^@/]+@)?|(?:[^@/]+@))?github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?/?$"
)


def _slug_from_remote(root: Path | None) -> str | None:
    """``owner/name`` parsed out of `git remote get-url origin`."""
    argv = ["git"] + (["-C", str(root)] if root else []) + ["remote", "get-url", "origin"]
    try:
        result = _run(argv, capture_output=True, text=True, check=False)  # noqa: S603 - literal argv
    except OSError:
        return None
    if result.returncode != 0:
        return None
    match = _REMOTE.match(result.stdout.strip())
    return match.group("slug") if match else None


def segment(name: str) -> str:
    """One path segment, escaped.

    Label names carry a `:` (``workstream:security``, ``type:bug``), which is why
    this is not optional — and it is applied to every segment rather than only
    the ones needing it today, so a name that starts needing it later does not
    become a 404 nobody expects.
    """
    return urllib.parse.quote(name, safe="")
