"""Delivery channels — terminal, desktop, Slack, email.

All channels are stdlib-only (no new dependencies): Slack posts to an incoming
webhook via urllib, email uses smtplib, desktop shells out to osascript
(macOS) / notify-send (Linux). Each channel's send() logs and returns a bool;
deliver() fans out across channels and never lets one failure block the others —
partial delivery is reported, not raised.

Every channel takes a :class:`~yeaboi.agent.state.Dispatch` rather than a
report. This module used to live in ``standup/`` and be typed on
``StandupReport``, and that typing was load-bearing in the wrong direction: the
agent standup could reuse none of it and hand-rolled its own webhook POST
instead, with a comment explaining why. A title, a one-line summary and a
plaintext body is everything all four channels ever read.
``standup/delivery.py`` remains as a shim.

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
from collections.abc import Callable
from email.message import EmailMessage

from yeaboi.agent.state import Dispatch, MessageRef

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

    #: Where the last successful send landed, when the channel has a durable
    #: address for it. Only Slack-with-a-bot-token sets this; a webhook, an
    #: SMTP message and a desktop banner have nothing to point back at.
    receipt: MessageRef | None = None

    @abstractmethod
    def send(self, dispatch: Dispatch) -> bool:
        """Deliver it. Return True on success, False on handled failure."""
        raise NotImplementedError


class TerminalDelivery(NotificationDelivery):
    """Print to stdout (baseline channel, needs no config).

    Plain text rather than each mode's Rich renderer: this channel exists so a
    scheduled run has somewhere to land when nothing else is configured, and
    that run has no terminal attached to be pretty in. Reading a report
    properly is what opening the mode is for.
    """

    name = CHANNEL_TERMINAL

    def send(self, dispatch: Dispatch) -> bool:
        from rich.console import Console

        logger.info("delivery[terminal]: printing %s", dispatch.title or "dispatch")
        console = Console()
        if dispatch.title:
            console.print(dispatch.title, style="bold")
        console.print(dispatch.body or dispatch.summary)
        return True


def notify_desktop(title: str, body: str) -> bool:
    """Post one native desktop notification. No ``Dispatch`` required.

    Split out of :class:`DesktopDelivery` so a caller with something to say that
    is not a delivery — the transcript reminder — can say it without the ABC,
    the channel registry or a fabricated payload standing in for one.
    """
    body = (body or "")[:200]
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


class DesktopDelivery(NotificationDelivery):
    """Post a native desktop notification (macOS osascript / Linux notify-send)."""

    name = CHANNEL_DESKTOP

    def send(self, dispatch: Dispatch) -> bool:
        return notify_desktop(dispatch.title or "yeaboi", dispatch.summary or dispatch.body)


class SlackDelivery(NotificationDelivery):
    """Post to Slack — as a bot when two-way is configured, else to the webhook.

    The two transports are not interchangeable and the fallback is one-way on
    purpose. ``chat.postMessage`` returns the ``(channel, ts)`` that makes a
    message answerable, which is the only reason the token path exists; but it
    also changes the **visible sender** — a webhook posts as its configured app,
    a bot posts as the bot user and must first be invited to the channel. So
    the token path is taken only when a token *and* a channel are both set
    (:func:`config.slack_two_way_ready`), and a failed ``chat.postMessage``
    falls back to the webhook rather than failing the delivery. That message is
    simply not answerable; the standup still lands.
    """

    name = CHANNEL_SLACK

    def __init__(self, webhook_url: str, *, bot_token: str = "", channel: str = ""):
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.channel = channel
        self.receipt: MessageRef | None = None

    def send(self, dispatch: Dispatch) -> bool:
        self.receipt = None
        text = dispatch.body or dispatch.summary
        if self.bot_token and self.channel and self._send_as_bot(text, dispatch.title):
            return True
        if not self.webhook_url:
            logger.warning("delivery[slack] skipped — no SLACK_WEBHOOK_URL configured")
            return False
        import json

        payload = json.dumps({"text": text}).encode("utf-8")
        # self.webhook_url is the user's own configured https Slack webhook.
        req = urllib.request.Request(self.webhook_url, data=payload, headers={"Content-Type": "application/json"})  # noqa: S310
        logger.info("delivery[slack]: POSTing %s", dispatch.title or "dispatch")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 — user-provided webhook URL
                ok = 200 <= resp.status < 300
                if not ok:
                    logger.error("delivery[slack] non-2xx: %s", resp.status)
                return ok
        except (urllib.error.URLError, OSError) as e:
            logger.error("delivery[slack] failed: %s", e)
            return False

    def _send_as_bot(self, text: str, title: str) -> bool:
        """Post via chat.postMessage and keep the receipt. False falls back."""
        from yeaboi.tools import slack as slack_api

        logger.info("delivery[slack]: posting %s as a bot", title or "dispatch")
        resp = slack_api.post_message(self.channel, text, token=self.bot_token)
        if not resp.ok:
            # Not an error for the *delivery* — the webhook is about to try.
            logger.warning("delivery[slack]: bot post failed (%s), falling back to the webhook", resp.error)
            return False
        self.receipt = MessageRef(
            kind=CHANNEL_SLACK,
            channel=str(resp.data.get("channel", self.channel)),
            ts=str(resp.data.get("ts", "")),
        )
        return True


class EmailDelivery(NotificationDelivery):
    """Send via SMTP."""

    name = CHANNEL_EMAIL

    def __init__(self, *, host: str, port: int, user: str, password: str, sender: str, recipients: list[str]):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender or user
        self.recipients = recipients

    def send(self, dispatch: Dispatch) -> bool:
        if not (self.host and self.recipients):
            logger.warning("delivery[email] skipped — SMTP host or recipients not configured")
            return False
        msg = EmailMessage()
        msg["Subject"] = dispatch.subject or dispatch.title or "yeaboi"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(dispatch.body or dispatch.summary)
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
        return SlackDelivery(
            webhook_url=_safe(config, "get_slack_webhook_url"),
            bot_token=_safe(config, "get_slack_bot_token"),
            channel=_safe(config, "get_slack_channel_id"),
        )
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


def deliver(
    dispatch: Dispatch,
    channels: list[str],
    *,
    on_receipt: Callable[[str, MessageRef], None] | None = None,
) -> dict[str, bool]:
    """Send ``dispatch`` to each channel; return {channel: success}. Never raises.

    ``on_receipt`` fires once per channel that came back with a durable address
    — today only Slack posting as a bot. It is a keyword-only callback rather
    than a widened return type because ``dict[str, bool]`` is load-bearing in
    three places outside this module: the standup store JSON-encodes it into a
    ``delivery_status`` column that history and exports read, agentwatch
    returns it straight out through an MCP tool's schema, and
    ``CeremonyRun.delivery`` is typed on it. Widening would cost a store
    migration, a tool-schema change and a state-field change to give three
    channels a field that is permanently empty.
    """
    logger.info("deliver: channels=%s", channels)
    results: dict[str, bool] = {}
    for channel in channels:
        handler = get_delivery(channel)
        if handler is None:
            results[channel] = False
            continue
        try:
            results[channel] = handler.send(dispatch)
        except Exception as e:  # defensive — a channel should never crash the run
            logger.error("deliver: channel %s raised: %s", channel, e)
            results[channel] = False
            continue
        ref = getattr(handler, "receipt", None)
        if results[channel] and ref is not None and on_receipt is not None:
            try:
                on_receipt(channel, ref)
            except Exception:  # noqa: BLE001 — recording must not fail a delivery
                logger.warning("deliver: on_receipt for %s raised", channel, exc_info=True)
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
