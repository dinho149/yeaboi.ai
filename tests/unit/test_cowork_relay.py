"""Tests for scripts/cowork_relay.py — the relay's decision, moved into Python.

Every test here is a way the 2026-08-09 run on issue #172 went wrong. It
announced the same approval three times and left a duplicate audit comment,
because "which reactions are still unhandled" was a judgement made at the
``fast`` tier against a fifteen-reply thread that the relay itself was appending
to. Nothing at run time would report that: an extra ack in Slack looks exactly
like a second approval, and GitHub absorbed the repeated writes silently.

``TestRecordedFailure`` runs the real thread. The rest pin the individual rules,
so a future edit that reintroduces one fails here rather than in the channel.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# scripts/ is not a package, so load the module straight from its file path.
_MODULE_PATH = ROOT / "scripts" / "cowork_relay.py"
_spec = importlib.util.spec_from_file_location("cowork_relay", _MODULE_PATH)
relay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(relay)

HUMAN = "U0BLM1QU3JN"
BOT = "U0BLP02UZ7T"
ALLOWLIST = {HUMAN: "onoureldin (onoureldin@gmail.com)"}

FIXTURE = ROOT / "tests" / "fixtures" / "slack_thread_172.json"


def reply(ts: str, text: str, **reactions: list[str]) -> dict:
    """One thread reply; ``reactions`` maps an emoji name to the users on it."""
    return {
        "ts": ts,
        "text": text,
        "reactions": [{"name": name, "count": len(users), "users": users} for name, users in reactions.items()],
    }


@pytest.fixture(autouse=True)
def _not_already_approved(monkeypatch):
    """Default every plan to "this issue is not yet approved".

    `build_plan` asks `is_approved` on the plain-approval path, and that shells
    out to `gh issue view`. Unstubbed, every test in this file that builds a plan
    made a live GitHub read against whatever repo the checkout pointed at — which
    passed quietly until `_no_real_gh_calls` started refusing, and then failed
    only in a *scoped* CI run, because which modules are loaded decides which
    transport objects the guard reached.

    False rather than None is what these tests mean: they assert on the `approve`
    verb, which is the first approval. The handful about re-firing pass
    `approved_check` explicitly, and an explicit argument wins over this.
    """
    monkeypatch.setattr(relay, "is_approved", lambda issue, **kwargs: False)


def item(number: int, ts: str = "1", **reactions: list[str]) -> dict:
    return reply(ts, f"#{number} — [bug][platform] something — https://example.invalid/{number}", **reactions)


def promotion(number: int, version: str = "3.6.1", ts: str = "1", **reactions: list[str]) -> dict:
    """The ask routine's reply, in the exact contract `PROMOTE_RE` parses."""
    return reply(ts, f"#{number} — promote {version} — https://example.invalid/{number}", **reactions)


def candidate(number: int, provider: str = "gitlab", ts: str = "1", **reactions: list[str]) -> dict:
    """The digest's shortlist line, in the exact contract `CANDIDATE_RE` parses."""
    return reply(ts, f"#{number} — integration candidate: {provider} — https://example.invalid/{number}", **reactions)


