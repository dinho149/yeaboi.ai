"""The headless surface of the Slack lane.

The shape of the entry-point set is the thing worth pinning: **one that acts,
two that read, and nothing that configures.** A function that installed the
poll, set a token or edited the allowlist would put an operating-system write
and a credential write behind a surface that is not the terminal the job runs
on — so the absence is asserted, not just observed.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from yeaboi.slack import engine
from yeaboi.slack.identity import IdentityError
from yeaboi.slack.poller import PollResult
from yeaboi.slack.store import OUTCOME_APPLIED, POLL_NO_TOKEN, InboundEvent, SlackStore

_ENGINE = pathlib.Path(engine.__file__)


@pytest.fixture
def db(tmp_path):
    return tmp_path / "sessions.db"


class TestTheEntryPointSet:
    def test_exactly_three_public_functions(self):
        tree = ast.parse(_ENGINE.read_text(encoding="utf-8"))
        public = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_")
        }
        assert public == {"apply_inbound_events", "inbound_history", "link_slack_member"}

    def test_nothing_here_writes_to_the_os_or_to_the_env(self):
        # `slack watch` stays a CLI verb over ceremonies.scheduler, and the token
        # and allowlist stay in ~/.yeaboi/.env, for the same reason `ceremonies
        # add` does: they are decisions made at the machine that will run the job.
        #
        # Read out of the AST rather than off the raw text, for the reason the
        # OP_NOTE guard is: writing the rule down in a docstring must not trip it.
        tree = ast.parse(_ENGINE.read_text(encoding="utf-8"))
        named = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Name | ast.Attribute)
        }
        named |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
        reaching = sorted(
            name
            for name in named
            if "scheduler" in name or name.startswith(("set_slack_", "install_")) or name == "set_config_value"
        )
        assert not reaching, f"the Slack engine reaches for {reaching}"

    def test_imports_stay_inside_the_functions(self):
        # The recurring poll starts a fresh process every few minutes, so nothing
        # here may drag LangChain onto the start-up path.
        tree = ast.parse(_ENGINE.read_text(encoding="utf-8"))
        top = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
        assert {alias.name for node in top for alias in node.names} <= {"annotations", "logging", "pathlib.Path"} | {
            "Path"
        }


class TestApplyInboundEvents:
    def test_it_returns_the_poll_and_says_whether_it_declined(self, monkeypatch, db):
        monkeypatch.setattr(
            "yeaboi.slack.poller.run_poll",
            lambda **_kw: PollResult(outcome=POLL_NO_TOKEN, detail="no SLACK_BOT_TOKEN"),
        )
        result = engine.apply_inbound_events(db_path=db)
        assert result["outcome"] == POLL_NO_TOKEN
        assert result["declined"] is True
        assert result["detail"] == "no SLACK_BOT_TOKEN"

    def test_an_applied_poll_did_not_decline(self, monkeypatch, db):
        monkeypatch.setattr("yeaboi.slack.poller.run_poll", lambda **_kw: PollResult(events_applied=2, events_seen=3))
        result = engine.apply_inbound_events(db_path=db)
        assert (result["declined"], result["events_applied"]) == (False, 2)

    def test_there_is_no_scheduled_flag(self):
        # Deliberate, and the opposite of `run_ceremony`. A poll reads a fixed
        # window and every act it applies is free and idempotent, so its overlap
        # lock and gap notice are unconditional — there is nothing for a flag to
        # arm, and a parameter that selects nothing is worse than none.
        import inspect

        assert "scheduled" not in inspect.signature(engine.apply_inbound_events).parameters


class TestInboundHistory:
    def _claim(self, db, key: str, outcome: str = OUTCOME_APPLIED) -> None:
        with SlackStore(db) as store:
            store.claim(InboundEvent(event_key=key, channel="C123", anchor_ts="1.0", act="control", slack_user="U1"))
            if outcome:
                store.settle(key, outcome=outcome)

    def test_it_reads_what_the_lane_did(self, db):
        self._claim(db, "react:C123:1.0:U1:+1")
        result = engine.inbound_history(db_path=db)
        assert [e["event_key"] for e in result["events"]] == ["react:C123:1.0:U1:+1"]
        assert result["pending_only"] is False

    def test_pending_narrows_to_what_a_crash_left(self, db):
        self._claim(db, "settled", OUTCOME_APPLIED)
        self._claim(db, "crashed", "")
        assert [e["event_key"] for e in engine.inbound_history(pending=True, db_path=db)["events"]] == ["crashed"]

    def test_it_carries_whether_the_reader_is_even_running(self, db):
        # A different question from "did my reaction register", and the one
        # nobody thinks to ask until a week of them have gone unanswered.
        with SlackStore(db) as store:
            store.record_poll({"outcome": "ok", "events_applied": 1})
        assert engine.inbound_history(db_path=db)["recent_polls"][0]["outcome"] == "ok"

    @pytest.mark.parametrize("limit", [0, -1, 201])
    def test_an_out_of_range_limit_is_refused(self, db, limit):
        with pytest.raises(ValueError, match="between 1 and 200"):
            engine.inbound_history(limit=limit, db_path=db)


class TestLinkSlackMember:
    @pytest.fixture(autouse=True)
    def _roster(self, monkeypatch):
        monkeypatch.setattr("yeaboi.slack.identity.roster", lambda _s, **_kw: ["Ada Lovelace"])

    def test_no_slack_user_lists(self, db):
        engine.link_slack_member("s1", "U0123456789", "Ada Lovelace", db_path=db)
        result = engine.link_slack_member("s1", db_path=db)
        assert [row["member"] for row in result["identities"]] == ["Ada Lovelace"]

    def test_linking_reports_what_it_bound(self, db):
        assert engine.link_slack_member("s1", "U0123456789", "Ada Lovelace", db_path=db)["linked"].endswith(
            "Ada Lovelace"
        )

    def test_unlinking_reports_whether_anything_went(self, db):
        engine.link_slack_member("s1", "U0123456789", "Ada Lovelace", db_path=db)
        assert engine.link_slack_member("s1", "U0123456789", unlink=True, db_path=db)["unlinked"] is True
        assert engine.link_slack_member("s1", "U0123456789", unlink=True, db_path=db)["unlinked"] is False

    def test_a_bad_link_raises_rather_than_returning_a_flag(self, db):
        # A link that silently did not happen leaves somebody believing their
        # corrections carry their name when they carry an id.
        with pytest.raises(IdentityError):
            engine.link_slack_member("s1", "U0123456789", "Nobody At All", db_path=db)
