"""Tests for the ship engine (ship/engine.py) — the full run, end to end.

A FakeDriver stands in for Claude Code (writing files into the real worktree
the engine prepared), the budget is stubbed per test, and gate resolutions
arrive from a second thread through a second store connection — the same path
a real approving surface takes. The engine's contract under test: it never
raises, every failure is a status with a reason, and the diff on disk — not
the driver's word — decides whether a run proceeds.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

import pytest

from yeaboi.agent.state import AcceptanceCriterion, Priority, StoryPointValue, Task, UserStory
from yeaboi.ship import budget, engine, worktree
from yeaboi.ship.driver import DriverResult
from yeaboi.ship.store import ShipStore
from yeaboi.tools.local_git import git_subprocess_env


def _run_git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_subprocess_env())


def _story():
    return UserStory(
        id="US-001",
        feature_id="F-001",
        persona="developer",
        goal="ship faster",
        benefit="less toil",
        acceptance_criteria=(AcceptanceCriterion(given="a plan", when="ship runs", then="a PR opens"),),
        story_points=StoryPointValue.THREE,
        priority=Priority.HIGH,
        title="Ship pipeline",
    )


def _task():
    return Task(id="T-1", story_id="US-001", title="wire", description="wire it", ai_prompt="Add x to y.")


class FakeDriver:
    """A scripted agent: each call runs the next behavior in the list.

    Behaviors: "work" writes a new file into the cwd, "nothing" does nothing,
    "fail_quota" fails with a quota-shaped error, "cancelled" reports a
    cancelled run.
    """

    def __init__(self, behaviors=("work",)):
        self.behaviors = list(behaviors)
        self.calls: list[str] = []  # the prompts, for assertions

    def available(self):
        return True, "fake 1.0"

    def get_type(self):
        return "fake"

    def get_capabilities(self):
        return {}

    def run(self, prompt, cwd, *, timeout_s=0, cancel_event=None, on_line=None):
        self.calls.append(prompt)
        behavior = self.behaviors.pop(0) if self.behaviors else "nothing"
        if behavior == "work":
            (Path(cwd) / f"agent_{len(self.calls)}.py").write_text("x = 1\n", encoding="utf-8")
            return DriverResult(ok=True, output="implemented", session_id="", returncode=0)
        if behavior == "fail_quota":
            return DriverResult(ok=False, error="HTTP 429 rate limit", returncode=1)
        if behavior == "cancelled":
            return DriverResult(ok=False, cancelled=True, returncode=-1)
        return DriverResult(ok=True, output="I could not find anything to change.", returncode=0)


@pytest.fixture()
def ship_env(tmp_path, monkeypatch):
    """Everything a run touches, isolated: worktree root, budget, db, repo."""
    home = tmp_path / "ship-home"
    home.mkdir()
    monkeypatch.setattr(worktree, "SHIP_WORKTREES_DIR", home / "worktrees")
    monkeypatch.setattr(worktree, "SHIP_WORKTREE_REGISTRY", home / "worktrees.json")
    monkeypatch.setattr(worktree, "get_ship_dir", lambda: home)
    released: list[str] = []
    monkeypatch.setattr(budget, "reserve", lambda **kw: budget.BudgetDecision(allowed=True, permit_id="permit_test"))
    monkeypatch.setattr(budget, "release", released.append)
    monkeypatch.setattr(engine, "_GATE_POLL_S", 0.02)

    repo = tmp_path / "proj"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-q", "-m", "init")
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, env=git_subprocess_env())
    _run_git(repo, "remote", "add", "origin", str(bare))

    monkeypatch.setattr(engine, "_load_story", lambda sid, story_id, db: (_story(), [_task()], "sess-1"))

    class Env:
        pass

    env = Env()
    env.repo = repo
    env.db = tmp_path / "sessions.db"
    env.released = released
    return env


def _resolver(db_path, resolutions, comments=()):
    """A thread that answers the gate each time it opens, like a real surface."""
    comments = list(comments) + [""] * len(resolutions)

    def _work():
        with ShipStore(db_path) as store:
            for index, resolution in enumerate(resolutions):
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    runs = store.list_runs(limit=1)
                    if runs and runs[0].status == "awaiting_approval" and not runs[0].gate_resolution:
                        store.resolve_gate(runs[0].run_id, resolution, comments[index])
                        break
                    time.sleep(0.02)

    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    return thread


class TestDryRun:
    def test_dry_run_is_canned_and_touches_nothing(self, tmp_path):
        run = engine.run_ship("US-001", str(tmp_path), dry_run=True)
        assert run.status == "approved"
        assert any("dry run" in w for w in run.warnings)
        assert not (tmp_path / ".git").exists()


class TestFailurePaths:
    def test_unloadable_story_is_a_failed_run(self, ship_env, monkeypatch):
        def _boom(sid, story_id, db):
            raise ValueError("story US-404 not found in this plan")

        monkeypatch.setattr(engine, "_load_story", _boom)
        run = engine.run_ship("US-404", str(ship_env.repo), db_path=ship_env.db, driver=FakeDriver())
        assert run.status == "failed"
        assert any("US-404" in w for w in run.warnings)

    def test_budget_denial_is_a_failed_run_before_any_git(self, ship_env, monkeypatch):
        monkeypatch.setattr(budget, "reserve", lambda **kw: budget.BudgetDecision(reason="hourly-budget (2/2)"))
        run = engine.run_ship("US-001", str(ship_env.repo), db_path=ship_env.db, driver=FakeDriver())
        assert run.status == "failed"
        assert any("hourly-budget" in w for w in run.warnings)
        assert not (worktree.SHIP_WORKTREES_DIR / "proj").exists()

    def test_unavailable_agent_fails_before_the_budget(self, ship_env):
        driver = FakeDriver()
        driver.available = lambda: (False, "claude not found on PATH")
        run = engine.run_ship("US-001", str(ship_env.repo), db_path=ship_env.db, driver=driver)
        assert run.status == "failed"
        assert any("not found on PATH" in w for w in run.warnings)

    def test_no_diff_fails_whatever_the_agent_said(self, ship_env):
        driver = FakeDriver(behaviors=("nothing",))
        resolver = _resolver(ship_env.db, [])  # gate never opens
        run = engine.run_ship("US-001", str(ship_env.repo), db_path=ship_env.db, driver=driver)
        resolver.join(timeout=1)
        assert run.status == "failed"
        assert any("produced no changes" in w for w in run.warnings)
        assert any("could not find anything" in w for w in run.warnings)  # the agent's own words surface

    def test_quota_failure_trips_the_circuit_breaker(self, ship_env, monkeypatch):
        tripped: list[str] = []
        monkeypatch.setattr(budget, "record_quota_error", tripped.append)
        run = engine.run_ship(
            "US-001", str(ship_env.repo), db_path=ship_env.db, driver=FakeDriver(behaviors=("fail_quota",))
        )
        assert run.status == "failed"
        assert tripped and "429" in tripped[0]

    def test_budget_is_released_even_when_the_run_fails(self, ship_env):
        engine.run_ship("US-001", str(ship_env.repo), db_path=ship_env.db, driver=FakeDriver(behaviors=("nothing",)))
        assert ship_env.released == ["permit_test"]


class TestHappyPath:
    def test_approved_run_pushes_and_persists(self, ship_env):
        driver = FakeDriver(behaviors=("work",))
        resolver = _resolver(ship_env.db, ["approved"], ["looks right"])
        run = engine.run_ship("US-001", str(ship_env.repo), db_path=ship_env.db, check_command="echo ok", driver=driver)
        resolver.join(timeout=5)
        assert run.status == "approved", run.warnings
        assert run.gate_comment == "looks right"
        assert run.validation.passed
        assert "agent_1.py" in run.diff_stat
        assert run.pr_url == ""  # local origin — branch pushed, no GitHub
        with ShipStore(ship_env.db) as store:
            stored = store.get_run(run.run_id)
        assert stored.status == "approved"
        # The branch reached origin.
        heads = subprocess.run(
            ["git", "-C", str(ship_env.repo), "ls-remote", "--heads", "origin"],
            capture_output=True,
            text=True,
            env=git_subprocess_env(),
        ).stdout
        assert run.branch in heads

    def test_progress_events_cover_every_phase(self, ship_env):
        events: list[dict] = []
        resolver = _resolver(ship_env.db, ["approved"])
        engine.run_ship(
            "US-001",
            str(ship_env.repo),
            db_path=ship_env.db,
            driver=FakeDriver(behaviors=("work",)),
            on_progress=events.append,
        )
        resolver.join(timeout=5)
        seen = {e.get("component_id") for e in events if isinstance(e, dict)}
        assert {"ship-setup", "ship-implement", "ship-validate", "ship-gate", "ship-finalize"} <= seen


class TestGateLoop:
    def test_rejection_reworks_with_the_reviewers_words(self, ship_env):
        driver = FakeDriver(behaviors=("work", "work"))
        resolver = _resolver(ship_env.db, ["rejected", "approved"], ["use tabs not spaces", "better"])
        run = engine.run_ship("US-001", str(ship_env.repo), db_path=ship_env.db, driver=driver)
        resolver.join(timeout=10)
        assert run.status == "approved", run.warnings
        assert run.rejection_count == 1
        assert len(driver.calls) == 2
        assert "use tabs not spaces" in driver.calls[1]  # the rework prompt carries the comment

    def test_rejections_at_the_cap_are_terminal(self, ship_env, monkeypatch):
        monkeypatch.setattr(engine, "MAX_REJECTION_ATTEMPTS", 2)
        driver = FakeDriver(behaviors=("work", "work", "work"))
        resolver = _resolver(ship_env.db, ["rejected", "rejected"], ["no", "still no"])
        run = engine.run_ship("US-001", str(ship_env.repo), db_path=ship_env.db, driver=driver)
        resolver.join(timeout=10)
        assert run.status == "rejected"
        assert run.rejection_count == 2
        assert len(driver.calls) == 2  # rework ran once; the second rejection was terminal

    def test_cancel_while_awaiting_approval(self, ship_env):
        cancel = threading.Event()

        def _cancel_when_gated():
            with ShipStore(ship_env.db) as store:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    runs = store.list_runs(limit=1)
                    if runs and runs[0].status == "awaiting_approval":
                        cancel.set()
                        return
                    time.sleep(0.02)

        thread = threading.Thread(target=_cancel_when_gated, daemon=True)
        thread.start()
        run = engine.run_ship(
            "US-001",
            str(ship_env.repo),
            db_path=ship_env.db,
            driver=FakeDriver(behaviors=("work",)),
            cancel_event=cancel,
        )
        thread.join(timeout=5)
        assert run.status == "cancelled"