class TestRecordedFailure:
    """The #172 thread, exactly as Slack holds it."""

    @pytest.fixture
    def thread(self) -> list[dict]:
        return relay.load_replies(FIXTURE.read_text())

    def test_the_thread_that_was_acked_three_times_now_plans_nothing(self, thread):
        result = relay.build_plan(thread, ALLOWLIST)
        assert result["plan"] == [], "the ✅ carries the marker — re-announcing it is the bug this fixes"
        assert result["counts"] == {
            "replies": 15,
            "item_replies": 12,
            "marked": 1,
            "ignored_markers": 0,
            "actionable": 0,
        }

    def test_the_relays_own_acks_are_never_inputs(self, thread):
        """Three replies say "added `claude-implement` to #172". None is an item.

        This is the loop's fuel: the connector posts as the human, so an ack comes
        back on the next read looking like human input. The digest contract puts
        the issue number first; an ack does not, and that is what separates them.
        """
        acks = [r for r in thread if r["text"].startswith("added ")]
        assert len(acks) == 3
        for ack in acks:
            assert relay.ITEM_RE.match(ack["text"]) is None
        # and with every marker stripped, they are still not actionable
        bare = [{**r, "reactions": []} for r in thread]
        counts = relay.build_plan(bare, ALLOWLIST, approved_check=lambda n: False)["counts"]
        assert counts["item_replies"] == 12

    def test_stripping_the_marker_recovers_exactly_one_approval(self, thread):
        """`approved_check` stubbed False — the state #172 was actually in when
        this thread was recorded, before the label existed on it."""
        thread[0]["reactions"] = [r for r in thread[0]["reactions"] if r["name"] != relay.DONE]
        plan = relay.build_plan(thread, ALLOWLIST, approved_check=lambda n: False)["plan"]
        assert [(p["issue"], p["verb"]) for p in plan] == [(172, "approve")]
        assert plan[0]["who"] == ALLOWLIST[HUMAN]

    def test_the_same_thread_re_fires_once_the_issue_is_already_labelled(self, thread):
        """The bug this recording is of, stated as the fix.

        #172 was ✅'d five times between 2026-08-09 and 08-11. Every one after the
        first emitted `--add-label claude-implement` onto an issue that already had
        it — a silent no-op, because `claude.yml`'s implement job fires on the
        `labeled` *event* and GitHub emits none for a label that is already there.
        The relay acked all five, so nothing anywhere reported that four of them
        did nothing.
        """
        thread[0]["reactions"] = [r for r in thread[0]["reactions"] if r["name"] != relay.DONE]
        plan = relay.build_plan(thread, ALLOWLIST, approved_check=lambda n: True)["plan"]
        assert [(p["issue"], p["verb"]) for p in plan] == [(172, "refire")]
        assert plan[0]["command"][:3] == ["gh", "issue", "comment"]
        assert "<!-- implement-retry -->" in plan[0]["command"][-1]

    def test_an_unreadable_approval_state_still_approves(self, thread):
        """The asymmetry with `is_promotion`, which routes `None` to `ask`.

        Guessing wrong there starts an implementation run against a release ask.
        Guessing wrong here re-applies a label that is already present, which does
        nothing — so refusing to act would only strand the ordinary first approval
        behind a `gh` call the routine sessions' egress proxy is known to refuse.
        """
        thread[0]["reactions"] = [r for r in thread[0]["reactions"] if r["name"] != relay.DONE]
        plan = relay.build_plan(thread, ALLOWLIST, approved_check=lambda n: None)["plan"]
        assert [(p["issue"], p["verb"]) for p in plan] == [(172, "approve")]


class TestVerbs:
    def test_an_approval_adds_the_label_and_never_replaces_the_set(self):
        """#172 lost three labels in the second it gained one.

        `gh issue edit --add-label` adds; `gh api -X PUT .../labels` replaces. The
        emitted value is argv, so the second cannot be spelled from here — and the
        lost `workstream:` label is what scopes which paths the implement job may
        touch, so this is a boundary, not bookkeeping.
        """
        fresh = [item(172, white_check_mark=[HUMAN])]
        plan = relay.build_plan(fresh, ALLOWLIST, approved_check=lambda n: False)["plan"]
        assert plan[0]["command"] == ["gh", "issue", "edit", "172", "--add-label", "claude-implement"]

    @pytest.mark.parametrize("verb", ["approve", "refire", "promote", "campaign", "reject"])
    def test_no_emitted_command_can_replace_a_label_set(self, verb):
        """Over every verb by name, rather than over whichever ones a sample thread
        happens to reach — `refire` needs a stubbed label read to be reached at all,
        and a safety assertion that silently skips the newest verb is not one.

        `gh issue edit --add-label` adds; `gh api -X PUT .../labels` replaces. On
        2026-08-09 something ran the second, and #172 lost `cowork:proposal`,
        `workstream:web-ux` and `type:security` in the same second it gained
        `claude-implement` — leaving the implement job with no charter naming which
        paths it was allowed to touch.
        """
        argv = relay._command(verb, 7)
        assert "api" not in argv
        assert not {"PUT", "-X", "--method"} & set(argv)
        assert "--remove-label" not in argv

    def test_every_verb_build_plan_can_choose_has_a_command(self):
        """`_command` raises on an unknown verb, so a verb added to `build_plan`
        without one turns a relay run into a crash mid-plan — after it has already
        executed the entries before it."""
        for verb in ("approve", "refire", "promote", "campaign", "reject"):
            assert relay._command(verb, 1)[0] == "gh"

    def test_a_rejection_closes(self):
        plan = relay.build_plan([item(5, x=[HUMAN])], ALLOWLIST)["plan"]
        assert plan[0]["verb"] == "reject"
        assert plan[0]["command"] == ["gh", "issue", "close", "5"]

    def test_both_verbs_from_a_human_asks_and_acts_on_nothing(self):
        plan = relay.build_plan(
            [item(9, white_check_mark=[HUMAN], x=[HUMAN])], ALLOWLIST, approved_check=lambda n: False
        )["plan"]
        assert plan[0]["verb"] == "ask"
        assert plan[0]["command"] is None

    def test_the_plan_is_oldest_first(self):
        thread = [item(2, ts="200.5", white_check_mark=[HUMAN]), item(1, ts="100.5", white_check_mark=[HUMAN])]
        assert [p["issue"] for p in relay.build_plan(thread, ALLOWLIST)["plan"]] == [1, 2]


