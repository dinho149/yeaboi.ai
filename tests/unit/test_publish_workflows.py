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
    """The headline property: an UNATTENDED merge to main cannot cut a release.

    This used to read "merging to main cannot cut a release" and assert that
    `publish.yml` had no `push:` trigger at all. That was too wide by one lane:
    the gate exists so unattended work does not reach `pip install yeaboi`
    without somebody signing for it, and a human merging their own PR *is* that
    signature. Holding their work for a weekly ask was the gate misfiring on its
    author — 14 of the 17 commits it stranded before v3.10.0 were the
    maintainer's. The property that actually matters is the one below.
    """

    def test_a_push_cannot_publish_without_being_classified(self):
        """The `push` trigger is admitted, so every step that leads to an upload
        must be gated on the lane verdict. A step that forgets the guard runs for
        a fleet merge too, and `check` starts handing `go=true` to the publish
        job — the exact regression the old no-push-trigger test prevented."""
        steps = load(PUBLISH)["jobs"]["check"]["steps"]
        lane = [s for s in steps if s.get("id") == "lane"]
        assert lane, "check has no `lane` step, but publish.yml fires on push"
        assert lane[0]["if"] == "github.event_name == 'push'"

        guard = "steps.lane.outputs.unattended != 'true'"
        after = steps[steps.index(lane[0]) + 1 :]

        def label(step: dict) -> str:
            return step.get("id") or step.get("uses") or step.get("name") or "<unnamed>"

        ungated = {label(s): str(s.get("if", "")) for s in after if guard not in str(s.get("if", ""))}
        # Unconditional now: the issues-only exemption died with the `issues:`
        # trigger, so EVERY step after `lane` must carry the guard.
        assert not ungated, f"steps after `lane` without the lane guard: {ungated}"

    def test_the_lane_verdict_is_not_respelled_in_yaml(self):
        """One predicate, one language. A prefix added to `pr_feedback.py` and
        not to a YAML copy would turn a fleet merge into an official release."""
        text = PUBLISH.read_text(encoding="utf-8")
        assert "release_lane.py" in text
        for prefix in prf.UNATTENDED_BRANCH_PREFIXES:
            assert prefix not in text, f"publish.yml hardcodes the unattended prefix {prefix!r}"

    def test_an_unreadable_lane_stays_on_the_prerelease_channel(self):
        """PyPI has no delete, so "I could not tell" must fail toward the rc."""
        lane = next(s for s in load(PUBLISH)["jobs"]["check"]["steps"] if s.get("id") == "lane")
        setter = 'echo "unattended=true" >> "$GITHUB_OUTPUT"'
        # Per BLOCK, not a bare count: three copies inside the fleet branch would
        # satisfy a count of three while leaving both failure paths falling
        # through to `unattended` unset — which reads as "human" and publishes.
        blocks = re.split(r"\n\s*(?=if |fi\b|else\b)", lane["run"])
        failure_blocks = [b for b in blocks if "if ! " in b]
        assert len(failure_blocks) == 2, (
            f"expected the gh lookup and the classifier to be the two guarded calls, got {len(failure_blocks)}"
        )
        for block in failure_blocks:
            assert setter in block, f"a failure path does not stay on the pre-release channel:\n{block}"

    def test_publish_has_no_issues_trigger(self):
        """Releasing by label is RETIRED. The batch model's ship act is a human
        merging the batch PR — a push — and an `issues:` trigger surviving a
        merge-conflict resolution would silently revive a label-driven release
        path that now has no double-label guard behind it."""
        triggers = load(PUBLISH)[True]
        assert "issues" not in triggers
        assert set(triggers) == {"push", "workflow_dispatch"}

    def test_no_promotion_label_is_read_anywhere(self):
        text = PUBLISH.read_text(encoding="utf-8")
        assert "release:promote" not in text
        assert "ISSUE_BODY" not in text, "the issue-body marker plumbing went with the issues trigger"

    def test_the_release_notes_are_built_before_the_upload(self):
        """Publishing without tagging is the one unrecoverable ordering.

        PyPI would hold the final while nothing tagged it, cut a Release, or fired
        `release-published-announce.md` — the only announcement path there is.
        """
        jobs = load(PUBLISH)["jobs"]
        check_steps = " ".join(str(step) for step in jobs["check"]["steps"])
        assert "--manifest" in check_steps, "the batch manifest must be built in `check`"
        release_steps = " ".join(str(step) for step in jobs["release"]["steps"])
        assert "--manifest" not in release_steps

    def test_the_release_body_is_not_the_promotion_ask(self):
        """`markdown()` renders two things and only one belongs on a public page.

        The ask opens "Promote 3.7.0?" and closes with the ✅/❌ verbs and the
        `<!-- promote: -->` marker. Published as the Release body, every shipped
        release would permanently ask whether to ship it, and copies of the marker
        the promotion path trusts would litter public pages.
        """
        # Match the invocation, not the prose: the step carries a comment naming
        # `--markdown` to say why it is *not* used, and a bare substring test on
        # the flag would fail on the explanation.
        check_steps = " ".join(str(step) for step in load(PUBLISH)["jobs"]["check"]["steps"])
        assert "--manifest --release-notes" in check_steps
        assert "--manifest --markdown" not in check_steps

    def test_promotion_refuses_a_version_that_went_backwards(self):
        """A bare tag-exists check walks straight past the dual-PR race."""
        check_steps = " ".join(str(step) for step in load(PUBLISH)["jobs"]["check"]["steps"])
        assert "--check-promotable" in check_steps


