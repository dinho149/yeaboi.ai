"""Python client for the Go core sidecar (``yeaboi-core``).

The first step of the strangler-fig migration: engines ask this package for a
client; when the ``YEABOI_GO`` flag is on AND a compatible binary is found AND
the handshake succeeds, they get one and can route work over ndjson JSON-RPC
(contracts/v1/rpc.md). Anything else — flag off, no binary, version mismatch,
crash mid-call — yields ``None`` or a ``CoreError``, and the caller runs the
existing Python implementation. The Go path must never be the only path.
"""

from yeaboi.gocore.client import CoreClient, CoreError, get_client, is_enabled
from yeaboi.gocore.discovery import find_core_binary

__all__ = ["CoreClient", "CoreError", "find_core_binary", "get_client", "is_enabled"]
