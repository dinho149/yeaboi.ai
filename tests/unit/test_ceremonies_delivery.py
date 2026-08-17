"""Unit tests for the delivery channels (stdlib mocks).

Promoted out of ``standup/delivery.py``. Every channel now takes a ``Dispatch``
— a title, a one-line summary and a plaintext body — instead of a
``StandupReport``, which is what let the agent standup stop re-implementing the
webhook POST.

The load-bearing class here is ``TestStandupBytesAreUnchanged``: those bytes
already go to real Slack channels and real inboxes, and a refactor that
reformats them is a behaviour change wearing a refactor's clothes.
"""

from unittest.mock import MagicMock

import pytest

from yeaboi.agent.state import Dispatch, MemberUpdate, StandupReport
from yeaboi.ceremonies import delivery
from yeaboi.ceremonies.delivery import (
    DesktopDelivery,
    EmailDelivery,
    SlackDelivery,
    TerminalDelivery,
    deliver,
    get_delivery,
)
from yeaboi.ceremonies.renderers import standup_dispatch


def _report() -> StandupReport:
    return StandupReport(
        date="2026-07-10",
        sprint_name="Sprint 5",
        sprint_day=3,
        sprint_total_days=10,
        confidence_pct=82,
        confidence_label="At risk",
        confidence_rationale="behind ideal burn",
        team_summary="steady progress",
        member_updates=(
            MemberUpdate(name="Alice", summary="login page", source="inferred"),
            MemberUpdate(name="Bob", summary="paired on auth", blockers="waiting on review", source="self-reported"),
        ),
        activity_counts=(("github", 2), ("jira", 1)),
    )


def _dispatch(**overrides) -> Dispatch:
    base = {"title": "Daily Standup — 2026-07-10", "summary": "steady progress", "body": "the whole standup"}
    return Dispatch(**{**base, **overrides})


class TestStandupBytesAreUnchanged:
    """The refactor must not reword anything already landing in a channel."""

    def test_slack_and_email_still_send_the_plaintext_renderers_output(self):
        from yeaboi.standup.render import format_standup_plaintext

        report = _report()
        assert standup_dispatch(report).body == format_standup_plaintext(report)

    def test_the_desktop_one_liner_is_the_one_it_always_was(self):
        # team_summary, falling back to the confidence rationale.
        assert standup_dispatch(_report()).summary == "steady progress"
        thin = StandupReport(date="2026-07-10", confidence_rationale="not much in yet")
        assert standup_dispatch(thin).summary == "not much in yet"

    def test_a_standup_with_nothing_to_say_still_says_something(self):
        assert standup_dispatch(StandupReport(date="2026-07-10")).summary


class TestTerminalDelivery:
    def test_prints_the_body_and_succeeds(self, capsys):
        assert TerminalDelivery().send(_dispatch()) is True
        out = capsys.readouterr().out
        assert "Daily Standup" in out
        assert "the whole standup" in out

    def test_falls_back_to_the_summary_when_there_is_no_body(self, capsys):
        assert TerminalDelivery().send(_dispatch(body="")) is True
        assert "steady progress" in capsys.readouterr().out


