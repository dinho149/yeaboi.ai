"""`yeaboi slack …` — the only surface `link`, `unlink` and `members` have.

Two properties carry these tests. **A declined poll is not a failed one**, so a
cron job that could not act does not page anybody — the same exit-code rule the
ceremonies runner follows. And **a refused link is announced**: a binding that
silently did not happen leaves somebody believing their corrections carry their
name when they carry a raw id.
"""

from __future__ import annotations

import json

import pytest
from rich.console import Console

from yeaboi.cli import _cmd_slack, build_parser
from yeaboi.slack.poller import PollResult
from yeaboi.slack.store import OUTCOME_APPLIED, POLL_NO_TOKEN, InboundEvent, SlackStore
from yeaboi.tools.slack import SlackResponse


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = tmp_path / "sessions.db"
    monkeypatch.setattr("yeaboi.paths.get_db_path", lambda: db)
    monkeypatch.setattr("yeaboi.mcp.tools_sessions.resolve_session_id", lambda sid="": sid or "s1")
    monkeypatch.setattr("yeaboi.slack.identity.roster", lambda _s, **_kw: ["Ada Lovelace", "Ben Carter"])
    return db


def _run(*argv, capsys) -> tuple[int, str]:
    """(exit code, everything the command printed).

    Mirrors cli.py's dispatcher: in JSON mode the human-facing console is bound
    to stderr so stdout stays machine-clean, which is what the JSON tests below
    then assert by reading stdout alone.
    """
    args = build_parser().parse_args(["slack", *argv])
    to_json = getattr(args, "format", "text") == "json"
    code = _cmd_slack(args, Console(stderr=to_json, width=200))
    captured = capsys.readouterr()
    return code, (captured.out if to_json else captured.out + captured.err)


class TestLinking:
    def test_a_link_round_trips_through_the_cli(self, env, capsys):
        assert _run("link", "U0123456789", "Ada Lovelace", capsys=capsys)[0] == 0
        code, out = _run("link", capsys=capsys)
        assert code == 0
        assert "U0123456789" in out and "Ada Lovelace" in out

    def test_a_refused_link_exits_nonzero_and_says_why(self, env, capsys):
        code, out = _run("link", "U0123456789", "Grace Hopper", capsys=capsys)
        assert code == 1
        assert "not on this session's roster" in out

    def test_listing_nothing_says_the_lane_still_works(self, env, capsys):
        code, out = _run("link", capsys=capsys)
        assert code == 0
        assert "still works" in out

    def test_unlinking_says_whether_anything_went(self, env, capsys):
        _run("link", "U0123456789", "Ada Lovelace", capsys=capsys)
        assert "Unlinked" in _run("unlink", "U0123456789", capsys=capsys)[1]
        assert "Nothing was linked" in _run("unlink", "U0123456789", capsys=capsys)[1]

    def test_json_mode_keeps_stdout_machine_clean(self, env, capsys):
        _run("link", "U0123456789", "Ada Lovelace", capsys=capsys)
        code, out = _run("link", "--format", "json", capsys=capsys)
        assert code == 0
        assert [row["member"] for row in json.loads(out)["identities"]] == ["Ada Lovelace"]

    def test_a_refusal_is_still_json_in_json_mode(self, env, capsys):
        code, out = _run("link", "U0123456789", "Grace Hopper", "--format", "json", capsys=capsys)
        assert code == 1
        assert "roster" in json.loads(out)["error"]


class TestMembers:
    @pytest.fixture()
    def workspace(self, monkeypatch):
        people = [
            {"id": "U0123456789", "name": "ada", "profile": {"real_name": "Ada Lovelace"}},
            {"id": "U0987654321", "name": "ben", "profile": {"real_name": "Ben Carter"}},
            {"id": "B0000000001", "name": "someapp", "is_bot": True, "profile": {}},
            {"id": "USLACKBOT", "name": "slackbot", "profile": {}},
            {"id": "U0000000009", "name": "gone", "deleted": True, "profile": {}},
        ]
        monkeypatch.setattr(
            "yeaboi.tools.slack.users_list", lambda **_kw: SlackResponse(ok=True, data={"members": people})
        )
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (True, ""))
        return people

    def test_it_lists_people_with_their_ids(self, env, workspace, capsys):
        code, out = _run("members", capsys=capsys)
        assert code == 0
        assert "U0123456789" in out and "Ada Lovelace" in out

    def test_bots_apps_slackbot_and_deleted_accounts_are_never_offered(self, env, workspace, capsys):
        # Every one of them is a member id you can never usefully link or allow,
        # so offering it is offering a dead end.
        _code, out = _run("members", capsys=capsys)
        for absent in ("B0000000001", "USLACKBOT", "U0000000009"):
            assert absent not in out

    def test_match_narrows(self, env, workspace, capsys):
        _code, out = _run("members", "--match", "ben", capsys=capsys)
        assert "Ben Carter" in out and "Ada Lovelace" not in out

    def test_a_slack_error_is_reported_with_its_fix_rather_than_an_empty_list(self, env, monkeypatch, capsys):
        # An empty list and a refused call look identical on screen otherwise,
        # and only one of them is the user's to fix.
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (True, ""))
        monkeypatch.setattr(
            "yeaboi.tools.slack.users_list", lambda **_kw: SlackResponse(ok=False, error="missing_scope")
        )
        code, out = _run("members", capsys=capsys)
        assert code == 1
        assert "missing_scope" in out

    def test_no_two_way_exits_nonzero_without_calling_slack(self, env, monkeypatch, capsys):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (False, "no SLACK_BOT_TOKEN"))
        monkeypatch.setattr(
            "yeaboi.tools.slack.users_list", lambda **_kw: pytest.fail("called Slack with nothing configured")
        )
        code, out = _run("members", capsys=capsys)
        assert code == 1
        assert "no SLACK_BOT_TOKEN" in out


