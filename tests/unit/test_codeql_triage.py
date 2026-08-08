"""Standing guards over the CodeQL triage loop.

`codeql-triage.yml` is the only unattended job in this repo that writes code and
enables its own merge. What makes that acceptable is a set of invariants that are
each one careless edit away from being untrue, and none of which any other check
would notice:

* the auto-fix allowlist is a closed, reviewed list — not "whatever the model
  thought looked mechanical";
* the human-readable allowlist in `cowork/house-rules.md` points at the
  machine-readable one, so the two cannot drift into disagreeing;
* the job merges via `--auto` and never directly, so the main-branch ruleset is
  what actually decides;
* it runs at `deep`, because `cowork/models.md` forbids security work on `heavy`.

The last class here is different in kind: it pins the *fix* for the 25
`actions/unpinned-tag` alerts, so the largest single class of findings this repo
has carried cannot silently come back one `uses:` line at a time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TRIAGE_WORKFLOW = WORKFLOWS / "codeql-triage.yml"
CODEQL_WORKFLOW = WORKFLOWS / "codeql.yml"
POLICY = REPO_ROOT / ".github" / "codeql" / "triage-policy.yml"
CONFIG = REPO_ROOT / ".github" / "codeql" / "config.yml"
HOUSE_RULES = REPO_ROOT / "cowork" / "house-rules.md"

# Actions published by GitHub itself. CodeQL's `actions/unpinned-tag` query does
# not flag these, and pinning them would mean hand-rotating SHAs for `checkout`
# on every release for no threat-model gain — the trust boundary is the same one
# that already runs the workflow.
FIRST_PARTY_OWNERS = ("actions", "github")

_SHA = re.compile(r"^[0-9a-f]{40}$")
# Trailing `# vX.Y.Z` comments are the point of the pin, so do not anchor to EOL.
_USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.MULTILINE)


def _policy() -> dict:
    return yaml.safe_load(POLICY.read_text())


def _triage() -> dict:
    return yaml.safe_load(TRIAGE_WORKFLOW.read_text())


def _third_party_uses() -> list[tuple[Path, str]]:
    """Every `uses:` reference in every workflow that is not GitHub's own."""
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for ref in _USES.findall(path.read_text()):
            if ref.split("/", 1)[0] not in FIRST_PARTY_OWNERS:
                found.append((path, ref))
    return found


class TestTriagePolicy:
    """The allowlist is the whole control. It must stay closed and reviewed."""

    def test_policy_parses_with_the_shape_the_workflow_reads(self):
        policy = _policy()
        assert isinstance(policy["max_batch"], int) and policy["max_batch"] > 0
        for entry in policy["auto"]:
            assert entry["id"] and entry["fix"], "every auto rule needs a prescribed fix"
        for entry in policy["propose"]:
            assert entry["id"] and entry["why"], "every propose rule records why, so nobody re-litigates it"

    def test_no_rule_is_both_auto_and_propose(self):
        policy = _policy()
        overlap = {e["id"] for e in policy["auto"]} & {e["id"] for e in policy["propose"]}
        assert not overlap, f"a rule cannot be both auto-fixed and proposed: {overlap}"

    def test_untrusted_checkout_is_never_auto_fixed(self):
        """auto-version.yml's PR-head checkout is a documented tradeoff.

        Removing it breaks the `workflow_run` chain that Claude Review and the
        Dependabot verifier both depend on — a redesign of the release path, not
        an alert fix. It must stay a human decision.
        """
        policy = _policy()
        assert "actions/untrusted-checkout/medium" not in {e["id"] for e in policy["auto"]}
        assert "actions/untrusted-checkout/medium" in {e["id"] for e in policy["propose"]}

    def test_house_rules_points_at_the_policy_file(self):
        """The two allowlists must not be able to drift.

        `house-rules.md` is the allowlist a human reads and `triage-policy.yml`
        is the one the workflow reads. They stay honest by the markdown
        *referencing* the YAML rather than restating its rule ids — so the check
        is that the reference survives, not that two lists match.
        """
        text = HOUSE_RULES.read_text()
        assert ".github/codeql/triage-policy.yml" in text, (
            "cowork/house-rules.md must name the policy file, or its auto-lane list "
            "silently stops covering the rules codeql-triage.yml actually fixes."
        )


