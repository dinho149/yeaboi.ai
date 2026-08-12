"""Standing guards over `.github/workflows/claude.yml`'s `implement` job.

This is the bridge from a human's approval to code: adding `claude-implement` to
an issue starts an unattended run that opens a PR. It ran for the first time on
2026-08-09, on issue #172, and did nothing at all — 31 turns, 17 denied tool
calls, `is_error: false`, a green check, no branch. It had never carried an
`--allowedTools` grant, so the action's `settingSources: [user, project, local]`
left the repo's deliberately read-only `.claude/settings.json` allowlist
governing an unattended job that has to write code. Every other Claude-invoking
workflow in this repo passes a grant; this one's absence was invisible precisely
because the job reported success.

The class of invariant is the same one `test_codeql_triage.py` pins for the other
unattended job that writes code, and for the same reason: nothing at run time
notices when one of these stops being true. A denied tool call, a missing
toolchain and a security issue routed to the wrong model all look identical from
the outside — a green run that produced nothing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="the picker script shells out to jq")

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "claude.yml"


@pytest.fixture(scope="module")
def implement() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["implement"]


@pytest.fixture(scope="module")
def action(implement) -> dict:
    """The `claude-code-action` step — the one that does the work."""
    return next(step for step in implement["steps"] if "claude-code-action" in str(step.get("uses", "")))


class TestTheGrant:
    def test_the_job_declares_allowed_tools(self, action):
        """The regression that made this job a no-op for its entire existence.

        Without it the action inherits `.claude/settings.json`, which grants
        `Bash(git status)` and `Bash(gh pr view:*)` and nothing that writes — the
        right answer for an interactive session where a human approves each write,
        and the wrong one for a job whose whole purpose is to commit and push.
        """
        assert "--allowedTools" in action["with"]["claude_args"], (
            "an implement job with no tool grant inherits the repo's read-only allowlist and "
            "exits green having written nothing — see issue #172, run 31312744716"
        )

    @pytest.mark.parametrize("tool", ["Edit", "Write", "Task", "Bash(git:*)", "Bash(gh pr:*)", "Bash(make:*)"])
    def test_the_grant_covers_what_the_prompt_asks_for(self, action, tool):
        """Each of these is a numbered step in the prompt below it: implement (Edit,
        Write), spawn the scribe and the reviewer (Task), branch and push (git),
        open the PR (gh pr), run the gate (make)."""
        assert tool in action["with"]["claude_args"]


class TestTheModelTier:
    """The picker is a self-contained shell script with one output, so it is run
    rather than read.

    Asserting that `"type:security"` and `"YEABOI_MODEL_DEEP"` both appear in the
    script proves nothing: swapping the two branches keeps every substring in
    place and routes security straight to `heavy`, which is the one thing
    cowork/models.md forbids. These execute it instead.
    """

    @staticmethod
    def _pick(implement: dict, labels: list[str], title: str, tmp_path: Path) -> str:
        """Run the picker's own shell and return the tier id it selected."""
        script = next(step for step in implement["steps"] if step.get("id") == "model")["run"]
        # The only interpolations in the script are the two tier expressions;
        # stand each one up as its own tier name so the branch taken is visible.
        script = re.sub(r"\$\{\{[^}]*YEABOI_MODEL_(\w+)[^}]*\}\}", r"\1", script)
        output = tmp_path / "github_output"
        output.touch()
        subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            check=True,
            capture_output=True,
            env={
                "PATH": os.environ["PATH"],
                "LABELS": json.dumps(labels),
                "TITLE": title,
                "GITHUB_OUTPUT": str(output),
            },
        )
        return output.read_text().strip().removeprefix("id=")

    @pytest.mark.parametrize(
        ("labels", "title"),
        [
            (["type:security", "workstream:web-ux"], "[security][web-ux] unpkg lenis has no SRI"),
            # The workstream axis. models.md scopes the rule to "any auto-lane item
            # in the `security` workstream", and these are separate labels — a
            # security-workstream bug is `[bug][security]`, which a type-only check
            # never sees. #172, the first issue this job ever received, was the
            # other shape.
            (["type:bug", "workstream:security"], "[bug][security] redaction misses a token"),
            # Labels lost entirely — #172's whole set was replaced while it was
            # being approved, so the title is the copy that survives.
            ([], "[bug][security] redaction misses a token"),
            ([], "[security][web-ux] unpkg lenis has no SRI"),
        ],
        ids=["type-label", "workstream-label", "title-only-workstream", "title-only-type"],
    )
    def test_security_never_reaches_heavy(self, implement, labels, title, tmp_path):
        assert self._pick(implement, labels, title, tmp_path) == "DEEP"

    @pytest.mark.parametrize(
        ("labels", "title"),
        [
            (["type:bug", "workstream:platform"], "[bug][platform] auto-version rejects bot PRs"),
            ([], "[chore][tui-ux] dedupe viewport math"),
        ],
        ids=["labelled", "unlabelled"],
    )
    def test_everything_else_still_gets_the_heavy_tier(self, implement, labels, title, tmp_path):
        """The narrowing must not quietly become "everything runs on deep" — heavy
        exists for exactly this job, and paying deep prices for every chore would
        be a silent regression in the other direction."""
        assert self._pick(implement, labels, title, tmp_path) == "HEAVY"

    def test_the_tier_is_chosen_rather_than_hardcoded(self, action):
        args = action["with"]["claude_args"]
        assert "steps.model.outputs.id" in args
        assert "YEABOI_MODEL_HEAVY" not in args, "the tier must come from the picker, which excludes security"


class TestTheToolchain:
    def test_uv_is_installed_before_claude_runs(self, implement):
        """Prompt step 6 makes `make test` + `make lint` this job's own gate. With
        no toolchain both are `command not found`, so the gate passes by never
        having run — a second, independent way for this job to look green."""
        names = [str(step.get("uses") or step.get("run") or step.get("name")) for step in implement["steps"]]
        uv = next(i for i, name in enumerate(names) if "setup-uv" in name)
        claude = next(i for i, name in enumerate(names) if "claude-code-action" in name)
        assert uv < claude
        assert any("uv sync" in name for name in names)


class TestTheOutcomeGuard:
    @staticmethod
    def _guard(implement):
        """The guard step, selected by name rather than by position.

        It used to be `steps[-1]`, which was true only for as long as nothing ran
        after it — and the fix for a guard that dies silently is precisely to add a
        reporting step after it.
        """
        return next(step for step in implement["steps"] if step.get("name") == "Assert the run produced something")

    def test_the_job_asserts_it_produced_something(self, implement):
        """A green run that wrote no code is what hid all of the above.

        Nothing downstream would have caught it either: `digest.md` explicitly
        refuses to age out an issue carrying `claude-implement`, so #172 became
        invisible to every routine in the fleet the moment it was approved.
        """
        guard = self._guard(implement)
        assert "permission_denials_count" in guard["run"], "a denial in this job is always a misconfiguration"
        assert "feature/issue-" in guard["run"], "the branch prompt step 4 promises is the evidence of work"
        assert guard.get("if", "").startswith("always()")

    def test_only_a_bot_comment_counts_as_the_ambiguous_exit(self, implement):
        """The relay comments on the issue seconds before this job starts.

        `cron/slack-relay.md` writes `approved via Slack ✅ by …` under a *human*
        login — the Slack connector posts as the person — and that is the normal
        way this job gets triggered at all. A guard that accepted any recent
        comment as "the run asked a question and stopped" would therefore wave
        through a silent no-op on the single most common path into the job.
        """
        guard = self._guard(implement)
        assert 'endswith("[bot]")' in guard["run"]
