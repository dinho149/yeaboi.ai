"""Tests for scripts/release_channel.py — the numbering behind the beta channel.

Every function here decides something a publish acts on, so the failure modes are
all silent-and-expensive: a version that goes backwards, a re-run that publishes
the same code twice under two numbers, or an "empty batch" that is really a git
error. Each of those is pinned below against a throwaway repo built in `tmp_path`
— no network, no tags from the real project, and no dependence on where this
checkout happens to be.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_MODULE_PATH = ROOT / "scripts" / "release_channel.py"
_spec = importlib.util.spec_from_file_location("release_channel", _MODULE_PATH)
rc = importlib.util.module_from_spec(_spec)
sys.modules["release_channel"] = rc
_spec.loader.exec_module(rc)


def git(repo: Path, *args: str) -> str:
    """Run git against ``repo`` with the *inherited* git environment stripped.

    `cwd=` is not enough. Git exports ``GIT_DIR``, ``GIT_INDEX_FILE`` and friends
    into every child process, so anything running inside a hook — the pre-commit
    `Unit tests` stage, a `git rebase`, a `git bisect run` — hands this fixture
    the OUTER repository's index and these commits land nowhere near `tmp_path`.
    That failure only appears under the hook: the suite is green standalone and
    the commit that runs it is rejected, which is the worst possible way to find
    out. `GIT_CONFIG_*` go too, so a developer's global config cannot change what
    the fixture measures.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True, env=env)
    return result.stdout.strip()


def commit(repo: Path, message: str) -> None:
    (repo / "log.txt").write_text(f"{message}\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo with one commit, wired in as release_channel's world."""
    work = tmp_path / "repo"
    work.mkdir()
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "test@example.com")
    git(work, "config", "user.name", "Test")
    commit(work, "first")

    changelog = work / "changelog_data.json"
    changelog.write_text(json.dumps({"schema_version": 1, "entries": []}), encoding="utf-8")

    monkeypatch.setattr(rc, "ROOT", work)
    monkeypatch.setattr(rc, "CHANGELOG", changelog)
    return work


def set_version(monkeypatch, version: str) -> None:
    monkeypatch.setattr(rc, "read_current", lambda *a, **k: version)


