"""Tests for scripts/batch_assemble.py and the batch model's standing guards.

Named `test_release_*.py` so `scripts/test_scope.py`'s ALWAYS glob runs it on
every scoped CI run — these are release-path guards, and a release guard that
only runs when somebody remembers to touch the right file is not one.

Two kinds of test live here. The assembler's mechanics run against a throwaway
git repository, in the style of `test_release_channel.py`. The standing guards
pin the invariants that fail silently: a routine document re-growing
`gh pr merge --auto`, a fleet PR without `semver:none`, and — worst — the batch
PR classifying `fleet`, which turns the human's ship merge into a release of
nothing at all.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("batch_assemble", ROOT / "scripts" / "batch_assemble.py")
assemble_mod = importlib.util.module_from_spec(_spec)
sys.modules["batch_assemble"] = assemble_mod
_spec.loader.exec_module(assemble_mod)

import pr_feedback as prf  # noqa: E402
import release_lane  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False, env={"PATH": "/usr/bin:/bin"}
    )
    assert done.returncode == 0, f"git {' '.join(args)}: {done.stderr}"
    return done


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A working repo with an `origin` remote and a base commit on `main`."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "main")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    (repo / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.0.0"\n', encoding="utf-8")
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "remote", "add", "origin", str(origin))
    _git(repo, "push", "-u", "origin", "main")
    monkeypatch.setattr(assemble_mod, "ROOT", repo)
    return repo


def _branch(repo: Path, name: str, path: str, content: str, message: str) -> str:
    """A one-commit branch off main; returns its head sha."""
    _git(repo, "switch", "-c", name, "main")
    (repo / path).write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "switch", "main")
    return sha


def pr(number: int, sha: str, title: str = "fix a thing", body: str = "") -> dict:
    return {"number": number, "title": title, "body": body, "headRefOid": sha}


class TestAssemble:
    def test_one_squash_commit_per_pr_in_number_order(self, repo):
        sha_b = _branch(repo, "cowork/b", "b.py", "b = 1\n", "add b")
        sha_c = _branch(repo, "cowork/c", "c.py", "c = 1\n", "add c")
        worktree, branch, included, skipped = assemble_mod.assemble(
            [pr(11, sha_b, "add b"), pr(12, sha_c, "add c")], date="2026-08-17"
        )
        assert [p["number"] for p in included] == [11, 12]
        assert skipped == []
        log = _git(worktree, "log", "--oneline", "origin/main..HEAD").stdout.splitlines()
        assert [line.split(" ", 1)[1] for line in log] == ["add c (#12)", "add b (#11)"]
        assert branch == "batch/2026-08-17"

    def test_a_conflicting_pr_is_skipped_and_the_batch_survives(self, repo):
        sha_b = _branch(repo, "cowork/b", "a.py", "a = 2\n", "a to 2")
        sha_c = _branch(repo, "cowork/c", "a.py", "a = 3\n", "a to 3")
        worktree, _, included, skipped = assemble_mod.assemble(
            [pr(11, sha_b, "a to 2"), pr(12, sha_c, "a to 3")], date="2026-08-17"
        )
        assert [p["number"] for p in included] == [11]
        assert [p["number"] for p, _ in skipped] == [12]
        assert "conflicts with #11" in skipped[0][1]
        # the worktree is clean after the skip — the next constituent still lands
        assert _git(worktree, "status", "--porcelain").stdout == ""

    def test_a_version_bumping_pr_is_skipped(self, repo):
        """Fleet PRs carry `semver:none` so no branch bumps; one that slipped
        through would collide with every other constituent, so it is the PR
        that must change, not the batch."""
        sha = _branch(
            repo,
            "cowork/bump",
            "pyproject.toml",
            '[project]\nname = "x"\nversion = "1.1.0"\n',
            "bump",
        )
        _, _, included, skipped = assemble_mod.assemble([pr(11, sha, "bump")], date="2026-08-17")
        assert included == []
        assert "version bump" in skipped[0][1]
        assert "semver:none" in skipped[0][1]

    def test_an_already_contained_pr_is_skipped_not_committed_empty(self, repo):
        """Two PRs carrying the same change: the second squashes to nothing."""
        sha_b = _branch(repo, "cowork/b", "b.py", "b = 1\n", "add b")
        sha_dupe = _branch(repo, "cowork/dupe", "b.py", "b = 1\n", "add b again")
        _, _, included, skipped = assemble_mod.assemble(
            [pr(11, sha_b, "add b"), pr(12, sha_dupe, "add b again")], date="2026-08-17"
        )
        assert [p["number"] for p in included] == [11]
        assert "already contained" in skipped[0][1]


class TestTheBatchBody:
    def test_constituent_lines_round_trip(self):
        body = assemble_mod._body(
            [pr(11, "x", "fix the retro export"), pr(12, "y", "integration(gitlab): wizard step")],
            [],
            ["src/yeaboi/retro/engine.py"],
            "2026-08-17",
        )
        assert assemble_mod.constituents_of(body) == [11, 12]
        assert "--merge" in body and "never squash" in body

    def test_closes_lines_are_carried_verbatim_and_deduped(self):
        prs = [
            pr(11, "x", body="does a thing\n\nCloses #7\nCloses YEA-42\n"),
            pr(12, "y", body="Closes #7\nfixes ABC-9\n"),
        ]
        assert assemble_mod.collect_closes(prs) == ["Closes #7", "Closes YEA-42", "fixes ABC-9"]

    def test_prose_mentioning_closes_is_not_a_closes_line(self):
        prs = [pr(11, "x", body="this closes the gap between A and B\n")]
        assert assemble_mod.collect_closes(prs) == []


class TestGateGreen:
    def test_an_empty_rollup_is_not_green(self):
        """No checks reported ≠ all checks passed — a batch is built only from
        work the gate has finished with."""
        assert not assemble_mod.gate_green({"statusCheckRollup": []})

    def test_missing_pr_feedback_is_not_green(self):
        rollup = [{"name": "Unit tests", "conclusion": "SUCCESS"}]
        assert not assemble_mod.gate_green({"statusCheckRollup": rollup})

    def test_both_check_shapes_are_read(self):
        rollup = [
            {"name": "Unit tests", "conclusion": "SUCCESS"},  # CheckRun shape
            {"context": "pr-feedback", "state": "SUCCESS"},  # commit-status shape
        ]
        assert assemble_mod.gate_green({"statusCheckRollup": rollup})

    def test_one_red_check_sinks_it(self):
        rollup = [
            {"context": "pr-feedback", "state": "SUCCESS"},
            {"name": "Unit tests", "conclusion": "FAILURE"},
        ]
        assert not assemble_mod.gate_green({"statusCheckRollup": rollup})


class TestTheShipIsHumanLane:
    """The whole model rests on the batch merge classifying `human`.

    A `cowork` label on the batch PR, or a batch prefix added to
    `UNATTENDED_BRANCH_PREFIXES`, would gate `publish.yml` off and the signed
    batch would merge and release NOTHING, with one ::notice:: to show for it.
    """

    def test_the_batch_head_classifies_human(self):
        verdict = release_lane.classify(
            {"labels": [assemble_mod.PROMOTION_LABEL], "head": f"{assemble_mod.BATCH_PREFIX}2026-08-17"}
        )
        assert verdict == release_lane.HUMAN

    def test_the_batch_prefix_is_not_an_unattended_prefix(self):
        for prefix in prf.UNATTENDED_BRANCH_PREFIXES:
            assert not f"{assemble_mod.BATCH_PREFIX}2026-08-17".startswith(prefix), prefix
            assert not prefix.startswith("batch"), (
                f"{prefix!r} would swallow the batch namespace — the ship merge would release nothing"
            )

    def test_the_assembler_refuses_a_fleet_shaped_batch(self):
        with pytest.raises(assemble_mod.AssembleError):
            assemble_mod.assert_human_lane("cowork/2026-08-17", [assemble_mod.PROMOTION_LABEL])
        with pytest.raises(assemble_mod.AssembleError):
            assemble_mod.assert_human_lane("batch/2026-08-17", ["cowork"])
        assemble_mod.assert_human_lane("batch/2026-08-17", [assemble_mod.PROMOTION_LABEL])


class TestCloseConstituents:
    def test_it_refuses_an_unmerged_batch(self, monkeypatch, capsys):
        """Closing constituents of a batch that never shipped reads as a pile of
        rejections to the next sweep's dedupe pass."""
        monkeypatch.setattr(assemble_mod, "_json", lambda *a: {"state": "OPEN", "body": "- x (#1)", "url": "u"})
        monkeypatch.setattr(assemble_mod, "_gh", lambda *a: pytest.fail("must not write"))
        assert assemble_mod.close_constituents(301) == 1

    def test_it_closes_each_open_constituent_with_a_pointer(self, monkeypatch, capsys):
        sent = []
        payloads = {
            ("pr", "view", "301"): {"state": "MERGED", "body": "- a (#11)\n- b (#12)\n", "url": "u"},
            ("pr", "view", "11"): {"state": "OPEN"},
            ("pr", "view", "12"): {"state": "CLOSED"},
        }
        monkeypatch.setattr(assemble_mod, "_json", lambda *a: payloads.get(a[:3]))
        monkeypatch.setattr(assemble_mod, "_gh", lambda *argv: sent.append(list(argv)) or "ok")
        assert assemble_mod.close_constituents(301) == 0
        assert ["pr", "close", "11"] in sent
        assert not any(argv[:3] == ["pr", "close", "12"] for argv in sent), "already closed — left alone"
        comment = next(argv for argv in sent if argv[:2] == ["pr", "comment"])
        assert "shipped in batch #301" in comment[-1]


