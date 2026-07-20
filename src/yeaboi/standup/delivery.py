"""Delivery channels for a StandupReport — terminal, desktop, Slack, email.

All channels are stdlib-only (no new dependencies): Slack posts to an incoming
webhook via urllib, email uses smtplib, desktop shells out to osascript
(macOS) / notify-send (Linux). Each channel's send() logs and returns a bool;
deliver() fans out across channels and never lets one failure block the others —
partial delivery is reported, not raised.

# See docs: "Daily Standup" — delivery
"""

from __future__ import annotations

import logging
import platform
import smtplib
import subprocess
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from email.message import EmailMessage

from yeaboi.agent.state import StandupReport
from yeaboi.standup.render import format_standup_plaintext

logger = logging.getLogger(__name__)

# Canonical channel identifiers.
CHANNEL_TERMINAL = "terminal"
CHANNEL_DESKTOP = "desktop"
CHANNEL_SLACK = "slack"
CHANNEL_EMAIL = "email"

ALL_CHANNELS = (CHANNEL_TERMINAL, CHANNEL_DESKTOP, CHANNEL_SLACK, CHANNEL_EMAIL)


class NotificationDelivery(ABC):
    """Base class for a single delivery channel."""

    name: str = ""

    @abstractmethod
    def send(self, report: StandupReport) -> bool:
        """Deliver the report. Return True on success, False on handled failure."""
        raise NotImplementedError


class TerminalDelivery(NotificationDelivery):
    """Print the standup to stdout (baseline channel, needs no config)."""

    name = CHANNEL_TERMINAL

    def send(self, report: StandupReport) -> bool:
        from rich.console import Console

        from yeaboi.standup.render import format_standup_rich

        logger.info("delivery[terminal]: printing standup for %s", report.date)
        Console().print(format_standup_rich(report))
        return True


class DesktopDelivery(NotificationDelivery):
    """Post a native desktop notification (macOS osascript / Linux notify-send)."""

    name = CHANNEL_DESKTOP

    def send(self, report: StandupReport) -> bool:
        title = f"Daily Standup — {report.confidence_label or report.date}"
        # One-line body: confidence + team summary head.
        body = report.team_summary or report.confidence_rationale or "Standup ready."
        body = body[:200]
        system = platform.system()
        logger.info("delivery[desktop]: system=%s", system)
        try:
            if system == "Darwin":
                # SECURITY: body/title are LLM-generated (from Jira/git/transcript data), so they
                # must never be interpolated into the AppleScript source — a crafted string could
                # break out of the quoted literal and AppleScript can `do shell script`. Instead we
                # pass them as runtime arguments via `on run argv`; AppleScript treats argv items as
                # opaque data, never code, so no escaping is needed and injection is impossible.
                script = "on run argv\n  display notification (item 1 of argv) with title (item 2 of argv)\nend run"
                subprocess.run(
                    ["osascript", "-e", script, body, title],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
            elif system == "Linux":
                subprocess.run(["notify-send", title, body], check=True, capture_output=True, timeout=10)
            else:
                logger.warning("delivery[desktop]: unsupported platform %s", system)
                return False
            return True
        except (FileNotFoundError, subprocess.SubprocessError) as e:
            logger.error("delivery[desktop] failed: %s", e)
            return False


class SlackDelivery(NotificationDelivery):
    """Post the standup to a Slack incoming webhook."""

    name = CHANNEL_SLACK

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    def send(self, report: StandupReport) -> bool:
        if not self.webhook_url:
            logger.warning("delivery[slack] skipped — no SLACK_WEBHOOK_URL configured")
            return False
        import json

        text = format_standup_plaintext(report)
        payload = json.dumps({"text": text}).encode("utf-8")
        # self.webhook_url is the user's own configured https Slack webhook.
        req = urllib.request.Request(self.webhook_url, data=payload, headers={"Content-Type": "application/json"})  # noqa: S310
        logger.info("delivery[slack]: POSTing standup for %s", report.date)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — user-provided webhook URL
                ok = 200 <= resp.status < 300
                if not ok:
                    logger.error("delivery[slack] non-2xx: %s", resp.status)
                return ok
        except (urllib.error.URLError, OSError) as e:
            logger.error("delivery[slack] failed: %s", e)
            return False


class EmailDelivery(NotificationDelivery):
    """Send the standup via SMTP."""

    name = CHANNEL_EMAIL

    def __init__(self, *, host: str, port: int, user: str, password: str, sender: str, recipients: list[str]):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender or user
        self.recipients = recipients

    def send(self, report: StandupReport) -> bool:
        if not (self.host and self.recipients):
            logger.warning("delivery[email] skipped — SMTP host or recipients not configured")
            return False
        msg = EmailMessage()
        msg["Subject"] = f"Daily Standup — {report.date} ({report.confidence_label})"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(format_standup_plaintext(report))
        logger.info("delivery[email]: sending to %d recipient(s) via %s:%d", len(self.recipients), self.host, self.port)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
                smtp.ehlo()
                if smtp.has_extn("STARTTLS"):
                    smtp.starttls()
                    smtp.ehlo()
                if self.user and self.password:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)
            return True
        except (smtplib.SMTPException, OSError) as e:
            logger.error("delivery[email] failed: %s", e)
            return False


def get_delivery(channel: str) -> NotificationDelivery | None:
    """Build a delivery instance for ``channel``, pulling any secrets from config.

    Returns None for an unknown channel. Channels with missing credentials still
    build (and report the missing-config failure at send() time) so the run is
    recorded consistently.
    """
    from yeaboi import config

    if channel == CHANNEL_TERMINAL:
        return TerminalDelivery()
    if channel == CHANNEL_DESKTOP:
        return DesktopDelivery()
    if channel == CHANNEL_SLACK:
        return SlackDelivery(webhook_url=_safe(config, "get_slack_webhook_url"))
    if channel == CHANNEL_EMAIL:
        return EmailDelivery(
            host=_safe(config, "get_smtp_host"),
            port=_safe_int(config, "get_smtp_port", 587),
            user=_safe(config, "get_smtp_user"),
            password=_safe(config, "get_smtp_password"),
            sender=_safe(config, "get_smtp_sender"),
            recipients=_safe_list(config, "get_standup_email_recipients"),
        )
    logger.warning("get_delivery: unknown channel %r", channel)
    return None


def deliver(report: StandupReport, channels: list[str]) -> dict[str, bool]:
    """Send ``report`` to each channel; return {channel: success}. Never raises."""
    logger.info("deliver: channels=%s", channels)
    results: dict[str, bool] = {}
    for channel in channels:
        handler = get_delivery(channel)
        if handler is None:
            results[channel] = False
            continue
        try:
            results[channel] = handler.send(report)
        except Exception as e:  # defensive — a channel should never crash the run
            logger.error("deliver: channel %s raised: %s", channel, e)
            results[channel] = False
    logger.info("deliver complete: %s", results)
    return results


# ── config accessors (tolerant of getters not existing yet) ────────────────


def _safe(config_mod, getter: str) -> str:
    fn = getattr(config_mod, getter, None)
    try:
        return (fn() if fn else "") or ""
    except Exception:
        return ""


def _safe_int(config_mod, getter: str, default: int) -> int:
    fn = getattr(config_mod, getter, None)
    try:
        val = fn() if fn else None
        return int(val) if val else default
    except Exception:
        return default


def _safe_list(config_mod, getter: str) -> list[str]:
    fn = getattr(config_mod, getter, None)
    try:
        val = fn() if fn else None
        return list(val) if val else []
    except Exception:
        return []
