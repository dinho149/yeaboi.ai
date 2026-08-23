"""Tests for the ship engine (ship/engine.py) — the full run, end to end.

A FakeDriver stands in for Claude Code (writing files into the real worktree
the engine prepared), the budget is stubbed per test, and gate resolutions
arrive from a second thread through a second store connection — the same path
a real approving surface takes. The engine's contract under test: it never
raises, every failure is a status with a reason, and the diff on disk — not
the driver's word — decides whether a run proceeds.
"""

from __future__ import annotations

import contextlib
import dataclasses
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from yeaboi.agent.state import (
    AcceptanceCriterion,
    Priority,
    ShipValidation,
    StoryPointValue,
    Task,
    UserStory,
)
from yeaboi.ship import budget, engine, worktree
from yeaboi.ship.driver import DriverResult
from yeaboi.ship.scope import ShipTarget
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


def _target(story=None):
    story = story or _story()
    return ShipTarget(
        level="story",
        id=story.id,
        title=story.title or story.id,
        summary=story.text,
        stories=(story,),
        tasks=(_task(),),
    )


@contextlib.contextmanager
def monkeypatch_context():
    """A monkeypatch usable inside a test body, for patches that must be undone
    before the fixture teardown that depends on the real function."""
    mp = pytest.MonkeyPatch()
    try:
        yield mp
    finally:
        mp.undo()


class FakeDriver:
    """A scripted agent: each call runs the next behavior in the list.

    Behaviors: "work" writes a new file into the cwd, "nothing" does nothing,
    "fail_quota" fails with a quota-shaped error, "cancelled" reports a
    cancelled run.
    """

    def __init__(self, behaviors=("work",), tag=""):
        # ``tag`` keeps two drivers in one test from writing the same file: on a
        # stacked branch, work identical to the base is correctly no diff at all.
        self.tag = tag
        self.behaviors = list(behaviors)
        self.calls: list[str] = []  # the prompts, for assertions
        self.streamed: list[bool] = []  # the `stream` flag per call, for assertions

    def available(self):
        return True, "fake 1.0"

    def get_type(self):
        return "fake"

    def get_capabilities(self):
        return {}

    def run(self, prompt, cwd, *, timeout_s=0, cancel_event=None, on_line=None, stream=False):
        self.calls.append(prompt)
        self.streamed.append(stream)
        if on_line is not None:
            on_line('{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}')
        behavior = self.behaviors.pop(0) if self.behaviors else "nothing"
        if behavior == "work":
            name = f"agent_{self.tag}{len(self.calls)}.py"
            (Path(cwd) / name).write_text(f"x = {len(self.calls)}  # {self.tag}\n", encoding="utf-8")
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

    monkeypatch.setattr(engine, "_load_target", lambda sid, item_id, level, db: (_target(), "sess-1", "Proj"))

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
        def _boom(sid, item_id, level, db):
            raise ValueError("US-404 is not in this plan")

        monkeypatch.setattr(engine, "_load_target", _boom)
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

    def test_the_run_id_reaches_the_caller_before_the_gate_opens(self, ship_env):
        # Surfaces poll a shared store for the gate; identifying "my run" by
        # elimination stops being true the moment two runs are allowed at
        # once, and then someone approves a diff they never saw.
        seen: list[str] = []
        resolver = _resolver(ship_env.db, ["approved"])
        run = engine.run_ship(
            "US-001",
            str(ship_env.repo),
            db_path=ship_env.db,
            driver=FakeDriver(behaviors=("work",)),
            on_run_id=seen.append,
        )
        resolver.join(timeout=5)
        assert seen == [run.run_id]

    def test_the_run_id_is_handed_over_even_when_the_run_fails(self, ship_env):
        seen: list[str] = []
        run = engine.run_ship(
            "US-001",
            str(ship_env.repo),
            db_path=ship_env.db,
            driver=FakeDriver(behaviors=("nothing",)),
            on_run_id=seen.append,
        )
        assert run.status == "failed"
        assert seen == [run.run_id]

    def test_a_raising_run_id_callback_does_not_kill_the_run(self, ship_env):
        def _boom(_run_id):
            raise RuntimeError("a surface hiccup")

        resolver = _resolver(ship_env.db, ["approved"])
        run = engine.run_ship(
            "US-001",
            str(ship_env.repo),
            db_path=ship_env.db,
            driver=FakeDriver(behaviors=("work",)),
            on_run_id=_boom,
        )
        resolver.join(timeout=5)
        assert run.status == "approved", run.warnings

    def test_the_patch_travels_with_the_artifact(self, ship_env):
        resolver = _resolver(ship_env.db, ["approved"])
        run = engine.run_ship("US-001", str(ship_env.repo), db_path=ship_env.db, driver=FakeDriver(("work",)))
        resolver.join(timeout=5)
        assert "agent_1.py" in run.diff_text
        assert run.diff_text.startswith("diff --git")

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


