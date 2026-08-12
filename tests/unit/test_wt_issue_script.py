"""Tests for scripts/wt-issue.sh — GitHub-issue-driven worktree creation.

The script resolves an issue number to a branch (linked branches first, then the
head branch of a same-repo PR that closes the issue) and delegates to wt.sh.
Like test_wt_script.py, each test runs the real script against a throwaway bare
origin + clone; `gh` is stubbed on PATH and driven by files in $GH_STUB_DIR so
each case controls exactly what the "API" returns — including the failure modes
(`gh` exiting non-zero must fall through to guidance, never abort silently).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

GH_STUB = """#!/bin/sh
# Minimal gh stub: canned answers from $GH_STUB_DIR; absent file = gh failure.
case "$1 $2" in
  "auth status") exit 0 ;;
  "repo view") echo "acme/demo" ;;
  "issue view") echo "stub title" ;;
  "issue develop") [ -f "$GH_STUB_DIR/develop_list" ] || exit 1; cat "$GH_STUB_DIR/develop_list" ;;
  "api graphql") [ -f "$GH_STUB_DIR/graphql" ] || exit 1; cat "$GH_STUB_DIR/graphql" ;;
  *) exit 1 ;;
esac
"""


def _git(repo: Path, *args: str, env: dict[str, str], check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        env=env,
        check=check,
    )
    return result.stdout.strip()


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """Isolated git env + stubbed `uv` and `gh` on PATH (see test_wt_script.py)."""
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in [("uv", "#!/bin/sh\nexit 0\n"), ("gh", GH_STUB)]:
        stub = bin_dir / name
        stub.write_text(body)
        stub.chmod(0o755)
    stub_dir = tmp_path / "gh-stub"
    stub_dir.mkdir()
    return {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GH_STUB_DIR": str(stub_dir),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.com",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.com",
        "GIT_TERMINAL_PROMPT": "0",
    }


@pytest.fixture
def repo(tmp_path: Path, env: dict[str, str]) -> Path:
    """Bare origin + clone holding wt.sh AND wt-issue.sh, plus a remote-only
    branch `feat-i` (the branch the stubbed issue resolves to)."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, env=env)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, env=env)

    scripts = work / "scripts"
    scripts.mkdir()
    shutil.copy(SCRIPTS / "wt.sh", scripts / "wt.sh")
    shutil.copy(SCRIPTS / "wt-issue.sh", scripts / "wt-issue.sh")
    (work / "f.txt").write_text("one\n")
    _git(work, "add", "-A", env=env)
    _git(work, "commit", "-qm", "one", env=env)
    _git(work, "push", "-q", "-u", "origin", "main", env=env)
    (work / ".git" / "info" / "exclude").write_text(".claude/worktrees/\n")

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, env=env)
    _git(other, "checkout", "-qb", "feat-i", env=env)
    (other / "i.txt").write_text("issue work\n")
    _git(other, "add", "-A", env=env)
    _git(other, "commit", "-qm", "issue work", env=env)
    _git(other, "push", "-q", "origin", "feat-i", env=env)
    return work


def _run(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/wt-issue.sh", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def _stub_file(env: dict[str, str], name: str, content: str) -> None:
    (Path(env["GH_STUB_DIR"]) / name).write_text(content)


class TestResolution:
    def test_single_linked_branch_creates_tracking_worktree(self, repo: Path, env: dict[str, str]) -> None:
        _stub_file(env, "develop_list", "feat-i\thttps://github.com/acme/demo/tree/feat-i\n")

        result = _run(repo, env, "7", "headless")

        assert result.returncode == 0, result.stderr
        assert "issue #7" in result.stdout and "'feat-i'" in result.stdout
        assert (repo / ".claude" / "worktrees" / "feat-i").is_dir()
        assert _git(repo, "config", "--get", "branch.feat-i.merge", env=env) == "refs/heads/feat-i"

    def test_pr_fallback_used_when_gh_develop_fails(self, repo: Path, env: dict[str, str]) -> None:
        """`gh issue develop` exiting non-zero must fall through, not abort under set -e."""
        _stub_file(env, "graphql", "feat-i\n")

        result = _run(repo, env, "7", "headless")

        assert result.returncode == 0, result.stderr
        assert (repo / ".claude" / "worktrees" / "feat-i").is_dir()

    def test_multiple_candidates_are_listed_and_fail(self, repo: Path, env: dict[str, str]) -> None:
        _stub_file(env, "develop_list", "feat-i\turl\nfeat-j\turl\n")

        result = _run(repo, env, "7", "headless")

        assert result.returncode == 1
        assert "2 candidate branches" in result.stderr
        assert "feat-i" in result.stderr and "feat-j" in result.stderr

    def test_no_branch_anywhere_fails_with_guidance(self, repo: Path, env: dict[str, str]) -> None:
        """Both gh calls failing outright still ends in the friendly message."""
        result = _run(repo, env, "7", "headless")

        assert result.returncode == 1
        assert "no linked branch" in result.stderr
        assert "gh issue develop 7" in result.stderr

    def test_branch_absent_from_origin_fails_clearly(self, repo: Path, env: dict[str, str]) -> None:
        """A linked branch living in a fork must not become a re-cut from main."""
        _stub_file(env, "develop_list", "ghost-branch\turl\n")

        result = _run(repo, env, "7", "headless")

        assert result.returncode == 1
        assert "does not exist on origin" in result.stderr
        assert not (repo / ".claude" / "worktrees" / "ghost-branch").exists()


class TestArgumentValidation:
    def test_rejects_non_numeric_issue(self, repo: Path, env: dict[str, str]) -> None:
        result = _run(repo, env, "abc", "headless")

        assert result.returncode == 1
        assert "not an issue number" in result.stderr

    def test_rejects_destructive_action(self, repo: Path, env: dict[str, str]) -> None:
        """Only open/headless pass through — `rm` must never reach wt.sh."""
        result = _run(repo, env, "7", "rm")

        assert result.returncode == 1
        assert "must be 'open' or 'headless'" in result.stderr