class TestPromotion:
    """✅ on the weekly ask releases the accumulated batch.

    The verb rides the proven `--add-label` path rather than dispatching a
    workflow, because a workflow cannot start another with GITHUB_TOKEN — but the
    relay is not CI, and `gh issue edit --add-label claude-implement` is already
    trusted here. This is that path with a different label.
    """

    def test_an_approval_on_a_promotion_ask_promotes(self):
        plan = relay.build_plan([promotion(231, white_check_mark=[HUMAN])], ALLOWLIST, promotion_check=lambda n: True)[
            "plan"
        ]
        assert plan[0]["verb"] == "promote"
        assert plan[0]["command"] == ["gh", "issue", "edit", "231", "--add-label", "release:promote"]

    def test_the_shape_alone_is_not_enough_without_the_label(self):
        """A user-written title can match the regex; only the label is authoritative.

        The damage is a *lost approval*, not a stray label: `publish.yml` would
        refuse the release anyway, but the ✅ meant as "build this" would have
        applied `release:promote` and never `claude-implement`, with nothing said.
        """
        plan = relay.build_plan([promotion(231, white_check_mark=[HUMAN])], ALLOWLIST, promotion_check=lambda n: False)[
            "plan"
        ]
        assert plan[0]["verb"] == "approve"
        assert plan[0]["command"][-1] == "claude-implement"

    @pytest.mark.parametrize("payload", [None, "not json", '{"labels": "wrong shape"}', ""])
    def test_an_unanswerable_question_is_not_a_no(self, payload):
        """`None`, never `False` — the difference decides whether an agent starts.

        `approve` is not a no-op: it applies `claude-implement`, and `claude.yml`
        fires on any issue receiving that label. Collapsing "could not reach
        GitHub" into "not a promotion" would turn one rate-limited `gh` call into
        an unattended implementation run against the release ask itself.
        """
        assert relay.is_promotion(231, runner=lambda argv: payload) is None

    def test_an_unreachable_github_asks_rather_than_approving(self):
        plan = relay.build_plan([promotion(231, white_check_mark=[HUMAN])], ALLOWLIST, promotion_check=lambda n: None)[
            "plan"
        ]
        assert plan[0]["verb"] == "ask"
        assert plan[0]["command"] is None, "no command at all — not `claude-implement` by another name"

    def test_the_label_is_read_from_github(self):
        seen = {}

        def runner(argv):
            seen["argv"] = argv
            return '{"labels": [{"name": "release:promotion"}, {"name": "type:chore"}]}'

        assert relay.is_promotion(231, runner=runner) is True
        assert seen["argv"] == ["gh", "issue", "view", "231", "--json", "labels,state"]
        assert relay.is_promotion(231, runner=lambda a: '{"labels": [{"name": "type:chore"}]}') is False

    def test_a_closed_ask_cannot_be_promoted(self):
        """The stale-Slack-reply hole, closed on the only side that can close it.

        `cron/release-promote-ask.md` supersedes an unanswered ask by closing it
        and opening a fresh one — which dedups GitHub and does nothing for Slack.
        Last week's thread reply is still in the 48h read window, still unmarked,
        and `publish.yml`'s guard fires on the `labeled` event without ever
        looking at issue state. A ✅ there would promote against a manifest nobody
        read. `ask` rather than `reject`: the human does want to promote, just not
        on that issue.
        """
        payload = '{"labels": [{"name": "release:promotion"}], "state": "CLOSED"}'
        assert relay.is_promotion(231, runner=lambda argv: payload) is None
        plan = relay.build_plan(
            [promotion(231, white_check_mark=[HUMAN])],
            ALLOWLIST,
            promotion_check=lambda n: relay.is_promotion(231, runner=lambda a: payload),
        )["plan"]
        assert plan[0]["verb"] == "ask"
        assert plan[0]["command"] is None, "no label at all — not release:promote, not claude-implement"

    def test_an_open_ask_still_promotes(self):
        payload = '{"labels": [{"name": "release:promotion"}], "state": "OPEN"}'
        assert relay.is_promotion(231, runner=lambda argv: payload) is True

    def test_a_payload_with_no_state_reads_as_open(self):
        """Absence is not evidence: an older transport shape is not a closed issue."""
        assert relay.is_promotion(231, runner=lambda a: '{"labels": [{"name": "release:promotion"}]}') is True

    def test_a_closed_ordinary_proposal_is_untouched_by_the_state_check(self):
        """The state read must only gate promotion, not ordinary approvals."""
        payload = '{"labels": [{"name": "type:chore"}], "state": "CLOSED"}'
        assert relay.is_promotion(231, runner=lambda a: payload) is False

    def test_an_ordinary_proposal_is_still_an_approval(self):
        fresh = [item(172, white_check_mark=[HUMAN])]
        plan = relay.build_plan(fresh, ALLOWLIST, approved_check=lambda n: False)["plan"]
        assert plan[0]["verb"] == "approve"
        assert plan[0]["command"][-1] == "claude-implement"

    @pytest.mark.parametrize(
        "text",
        [
            "#12 — [feature][web-ux] promote the share gate — https://example.invalid/12",
            "#12 — promote everything — https://example.invalid/12",
            "#12 — promoted 3.6.1 — https://example.invalid/12",
            "promote 3.6.1 — #12",
        ],
    )
    def test_a_title_that_merely_mentions_promote_does_not(self, text):
        """A proposal title is quoted verbatim into the thread, and anyone can
        file an issue on a public repo. Only the fixed contract routes the label."""
        plan = relay.build_plan(
            [reply("1", text, white_check_mark=[HUMAN])], ALLOWLIST, promotion_check=lambda n: True
        )["plan"]
        assert all(entry["verb"] != "promote" for entry in plan)

    def test_a_rejection_on_a_promotion_ask_just_closes_it(self):
        """ "Not this week" — next Monday's run opens a fresh ask."""
        plan = relay.build_plan([promotion(231, x=[HUMAN])], ALLOWLIST, promotion_check=lambda n: True)["plan"]
        assert plan[0]["verb"] == "reject"
        assert plan[0]["command"] == ["gh", "issue", "close", "231"]

    def test_the_promote_command_still_cannot_replace_a_label_set(self):
        for entry in relay.build_plan(
            [promotion(231, white_check_mark=[HUMAN])], ALLOWLIST, promotion_check=lambda n: True
        )["plan"]:
            argv = entry["command"]
            assert "api" not in argv
            assert not {"PUT", "-X", "--method"} & set(argv)
            assert "--remove-label" not in argv

    def test_an_unauthorised_reaction_promotes_nothing(self):
        plan = relay.build_plan(
            [promotion(231, white_check_mark=["USTRANGER"])], ALLOWLIST, promotion_check=lambda n: True
        )["plan"]
        assert plan == []


