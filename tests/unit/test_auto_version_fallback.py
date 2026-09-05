"""auto-version.yml lands a bump even when Claude cannot (scripts/changelog_stub.py and the fallback step)."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
import yaml

from tests.unit import test_changelog as changelog_rules

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "auto-version.yml"


@pytest.fixture(scope="module")
def stub():
    spec = importlib.util.spec_from_file_location("changelog_stub", ROOT / "scripts" / "changelog_stub.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def data(tmp_path: Path) -> Path:
    path = tmp_path / "changelog_data.json"
    real = json.loads((ROOT / "src" / "yeaboi" / "changelog_data.json").read_text(encoding="utf-8"))
    path.write_text(json.dumps({"schema_version": real.get("schema_version", 2), "entries": real["entries"][:2]}))
    return path


class TestStub:
    def test_the_placeholder_passes_the_copy_contract(self, stub):
        rules = changelog_rules.TestCopyContract()
        entry = stub.stub_entry("9.9.9", "2026-09-05")
        assert len(entry["headline"]) <= rules.HEADLINE_MAX and not entry["headline"].endswith(".")
        assert len(entry["summary"]) <= rules.SUMMARY_MAX
        assert len(re.split(r"(?<=[.!?])\s+", entry["summary"].strip())) <= rules.SENTENCE_MAX
        for highlight in entry["highlights"]:
            assert len(highlight["text"]) <= rules.HIGHLIGHT_MAX and not highlight["text"].endswith(".")
            assert highlight["areas"] == ["general"]
        for _field, text in rules._strings(entry):
            for label, pattern in rules.BANNED:
                assert re.search(pattern, text) is None, (label, text)

    def test_write_prepends_once_and_is_stub_recognises_it(self, stub, data):
        assert stub.write("9.9.9", date="2026-09-05", path=data)
        assert not stub.write("9.9.9", date="2026-09-05", path=data)  # already there
        entries = json.loads(data.read_text())["entries"]
        assert entries[0]["version"] == "9.9.9" and entries[0]["date"] == "2026-09-05"
        assert stub.is_stub("9.9.9", path=data)
        assert not stub.is_stub(entries[1]["version"], path=data)  # a real entry
        assert not stub.is_stub("0.0.1", path=data)  # no entry at all

    def test_the_command_line(self, stub, data):
        # Always through --data: a test that touched the bundled file would ship a fake release.
        assert stub.main(["--data", str(data), "is-stub", "9.9.9"]) == 1
        assert stub.main(["--data", str(data), "write", "9.9.9", "--pr", "362", "--date", "2026-09-05"]) == 0
        assert stub.main(["--data", str(data), "is-stub", "9.9.9"]) == 0

    def test_writing_only_adds_lines(self, stub, tmp_path):
        import difflib

        real = ROOT / "src" / "yeaboi" / "changelog_data.json"
        copy = tmp_path / "changelog_data.json"
        copy.write_text(real.read_text(encoding="utf-8"), encoding="utf-8")
        assert stub.write("9.9.9", date="2026-09-05", path=copy)
        before, after = real.read_text(encoding="utf-8").splitlines(), copy.read_text(encoding="utf-8").splitlines()
        changes = [line for line in difflib.unified_diff(before, after, lineterm="", n=0) if line[:1] in "+-"]
        removed = [line for line in changes if line.startswith("-") and not line.startswith("---")]
        added = [line for line in changes if line.startswith("+") and not line.startswith("+++")]
        assert removed == [] and 5 <= len(added) <= 12  # the stub entry, and nothing re-flowed
        assert json.loads(copy.read_text())["entries"][0]["version"] == "9.9.9"


@pytest.fixture(scope="module")
def steps() -> dict[str, dict]:
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["bump"]
    return {step["name"]: step for step in job["steps"]}


class TestWorkflow:
    def test_claude_no_longer_fails_the_job(self, steps):
        assert steps["Classify & bump with Claude"].get("continue-on-error") is True

    def test_the_fallback_runs_exactly_when_claude_failed(self, steps):
        fallback = steps["Fall back to a deterministic bump"]
        assert "steps.claude.outcome == 'failure'" in fallback["if"]
        assert "steps.guard.outputs.skip == 'false'" in fallback["if"]
        assert "steps.claude.outcome == 'failure'" in steps["Explain an auth failure"]["if"]
        # continue-on-error makes `conclusion` read success; only `outcome` tells the truth.
        assert "conclusion" not in fallback["if"] and "conclusion" not in steps["Explain an auth failure"]["if"]

    def test_the_fallback_bumps_by_rule_and_writes_the_placeholder(self, steps):
        run = steps["Fall back to a deterministic bump"]["run"]
        assert "scripts/bump_version.py" in run and "scripts/changelog_stub.py write" in run
        for label in ("semver:major", "semver:minor", "semver:patch", "release:skip", "semver:none"):
            assert label in run
        assert "ADDED=$(git diff --name-only --diff-filter=A" in run  # a new module reads as a feature, via a variable
        assert "git push origin" in run and "gh pr comment" in run
        assert 'if [ "$CHANGELOG_ONLY" = "true" ]' in run  # never bumps twice
        assert 'git reset -q --hard "$HEAD_SHA"' in run and '!= "$HEAD_SHA"' in run  # only from the untouched head

    def test_the_guard_heals_the_placeholder_on_the_next_run(self, steps):
        run = steps["Deterministic guards"]["run"]
        assert "changelog_stub.py is-stub" in run
        assert run.index("is-stub") < run.index("changelog entry exists; skipping")
