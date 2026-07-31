"""Built front-end assets and the Python seam that inlines them into pages.

The TypeScript sources live in ``frontend/``; ``make web`` compiles them into
``static/``, which is committed so installing yeaboi never requires Node.

Three modules, one per direction of that seam, and each is the *only* place its
concern is spelled: :mod:`~yeaboi.web.assets` reads the bundles and builds the
document, :mod:`~yeaboi.web.brand` builds the masthead payload every surface
wears, and :mod:`~yeaboi.web.security` owns the headers and policies a served
document carries. A surface that re-implements any of them is the drift this
package exists to prevent.
"""

from yeaboi.web.assets import STATIC_DIR, json_island, read_asset, render_page
from yeaboi.web.brand import DEFAULT_FOOTER, accent_mode, build_chrome, frame_title
from yeaboi.web.security import DOCUMENT_HEADERS, send_document

__all__ = [
    "DEFAULT_FOOTER",
    "DOCUMENT_HEADERS",
    "STATIC_DIR",
    "accent_mode",
    "build_chrome",
    "frame_title",
    "json_island",
    "read_asset",
    "render_page",
    "send_document",
]