class TestPrereleaseNumbering:
    def test_the_number_is_the_commit_count_since_the_last_final(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        commit(repo, "third")
        assert rc.next_prerelease() == "1.1.0rc2"

    def test_a_further_commit_raises_the_number(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        first = rc.next_prerelease()
        commit(repo, "third")
        assert rc.next_prerelease() != first
        assert rc.next_prerelease() == "1.1.0rc2"

    def test_the_same_commit_twice_gives_the_same_number(self, repo, monkeypatch):
        """A workflow re-run must republish the same thing, not a new one."""
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        assert rc.next_prerelease() == rc.next_prerelease()

    def test_an_unbumped_version_has_nothing_to_prerelease(self, repo, monkeypatch):
        """The steady state between a release and the next bump — not an error."""
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.0.0")
        commit(repo, "docs only")
        assert rc.next_prerelease() is None

    def test_a_version_below_the_last_final_is_refused(self, repo, monkeypatch):
        """The dual-PR race landing: an rc here would sort below a published one."""
        git(repo, "tag", "v2.0.0")
        set_version(monkeypatch, "1.9.0")
        commit(repo, "second")
        with pytest.raises(rc.ReleaseChannelError, match="below the last final"):
            rc.next_prerelease()

    def test_before_the_first_release_it_is_rc1(self, repo, monkeypatch):
        set_version(monkeypatch, "0.1.0")
        commit(repo, "second")
        assert rc.next_prerelease() == "0.1.0rc1"

    def test_an_rc_string_in_pyproject_is_refused(self, repo, monkeypatch):
        """It can only get there by a bug, and every later bump would then crash."""
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0rc4")
        with pytest.raises(rc.ReleaseChannelError, match="expected an X.Y.Z"):
            rc.next_prerelease()


class TestTagSelection:
    def test_tags_sort_numerically_not_lexically(self, repo, monkeypatch):
        """`v3.9.0` outranks `v3.10.0` as a string, and that would misdate the batch."""
        git(repo, "tag", "v3.9.0")
        commit(repo, "second")
        git(repo, "tag", "v3.10.0")
        assert rc.last_final_tag() == ("v3.10.0", (3, 10, 0))

    def test_prerelease_tags_are_ignored(self, repo, monkeypatch):
        """`v*` is a finals-only namespace; a stray rc tag must not anchor a count."""
        git(repo, "tag", "v1.0.0")
        commit(repo, "second")
        git(repo, "tag", "v1.1.0rc3")
        assert rc.last_final_tag() == ("v1.0.0", (1, 0, 0))

    def test_no_tags_at_all(self, repo):
        assert rc.last_final_tag() is None


class TestPendingBatch:
    def _changelog(self, repo, versions):
        entries = [
            {
                "version": v,
                "date": "2026-08-10",
                "summary": f"summary {v}",
                "highlights": [{"text": f"did {v}", "areas": ["general"]}],
            }
            for v in versions
        ]
        (repo / "changelog_data.json").write_text(
            json.dumps({"schema_version": 1, "entries": entries}), encoding="utf-8"
        )

    def test_only_entries_newer_than_the_last_final_are_in_the_batch(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.2.0")
        commit(repo, "second")
        self._changelog(repo, ["1.2.0", "1.1.0", "1.0.0", "0.9.0"])
        batch = rc.pending()
        assert [entry["version"] for entry in batch["entries"]] == ["1.2.0", "1.1.0"]

    def test_commits_are_counted_from_the_tag(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        commit(repo, "third")
        batch = rc.pending()
        assert batch["commits_since"] == 2
        assert len(batch["commits"]) == 2

    def test_an_unbumped_batch_is_not_promotable(self, repo, monkeypatch):
        """Docs merges move main without moving the version; promoting would re-tag."""
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.0.0")
        commit(repo, "docs only")
        batch = rc.pending()
        assert batch["promotable"] is False
        assert batch["latest_prerelease"] is None

    def test_a_bumped_batch_is_promotable(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        assert rc.pending()["promotable"] is True

    def test_a_git_failure_raises_rather_than_reading_as_empty(self, repo, monkeypatch):
        """ "Nothing to promote" and "git broke" must never look alike."""
        set_version(monkeypatch, "1.1.0")
        with pytest.raises(rc.ReleaseChannelError):
            rc.pending(ref="no-such-ref")


class TestMarkdown:
    def test_it_carries_the_marker_publish_reads(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        body = rc.markdown(rc.pending())
        assert "<!-- promote: 1.1.0 -->" in body
        assert "pip install --pre yeaboi==1.1.0rc1" in body
        assert "✅" in body and "❌" in body


class TestWrite:
    @pytest.mark.parametrize("version", ["3.6.0", "3.6.0rc12"])
    def test_it_stamps_a_valid_version(self, tmp_path, monkeypatch, version):
        seen = {}
        monkeypatch.setattr(rc, "write_version", lambda value: seen.setdefault("value", value))
        assert rc.main(["--write", "--version", version]) == 0
        assert seen["value"] == version

    @pytest.mark.parametrize("version", ["3.6", "v3.6.0", "3.6.0.dev1", "3.6.0-rc1", "; rm -rf /"])
    def test_it_refuses_anything_else(self, monkeypatch, version, capsys):
        monkeypatch.setattr(rc, "write_version", lambda value: pytest.fail(f"wrote {value!r}"))
        assert rc.main(["--write", "--version", version]) == 2
        assert "refusing to stamp" in capsys.readouterr().err


class TestEnvironmentIsolation:
    """The bug that rejected the commit adding this file.

    `_git` hardcodes `cwd=ROOT`, which reads as "this repository" and is not.
    Git exports `GIT_DIR` and `GIT_INDEX_FILE` into every child process, so under
    a pre-commit hook, a rebase, or `git bisect run`, an unsanitised subprocess
    counts commits in whichever repository invoked it. Every number this module
    produces would then describe the wrong tree while looking entirely normal —
    and the whole suite passes standalone, so the only symptom is a commit that
    mysteriously will not go through.
    """

    def test_the_script_ignores_an_inherited_git_dir(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        # Point the inherited environment at a different repository entirely.
        monkeypatch.setenv("GIT_DIR", str(ROOT / ".git"))
        monkeypatch.setenv("GIT_INDEX_FILE", str(ROOT / ".git" / "index"))
        monkeypatch.setenv("GIT_WORK_TREE", str(ROOT))
        assert rc.next_prerelease() == "1.1.0rc1"

    def test_the_fixture_ignores_one_too(self, repo, monkeypatch):
        monkeypatch.setenv("GIT_DIR", str(ROOT / ".git"))
        monkeypatch.setenv("GIT_WORK_TREE", str(ROOT))
        commit(repo, "second")  # would commit into the real repo, or fail outright
        assert git(repo, "rev-list", "--count", "HEAD") == "2"


class TestExitCodes:
    """`publish-beta.yml` branches on these, so they are a contract, not a detail.

    0 publishes an rc, 1 is "the version has not moved — no-op this merge", and 2
    stops the workflow. Confusing 1 with 2 either publishes nothing on a real
    release or reds every quiet merge.
    """

    def test_a_bumped_version_exits_zero_and_prints_the_rc(self, repo, monkeypatch, capsys):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        assert rc.main(["--next-rc"]) == 0
        assert capsys.readouterr().out.strip() == "1.1.0rc1"

    def test_an_unbumped_version_exits_one(self, repo, monkeypatch, capsys):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.0.0")
        assert rc.main(["--next-rc"]) == 1
        assert "nothing to pre-release" in capsys.readouterr().err

    def test_a_backwards_version_exits_two(self, repo, monkeypatch, capsys):
        git(repo, "tag", "v2.0.0")
        set_version(monkeypatch, "1.9.0")
        assert rc.main(["--next-rc"]) == 2
        assert "below the last final" in capsys.readouterr().err

    def test_check_promotable_refuses_a_backwards_version(self, repo, monkeypatch, capsys):
        """The promotion path had a weaker check than the beta path it promotes."""
        git(repo, "tag", "v2.0.0")
        set_version(monkeypatch, "1.9.0")
        assert rc.main(["--check-promotable"]) == 2
        assert "sorts backwards" in capsys.readouterr().err

    def test_check_promotable_refuses_an_already_released_version(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.0.0")
        assert rc.main(["--check-promotable"]) == 2

    def test_check_promotable_accepts_a_real_bump(self, repo, monkeypatch, capsys):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        assert rc.main(["--check-promotable"]) == 0
        assert capsys.readouterr().out.strip() == "1.1.0"


class TestCommittedVersionShape:
    def test_pyproject_holds_a_plain_version(self):
        """An rc string on `main` would crash every later auto-version bump.

        Structurally prevented — the stamp lives in a throwaway checkout in the
        publish job — but asserted here so that if it ever does get committed, the
        red test is on the PR that did it rather than on the third PR afterwards.
        """
        line = next(
            entry
            for entry in (ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
            if entry.startswith("version = ")
        )
        assert rc.SEMVER_RE.match(line.split('"')[1]), f"pyproject.toml holds {line!r} — must be X.Y.Z"
