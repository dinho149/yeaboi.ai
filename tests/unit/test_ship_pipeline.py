"""Tests for the deterministic pipeline pieces (ship/pipeline.py).

The bridge tests run against a real prepared worktree: the bridge's one job
is telling "the agent said done" apart from "there is work on disk", and only
a real git tree can prove that distinction.
"""

from __future__ import annotations

import subprocess

import pytest

from yeaboi.agent.state import AcceptanceCriterion, Priority, ShipValidation, StoryPointValue, Task, UserStory
from yeaboi.ship import pipeline, worktree
from yeaboi.tools.local_git import git_subprocess_env


def _story(story_id="US-001"):
    return UserStory(
        id=story_id,
        feature_id="F-001",
        persona="developer",
        goal="ship faster",
        benefit="less toil",
        acceptance_criteria=(AcceptanceCriterion(given="a plan", when="I run ship", then="a PR opens"),),
        story_points=StoryPointValue.THREE,
        priority=Priority.HIGH,
        title="Ship pipeline",
    )


def _task(task_id="T-001", story_id="US-001"):
    return Task(
        id=task_id,
        story_id=story_id,
        title="Wire the thing",
        description="Wire it up",
        test_plan="run the tests",
        ai_prompt="You are implementing the wiring. Add X to Y.",
    )


class TestFindStory:
    def test_finds_story_and_its_tasks(self):
        state = {"stories": [_story()], "tasks": [_task(), _task("T-9", story_id="US-999")]}
        story, tasks = pipeline.find_story(state, "US-001")
        assert story.id == "US-001"
        assert [t.id for t in tasks] == ["T-001"]

    def test_missing_story_names_the_available_ids(self):
        with pytest.raises(ValueError, match="US-001"):
            pipeline.find_story({"stories": [_story()], "tasks": []}, "US-404")


class TestBuildPrompt:
    def test_carries_story_criteria_tasks_and_the_run_contract(self):
        prompt = pipeline.build_prompt(_story(), [_task()])
        assert "As a developer" in prompt
        assert "Given a plan, when I run ship, then a PR opens." in prompt
        assert "You are implementing the wiring." in prompt
        assert "Test plan: run the tests" in prompt
        assert "Do NOT push" in prompt

    def test_rework_prompt_carries_the_reviewers_words_and_the_failure(self):
        validation = ShipValidation(configured=True, command="make test", passed=False, exit_code=2, output_tail="boom")
        prompt = pipeline.rework_prompt("wrong file, fix models.py", validation)
        assert "wrong file, fix models.py" in prompt
        assert "make test" in prompt
        assert "boom" in prompt


# ---------------------------------------------------------------------------
# Bridges against a real worktree
# ---------------------------------------------------------------------------


def _run_git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=git_subprocess_env())


@pytest.fixture()
def prepared(tmp_path, monkeypatch):
    """A real repo + prepared ship worktree, isolated under tmp."""
    home = tmp_path / "ship-home"
    home.mkdir()
    monkeypatch.setattr(worktree, "SHIP_WORKTREES_DIR", home / "worktrees")
    monkeypatch.setattr(worktree, "SHIP_WORKTREE_REGISTRY", home / "worktrees.json")
    monkeypatch.setattr(worktree, "get_ship_dir", lambda: home)
    repo = tmp_path / "proj"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git(repo, "add", "README.md")
    _run_git(repo, "commit", "-q", "-m", "init")
    return worktree.prepare("run-1", repo)


class TestDiffBridge:
    def test_no_work_is_no_work_whatever_the_agent_said(self, prepared):
        has_work, stat = pipeline.diff_bridge(prepared)
        assert not has_work
        assert stat == ""

    def test_uncommitted_agent_output_is_committed_and_counted(self, prepared):
        (worktree._validate_owned(prepared.path) / "new.py").write_text("x = 1\n", encoding="utf-8")
        has_work, stat = pipeline.diff_bridge(prepared)
        assert has_work
        assert "new.py" in stat
        # And the tree is clean afterwards — the branch owns the work.
        assert not worktree.is_dirty(prepared.path)

    def test_committed_agent_work_counts_too(self, prepared):
        path = worktree._validate_owned(prepared.path)
        (path / "done.py").write_text("y = 2\n", encoding="utf-8")
        _run_git(path, "add", "done.py")
        _run_git(path, "commit", "-q", "-m", "agent work")
        has_work, stat = pipeline.diff_bridge(prepared)
        assert has_work
        assert "done.py" in stat


class TestRunValidation:
    def test_no_command_is_visible_not_a_silent_pass(self, prepared):
        validation = pipeline.run_validation(prepared, "")
        assert not validation.configured
        assert not validation.passed

    def test_passing_command(self, prepared):
        validation = pipeline.run_validation(prepared, "echo all good")
        assert validation.configured
        assert validation.passed
        assert "all good" in validation.output_tail

    def test_failing_command_carries_exit_and_tail(self, prepared):
        validation = pipeline.run_validation(prepared, "echo broke; exit 3")
        assert validation.configured
        assert not validation.passed
        assert validation.exit_code == 3
        assert "broke" in validation.output_tail


class TestGithubSlug:
    @pytest.mark.parametrize(
        "url",
        [
            "git@github.com:owner/proj.git",
            "https://github.com/owner/proj.git",
            "https://github.com/owner/proj",
            "ssh://git@github.com/owner/proj.git",
        ],
    )
    def test_github_shapes(self, url):
        assert pipeline._github_slug(url) == ("owner", "proj")

    def test_non_github_is_none(self):
        assert pipeline._github_slug("https://gitlab.com/owner/proj.git") is None
        assert pipeline._github_slug("/some/local/path") is None
        assert pipeline._github_slug("") is None


class TestPrBody:
    def test_normal_body_carries_summary_and_approval(self):
        body = pipeline.build_pr_body("## Summary\nimplements US-001", "looks right")
        assert "implements US-001" in body
        assert "looks right" in body
        assert "a human approved this diff" in body

    def test_leaky_body_is_replaced_not_published(self):
        secret = "sk-ant-PLANTED000FAKE111SECRET222"
        body = pipeline.build_pr_body(f"## Summary\ntoken {secret}", "")
        assert secret not in body


class TestPushAndOpenPr:
    def test_push_to_a_local_origin_without_github(self, prepared, tmp_path):
        bare = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, env=git_subprocess_env())
        _run_git(prepared.repo, "remote", "add", "origin", str(bare))
        path = worktree._validate_owned(prepared.path)
        (path / "new.py").write_text("x = 1\n", encoding="utf-8")
        pipeline.diff_bridge(prepared)  # commit the work
        outcome = pipeline.push_and_open_pr(prepared, title="t", body="b")
        assert outcome.pushed
        assert outcome.pr_url == ""
        assert "not GitHub" in outcome.detail

    def test_failed_push_reports_instead_of_raising(self, prepared):
        outcome = pipeline.push_and_open_pr(prepared, title="t", body="b")  # no origin at all
        assert not outcome.pushed
        assert "push failed" in outcome.detail
