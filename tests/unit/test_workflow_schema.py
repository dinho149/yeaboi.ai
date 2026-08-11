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

import re
import subprocess
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


class TestInlineSedExpressionsAreValid:
    """`sed` expressions in workflows must be ones `sed` will actually accept.

    `publish-beta.yml` shipped `s/…/\\1/{p;q;}` — a substitution with a block
    glued to it, which sed reads as a substitute *flag* and rejects with
    "unknown option to `s'". actionlint passed it, shellcheck passed it, YAML
    passed it, and the workflow died on its first real run: the merge that was
    supposed to publish the first pre-release published nothing.

    Nothing statically checks a sed program, so this runs each one against empty
    input and asks sed whether it parses. Cheap, and it is the only thing in the
    suite that would have caught it.
    """

    # `sed -nE '<expr>'` / `sed -n -E '<expr>'`, single-quoted, as written in a
    # `run:` block. Double-quoted forms are skipped: they interpolate shell and
    # cannot be validated without evaluating them.
    SED_CALL = re.compile(r"sed\s+(?:-[a-zA-Z]+\s+)*'([^']+)'")

    def test_every_workflow_sed_program_parses(self):
        checked = 0
        for path in WORKFLOWS:
            for expr in self.SED_CALL.findall(path.read_text(encoding="utf-8")):
                if "${{" in expr:  # a GitHub expression, not a sed program
                    continue
                result = subprocess.run(["sed", "-nE", expr], input="", capture_output=True, text=True, check=False)
                assert result.returncode == 0, f"{path.name}: sed rejects {expr!r} — {result.stderr.strip()}"
                checked += 1
        assert checked, "no sed expressions found — this test would pass vacuously"


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
