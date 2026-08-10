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
import json
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


def item(number: int, ts: str = "1", **reactions: list[str]) -> dict:
    return reply(ts, f"#{number} — [bug][platform] something — https://example.invalid/{number}", **reactions)


class TestRecordedFailure:
    """The #172 thread, exactly as Slack holds it."""

    @pytest.fixture
    def thread(self) -> list[dict]:
        return relay.load_replies(FIXTURE.read_text())

    def test_the_thread_that_was_acked_three_times_now_plans_nothing(self, thread):
        result = relay.build_plan(thread, ALLOWLIST)
        assert result["plan"] == [], "the ✅ carries the marker — re-announcing it is the bug this fixes"
        assert result["counts"] == {"replies": 15, "item_replies": 12, "marked": 1, "actionable": 0}

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
        assert relay.build_plan(bare, ALLOWLIST)["counts"]["item_replies"] == 12

    def test_stripping_the_marker_recovers_exactly_one_approval(self, thread):
        thread[0]["reactions"] = [r for r in thread[0]["reactions"] if r["name"] != relay.DONE]
        plan = relay.build_plan(thread, ALLOWLIST)["plan"]
        assert [(p["issue"], p["verb"]) for p in plan] == [(172, "approve")]
        assert plan[0]["who"] == ALLOWLIST[HUMAN]


class TestVerbs:
    def test_an_approval_adds_the_label_and_never_replaces_the_set(self):
        """#172 lost three labels in the second it gained one.

        `gh issue edit --add-label` adds; `gh api -X PUT .../labels` replaces. The
        emitted value is argv, so the second cannot be spelled from here — and the
        lost `workstream:` label is what scopes which paths the implement job may
        touch, so this is a boundary, not bookkeeping.
        """
        plan = relay.build_plan([item(172, white_check_mark=[HUMAN])], ALLOWLIST)["plan"]
        assert plan[0]["command"] == ["gh", "issue", "edit", "172", "--add-label", "claude-implement"]

    def test_no_emitted_command_can_replace_a_label_set(self):
        thread = [item(1, ts="1", white_check_mark=[HUMAN]), item(2, ts="2", x=[HUMAN])]
        for entry in relay.build_plan(thread, ALLOWLIST)["plan"]:
            argv = entry["command"]
            assert "api" not in argv
            assert not {"PUT", "-X", "--method"} & set(argv)
            assert "--remove-label" not in argv

    def test_a_rejection_closes(self):
        plan = relay.build_plan([item(5, x=[HUMAN])], ALLOWLIST)["plan"]
        assert plan[0]["verb"] == "reject"
        assert plan[0]["command"] == ["gh", "issue", "close", "5"]

    def test_both_verbs_from_a_human_asks_and_acts_on_nothing(self):
        plan = relay.build_plan([item(9, white_check_mark=[HUMAN], x=[HUMAN])], ALLOWLIST)["plan"]
        assert plan[0]["verb"] == "ask"
        assert plan[0]["command"] is None

    def test_the_plan_is_oldest_first(self):
        thread = [item(2, ts="200.5", white_check_mark=[HUMAN]), item(1, ts="100.5", white_check_mark=[HUMAN])]
        assert [p["issue"] for p in relay.build_plan(thread, ALLOWLIST)["plan"]] == [1, 2]


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
