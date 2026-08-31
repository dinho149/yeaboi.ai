"""The inbound half of a webhook-kind custom connection.

One loopback receiver (``server.py``), started by ``yeaboi webhooks serve`` or
the desktop backend, POSTed to by the user's services, persisting into
:mod:`yeaboi.connectors.webhook_store` — from which the ordinary gather reads.
"""

from yeaboi.connectors.webhooks.server import (
    DEFAULT_PORT,
    receiver_port,
    server_status,
    start_server,
    stop_server,
)

__all__ = ["DEFAULT_PORT", "receiver_port", "server_status", "start_server", "stop_server"]