class TestResumable:
    """`_resumable_reason` is the predicate the hub and the CLI both trust."""

    def test_a_run_not_at_the_gate_is_not_resumable(self):
        from yeaboi.agent.state import ShipRun

        reason = engine._resumable_reason(ShipRun(run_id="r1", status="approved"))
        assert "waiting at the gate" in reason

    def test_a_live_owner_blocks_a_resume(self, ship_env, monkeypatch):
        from yeaboi.agent.state import ShipRun

        monkeypatch.setattr(budget, "process_alive", lambda pid: True)
        reason = engine._resumable_reason(ShipRun(run_id="r1", status="awaiting_approval", owner_pid=424242))
        assert "still driving" in reason

    def test_a_missing_worktree_blocks_a_resume(self, ship_env, monkeypatch):
        from yeaboi.agent.state import ShipRun

        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        reason = engine._resumable_reason(ShipRun(run_id="gone", status="awaiting_approval", owner_pid=424242))
        assert "worktree was removed" in reason

    def test_our_own_pid_never_blocks_us(self, ship_env, monkeypatch):
        import os

        from yeaboi.agent.state import ShipRun

        # Same process = this IS the owner; the check must fall through to the
        # worktree question rather than refusing us our own run.
        reason = engine._resumable_reason(ShipRun(run_id="x", status="awaiting_approval", owner_pid=os.getpid()))
        assert "still driving" not in reason


def _abandon_at_the_gate(ship_env, driver=None):
    """Reconstruct exactly what a `kill -9` at the gate leaves behind.

    Run for real until the gate opens and snapshot the row, then stop the engine
    thread and write that snapshot back with a dead owner pid. The write is
    unconditional on purpose: a killed process never gets to update its own row,
    so the stranded state is the gate-open row plus an owner that is gone. The
    branch, its commits and the worktree are left where the run put them.
    """
    cancel = threading.Event()

    def _work():
        engine.run_ship(
            "US-001",
            str(ship_env.repo),
            db_path=ship_env.db,
            driver=driver or FakeDriver(),
            cancel_event=cancel,
        )

    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    at_gate = None
    with ShipStore(ship_env.db) as store:
        while time.monotonic() < deadline:
            runs = store.list_runs(limit=1)
            if runs and runs[0].status == "awaiting_approval":
                at_gate = runs[0]
                break
            time.sleep(0.02)
    assert at_gate is not None, "the run never reached the gate"
    cancel.set()
    thread.join(timeout=30)
    with ShipStore(ship_env.db) as store:
        store.save_run(replace(at_gate, status="awaiting_approval", gate_resolution="", owner_pid=424242))
    return at_gate.run_id


def _resume_resolver(db_path, resolutions, comments=()):
    """Answer only the gates a *resume* re-opened, never the stranded one.

    An abandoned run is already sitting at an open gate, so a plain resolver would
    spend its answer on that row before resume ever re-asks. The claim is what
    tells the two apart: resume stamps its own pid on the run, so this waits for
    `owner_pid == os.getpid()` before answering.
    """
    import os

    comments = list(comments) + [""] * len(resolutions)

    def _work():
        with ShipStore(db_path) as store:
            for index, resolution in enumerate(resolutions):
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    runs = store.list_runs(limit=1)
                    run = runs[0] if runs else None
                    if (
                        run is not None
                        and run.status == "awaiting_approval"
                        and not run.gate_resolution
                        and run.owner_pid == os.getpid()
                    ):
                        store.resolve_gate(run.run_id, resolution, comments[index])
                        break
                    time.sleep(0.02)

    thread = threading.Thread(target=_work, daemon=True)
    thread.start()
    return thread


