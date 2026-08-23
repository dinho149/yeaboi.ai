"""The two-way Slack lane: what yeaboi posted, and what a team said back.

Slack has been write-only since it existed here, and structurally so — an
incoming webhook answers a POST with the literal body ``ok`` and no message id,
so yeaboi could never identify its own message and a reaction on it was
unreadable by construction. This package is the other direction.

One rule holds the whole design together:

    **The anchor row is the argument list of the function the event will
    eventually call.** Slack text never identifies a target and never selects
    an action; identity is looked up, never parsed.

That is what lets a reaction or a reply become a practice verdict, a paused
ceremony or an attributed correction with **no LLM anywhere in the inbound
path**, with no id in the message body for anyone to spoof, and with the
roster mapping off the critical path entirely.
"""

from yeaboi.slack.store import SlackAnchor, SlackStore, record_post

__all__ = ["SlackAnchor", "SlackStore", "record_post"]
