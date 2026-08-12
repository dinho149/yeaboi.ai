"""yeaboi-core — platform-wheel home of the yeaboi Go sidecar binary.

The only API is :func:`binary_path`; ``yeaboi.gocore.discovery`` imports it
(inside a try/except, so this package stays optional) as resolution step 2,
between ``YEABOI_CORE_BIN`` and PATH lookup.
"""

from __future__ import annotations

import sys
from importlib import resources

__all__ = ["binary_path"]


def binary_path() -> str:
    """Absolute path of the bundled ``yeaboi-core`` binary for this platform."""
    name = "yeaboi-core.exe" if sys.platform == "win32" else "yeaboi-core"
    return str(resources.files("yeaboi_core") / "bin" / name)
