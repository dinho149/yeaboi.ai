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

import base64
import json
import re

import pytest

from tests._pages import CREDIT_URL
from yeaboi.web.assets import (
    ASSETS_PACKAGE,
    FAVICON_PATH,
    STATIC_DIR,
    STATIC_ENV,
    json_island,
    read_asset,
    render_page,
)

# Every Vite entry that exists today. Grows one row per phase of the React
# migration; the parametrized guards below then cover the new bundle for free.
BUNDLES = ("deck", "export", "gate", "poker", "retro", "ship")


@pytest.fixture
def installed_assets(tmp_path, monkeypatch):
    """A real, importable ``yeaboi_web_assets`` for the duration of one test.

    A genuine package on ``sys.path`` rather than a stubbed ``importlib``,
    because the arm being tested is dormant until ``frontend/`` becomes its own
    repo — and a mock of the resolution would only prove the mock resolves.
    """
    import importlib
    import sys

    site = tmp_path / "site-packages"
    static = site / ASSETS_PACKAGE / "static"
    static.mkdir(parents=True)
    (site / ASSETS_PACKAGE / "__init__.py").write_text("", encoding="utf-8")
    (static / "export.css").write_text("/* shipped by the package */", encoding="utf-8")

    monkeypatch.syspath_prepend(site)
    importlib.invalidate_caches()
    try:
        yield static
    finally:
        sys.modules.pop(ASSETS_PACKAGE, None)
        importlib.invalidate_caches()


@pytest.fixture(autouse=True)
def _no_inherited_override(monkeypatch):
    """A developer running `make web-dev` must not change what the suite tests."""
    monkeypatch.delenv(STATIC_ENV, raising=False)


class TestStaticDirResolution:
    """Where the bundles come from — the seam the frontend split turns on.

    Three sources, and the two that do not exist yet are the ones worth
    testing: the in-tree copy is what every other test in this file already
    exercises, while ``$YEABOI_WEB_STATIC`` and an installed
    ``yeaboi_web_assets`` are code paths nothing else reaches.
    """

    def test_in_tree_is_the_fallback(self):
        from yeaboi.web.assets import _static_dir

        path, source = _static_dir()
        assert source == "tree"
        assert path.name == "static" and path.parent.name == "web"

    def test_the_env_override_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv(STATIC_ENV, str(tmp_path))
        from yeaboi.web.assets import _static_dir

        assert _static_dir() == (tmp_path, "env")

    def test_a_wrong_override_raises_instead_of_falling_through(self, tmp_path, monkeypatch):
        """Serving different bundles from the ones you built, silently, is worse.

        A typo here would otherwise resolve to the in-tree copy and look like
        the front end simply is not rebuilding.
        """
        monkeypatch.setenv(STATIC_ENV, str(tmp_path / "typo"))
        from yeaboi.web.assets import _static_dir

        with pytest.raises(NotADirectoryError, match=STATIC_ENV):
            _static_dir()

    def test_an_installed_package_beats_the_in_tree_copy(self, installed_assets):
        from yeaboi.web.assets import _static_dir

        assert _static_dir() == (installed_assets, "package")

    def test_the_override_beats_an_installed_package(self, installed_assets, tmp_path, monkeypatch):
        """The dev loop has to win, or `make web-dev` stops working the day
        `yeaboi-web-assets` becomes a dependency."""
        monkeypatch.setenv(STATIC_ENV, str(tmp_path))
        from yeaboi.web.assets import _static_dir

        assert _static_dir() == (tmp_path, "env")

    def test_a_package_without_a_static_dir_falls_through(self, installed_assets):
        """An installed-but-empty package must not shadow working bundles."""
        import shutil

        from yeaboi.web.assets import _static_dir

        shutil.rmtree(installed_assets)
        assert _static_dir()[1] == "tree"

    def test_the_missing_bundle_hint_matches_where_they_come_from(self, monkeypatch):
        """ "Run make web" is a lie once the bundles ship in another package."""
        import yeaboi.web.assets as assets_mod

        monkeypatch.setattr(assets_mod, "STATIC_SOURCE", "package")
        hint = assets_mod._rebuild_hint()
        assert ASSETS_PACKAGE in hint and "make web" not in hint


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

    def test_body_lands(self):
        page = render_page(bundle="export", title="T", body="<p>hi</p>")
        assert "<p>hi</p>" in page

    def test_head_extras_land_in_head(self):
        page = render_page(bundle="export", title="T", head='<meta name="robots" content="noindex">')
        assert page.index('name="robots"') < page.index("<body>")

    def test_head_extras_come_after_the_favicon(self):
        """So a caller can override the icon by emitting its own link."""
        page = render_page(bundle="export", title="T", head='<link rel="icon" href="data:image/png;base64,AA">')
        assert page.rindex('rel="icon"') > page.index('rel="icon"')
        assert page.rindex('rel="icon"') < page.index("<body>")

    def test_unknown_bundle_raises(self):
        with pytest.raises(FileNotFoundError, match="make web"):
            render_page(bundle="nope", title="T")


