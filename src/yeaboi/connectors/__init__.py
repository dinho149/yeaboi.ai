"""Read-only integrations, declared as data.

One :class:`~yeaboi.connectors.spec.Connector` descriptor per vendor is the
single source of truth every surface derives from — the settings fields, the
verify table, the secret lists, the catalog. Adding a vendor is a descriptor
plus a verify function, not an edit in nine registries.
"""

from yeaboi.connectors.registry import all_connectors, by_key, connected, connection_kinds, secret_envs
from yeaboi.connectors.spec import Connector, ConnectorField

__all__ = [
    "Connector",
    "ConnectorField",
    "all_connectors",
    "by_key",
    "connected",
    "connection_kinds",
    "secret_envs",
]