class TestCampaignCandidate:
    """✅ on a shortlisted provider makes it this week's campaign.

    Cloned from `TestPromotion` deliberately, including the tristate and the
    crafted-title defence, because the failure it prevents is worse. Reusing
    `claude-implement` here would not merely mislabel: `claude.yml` fires a
    110-turn unattended implement job on any issue receiving that label, and a
    candidate issue describes a week of work across six workstreams' files. The
    approval would be spent with nothing to show and no second chance until the
    next shortlist.
    """

    def test_an_approval_on_a_candidate_starts_the_campaign(self):
        plan = relay.build_plan([candidate(241, white_check_mark=[HUMAN])], ALLOWLIST, candidate_check=lambda n: True)[
            "plan"
        ]
        assert plan[0]["verb"] == "campaign"
        assert plan[0]["command"] == ["gh", "issue", "edit", "241", "--add-label", "integration:approved"]
        assert "claude-implement" not in plan[0]["command"], "this is the whole reason the label is separate"

    def test_the_shape_alone_is_not_enough_without_the_label(self):
        plan = relay.build_plan([candidate(241, white_check_mark=[HUMAN])], ALLOWLIST, candidate_check=lambda n: False)[
            "plan"
        ]
        assert plan[0]["verb"] == "approve"
        assert plan[0]["command"][-1] == "claude-implement"

    @pytest.mark.parametrize("payload", [None, "not json", '{"labels": "wrong shape"}', ""])
    def test_an_unanswerable_question_is_not_a_no(self, payload):
        assert relay.is_campaign_candidate(241, runner=lambda argv: payload) is None

    def test_an_unreachable_github_asks_rather_than_approving(self):
        plan = relay.build_plan([candidate(241, white_check_mark=[HUMAN])], ALLOWLIST, candidate_check=lambda n: None)[
            "plan"
        ]
        assert plan[0]["verb"] == "ask"
        assert plan[0]["command"] is None, "no command at all — not `claude-implement` by another name"

    def test_the_label_is_read_from_github(self):
        seen = {}

        def runner(argv):
            seen["argv"] = argv
            return '{"labels": [{"name": "integration:candidate"}]}'

        assert relay.is_campaign_candidate(241, runner=runner) is True
        assert seen["argv"] == ["gh", "issue", "view", "241", "--json", "labels,state"]
        assert relay.is_campaign_candidate(241, runner=lambda a: '{"labels": [{"name": "type:chore"}]}') is False

    def test_a_closed_candidate_cannot_be_approved(self):
        """Monday supersedes an unanswered shortlist by closing it and filing a
        fresh one — which dedups GitHub and does nothing for Slack. Last week's
        three replies are still in the read window. A late ✅ would approve a
        campaign against a shortlist nobody re-read."""
        payload = '{"labels": [{"name": "integration:candidate"}], "state": "CLOSED"}'
        assert relay.is_campaign_candidate(241, runner=lambda argv: payload) is None
        plan = relay.build_plan(
            [candidate(241, white_check_mark=[HUMAN])],
            ALLOWLIST,
            candidate_check=lambda n: relay.is_campaign_candidate(241, runner=lambda a: payload),
        )["plan"]
        assert plan[0]["verb"] == "ask"
        assert plan[0]["command"] is None

    def test_a_payload_with_no_state_reads_as_open(self):
        assert relay.is_campaign_candidate(241, runner=lambda a: '{"labels": [{"name": "integration:candidate"}]}')

    def test_an_ordinary_proposal_is_still_an_approval(self):
        """The test that would have caught reusing `claude-implement` for the pick."""
        plan = relay.build_plan(
            [item(172, white_check_mark=[HUMAN])],
            ALLOWLIST,
            candidate_check=lambda n: True,
            approved_check=lambda n: False,
        )["plan"]
        assert plan[0]["verb"] == "approve"
        assert plan[0]["command"][-1] == "claude-implement"

    @pytest.mark.parametrize(
        "text",
        [
            "#12 — [bug][platform] integration candidates keep timing out — https://example.invalid/12",
            "#12 — integration candidates — https://example.invalid/12",
            "integration candidate: gitlab — #12",
        ],
    )
    def test_a_title_that_merely_mentions_one_does_not(self, text):
        plan = relay.build_plan(
            [reply("1", text, white_check_mark=[HUMAN])], ALLOWLIST, candidate_check=lambda n: True
        )["plan"]
        assert all(entry["verb"] != "campaign" for entry in plan)

    def test_a_rejection_just_closes_it(self):
        """❌ is "not this provider", and Monday reads closed candidates as the
        standing record of what it must not re-propose."""
        plan = relay.build_plan([candidate(241, x=[HUMAN])], ALLOWLIST, candidate_check=lambda n: True)["plan"]
        assert plan[0]["verb"] == "reject"
        assert plan[0]["command"] == ["gh", "issue", "close", "241"]

    def test_the_campaign_command_cannot_replace_a_label_set(self):
        for entry in relay.build_plan(
            [candidate(241, white_check_mark=[HUMAN])], ALLOWLIST, candidate_check=lambda n: True
        )["plan"]:
            argv = entry["command"]
            assert "api" not in argv
            assert not {"PUT", "-X", "--method"} & set(argv)
            assert "--remove-label" not in argv

    def test_an_unauthorised_reaction_approves_nothing(self):
        plan = relay.build_plan(
            [candidate(241, white_check_mark=["USTRANGER"])], ALLOWLIST, candidate_check=lambda n: True
        )["plan"]
        assert plan == []

    def test_a_promotion_and_a_candidate_do_not_collide(self):
        """Two special contracts in one thread, each routed by its own label."""
        plan = relay.build_plan(
            [promotion(231, ts="1", white_check_mark=[HUMAN]), candidate(241, ts="2", white_check_mark=[HUMAN])],
            ALLOWLIST,
            promotion_check=lambda n: True,
            candidate_check=lambda n: True,
        )["plan"]
        assert [entry["verb"] for entry in plan] == ["promote", "campaign"]


