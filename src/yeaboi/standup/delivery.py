"""Standup delivery — a shim over :mod:`yeaboi.ceremonies.delivery`.

The channels used to live here and to be typed on ``StandupReport``, which is
why nothing but the standup could use them. They now take a mode-neutral
``Dispatch`` and serve every ceremony, so they moved; this module stays because
the CLI, the TUI and the MCP tools address ``ALL_CHANNELS`` and
``notify_desktop`` by this name.

Import from :mod:`yeaboi.ceremonies.delivery` for new code.
"""

from __future__ import annotations

from yeaboi.ceremonies.delivery import (
    ALL_CHANNELS,
    CHANNEL_DESKTOP,
    CHANNEL_EMAIL,
    CHANNEL_SLACK,
    CHANNEL_TERMINAL,
    DesktopDelivery,
    EmailDelivery,
    NotificationDelivery,
    SlackDelivery,
    TerminalDelivery,
    deliver,
    get_delivery,
    notify_desktop,
)

__all__ = [
    "ALL_CHANNELS",
    "CHANNEL_DESKTOP",
    "CHANNEL_EMAIL",
    "CHANNEL_SLACK",
    "CHANNEL_TERMINAL",
    "DesktopDelivery",
    "EmailDelivery",
    "NotificationDelivery",
    "SlackDelivery",
    "TerminalDelivery",
    "deliver",
    "get_delivery",
    "notify_desktop",
]
