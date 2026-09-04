"""The network edge (src/yeaboi/news/fetch.py): the guard on a user's URL."""

from __future__ import annotations

import urllib.request

import pytest

from yeaboi.connectors.http import UnsafeUrlError
from yeaboi.news import fetch as module
from yeaboi.news.fetch import Conditional, fetch_one
from yeaboi.news.sources import NewsSource

PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]


class _Response:
    headers = {}

    def __init__(self, body: bytes = b"<rss/>"):
        self.body = body

    def read(self, n: int) -> bytes:
        return self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestGuard:
    def test_a_users_private_url_never_reaches_the_opener(self, monkeypatch):
        opened = []
        monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: opened.append(handlers))
        result = fetch_one(NewsSource(id="c", url="https://127.0.0.1/feed", builtin=False), Conditional())
        assert opened == []
        assert result.status == 0 and result.error.startswith("UnsafeUrlError")

    def test_a_users_public_url_goes_through_the_guarded_opener(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: PUBLIC)
        seen = {}

        class Opener:
            def open(self, req, timeout):
                seen["url"] = req.full_url
                return _Response()

        def build_opener(*handlers):
            seen["handlers"] = handlers
            return Opener()

        monkeypatch.setattr(urllib.request, "build_opener", build_opener)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("plain urlopen used"))
        result = fetch_one(NewsSource(id="c", url="https://lobste.rs/rss", builtin=False), Conditional())
        assert result.status == 200 and result.body == b"<rss/>"
        assert seen["url"] == "https://lobste.rs/rss" and seen["handlers"] == (module._GuardedRedirect,)

    def test_a_builtin_source_uses_plain_urlopen_without_the_guard(self, monkeypatch):
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Response(b"<rss>x</rss>"))
        monkeypatch.setattr(urllib.request, "build_opener", lambda *a: pytest.fail("guarded opener used"))
        monkeypatch.setattr("yeaboi.connectors.http.assert_safe_url", lambda url: pytest.fail("guard called"))
        result = fetch_one(NewsSource(id="b", url="https://a.example/f"), Conditional())
        assert result.status == 200 and result.body == b"<rss>x</rss>"


class TestGuardedRedirect:
    def _redirect(self, target: str):
        handler = module._GuardedRedirect()
        req = urllib.request.Request("https://lobste.rs/rss")
        return handler.redirect_request(req, None, 302, "Found", {}, target)

    @pytest.mark.parametrize("target", ["http://lobste.rs/rss", "https://127.0.0.1/rss", "https://169.254.169.254/"])
    def test_refuses_a_private_or_http_hop(self, target):
        with pytest.raises(UnsafeUrlError):
            self._redirect(target)

    def test_passes_a_public_https_hop(self, monkeypatch):
        monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: PUBLIC)
        followed = self._redirect("https://lobste.rs/rss.xml")
        assert followed is not None and followed.full_url == "https://lobste.rs/rss.xml"
