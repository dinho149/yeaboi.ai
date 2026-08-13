"""Every workflow file must be one GitHub Actions can actually parse.

Two real outages sit behind this file, and both were invisible to every other
check in the repo:

`pr-feedback.yml` carried a trigger GitHub rejects, from its first commit. The
whole file was therefore unparseable: 108 runs over four days, every one a
zero-job failure attributed to `push` — an event it does not declare — while
GitHub reported the workflow's name as its own file path. The merge gate that
five files describe as the thing making review enforceable never ran once, and
adding its status to the ruleset turned that into a permanent block on every PR.

`publish.yml` was broken a different way: a multi-line shell string inside a
block scalar was dedented to column 1, which ended the scalar and created a bogus
top-level key `_note`. It still parsed as YAML, and `test_publish_workflows.py`
still found its jobs and triggers, so nothing failed.

`actionlint` in CI catches both now. These tests are the cheap second opinion
that needs no network and no binary: the schema check below is three lines and
would have caught `_note` on the commit that introduced it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))

# Everything GitHub allows at the top level of a workflow. Anything else means a
# key was created by accident — almost always by a block scalar ending early.
TOP_LEVEL = frozenset({"name", "on", "permissions", "env", "defaults", "concurrency", "jobs", "run-name"})

# Events this repo's workflows legitimately declare. `pull_request_review_thread`
# is deliberately absent: GitHub documents it, and rejected it here.
KNOWN_EVENTS = frozenset(
    {
        "push",
        "pull_request",
        "pull_request_target",
        "issue_comment",
        "issues",
        "pull_request_review",
        "pull_request_review_comment",
        "workflow_run",
        "workflow_dispatch",
        "schedule",
        "release",
    }
)


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def ids(paths: list[Path]) -> list[str]:
    return [p.name for p in paths]


assert WORKFLOWS, "no workflow files found — this suite would pass vacuously"


@pytest.mark.parametrize("path", WORKFLOWS, ids=ids(WORKFLOWS))
class TestEveryWorkflowParses:
    def test_it_is_a_mapping(self, path: Path):
        assert isinstance(load(path), dict)

    def test_no_stray_top_level_keys(self, path: Path):
        """The `_note` bug, in three lines.

        A shell continuation line at column 1 inside a `run: |` block ends the
        scalar and becomes a top-level key. YAML is happy; GitHub refuses the
        whole file and every run becomes a zero-job failure.
        """
        keys = {k for k in load(path) if isinstance(k, str)}
        assert keys <= TOP_LEVEL, f"{path.name} has top-level keys GitHub will reject: {sorted(keys - TOP_LEVEL)}"

    def test_it_declares_a_name_and_a_trigger(self, path: Path):
        data = load(path)
        assert data.get("name"), f"{path.name} has no `name:` — GitHub falls back to the path"
        # PyYAML parses the `on:` key as the boolean True.
        assert True in data, f"{path.name} declares no triggers"

    def test_every_trigger_is_one_github_accepts(self, path: Path):
        """The `pull_request_review_thread` bug.

        An unknown event does not disable one trigger — it makes the entire file
        unparseable, so the workflow silently never runs at all.
        """
        triggers = load(path)[True]
        names = (
            set(triggers) if isinstance(triggers, dict) else {triggers} if isinstance(triggers, str) else set(triggers)
        )
        assert names <= KNOWN_EVENTS, f"{path.name} declares unknown events: {sorted(names - KNOWN_EVENTS)}"

    def test_it_has_at_least_one_job(self, path: Path):
        assert load(path).get("jobs"), f"{path.name} defines no jobs"


class TestTheGateSurvives:
    """Specific properties of pr-feedback.yml, the file that was dead for four days."""

    def _gate(self) -> dict:
        return load(ROOT / ".github" / "workflows" / "pr-feedback.yml")

    def test_the_rejected_trigger_stays_gone(self):
        assert "pull_request_review_thread" not in self._gate()[True], (
            "this trigger made the whole workflow unparseable; re-adding it takes the gate back to dead"
        )

    def test_it_keeps_a_manual_refresh_path(self):
        """Losing the resolve event means a stale red needs some way to clear."""
        triggers = self._gate()[True]
        assert "workflow_dispatch" in triggers
        assert "pr" in triggers["workflow_dispatch"]["inputs"]

    def test_it_still_runs_on_the_events_that_matter(self):
        triggers = self._gate()[True]
        for event in ("pull_request_target", "issue_comment", "pull_request_review", "workflow_run"):
            assert event in triggers, f"{event} is how the gate learns the answer changed"


# The check names the `main-branch` ruleset makes required. Verified against the
# live ruleset rather than against the docs, which described them under short
# names (`Lint`, `Format check`) that match no posted check.
#
# Nothing in this repo can edit that ruleset, so this list is a copy — and a copy
# is exactly what needs a test. `make cowork-check` probes the live one.
REQUIRED_CONTEXTS = frozenset(
    {
        "Unit tests",
        "Integration & contract tests",
        "Lint (ruff)",
        "Format check (ruff)",
        "Security scan",
    }
)


class TestRequiredChecksAlwaysReport:
    """A required context must come from a job that cannot be skipped.

    This is the same shape as the outage in this module's header, approached
    from the other side. There, a required context existed and nothing posted
    it; here, the risk is a job that posts one *sometimes* — scoping `ci.yml` by
    changed paths makes that a one-line mistake. GitHub does report a skipped job
    as a passing required check, but only when the workflow ran and evaluated the
    `if:`; a job skipped because its `needs:` was skipped, or a workflow filtered
    out by `paths:`, produces no check at all and blocks the PR forever.

    So the rule is blunt on purpose: the five required jobs run every time, and
    scoping may change what they *do*, never whether they report.
    """

    def _ci_jobs(self) -> dict:
        data = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
        return data["jobs"]

    def test_every_required_context_is_produced_by_ci(self):
        names = {job.get("name") for job in self._ci_jobs().values()}
        missing = REQUIRED_CONTEXTS - names
        assert not missing, f"no ci.yml job posts {sorted(missing)} — the ruleset would wait forever"

    @pytest.mark.parametrize("context", sorted(REQUIRED_CONTEXTS))
    def test_a_required_job_is_never_conditional(self, context: str):
        job = next(j for j in self._ci_jobs().values() if j.get("name") == context)
        assert "if" not in job, (
            f"{context!r} is a required status check, so its job must not carry an `if:` — "
            "scope it by changing what it runs, not whether it runs"
        )

    @pytest.mark.parametrize("context", sorted(REQUIRED_CONTEXTS))
    def test_a_required_job_never_depends_on_a_skippable_one(self, context: str):
        """`needs:` on a job that can skip makes this job skip too, and a job
        that never ran posts no check at all."""
        jobs = self._ci_jobs()
        job = next(j for j in jobs.values() if j.get("name") == context)
        needs = job.get("needs") or []
        for dependency in [needs] if isinstance(needs, str) else needs:
            assert "if" not in jobs[dependency], (
                f"{context!r} needs {dependency!r}, which is conditional — if {dependency!r} skips, "
                f"{context!r} never runs and the required check never appears"
            )

    def test_the_workflow_linter_is_never_conditional_either(self):
        """Not a required context, but gating it on `.github/**` would recreate
        the blind spot it exists to close: an unparseable workflow is invisible
        to every tool that reads workflows, including the one that would say so."""
        job = next(j for j in self._ci_jobs().values() if j.get("name", "").startswith("Workflow lint"))
        assert "if" not in job


class TestTheScopeJobCannotBreakARequiredCheck:
    """`unit` is a required context and it `needs: scope`.

    A dependency that FAILS skips the dependent job exactly as a dependency that
    was skipped does — and `test_a_required_job_never_depends_on_a_skippable_one`
    above only checks for an `if:`, which is not how this one would go wrong. The
    scope job resolves a git diff and runs a script; a fetch blip, a branch with
    no common ancestor, or any traceback would take `Unit tests` down with it and
    leave the PR unmergeable with nothing in the UI naming the cause.

    So the step is written to never exit non-zero, and these assert that it stays
    that way.
    """

    def _script_output_keys(self) -> set[str]:
        """Every key `scripts/test_scope.py --github-output` actually writes."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.txt"
            changed = Path(tmp) / "changed.txt"
            changed.write_text("src/yeaboi/poker/board.py\n", encoding="utf-8")
            subprocess.run(
                [sys.executable, "scripts/test_scope.py", "--changed-files", str(changed), "--github-output"],
                cwd=ROOT,
                env={**os.environ, "GITHUB_OUTPUT": str(out)},
                check=True,
                capture_output=True,
            )
            return {line.split("=", 1)[0] for line in out.read_text(encoding="utf-8").splitlines() if line}

    def _scope_step(self) -> dict:
        data = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
        steps = data["jobs"]["scope"]["steps"]
        return next(s for s in steps if s.get("id") == "scope")

    def test_the_step_does_not_abort_on_the_first_error(self):
        script = self._scope_step()["run"]
        assert "set -euo pipefail" not in script, (
            "`set -e` here aborts the step on any failing command, which fails the "
            "job, which skips `Unit tests` — a required context that then never posts"
        )
        assert "FAIL-SAFE" in script, "the reason this step is shaped oddly must stay written down"

    def test_the_fallback_writes_every_output_key(self):
        """The fallback duplicates the script's output keys in bash, so it needs
        a guard: a key added to `test_scope.py` and not here means the fallback
        produces an empty output and the dependent `if:` reads it as false.

        Ground truth is the script's own run, not a second hand-written list —
        one more copy of the keys is one more thing to drift."""
        script = self._scope_step()["run"]
        fallback = script.split("::warning::", 1)[1]
        written = set(re.findall(r'echo "([a-z_]+)=', fallback))
        assert written == self._script_output_keys(), (
            f"fallback writes {sorted(written)}, script writes {sorted(self._script_output_keys())}"
        )

    def test_every_declared_output_is_in_the_fallback(self):
        data = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
        declared = set(data["jobs"]["scope"]["outputs"])
        fallback = self._scope_step()["run"].split("::warning::", 1)[1]
        assert declared == set(re.findall(r'echo "([a-z_]+)=', fallback))

    def test_the_fallback_runs_everything_rather_than_nothing(self):
        """Which direction it fails in is the whole point."""
        fallback = self._scope_step()["run"].split("::warning::", 1)[1]
        assert 'echo "full=true"' in fallback
        assert "=false" not in fallback