class TestCheck:
    """`check` answers "can this actually read the channel", not "is the token live".

    ``auth.test`` succeeds for a bot that was never invited anywhere, and
    ``not_in_channel`` is the most common real Slack failure there is — so a
    check that stopped at identity reported "on" for exactly the configuration
    whose failure mode the error table calls out by name.
    """

    @pytest.fixture()
    def configured(self, monkeypatch):
        monkeypatch.setattr("yeaboi.config.slack_two_way_ready", lambda: (True, ""))
        monkeypatch.setattr("yeaboi.config.get_slack_channel_id", lambda: "C0123456789")
        monkeypatch.setattr(
            "yeaboi.tools.slack.auth_test",
            lambda **_kw: SlackResponse(ok=True, data={"team": "Acme", "user": "yeaboi", "user_id": "U0BOT"}),
        )

    def test_a_readable_channel_passes(self, env, configured, monkeypatch, capsys):
        monkeypatch.setattr(
            "yeaboi.tools.slack.history", lambda *_a, **_kw: SlackResponse(ok=True, data={"messages": []})
        )
        code, out = _run("check", capsys=capsys)
        assert code == 0
        assert "readable" in out

    def test_an_uninvited_bot_fails_and_says_which_half_broke(self, env, configured, monkeypatch, capsys):
        monkeypatch.setattr(
            "yeaboi.tools.slack.history", lambda *_a, **_kw: SlackResponse(ok=False, error="not_in_channel")
        )
        code, out = _run("check", capsys=capsys)
        assert code == 1, "a bot that cannot read the channel is not configured"
        assert "invite" in out.lower() or "not_in_channel" in out

    def test_the_probe_reads_the_configured_channel(self, env, configured, monkeypatch, capsys):
        seen: list = []

        def _probe(channel, **kw):
            seen.append((channel, kw.get("limit")))
            return SlackResponse(ok=True, data={"messages": []})

        monkeypatch.setattr("yeaboi.tools.slack.history", _probe)
        _run("check", capsys=capsys)
        assert seen == [("C0123456789", 1)], "one message off the channel the poll will read"


class TestPoll:
    def test_a_decline_is_not_a_failure(self, env, monkeypatch, capsys):
        # The rule that keeps a cron job that could not act from paging anybody.
        monkeypatch.setattr("yeaboi.slack.poller.run_poll", lambda **_kw: PollResult(outcome=POLL_NO_TOKEN))
        assert _run("poll", capsys=capsys)[0] == 0

    def test_a_failure_is(self, env, monkeypatch, capsys):
        monkeypatch.setattr("yeaboi.slack.poller.run_poll", lambda **_kw: PollResult(outcome="failed"))
        assert _run("poll", capsys=capsys)[0] == 1

    def test_scheduled_is_accepted_and_changes_nothing(self, env, monkeypatch, capsys):
        # The installed job's argv carries it, so argparse must keep accepting
        # it — and a poll's guards are unconditional, so it selects nothing.
        seen: list[dict] = []
        monkeypatch.setattr("yeaboi.slack.poller.run_poll", lambda **kw: seen.append(kw) or PollResult())
        assert _run("poll", "--scheduled", capsys=capsys)[0] == 0
        assert "scheduled" not in seen[0]


class TestHistory:
    def test_it_reports_the_refusals_too(self, env, capsys):
        with SlackStore(env) as store:
            store.claim(InboundEvent(event_key="k1", channel="C123", act="control", slack_user="U1"))
            store.settle("k1", outcome="unauthorized", reason="not on the list")
        _code, out = _run("history", capsys=capsys)
        assert "unauthorized" in out and "not on the list" in out

    def test_pending_narrows_to_what_a_crash_left(self, env, capsys):
        with SlackStore(env) as store:
            store.claim(InboundEvent(event_key="done", channel="C123", act="control"))
            store.settle("done", outcome=OUTCOME_APPLIED)
            store.claim(InboundEvent(event_key="crashed", channel="C123", act="control", slack_user="U9"))
        _code, out = _run("history", "--pending", capsys=capsys)
        assert "U9" in out
        assert "applied" not in out
