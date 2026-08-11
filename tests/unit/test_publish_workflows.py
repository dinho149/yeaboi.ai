"""Tests for the two-channel release path and the branch-prefix contract.

Everything here pins a property that is currently held by a comment. Both are
the kind that fails silently: re-adding a `push:` trigger to `publish.yml` during
a merge conflict restores release-on-merge with nothing to notice, and letting
the unattended-branch lists in `scripts/pr_feedback.py` and
`.github/workflows/claude-review.yml` drift leaves a PR red forever waiting on a
review that was never going to run.

This repo already pins workflow invariants this way — `test_claude_workflow.py`,
`test_codeql_triage.py`, `test_workflow_concurrency.py` all parse YAML to hold
one — so this is the house style rather than a new idea.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLISH = WORKFLOWS / "publish.yml"
PUBLISH_BETA = WORKFLOWS / "publish-beta.yml"
CLAUDE_REVIEW = WORKFLOWS / "claude-review.yml"

_spec = importlib.util.spec_from_file_location("pr_feedback", ROOT / "scripts" / "pr_feedback.py")
prf = importlib.util.module_from_spec(_spec)
sys.modules["pr_feedback"] = prf
_spec.loader.exec_module(prf)


def load(path: Path) -> dict:
    # PyYAML parses the `on:` key as the boolean True.
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestTheOfficialChannelIsPromotionOnly:
    """The headline property: merging to main cannot cut a release."""

    def test_publish_has_no_push_trigger(self):
        triggers = load(PUBLISH)[True]
        assert "push" not in triggers, (
            "publish.yml fired on push again — merging to main would publish an official "
            "release straight to PyPI, which is the whole thing the beta channel replaced."
        )

    def test_publish_fires_on_the_promotion_label(self):
        triggers = load(PUBLISH)[True]
        assert "issues" in triggers and "labeled" in triggers["issues"]["types"]

    def test_promotion_requires_both_labels(self):
        """One label is the human's ✅; the other proves the issue is really an ask.

        Anyone can file an issue on a public repo and the relay parses thread
        replies, so a crafted title could route a ✅ at the wrong issue. It cannot
        put `release:promotion` there — only the ask routine applies that.
        """
        guard = load(PUBLISH)["jobs"]["check"]["if"]
        assert "release:promote" in guard
        assert "release:promotion" in guard

    def test_the_release_notes_are_built_before_the_upload(self):
        """Publishing without tagging is the one unrecoverable ordering.

        PyPI would hold the final while nothing tagged it, cut a Release, or fired
        `release-published-announce.md` — the only announcement path there is.
        """
        jobs = load(PUBLISH)["jobs"]
        check_steps = " ".join(str(step) for step in jobs["check"]["steps"])
        assert "--manifest --markdown" in check_steps, "the batch manifest must be built in `check`"
        release_steps = " ".join(str(step) for step in jobs["release"]["steps"])
        assert "--manifest --markdown" not in release_steps

    def test_promotion_refuses_a_version_that_went_backwards(self):
        """A bare tag-exists check walks straight past the dual-PR race."""
        check_steps = " ".join(str(step) for step in load(PUBLISH)["jobs"]["check"]["steps"])
        assert "--check-promotable" in check_steps


class TestTheBetaChannelStaysABeta:
    def test_beta_fires_on_push_to_main(self):
        triggers = load(PUBLISH_BETA)[True]
        assert triggers["push"]["branches"] == ["main"]

    def test_beta_creates_no_tag_and_no_release(self):
        """`v*` is a finals-only namespace — every count in release_channel.py
        anchors on it, and one rc tag would misdate every batch after it."""
        text = PUBLISH_BETA.read_text(encoding="utf-8")
        assert "action-gh-release" not in text
        assert "tag_name" not in text

    def test_beta_stamps_the_rc_only_in_the_job_that_runs_no_tests(self):
        """Nothing may observe the mutated pyproject.toml: `bump_version.py`
        rejects a non-X.Y.Z version, so an rc reaching `main` breaks later PRs."""
        jobs = load(PUBLISH_BETA)["jobs"]
        publish_steps = " ".join(str(step) for step in jobs["publish"]["steps"])
        assert "--write" in publish_steps
        for name in ("check", "test"):
            assert "--write" not in " ".join(str(step) for step in jobs[name]["steps"])

    def test_the_beta_publish_is_not_cancellable(self):
        """A cancelled beta is a merge that silently never shipped."""
        assert load(PUBLISH_BETA)["concurrency"]["cancel-in-progress"] is False


class TestUnattendedBranchPrefixesAgree:
    """`pr_feedback.py` and `claude-review.yml` must cover the same branches.

    A prefix in the gate but not the reviewer leaves the PR red forever with
    nothing to fix; the reverse spends a review nothing reads.
    """

    def _workflow_prefixes(self) -> set[str]:
        text = CLAUDE_REVIEW.read_text(encoding="utf-8")
        match = re.search(r"case \"\$branch\" in\s*\n\s*([^)]+)\)\s*unattended=true", text)
        assert match, "the unattended `case` arm in claude-review.yml moved or was removed"
        return {arm.strip().removesuffix("*") for arm in match.group(1).split("|")}

    def test_the_two_lists_are_the_same_set(self):
        assert self._workflow_prefixes() == set(prf.UNATTENDED_BRANCH_PREFIXES)

    @pytest.mark.parametrize(
        "branch",
        ["cowork/security-x", "feature/issue-9-y", "security/codeql-triage-z", "ci-sentinel/red"],
    )
    def test_every_machine_branch_is_covered_by_both(self, branch):
        assert branch.startswith(prf.UNATTENDED_BRANCH_PREFIXES)
        assert any(branch.startswith(prefix) for prefix in self._workflow_prefixes())
