"""Python client for the Go core sidecar (``yeaboi-core``).

The first step of the strangler-fig migration: engines ask this package for a
client; when a compatible binary is found (installed via ``yeaboi[core]``, on
PATH, or named by ``YEABOI_CORE_BIN``) and the handshake succeeds, they get one
and can route work over ndjson JSON-RPC (contracts/v1/rpc.md). Discovery is
automatic unless ``YEABOI_GO`` says otherwise (``0`` = off, truthy = forced on
with a log line when the binary is missing). Anything else — no binary, version
mismatch, crash mid-call — yields ``None`` or a ``CoreError``, and the caller
runs the existing Python implementation. The Go path must never be the only
path.
"""

from yeaboi.gocore.client import CoreClient, CoreError, enabled_state, get_client
from yeaboi.gocore.discovery import find_core_binary

__all__ = ["CoreClient", "CoreError", "enabled_state", "find_core_binary", "get_client"]