class TestNoRoutineArmsAutoMerge:
    """The inverse of the old convention, so it WILL regress by habit.

    Every fleet document used to instruct `gh pr merge --auto --squash`; under
    the batch model no routine merges and none arms. `dependabot-auto.yml` and
    the human `/ship` command keep theirs — dependency bumps and human branches
    are outside the fleet lane on purpose.
    """

    FLEET_DOCS = [
        ROOT / "cowork" / "sweep-procedure.md",
        ROOT / "cowork" / "house-rules.md",
        ROOT / "cowork" / "release-signoff.md",
        *sorted((ROOT / "cowork" / "routines").rglob("*.md")),
        ROOT / ".claude" / "agents" / "cowork-builder.md",
        ROOT / ".github" / "workflows" / "codeql-triage.yml",
        ROOT / ".github" / "workflows" / "claude.yml",
    ]

    # The historical instruction shape, `gh pr merge [<n>] --auto [--squash]`.
    # Prohibition sentences ("never … including `--auto`") are allowed to name
    # the flag; what must never come back is the runnable spelling.
    ARMING = re.compile(r"gh pr merge[^\n]*--auto")

    def test_no_fleet_document_arms_auto_merge(self):
        offenders: dict[str, list[str]] = {}
        for path in self.FLEET_DOCS:
            for line in path.read_text(encoding="utf-8").splitlines():
                if self.ARMING.search(line) and not re.search(r"\bnever\b|\bnot\b|\bincluding\b", line, re.I):
                    offenders.setdefault(str(path.relative_to(ROOT)), []).append(line.strip())
        assert not offenders, f"fleet documents instructing `gh pr merge --auto`: {offenders}"

    def test_fleet_pr_creation_documents_the_no_bump_label(self):
        """Without `semver:none`, auto-version bumps every fleet branch to the
        same next version and assembly conflicts on the second constituent."""
        for path in (
            ROOT / "cowork" / "sweep-procedure.md",
            ROOT / "cowork" / "routines" / "cron" / "integrations-campaign.md",
            ROOT / "cowork" / "routines" / "cron" / "go-migration-campaign.md",
            ROOT / "cowork" / "routines" / "cron" / "retune.md",
            ROOT / ".github" / "workflows" / "claude.yml",
            ROOT / ".github" / "workflows" / "codeql-triage.yml",
        ):
            assert "semver:none" in path.read_text(encoding="utf-8"), path.name

    def test_the_ship_documents_merge_not_squash(self):
        text = (ROOT / "cowork" / "release-signoff.md").read_text(encoding="utf-8")
        assert "--merge" in text
        assert "never squash" in text
