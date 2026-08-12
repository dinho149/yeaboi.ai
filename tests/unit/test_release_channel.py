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


class TestPublishedPreReleases:
    """The `beta/*` tags — the only evidence a pre-release actually exists.

    Every test here is really the same test: `installable` is a fact and
    `latest_prerelease` is a forecast, and the two diverge in the ordinary case,
    not a rare one. The forecast was what the promotion ask and the daily standup
    were printing as `pip install --pre`.
    """

    def test_only_beta_tags_count_and_they_sort_numerically(self, repo, monkeypatch):
        set_version(monkeypatch, "3.10.0")
        git(repo, "tag", "beta/3.10.0rc9")
        commit(repo, "second")
        git(repo, "tag", "beta/3.10.0rc10")
        git(repo, "tag", "v1.0.0")  # a final, in the other namespace
        git(repo, "tag", "beta/nonsense")
        published = rc.published_prereleases()
        assert [entry["version"] for entry in published] == ["3.10.0rc10", "3.10.0rc9"]
        assert published[0]["sha"] == git(repo, "rev-parse", "HEAD")

    def test_a_beta_tag_does_not_disturb_the_last_final(self, repo, monkeypatch):
        """`last_final_tag` globs `v*`; `beta/` must stay invisible to it."""
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        git(repo, "tag", "beta/1.1.0rc1")
        assert rc.last_final_tag() == ("v1.0.0", (1, 0, 0))
        assert rc.next_prerelease() == "1.1.0rc1"

    def test_installable_is_what_shipped_and_latest_prerelease_is_what_would(self, repo, monkeypatch):
        """The bug this whole namespace exists to fix, in one assertion.

        Two chore commits after the last upload push `next_prerelease` past
        anything on PyPI. Printing it as an install command 404s.
        """
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "the release-worthy one")
        git(repo, "tag", "beta/1.1.0rc1")
        commit(repo, "docs")
        commit(repo, "ci")
        batch = rc.pending()
        assert batch["installable"] == "1.1.0rc1"
        assert batch["latest_prerelease"] == "1.1.0rc3"
        assert batch["installable_tag"] == "beta/1.1.0rc1"

    def test_untested_commits_are_the_ones_no_prerelease_carries(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "the release-worthy one")
        git(repo, "tag", "beta/1.1.0rc1")
        commit(repo, "docs")
        commit(repo, "ci")
        untested = rc.pending()["untested_commits"]
        assert len(untested) == 2
        assert untested[0].endswith("ci")
        assert untested[1].endswith("docs")

    def test_with_nothing_published_the_whole_batch_is_untested(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        batch = rc.pending()
        assert batch["installable"] is None
        assert batch["untested_commits"] == batch["commits"]

    def test_beta_tags_below_the_last_final_are_history(self, repo, monkeypatch):
        """A promoted batch's pre-releases must not resurface in the next one."""
        git(repo, "tag", "beta/1.1.0rc1")
        commit(repo, "second")
        git(repo, "tag", "v1.1.0")
        set_version(monkeypatch, "1.2.0")
        commit(repo, "third")
        assert rc.pending()["installable"] is None

    def test_prerelease_key_orders_rc10_above_rc9(self):
        assert rc.prerelease_key("3.9.0rc10") > rc.prerelease_key("3.9.0rc9")
        assert rc.prerelease_key("beta/3.9.0rc2") == (3, 9, 0, 2)
        with pytest.raises(rc.ReleaseChannelError, match="expected an X.Y.ZrcN"):
            rc.prerelease_key("3.9.0")

    def test_resolve_beta_never_guesses(self, repo, monkeypatch):
        set_version(monkeypatch, "1.1.0")
        git(repo, "tag", "beta/1.1.0rc1")
        assert rc.resolve_beta("beta/1.1.0rc1")["tag"] == "beta/1.1.0rc1"
        assert rc.resolve_beta("1.1.0rc1")["tag"] == "beta/1.1.0rc1"
        assert rc.resolve_beta("beta/9.9.9rc9") is None


class TestDeltaBatch:
    """`--since` — what a skipped week has to ask about, and no more."""

    def _grown_batch(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "week one")
        git(repo, "tag", "beta/1.1.0rc1")
        commit(repo, "week two a")
        commit(repo, "week two b")
        git(repo, "tag", "beta/1.1.0rc3")

    def test_since_narrows_the_commits_to_what_is_new(self, repo, monkeypatch):
        self._grown_batch(repo, monkeypatch)
        full = rc.pending()
        delta = rc.pending(since="beta/1.1.0rc1")
        assert len(full["commits"]) == 3
        assert [line.split(" ", 1)[1] for line in delta["commits"]] == ["week two b", "week two a"]
        assert delta["delta"] is True and delta["since"] == "beta/1.1.0rc1"
        assert full["delta"] is False and full["since"] is None

    def test_since_narrows_the_changelog_entries_too(self, repo, monkeypatch):
        (repo / "changelog_data.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "entries": [
                        {"version": "1.1.0", "summary": "the new one"},
                        {"version": "1.0.5", "summary": "already signed off"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "bump to 1.0.5")
        git(repo, "tag", "beta/1.0.5rc1")
        commit(repo, "bump to 1.1.0")
        assert [e["version"] for e in rc.pending()["entries"]] == ["1.1.0", "1.0.5"]
        assert [e["version"] for e in rc.pending(since="beta/1.0.5rc1")["entries"]] == ["1.1.0"]

    def test_signing_off_on_the_newest_published_rc_leaves_nothing_to_recheck(self, repo, monkeypatch):
        """Distinct from "nothing pending" — the promotion is still worth making.

        Everything after the tested tag is on `main` and in nothing installable,
        so a delta here would render an empty checklist beside an install line
        naming the build they already ran. Saying so is the honest output.
        """
        self._grown_batch(repo, monkeypatch)
        batch = rc.pending(since="beta/1.1.0rc3")
        assert batch["nothing_new"] is True
        assert batch["promotable"] is True
        body = rc.markdown(batch)
        assert "Nothing new has been published" in body
        assert "- [ ] " not in body, "no checklist when there is nothing new to check"
        assert "<!-- promote: 1.1.0 -->" in body, "still promotable, just not re-testable"

    def test_a_delta_with_new_uploads_still_asks_for_the_checks(self, repo, monkeypatch):
        self._grown_batch(repo, monkeypatch)
        batch = rc.pending(since="beta/1.1.0rc1")
        assert batch["nothing_new"] is False
        assert "- [ ] " in rc.markdown(batch)

    def test_the_full_batch_is_never_nothing_new(self, repo, monkeypatch):
        self._grown_batch(repo, monkeypatch)
        assert rc.pending()["nothing_new"] is False

    def test_an_unknown_since_widens_rather_than_narrows(self, repo, monkeypatch):
        """A marker naming a deleted tag must never silently shrink the review."""
        self._grown_batch(repo, monkeypatch)
        assert rc.pending(since="beta/9.9.9rc9")["commits"] == rc.pending()["commits"]
        assert rc.pending(since="beta/9.9.9rc9")["delta"] is False


class TestChangedPaths:
    def test_the_diff_ends_at_the_prerelease_not_at_head(self, repo, monkeypatch):
        """Promotion is pinned, so the checklist must describe the pinned tree.

        A path changed after the last upload is not in the release, and putting it
        on the checklist spends the reviewer's attention on something they cannot
        affect.
        """
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        (repo / "shipped.py").write_text("x\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "in the release")
        git(repo, "tag", "beta/1.1.0rc1")
        (repo / "later.py").write_text("y\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "after the release")
        paths = rc.pending()["changed_paths"]
        assert "shipped.py" in paths
        assert "later.py" not in paths


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


class TestTrackSplit:
    """Which of the two hand-test sessions a batch's work belongs to.

    Paths are the primary signal and the commit subject is corroborating, so a
    missed attribution costs one extra test session and never a missed one. That
    asymmetry is the whole design: the alternative — a commit trailer — is
    unreadable on this repo's squash merges, because git's trailer parser reads
    only the last paragraph and GitHub appends its own block below a separator.
    """

    def test_a_campaign_subject_names_its_provider(self):
        assert rc.providers_from_subjects(["abc1234 integration(gitlab): wire activity into standup"]) == ("gitlab",)

    def test_an_ordinary_subject_names_nothing(self):
        assert rc.providers_from_subjects(["abc1234 fix the thing (#231)", "def5678 integration tests are flaky"]) == ()

    def test_the_same_provider_twice_is_named_once(self):
        assert rc.providers_from_subjects(["a1 integration(gitlab): client", "b2 integration(gitlab): reach"]) == (
            "gitlab",
        )

    def test_an_untrailed_commit_is_maintenance(self):
        """Maintenance is also where an attribution FAILURE lands, which is why the
        split may only ever add a checklist row and never remove one."""
        tracks = rc._tracks(["abc1234 fix a crash (#231)"], ["src/yeaboi/ui/x.py"])
        assert tracks["maintenance"]["commits"] == ["abc1234 fix a crash (#231)"]
        assert tracks["integration"]["commits"] == []
        assert tracks["integration"]["required"] is False

    def test_a_reach_angle_is_found_by_its_subject_alone(self):
        """No provider module in the diff at all — the only signal is the title."""
        tracks = rc._tracks(
            ["abc1234 integration(gitlab): wire into standup"],
            ["src/yeaboi/standup/collector.py"],
        )
        assert tracks["integration"]["required"] is True
        assert tracks["integration"]["providers"] == ["gitlab"]

    def test_a_provider_module_is_found_by_its_path_alone(self):
        """A maintenance fix in `tools/jira.py` still needs somebody to drive Jira."""
        tracks = rc._tracks(["abc1234 fix jira pagination (#231)"], ["src/yeaboi/tools/jira.py"])
        assert tracks["integration"]["required"] is True
        assert tracks["integration"]["providers"] == ["jira"]

    def test_a_zero_work_track_is_never_required(self):
        tracks = rc._tracks([], [])
        assert tracks["maintenance"]["required"] is False
        assert tracks["integration"]["required"] is False


class TestPendingCarriesTracks:
    def test_the_key_is_there_and_names_both(self):
        batch = rc.pending()
        assert set(batch["tracks"]) == set(rc.release_surfaces.TRACKS)

    def test_every_other_key_survived(self):
        """Additive only — a caller that never heard of tracks still works."""
        batch = rc.pending()
        for key in ("target", "commits", "changed_paths", "installable", "untested_commits", "promotable"):
            assert key in batch


class TestMarkdown:
    def test_it_carries_both_markers_publish_reads(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        git(repo, "tag", "beta/1.1.0rc1")
        body = rc.markdown(rc.pending())
        assert "<!-- promote: 1.1.0 -->" in body, "which version was asked about"
        assert "<!-- beta: beta/1.1.0rc1 -->" in body, "which commit to cut it from"
        assert "pip install --pre yeaboi==1.1.0rc1" in body
        assert "✅" in body and "❌" in body

    def test_it_never_hands_out_an_install_command_for_an_unpublished_rc(self, repo, monkeypatch):
        """The install line comes from a tag or it does not appear.

        `next_prerelease` answers "what would the next upload be called", which is
        the right question for the workflow deciding what to upload and the wrong
        one entirely for a human being told to go and try it. With no `beta/*` tag
        there is nothing on PyPI, and saying so is the only honest output.
        """
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        body = rc.markdown(rc.pending())
        assert "pip install" not in body
        assert "Nothing has been published" in body

    def test_it_carries_the_hand_test_checklist_for_what_changed(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        (repo / "frontend").mkdir()
        (repo / "frontend" / "app.tsx").write_text("x\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "front end")
        git(repo, "tag", "beta/1.1.0rc1")
        body = rc.markdown(rc.pending())
        assert "Before you ✅" in body
        assert "**browser**" in body, "frontend/ changed, so the CSP row must fire"
        assert "**install**" in body and "**boot**" in body, "the baseline always fires"

    def test_it_names_what_a_pinned_promotion_leaves_behind(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "in the release")
        git(repo, "tag", "beta/1.1.0rc1")
        commit(repo, "landed after")
        body = rc.markdown(rc.pending())
        assert "in no pre-release" in body
        assert "landed after" in body

    def test_a_delta_ask_says_what_it_is_measured_from(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "week one")
        git(repo, "tag", "beta/1.1.0rc1")
        commit(repo, "week two")
        git(repo, "tag", "beta/1.1.0rc2")
        body = rc.markdown(rc.pending(since="beta/1.1.0rc1"))
        assert "since `beta/1.1.0rc1`, the pre-release you last signed off on" in body
        assert "week one" not in body


class TestReleaseNotes:
    """The published release page is not the promotion ask.

    `publish.yml` uses this as the GitHub Release body, so an ask footer riding
    along would leave every shipped release asking whether to ship it — on the
    most public artefact the channel produces, permanently.
    """

    def _batch(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "second")
        return rc.pending()

    def test_the_ask_carries_the_question_and_the_verbs(self, repo, monkeypatch):
        body = rc.markdown(self._batch(repo, monkeypatch), asking=True)
        assert "Promote `1.1.0`?" in body
        assert "✅" in body and "❌" in body
        assert "<!-- promote: 1.1.0 -->" in body

    def test_the_release_notes_carry_none_of_them(self, repo, monkeypatch):
        body = rc.markdown(self._batch(repo, monkeypatch), asking=False)
        assert "Promote" not in body
        assert "✅" not in body and "❌" not in body
        assert "promote:" not in body, "the marker the promotion path trusts must not be scattered publicly"
        assert "beta:" not in body, "nor the one that decides which commit gets released"
        assert "Before you" not in body, "a checklist of things to test before shipping — it already shipped"
        assert "pip install --pre" not in body, "a released version is not installed with --pre"

    def test_both_describe_the_same_batch(self, repo, monkeypatch):
        """Only the question and the call to action differ; the changelog is one renderer."""
        batch = self._batch(repo, monkeypatch)
        asking = rc.markdown(batch, asking=True)
        notes = rc.markdown(batch, asking=False)
        assert "1 commits since `v1.0.0`" in asking
        assert "1 commits since `v1.0.0`" in notes

    def test_the_cli_renders_each(self, repo, monkeypatch, capsys):
        self._batch(repo, monkeypatch)
        assert rc.main(["--manifest", "--release-notes"]) == 0
        assert "Promote" not in capsys.readouterr().out
        assert rc.main(["--manifest", "--markdown"]) == 0
        assert "Promote" in capsys.readouterr().out


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


class TestBothTrackSignalsMeasureTheSameRelease:
    """Promotion is pinned to a published rc, so both signals must stop there.

    The path signal already does. If the subject signal ran to HEAD instead, a
    campaign PR merged after the newest rc would mark the integration track
    `required` for a provider the pinned promotion does not contain — and
    `beta_signoff.promote` refuses an unsigned required track, so the batch
    becomes unpromotable with no way to sign the track off short of cutting
    another rc for work nobody asked to release.
    """

    def test_a_campaign_merged_after_the_newest_rc_does_not_gate_it(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "fix a crash (#231)")
        git(repo, "tag", "beta/1.1.0rc1")
        commit(repo, "integration(gitlab): client, cassette and credential (#232)")

        batch = rc.pending()
        assert batch["installable"] == "1.1.0rc1"
        assert batch["tracks"]["integration"]["required"] is False, (
            "the gitlab work is not in the tree this promotion would ship"
        )
        assert any("integration(gitlab)" in line for line in batch["untested_commits"]), (
            "and it must still be reported as left behind"
        )

    def test_a_campaign_inside_the_pinned_tree_still_gates_it(self, repo, monkeypatch):
        git(repo, "tag", "v1.0.0")
        set_version(monkeypatch, "1.1.0")
        commit(repo, "integration(gitlab): client, cassette and credential (#232)")
        git(repo, "tag", "beta/1.1.0rc1")

        batch = rc.pending()
        assert batch["tracks"]["integration"]["required"] is True
        assert batch["tracks"]["integration"]["providers"] == ["gitlab"]
