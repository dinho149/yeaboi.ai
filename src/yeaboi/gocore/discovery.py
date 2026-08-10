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
    3. ``yeaboi-core`` on PATH, **never** from the working directory.
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

    found = shutil.which("yeaboi-core")
    if found is None:
        return None
    # shutil.which is NOT a PATH-only lookup on Windows: CPython prepends
    # os.curdir (unless NoDefaultCurrentDirectoryInExePath is set) and then
    # tries every PATHEXT suffix, so a planted .\yeaboi-core.exe outranks
    # anything on PATH. Discovery is automatic — auto is the default state of
    # YEABOI_GO, the first engine call spawns with no prompt, and a failure is
    # silent by design — so a cwd hit would be an unprompted execution of an
    # attacker-plantable file. Rejected on every platform (a POSIX PATH with
    # "." on it is the same hazard); YEABOI_CORE_BIN stays the explicit
    # opt-in for anyone who really means the local binary.
    try:
        in_cwd = Path(found).resolve().parent == Path.cwd().resolve()
    except OSError:  # deleted or unreachable cwd — treat as untrusted
        in_cwd = True
    if in_cwd:
        logger.warning("gocore: ignoring a yeaboi-core in the working directory — set YEABOI_CORE_BIN to use it")
        return None
    return found
