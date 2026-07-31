"""The one seam between the Vite build and every page Python serves or writes.

Bundles are built from ``frontend/`` (``make web``) and **committed** to
``static/`` so ``pip install yeaboi`` never needs Node. Nothing here builds
anything; it reads the committed output and inlines it.

Everything a page needs arrives as one self-contained document:

* the stylesheet inlined in a ``<style>``,
* the favicon inlined as a ``data:`` URI,
* server data as a JSON island in a non-executing ``<script type="application/json">``,
* the bundle inlined in a classic (non-module) ``<script>``.

All three forms are forced by constraints rather than taste — exported pages are
opened over ``file://`` where a ``type="module"`` script does not execute at all,
and tunnel-served pages run under a CSP with no ``eval`` and no external origins
(``sharing/server.py``).
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Committed build output. Not a user path, so it does NOT come from paths.py —
# this is package data that ships inside the wheel.
STATIC_DIR = Path(__file__).parent / "static"

# The tab icon, 32x32, written by scripts/gen_duck_sprites.py. It sits beside
# this module rather than in static/, which holds Vite output only.
FAVICON_PATH = Path(__file__).parent / "favicon.png"

# Bundles are named by their Vite entry (see frontend/entries.mjs). The pattern
# is a hard gate rather than decoration: read_asset takes a caller-supplied
# name, so anything path-like ("../../.env", an absolute path, a symlink hop)
# must be impossible to express before it reaches the filesystem.
_ASSET_NAME = re.compile(r"^[a-z][a-z0-9_-]*\.(js|css)$")


@lru_cache(maxsize=16)
def read_asset(filename: str) -> str:
    """Return the text of a built bundle from ``static/``.

    Cached — these are immutable build artifacts, read once per process. That
    also means ``make web`` output is not picked up by an already-running
    process; restart it (``make web-dev`` serves from Vite instead).

    Raises:
        ValueError: ``filename`` is not a plain ``<name>.js``/``<name>.css``.
        FileNotFoundError: the bundle is missing — almost always "you have not
            run ``make web``", so the message says exactly that.
    """
    if not _ASSET_NAME.match(filename):
        raise ValueError(f"not a valid bundle name: {filename!r}")

    path = STATIC_DIR / filename
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.error("web asset missing: %s", path)
        raise FileNotFoundError(
            f"missing built asset {filename!r} at {path}.\n"
            "The front-end bundles are committed — if yours are absent, rebuild them with:\n"
            "    make web"
        ) from None
    logger.debug("web asset loaded: %s (%d bytes)", filename, len(text))
    return text


@lru_cache(maxsize=1)
def _favicon_data_uri() -> str:
    """Return the tab icon as a ``data:`` URI, or ``""`` if it cannot be read.

    A ``data:`` URI rather than a ``/favicon.ico`` route because half these
    documents are files: an export opens over ``file://`` with no server to ask.
    It needs no CSP change either — a favicon request is governed by ``img-src``,
    and every policy in ``web.security`` already allows ``data:``.

    Deliberately *not* ``html_theme.image_data_uri``: that one is best-effort
    embedding of arbitrary user files under ``~/.yeaboi`` (mimetype guessing, a
    5 MB cap, no caching), and ``html_theme`` imports this module, so depending
    back on it would be a cycle. This is one known packaged PNG, read once.

    Tolerant by design. A missing icon is a missing decoration; it must never be
    the reason an export fails to write.
    """
    try:
        raw = FAVICON_PATH.read_bytes()
    except OSError as exc:
        logger.warning("favicon unavailable at %s (%s) — pages will have no tab icon", FAVICON_PATH, exc)
        return ""
    return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"


def json_island(value: object) -> str:
    """JSON-encode ``value`` for embedding as the text content of a ``<script>``.

    ``json.dumps`` leaves ``<``, ``>`` and ``&`` literal. Inside a ``<script>``
    element the HTML tokenizer is in script-data state, where ``</script``,
    ``<!--`` and ``<script`` all change parsing — so an untrusted card title or
    ticket summary could otherwise close the element early and have the
    remainder parsed as markup. Escaping those three characters to their
    ``\\uXXXX`` forms defeats all three at once and stays valid JSON.

    U+2028/U+2029 are escaped too. They are legal in JSON but were historically
    illegal raw in JavaScript string literals; escaping costs nothing and
    removes a whole class of parser-difference surprises.
    """
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    return (
        encoded.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_page(
    *,
    bundle: str,
    title: str,
    data: Mapping[str, object] | None = None,
    body: str = "",
    head: str = "",
    html_attrs: str = "",
    root_id: str = "root",
    lang: str = "en",
) -> str:
    """Render one self-contained document around a built bundle.

    Args:
        bundle: Vite entry name — ``static/<bundle>.css`` and ``<bundle>.js``.
        title: Document ``<title>``. Escaped here.
        data: Boot payload for the client, emitted as ``#yeaboi-data``. Read
            client-side via ``textContent`` + ``JSON.parse``, never by evaluating.
        body: Markup placed inside the root element — a server-rendered shell or
            ``<noscript>`` fallback. **Trusted**: every caller-supplied value in
            it must already be escaped.
        head: Extra ``<head>`` markup (meta tags). Trusted, same rule as ``body``.
            Emitted after the favicon link, so a caller can override the icon.
        html_attrs: Attributes for the ``<html>`` element, e.g. ``data-mode="retro"``.
        root_id: Element id the bundle mounts into.
        lang: ``<html lang>``.

    Never put a secret in ``data`` — the whole document is served token-free at
    ``GET /`` and is also what gets written to disk by the exporters.
    """
    # html.escape directly rather than html_theme.escape: html_theme imports
    # read_asset from this module, so depending back on it would be a cycle.
    escape = html.escape

    css = read_asset(f"{bundle}.css")
    js = read_asset(f"{bundle}.js")

    island = ""
    if data is not None:
        # type="application/json" is not executable: the browser parses it as
        # data, so the payload can never run even if the escaping above failed.
        island = f'<script type="application/json" id="yeaboi-data">{json_island(data)}</script>'

    attrs = f" {html_attrs}" if html_attrs else ""

    icon = _favicon_data_uri()
    icon_link = f'\n  <link rel="icon" type="image/png" href="{icon}">' if icon else ""

    return f"""<!DOCTYPE html>
<html lang="{escape(lang)}"{attrs}>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escape(title)}</title>{icon_link}
  <style>{css}</style>
  {head}
</head>
<body>
<div id="{escape(root_id)}">{body}</div>
{island}
<script>{js}</script>
</body>
</html>"""