class TestAuthorisation:
    def test_a_reaction_from_outside_the_allowlist_authorises_nothing(self):
        plan = relay.build_plan([item(3, white_check_mark=["U000NOTME"])], ALLOWLIST)["plan"]
        assert plan == [], "a non-allowlisted reaction is ignored silently, not relayed"

    def test_the_bots_own_marker_is_not_an_approval(self):
        thread = [item(4, white_check_mark=[BOT])]
        assert relay.build_plan(thread, ALLOWLIST)["plan"] == []

    def test_an_empty_allowlist_stops_everything(self):
        """The routine's own stop condition: a placeholder table means act on nothing."""
        assert relay.build_plan([item(7, white_check_mark=[HUMAN])], {})["plan"] == []

    def test_the_allowlist_is_read_from_the_routine_that_documents_it(self):
        found = relay.parse_allowlist(relay.RELAY_ROUTINE.read_text())
        assert HUMAN in found, "the relay routine's table is the versioned source of who may approve"
        assert all(uid.startswith("U") for uid in found)


class TestInput:
    def test_a_bare_array_and_a_slack_envelope_both_load(self):
        entries = [item(1)]
        assert relay.load_replies(json.dumps(entries)) == entries
        assert relay.load_replies(json.dumps({"messages": entries})) == entries

    def test_a_reply_with_no_reactions_key_is_not_a_crash(self):
        assert relay.build_plan([{"ts": "1", "text": "#8 — a thing — url"}], ALLOWLIST)["plan"] == []

    def test_non_json_is_a_clean_error_not_a_traceback(self):
        with pytest.raises(relay.RelayError):
            relay.load_replies("not json")