class TestTheOfficialReleaseIsTheTreeThatWasTested:
    """Promotion is pinned to a commit, not to whatever `main` is at the ✅.

    The failure this replaces was silent by construction: drift was compared at
    *version* granularity, so a `main` that grew by commits which never moved the
    version line matched the ask exactly and shipped untested code with no warning
    at all. Each test below holds one half of the pin — resolve the commit, then
    use it everywhere.
    """

    def test_every_job_builds_the_pinned_commit(self):
        """Testing one tree and shipping another defeats the whole mechanism."""
        jobs = load(PUBLISH)["jobs"]
        for name in ("test", "publish", "release"):
            checkouts = [step for step in jobs[name]["steps"] if "actions/checkout" in str(step.get("uses", ""))]
            assert checkouts, f"{name} has no checkout"
            for step in checkouts:
                ref = str((step.get("with") or {}).get("ref", ""))
                assert "needs.check.outputs.sha" in ref, f"{name} checks out main, not the tested commit"

    def test_the_commit_is_resolved_before_anything_reads_the_tree(self):
        """`notes` and the version grep must see the pinned pyproject.toml."""
        steps = load(PUBLISH)["jobs"]["check"]["steps"]
        ids = [step.get("id") for step in steps if step.get("id")]
        assert ids.index("pin") < ids.index("notes") < ids.index("v")

    def test_the_pin_is_the_pushed_commit_and_reads_no_marker(self):
        """The sign-off IS the merge now. The hand-test markers live on the batch
        PR and gated the old promotion flow; the workflow releases the pushed tree
        and must not resurrect the marker plumbing."""
        pin = next(step for step in load(PUBLISH)["jobs"]["check"]["steps"] if step.get("id") == "pin")
        run = pin["run"]
        assert "git rev-parse HEAD" in run
        assert "exit 1" not in run
        assert "tested" not in run, "no marker resolution — beta_signoff.py owns the markers now"

    def test_a_versionless_merge_closes_out_green(self):
        """A human merge that never moved the version line — docs, CI — is the
        ordinary quiet case, not a broken release."""
        job = load(PUBLISH)["jobs"]["check"]
        run = next(step for step in job["steps"] if step.get("id") == "v")["run"]
        assert "is already released" in run
        assert "go=false" in run and "go=true" in run
        for name in ("test", "publish", "release"):
            assert "needs.check.outputs.go" in str(load(PUBLISH)["jobs"][name].get("if", ""))

    def test_no_job_holds_an_issues_write(self):
        """The promotion-issue plumbing is gone; a write permission surviving it
        would be a grant with no consumer, waiting to be used by accident."""
        for name, job in load(PUBLISH)["jobs"].items():
            assert "issues" not in (job.get("permissions") or {}), name