class TestFavicon:
    """Every surface gets a tab icon, and it costs no request to get it."""

    def test_page_carries_an_inline_icon(self):
        page = render_page(bundle="export", title="T")
        assert '<link rel="icon" type="image/png" href="data:image/png;base64,' in page

    def test_icon_lives_in_the_head(self):
        page = render_page(bundle="export", title="T")
        assert page.index('rel="icon"') < page.index("<body>")

    def test_icon_is_the_committed_png(self):
        assert FAVICON_PATH.exists(), "run: uv run --extra charts python scripts/gen_duck_sprites.py"
        expected = base64.b64encode(FAVICON_PATH.read_bytes()).decode("ascii")
        assert expected in render_page(bundle="export", title="T")

    def test_icon_is_small_enough_to_ship_in_every_document(self):
        """It rides in ten export files and both boards; a 64px source was 9.4 KB."""
        assert len(FAVICON_PATH.read_bytes()) < 4096

    def test_a_missing_icon_does_not_break_the_page(self, monkeypatch, tmp_path):
        """A missing decoration must never be why an export fails to write."""
        import yeaboi.web.assets as assets_mod

        monkeypatch.setattr(assets_mod, "FAVICON_PATH", tmp_path / "gone.png")
        assets_mod._favicon_data_uri.cache_clear()
        try:
            page = render_page(bundle="export", title="T")
            assert "<link" not in page
            assert "<title>T</title>" in page
        finally:
            assets_mod._favicon_data_uri.cache_clear()


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

        The footer credit is the one exception, and it is exempted by *blanking
        a single occurrence* rather than by widening the pattern. A link is a
        place to go, not something the page loads: nothing is fetched, an export
        opened from disk with no network renders identically, and the CSPs
        govern requests and framing rather than where a click leads. Blanking
        one occurrence keeps the guard's teeth — a second appearance of the same
        string, which is what an ``<img src>`` or a real fetch to the site would
        look like, still fails here.
        """
        js = read_asset(f"{bundle}.js").replace(f'"{CREDIT_URL}"', '""', 1)
        assert not re.search(r"https?://(?!www\.w3\.org)", js), "bundle references an external URL"
        assert "import(" not in js, "dynamic import would emit a second file"
        assert "importScripts" not in js

    @pytest.mark.parametrize("bundle", BUNDLES)
    def test_every_surface_links_its_credit(self, bundle):
        """Two-way: the exemption above must not become a place a URL can hide.

        Every one of the five surfaces renders the byline, so every bundle must
        contain the link — if one stops, the carve-out in the guard above is
        exempting nothing and should go rather than sit there widening it.
        """
        assert CREDIT_URL in read_asset(f"{bundle}.js"), f"{bundle}.js has no credit link"

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


class TestExportedFilesAreInert:
    """The edit stack ships in the bundle; it must never switch itself on.

    `import()` is banned by `test_bundle_fetches_nothing` and `cssCodeSplit` is
    off, so there is no lazy path and never can be — the editing code is in every
    exported file whether that file wants it or not. What keeps the file inert is
    that `main.tsx` reaches the edit stack only through the boot payload's
    `editing` key, and an exporter never writes one.
    """

    def test_the_bundle_really_does_contain_the_edit_stack(self):
        # The premise. If this ever stops being true the rest of the class is
        # asserting something vacuous.
        source = (STATIC_DIR / "export.js").read_text(encoding="utf-8")
        assert "/api/edit" in source

    def test_no_exported_report_carries_an_editing_session(self):
        """Asserted over the committed boot payloads of all ten exports.

        Those fixtures are read straight out of the rendered pages by
        ``test_web_wire_shapes``, so this checks the real documents rather than a
        re-derivation of them.
        """
        import json
        from pathlib import Path

        # Off this file, not off STATIC_DIR: the bundles may be resolved from an
        # installed package or an override, and the fixtures are neither.
        fixtures = Path(__file__).resolve().parents[2] / "contracts" / "web" / "fixtures"
        pages = sorted(fixtures.glob("export.*.json"))
        assert pages, "no export fixtures found — this test is checking nothing"
        for page in pages:
            payload = json.loads(page.read_text(encoding="utf-8"))
            assert "editing" not in payload, f"{page.name} carries an editing session"

    def test_a_served_editable_document_does(self):
        # The other half: a guard nobody has seen distinguish two cases is a
        # guard nobody knows works.
        from tests._pages import island
        from yeaboi.agent.state import StandupReport
        from yeaboi.sharing.documents import editable_share, render_editable_page

        share = editable_share(StandupReport(date="2026-01-01"), kind="standup")
        assert "editing" in island(render_editable_page(share))
