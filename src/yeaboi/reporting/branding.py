"""The yeaboi duck, for the .pptx export.

One small pixel-art duck (``assets/duck.png``, shipped as package data) placed on
the PowerPoint title and thank-you slides. Loading is best-effort and cached: a
missing or unreadable asset returns None and the export renders without
branding — cosmetics must never break an export.

There used to be a ``duck_data_uri()`` beside it, which base64'd this same PNG
into the HTML slide deck's footer. The deck is a React bundle now and takes the
duck from ``frontend/src/assets/duck`` — the quantised 128px sprites the live
boards use, ~7 KB for all three layers against ~59 KB for one base64 copy of
this one, and animated rather than flat. python-pptx needs real bytes, so this
half stays.
"""

from __future__ import annotations

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