class TestResume:
    def test_an_abandoned_gate_can_be_approved_and_pushed(self, ship_env, monkeypatch):
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        run_id = _abandon_at_the_gate(ship_env)
        monkeypatch.setattr(engine, "_load_target", lambda sid, item_id, level, db: (_target(), "sess-1", "Proj"))
        resolver = _resume_resolver(ship_env.db, ["approved"])
        run = engine.resume_ship(run_id, db_path=ship_env.db, driver=FakeDriver())
        resolver.join(timeout=30)
        assert run.status == "approved", run.warnings
        assert run.pr_url or "pushed" in " ".join(p.detail for p in run.phases)

    def test_approving_a_resume_spends_no_launch_budget(self, ship_env, monkeypatch):
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        run_id = _abandon_at_the_gate(ship_env)
        reserved: list = []
        monkeypatch.setattr(
            budget,
            "reserve",
            lambda **kw: reserved.append(1) or budget.BudgetDecision(allowed=True, permit_id="p"),
        )
        resolver = _resume_resolver(ship_env.db, ["approved"])
        engine.resume_ship(run_id, db_path=ship_env.db, driver=FakeDriver())
        resolver.join(timeout=30)
        # A push is not a launch. Only a rejection, which re-runs the agent, is.
        assert reserved == []

    def test_rejecting_a_resume_reserves_a_slot_and_reworks(self, ship_env, monkeypatch):
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        run_id = _abandon_at_the_gate(ship_env)
        reserved: list = []
        monkeypatch.setattr(
            budget,
            "reserve",
            lambda **kw: reserved.append(1) or budget.BudgetDecision(allowed=True, permit_id="p"),
        )
        driver = FakeDriver(["work"])
        resolver = _resume_resolver(ship_env.db, ["rejected", "approved"], comments=["needs a test"])
        run = engine.resume_ship(run_id, db_path=ship_env.db, driver=driver)
        resolver.join(timeout=30)
        assert reserved, "a rework re-runs the agent and must be charged a launch slot"
        assert driver.calls and "needs a test" in driver.calls[0]
        assert run.status == "approved", run.warnings

    def test_a_denied_budget_stops_the_rework_instead_of_running_it(self, ship_env, monkeypatch):
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        run_id = _abandon_at_the_gate(ship_env)
        monkeypatch.setattr(budget, "reserve", lambda **kw: budget.BudgetDecision(reason="hourly-budget (2/2)"))
        driver = FakeDriver(["work"])
        resolver = _resume_resolver(ship_env.db, ["rejected"])
        run = engine.resume_ship(run_id, db_path=ship_env.db, driver=driver)
        resolver.join(timeout=30)
        assert run.status == "failed"
        assert any("hourly-budget" in w for w in run.warnings)
        assert driver.calls == [], "the agent must not run when the slot was denied"

    def test_resume_keeps_the_recorded_verdict_when_no_check_is_given(self, ship_env, monkeypatch):
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        run_id = _abandon_at_the_gate(ship_env)
        with ShipStore(ship_env.db) as store:
            stored = store.get_run(run_id)
            store.save_run(
                replace(
                    stored,
                    validation=ShipValidation(configured=True, command="make test", passed=True, exit_code=0),
                ),
                expect_status="awaiting_approval",
            )
        ran: list = []
        monkeypatch.setattr(engine.pipeline, "run_validation", lambda rec, cmd: ran.append(cmd))
        resolver = _resume_resolver(ship_env.db, ["approved"])
        run = engine.resume_ship(run_id, db_path=ship_env.db, driver=FakeDriver())
        resolver.join(timeout=30)
        assert ran == [], "resume must not silently re-run the check command"
        assert run.validation.command == "make test" and run.validation.passed

    def test_resume_refuses_an_unknown_run(self, ship_env):
        run = engine.resume_ship("nope", db_path=ship_env.db)
        assert run.status == "failed"
        assert any("no ship run named" in w for w in run.warnings)

    def test_resume_refuses_a_finished_run(self, ship_env, monkeypatch):
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        resolver = _resolver(ship_env.db, ["approved"])
        done = engine.run_ship("US-001", str(ship_env.repo), db_path=ship_env.db, driver=FakeDriver())
        resolver.join(timeout=30)
        assert done.status == "approved"
        again = engine.resume_ship(done.run_id, db_path=ship_env.db)
        assert again.status == "failed"
        assert any("waiting at the gate" in w for w in again.warnings)

    def test_resume_survives_a_plan_that_is_gone(self, ship_env, monkeypatch):
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        run_id = _abandon_at_the_gate(ship_env)

        def _boom(sid, item_id, level, db):
            raise ValueError("plan deleted")

        monkeypatch.setattr(engine, "_load_target", _boom)
        resolver = _resume_resolver(ship_env.db, ["approved"])
        run = engine.resume_ship(run_id, db_path=ship_env.db, driver=FakeDriver())
        resolver.join(timeout=30)
        # The diff is the deliverable; a missing plan costs prose, not the PR.
        assert run.status == "approved", run.warnings

    def test_a_run_another_process_already_moved_is_refused(self, ship_env, monkeypatch):
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        run_id = _abandon_at_the_gate(ship_env)
        with ShipStore(ship_env.db) as store:
            stored = store.get_run(run_id)
            assert store.save_run(
                replace(stored, status="running", owner_pid=999999), expect_status="awaiting_approval"
            )
        run = engine.resume_ship(run_id, db_path=ship_env.db, driver=FakeDriver())
        assert run.status == "failed"
        assert any("waiting at the gate" in w for w in run.warnings)

    def test_losing_the_claim_race_stops_us_before_the_agent(self, ship_env, monkeypatch):
        """The claim is one guarded write, and its boolean decides who owns the run.

        Stamping an owner without moving the status would let both resumers win —
        the CAS predicate is on status, so the second write would still match — and
        both would go on to push the same branch.
        """
        monkeypatch.setattr(budget, "process_alive", lambda pid: False)
        run_id = _abandon_at_the_gate(ship_env)

        real_save = ShipStore.save_run
        raced: list = []

        def _lose_the_first_write(self, run, *, expect_status=None):
            if not raced:
                raced.append(1)
                # Another resumer landed its claim a microsecond earlier.
                real_save(self, replace(run, owner_pid=999999), expect_status=expect_status)
                return False
            return real_save(self, run, expect_status=expect_status)

        monkeypatch.setattr(ShipStore, "save_run", _lose_the_first_write)
        driver = FakeDriver(["work"])
        run = engine.resume_ship(run_id, db_path=ship_env.db, driver=driver)
        assert raced, "the claim write never happened"
        assert run.owner_pid == 999999, "the winner's row must come back, not ours"
        assert driver.calls == [], "the loser must not run the agent"


