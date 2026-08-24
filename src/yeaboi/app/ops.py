"""Long-running operations: an id the client can cancel.

Engines expose cancellation as an in-process ``threading.Event``
(``cancel_event`` — one of the parity registry's always-hidden injection
seams). Over the wire that becomes: the client includes an ``op_id`` with the
request that starts the work, and ``POST /api/ops/{op_id}/cancel`` sets the
event. Progress callbacks publish to the event bus under the same ``op_id``,
so the shell can join the two ends.

The table is bookkeeping, not a scheduler: whoever starts the work registers
the operation, runs it however it likes, and removes it when done. Finished
entries left behind by a crashed caller are swept by ``prune``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Entries older than this are eligible for pruning (a defensive backstop —
#: well-behaved callers remove their own operations).
PRUNE_AFTER_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class Operation:
    """One cancellable unit of work."""

    op_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    created: float = field(default_factory=time.time)


class OperationTable:
    """Registry of in-flight operations. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ops: dict[str, Operation] = {}

    def create(self, op_id: str | None = None) -> Operation:
        """Register a new operation (minting an id when none is supplied).

        A caller-supplied id that is already registered is refused — silently
        sharing a cancel event between two unrelated calls would cancel both.
        """
        op = Operation(op_id=op_id or uuid.uuid4().hex)
        with self._lock:
            if op.op_id in self._ops:
                raise ValueError(f"operation id already in flight: {op.op_id}")
            self._ops[op.op_id] = op
        logger.info("operation registered: %s", op.op_id)
        return op

    def get(self, op_id: str) -> Operation | None:
        with self._lock:
            return self._ops.get(op_id)

    def cancel(self, op_id: str) -> bool:
        """Set the operation's cancel event. False when the id is unknown."""
        op = self.get(op_id)
        if op is None:
            logger.warning("cancel for unknown operation: %s", op_id)
            return False
        op.cancel.set()
        logger.info("operation cancelled: %s", op_id)
        return True

    def remove(self, op_id: str) -> None:
        with self._lock:
            self._ops.pop(op_id, None)

    def prune(self, *, max_age_seconds: float = PRUNE_AFTER_SECONDS) -> int:
        """Drop entries older than ``max_age_seconds``; returns how many."""
        cutoff = time.time() - max_age_seconds
        with self._lock:
            stale = [op_id for op_id, op in self._ops.items() if op.created < cutoff]
            for op_id in stale:
                del self._ops[op_id]
        if stale:
            logger.warning("pruned %d stale operation(s)", len(stale))
        return len(stale)

    def __len__(self) -> int:
        with self._lock:
            return len(self._ops)
