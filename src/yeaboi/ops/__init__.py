"""The ops data plane: what production did, as bounded counts.

Two types at two altitudes, mirroring the ``raw dict → SupportingSignal`` split
that already exists elsewhere in the tree. :class:`~yeaboi.ops.events.OpsEvent`
is what a connector's fetcher returns — one thing, at one moment, with no field
capable of holding a body. :class:`~yeaboi.ops.signals.OpsSignal` is the
rolled-up form a mode renders.

Nothing here reads a credential or makes a request: fetching lives on the
connector, so every call inherits ``connectors.http``'s SSRF guard.
"""

from yeaboi.ops.events import EVENT_KINDS, SEVERITIES, OpsEvent, parse_window
from yeaboi.ops.signals import OpsSignal, roll_up

__all__ = [
    "EVENT_KINDS",
    "SEVERITIES",
    "OpsEvent",
    "OpsSignal",
    "parse_window",
    "roll_up",
]
