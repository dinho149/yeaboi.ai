"""Standing guards over `.github/workflows/implement-reconcile.yml`.

`claude-implement` is a level — "this issue is approved and unbuilt" — and
`claude.yml`'s implement job only ever observed its edge. This workflow reads the
level, which is what makes a lost `labeled` event, a repeated ✅ and a run that
produced nothing all recoverable by one mechanism.

Reading a level means looping, and a loop over an unattended 110-turn agent is
the one way this can be worse than the bug it fixes. So most of what is pinned
here is about **stopping**: the attempt cap, the terminal label, and the two
queries that decide whether there is anything to do. Each fails silently — a
reconciler that re-fires forever and one that never re-fires at all both look
exactly like a quiet workflow from the outside.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "implement-reconcile.yml"
CLAUDE = REPO_ROOT / ".github" / "workflows" / "claude.yml"


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def script(workflow) -> str:
    """The one `run:` block that does the reconciling."""
    steps = workflow["jobs"]["reconcile"]["steps"]
    return next(step["run"] for step in steps if "run" in step and "claude-implement" in step["run"])


class TestItCanStop:
    def test_there_is_an_attempt_cap(self, script, workflow):
        """Without one, a broken implement job burns a 110-turn agent every six
        hours forever. The cap is the difference between a reconciler and a loop."""
        env = next(s for s in workflow["jobs"]["reconcile"]["steps"] if "run" in s and "MAX_ATTEMPTS" in str(s))["env"]
        assert int(env["MAX_ATTEMPTS"]) >= 1
        assert "-ge" in script and "MAX_ATTEMPTS" in script

    def test_reaching_the_cap_applies_the_terminal_label(self, script):
        assert "--add-label implement-blocked" in script

    def test_a_blocked_issue_is_skipped_before_anything_else(self, script):
        """Ordering is the invariant, not just presence. The blocked check must come
        before the attempt count, or a capped issue is re-counted and re-notified
        on every tick — which is the noise the cap exists to prevent."""
        assert script.index('index("implement-blocked")') < script.index("MAX_ATTEMPTS")

    def test_clearing_the_label_actually_lets_it_retry(self, script):
        """The documented recovery path has to work, and once did the opposite.

        Nothing deletes the `<!-- implement-attempt -->` markers, so a lifetime
        count meant removing `implement-blocked` put the issue straight back into
        the cap branch on the next tick: label re-applied, same notice posted, six
        hours apart, forever. Following the instructions was what generated the
        noise. Attempts are counted from the last time that label was removed.
        """
        assert "UnlabeledEvent" in script
        assert "timelineItems" in script
        assert "$since" in script, "the attempt count is not scoped to anything"


class TestItKnowsWhenThereIsNothingToDo:
    @pytest.mark.parametrize("path", [WORKFLOW, CLAUDE])
    def test_a_closed_unmerged_pr_does_not_count_as_a_pr(self, path):
        """Asked of both files, because they ask the same question and a fix in one
        is not a fix in the other.

        `--state all` alone would let a previous attempt's PR — including one closed
        unmerged, which is among the likeliest reasons a re-fire is wanted — answer
        for this attempt. In `claude.yml` that bypasses the whole outcome guard; here
        it means the issue is never re-fired *and* never blocked, so it stops
        existing to the fleet entirely, since `digest.md`'s approved-with-no-PR
        section asks the identical question.
        """
        text = path.read_text()
        assert "in:body" in text
        assert ".mergedAt != null" in text, f"{path.name} counts a closed-unmerged PR as a PR"
        assert '.state == "OPEN"' in text

    def test_both_branch_conventions_are_checked(self, script):
        """`claude-code-action` names its own branches `claude/issue-N-…`. Knowing
        only `feature/issue-N-*` reads a mid-flight run as a failed one and re-fires
        on top of it."""
        assert "feature/issue-$n-*" in script
        assert "claude/issue-$n-*" in script

    def test_it_leaves_a_run_in_flight_alone(self, script):
        assert "in_progress" in script


class TestItCannotDoMoreThanReFire:
    def test_it_never_applies_the_approval_label(self, script):
        """`claude-implement` is a human's verb. A machine that could apply it could
        approve its own work; `house-rules.md` forbids it and this is the one file
        with both the motive and the token."""
        assert "--add-label claude-implement" not in script

    def test_it_never_closes_or_merges_anything(self, script):
        for forbidden in ("gh issue close", "gh pr merge", "gh pr close"):
            assert forbidden not in script

    def test_it_dispatches_rather_than_implementing(self, script):
        """It runs no model of its own — `gh` and `jq`. The job it starts is the one
        that carries the tool grant, the turn cap and the outcome guard."""
        assert "gh workflow run claude.yml" in script

    def test_the_dispatch_target_accepts_being_dispatched(self):
        """The two halves of one contract, in different files: this workflow passes
        `-f issue=<n>`, and `claude.yml` has to declare that input and fire on it."""
        claude = yaml.safe_load(CLAUDE.read_text())
        triggers = claude[True] if True in claude else claude["on"]
        assert "issue" in triggers["workflow_dispatch"]["inputs"]
        assert "workflow_dispatch" in claude["jobs"]["implement"]["if"]


class TestTheLabelIsRealEverywhereItIsUsed:
    def test_cowork_setup_creates_it(self):
        """A label a workflow applies but `make cowork-setup` never creates does
        nothing at all, silently — and the thing it silently fails to do here is
        stop the loop."""
        import sys

        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import cowork_setup as setup

        assert "implement-blocked" in {label.name for label in setup.expected_labels()}
        assert "implement-blocked" in setup.KEEP_LABELS, "teardown would strip the record off every issue"
