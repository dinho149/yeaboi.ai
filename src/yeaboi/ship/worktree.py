"""Isolated git worktrees for supervised coding-agent runs.

Behaviors ported from ruflo's worktree coordinator (MIT), re-implemented for
yeaboi — each one was a production scar upstream:

- **IDs are whitelisted, never escaped** (``^[a-z0-9][a-z0-9._-]{0,63}$``): a
  run id reaches branch names, directory names and registry keys, and escaping
  three grammars correctly is harder than refusing one.
- **A dirty target repo is refused**: the agent's diff must be attributable to
  the agent; pre-existing uncommitted work would be swept into its branch.
- **Prepare is idempotent**: re-preparing an existing run id returns the
  existing record rather than stacking a second worktree.
- **Partial failure rolls back** (best-effort, each removal individually
  swallowed so one stuck worktree doesn't strand the rest).
- **The registry is re-validated before any delete**: a tampered registry must
  not be able to point ``remove()`` at an arbitrary path.

One deliberate divergence: the checkout lives under yeaboi's own data root
(``paths.SHIP_WORKTREES_DIR``), not beside the target repo. The fs sandbox
already allows that tree read-write, so yeaboi-side reads and writes of the
agent's workspace need no extra consent — only the *target repo* does (its
``.git`` gains a worktree entry), which keeps the consent prompt honest: one
grant, for the one directory the user named.

Every git subprocess passes ``env=git_subprocess_env()`` — see local_git.py:
inherited ``GIT_*`` vars from an enclosing hook would silently retarget a
mutating command at the wrong repository.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from yeaboi.paths import SHIP_WORKTREE_REGISTRY, SHIP_WORKTREES_DIR, _safe_key, get_ship_dir
from yeaboi.tools.local_git import git_subprocess_env

logger = logging.getLogger(__name__)

# Whitelist, not an escape: applied to run ids before they reach branch names,
# directory names, or registry keys.
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_GIT_TIMEOUT_S = 60


class WorktreeError(RuntimeError):
    """A worktree operation failed; the message is user-facing and names why."""


@dataclass(frozen=True)
class WorktreeRecord:
    """One prepared worktree: where it is, whose repo it came from."""

    run_id: str = ""
    repo: str = ""  # resolved toplevel of the target repository
    path: str = ""  # the agent's checkout
    branch: str = ""  # ship/<run-id> in the target repo
    base_sha: str = ""  # the commit the branch was cut from


def assert_safe_id(run_id: str) -> str:
    """Return *run_id* if it matches the whitelist, else raise WorktreeError."""
    if not SAFE_ID_RE.match(run_id or ""):
        raise WorktreeError(
            f"unsafe run id {run_id!r}: must match {SAFE_ID_RE.pattern} (lowercase, digits, . _ -, max 64 chars)"
        )
    return run_id


def _git(repo: Path | str, *args: str) -> str:
    """Run one git command against *repo*; raise WorktreeError with the tail."""
    argv = ["git", "-C", str(repo), *args]
    try:
        proc = subprocess.run(  # noqa: S603 — fixed binary, whitelisted-id args
            argv,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
            env=git_subprocess_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError(f"git {' '.join(args[:2])} failed: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        raise WorktreeError(f"git {' '.join(args[:2])} failed: {tail}")
    return proc.stdout.strip()


def resolve_repo(repo: Path | str) -> Path:
    """Resolve *repo* to its toplevel; refuse anything that is not a work tree."""
    top = _git(repo, "rev-parse", "--show-toplevel")
    if not top:
        raise WorktreeError(f"not a git work tree: {repo}")
    return Path(top)


def _read_registry() -> dict:
    try:
        data = json.loads(SHIP_WORKTREE_REGISTRY.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        logger.warning("Worktree registry is corrupt; treating as empty")
        return {}
    return data if isinstance(data, dict) else {}


def _write_registry(data: dict) -> None:
    get_ship_dir()
    tmp = SHIP_WORKTREE_REGISTRY.with_name(f"{SHIP_WORKTREE_REGISTRY.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(SHIP_WORKTREE_REGISTRY)


def _checkout_path(repo_top: Path, run_id: str) -> Path:
    return SHIP_WORKTREES_DIR / _safe_key(repo_top.name, "repo") / run_id


def _validate_owned(path_str: str) -> Path:
    """A registry path must live under our worktree root before we touch it.

    Symlink-following containment, never string prefix — the same rule
    fs_policy applies (``/root-evil`` must not pass for ``/root``).
    """
    resolved = Path(path_str).expanduser().resolve(strict=False)
    root = SHIP_WORKTREES_DIR.expanduser().resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise WorktreeError(f"registry path escapes the owned worktree root: {path_str}")
    return resolved


def _branch_exists(repo: Path | str, branch: str) -> bool:
    try:
        _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    except WorktreeError:
        return False
    return True


def is_dirty(repo: Path | str) -> bool:
    """Whether the target repo has uncommitted changes (untracked included)."""
    return bool(_git(repo, "status", "--porcelain"))


def prepare(run_id: str, repo: Path | str, *, base_ref: str = "HEAD") -> WorktreeRecord:
    """Create (or return the existing) worktree + branch for *run_id*.

    Raises WorktreeError on an unsafe id, a non-repo, a dirty repo, or a git
    failure — after rolling back anything half-created.
    """
    assert_safe_id(run_id)
    repo_top = resolve_repo(repo)
    registry = _read_registry()
    existing = registry.get(run_id)
    if isinstance(existing, dict) and existing.get("path"):
        # Idempotent: a re-prepare returns the record instead of stacking.
        logger.info("Worktree for %s already prepared at %s", run_id, existing.get("path"))
        return WorktreeRecord(**{f.name: str(existing.get(f.name, "")) for f in fields(WorktreeRecord)})
    if is_dirty(repo_top):
        raise WorktreeError(
            f"refusing to prepare a worktree from a dirty repository: {repo_top} "
            "(commit or stash first — the agent's diff must be attributable to the agent)"
        )
    base_sha = _git(repo_top, "rev-parse", base_ref)
    branch = f"ship/{run_id}"
    # Refuse, don't reuse: a pre-existing branch with this name belongs to a
    # previous run (a lost registry, a hand-pruned worktree) and may hold
    # unpushed commits. Failing here also keeps the rollback below honest —
    # it only ever deletes a branch this call created.
    if _branch_exists(repo_top, branch):
        raise WorktreeError(
            f"branch {branch} already exists in {repo_top} — a previous run left it; "
            "delete it (or push it) and retry, or use a different run id"
        )
    checkout = _checkout_path(repo_top, run_id)
    checkout.parent.mkdir(parents=True, exist_ok=True)
    try:
        _git(repo_top, "worktree", "add", "-b", branch, str(checkout), base_sha)
    except WorktreeError:
        # Roll back whatever half-landed; keep the failure that mattered.
        try:
            _git(repo_top, "worktree", "remove", "--force", str(checkout))
        except WorktreeError:
            pass  # retain for manual recovery
        try:
            _git(repo_top, "branch", "-D", branch)
        except WorktreeError:
            pass
        raise
    record = WorktreeRecord(run_id=run_id, repo=str(repo_top), path=str(checkout), branch=branch, base_sha=base_sha)
    registry[run_id] = asdict(record)
    _write_registry(registry)
    logger.info("Prepared worktree %s (branch %s, base %s)", checkout, branch, base_sha[:12])
    return record


def get_record(run_id: str) -> WorktreeRecord | None:
    """The registry record for *run_id*, or None."""
    entry = _read_registry().get(run_id)
    if not isinstance(entry, dict):
        return None
    return WorktreeRecord(**{f.name: str(entry.get(f.name, "")) for f in fields(WorktreeRecord)})


def remove(run_id: str, *, delete_branch: bool = False) -> bool:
    """Remove *run_id*'s worktree (and optionally its branch). True on success.

    The branch survives by default: after a PR is opened it is the record of
    the run, and deleting it is a separate, explicit decision.
    """
    assert_safe_id(run_id)
    registry = _read_registry()
    entry = registry.get(run_id)
    if not isinstance(entry, dict):
        return False
    path = str(entry.get("path", ""))
    repo = str(entry.get("repo", ""))
    if not (path and repo):
        # A hand-edited registry can name a run with no checkout behind it.
        # Popping the row and reporting success would claim a removal that
        # never happened, so say what is true: nothing was removed.
        logger.warning("Registry entry for %s names no worktree; nothing removed", run_id)
        return False
    ok = True
    try:
        checkout = _validate_owned(path)
        _git(repo, "worktree", "remove", "--force", str(checkout))
        _git(repo, "worktree", "prune")
    except WorktreeError as exc:
        logger.warning("Could not remove worktree for %s: %s", run_id, exc)
        ok = False
    if ok and delete_branch and repo and entry.get("branch"):
        try:
            _git(repo, "branch", "-D", str(entry["branch"]))
        except WorktreeError as exc:
            logger.warning("Could not delete branch for %s: %s", run_id, exc)
    if ok:
        registry.pop(run_id, None)
        _write_registry(registry)
    return ok
