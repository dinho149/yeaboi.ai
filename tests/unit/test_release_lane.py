"""Tests for `scripts/release_lane.py` — which lane a merge to main came from.

`publish.yml` releases on this answer: `human` cuts the official X.Y.Z straight
to PyPI, `fleet` publishes an rc and waits for a sign-off. PyPI has no delete, so
the interesting cases here are all the ones where the answer must NOT be `human`.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_lane.py"

_spec = importlib.util.spec_from_file_location("release_lane", SCRIPT)
lane = importlib.util.module_from_spec(_spec)
sys.modules["release_lane"] = lane
_spec.loader.exec_module(lane)

import pr_feedback as prf  # noqa: E402 - release_lane puts scripts/ on the path


class TestTheFleetLaneIsRecognised:
    def test_the_cowork_label_is_enough(self):
        assert lane.classify({"labels": [prf.COWORK_LABEL], "head": "anything"}) == lane.FLEET

    @pytest.mark.parametrize("prefix", prf.UNATTENDED_BRANCH_PREFIXES)
    def test_every_unattended_prefix_is_enough(self, prefix):
        """Parametrised over the real tuple, not a copy: a prefix added to
        `pr_feedback.py` is covered here the moment it lands."""
        assert lane.classify({"labels": [], "head": f"{prefix}whatever"}) == lane.FLEET

    def test_a_label_the_run_failed_to_apply_is_caught_by_the_branch(self):
        """`cowork/house-rules.md` requires the label on every routine PR, but a
        run truncated between `git push` and `gh pr create --label` leaves an
        unlabelled machine PR — which must not be trusted more than a labelled
        one. This is why the predicate is an OR."""
        assert lane.classify({"labels": [], "head": "cowork/platform-sweep"}) == lane.FLEET


class TestTheHumanLaneIsRecognised:
    def test_an_ordinary_feature_branch_is_human(self):
        assert lane.classify({"labels": [], "head": "feature/export-visuals"}) == lane.HUMAN

    def test_an_unrelated_label_does_not_make_it_fleet(self):
        assert lane.classify({"labels": ["type:bug", "security"], "head": "fix/thing"}) == lane.HUMAN

    def test_authorship_is_never_consulted(self):
        """`claude[bot]` authors plenty of human-driven PRs from `/ship`; a
        maintainer merging one has signed for it. Only the label and the branch
        decide, so an author field in the payload must change nothing."""
        pr = {"labels": [], "head": "feature/x", "author": "claude[bot]"}
        assert lane.classify(pr) == lane.HUMAN

    def test_feature_issue_is_fleet_but_plain_feature_is_not(self):
        """`feature/issue-123` is the implement job's branch; `feature/issue-tracker`
        is a human's. The prefix carries the hyphen for exactly this reason."""
        assert lane.classify({"labels": [], "head": "feature/issue-123"}) == lane.FLEET
        assert lane.classify({"labels": [], "head": "feature/issues-overview"}) == lane.HUMAN


class TestSeveralPrsForOneCommit:
    """`/commits/{sha}/pulls` can return more than one — an open PR that rebased
    onto main, a cherry-pick — in an order it does not document. Picking `.[0]`
    picks arbitrarily, and one of the two wrong answers publishes to a PyPI that
    has no delete. So: any fleet entry wins.
    """

    def test_one_fleet_among_humans_is_fleet(self):
        prs = [
            {"labels": [], "head": "fix/a"},
            {"labels": ["cowork"], "head": "cowork/b"},
            {"labels": [], "head": "feature/c"},
        ]
        assert lane.classify_all(prs) == lane.FLEET

    def test_the_fleet_entry_last_still_wins(self):
        """Order must not matter — that is the whole point."""
        prs = [{"labels": [], "head": "fix/a"}, {"labels": [], "head": "cowork/z"}]
        assert lane.classify_all(prs) == lane.FLEET
        assert lane.classify_all(list(reversed(prs))) == lane.FLEET

    def test_all_human_is_human(self):
        prs = [{"labels": [], "head": "fix/a"}, {"labels": ["type:bug"], "head": "feature/b"}]
        assert lane.classify_all(prs) == lane.HUMAN

    def test_an_empty_list_is_the_no_pr_case(self):
        assert lane.classify_all([]) == lane.HUMAN

    def test_a_bare_object_is_still_accepted(self):
        assert lane.classify_all({"labels": ["cowork"], "head": "x"}) == lane.FLEET

    def test_junk_entries_do_not_mask_a_fleet_one(self):
        assert lane.classify_all(["nonsense", {"labels": ["cowork"], "head": "x"}]) == lane.FLEET


