"""Tests for the Vite-build → Python seam (``yeaboi.web.assets``).

Two jobs here. The first is ordinary unit coverage of ``read_asset`` /
``json_island`` / ``render_page``. The second is a set of standing guards over
the *committed build output*, because that output is produced by a toolchain
``make test`` never runs — nothing else in the Python suite would notice if a
bundle started reaching for a CDN, calling ``eval``, or being emitted as an ES
module. Those failures are invisible on localhost and on a LAN, and only break
for the remote teammate on the tunnel or the person who opens an export over
``file://``, so they have to be caught statically.
"""

from __future__ import annotations

import json
import re

import pytest

from yeaboi.web.assets import STATIC_DIR, json_island, read_asset, render_page

# Every Vite entry that exists today. Grows one row per phase of the React
# migration; the parametrized guards below then cover the new bundle for free.
BUNDLES = ("deck", "export", "gate", "poker", "retro")


class TestReadAsset:
    def test_reads_a_built_bundle(self):
        css = read_asset("export.css")
        assert "[data-theme=" in css
        assert len(css) > 1000

    def test_missing_bundle_names_the_fix(self):
        with pytest.raises(FileNotFoundError, match="make web"):
            read_asset("nosuchbundle.js")

    @pytest.mark.parametrize(
        "name",
        [
            "../../../etc/passwd",
            "../pyproject.toml",
            "/etc/passwd",
            "export.py",  # right shape, wrong extension
            "Export.css",  # uppercase — not an entry name
            "",
            "export.css/../../secrets.css",
        ],
    )
    def test_rejects_anything_path_like(self, name):
        with pytest.raises(ValueError, match="not a valid bundle name"):
            read_asset(name)

    def test_result_is_cached(self):
        # Same object identity => the file was read once, not per call.
        assert read_asset("export.css") is read_asset("export.css")


class TestJsonIsland:
    def test_round_trips(self):
        value = {"grids": ["went_well"], "n": 3, "ok": True, "none": None}
        assert json.loads(json_island(value)) == value

    def test_escapes_script_breakout(self):
        # The attack this exists to stop: a card title that closes the element.
        payload = json_island({"text": "</script><img src=x onerror=alert(1)>"})
        assert "</script" not in payload
        assert "<" not in payload and ">" not in payload
        assert json.loads(payload)["text"] == "</script><img src=x onerror=alert(1)>"

    def test_escapes_comment_and_nested_script_openers(self):
        # `<!--` and `<script` also switch the tokenizer's script-data state.
        payload = json_island({"a": "<!--", "b": "<script>"})
        assert "<!--" not in payload and "<script" not in payload
        assert json.loads(payload) == {"a": "<!--", "b": "<script>"}

    def test_escapes_ampersand(self):
        assert "&" not in json_island({"x": "a&b"})

    def test_escapes_line_separators(self):
        payload = json_island({"x": "a\u2028b\u2029c"})
        assert "\u2028" not in payload and "\u2029" not in payload
        assert json.loads(payload)["x"] == "a\u2028b\u2029c"

    def test_non_serializable_falls_back_to_str(self):
        class Thing:
            def __str__(self) -> str:
                return "thing"

        assert json.loads(json_island({"t": Thing()}))["t"] == "thing"

    def test_unicode_is_not_ascii_escaped(self):
        # ensure_ascii=False keeps the payload small — these pages carry the
        # whole snapshot inline, and emoji/accents are everywhere in card text.
        assert "é" in json_island({"name": "Zoé"})


