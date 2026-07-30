"""Looking at a rendered page from a test.

Every page yeaboi serves or writes is one self-contained document: an inlined
stylesheet, an inlined bundle, and — on a React surface — the server's data as a
``<script type="application/json" id="yeaboi-data">`` island. That shape breaks
two habits tests used to rely on, and these two helpers are the answers.

``island`` is for React surfaces: assert against the payload, because the markup
is built by a bundle the Python suite deliberately does not run.

``markup`` is for the string-templated exports that are still to migrate. Their
tests assert things like "no image was embedded", which used to be safe against
the whole document because the only script was 900 bytes. The same pages now
inline a 45 KB bundle carrying the duck sprites as data URIs, so a bare
substring check reads the bundle and reports whatever it happens to contain.
"""

from __future__ import annotations

import json
import re

_ISLAND_RE = re.compile(r'<script type="application/json" id="yeaboi-data">(.*?)</script>', re.S)
_ASSET_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S)


def island(html: str) -> dict:
    """Return the parsed boot payload. Fails loudly if it is absent or malformed."""
    match = _ISLAND_RE.search(html)
    assert match is not None, "no boot island in the page"
    return json.loads(match.group(1))


def markup(html: str) -> str:
    """Return the document with every ``<script>`` and ``<style>`` block removed.

    What is left is the markup the exporter itself wrote — which is what a test
    asserting on a report's *content* means, and the only part of the page it
    can meaningfully make a negative claim about.
    """
    return _ASSET_RE.sub("", html)