class TestTriageWorkflow:
    def test_runs_at_deep_never_heavy(self):
        """cowork/models.md: "Security never runs on `heavy`"."""
        args = _triage()["jobs"]["triage"]["steps"][-1]["with"]["claude_args"]
        assert "vars.YEABOI_MODEL_DEEP" in args
        assert "YEABOI_MODEL_HEAVY" not in args

    def test_merges_only_via_auto_merge(self):
        """`--auto` defers to the required checks; a direct merge would not.

        The invariant is about the *invocation*: `--admin` appears in this file
        on purpose, in the sentence forbidding it, so asserting its absence
        would be asserting the prose stayed unhelpful.
        """
        text = TRIAGE_WORKFLOW.read_text()
        assert "--auto --squash" in text
        assert not re.search(r"gh pr merge[^\n]*--admin", text), (
            "an admin merge bypasses the main-branch ruleset, which is the only thing "
            "standing between this job and an unreviewed merge"
        )

    def test_reads_alerts_and_can_open_a_pr(self):
        perms = _triage()["permissions"]
        assert perms["security-events"] == "read", "reading the alerts is the entire point"
        assert perms["contents"] == "write" and perms["pull-requests"] == "write"

    def test_concurrent_runs_queue_rather_than_cancel(self):
        """A cancelled run can leave a pushed branch with no PR behind it."""
        concurrency = _triage()["concurrency"]
        assert concurrency["group"] == "codeql-triage"
        assert concurrency["cancel-in-progress"] is False

    def test_survey_scratch_files_are_gitignored(self):
        """The job commits, so a stray `git add` must not be able to sweep in a
        file listing every open security alert."""
        ignored = (REPO_ROOT / ".gitignore").read_text()
        for name in ("alerts-raw.json", "codeql-auto.json", "codeql-propose.json"):
            assert f"/{name}" in ignored


class TestCodeqlConfig:
    def test_codeql_uses_the_config_file(self):
        init = next(
            step
            for step in yaml.safe_load(CODEQL_WORKFLOW.read_text())["jobs"]["analyze"]["steps"]
            if "codeql-action/init" in str(step.get("uses", ""))
        )
        assert init["with"]["config-file"] == "./.github/codeql/config.yml"

    def test_tests_are_excluded_from_analysis(self):
        """`security-extended` reads `assert "host" in out` as an authorization
        check. Twelve such assertions were the largest class of open alerts and
        none was a vulnerability; ruff-bandit still covers tests/ via
        `make security`."""
        assert "tests/**" in yaml.safe_load(CONFIG.read_text())["paths-ignore"]


class TestActionsArePinned:
    """The standing fix for `actions/unpinned-tag`, so it cannot come back.

    A moving tag is a mutable reference to code that runs with this repo's
    tokens: whoever controls the tag controls what executes next Tuesday.
    Dependabot's `github-actions` group rotates SHA pins weekly, so pinning
    costs nothing in freshness.
    """

    @pytest.mark.parametrize(
        ("path", "ref"), _third_party_uses(), ids=lambda v: v.name if isinstance(v, Path) else str(v)
    )
    def test_third_party_action_is_pinned_to_a_sha(self, path: Path, ref: str):
        action, _, version = ref.partition("@")
        assert _SHA.match(version), (
            f"{path.name} uses {action}@{version} — pin it to the 40-char commit SHA that ref "
            f"points at today, with the version as a trailing `# vX.Y.Z` comment "
            f"(`gh api repos/{action.split('/')[0]}/.../git/ref/tags/{version}`). "
            "Do not bump the major while pinning."
        )

    def test_the_repo_actually_has_third_party_actions_to_check(self):
        """Guards the guard: a rename of the workflows dir would make every
        parametrized case above vanish and the suite still pass green."""
        assert len(_third_party_uses()) > 10