class TestTheMarkerIsAuthorised:
    """A 🤖 from outside the allowlist must not be able to bury an approval.

    The marker is how this module decides an item is handled, and it is written
    through the same Slack connector that posts as the human — the ✅ and the 🤖
    on the #172 reply are both under `U0BLM1QU3JN`, confirmed via
    `slack_get_reactions`. Ungated, any member of the channel could mark an item
    and suppress it from every future run, and the run accounting would report it
    as one more handled reply.
    """

    def test_a_marker_from_a_stranger_does_not_suppress_an_approval(self):
        thread = [item(11, white_check_mark=[HUMAN], robot_face=["U000NOTME"])]
        result = relay.build_plan(thread, ALLOWLIST)
        assert [p["issue"] for p in result["plan"]] == [11], "a stranger must not be able to veto"
        assert result["counts"]["marked"] == 0

    def test_a_disregarded_marker_is_counted_rather_than_swallowed(self):
        thread = [item(11, white_check_mark=[HUMAN], robot_face=["U000NOTME"])]
        assert relay.build_plan(thread, ALLOWLIST)["counts"]["ignored_markers"] == 1

    def test_a_marker_from_the_allowlist_still_means_handled(self):
        thread = [item(11, white_check_mark=[HUMAN], robot_face=[HUMAN])]
        result = relay.build_plan(thread, ALLOWLIST)
        assert result["plan"] == []
        assert result["counts"] == {
            "replies": 1,
            "item_replies": 1,
            "marked": 1,
            "ignored_markers": 0,
            "actionable": 0,
        }