class TestTheBetaChannelTagsWhatItPublished:
    """`beta/X.Y.ZrcN` is the only durable record that a pre-release exists.

    Without it "the latest pre-release" can only be computed as a commit count,
    which every docs merge raises past anything on PyPI — and that number was
    being handed to a human as a `pip install --pre` line.
    """

    def test_the_tag_is_pushed_after_the_upload_and_not_before(self):
        """A tag created first promises a file a failed upload never produced."""
        steps = load(PUBLISH_BETA)["jobs"]["publish"]["steps"]
        names = [str(step.get("name") or step.get("uses") or step.get("run", ""))[:40] for step in steps]
        upload = next(i for i, step in enumerate(steps) if "gh-action-pypi-publish" in str(step.get("uses", "")))
        tag = next(i for i, name in enumerate(names) if "Tag the published pre-release" in name)
        assert upload < tag, names

    def test_it_holds_the_write_permission_the_tag_needs(self):
        assert load(PUBLISH_BETA)["jobs"]["publish"]["permissions"]["contents"] == "write"

    def test_the_tag_is_idempotent(self):
        """A re-run recomputes the same rc; it must not fail on the existing tag."""
        step = next(
            s for s in load(PUBLISH_BETA)["jobs"]["publish"]["steps"] if "Tag the published" in str(s.get("name", ""))
        )
        assert "rev-parse -q --verify" in step["run"] and "exit 0" in step["run"]

    def test_the_tag_stays_out_of_the_finals_namespace(self):
        """`last_final_tag` globs `v*`; a `v…rc…` tag would poison every count."""
        step = next(
            s for s in load(PUBLISH_BETA)["jobs"]["publish"]["steps"] if "Tag the published" in str(s.get("name", ""))
        )
        assert "beta/" in str(step.get("env", {}).get("TAG", ""))
        assert not re.search(r"TAG:\s*v\$", str(step.get("env", {})))


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


class TestOnlyTheOfficialReleaseIsLatest:
    """GitHub's "Latest" badge is picked by publish date, not by tag namespace.

    Three channels write to the same repo on their own version lines, so
    whichever published most recently wins it by default — which is how a
    `core-v0.3.0` Go wheel came to be the release page for a product on
    `v3.7.0`. Nothing goes red when that happens: the badge is the only
    symptom, and it is on the release page rather than in CI.
    """

    def _release_steps(self) -> dict[str, dict]:
        """Every GitHub Release any workflow can create, by workflow filename.

        Discovered rather than listed: a channel added later is the whole risk
        here, and one named in a test is one that already exists.
        """
        found: dict[str, dict] = {}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for job in load(path)["jobs"].values():
                for step in job.get("steps", []):
                    if "action-gh-release" in str(step.get("uses", "")):
                        found[path.name] = step["with"]
        return found

    def _release_inputs(self, path: Path) -> dict:
        steps = self._release_steps()
        assert path.name in steps, f"{path.name} creates no GitHub Release"
        return steps[path.name]

    def test_every_channel_decides_the_badge_on_purpose(self):
        """The default is date-based, so an undeclared channel steals the badge
        the first time it publishes after a release. Declaring it is the point —
        this fails for a new workflow whether it wants the badge or not."""
        undeclared = [name for name, inputs in self._release_steps().items() if "make_latest" not in inputs]
        assert not undeclared, f"release steps with no make_latest: {undeclared}"

    def test_exactly_one_channel_claims_the_badge(self):
        claimants = [name for name, inputs in self._release_steps().items() if inputs.get("make_latest") is True]
        assert claimants == ["publish.yml"]

    def test_the_official_release_claims_latest(self):
        assert self._release_inputs(PUBLISH)["make_latest"] is True

    def test_the_channels_stay_on_separate_tag_namespaces(self):
        assert self._release_inputs(PUBLISH)["tag_name"].startswith("v")


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
