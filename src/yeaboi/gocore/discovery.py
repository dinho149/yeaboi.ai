"""Locate the ``yeaboi-core`` binary. Absence is normal, never an error."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def find_core_binary() -> str | None:
    """Resolve the sidecar binary, most-explicit source first.

    1. ``YEABOI_CORE_BIN`` — explicit path (dev workflow: ``make go-build``
       then ``YEABOI_CORE_BIN=bin/yeaboi-core make run``).
    2. The ``yeaboi_core`` platform wheel, when installed (``yeaboi[core]``).
    3. ``yeaboi-core`` on PATH.
    4. ``None`` — the caller falls back to the Python implementation.
    """
    explicit = os.environ.get("YEABOI_CORE_BIN", "").strip()
    if explicit:
        path = Path(explicit)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        logger.warning("gocore: YEABOI_CORE_BIN=%r is not an executable file — ignoring", explicit)

    try:
        # The optional platform wheel ships the binary as package data.
        from yeaboi_core import binary_path  # type: ignore[import-not-found]

        bundled = str(binary_path())
        if Path(bundled).is_file():
            return bundled
    except Exception:  # noqa: BLE001 — the extra simply isn't installed
        pass

    return shutil.which("yeaboi-core")
