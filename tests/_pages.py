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

``assert_self_contained`` is the third: the claim that a document reaches for
nothing. A dozen tests used to spell it ``assert "<link" not in html``, which
stopped being true the day every page gained an inlined favicon — so the rule
lives in one place that knows which single ``<link>`` is legal.
"""

from __future__ import annotations

import json
import re

# The byline every surface wears, and the one external URL a bundle may
# contain. Mirrors ``CREDIT_URL`` in ``frontend/src/shared/Credit.tsx``.
#
# Tests that assert "no external origin appears anywhere" blank a *single*
# occurrence of this before they scan, rather than widening their pattern to
# allow the host: a link is a place to go rather than something the page loads,
# so it changes nothing about a document opened over ``file://`` with no network
# — and blanking one occurrence means a second one, which is what an ``<img
# src>`` or a real fetch would look like, still fails.
CREDIT_URL = "https://yeaboi.ai"

_ISLAND_RE = re.compile(r'<script type="application/json" id="yeaboi-data">(.*?)</script>', re.S)
_ASSET_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.S)
_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
# Constructs that make the browser go and get something as the page loads. This
# is deliberately about *retrieval*, not about the substring "http": a board's
# boot payload legitimately carries the radio stream URLs, and a report carries
# ticket links — neither is fetched when the document opens, and `media-src
# https:` in web/security.py is the policy that says so.
_FETCHES = (
    re.compile(r"<script\b[^>]*\bsrc\s*=", re.I),
    re.compile(r"""\bsrc\s*=\s*['"]?https?:""", re.I),
    re.compile(r"""url\(\s*['"]?https?:""", re.I),
    re.compile(r"@import", re.I),
)


def assert_self_contained(html: str) -> None:
    """Assert the document fetches nothing as it loads.

    Exactly one ``<link>`` is permitted: the inlined ``data:`` favicon. It is a
    link element rather than inline content because there is no other way to
    give a browser a tab icon, and a ``data:`` href retrieves nothing.

    This is the property that breaks silently. It holds on localhost and on a
    LAN no matter what, and fails only for the teammate on the tunnel (strict
    CSP) or the person who opens an export over ``file://``.
    """
    for pattern in _FETCHES:
        match = pattern.search(html)
        assert match is None, f"document fetches at load: {html[match.start() : match.start() + 60]!r}"
    for link in _LINK_RE.findall(html):
        assert 'rel="icon"' in link and 'href="data:' in link, f"non-inline <link>: {link}"


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
