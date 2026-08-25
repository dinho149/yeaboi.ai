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


class TestTheBundleOverrideIsDropped:
    """`$YEABOI_WEB_STATIC` must not reach the suite from a developer's shell.

    `web/assets.py` resolves the bundle directory once, at import, so this is
    the one piece of environment isolation a fixture cannot do — by the time
    any fixture runs, the choice is made. Anyone who exports it to serve a Vite
    `dist/` would otherwise have the whole suite assert against bundles that
    are not the committed ones, passing or failing for a reason nothing in the
    output names.
    """

    def test_the_suite_reads_the_committed_bundles(self):
        from yeaboi.web.assets import STATIC_SOURCE

        assert STATIC_SOURCE == "tree"

    def test_conftest_drops_it_at_import_rather_than_in_a_fixture(self):
        """A static check, because the one above passes for free on a machine
        that never set the variable — which is every CI runner."""
        import ast
        from pathlib import Path

        conftest = Path(__file__).resolve().parents[1] / "conftest.py"
        module = ast.parse(conftest.read_text(encoding="utf-8"))
        popped = [
            node.value.args[0].value
            for node in module.body  # module scope only: inside a fixture is too late
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and ast.unparse(node.value.func).endswith("environ.pop")
            and isinstance(node.value.args[0], ast.Constant)
        ]
        assert "YEABOI_WEB_STATIC" in popped