def _epic_target(story_ids=("US-001", "US-002")):
    stories = tuple(dataclasses.replace(_story(), id=sid, title=f"Story {sid}") for sid in story_ids)
    return ShipTarget(
        level="epic",
        id="F1",
        title="Core Functionality",
        summary="The primary flow.",
        stories=stories,
        tasks=(),
    )


@pytest.fixture()
def batch_env(ship_env, monkeypatch):
    """``ship_env`` with the epic resolving to a two-story split."""

    def _load(sid, item_id, level, db):
        if item_id == "F1":
            return _epic_target(), "sess-1", "Proj"
        return _target(dataclasses.replace(_story(), id=item_id, title=f"Story {item_id}")), "sess-1", "Proj"

    monkeypatch.setattr(engine, "_load_target", _load)
    return ship_env


class TestBatch:
    def test_each_member_stacks_on_the_branch_before_it(self, batch_env):
        # Later stories in an epic build on earlier ones: off a shared base every
        # diff would re-show the same files and the PRs would conflict.
        bases: list[str] = []
        real_prepare = worktree.prepare

        def _spy(run_id, repo, *, base_ref="HEAD"):
            bases.append(base_ref)
            return real_prepare(run_id, repo, base_ref=base_ref)

        pr_bases: list[str] = []
        real_push = engine.pipeline.push_and_open_pr

        def _push_spy(record, *, title, body, base=""):
            pr_bases.append(base)
            return real_push(record, title=title, body=body, base=base)

        with monkeypatch_context() as mp:
            mp.setattr(worktree, "prepare", _spy)
            mp.setattr(engine.pipeline, "push_and_open_pr", _push_spy)
            resolver = _resolver(batch_env.db, ["approved", "approved"])
            members = engine.run_ship_batch(
                "F1", str(batch_env.repo), db_path=batch_env.db, driver=FakeDriver(["work", "work"])
            )
            resolver.join(timeout=60)

        assert [m.status for m in members] == ["approved", "approved"]
        assert bases[0] == "HEAD"
        assert bases[1] == members[0].branch
        assert pr_bases == ["", members[0].branch]

    def test_members_share_a_batch_id_and_are_numbered(self, batch_env):
        resolver = _resolver(batch_env.db, ["approved", "approved"])
        members = engine.run_ship_batch(
            "F1", str(batch_env.repo), db_path=batch_env.db, driver=FakeDriver(["work", "work"])
        )
        resolver.join(timeout=60)
        assert len({m.batch_id for m in members}) == 1
        assert [m.batch_index for m in members] == [1, 2]
        assert {m.batch_total for m in members} == {2}
        assert {m.batch_item_id for m in members} == {"F1"}

    def test_a_rejected_member_stops_the_batch_and_leaves_the_rest_unstarted(self, batch_env):
        # Stacked branches make this close to forced: story 2 would otherwise be
        # built on a base the human just refused.
        resolver = _resolver(batch_env.db, ["rejected", "rejected", "rejected"])
        members = engine.run_ship_batch(
            "F1",
            str(batch_env.repo),
            db_path=batch_env.db,
            driver=FakeDriver(["work", "work", "work", "work"]),
        )
        resolver.join(timeout=60)
        assert members[0].status == "rejected"
        assert members[1].status == "planned"
        assert any("stopped at US-001" in w for w in members[1].warnings)

    def test_an_unstarted_member_is_never_persisted(self, batch_env):
        resolver = _resolver(batch_env.db, ["rejected", "rejected", "rejected"])
        engine.run_ship_batch("F1", str(batch_env.repo), db_path=batch_env.db, driver=FakeDriver(["work"] * 4))
        resolver.join(timeout=60)
        with ShipStore(batch_env.db) as store:
            stored = {r.item_id for r in store.list_runs(limit=50)}
        # It was never attempted, so it is not a run — writing it would make the
        # hub claim work that never happened.
        assert stored == {"US-001"}

    def test_a_denied_budget_stops_the_batch_in_the_fuses_own_words(self, batch_env, monkeypatch):
        # Two launches an hour means a large epic *will* stop partway; that is
        # the intended shape, and the reason has to survive to the surface.
        monkeypatch.setattr(budget, "reserve", lambda **kw: budget.BudgetDecision(reason="hourly-budget (2/2)"))
        members = engine.run_ship_batch(
            "F1", str(batch_env.repo), db_path=batch_env.db, driver=FakeDriver(["work", "work"])
        )
        assert members[0].status == "failed"
        assert any("hourly-budget" in w for w in members[0].warnings)
        assert members[1].status == "planned"
        assert any("hourly-budget" in w for w in members[1].warnings)

    def test_relaunching_continues_the_batch_instead_of_reshipping(self, batch_env):
        resolver = _resolver(batch_env.db, ["approved", "rejected", "rejected", "rejected"])
        first = engine.run_ship_batch("F1", str(batch_env.repo), db_path=batch_env.db, driver=FakeDriver(["work"] * 5))
        resolver.join(timeout=60)
        assert [m.status for m in first] == ["approved", "rejected"]

        driver = FakeDriver(["work"] * 3, tag="second_")
        resolver = _resolver(batch_env.db, ["approved"])
        second = engine.run_ship_batch("F1", str(batch_env.repo), db_path=batch_env.db, driver=driver)
        resolver.join(timeout=60)
        assert second[0].run_id == first[0].run_id  # the shipped story is adopted, not re-run
        assert second[0].batch_id == first[0].batch_id
        assert second[1].status == "approved", second[1].warnings
        # Only the unfinished story went back to the agent.
        assert len(driver.calls) >= 1

    def test_a_story_cannot_be_split(self, batch_env):
        members = engine.run_ship_batch("US-001", str(batch_env.repo), db_path=batch_env.db, driver=FakeDriver())
        assert len(members) == 1
        assert members[0].status == "failed"
        assert any("only an epic" in w for w in members[0].warnings)

    def test_an_epic_with_no_stories_says_so(self, batch_env, monkeypatch):
        monkeypatch.setattr(
            engine,
            "_load_target",
            lambda sid, item_id, level, db: (dataclasses.replace(_epic_target(), stories=()), "sess-1", "Proj"),
        )
        members = engine.run_ship_batch("F1", str(batch_env.repo), db_path=batch_env.db, driver=FakeDriver())
        assert members[0].status == "failed"
        assert any("no stories to split over" in w for w in members[0].warnings)

    def test_an_unloadable_plan_is_one_failed_artifact_not_a_raise(self, batch_env, monkeypatch):
        def _boom(sid, item_id, level, db):
            raise ValueError("F404 is not in this plan")

        monkeypatch.setattr(engine, "_load_target", _boom)
        members = engine.run_ship_batch("F404", str(batch_env.repo), db_path=batch_env.db, driver=FakeDriver())
        assert len(members) == 1
        assert members[0].status == "failed"
        assert any("F404" in w for w in members[0].warnings)

    def test_a_merged_away_parent_branch_falls_back_to_head(self, batch_env):
        # A parent branch that is gone was almost certainly merged, so its work
        # is in HEAD already — failing the rest of the batch on "unknown
        # revision" would be the wrong reading of that.
        resolver = _resolver(batch_env.db, ["approved", "approved"])
        with monkeypatch_context() as mp:
            mp.setattr(worktree, "_branch_exists", lambda repo, branch: False)
            members = engine.run_ship_batch(
                "F1", str(batch_env.repo), db_path=batch_env.db, driver=FakeDriver(["work", "work"])
            )
        resolver.join(timeout=60)
        assert [m.status for m in members] == ["approved", "approved"]
        # Both branched from the same base, because the first one's was "gone".
        assert members[0].base_sha == members[1].base_sha
