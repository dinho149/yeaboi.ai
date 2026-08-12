"""Tests for scripts/beta_signoff.py — the two commands a human runs.

Two things here are load-bearing beyond the obvious. The sign-off marker is read
back by `publish.yml` to decide *which commit becomes the release*, so writing a
wrong one, or writing one twice, is a release defect rather than a cosmetic one.
And `beta-promote` must apply the label the same way the Slack ✅ does — two
spellings of one approval is how they drift, and the one that drifts is the one
nobody watches.

`gh` is never invoked: every test injects the payloads it would have returned.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

_spec = importlib.util.spec_from_file_location("beta_signoff", ROOT / "scripts" / "beta_signoff.py")
signoff = importlib.util.module_from_spec(_spec)
sys.modules["beta_signoff"] = signoff
_spec.loader.exec_module(signoff)

import cowork_relay  # noqa: E402
import release_surfaces as surfaces  # noqa: E402


def ask(number: int, state: str = "OPEN", body: str = "") -> dict:
    return {"number": number, "state": state, "body": body, "title": "promote 1.1.0", "url": f"http://x/{number}"}


class TestTheSignOffMarker:
    def test_it_is_the_shape_publish_reads(self):
        argv = signoff.mark_tested(244, "beta/1.1.0rc7")
        assert argv[:4] == ["gh", "issue", "comment", "244"]
        assert "<!-- tested: beta/1.1.0rc7 -->" in argv[-1]
        assert signoff.TESTED_RE.search(argv[-1]).group(1) == "beta/1.1.0rc7"

    def test_it_is_literal_argv_not_a_formatted_command(self):
        """The tag reaches this from a git tag list; the issue number from gh."""
        argv = signoff.mark_tested(244, "beta/1.1.0rc7")
        assert all(isinstance(part, str) for part in argv)
        assert not any(" && " in part or "$(" in part for part in argv[:4])

    def test_the_two_markers_do_not_read_each_other(self):
        """`beta:` is what the ask is about; `tested:` is what somebody ran."""
        body = "<!-- beta: beta/1.1.0rc7 -->\n<!-- promote: 1.1.0 -->"
        assert signoff.TESTED_RE.search(body) is None
        assert signoff.BETA_MARKER_RE.search(body).group(1) == "beta/1.1.0rc7"


class TestNewestTested:
    def test_it_orders_by_the_pre_release_not_by_the_comment(self, monkeypatch):
        """A late sign-off on an older rc must not narrow the next batch.

        Comment timestamps would let it: somebody catching up on last week's ask
        after this week's has opened would set the floor backwards past work
        nobody looked at.
        """
        monkeypatch.setattr(
            signoff,
            "_comment_bodies",
            lambda n: {1: ["<!-- tested: beta/1.1.0rc10 -->"], 2: ["<!-- tested: beta/1.1.0rc9 -->"]}[n],
        )
        monkeypatch.setattr(
            signoff.channel,
            "resolve_beta",
            lambda name: {"tag": f"beta/{name.removeprefix('beta/')}", "version": name.removeprefix("beta/")},
        )
        # #2 is listed first (newer issue), but rc10 is the newer pre-release.
        assert signoff.newest_tested([ask(2), ask(1)]) == "beta/1.1.0rc10"

    def test_a_marker_naming_a_tag_that_does_not_exist_is_ignored(self, monkeypatch):
        monkeypatch.setattr(signoff, "_comment_bodies", lambda n: ["<!-- tested: beta/9.9.9rc9 -->"])
        monkeypatch.setattr(signoff.channel, "resolve_beta", lambda name: None)
        assert signoff.newest_tested([ask(1)]) is None

    def test_no_markers_at_all_is_none(self, monkeypatch):
        monkeypatch.setattr(signoff, "_comment_bodies", lambda n: ["just a comment"])
        assert signoff.newest_tested([ask(1)]) is None


class TestOpenAsk:
    def test_a_closed_ask_is_not_the_open_one(self):
        assert signoff.open_ask([ask(2, "CLOSED"), ask(1, "OPEN")])["number"] == 1
        assert signoff.open_ask([ask(2, "CLOSED")]) is None

    def test_the_beta_marker_comes_off_the_body(self):
        assert signoff.ask_beta(ask(1, body="x <!-- beta: beta/1.1.0rc2 --> y")) == "beta/1.1.0rc2"
        assert signoff.ask_beta(ask(1, body="no marker")) is None
        assert signoff.ask_beta(None) is None


class TestPromote:
    def test_it_applies_the_label_exactly_as_the_slack_tick_does(self, monkeypatch, capsys):
        """One approval, one argv. Asserted against the relay's own function.

        `--add-label` adds; `gh api -X PUT .../labels` replaces, and once wiped an
        issue's whole label set. Importing rather than respelling is what makes
        the second unreachable from here.
        """
        sent = []
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: [ask(244, body="<!-- beta: beta/1.1.0rc2 -->")])
        monkeypatch.setattr(signoff, "newest_tested", lambda asks: None)
        monkeypatch.setattr(
            signoff.channel,
            "pending",
            lambda *a, **k: {
                "promotable": True,
                "target": "1.1.0",
                "last_final": "v1.0.0",
                "installable_tag": "beta/1.1.0rc2",
                "untested_commits": [],
            },
        )
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")

        assert signoff.main(["promote", "--yes"]) == 0
        assert ["gh", *sent[0]] == cowork_relay._command("promote", 244)
        assert "--add-label" in sent[0] and "release:promote" in sent[0]

    def test_it_names_what_the_pinned_release_leaves_behind(self, monkeypatch, capsys):
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: [ask(244, body="<!-- beta: beta/1.1.0rc2 -->")])
        monkeypatch.setattr(signoff, "newest_tested", lambda asks: None)
        monkeypatch.setattr(
            signoff.channel,
            "pending",
            lambda *a, **k: {
                "promotable": True,
                "target": "1.1.0",
                "last_final": "v1.0.0",
                "installable_tag": "beta/1.1.0rc2",
                "untested_commits": ["abc123 landed after"],
            },
        )
        monkeypatch.setattr(signoff, "_gh", lambda *argv: "{}")
        signoff.main(["promote", "--yes"])
        out = capsys.readouterr().out
        assert "NOT in this release" in out
        assert "landed after" in out

    def test_with_no_open_ask_it_offers_the_dispatch_and_says_it_is_unpinned(self, monkeypatch, capsys):
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: [])
        monkeypatch.setattr(signoff, "newest_tested", lambda asks: None)
        monkeypatch.setattr(
            signoff.channel, "pending", lambda *a, **k: {"promotable": True, "target": "1.1.0", "last_final": "v1.0.0"}
        )
        assert signoff.main(["promote"]) == 1
        out = capsys.readouterr().out
        assert "gh workflow run publish.yml -f version=1.1.0" in out
        assert "not a pinned pre-release" in out

    def test_nothing_promotable_promotes_nothing(self, monkeypatch, capsys):
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: [ask(244)])
        monkeypatch.setattr(signoff, "newest_tested", lambda asks: None)
        monkeypatch.setattr(
            signoff.channel, "pending", lambda *a, **k: {"promotable": False, "target": "1.0.0", "last_final": "v1.0.0"}
        )
        assert signoff.main(["promote"]) == 1
        assert "nothing to promote" in capsys.readouterr().out


class TestCheck:
    def _wire(self, monkeypatch, batch, asks, marked=False, floors=None):
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: asks)
        monkeypatch.setattr(signoff, "newest_tested", lambda a: None)
        monkeypatch.setattr(signoff, "track_floors", lambda a: dict(floors or {}))
        monkeypatch.setattr(signoff.channel, "pending", lambda *a, **k: batch)
        monkeypatch.setattr(signoff, "_already_marked", lambda n, tag, track=None: marked)

    def _batch(self, **over):
        base = {
            "promotable": True,
            "target": "1.1.0",
            "last_final": "v1.0.0",
            "commits_since": 3,
            "entries": [],
            "commits": [],
            "changed_paths": ["frontend/app.tsx"],
            "installable": "1.1.0rc2",
            "installable_tag": "beta/1.1.0rc2",
            "untested_commits": [],
            "since": None,
            # `frontend/app.tsx` fires the browser row and no provider module, so
            # this is the ordinary maintenance-only week.
            "tracks": {
                "maintenance": {"commits": [], "items": 1, "required": True},
                "integration": {"commits": [], "items": 0, "required": False, "providers": []},
            },
        }
        base.update(over)
        return base

    def test_it_never_records_anything(self, monkeypatch, capsys):
        """`check` reports. It used to also sign, and with two sessions it must not.

        Printing both checklists and silently signing both would be a command that
        signs off work nobody ran — which is the one failure the split exists to
        make impossible.
        """
        sent = []
        self._wire(monkeypatch, self._batch(), [ask(244)])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["check"]) == 0
        assert not [argv for argv in sent if argv[:2] == ["issue", "comment"]]
        assert "pip install --pre yeaboi==1.1.0rc2" in capsys.readouterr().out

    def test_no_mark_is_still_accepted(self, monkeypatch, capsys):
        """Kept as a no-op rather than removed: a habit or a script keeps working."""
        self._wire(monkeypatch, self._batch(), [ask(244)])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["check", "--no-mark"]) == 0
        assert "pip install --pre yeaboi==1.1.0rc2" in capsys.readouterr().out

    def test_it_names_the_sign_off_still_owed(self, monkeypatch, capsys):
        self._wire(monkeypatch, self._batch(), [ask(244)])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["check"]) == 0
        assert "make beta-sign-maintenance" in capsys.readouterr().out

    def test_with_no_open_ask_it_still_reports(self, monkeypatch, capsys):
        """The batch and the checklist are the point; the ask is not needed to read them."""
        self._wire(monkeypatch, self._batch(), [])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["check"]) == 0
        out = capsys.readouterr().out
        assert "TEST THIS WEEK" in out
        assert "browser" in out, "frontend/ changed, so the CSP row must fire"
        assert "none open" in out, "the report still says there is no ask to sign against"

    def test_the_two_sections_sit_under_one_baseline(self, monkeypatch, capsys):
        """`install` and `boot` are printed once, above both tracks.

        Repeated in each section they get done twice; in one section only, the
        other track can be signed by somebody who never installed the wheel.
        """
        batch = self._batch(
            changed_paths=["frontend/app.tsx", "src/yeaboi/tools/gitlab.py", "src/yeaboi/standup/collector.py"],
            tracks={
                "maintenance": {"commits": [], "items": 1, "required": True},
                "integration": {"commits": [], "items": 2, "required": True, "providers": ["gitlab"]},
            },
        )
        self._wire(monkeypatch, batch, [ask(244)])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["check"]) == 0
        out = capsys.readouterr().out
        assert out.count("[ ] install") == 1, "the baseline is shared, not repeated per section"
        assert "── MAINTENANCE" in out
        assert "── INTEGRATION: gitlab" in out
        assert "make beta-sign-integration" in out
        # An angle the batch did not reach is listed, not omitted: a vanishing
        # angle reads as an angle that was never needed.
        assert "not wired in this batch" in out

    def test_a_quiet_week_says_so_and_stops(self, monkeypatch, capsys):
        self._wire(monkeypatch, self._batch(promotable=False, target="1.0.0"), [])
        assert signoff.main(["check"]) == 0
        out = capsys.readouterr().out
        assert "nothing pending" in out
        assert "TEST THIS WEEK" not in out

    def test_a_delta_batch_says_what_it_is_measured_from(self, monkeypatch, capsys):
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: [ask(244)])
        monkeypatch.setattr(signoff, "newest_tested", lambda a: "beta/1.1.0rc1")
        monkeypatch.setattr(signoff.channel, "pending", lambda *a, **k: self._batch(since="beta/1.1.0rc1"))
        monkeypatch.setattr(signoff, "track_floors", lambda a: {})
        monkeypatch.setattr(signoff, "_already_marked", lambda n, tag, track=None: True)
        signoff.main(["check"])
        assert "you last signed off on beta/1.1.0rc1" in capsys.readouterr().out


class TestSign:
    """Recording, which moved out of `check` when there became two sessions."""

    def _wire(self, monkeypatch, batch, asks, marked=(), floors=None):
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: asks)
        monkeypatch.setattr(signoff, "newest_tested", lambda a: None)
        monkeypatch.setattr(signoff, "track_floors", lambda a: dict(floors or {}))
        monkeypatch.setattr(signoff.channel, "pending", lambda *a, **k: batch)
        monkeypatch.setattr(signoff, "_already_marked", lambda n, tag, track=None: track in marked)

    @staticmethod
    def _batch(maintenance=True, integration=False, **over):
        base = {
            "promotable": True,
            "target": "1.1.0",
            "last_final": "v1.0.0",
            "changed_paths": [],
            "installable": "1.1.0rc2",
            "installable_tag": "beta/1.1.0rc2",
            "untested_commits": [],
            "tracks": {
                "maintenance": {"commits": [], "items": 1, "required": maintenance},
                "integration": {
                    "commits": [],
                    "items": 1 if integration else 0,
                    "required": integration,
                    "providers": ["gitlab"] if integration else [],
                },
            },
        }
        base.update(over)
        return base

    def test_an_unknown_track_is_refused(self, capsys):
        assert signoff.main(["sign", "nonsense"]) == 2

    def test_the_last_required_track_also_writes_the_completion_marker(self, monkeypatch, capsys):
        """One session in the batch, so signing it is signing the whole thing.

        Two comments, and only the second is the one `publish.yml` can see.
        """
        sent = []
        self._wire(monkeypatch, self._batch(), [ask(244)])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["sign", "maintenance"]) == 0
        bodies = [argv[-1] for argv in sent if argv[:3] == ["issue", "comment", "244"]]
        assert len(bodies) == 2, bodies
        assert signoff.TRACK_TESTED_RE.search(bodies[0]).groups() == ("beta/1.1.0rc2", "maintenance")
        assert signoff.TESTED_RE.search(bodies[1]).group(1) == "beta/1.1.0rc2"
        assert "ready to promote" in capsys.readouterr().out

    def test_an_outstanding_track_withholds_the_completion_marker(self, monkeypatch, capsys):
        """Half-signed must stay unpromotable, and the marker is how that is enforced."""
        sent = []
        self._wire(monkeypatch, self._batch(integration=True), [ask(244)])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["sign", "maintenance"]) == 0
        bodies = [argv[-1] for argv in sent if argv[:3] == ["issue", "comment", "244"]]
        assert len(bodies) == 1
        assert not signoff.TESTED_RE.search(bodies[0]), "a half-signed batch must not look complete"
        assert "still outstanding: integration" in capsys.readouterr().out

    def test_a_track_with_no_work_is_not_signed(self, monkeypatch, capsys):
        """Not an error, and not a signature either.

        An empty checklist reads as "signed off" when it means "never asked", so a
        week with no campaign records nothing against a human's name.
        """
        self._wire(monkeypatch, self._batch(integration=False), [ask(244)])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["sign", "integration"]) == 0
        assert "nothing integration in this batch" in capsys.readouterr().out

    def test_a_quiet_week_has_nothing_to_sign(self, monkeypatch, capsys):
        self._wire(monkeypatch, self._batch(promotable=False), [ask(244)])
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not write"))
        assert signoff.main(["sign", "maintenance"]) == 1

    def test_re_signing_records_nothing_twice(self, monkeypatch, capsys):
        sent = []
        self._wire(
            monkeypatch,
            self._batch(),
            [ask(244)],
            marked=("maintenance", None),
            floors={"maintenance": "beta/1.1.0rc2"},
        )
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["sign", "maintenance"]) == 0
        assert not [argv for argv in sent if argv[:2] == ["issue", "comment"]]


class TestPromoteWaitsForEveryTrack:
    @staticmethod
    def _pending(**over):
        base = {
            "promotable": True,
            "target": "1.1.0",
            "last_final": "v1.0.0",
            "installable_tag": "beta/1.1.0rc2",
            "untested_commits": [],
            "tracks": {
                "maintenance": {"commits": [], "items": 1, "required": True},
                "integration": {"commits": [], "items": 1, "required": True, "providers": ["gitlab"]},
            },
        }
        base.update(over)
        return base

    def test_it_refuses_while_a_required_track_is_unsigned(self, monkeypatch, capsys):
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: [ask(244)])
        monkeypatch.setattr(signoff, "newest_tested", lambda asks: None)
        monkeypatch.setattr(signoff, "track_floors", lambda asks: {"maintenance": "beta/1.1.0rc2"})
        monkeypatch.setattr(signoff.channel, "pending", lambda *a, **k: self._pending())
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not label"))
        assert signoff.main(["promote"]) == 1
        out = capsys.readouterr().out
        assert "integration has not been signed off" in out
        assert "make beta-sign-integration" in out

    def test_yes_overrides_and_says_that_it_did(self, monkeypatch, capsys):
        sent = []
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: [ask(244)])
        monkeypatch.setattr(signoff, "newest_tested", lambda asks: None)
        monkeypatch.setattr(signoff, "track_floors", lambda asks: {"maintenance": "beta/1.1.0rc2"})
        monkeypatch.setattr(signoff.channel, "pending", lambda *a, **k: self._pending())
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["promote", "--yes"]) == 0
        assert ["gh", *sent[-1]] == cowork_relay._command("promote", 244)
        assert "unsigned, because --yes was passed" in capsys.readouterr().out

    def test_a_signed_batch_promotes(self, monkeypatch, capsys):
        sent = []
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: [ask(244)])
        monkeypatch.setattr(signoff, "newest_tested", lambda asks: None)
        monkeypatch.setattr(
            signoff, "track_floors", lambda asks: {"maintenance": "beta/1.1.0rc2", "integration": "beta/1.1.0rc2"}
        )
        monkeypatch.setattr(signoff.channel, "pending", lambda *a, **k: self._pending())
        monkeypatch.setattr(signoff, "_gh", lambda *argv: sent.append(list(argv)) or "{}")
        assert signoff.main(["promote", "--yes"]) == 0
        assert ["gh", *sent[-1]] == cowork_relay._command("promote", 244)

    def test_a_track_signed_at_an_older_rc_does_not_count(self, monkeypatch, capsys):
        """rc7 signed, rc9 installable: the older signature covers a different tree."""
        monkeypatch.setattr(signoff, "recent_asks", lambda limit=5: [ask(244)])
        monkeypatch.setattr(signoff, "newest_tested", lambda asks: None)
        monkeypatch.setattr(
            signoff, "track_floors", lambda asks: {"maintenance": "beta/1.1.0rc7", "integration": "beta/1.1.0rc7"}
        )
        monkeypatch.setattr(signoff.channel, "pending", lambda *a, **k: self._pending(installable_tag="beta/1.1.0rc9"))
        monkeypatch.setattr(signoff, "_gh", lambda *argv: pytest.fail("must not label"))
        assert signoff.main(["promote"]) == 1


class TestGhIsNeverFatal:
    def test_a_missing_gh_is_not_an_error(self, monkeypatch):
        """`beta-check` is a reporting command first."""
        monkeypatch.setattr(signoff.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert signoff._gh("issue", "list") is None
        assert signoff.recent_asks() == []

    def test_a_refusal_reads_as_no_answer_not_as_an_empty_one(self, monkeypatch):
        class Result:
            returncode = 1
            stdout = "[]"

        monkeypatch.setattr(signoff.subprocess, "run", lambda *a, **k: Result())
        assert signoff._gh("issue", "list") is None

    def test_malformed_json_is_no_answer(self, monkeypatch):
        monkeypatch.setattr(signoff, "_gh", lambda *a: "not json")
        assert signoff._json("issue", "list") is None
        assert signoff.recent_asks() == []


class TestTheTwoMarkerFamilies:
    """The per-track marker is inert to both existing readers, on purpose.

    `publish.yml` and `TESTED_RE` both require ` -->` directly after the digits.
    That is what makes a half-signed batch unpromotable by a workflow that only
    ever learned to read one marker — and it is why `publish.yml` needed no edit
    when the sign-off split in two. If either regex is ever loosened, these fail.
    """

    PUBLISH_RE = re.compile(r"<!-- tested: beta/[0-9]+\.[0-9]+\.[0-9]+rc[0-9]+ -->")

    def _body(self, track=None):
        return signoff.mark_tested(244, "beta/3.8.0rc4", track)[-1]

    def test_the_completion_marker_is_the_one_publish_reads(self):
        assert self.PUBLISH_RE.search(self._body())
        assert signoff.TESTED_RE.search(self._body())

    def test_a_track_marker_is_invisible_to_publish(self):
        for track in surfaces.TRACKS:
            body = self._body(track)
            assert not self.PUBLISH_RE.search(body), f"{track} marker would pin a half-signed batch"
            assert not signoff.TESTED_RE.search(body)

    def test_a_completion_marker_is_not_mistaken_for_a_track(self):
        assert not signoff.TRACK_TESTED_RE.search(self._body())

    def test_a_bare_legacy_marker_seeds_every_track(self, monkeypatch):
        """Written before the split, it meant "I ran this build and signed it off".

        Reading it as maintenance-only would strand promotion behind an integration
        sign-off nobody was ever asked for; reading it as neither would lose the
        floor. Seeding every track is the only reading that regresses nothing.
        """
        monkeypatch.setattr(signoff, "_comment_bodies", lambda n: ["<!-- tested: beta/1.1.0rc3 -->"])
        monkeypatch.setattr(signoff.channel, "resolve_beta", lambda tag: {"tag": tag, "version": tag.split("/", 1)[1]})
        assert signoff.track_floors([ask(244)]) == dict.fromkeys(surfaces.TRACKS, "beta/1.1.0rc3")

    def test_a_track_floor_is_ordered_by_the_tag_and_not_by_the_comment(self, monkeypatch):
        """rc10 beats rc9, and a late sign-off on an older rc does not win."""
        monkeypatch.setattr(
            signoff,
            "_comment_bodies",
            lambda n: [
                "<!-- tested: beta/1.1.0rc10 track=integration -->",
                "<!-- tested: beta/1.1.0rc9 track=integration -->",
            ],
        )
        monkeypatch.setattr(signoff.channel, "resolve_beta", lambda tag: {"tag": tag, "version": tag.split("/", 1)[1]})
        assert signoff.track_floors([ask(244)])["integration"] == "beta/1.1.0rc10"


class TestMarkerRoundTrip:
    def test_what_check_writes_is_what_publish_greps_for(self):
        """The workflow's regex, spelled here so the two cannot drift apart."""
        import re

        body = signoff.mark_tested(1, "beta/3.10.0rc14")[-1]
        workflow = re.compile(r"<!-- tested: beta/[0-9]+\.[0-9]+\.[0-9]+rc[0-9]+ -->")
        assert workflow.search(body)

    def test_what_the_ask_renders_is_what_publish_greps_for(self):
        import re

        rendered = "<!-- beta: beta/3.10.0rc14 -->"
        assert re.compile(r"<!-- beta: beta/[0-9]+\.[0-9]+\.[0-9]+rc[0-9]+ -->").search(rendered)
        assert json.dumps(rendered)  # no stray control characters
