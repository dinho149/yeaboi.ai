"""Email delivery for a completed 1:1 summary.

Reuses the Daily Standup SMTP wiring: the same ``config.get_smtp_*`` getters and
the same STARTTLS/login/send flow as ``standup/delivery.py:EmailDelivery`` — no new
config surface and no new dependency (stdlib ``smtplib`` only). We build the
1:1-specific ``EmailMessage`` here (the standup EmailDelivery formats a
StandupReport), then hand it to the shared SMTP send.

Best-effort: a missing SMTP host / recipients returns False (the engine surfaces
that as a "not sent" warning); a transport error is logged and returns False. It
never raises.

# See docs: "Daily Standup" — delivery
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from yeaboi.agent.state import OneOnOneRecord
from yeaboi.performance.render import format_completion_lines

logger = logging.getLogger(__name__)


def _smtp_config() -> dict:
    """Pull SMTP settings from config (shared with the standup email channel)."""
    from yeaboi import config

    return {
        "host": config.get_smtp_host(),
        "port": config.get_smtp_port(),
        "user": config.get_smtp_user(),
        "password": config.get_smtp_password(),
        "sender": config.get_smtp_sender(),
        # Default recipients reuse the standup recipient list; callers may override.
        "recipients": config.get_standup_email_recipients(),
    }


def send_completion_email(record: OneOnOneRecord, *, recipients: list[str] | None = None) -> bool:
    """Send the 1:1 summary email via SMTP. Return True on success, False otherwise.

    Args:
        record: the completed 1:1 whose email_summary is the body.
        recipients: override recipient list; falls back to STANDUP_EMAIL_RECIPIENTS.
    """
    cfg = _smtp_config()
    to = recipients or cfg["recipients"]
    if not (cfg["host"] and to):
        logger.warning("performance[email] skipped — SMTP host or recipients not configured")
        return False

    msg = EmailMessage()
    msg["Subject"] = record.email_subject or f"1:1 follow-up — {record.date}"
    msg["From"] = cfg["sender"] or cfg["user"]
    msg["To"] = ", ".join(to)
    # Prefer the AI-written email body; fall back to the full rendered record.
    body = record.email_summary or "\n".join(format_completion_lines(record))
    msg.set_content(body)

    logger.info(
        "performance[email]: sending 1:1 summary for %s to %d recipient(s) via %s:%d",
        record.engineer,
        len(to),
        cfg["host"],
        cfg["port"],
    )
    try:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as smtp:
            smtp.ehlo()
            if smtp.has_extn("STARTTLS"):
                smtp.starttls()
                smtp.ehlo()
            if cfg["user"] and cfg["password"]:
                smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        logger.error("performance[email] failed: %s", e)
        return False
