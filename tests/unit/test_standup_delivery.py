"""Unit tests for standup delivery channels and rendering (stdlib mocks)."""

from unittest.mock import MagicMock

from rich.console import Group

from yeaboi.agent.state import MemberUpdate, StandupReport
from yeaboi.standup import delivery, render
from yeaboi.standup.delivery import (
    DesktopDelivery,
    EmailDelivery,
    SlackDelivery,
    TerminalDelivery,
    deliver,
    get_delivery,
)


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


class TestRender:
    def test_plaintext_contains_key_fields(self):
        text = render.format_standup_plaintext(_report())
        assert "Daily Standup — 2026-07-10" in text
        assert "day 3 of 10" in text
        assert "At risk" in text
        assert "Alice" in text
        assert "General overview: login page" in text
        assert "Ticketing:" in text
        assert "Code:" in text
        assert "Documentation:" in text
        assert "Blocker: waiting on review" in text

    def test_rich_returns_group(self):
        assert isinstance(render.format_standup_rich(_report()), Group)

    def test_rich_includes_notices(self):
        from rich.console import Console

        rep = StandupReport(date="2026-07-10", warnings=("Jira: authentication failed",))
        console = Console(width=90, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(render.format_standup_rich(rep))
        out = cap.get()
        assert "Notices" in out
        assert "Jira: authentication failed" in out

    def test_lines_handles_empty_report(self):
        lines = render.format_standup_lines(StandupReport(date="2026-07-10"))
        assert any("No individual updates" in ln for ln in lines)

    def test_warnings_appear_as_notices(self):
        rep = StandupReport(date="2026-07-10", warnings=("Jira: authentication failed",))
        text = render.format_standup_plaintext(rep)
        assert "Notices" in text
        assert "Jira: authentication failed" in text

    def test_member_links_render_on_both_surfaces(self):
        rep = StandupReport(
            date="2026-07-10",
            member_updates=(
                MemberUpdate(name="Alice", summary="moved a ticket", links=(("PSOT-1", "https://j/browse/PSOT-1"),)),
            ),
        )
        # Plaintext: raw URL so Slack/email clients auto-link it.
        text = render.format_standup_plaintext(rep)
        assert "🔗 PSOT-1: https://j/browse/PSOT-1" in text
        # Rich: label rendered (OSC-8 hyperlink carries the URL invisibly).
        from rich.console import Console

        console = Console(width=90, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(render.format_standup_rich(rep))
        assert "↗ PSOT-1" in cap.get()


class TestTerminalDelivery:
    def test_prints_and_succeeds(self, capsys):
        assert TerminalDelivery().send(_report()) is True


class TestDesktopDelivery:
    def test_macos_uses_osascript(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Darwin")
        run = MagicMock()
        monkeypatch.setattr(delivery.subprocess, "run", run)
        assert DesktopDelivery().send(_report()) is True
        assert run.call_args[0][0][0] == "osascript"

    def test_macos_passes_text_as_argv_not_interpolated(self, monkeypatch):
        # LLM-generated summary containing AppleScript-breaking metacharacters.
        import dataclasses

        evil = 'pwned" & (do shell script "touch /tmp/x") & "\\`end'
        report = dataclasses.replace(_report(), team_summary=evil)
        monkeypatch.setattr(delivery.platform, "system", lambda: "Darwin")
        run = MagicMock()
        monkeypatch.setattr(delivery.subprocess, "run", run)
        assert DesktopDelivery().send(report) is True
        argv = run.call_args[0][0]
        # The static script uses `on run argv` and must NOT contain the untrusted text.
        assert argv[0] == "osascript"
        script = argv[2]
        assert "on run argv" in script
        assert evil not in script  # never interpolated into the AppleScript source
        # The body is delivered verbatim as a runtime argument (data, not code).
        assert evil in argv

    def test_linux_uses_notify_send(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Linux")
        run = MagicMock()
        monkeypatch.setattr(delivery.subprocess, "run", run)
        assert DesktopDelivery().send(_report()) is True
        assert run.call_args[0][0][0] == "notify-send"

    def test_unsupported_platform_returns_false(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Windows")
        assert DesktopDelivery().send(_report()) is False

    def test_missing_binary_returns_false(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Linux")
        monkeypatch.setattr(delivery.subprocess, "run", MagicMock(side_effect=FileNotFoundError()))
        assert DesktopDelivery().send(_report()) is False


class TestSlackDelivery:
    def test_no_webhook_returns_false(self):
        assert SlackDelivery("").send(_report()) is False

    def test_posts_payload(self, monkeypatch):
        captured = {}

        class FakeResp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            captured["url"] = req.full_url
            captured["data"] = req.data
            return FakeResp()

        monkeypatch.setattr(delivery.urllib.request, "urlopen", fake_urlopen)
        assert SlackDelivery("https://hooks.slack.com/x").send(_report()) is True
        assert b"Daily Standup" in captured["data"]

    def test_network_error_returns_false(self, monkeypatch):
        def boom(req, timeout=0):
            raise delivery.urllib.error.URLError("down")

        monkeypatch.setattr(delivery.urllib.request, "urlopen", boom)
        assert SlackDelivery("https://hooks.slack.com/x").send(_report()) is False


class TestEmailDelivery:
    def _handler(self, **over):
        base = dict(
            host="smtp.example.com",
            port=587,
            user="u@example.com",
            password="pw",
            sender="u@example.com",
            recipients=["team@example.com"],
        )
        base.update(over)
        return EmailDelivery(**base)

    def test_missing_host_returns_false(self):
        assert self._handler(host="").send(_report()) is False

    def test_missing_recipients_returns_false(self):
        assert self._handler(recipients=[]).send(_report()) is False

    def test_sends_via_smtp(self, monkeypatch):
        smtp = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=smtp)
        ctx.__exit__ = MagicMock(return_value=False)
        smtp.has_extn.return_value = True
        monkeypatch.setattr(delivery.smtplib, "SMTP", MagicMock(return_value=ctx))
        assert self._handler().send(_report()) is True
        assert smtp.send_message.called
        assert smtp.starttls.called

    def test_smtp_error_returns_false(self, monkeypatch):
        monkeypatch.setattr(delivery.smtplib, "SMTP", MagicMock(side_effect=OSError("refused")))
        assert self._handler().send(_report()) is False


class TestFactoryAndFanOut:
    def test_get_delivery_terminal(self):
        assert isinstance(get_delivery("terminal"), TerminalDelivery)

    def test_get_delivery_unknown_returns_none(self):
        assert get_delivery("carrier-pigeon") is None

    def test_deliver_fans_out_and_reports_partial(self, monkeypatch):
        # terminal succeeds, slack fails (no webhook) → partial.
        monkeypatch.setattr("yeaboi.config.get_slack_webhook_url", lambda: "", raising=False)
        results = deliver(_report(), ["terminal", "slack"])
        assert results["terminal"] is True
        assert results["slack"] is False

    def test_deliver_channel_crash_isolated(self, monkeypatch):
        boom = MagicMock()
        boom.send.side_effect = RuntimeError("kaboom")
        monkeypatch.setattr(delivery, "get_delivery", lambda ch: boom if ch == "slack" else TerminalDelivery())
        results = deliver(_report(), ["terminal", "slack"])
        assert results["terminal"] is True
        assert results["slack"] is False
