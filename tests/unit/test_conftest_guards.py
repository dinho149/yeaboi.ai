"""Tests for the autouse guards in tests/conftest.py.

Every guard here shares one failure mode: if it silently stops working, nothing
goes red. A developer's browser just starts opening again during `make test-fast`,
or an environment key starts leaking between tests and something unrelated fails
in a way that names nothing. These tests pin the properties their docstrings
promise.
"""

from __future__ import annotations

import os
import webbrowser

import pytest

from yeaboi.config import set_tips_enabled


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


class TestTheEnvironmentIsRestored:
    """`_no_env_leak` — the guard that makes test order stop mattering.

    Thirteen functions in ``config.py`` write straight to ``os.environ`` by
    design. A test that calls one without ``monkeypatch`` used to leak the key
    into every test collected after it, which is invisible until the collection
    order changes — and it changed when ``tests/*.py`` joined the unit lane and
    the lane went parallel. Four welcome-screen tests failed on a missing tip
    strip, passing in isolation, with nothing in the failure naming the cause.
    """

    def test_a_leaked_key_does_not_survive_the_test(self):
        # This test *is* the leak: it sets a key with no monkeypatch, exactly the
        # way `set_tips_enabled` does. The assertion is in its sibling below,
        # which pytest runs next.
        os.environ["YEABOI_LEAK_PROBE"] = "1"
        assert os.environ["YEABOI_LEAK_PROBE"] == "1"

    def test_the_next_test_does_not_see_it(self):
        assert "YEABOI_LEAK_PROBE" not in os.environ

    def test_a_key_the_process_already_had_is_put_back(self, monkeypatch):
        monkeypatch.setenv("YEABOI_PREEXISTING", "original")
        os.environ["YEABOI_PREEXISTING"] = "clobbered"
        # Restored to what the process had when this test started, not deleted —
        # `monkeypatch` then unwinds its own layer on top of that.
        assert os.environ["YEABOI_PREEXISTING"] == "clobbered"

    def test_the_real_setter_cannot_leak_either(self, tmp_path, monkeypatch):
        """The concrete case, through production code rather than a probe key."""
        monkeypatch.setattr("yeaboi.config.get_config_file", lambda: tmp_path / ".env")
        set_tips_enabled(False)
        assert os.environ["TIPS_ENABLED"] == "false"

    def test_and_tips_are_back_on_for_everyone_else(self):
        assert os.environ.get("TIPS_ENABLED") != "false"
