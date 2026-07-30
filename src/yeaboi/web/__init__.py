"""Built front-end assets and the Python seam that inlines them into pages.

The TypeScript sources live in ``frontend/``; ``make web`` compiles them into
``static/``, which is committed so installing yeaboi never requires Node.
"""

from yeaboi.web.assets import STATIC_DIR, json_island, read_asset, render_page

__all__ = ["STATIC_DIR", "json_island", "read_asset", "render_page"]
