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


class TestNotifyDesktop:
    """The report-free notification path, for callers that have something to say
    that is not a StandupReport (the transcript reminder)."""

    def test_posts_a_notification_without_a_report(self, monkeypatch):
        run = MagicMock()
        monkeypatch.setattr(delivery.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(delivery.subprocess, "run", run)
        assert delivery.notify_desktop("Standup transcript", "5 standups unchecked") is True
        assert run.call_args[0][0][0] == "osascript"

    def test_the_argv_injection_guard_moved_with_it(self, monkeypatch):
        """`on run argv` is a security control, not a style choice — it has to
        still hold on the path the reminder uses."""
        evil = 'pwned" & (do shell script "touch /tmp/x") & "\\`end'
        monkeypatch.setattr(delivery.platform, "system", lambda: "Darwin")
        run = MagicMock()
        monkeypatch.setattr(delivery.subprocess, "run", run)
        assert delivery.notify_desktop("Title", evil) is True
        argv = run.call_args[0][0]
        script = argv[2]
        assert "on run argv" in script
        assert evil not in script  # never interpolated into the AppleScript source
        assert evil in argv  # delivered as data

    def test_body_is_clipped(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Linux")
        run = MagicMock()
        monkeypatch.setattr(delivery.subprocess, "run", run)
        delivery.notify_desktop("T", "x" * 500)
        assert len(run.call_args[0][0][2]) == 200

    def test_unsupported_platform_returns_false(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Windows")
        assert delivery.notify_desktop("T", "B") is False

    def test_a_missing_helper_never_raises(self, monkeypatch):
        monkeypatch.setattr(delivery.platform, "system", lambda: "Linux")
        monkeypatch.setattr(delivery.subprocess, "run", MagicMock(side_effect=FileNotFoundError))
        assert delivery.notify_desktop("T", "B") is False


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


class TestDayOverDayRender:
    def _report(self):
        return StandupReport(
            date="2026-07-10",
            confidence_pct=74,
            confidence_label="At risk",
            confidence_delta=-8,
            confidence_trend="declining",
            member_updates=(
                MemberUpdate(
                    name="Alice",
                    summary="login page",
                    progress_note="Still on PSOT-9 from the last standup.",
                    outlook="Likely to finish PSOT-9 today.",
                ),
            ),
        )

    def test_plaintext_progress_and_outlook_lines(self):
        text = render.format_standup_plaintext(self._report())
        assert "↺ Since last standup: Still on PSOT-9 from the last standup." in text
        assert "→ Outlook: Likely to finish PSOT-9 today." in text

    def test_plaintext_confidence_trend_fragment(self):
        assert "▼ 8 vs last" in render.format_standup_plaintext(self._report())

    def test_improving_fragment(self):
        rep = StandupReport(
            date="2026-07-10",
            confidence_pct=90,
            confidence_label="On track",
            confidence_delta=6,
            confidence_trend="improving",
        )
        assert "▲ +6 vs last" in render.format_standup_plaintext(rep)

    def test_steady_or_no_history_no_fragment(self):
        for trend in ("steady", ""):
            rep = StandupReport(
                date="2026-07-10",
                confidence_pct=90,
                confidence_label="On track",
                confidence_delta=1,
                confidence_trend=trend,
            )
            assert "vs last" not in render.format_standup_plaintext(rep)

    def test_empty_fields_render_no_lines(self):
        text = render.format_standup_plaintext(_report())
        assert "Since last standup" not in text
        assert "Outlook" not in text

    def test_rich_includes_progress_and_outlook(self):
        from rich.console import Console

        console = Console(width=120, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(render.format_standup_rich(self._report()))
        out = cap.get()
        assert "Since last standup" in out
        assert "Outlook" in out


class TestRenderPractices:
    def _report_with_practices(self, **signal_over):
        from yeaboi.agent.state import PracticeSignal

        base = dict(
            rule="untracked-work",
            title="Untracked work",
            detail="#91 carries no ticket reference in the branch, title, or description.",
            evidence=(("#91", "https://x/pull/91"),),
        )
        base.update(signal_over)
        return StandupReport(
            date="2026-07-10",
            member_updates=(MemberUpdate(name="Alice", summary="login page", practices=(PracticeSignal(**base),)),),
            practice_rollup=(("untracked-work", 2),),
        )

    def test_plaintext_shows_the_signal_after_the_blocker(self):
        text = render.format_standup_plaintext(self._report_with_practices())
        assert "◇ Untracked work: #91 carries no ticket reference" in text

    def test_plaintext_shows_the_team_rollup(self):
        text = render.format_standup_plaintext(self._report_with_practices())
        assert "Practices: Untracked work ×2" in text

    def test_plaintext_marks_a_repeat(self):
        text = render.format_standup_plaintext(self._report_with_practices(repeat=True))
        assert "Untracked work (again today):" in text

    def test_a_report_with_no_practices_says_nothing(self):
        text = render.format_standup_plaintext(_report())
        assert "◇" not in text
        assert "Practices:" not in text

    def test_rich_renders_them_too(self):
        from rich.console import Console

        console = Console(width=100, file=open("/dev/null", "w"))
        with console.capture() as cap:
            console.print(render.format_standup_rich(self._report_with_practices()))
        assert "Untracked work" in cap.get()

    def test_a_legacy_report_without_the_field_still_renders(self):
        # Reports serialized before this feature deserialize with practices=(),
        # but a hand-built object may not have the attribute at all.
        legacy = StandupReport(date="2026-07-10", member_updates=(MemberUpdate(name="Alice", summary="x"),))
        assert "Alice" in render.format_standup_plaintext(legacy)
