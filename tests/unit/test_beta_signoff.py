"""Tests for scripts/beta_signoff.py — the batch sign-off commands.

Two things here are load-bearing beyond the obvious. The sign-off markers on
the batch PR are what `beta-promote` counts before it lets the human merge, so
writing a wrong one, or reading a stranger's, is a release defect rather than a
cosmetic one. And nothing in this module may ever merge: the whole model rests
on the merge being a human's, because `publish.yml` releases on a human-lane
push and on nothing else.

`gh` is never invoked: every test injects the payloads it would have returned.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("beta_signoff", ROOT / "scripts" / "beta_signoff.py")
signoff = importlib.util.module_from_spec(_spec)
sys.modules["beta_signoff"] = signoff
_spec.loader.exec_module(signoff)

import release_surfaces as surfaces  # noqa: E402

HEAD = "a" * 40
OLD = "b" * 40


def batch(
    number: int = 301,
    state: str = "OPEN",
    head: str = HEAD,
    body: str = "- fix the retro export (#288)\n- integration(gitlab): wizard step (#290)\n",
    labels: tuple[str, ...] = ("release:promotion",),
    draft: bool = True,
) -> dict:
    return {
        "number": number,
        "state": state,
        "body": body,
        "title": "release batch 2026-08-17 — 2 changes",
        "url": f"http://x/{number}",
        "headRefName": "batch/2026-08-17",
        "headRefOid": head,
        "isDraft": draft,
        "labels": [{"name": name} for name in labels],
    }


def wire(monkeypatch, *, batches=None, paths=("frontend/app.tsx",), comments=()):
    monkeypatch.setattr(signoff, "recent_batches", lambda limit=5: list(batches or []))
    monkeypatch.setattr(signoff, "changed_paths", lambda b: list(paths))
    monkeypatch.setattr(signoff, "_comment_bodies", lambda n: list(comments))


class TestTheSignOffMarker:
    def test_it_is_a_pr_comment_pinned_to_the_head_sha(self):
        argv = signoff.mark_tested(301, HEAD)
        assert argv[:4] == ["gh", "pr", "comment", "301"]
        assert f"<!-- tested: {HEAD} -->" in argv[-1]
        assert signoff.TESTED_RE.search(argv[-1]).group(1) == HEAD

    def test_it_is_literal_argv_not_a_formatted_command(self):
        argv = signoff.mark_tested(301, HEAD)
        assert all(isinstance(part, str) for part in argv)
        assert not any(" && " in part or "$(" in part for part in argv[:4])

    def test_a_short_sha_is_not_a_marker(self):
        """A prefix that stops resolving uniquely is a signature that stops
        meaning anything — only the full 40-hex form counts."""
        assert signoff.TESTED_RE.search(f"<!-- tested: {HEAD[:12]} -->") is None
        assert signoff.TRACK_TESTED_RE.search(f"<!-- tested: {HEAD[:12]} track=maintenance -->") is None


class TestTheTwoMarkerFamilies:
    """The per-track marker is inert to the bare regex, on purpose.

    `TESTED_RE` requires ` -->` directly after the sha, so a `track=` marker
    cannot satisfy it — which is what makes a half-signed batch unable to look
    complete to any reader that only ever learned the bare marker.
    """

    def _body(self, track=None):
        return signoff.mark_tested(301, HEAD, track)[-1]

    def test_the_completion_marker_is_the_bare_one(self):
        assert signoff.TESTED_RE.search(self._body())
        assert not signoff.TRACK_TESTED_RE.search(self._body())

    def test_a_track_marker_is_invisible_to_the_bare_regex(self):
        for track in surfaces.TRACKS:
            body = self._body(track)
            assert not signoff.TESTED_RE.search(body), f"{track} marker would complete a half-signed batch"
            assert signoff.TRACK_TESTED_RE.search(body).groups() == (HEAD, track)

    def test_a_bare_marker_seeds_every_track(self, monkeypatch):
        """ "I tested this build" is a statement about the whole build."""
        monkeypatch.setattr(signoff, "_comment_bodies", lambda n: [f"<!-- tested: {HEAD} -->"])
        floors = signoff.track_floors(301)
        assert floors == {track: {HEAD} for track in surfaces.TRACKS}

    def test_track_markers_accumulate_as_sets(self, monkeypatch):
        monkeypatch.setattr(
            signoff,
            "_comment_bodies",
            lambda n: [
                f"<!-- tested: {OLD} track=integration -->",
                f"<!-- tested: {HEAD} track=integration -->",
            ],
        )
        assert signoff.track_floors(301)["integration"] == {OLD, HEAD}


class TestOnlyAMaintainerCanSignABatchOff:
    """The batch is an open PR on a public repo, so anybody can comment on it.

    A `<!-- tested: … -->` marker is what lets `beta-promote` tell the human the
    batch is ready to merge and release. The regex validates its shape; it never
    asks who wrote it. The author filter is the only authorization there is.
    """

    def _comments(self, monkeypatch, comments: list[dict]) -> None:
        monkeypatch.setattr(signoff, "_json", lambda *a: {"comments": comments})

    def test_an_outsiders_marker_is_not_read(self, monkeypatch):
        self._comments(
            monkeypatch,
            [
                {"body": f"<!-- tested: {HEAD} -->", "authorAssociation": "OWNER"},
                {"body": f"<!-- tested: {OLD} -->", "authorAssociation": "NONE"},
            ],
        )
        assert signoff._comment_bodies(301) == [f"<!-- tested: {HEAD} -->"]

    def test_every_association_that_means_write_access_is_read(self, monkeypatch):
        for association in ("OWNER", "MEMBER", "COLLABORATOR"):
            self._comments(monkeypatch, [{"body": "signed", "authorAssociation": association}])
            assert signoff._comment_bodies(301) == ["signed"], association

    def test_an_unrecognised_association_reads_as_an_outsider(self, monkeypatch):
        """The safe direction: a sign-off repeated, rather than a release nobody chose."""
        for association in ("CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "", "MANNEQUIN"):
            self._comments(monkeypatch, [{"body": "signed", "authorAssociation": association}])
            assert signoff._comment_bodies(301) == [], association


class TestOpenBatch:
    def test_a_closed_batch_is_not_the_open_one(self):
        assert signoff.open_batch([batch(2, "MERGED"), batch(1, "OPEN")])["number"] == 1
        assert signoff.open_batch([batch(2, "CLOSED")]) is None


class TestCheck:
    def test_no_batch_open_points_at_the_assembler(self, monkeypatch, capsys):
        wire(monkeypatch, batches=[])
        assert signoff.main(["check"]) == 1
        assert "make batch-assemble" in capsys.readouterr().out

    def test_it_never_records_anything(self, monkeypatch, capsys):
        """`check` reports. A command that printed the checklist and silently
        signed it would sign off work nobody ran."""
        sent = []
        wire(monkeypatch, batches=[batch()])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["check"]) == 0
        assert not [argv for argv in sent if "comment" in argv]

    def test_it_prints_the_checklist_and_the_sign_off_owed(self, monkeypatch, capsys):
        wire(monkeypatch, batches=[batch()])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["check"]) == 0
        out = capsys.readouterr().out
        assert "TEST THIS BATCH" in out
        assert "browser" in out, "frontend/ changed, so the CSP row must fire"
        assert "make beta-sign-maintenance" in out

    def test_the_two_sections_sit_under_one_baseline(self, monkeypatch, capsys):
        """`install` and `boot` are printed once, above both tracks."""
        wire(
            monkeypatch,
            batches=[batch()],
            paths=["frontend/app.tsx", "src/yeaboi/tools/gitlab.py"],
        )
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["check"]) == 0
        out = capsys.readouterr().out
        assert out.count("[ ] install") == 1, "the baseline is shared, not repeated per section"
        assert "── MAINTENANCE" in out
        assert "── INTEGRATION: gitlab" in out
        assert "make beta-sign-integration" in out
        assert "not wired in this batch" in out

    def test_no_mark_is_still_accepted(self, monkeypatch, capsys):
        """Kept as a no-op rather than removed: a habit or a script keeps working."""
        wire(monkeypatch, batches=[batch()])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["check", "--no-mark"]) == 0

    def test_json_reports_the_batch(self, monkeypatch, capsys):
        wire(monkeypatch, batches=[batch()], comments=[f"<!-- tested: {HEAD} track=maintenance -->"])
        payload = None
        assert signoff.main(["check", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["number"] == 301
        assert payload["covered"] == ["maintenance"]
        assert payload["constituents"] == [288, 290]

    def test_a_stale_signature_is_named(self, monkeypatch, capsys):
        """Signed at a head the batch has since moved past → say so, not '✓'."""
        wire(monkeypatch, batches=[batch()], comments=[f"<!-- tested: {OLD} track=maintenance -->"])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["check"]) == 0
        out = capsys.readouterr().out
        assert "signed at an older head" in out
        assert "✓ signed off" not in out


class TestSign:
    def test_an_unknown_track_is_refused(self, capsys):
        assert signoff.main(["sign", "nonsense"]) == 2

    def test_the_last_required_track_also_writes_the_completion_marker(self, monkeypatch, capsys):
        sent = []
        wire(monkeypatch, batches=[batch(body="- fix the retro export (#288)\n")])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["sign", "maintenance"]) == 0
        bodies = [argv[-1] for argv in sent if argv[:3] == ["pr", "comment", "301"]]
        assert len(bodies) == 2, bodies
        assert signoff.TRACK_TESTED_RE.search(bodies[0]).groups() == (HEAD, "maintenance")
        assert signoff.TESTED_RE.search(bodies[1]).group(1) == HEAD
        assert "make beta-promote" in capsys.readouterr().out

    def test_an_outstanding_track_withholds_the_completion_marker(self, monkeypatch, capsys):
        """Half-signed must stay unpromotable, and the marker is how that is enforced."""
        sent = []
        wire(monkeypatch, batches=[batch()], paths=["frontend/app.tsx", "src/yeaboi/tools/gitlab.py"])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["sign", "maintenance"]) == 0
        bodies = [argv[-1] for argv in sent if argv[:3] == ["pr", "comment", "301"]]
        assert len(bodies) == 1
        assert not signoff.TESTED_RE.search(bodies[0]), "a half-signed batch must not look complete"
        assert "still outstanding: integration" in capsys.readouterr().out

    def test_a_track_with_no_work_is_not_signed(self, monkeypatch, capsys):
        """Not an error, and not a signature either: an empty checklist reads as
        "signed off" when it means "never asked"."""
        wire(monkeypatch, batches=[batch(body="- fix the retro export (#288)\n")], paths=["frontend/app.tsx"])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["sign", "integration"]) == 0
        assert "nothing integration in this batch" in capsys.readouterr().out

    def test_re_signing_records_nothing_twice(self, monkeypatch, capsys):
        sent = []
        wire(monkeypatch, batches=[batch()], comments=[f"<!-- tested: {HEAD} track=maintenance -->"])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["sign", "maintenance"]) == 0
        # the completion marker may still be written; the track marker may not repeat
        tracked = [argv for argv in sent if "track=" in argv[-1]]
        assert not tracked

    def test_no_batch_open_points_at_the_assembler(self, monkeypatch, capsys):
        wire(monkeypatch, batches=[])
        assert signoff.main(["sign", "maintenance"]) == 1
        assert "make batch-assemble" in capsys.readouterr().out


class TestPromote:
    """`promote` verifies and hands over. It never merges — nothing here can."""

    def test_it_refuses_while_a_required_track_is_unsigned(self, monkeypatch, capsys):
        wire(monkeypatch, batches=[batch()])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["promote"]) == 1
        out = capsys.readouterr().out
        assert "maintenance" in out
        assert "make beta-sign-maintenance" in out

    def test_a_signature_at_an_old_head_does_not_count(self, monkeypatch, capsys):
        """The tree it names is not the tree that would merge."""
        wire(monkeypatch, batches=[batch()], comments=[f"<!-- tested: {OLD} track=maintenance -->"])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["promote"]) == 1

    def test_a_signed_batch_is_marked_ready_and_the_merge_is_printed_not_run(self, monkeypatch, capsys):
        sent = []
        wire(
            monkeypatch,
            batches=[batch(body="- fix the retro export (#288)\n")],
            comments=[f"<!-- tested: {HEAD} track=maintenance -->"],
        )
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["promote", "--yes"]) == 0
        assert ["pr", "ready", "301"] in sent
        assert not [argv for argv in sent if "merge" in argv], "promote must NEVER merge"
        out = capsys.readouterr().out
        assert "gh pr merge 301 --merge" in out
        assert "--squash" in out, "the never-squash warning is part of the handover"

    def test_yes_overrides_and_says_that_it_did(self, monkeypatch, capsys):
        sent = []
        wire(monkeypatch, batches=[batch()])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["promote", "--yes"]) == 0
        assert "unsigned, because --yes was passed" in capsys.readouterr().out

    def test_a_cowork_labelled_batch_is_refused(self, monkeypatch, capsys):
        """One stray label flips the lane and the merge would release NOTHING."""
        wire(monkeypatch, batches=[batch(labels=("release:promotion", "cowork"))])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["promote", "--yes"]) == 2
        assert "cut NO release" in capsys.readouterr().err

    def test_no_batch_open_points_at_the_assembler(self, monkeypatch, capsys):
        wire(monkeypatch, batches=[])
        assert signoff.main(["promote"]) == 1
        assert "make batch-assemble" in capsys.readouterr().out


class TestProvidersOf:
    def test_the_title_prefix_corroborates_the_paths(self, monkeypatch):
        """`integration(gitlab):` in a constituent line names the provider even
        when the changed paths alone would not."""
        assert signoff.providers_of(batch(), []) == ("gitlab",)

    def test_paths_are_the_primary_signal(self, monkeypatch):
        named = signoff.providers_of(batch(body="- fix a thing (#3)\n"), ["src/yeaboi/tools/jira.py"])
        assert "jira" in named


class TestGhIsNeverFatal:
    def test_a_missing_gh_is_not_an_error(self, monkeypatch):
        monkeypatch.setattr(signoff.transport, "_run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert signoff._gh("pr", "list") is None
        assert signoff.recent_batches() == []

    def test_a_refusal_reads_as_no_answer_not_as_an_empty_one(self, monkeypatch):
        class Result:
            returncode = 1
            stdout = "[]"

        monkeypatch.setattr(signoff.transport, "_run", lambda *a, **k: Result())
        assert signoff._gh("pr", "list") is None

    def test_malformed_json_is_no_answer(self, monkeypatch):
        monkeypatch.setattr(signoff, "_gh", lambda *a: "not json")
        assert signoff._json("pr", "list") is None
        assert signoff.recent_batches() == []
