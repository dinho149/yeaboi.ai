"""Tests for the autouse guards in tests/conftest.py.

The browser guard's failure mode is invisible: if it silently stops working, nothing
goes red — a developer's browser just starts opening again during `make test-fast`.
These tests pin the two properties its docstring promises.
"""

from __future__ import annotations

import webbrowser

import pytest


class TestNoRealBrowser:
    def test_an_unpatched_open_is_blocked(self):
        with pytest.raises(BaseException, match="real browser") as excinfo:
            webbrowser.open("https://example.com")
        # Must not be an Exception: every production call site wraps its
        # webbrowser.open in `except Exception` and degrades to a copy-this-URL
        # branch, which would swallow the guard and reroute the test instead.
        assert not isinstance(excinfo.value, Exception)

    def test_the_production_except_exception_does_not_swallow_it(self):
        """Mirrors the wrapper at all three call sites (gap_issues, feedback, mode_select)."""
        with pytest.raises(BaseException, match="real browser"):
            try:
                webbrowser.open("https://example.com")
            except Exception:  # noqa: BLE001 - reproducing production shape on purpose
                pytest.fail("except Exception swallowed the browser guard")

    @pytest.mark.parametrize("name", ["open", "open_new", "open_new_tab", "get"])
    def test_every_entry_point_is_covered(self, name):
        with pytest.raises(BaseException, match="real browser"):
            getattr(webbrowser, name)("https://example.com")

    def test_a_tests_own_patch_still_wins(self, monkeypatch):
        """The guard shares the monkeypatch instance, so a test's setattr lands after it."""
        opened = []
        monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url) or True)
        assert webbrowser.open("https://example.com") is True
        assert opened == ["https://example.com"]
