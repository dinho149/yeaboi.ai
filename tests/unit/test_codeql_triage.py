"""Standing guards over the CodeQL triage loop.

`codeql-triage.yml` is the only unattended job in this repo that writes code and
enables its own merge. What makes that acceptable is a set of invariants that are
each one careless edit away from being untrue, and none of which any other check
would notice:

* the auto-fix allowlist is a closed, reviewed list — not "whatever the model
  thought looked mechanical";
* the job merges via `--auto` and never directly, so the main-branch ruleset is
  what actually decides;
* it runs at `deep`, never `heavy`.

The last class here is different in kind: it pins the *fix* for the 25
`actions/unpinned-tag` alerts, so the largest single class of findings this repo
has carried cannot silently come back one `uses:` line at a time.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import codeql_triage  # noqa: E402  — scripts/ is not a package

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TRIAGE_WORKFLOW = WORKFLOWS / "codeql-triage.yml"
CODEQL_WORKFLOW = WORKFLOWS / "codeql.yml"
POLICY = REPO_ROOT / ".github" / "codeql" / "triage-policy.yml"
CONFIG = REPO_ROOT / ".github" / "codeql" / "config.yml"

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


class TestTriageWorkflow:
    def test_runs_at_deep_never_heavy(self):
        """Security never runs on `heavy`."""
        args = _triage()["jobs"]["triage"]["steps"][-1]["with"]["claude_args"]
        assert "vars.YEABOI_MODEL_DEEP" in args
        assert "YEABOI_MODEL_HEAVY" not in args

    def test_it_never_merges_and_never_arms_auto_merge(self):
        """Unattended PRs wait for a human's merge, never their own.

        Any merge invocation surviving here — `--auto` included — would put a
        machine merge on an unattended PR. The prompt is allowed to *forbid*
        merging by name; the runnable spelling must be absent.
        """
        text = TRIAGE_WORKFLOW.read_text()
        for line in text.splitlines():
            if re.search(r"gh pr merge[^\n]*--(auto|admin)", line):
                assert re.search(r"\bNEVER\b|\bnever\b|\bnot\b", line), f"merge invocation survives: {line.strip()}"
        assert "wait" in text.lower(), "the prompt must say the PR waits for a human merge"

    def test_the_pr_is_labelled_semver_none(self):
        """Without it, auto-version bumps the fix branch on an unattended PR."""
        assert "semver:none" in TRIAGE_WORKFLOW.read_text()

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
        for name in ("alerts-raw.json", "issues-raw.json", "codeql-auto.json", "codeql-propose.json"):
            assert f"/{name}" in ignored

    def test_classification_runs_from_the_script_not_a_heredoc(self):
        """The survey's three comparisons live in `scripts/codeql_triage.py`.

        Inlining them back into the shell step would put the accepted-path check,
        the new-location check and the undismissed-accept warning somewhere no
        unit test can reach — and each one's failure mode is a finding that never
        gets printed, which is precisely what nobody notices.
        """
        text = TRIAGE_WORKFLOW.read_text()
        assert "scripts/codeql_triage.py" in text
        assert "--alerts alerts-raw.json --issues issues-raw.json" in text, (
            "the classifier needs the issue list too, or a closed issue cannot be told from no issue"
        )
        assert "uv run python - <<" not in text, "the classifier must not move back into an untestable heredoc"

    def test_the_prompt_defers_to_the_classifier_action(self):
        """A prompt that re-derives "is this rule answered?" by searching issues
        reintroduces the rule-scoped dedup this change removed."""
        text = TRIAGE_WORKFLOW.read_text()
        for action in (codeql_triage.ACTION_OPEN, codeql_triage.ACTION_COMMENT, codeql_triage.ACTION_NEW_LOCATION):
            assert f"`{action}`" in text, f"the prompt must say what to do on action={action}"
        assert "--state all`. " not in text, "the old rule-scoped dedup instruction must be gone"


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


class TestAcceptedPaths:
    """`accepted:` scopes a rejection to the locations it was argued about.

    Before it existed, the propose lane deduped on the rule id alone against
    `--state all`, so closing one issue retired that rule at every file forever.
    The list is the fix, and it is only worth anything while it stays true.
    """

    def test_accepted_is_only_ever_a_list_of_repo_paths(self):
        for entry in _policy()["propose"]:
            for path in entry.get("accepted") or ():
                assert not path.startswith("/") and ".." not in path, f"{path} is not a repo-relative path"

    def test_every_accepted_path_still_exists(self):
        """A renamed or deleted file leaves an accept pointing at nothing.

        That is worse than no accept: the rule keeps firing at the new path and
        the stale entry makes the policy read as though somebody looked.
        """
        for entry in _policy()["propose"]:
            for path in entry.get("accepted") or ():
                assert (REPO_ROOT / path).exists(), (
                    f"{entry['id']} accepts {path}, which no longer exists — re-point the entry "
                    "at the file that replaced it, or drop it so the rule proposes again"
                )

    def test_auto_rules_cannot_carry_accepted(self):
        """The two lanes answer different questions. A rule that is auto-fixed is
        never accepted at a path; conflating them would silently stop a fix."""
        for entry in _policy()["auto"]:
            assert "accepted" not in entry, f"{entry['id']} is auto-fixed — `accepted:` has no meaning there"


class TestClassifier:
    """`scripts/codeql_triage.py` — the comparisons the prompt must not make."""

    POLICY = {
        "auto": [{"id": "actions/unpinned-tag", "fix": "pin it"}],
        "propose": [
            {"id": "actions/untrusted-checkout/medium", "why": "decided", "accepted": ["a.yml"]},
            {"id": "py/some-rule", "why": "decided nowhere yet"},
        ],
        "max_batch": 2,
    }

    @staticmethod
    def _alert(number: int, rule: str, path: str, severity: str = "medium") -> dict:
        return {
            "number": number,
            "rule": {"id": rule, "security_severity_level": severity},
            "html_url": f"https://example.invalid/{number}",
            "most_recent_instance": {
                "location": {"path": path, "start_line": 1},
                "message": {"text": "msg"},
            },
        }

    def test_auto_accepted_and_propose_are_three_distinct_lanes(self):
        found = codeql_triage.classify(
            [
                self._alert(1, "actions/unpinned-tag", "w.yml"),
                self._alert(2, "actions/untrusted-checkout/medium", "a.yml"),
                self._alert(3, "actions/untrusted-checkout/medium", "b.yml"),
            ],
            self.POLICY,
        )
        assert [a["number"] for a in found.auto] == [1]
        assert [a["number"] for a in found.accepted] == [2]
        assert [a["number"] for a in found.propose] == [3], (
            "b.yml is not on the rule's accepted list, so the decision about a.yml does not cover it"
        )

    def test_a_propose_entry_without_accepted_suppresses_nothing(self):
        """Writing down a reason and accepting a location are separate acts."""
        found = codeql_triage.classify([self._alert(9, "py/some-rule", "x.py")], self.POLICY)
        assert not found.accepted and [a["number"] for a in found.propose] == [9]

    def test_batch_cap_defers_rather_than_drops(self):
        found = codeql_triage.classify(
            [self._alert(n, "actions/unpinned-tag", f"w{n}.yml") for n in range(1, 5)], self.POLICY
        )
        assert len(found.auto) == 2 and len(found.dropped) == 2
        assert any("Deferred to next week" in line for line in codeql_triage.report(found, []))

    def test_worst_severity_survives_the_cap(self):
        found = codeql_triage.classify(
            [
                self._alert(1, "actions/unpinned-tag", "a.yml", "low"),
                self._alert(2, "actions/unpinned-tag", "b.yml", "critical"),
                self._alert(3, "actions/unpinned-tag", "c.yml", "high"),
            ],
            self.POLICY,
        )
        assert [a["number"] for a in found.auto] == [2, 3]


class TestProposalGrouping:
    """A closed issue is a record, not a reason to stay quiet."""

    @staticmethod
    def _slim(number: int, rule: str, path: str) -> dict:
        return {"number": number, "rule": rule, "severity": "medium", "path": path, "line": 1, "message": "", "url": ""}

    def test_a_rule_nobody_has_filed_opens_an_issue(self):
        groups = codeql_triage.group_proposals([self._slim(1, "r/one", "a.yml")], [])
        assert groups[0].action == codeql_triage.ACTION_OPEN and groups[0].existing_issue is None

    def test_an_open_issue_gets_a_comment_not_a_duplicate(self):
        issues = [{"number": 7, "title": "[security][security] codeql: r/one", "state": "OPEN"}]
        groups = codeql_triage.group_proposals([self._slim(1, "r/one", "a.yml")], issues)
        assert groups[0].action == codeql_triage.ACTION_COMMENT and groups[0].existing_issue == 7

    def test_a_closed_issue_no_longer_silences_a_new_location(self):
        """The regression this whole change exists for.

        #248 answered `actions/untrusted-checkout/medium` for two workflows and
        was closed. Under the old rule-scoped `--state all` dedup the same rule
        firing on a third file was treated as a duplicate and never surfaced.
        """
        issues = [{"number": 248, "title": "[security][security] codeql: r/one", "state": "CLOSED"}]
        groups = codeql_triage.group_proposals([self._slim(1, "r/one", "brand-new.yml")], issues)
        assert groups[0].action == codeql_triage.ACTION_NEW_LOCATION
        assert groups[0].existing_issue == 248

    def test_one_group_per_rule_so_a_repeated_rule_cannot_flood_the_queue(self):
        alerts = [self._slim(1, "r/one", "a.yml"), self._slim(2, "r/one", "b.yml"), self._slim(3, "r/two", "c.yml")]
        groups = codeql_triage.group_proposals(alerts, [])
        assert len(groups) == 2
        assert sorted(len(g.alerts) for g in groups) == [1, 2]

    def test_a_title_match_is_required_rather_than_a_body_mention(self):
        """A loose match against prose suppresses a real finding."""
        issues = [{"number": 5, "title": "some sweep find", "state": "CLOSED", "body": "mentions r/one"}]
        groups = codeql_triage.group_proposals([self._slim(1, "r/one", "a.yml")], issues)
        assert groups[0].action == codeql_triage.ACTION_OPEN


class TestSurveyReport:
    """Silence means "nothing found", so every suppression says so out loud."""

    def test_an_accepted_alert_still_open_is_warned_about_every_run(self):
        """The survey only fetches `state=open`, so reaching this lane at all
        means the accept was recorded and the alert never dismissed — the exact
        state four alerts sat in from 2026-08-12 with nothing reporting it."""
        found = codeql_triage.Classification(
            accepted=[{"number": 26, "rule": "r/one", "path": "a.yml", "line": 1, "severity": "medium"}]
        )
        warnings = [line for line in codeql_triage.report(found, []) if line.startswith("::warning::")]
        assert len(warnings) == 1
        assert "#26" in warnings[0] and "Dismiss it in the Security tab" in warnings[0]

    def test_a_new_location_on_a_decided_rule_is_warned_about(self):
        group = codeql_triage.Group(
            rule="r/one",
            action=codeql_triage.ACTION_NEW_LOCATION,
            existing_issue=248,
            alerts=[{"number": 9, "rule": "r/one", "path": "new.yml", "line": 1, "severity": "high"}],
        )
        warnings = [
            line for line in codeql_triage.report(codeql_triage.Classification(), [group]) if "::warning::" in line
        ]
        assert len(warnings) == 1 and "#248" in warnings[0] and "new.yml" in warnings[0]

    def test_nothing_to_do_is_a_notice_not_a_silence(self):
        lines = codeql_triage.report(codeql_triage.Classification(), [])
        assert any(line.startswith("::notice::") for line in lines)

    def test_accepted_alone_does_not_wake_claude(self):
        """A run whose only finding is an undismissed accept has no code to
        write and no issue to file. It reports and stops."""
        found = codeql_triage.classify(
            [
                {
                    "number": 26,
                    "rule": {"id": "actions/untrusted-checkout/medium", "security_severity_level": "medium"},
                    "most_recent_instance": {"location": {"path": "a.yml", "start_line": 1}},
                }
            ],
            TestClassifier.POLICY,
        )
        assert found.accepted and not found.auto
        assert not codeql_triage.group_proposals(found.propose, [])


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