class TestTheDefaults:
    def test_no_pr_behind_the_commit_is_human(self):
        """The default branch enforces `pull_request`, so this means somebody with
        repo write pushed straight to main — a human act, and the most direct
        sign-off there is."""
        assert lane.classify(None) == lane.HUMAN
        assert lane.classify({}) == lane.HUMAN

    def test_missing_keys_do_not_crash(self):
        assert lane.classify({"labels": None, "head": None}) == lane.HUMAN


class TestTheCommandLine:
    """The workflow pipes `gh api … --jq` output straight in and reads the exit
    code, so the contract is the stdout word plus zero/non-zero."""

    def _run(self, payload: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT)], input=payload, capture_output=True, text=True, cwd=ROOT)

    def test_a_fleet_pr_prints_fleet(self):
        got = self._run(json.dumps({"labels": ["cowork"], "head": "cowork/x"}))
        assert got.returncode == 0
        assert got.stdout.strip() == "fleet"

    def test_a_human_pr_prints_human(self):
        got = self._run(json.dumps({"labels": [], "head": "fix/thing"}))
        assert got.returncode == 0
        assert got.stdout.strip() == "human"

    def test_the_jq_null_of_an_empty_array_is_human(self):
        """publish.yml's filter says `if length == 0 then null` explicitly, so a
        commit with no PR behind it arrives here as the literal `null` rather
        than as a failed lookup — a bare `.[0] | …` exits 5 on an empty array."""
        assert self._run("null\n").stdout.strip() == "human"

    def test_unparseable_input_exits_non_zero_and_prints_no_verdict(self):
        """The caller treats non-zero as unclassified and stays on the rc channel.
        Printing `human` here would publish on a garbled payload."""
        got = self._run("<html>rate limited</html>")
        assert got.returncode != 0
        assert "human" not in got.stdout

    def test_it_imports_nothing_outside_the_standard_library(self):
        """`publish.yml` calls this with the runner's bare `python3`, before any
        dependency install.

        Running it under `sys.executable` proves nothing — that is the project
        venv, where everything is importable. So walk the transitive import graph
        statically instead. A third-party import anywhere in it fails on the
        runner, `release_lane.py` exits non-zero, `lane` reads that as "could not
        classify", and the official channel stops cutting releases with only a
        `::warning::` to show for it.
        """
        seen: set[Path] = set()
        pending = [SCRIPT]
        third_party: list[str] = []
        while pending:
            path = pending.pop()
            if path in seen or not path.exists():
                continue
            seen.add(path)
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    root = name.split(".")[0]
                    if not root:
                        continue
                    sibling = SCRIPT.parent / f"{root}.py"
                    if sibling.exists():
                        pending.append(sibling)  # a scripts/ neighbour — keep walking
                    elif root not in sys.stdlib_module_names:
                        third_party.append(f"{path.name} imports {name}")
        assert not third_party, f"reachable from release_lane.py under the runner's bare python3: {third_party}"

    def test_the_walk_reaches_pr_feedback(self):
        """Guards the guard: the scan above is only worth anything if it actually
        follows the `scripts/` hop into the module that owns the predicate."""
        src = SCRIPT.read_text(encoding="utf-8")
        assert "import pr_feedback" in src
        assert (SCRIPT.parent / "pr_feedback.py").exists()
