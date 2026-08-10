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

from pathlib import Path

import pytest
import yaml

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
    def test_a_security_issue_never_runs_on_heavy(self, implement):
        """cowork/models.md: "Security never runs on `heavy`."

        Fable 5 reroutes cybersecurity queries to less capable models. Tolerable in
        a chat; not in an unattended run that may edit `fs_policy.py` or a CSP
        invariant, because the report would read the same either way. The direct
        analogue of `test_codeql_triage.py::test_runs_at_deep_never_heavy` — and
        #172, the first issue this job ever received, was `type:security`.
        """
        picker = next(step for step in implement["steps"] if step.get("id") == "model")
        assert "type:security" in picker["run"]
        assert "YEABOI_MODEL_DEEP" in picker["run"]

    def test_the_tier_is_chosen_rather_than_hardcoded(self, implement, action):
        args = action["with"]["claude_args"]
        assert "steps.model.outputs.id" in args
        assert "YEABOI_MODEL_HEAVY" not in args, "the tier must come from the picker, which excludes security"

    def test_the_title_is_checked_as_well_as_the_label(self, implement):
        """A label can be lost — #172's whole set was replaced while it was being
        approved. The `[type][workstream]` title prefix cowork-scribe writes at
        filing time is the copy that survives that, so it is the second signal."""
        picker = next(step for step in implement["steps"] if step.get("id") == "model")
        assert "TITLE" in picker["run"] and "security" in picker["run"]


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
    def test_the_job_asserts_it_produced_something(self, implement):
        """A green run that wrote no code is what hid all of the above.

        Nothing downstream would have caught it either: `digest.md` explicitly
        refuses to age out an issue carrying `claude-implement`, so #172 became
        invisible to every routine in the fleet the moment it was approved.
        """
        guard = implement["steps"][-1]
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
        guard = implement["steps"][-1]
        assert 'endswith("[bot]")' in guard["run"]
