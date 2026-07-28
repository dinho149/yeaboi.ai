"""yeaboi.ai branding assets for the exported presentations.

One small pixel-art duck (``assets/duck.png``, shipped as package data) rendered
as a subtle mark on the HTML deck's footer badge and the .pptx title/thank-you
slides. Loading is best-effort and cached: a missing or unreadable asset returns
None and the export renders without branding — cosmetics must never break an
export.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from importlib import resources

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def duck_png() -> bytes | None:
    """The duck PNG bytes, or None when the packaged asset is unavailable."""
    try:
        data = (resources.files("yeaboi.reporting") / "assets" / "duck.png").read_bytes()
    except Exception:  # noqa: BLE001 — branding is cosmetic, never raise
        logger.debug("reporting branding: duck asset unavailable", exc_info=True)
        return None
    return data or None


@lru_cache(maxsize=1)
def duck_data_uri() -> str | None:
    """The duck as a ``data:image/png;base64`` URI for the self-contained deck."""
    data = duck_png()
    if data is None:
        return None
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")