class TestNotifyDesktop:
    def test_posts_a_notification_without_a_dispatch(self, monkeypatch):
        run = MagicMock()
        monkeypatch.setattr(delivery.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(delivery.subprocess, "run", run)
        assert delivery.notify_desktop("Title", "Body") is True
        assert run.call_args[0][0][:2] == ["osascript", "-e"]

    def test_the_argv_injection_guard_moved_with_it(self, monkeypatch):
        # Title/body are LLM-generated. They must arrive as argv items, never
        # interpolated into AppleScript source, which can `do shell script`.
        run = MagicMock()
        monkeypatch.setattr(delivery.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(delivery.subprocess, "run", run)
        evil = '" & (do shell script "touch /tmp/pwned") & "'
        delivery.notify_desktop("T", evil)
        argv = run.call_args[0][0]
        assert evil in argv  # passed as data
        assert evil not in argv[2]  # never in the script source

    def test_body_is_clipped(self, monkeypatch):
        run = MagicMock()
        monkeypatch.setattr(delivery.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(delivery.subprocess, "run", run)
        delivery.notify_desktop("T", "x" * 500)
        assert len(run.call_args[0][0][3]) == 200

    def test_unsupported_platform_returns_false(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Windows")
        assert delivery.notify_desktop("T", "B") is False

    def test_a_missing_helper_never_raises(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Linux")
        monkeypatch.setattr(delivery.subprocess, "run", MagicMock(side_effect=FileNotFoundError))
        assert delivery.notify_desktop("T", "B") is False


class TestDesktopDelivery:
    def test_sends_the_title_and_the_one_line_summary(self, monkeypatch):
        run = MagicMock()
        monkeypatch.setattr(delivery.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(delivery.subprocess, "run", run)
        assert DesktopDelivery().send(_dispatch()) is True
        argv = run.call_args[0][0]
        assert argv[3] == "steady progress"  # body position
        assert argv[4] == "Daily Standup — 2026-07-10"  # title position

    def test_linux_uses_notify_send(self, monkeypatch):
        run = MagicMock()
        monkeypatch.setattr(delivery.platform, "system", lambda: "Linux")
        monkeypatch.setattr(delivery.subprocess, "run", run)
        assert DesktopDelivery().send(_dispatch()) is True
        assert run.call_args[0][0][0] == "notify-send"

    def test_unsupported_platform_returns_false(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Windows")
        assert DesktopDelivery().send(_dispatch()) is False

    def test_missing_binary_returns_false(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Linux")
        monkeypatch.setattr(delivery.subprocess, "run", MagicMock(side_effect=FileNotFoundError))
        assert DesktopDelivery().send(_dispatch()) is False


class TestSlackDelivery:
    def test_posts_the_body(self, monkeypatch):
        import json

        opened = {}

        class _Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            opened["body"] = json.loads(req.data.decode())
            return _Resp()

        monkeypatch.setattr(delivery.urllib.request, "urlopen", fake_urlopen)
        assert SlackDelivery("https://hooks.slack.com/services/x").send(_dispatch()) is True
        assert opened["body"] == {"text": "the whole standup"}

    def test_no_webhook_is_a_handled_failure_not_a_crash(self):
        assert SlackDelivery("").send(_dispatch()) is False

    def test_network_error_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            delivery.urllib.request, "urlopen", MagicMock(side_effect=delivery.urllib.error.URLError("down"))
        )
        assert SlackDelivery("https://hooks.slack.com/services/x").send(_dispatch()) is False


class TestEmailDelivery:
    def _mailer(self, **overrides):
        base = {
            "host": "smtp.example.com",
            "port": 587,
            "user": "u",
            "password": "p",
            "sender": "from@example.com",
            "recipients": ["to@example.com"],
        }
        return EmailDelivery(**{**base, **overrides})

    def test_sends_via_smtp_with_the_title_as_the_subject(self, monkeypatch):
        smtp = MagicMock()
        smtp.__enter__ = lambda s: s
        smtp.__exit__ = lambda s, *e: False
        smtp.has_extn.return_value = True
        monkeypatch.setattr(delivery.smtplib, "SMTP", MagicMock(return_value=smtp))
        assert self._mailer().send(_dispatch()) is True
        msg = smtp.send_message.call_args[0][0]
        assert msg["Subject"] == "Daily Standup — 2026-07-10"
        assert "the whole standup" in msg.get_content()

    def test_no_recipients_is_a_handled_failure(self):
        assert self._mailer(recipients=[]).send(_dispatch()) is False

    def test_smtp_error_returns_false(self, monkeypatch):
        monkeypatch.setattr(delivery.smtplib, "SMTP", MagicMock(side_effect=OSError("refused")))
        assert self._mailer().send(_dispatch()) is False


class TestFactoryAndFanOut:
    def test_get_delivery_terminal(self):
        assert isinstance(get_delivery("terminal"), TerminalDelivery)

    def test_get_delivery_unknown_returns_none(self):
        assert get_delivery("carrier-pigeon") is None

    def test_deliver_fans_out_and_reports_partial(self, monkeypatch):
        # terminal succeeds, slack fails (no webhook) → partial.
        monkeypatch.setattr("yeaboi.config.get_slack_webhook_url", lambda: "", raising=False)
        results = deliver(_dispatch(), ["terminal", "slack"])
        assert results["terminal"] is True
        assert results["slack"] is False

    def test_deliver_channel_crash_isolated(self, monkeypatch):
        boom = MagicMock()
        boom.send.side_effect = RuntimeError("kaboom")
        monkeypatch.setattr(delivery, "get_delivery", lambda ch: boom if ch == "slack" else TerminalDelivery())
        results = deliver(_dispatch(), ["terminal", "slack"])
        assert results["terminal"] is True
        assert results["slack"] is False


class TestTheShimStillWorks:
    """The CLI, the TUI and the MCP tools import these by the old path."""

    def test_names_the_old_module_exported_are_still_there(self):
        from yeaboi.standup import delivery as shim

        assert shim.ALL_CHANNELS == delivery.ALL_CHANNELS
        assert shim.notify_desktop is delivery.notify_desktop
        assert shim.deliver is delivery.deliver

    @pytest.mark.parametrize("channel", ["terminal", "desktop", "slack", "email"])
    def test_every_advertised_channel_can_be_built(self, channel):
        assert get_delivery(channel) is not None