class TestRenderPage:
    def test_inlines_css_and_js(self):
        page = render_page(bundle="export", title="T")
        assert f"<style>{read_asset('export.css')}</style>" in page
        assert f"<script>{read_asset('export.js')}</script>" in page

    def test_escapes_the_title(self):
        page = render_page(bundle="export", title="</title><script>alert(1)</script>")
        assert "<script>alert(1)</script>" not in page
        assert "&lt;/title&gt;" in page

    def test_no_data_means_no_island(self):
        assert 'id="yeaboi-data"' not in render_page(bundle="export", title="T")

    def test_island_is_non_executable_json(self):
        page = render_page(bundle="export", title="T", data={"a": 1})
        assert '<script type="application/json" id="yeaboi-data">{"a": 1}</script>' in page

    def test_island_data_cannot_break_out(self):
        page = render_page(bundle="export", title="T", data={"t": "</script><b>x</b>"})
        # Exactly two script elements: the island and the bundle. A breakout
        # would add a third opener.
        assert page.count("<script") == 2
        assert page.count("</script>") == 2

    def test_root_id_and_html_attrs(self):
        page = render_page(bundle="export", title="T", root_id="app", html_attrs='data-mode="retro"')
        assert '<div id="app">' in page
        assert '<html lang="en" data-mode="retro">' in page

    def test_body_and_extra_css_land(self):
        page = render_page(bundle="export", title="T", body="<p>hi</p>", extra_css=".x{color:red}")
        assert "<p>hi</p>" in page
        assert "<style>.x{color:red}</style>" in page

    def test_head_extras_land_in_head(self):
        page = render_page(bundle="export", title="T", head='<meta name="robots" content="noindex">')
        assert page.index('name="robots"') < page.index("<body>")

    def test_unknown_bundle_raises(self):
        with pytest.raises(FileNotFoundError, match="make web"):
            render_page(bundle="nope", title="T")


class TestBundlesAreShippable:
    """Standing guards over the committed Vite output.

    These assert on build *artifacts*, so they fail when someone changes the
    Vite config or adds a dependency that violates a deployment constraint —
    the one class of regression the rest of the Python suite is blind to.
    """

    @pytest.mark.parametrize("bundle", BUNDLES)
    def test_both_files_are_committed(self, bundle):
        for ext in ("js", "css"):
            path = STATIC_DIR / f"{bundle}.{ext}"
            assert path.is_file(), f"{path} missing — run `make web`"
            assert path.stat().st_size > 0

    @pytest.mark.parametrize("bundle", BUNDLES)
    def test_bundle_is_eval_free(self, bundle):
        """The tunnel CSP has no 'unsafe-eval' (sharing/server.py).

        A dependency that reaches for `eval` or `new Function` works perfectly
        in dev and on a LAN and breaks only for the remote teammate — exactly
        the failure nobody notices before shipping.
        """
        js = read_asset(f"{bundle}.js")
        for forbidden in ("eval(", "new Function(", "setTimeout('", 'setTimeout("'):
            assert forbidden not in js, f"{bundle}.js contains {forbidden!r} — blocked by the tunnel CSP"

    @pytest.mark.parametrize("bundle", BUNDLES)
    def test_bundle_fetches_nothing(self, bundle):
        """No CDN, no import(), no XHR to another origin — pages must be self-contained.

        Exports are opened over file:// where any external request fails, and
        the tunnel CSP allows no external origins at all.
        """
        js = read_asset(f"{bundle}.js")
        assert not re.search(r"https?://(?!www\.w3\.org)", js), "bundle references an external URL"
        assert "import(" not in js, "dynamic import would emit a second file"
        assert "importScripts" not in js

    @pytest.mark.parametrize("bundle", BUNDLES)
    def test_bundle_is_a_classic_script_not_a_module(self, bundle):
        """IIFE, not ESM.

        A `type="module"` script does not execute at all over file://, so an
        exported report opened from disk would silently lose its theme toggle.
        Top-level `import`/`export` is the signature of the wrong output format.
        """
        js = read_asset(f"{bundle}.js").lstrip()
        assert js.startswith("(function") or js.startswith("(()=>") or js.startswith("!function")
        assert not re.search(r"^\s*(import|export)\s", js, re.M)

    @pytest.mark.parametrize("bundle", BUNDLES)
    def test_stylesheet_has_no_external_references(self, bundle):
        css = read_asset(f"{bundle}.css")
        assert "@import" not in css
        assert not re.search(r"url\(\s*['\"]?https?:", css)

    def test_static_dir_holds_only_bundles(self):
        """Vite writes into a directory that ships in the wheel — nothing else may.

        A stray index.html or .map here would be committed and packaged.
        """
        unexpected = [p.name for p in STATIC_DIR.iterdir() if p.suffix not in {".js", ".css"}]
        assert unexpected == [], f"unexpected files in web/static: {unexpected}"

    def test_every_bundle_on_disk_is_declared(self):
        """Two-way check — adding an entry without listing it here skips its guards."""
        on_disk = {p.stem for p in STATIC_DIR.glob("*.js")}
        assert on_disk == set(BUNDLES)
