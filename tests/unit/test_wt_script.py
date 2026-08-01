"""Tests for scripts/wt.sh — the git-worktree provisioning script behind `make wt-new`.

The script's job is to cut every new feature branch from the *freshly fetched*
upstream default branch, so a stale main checkout cannot hand a worktree an old
base. That guarantee, and the four fallbacks around it, are only observable by
running the real script against real git repositories — so each test builds a
throwaway bare "origin" plus a clone in tmp_path and inspects the resulting refs.

Provisioning (uv venv, editable install, pre-commit) is stubbed out with a fake
`uv` on PATH: it is not what these tests are about, and a real install would put
a minute on every case.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

WT_SH = Path(__file__).resolve().parents[2] / "scripts" / "wt.sh"


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
    """Git env isolated from the developer's real config, hooks and credentials.

    Every inherited GIT_* variable is dropped, not just overridden. Under the
    pre-commit hook the suite runs with GIT_INDEX_FILE / GIT_DIR / GIT_WORK_TREE
    pointing at the *real* repository, and `git -C <tmp> add -A` obeys those over
    -C: the temp repo's tree lands in the real index, recording every real file
    as deleted. Inheriting the environment here is not a tidiness question.
    """
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # Fake uv: `uv venv`, `uv pip install`, `uv run pre-commit install` all no-op.
    stub = bin_dir / "uv"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    return {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "HOME": str(home),
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
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
    """A bare origin + a clone of it holding scripts/wt.sh, both on `main`.

    Returns the clone (the "main checkout" the script operates against).
    """
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, env=env)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, env=env)

    scripts = work / "scripts"
    scripts.mkdir()
    shutil.copy(WT_SH, scripts / "wt.sh")
    (work / "f.txt").write_text("one\n")
    _git(work, "add", "-A", env=env)
    _git(work, "commit", "-qm", "one", env=env)
    _git(work, "push", "-q", "-u", "origin", "main", env=env)
    # Worktrees are gitignored in the real repo; mirror that so the clone reads
    # as clean once one exists.
    (work / ".git" / "info" / "exclude").write_text(".claude/worktrees/\n")
    return work


def _push_upstream_commit(tmp_path: Path, env: dict[str, str], branch: str = "main") -> str:
    """Land a commit on origin/<branch> from a second clone, leaving `repo` stale."""
    other = tmp_path / f"other-{branch}"
    subprocess.run(["git", "clone", "-q", "-b", branch, str(tmp_path / "origin.git"), str(other)], check=True, env=env)
    (other / "f2.txt").write_text("two\n")
    _git(other, "add", "-A", env=env)
    _git(other, "commit", "-qm", "two", env=env)
    _git(other, "push", "-q", "origin", branch, env=env)
    return _git(other, "rev-parse", "HEAD", env=env)


def _run_wt(repo: Path, name: str, env: dict[str, str], action: str = "headless") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "scripts/wt.sh", name, action],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


class TestBranchBase:
    """The core guarantee: a new branch starts at the latest upstream default."""

    def test_new_branch_starts_at_origin_main_when_local_is_stale(
        self, tmp_path: Path, repo: Path, env: dict[str, str]
    ) -> None:
        upstream = _push_upstream_commit(tmp_path, env)
        local_before = _git(repo, "rev-parse", "HEAD", env=env)
        assert local_before != upstream  # the clone really is behind

        result = _run_wt(repo, "feat-a", env)

        assert result.returncode == 0, result.stderr
        assert _git(repo, "rev-parse", "feat-a", env=env) == upstream

    def test_new_branch_has_no_upstream(self, tmp_path: Path, repo: Path, env: dict[str, str]) -> None:
        """--no-track: otherwise a bare `git push` in the worktree aims at main."""
        _push_upstream_commit(tmp_path, env)
        _run_wt(repo, "feat-a", env)

        tracking = subprocess.run(
            ["git", "-C", str(repo), "config", "--get", "branch.feat-a.merge"],
            capture_output=True,
            text=True,
            env=env,
        )
        assert tracking.stdout.strip() == ""

    def test_honours_a_default_branch_that_is_not_main(self, tmp_path: Path, repo: Path, env: dict[str, str]) -> None:
        """origin/HEAD is asked for, not assumed — a wrong guess means a stale base."""
        origin = tmp_path / "origin.git"
        _git(repo, "branch", "-q", "trunk", env=env)
        _git(repo, "push", "-q", "origin", "trunk", env=env)
        _git(origin, "symbolic-ref", "HEAD", "refs/heads/trunk", env=env)
        upstream = _push_upstream_commit(tmp_path, env, branch="trunk")
        # Unset locally, as on clones built with `git init` + `git remote add`.
        _git(repo, "remote", "set-head", "origin", "-d", env=env, check=False)

        result = _run_wt(repo, "feat-a", env)

        assert result.returncode == 0, result.stderr
        assert _git(repo, "rev-parse", "feat-a", env=env) == upstream

    def test_no_origin_remote_falls_back_to_head(self, repo: Path, env: dict[str, str]) -> None:
        _git(repo, "remote", "remove", "origin", env=env)
        head = _git(repo, "rev-parse", "HEAD", env=env)

        result = _run_wt(repo, "solo", env)

        assert result.returncode == 0, result.stderr
        assert "no 'origin' remote" in result.stdout
        assert _git(repo, "rev-parse", "solo", env=env) == head


class TestExistingBranch:
    """Provisioning must never rewrite history that already exists."""

    def test_existing_branch_is_reused_untouched(self, tmp_path: Path, repo: Path, env: dict[str, str]) -> None:
        old = _git(repo, "rev-parse", "HEAD", env=env)
        _git(repo, "branch", "old-feat", old, env=env)
        _push_upstream_commit(tmp_path, env)

        result = _run_wt(repo, "old-feat", env)

        assert result.returncode == 0, result.stderr
        assert _git(repo, "rev-parse", "old-feat", env=env) == old
        assert "1 commit(s) behind" in result.stdout


class TestLocalDefaultSync:
    """The main checkout's own default branch is a convenience, never a risk."""

    def test_clean_checkout_is_fast_forwarded(self, tmp_path: Path, repo: Path, env: dict[str, str]) -> None:
        upstream = _push_upstream_commit(tmp_path, env)

        result = _run_wt(repo, "feat-a", env)

        assert "fast-forwarded 'main'" in result.stdout
        assert _git(repo, "rev-parse", "main", env=env) == upstream

    def test_uncommitted_changes_leave_the_checkout_alone(
        self, tmp_path: Path, repo: Path, env: dict[str, str]
    ) -> None:
        upstream = _push_upstream_commit(tmp_path, env)
        local_before = _git(repo, "rev-parse", "HEAD", env=env)
        (repo / "f.txt").write_text("edited\n")

        result = _run_wt(repo, "feat-a", env)

        assert "uncommitted changes" in result.stdout
        assert _git(repo, "rev-parse", "main", env=env) == local_before
        assert (repo / "f.txt").read_text() == "edited\n"
        # …and the new branch still starts at the upstream tip.
        assert _git(repo, "rev-parse", "feat-a", env=env) == upstream

    def test_untracked_files_do_not_block_the_fast_forward(
        self, tmp_path: Path, repo: Path, env: dict[str, str]
    ) -> None:
        """A real checkout always has untracked junk; counting it would kill this path."""
        upstream = _push_upstream_commit(tmp_path, env)
        (repo / "stray.log").write_text("noise\n")

        _run_wt(repo, "feat-a", env)

        assert _git(repo, "rev-parse", "main", env=env) == upstream
