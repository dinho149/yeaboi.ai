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
PUBLISH_CORE = WORKFLOWS / "publish-core.yml"
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

        # `name` as the last fallback: the duplicate-approval step has no `id` and
        # no `uses`, so keying on those two alone put a bare `None` in this list
        # and made the exemption below unmatchable.
        def label(step: dict) -> str:
            return step.get("id") or step.get("uses") or step.get("name") or "<unnamed>"

        ungated = {label(s): str(s.get("if", "")) for s in after if guard not in str(s.get("if", ""))}
        # One exemption, and it has to earn it: a step with no lane guard is only
        # safe if it cannot run on a push at all.
        assert all("issues" in cond for cond in ungated.values()), (
            f"steps after `lane` with neither the lane guard nor an issues-only condition: "
            f"{ {k: v for k, v in ungated.items() if 'issues' not in v} }"
        )

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

    def test_it_prefers_what_was_tested_over_what_was_asked(self):
        pin = next(step for step in load(PUBLISH)["jobs"]["check"]["steps"] if step.get("id") == "pin")
        run = pin["run"]
        assert run.index("marker tested") < run.index("marker beta"), "the sign-off wins over the ask"
        assert "git rev-parse -q --verify" in run, "a marker from a public issue is verified, never trusted"

    def test_a_missing_marker_falls_back_rather_than_failing(self):
        """Refusing here strands a promotion the human already approved."""
        run = next(step for step in load(PUBLISH)["jobs"]["check"]["steps"] if step.get("id") == "pin")["run"]
        assert "git rev-parse HEAD" in run
        assert "exit 1" not in run

    def test_drift_is_measured_in_commits_not_versions(self):
        run = next(step for step in load(PUBLISH)["jobs"]["check"]["steps"] if step.get("id") == "v")["run"]
        assert "git log --oneline --no-merges" in run
        assert "origin/main" in run, "the checkout is detached at the pinned commit by then"
        assert "left_behind" in run

    def test_a_duplicate_approval_closes_out_green(self):
        """Two ✅s in the relay's window is a race, not a broken release."""
        job = load(PUBLISH)["jobs"]["check"]
        run = next(step for step in job["steps"] if step.get("id") == "v")["run"]
        assert "is already released" in run
        assert "go=false" in run and "go=true" in run
        assert job["permissions"]["issues"] == "write"
        for name in ("test", "publish", "release"):
            assert "needs.check.outputs.go" in str(load(PUBLISH)["jobs"][name].get("if", ""))


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

    def test_the_core_wheel_disclaims_latest(self):
        """The sidecar is a dependency of the product, not the product."""
        assert self._release_inputs(PUBLISH_CORE)["make_latest"] is False

    def test_the_channels_stay_on_separate_tag_namespaces(self):
        assert self._release_inputs(PUBLISH)["tag_name"].startswith("v")
        assert self._release_inputs(PUBLISH_CORE)["tag_name"].startswith("core-v")


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


class TestTheReleaseStepsCannotDieOnTheirOwnPlumbing:
    """Two shell traps that fail a promotion for a reason nobody would guess.

    Both are the same shape: a step that is *reporting* something optional takes
    down the release it was reporting on. And both bite only in the case the line
    exists for — more than fifty commits left behind, or a `gh` call that did not
    answer — so a green run proves nothing about either.
    """

    def _step(self, needle: str) -> str:
        text = PUBLISH.read_text(encoding="utf-8")
        assert needle in text, f"the line under test moved: {needle}"
        return text

    def test_the_leftover_commit_list_does_not_pipe_into_head(self):
        """`git log | head -50` under `set -o pipefail`: SIGPIPE → 141 → dead step.

        The same file explains this trap fifteen lines above, for two other
        commands. `-n 50` asks git for fifty and never closes a pipe early.
        """
        text = self._step("origin/main")
        for line in text.splitlines():
            if "git log" in line and "origin/main" in line:
                assert "| head" not in line, "git log piped into head can take SIGPIPE and fail the promotion"

    def test_reading_the_tested_marker_cannot_hard_fail(self):
        """`marker`'s own `|| true` covers its greps, not the `gh` process.

        The block's stated contract is that a missing marker means less pinning,
        not no release — and a rate-limited `gh` must land in the same place as
        an absent comment.
        """
        text = self._step("--json comments")
        # The assignment spans several lines (the author filter), so the check is
        # on where it ends rather than on one line of it.
        assert "marker tested || true)" in text, "a failed `gh issue view` would abort the promotion"


class TestTheRepoSetupJobIsRedOnlyForRealProblems:
    """`--strict` is right where the write can be attempted, and wrong where it cannot.

    Without `AUTO_VERSION_PAT` the variable half 403s by design — the job warns
    about exactly that two steps earlier. Failing on it puts a permanent red on a
    repo that is behaving as documented, and a check that is always red is read
    no more often than one that is always green.
    """

    SETUP = WORKFLOWS / "cowork-repo-setup.yml"

    def test_strict_is_gated_on_the_token_that_makes_it_meaningful(self):
        text = self.SETUP.read_text(encoding="utf-8")
        apply_step = text.split("name: Apply", 1)[1].split("name: Verify", 1)[0]
        assert "--strict" in apply_step
        assert "HAS_PAT" in apply_step, "--strict runs unconditionally, so a missing PAT is a red job"