class TestPlaceholderAllowlist:
    """The routine's stop condition is "any row is a placeholder OR the table is
    empty". A half-filled table is the more dangerous of the two: it looks
    configured, so nothing would prompt anyone to look at it."""

    @pytest.mark.parametrize(
        "row",
        [
            "| `UXXXXXXXX` | your slack member id |",
            "| `U000000000` | fill me in |",
            "| `U0BLM1QU3JN` | <who> |",
            "| `U0BLM1QU3JN` | placeholder |",
        ],
    )
    def test_a_placeholder_row_disables_the_whole_table(self, row):
        assert relay.parse_allowlist(f"| id | who |\n|---|---|\n{row}\n") == {}

    def test_a_real_row_still_parses(self):
        table = "| id | who |\n|---|---|\n| `U0BLM1QU3JN` | onoureldin (onoureldin@gmail.com) |\n"
        assert relay.parse_allowlist(table) == {HUMAN: "onoureldin (onoureldin@gmail.com)"}


class TestEntryPoint:
    def test_plan_is_required(self, capsys):
        with pytest.raises(SystemExit):
            relay.main([])

    def test_a_plan_reaches_stdout_as_json(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps([item(3, white_check_mark=[HUMAN])])))
        assert relay.main(["--plan"]) == 0
        emitted = json.loads(capsys.readouterr().out)
        assert emitted["plan"][0]["issue"] == 3

    def test_bad_input_is_an_exit_code_not_a_traceback(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        assert relay.main(["--plan"]) == 2
        assert "cowork_relay:" in capsys.readouterr().err

    def test_a_missing_allowlist_file_is_reported(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr("sys.stdin", io.StringIO("[]"))
        assert relay.main(["--plan", "--allowlist-from", str(tmp_path / "nope.md")]) == 2

    def test_no_command_for_an_unknown_verb(self):
        with pytest.raises(relay.RelayError):
            relay._command("shrug", 1)


class TestTheDigestWritesWhatTheRelayParses:
    """The reply shape is a contract between two files that never see each other.

    `cron/digest.md` is prose a model follows; `CANDIDATE_RE` is a regex that
    reads the result an hour later. Nothing at run time reconciles them, and the
    failure is not a parse error: a candidate written in the ordinary
    verbatim-title shape still matches `ITEM_RE`, so a ✅ on it resolves to
    `approve` and applies `claude-implement` — and `claude.yml` fires a 110-turn
    unattended implement job on an issue describing a week of work across six
    workstreams' files. That is the exact outcome the separate
    `integration:approved` label exists to prevent.
    """

    def _examples(self) -> list[str]:
        text = (ROOT / "cowork" / "routines" / "cron" / "digest.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```slack-reply\n(.*?)```", text, re.S)
        assert blocks, "digest.md no longer shows the reply shape it is required to write"
        return [line.strip() for block in blocks for line in block.splitlines() if line.strip()]

    def test_every_example_reply_is_one_the_relay_can_read(self):
        for line in self._examples():
            assert relay.ITEM_RE.match(line), f"the relay would ignore this reply entirely: {line}"

    def test_the_candidate_shape_is_shown_and_routes_to_the_campaign_label(self):
        """Shown, not merely described: the model copies the example."""
        candidates = [line for line in self._examples() if relay.CANDIDATE_RE.match(line)]
        assert candidates, "digest.md shows no `integration candidate:` reply, so ✅ on one cannot work"
        for line in candidates:
            assert "claude-implement" not in line

    def test_a_candidate_written_in_the_ordinary_shape_would_route_to_implement(self):
        """Why the test above matters, stated as the failure it prevents."""
        wrong = "#248 — Integrate GitLab so the roadmap can read epics — https://example.invalid/248"
        assert relay.ITEM_RE.match(wrong) and not relay.CANDIDATE_RE.match(wrong)
