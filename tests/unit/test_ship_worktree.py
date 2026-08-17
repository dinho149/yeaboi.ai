"""Tests for the ship worktree coordinator (ship/worktree.py).

Real git repos in tmp dirs — the coordinator's whole job is the boundary
between our registry and git's on-disk state, so faking git would test the
mock. Every subprocess in these tests passes ``git_subprocess_env()`` for the
same reason production does: under the pre-commit hook, inherited ``GIT_*``
would silently retarget commands at the outer repository.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from yeaboi.ship import worktree
from yeaboi.tools.local_git import git_subprocess_env


def _run_git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=git_subprocess_env(),
    )


@pytest.fixture()
def ship_home(tmp_path, monkeypatch):
    """Redirect the registry and worktree root into an isolated temp dir."""
    home = tmp_path / "ship-home"
    home.mkdir()
    monkeypatch.setattr(worktree, "SHIP_WORKTREES_DIR", home / "worktrees")
    monkeypatch.setattr(worktree, "SHIP_WORKTREE_REGISTRY", home / "worktrees.json")
    monkeypatch.setattr(worktree, "get_ship_dir", lambda: home)
    return home


@pytest.fixture()
def target_repo(tmp_path):
    """A real git repo with one commit."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-q", "-m", "init")
    return repo


class TestSafeId:
    def test_accepts_the_shapes_runs_use(self):
        for good in ("run-1", "story-yea-42.a", "a", "x" * 64):
            assert worktree.assert_safe_id(good) == good

    def test_rejects_everything_else(self):
        for bad in ("", "Run-1", "a/b", "-lead", ".lead", "x" * 65, "a b", "a;rm"):
            with pytest.raises(worktree.WorktreeError):
                worktree.assert_safe_id(bad)


class TestPrepare:
    def test_creates_worktree_branch_and_record(self, ship_home, target_repo):
        record = worktree.prepare("run-1", target_repo)
        assert record.branch == "ship/run-1"
        assert (worktree.SHIP_WORKTREES_DIR / "proj" / "run-1" / "README.md").exists()
        branches = subprocess.run(
            ["git", "-C", str(target_repo), "branch", "--list", "ship/run-1"],
            capture_output=True,
            text=True,
            env=git_subprocess_env(),
        ).stdout
        assert "ship/run-1" in branches
        assert len(record.base_sha) == 40

    def test_prepare_is_idempotent(self, ship_home, target_repo):
        first = worktree.prepare("run-1", target_repo)
        second = worktree.prepare("run-1", target_repo)
        assert second == first

    def test_refuses_a_dirty_repository(self, ship_home, target_repo):
        (target_repo / "wip.txt").write_text("uncommitted", encoding="utf-8")
        with pytest.raises(worktree.WorktreeError, match="dirty repository"):
            worktree.prepare("run-1", target_repo)

    def test_refuses_a_non_repo(self, ship_home, tmp_path):
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        with pytest.raises(worktree.WorktreeError):
            worktree.prepare("run-1", plain)

    def test_refuses_a_preexisting_branch_instead_of_reusing_it(self, ship_home, target_repo):
        # A leftover ship/<id> branch may hold a previous run's unpushed
        # commits; prepare must refuse — and the refusal is also what keeps
        # the rollback honest (it only deletes branches this call created).
        _run_git(target_repo, "branch", "ship/run-1")
        with pytest.raises(worktree.WorktreeError, match="already exists"):
            worktree.prepare("run-1", target_repo)
        branches = subprocess.run(
            ["git", "-C", str(target_repo), "branch", "--list", "ship/run-1"],
            capture_output=True,
            text=True,
            env=git_subprocess_env(),
        ).stdout
        assert "ship/run-1" in branches  # untouched

    def test_failed_add_rolls_the_branch_back(self, ship_home, target_repo):
        # Pre-fill the checkout target so `git worktree add` fails after the
        # branch bookkeeping began.
        blocked = worktree.SHIP_WORKTREES_DIR / "proj" / "run-1"
        blocked.mkdir(parents=True)
        (blocked / "junk").write_text("x", encoding="utf-8")
        with pytest.raises(worktree.WorktreeError):
            worktree.prepare("run-1", target_repo)
        branches = subprocess.run(
            ["git", "-C", str(target_repo), "branch", "--list", "ship/run-1"],
            capture_output=True,
            text=True,
            env=git_subprocess_env(),
        ).stdout
        assert "ship/run-1" not in branches
        assert worktree.get_record("run-1") is None


class TestRemove:
    def test_removes_checkout_and_keeps_the_branch_by_default(self, ship_home, target_repo):
        record = worktree.prepare("run-1", target_repo)
        assert worktree.remove("run-1")
        assert not (worktree.SHIP_WORKTREES_DIR / "proj" / "run-1").exists()
        branches = subprocess.run(
            ["git", "-C", str(target_repo), "branch", "--list", record.branch],
            capture_output=True,
            text=True,
            env=git_subprocess_env(),
        ).stdout
        assert record.branch in branches
        assert worktree.get_record("run-1") is None

    def test_delete_branch_is_explicit(self, ship_home, target_repo):
        record = worktree.prepare("run-1", target_repo)
        assert worktree.remove("run-1", delete_branch=True)
        branches = subprocess.run(
            ["git", "-C", str(target_repo), "branch", "--list", record.branch],
            capture_output=True,
            text=True,
            env=git_subprocess_env(),
        ).stdout
        assert record.branch not in branches

    def test_unknown_run_returns_false(self, ship_home):
        assert not worktree.remove("run-404")

    def test_tampered_registry_path_is_refused(self, ship_home, target_repo, tmp_path):
        worktree.prepare("run-1", target_repo)
        victim = tmp_path / "victim"
        victim.mkdir()
        registry = json.loads(worktree.SHIP_WORKTREE_REGISTRY.read_text(encoding="utf-8"))
        registry["run-1"]["path"] = str(victim)
        worktree.SHIP_WORKTREE_REGISTRY.write_text(json.dumps(registry), encoding="utf-8")
        assert not worktree.remove("run-1")
        assert victim.exists()
        # The record stays for a human to look at — a refused delete must not
        # silently drop the evidence.
        assert worktree.get_record("run-1") is not None


class TestRegistry:
    def test_get_record_roundtrip(self, ship_home, target_repo):
        prepared = worktree.prepare("run-1", target_repo)
        assert worktree.get_record("run-1") == prepared
        assert worktree.get_record("run-2") is None

    def test_corrupt_registry_reads_as_empty(self, ship_home):
        worktree.SHIP_WORKTREE_REGISTRY.write_text("{broken", encoding="utf-8")
        assert worktree.get_record("run-1") is None
