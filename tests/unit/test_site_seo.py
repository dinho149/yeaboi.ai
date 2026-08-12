"""Tests for scripts/gen_site_seo.py and the generated docs/ site output.

``docs/`` is published as yeaboi.ai by GitHub Pages straight off ``main`` — there
is no build step and no deploy job, so a stale SEO block, a wrong canonical or a
dropped sitemap entry goes live silently. These tests are the ``--check``: unlike
``make web-check`` (which needs Node, absent from the Python CI lanes), Python is
in every lane, so asserting staleness here puts the guard into ``make test-fast``,
the pre-commit hook and both CI jobs at once.
"""

import base64
import importlib.util
import json
import re
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# scripts/ is not a package, so load the module straight from its file path.
_MODULE_PATH = ROOT / "scripts" / "gen_site_seo.py"
_spec = importlib.util.spec_from_file_location("gen_site_seo", _MODULE_PATH)
seo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seo)

DOCS = seo.DOCS
PAGES = seo.pages()
NAV = seo.parse_nav_groups(seo.SITE_JS.read_text(encoding="utf-8"))
IDS = [p.relative_to(DOCS).as_posix() for p in PAGES]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _head(text: str) -> str:
    """The managed block only — so a stray tag elsewhere can't satisfy a check."""
    return text.partition(seo.HEAD_BEGIN)[2].partition(seo.HEAD_END)[0]


def _attr(text: str, key: str, kind: str = "property") -> str | None:
    m = re.search(rf'<meta {kind}="{re.escape(key)}" content="([^"]*)"', text)
    return m.group(1) if m else None


class TestGeneratedOutputIsFresh:
    """The --check. Any failure here means: run `make site-seo` and commit."""

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_page_is_not_stale(self, path: Path) -> None:
        assert seo.render(path, NAV) == _read(path), (
            f"{path.relative_to(ROOT)} is stale — run `make site-seo` and commit the result"
        )

    def test_sitemap_is_not_stale(self) -> None:
        assert seo.render_sitemap(PAGES) == _read(DOCS / "sitemap.xml"), "run `make site-seo`"

    def test_robots_is_not_stale(self) -> None:
        assert seo.render_robots() == _read(DOCS / "robots.txt"), "run `make site-seo`"

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_whitespace_matches_precommit_hooks(self, path: Path) -> None:
        """LF, no trailing whitespace, exactly one final newline.

        `.pre-commit-config.yaml` runs trailing-whitespace and end-of-file-fixer
        over docs/. If the generator disagrees, the hooks rewrite the file, the
        check then fails, and the loop never converges.
        """
        text = _read(path)
        assert "\r" not in text, "CRLF in generated output"
        assert text.endswith("\n") and not text.endswith("\n\n")
        offenders = [n for n, ln in enumerate(text.split("\n"), 1) if ln != ln.rstrip()]
        assert not offenders, f"trailing whitespace on lines {offenders}"

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_rendering_is_a_fixed_point(self, path: Path, tmp_path: Path) -> None:
        """Re-rendering generated output must not change it.

        The scrub step deletes og/twitter/canonical tags found outside the
        managed block. If it ever reached inside its own output, run 2 would
        differ from run 1 — making --check pass locally and fail in CI, or the
        pre-commit hook and the generator rewrite each other forever.
        """
        once = seo.render(path, NAV)
        scratch = tmp_path / path.name
        scratch.write_text(once, encoding="utf-8")
        # render() keys off the path for KINDS/url, so re-render through the
        # real path after proving the on-disk bytes already equal `once`.
        assert seo.render(path, NAV) == once
        assert once.count(seo.HEAD_BEGIN) == 1, "block duplicated on re-render"


class TestRegistryParity:
    """Two-way set equality, in the style of test_surface_parity.py."""

    def test_every_page_has_a_kind(self) -> None:
        found, declared = set(IDS), set(seo.KINDS)
        assert found == declared, (
            f"KINDS is out of sync with docs/.\n"
            f"  missing entries: {sorted(found - declared)}\n"
            f"  stale entries:   {sorted(declared - found)}\n"
            f"Edit KINDS in scripts/gen_site_seo.py."
        )

    def test_nav_paths_match_docs_pages(self) -> None:
        nav_paths = set(seo.nav_lookup(NAV))
        page_urls = {
            seo.url_for(p)
            for p in PAGES
            if seo.KINDS[p.relative_to(DOCS).as_posix()] not in (seo.Kind.LANDING, seo.Kind.ERROR)
        }
        assert nav_paths == page_urls, (
            f"NAV_GROUPS in docs/assets/site.js disagrees with the docs pages on disk.\n"
            f"  in nav, no file:  {sorted(nav_paths - page_urls)}\n"
            f"  file, not in nav: {sorted(page_urls - nav_paths)}"
        )

    def test_nav_shape(self) -> None:
        # Modes is the Humans half of the landing split; Agents is the other.
        assert [g["label"] for g in NAV] == ["Start", "Modes", "Agents", "Guides", "Reference"]
        for path in seo.nav_lookup(NAV):
            assert (DOCS / path.lstrip("/")).exists(), f"nav points at missing file: {path}"

    def test_sitemap_lists_exactly_the_indexable_pages(self) -> None:
        locs = set(re.findall(r"<loc>([^<]+)</loc>", _read(DOCS / "sitemap.xml")))
        want = {
            f"{seo.SITE}{seo.url_for(p)}"
            for p in PAGES
            if seo.KINDS[p.relative_to(DOCS).as_posix()] is not seo.Kind.ERROR
        }
        assert locs == want
        assert f"{seo.SITE}/404.html" not in locs, "the 404 page must not be in the sitemap"


class TestHeadBlock:
    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_exactly_one_managed_block_inside_head(self, path: Path) -> None:
        text = _read(path)
        assert text.count(seo.HEAD_BEGIN) == 1
        assert text.count(seo.HEAD_END) == 1
        assert text.index(seo.HEAD_BEGIN) < text.index(seo.HEAD_END) < text.index("</head>")

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_canonical_matches_computed_url(self, path: Path) -> None:
        text, head = _read(path), _head(_read(path))
        kind = seo.KINDS[path.relative_to(DOCS).as_posix()]
        if kind is seo.Kind.ERROR:
            assert "canonical" not in text, "a 404 page must not claim a canonical URL"
            assert 'content="noindex, follow"' in head
            return
        found = re.findall(r'<link rel="canonical" href="([^"]+)"', text)
        assert found == [f"{seo.SITE}{seo.url_for(path)}"]

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_open_graph_and_twitter_present(self, path: Path) -> None:
        head = _head(_read(path))
        for key in ("og:site_name", "og:locale", "og:type", "og:title", "og:description", "og:image"):
            assert _attr(head, key) is not None, f"missing {key}"
        assert _attr(head, "twitter:card", "name") == "summary_large_image"
        for key in ("twitter:title", "twitter:description", "twitter:image"):
            assert _attr(head, key, "name") is not None, f"missing {key}"
        if seo.KINDS[path.relative_to(DOCS).as_posix()] is not seo.Kind.ERROR:
            assert _attr(head, "og:url") == f"{seo.SITE}{seo.url_for(path)}"

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_og_image_is_absolute_and_exists(self, path: Path) -> None:
        url = _attr(_head(_read(path)), "og:image")
        assert url.startswith(f"{seo.SITE}/"), "social images must be absolute URLs"
        asset = DOCS / url.removeprefix(f"{seo.SITE}/")
        assert asset.exists(), f"og:image points at a missing file: {asset.relative_to(ROOT)}"

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_no_seo_tags_outside_the_block(self, path: Path) -> None:
        """The migration scrub held — otherwise a page emits two canonicals."""
        text = _read(path)
        outside = text.partition(seo.HEAD_BEGIN)[0] + text.partition(seo.HEAD_END)[2]
        for stray in ('property="og:', 'name="twitter:', 'rel="canonical"'):
            assert stray not in outside, f"{stray} found outside the managed block"

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_load_bearing_tags_survived_the_scrub(self, path: Path) -> None:
        """The scrub regex must never eat these — the worst realistic outcome."""
        text = _read(path)
        for keep in ('name="description"', 'name="viewport"', 'name="theme-color"', 'name="color-scheme"'):
            assert keep in text, f"scrub removed {keep}"


class TestJsonLd:
    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_parses_and_is_well_formed(self, path: Path) -> None:
        head = _head(_read(path))
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', head, re.DOTALL)
        if seo.KINDS[path.relative_to(DOCS).as_posix()] is seo.Kind.ERROR:
            assert blocks == []
            return
        assert len(blocks) == 1
        data = json.loads(blocks[0])
        assert data["@context"] == "https://schema.org"
        assert isinstance(data["@graph"], list) and data["@graph"]

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_cannot_break_out_of_its_script_element(self, path: Path) -> None:
        """Mirrors test_web_assets.py::test_escapes_script_breakout."""
        head = _head(_read(path))
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', head, re.DOTALL):
            for char in ("<", ">", "&"):
                assert char not in block, f"unescaped {char!r} in JSON-LD"

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_types_match_the_page_kind(self, path: Path) -> None:
        kind = seo.KINDS[path.relative_to(DOCS).as_posix()]
        if kind is seo.Kind.ERROR:
            return
        head = _head(_read(path))
        graph = json.loads(re.search(r'ld\+json">(.*?)</script>', head, re.DOTALL).group(1))["@graph"]
        types = {node["@type"] for node in graph}
        expected = {
            seo.Kind.LANDING: {"Organization", "WebSite", "SoftwareApplication"},
            seo.Kind.HUB: {"CollectionPage", "BreadcrumbList"},
            seo.Kind.ARTICLE: {"TechArticle", "BreadcrumbList"},
        }[kind]
        assert types == expected

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_breadcrumbs_are_valid(self, path: Path) -> None:
        kind = seo.KINDS[path.relative_to(DOCS).as_posix()]
        if kind in (seo.Kind.ERROR, seo.Kind.LANDING):
            return
        head = _head(_read(path))
        graph = json.loads(re.search(r'ld\+json">(.*?)</script>', head, re.DOTALL).group(1))["@graph"]
        crumbs = next(n for n in graph if n["@type"] == "BreadcrumbList")["itemListElement"]
        assert [c["position"] for c in crumbs] == list(range(1, len(crumbs) + 1))
        assert crumbs[0]["item"] == f"{seo.SITE}/"
        # Google's guidance: the current page carries a name but no item.
        assert "item" not in crumbs[-1]
        assert all("item" in c for c in crumbs[:-1])

    def test_software_application_facts(self) -> None:
        head = _head(_read(DOCS / "index.html"))
        graph = json.loads(re.search(r'ld\+json">(.*?)</script>', head, re.DOTALL).group(1))["@graph"]
        app = next(n for n in graph if n["@type"] == "SoftwareApplication")
        assert app["offers"]["price"] == "0"
        assert app["isAccessibleForFree"] is True
        assert app["license"] == "https://opensource.org/licenses/MIT"
        assert app["applicationCategory"] == "DeveloperApplication"
        # No softwareVersion anywhere: auto-version.yml pushes a version bump to
        # PR branches and regenerates nothing, so any version-derived output
        # would make --check fail on every PR that touches src/.
        assert "softwareVersion" not in json.dumps(graph)
        # Self-issued ratings on your own product are a manual-action risk.
        assert "aggregateRating" not in json.dumps(graph)

    def test_urls_agree_with_pyproject(self) -> None:
        meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        assert seo.REPO_URL == meta["urls"]["Repository"], "generator and pyproject disagree on the repo URL"
        assert seo.PYPI_URL.rstrip("/").endswith(meta["name"]), "PyPI URL does not match the package name"


class TestAnalytics:
    def test_measurement_id_is_valid_or_absent(self) -> None:
        """A placeholder ID must never reach production HTML.

        Two states are legal: a real ID (emitted on every page) or the
        placeholder (emitted nowhere). A dead `G-XXXXXXXXXX` tag in the wild is
        a silent failure — it looks configured and measures nothing.
        """
        gid = seo.GA_MEASUREMENT_ID
        configured = gid != seo.GA_PLACEHOLDER and gid
        if configured:
            assert re.fullmatch(r"G-[A-Z0-9]{10}", gid), f"not a valid GA4 measurement ID: {gid!r}"
        for path in PAGES:
            head = _head(_read(path))
            present = "googletagmanager.com/gtag/js?id=" in head
            assert present == bool(configured), f"{path.name}: GA snippet presence disagrees with the constant"
            if present:
                assert f"gtag/js?id={gid}" in head

    def test_cookieless_posture_is_enforced_when_configured(self) -> None:
        """The no-consent-banner stance is a tested property, not a comment."""
        if seo.GA_MEASUREMENT_ID == seo.GA_PLACEHOLDER:
            pytest.skip("GA4 not configured yet")
        head = _head(_read(DOCS / "index.html"))
        assert "gtag('consent','default'" in head
        for signal in ("ad_storage", "ad_user_data", "ad_personalization", "analytics_storage"):
            assert f"{signal}:'denied'" in head
        assert "client_storage:'none'" in head

    def test_spa_navigation_sends_a_pageview(self) -> None:
        """The SPA blind spot is silent, so it needs a guard.

        navigateTo swaps #page-content via outerHTML after pushState; gtag never
        sees it, so without an explicit page_view the whole session is
        attributed to the landing page.
        """
        js = seo.SITE_JS.read_text(encoding="utf-8")
        nav = js.partition("function navigateTo(")[2].partition("\n}\n")[0]
        assert "gtag('event', 'page_view'" in nav

    def test_navigation_checks_response_status(self) -> None:
        js = seo.SITE_JS.read_text(encoding="utf-8")
        nav = js.partition("function navigateTo(")[2].partition("\n}\n")[0]
        assert "if (!r.ok)" in nav, "navigateTo would swap a 404 body in as a real page"


class TestCopy:
    def test_titles_are_unique_and_within_the_serp_limit(self) -> None:
        titles = {}
        for path in PAGES:
            title, _desc, _h1 = seo.page_copy(_read(path), path)
            assert len(title) <= 60, f"{path.name}: title is {len(title)} chars (max 60): {title!r}"
            assert title not in titles, f"duplicate title on {path.name} and {titles[title]}"
            titles[title] = path.name

    def test_descriptions_are_unique_and_within_the_serp_limit(self) -> None:
        seen = {}
        for path in PAGES:
            _title, desc, _h1 = seo.page_copy(_read(path), path)
            assert 20 <= len(desc) <= 160, f"{path.name}: description is {len(desc)} chars (want 20-160)"
            assert desc not in seen, f"duplicate description on {path.name} and {seen[desc]}"
            seen[desc] = path.name

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_exactly_one_h1(self, path: Path) -> None:
        assert len(re.findall(r"<h1[^>]*>", _read(path))) == 1


class TestAssetVersioning:
    def test_one_cache_bust_across_every_page(self) -> None:
        """Catches the 72-string drift class this generator exists to kill."""
        found = {v for p in PAGES for v in re.findall(r'(?:href|src)="/(?:docs/)?assets/[^"?]+\?v=(\d+)"', _read(p))}
        assert found == {str(seo.ASSET_VERSION)}, f"mixed ?v= values across docs/: {sorted(found)}"

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_versioned_assets_exist(self, path: Path) -> None:
        for ref in re.findall(r'(?:href|src)="(/(?:docs/)?assets/[^"?]+)\?v=\d+"', _read(path)):
            assert (DOCS / ref.lstrip("/")).exists(), f"{path.name} references missing asset {ref}"

    def test_one_pinned_third_party_script_version(self) -> None:
        found = {v for p in PAGES for v in re.findall(r"unpkg\.com/lenis@([\d.]+)", _read(p))}
        assert len(found) <= 1, f"mixed lenis versions across docs/: {sorted(found)}"
        assert found <= {seo.LENIS_VERSION}, f"docs/ disagrees with LENIS_VERSION: {sorted(found)}"


# Every <script> element with an external src, as (whole tag, url) pairs.
# Deliberately wide: single quotes and a protocol-relative `//cdn…/x.js` are both
# valid HTML and both load third-party code, so a pattern that only understood
# double-quoted `https://` would wave through the exact tag it exists to catch.
_EXTERNAL_SCRIPT_RE = re.compile(r"""<script\b[^>]*\bsrc=["']((?:https?:)?//[^"']+)["'][^>]*>""")


def _external_scripts(text: str) -> list[tuple[str, str]]:
    return [(m.group(0), m.group(1)) for m in _EXTERNAL_SCRIPT_RE.finditer(text)]


def _host_of(url: str) -> str:
    """Host of an absolute or protocol-relative URL — index 2 either way."""
    return url.split("/")[2]


class TestSubresourceIntegrity:
    """The site loads third-party JS; SRI is what stops a CDN from owning it.

    ``docs/`` is served straight off ``main`` by GitHub Pages, so a script fetched
    from unpkg executes on the production domain with the same privileges as our
    own code. Without an ``integrity`` hash, an unpkg outage-turned-compromise or
    a hijacked lenis release publishes arbitrary JS to every visitor and nothing
    on the page notices. The hash is one string that has to agree across 23 pages
    — exactly the drift class this generator already exists to kill — so it is a
    constant that gets rewritten in, never typed twice.
    """

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_third_party_script_is_hashed_and_cors_enabled(self, path: Path) -> None:
        """Both attributes, together: SRI on a cross-origin script is only
        enforced when the fetch is CORS-enabled, so ``integrity`` without
        ``crossorigin`` is a hash the browser never checks.
        """
        for tag, url in _external_scripts(_read(path)):
            if "unpkg.com" not in url:
                continue
            assert f'integrity="{seo.LENIS_SRI}"' in tag, (
                f"{path.name}: unpkg script has a missing or wrong integrity hash"
            )
            assert 'crossorigin="anonymous"' in tag, (
                f"{path.name}: integrity without crossorigin is never enforced on a cross-origin script"
            )

    def test_one_hash_across_every_page(self) -> None:
        """Scoped to script tags, not the whole document: an SRI'd stylesheet is
        a correct thing to add later and must not fail this test.
        """
        found = {
            h
            for p in PAGES
            for tag, _url in _external_scripts(_read(p))
            for h in re.findall(r'integrity="([^"]+)"', tag)
        }
        assert found == {seo.LENIS_SRI}, f"mixed or missing integrity hashes across docs/: {sorted(found)}"

    @pytest.mark.parametrize("path", PAGES, ids=IDS)
    def test_no_unhashed_external_script(self, path: Path) -> None:
        """The guard that outlives this one script.

        A future page adding a second CDN tag is the whole failure mode here, and
        it is silent — the page works perfectly right up until the CDN is
        compromised. Anything external must be hashed or explicitly exempt.
        """
        for tag, url in _external_scripts(_read(path)):
            host = _host_of(url)
            if host in seo.SRI_EXEMPT_HOSTS:
                continue
            assert "integrity=" in tag, (
                f"{path.name}: external script with no integrity hash: {url}\n"
                f"Add an SRI hash, or add its host to SRI_EXEMPT_HOSTS with a reason."
            )

    def test_only_the_known_third_parties_are_loaded(self) -> None:
        """Two-way, so removing lenis fails here rather than rotting the constants."""
        hosts = {_host_of(url) for p in PAGES for _tag, url in _external_scripts(_read(p))}
        assert hosts == {"unpkg.com", *seo.SRI_EXEMPT_HOSTS}, f"unexpected third-party script hosts: {sorted(hosts)}"

    def test_exempt_host_is_the_generated_analytics_loader(self) -> None:
        """gtag.js is exempt because Google publishes no hash for it and rewrites
        it continuously — not because hashing was inconvenient. It is emitted by
        _ga_lines(), so it cannot be a hand-added stray.
        """
        assert seo.SRI_EXEMPT_HOSTS == ("www.googletagmanager.com",)
        assert "www.googletagmanager.com" in "\n".join(seo._ga_lines())

    def test_sri_constant_is_a_wellformed_sha384(self) -> None:
        algo, _, digest = seo.LENIS_SRI.partition("-")
        assert algo == "sha384", "SRI must be sha384 — sha256 is the weakest browsers accept"
        assert len(base64.b64decode(digest, validate=True)) == 48, "not a 48-byte SHA-384 digest"

    @pytest.mark.parametrize(
        ("tag", "host"),
        [
            ('<script src="https://cdn.example.com/x.js"></script>', "cdn.example.com"),
            ('<script src="http://cdn.example.com/x.js"></script>', "cdn.example.com"),
            # Protocol-relative and single-quoted are both valid HTML and both
            # load third-party code — the detector must not have a blind spot
            # that the exact tag it guards against could be pasted through.
            ('<script src="//cdn.example.com/x.js"></script>', "cdn.example.com"),
            ("<script src='https://cdn.example.com/x.js'></script>", "cdn.example.com"),
            ('<script defer src="https://cdn.example.com/x.js"></script>', "cdn.example.com"),
        ],
    )
    def test_external_script_detector_has_no_blind_spot(self, tag: str, host: str) -> None:
        found = _external_scripts(tag)
        assert len(found) == 1, f"external script not detected: {tag}"
        assert _host_of(found[0][1]) == host

    def test_external_script_detector_ignores_local_scripts(self) -> None:
        for local in (
            '<script src="/assets/site.js?v=136"></script>',
            '<script type="application/ld+json">{}</script>',
        ):
            assert _external_scripts(local) == []

    def test_version_is_pinned_exactly(self) -> None:
        """A range or `latest` in the URL would serve bytes the hash cannot match,
        which fails closed on every page the moment lenis publishes.
        """
        assert re.fullmatch(r"\d+\.\d+\.\d+", seo.LENIS_VERSION), f"not an exact version: {seo.LENIS_VERSION!r}"


class TestLenisTagRewrite:
    """Pure unit tests of the rewrite — no filesystem."""

    def test_tag_carries_version_hash_and_crossorigin(self) -> None:
        tag = seo.lenis_tag()
        assert f"lenis@{seo.LENIS_VERSION}/dist/lenis.min.js" in tag
        assert f'integrity="{seo.LENIS_SRI}"' in tag
        assert 'crossorigin="anonymous"' in tag
        assert tag.endswith("></script>")

    def test_rewrite_upgrades_a_bare_tag(self) -> None:
        bare = '<script src="https://unpkg.com/lenis@1.3.4/dist/lenis.min.js"></script>'
        assert seo._LENIS_RE.sub(lambda _: seo.lenis_tag(), bare) == seo.lenis_tag()

    def test_rewrite_corrects_a_stale_version_or_hash(self) -> None:
        stale = (
            '<script src="https://unpkg.com/lenis@1.0.0/dist/lenis.min.js" '
            'integrity="sha384-old" crossorigin="anonymous"></script>'
        )
        assert seo._LENIS_RE.sub(lambda _: seo.lenis_tag(), stale) == seo.lenis_tag()

    def test_rewrite_handles_attributes_before_src(self) -> None:
        """`<script defer src="…">` must be rewritten too.

        If it were not, the page would keep an un-hashed tag while the failing
        test told the author to run `make site-seo` — advice that would not have
        fixed it. Extra attributes are preserved by replacement, not by capture:
        the canonical tag is what gets written, so a `defer` is dropped rather
        than silently kept. That is deliberate — the loading semantics of this
        one script are load-bearing (site.js reads `window.Lenis` right after).
        """
        deferred = '<script defer src="https://unpkg.com/lenis@1.3.4/dist/lenis.min.js"></script>'
        assert seo._LENIS_RE.sub(lambda _: seo.lenis_tag(), deferred) == seo.lenis_tag()

    def test_rewrite_is_idempotent(self) -> None:
        once = seo._LENIS_RE.sub(lambda _: seo.lenis_tag(), seo.lenis_tag())
        assert seo._LENIS_RE.sub(lambda _: seo.lenis_tag(), once) == seo.lenis_tag()

    def test_rewrite_leaves_other_scripts_alone(self) -> None:
        others = (
            '<script src="/assets/site.js?v=136"></script>\n'
            '<script async src="https://www.googletagmanager.com/gtag/js?id=G-K9HLPT5ZMP"></script>'
        )
        assert seo._LENIS_RE.sub(lambda _: seo.lenis_tag(), others) == others

    def test_rewrite_does_not_invent_a_tag(self) -> None:
        """A page that loads no lenis must stay that way — the rewrite corrects
        what an author wrote, it does not add markup nobody asked for.
        """
        page = '<html><body><script src="/assets/site.js?v=136"></script></body></html>'
        assert seo._LENIS_RE.sub(lambda _: seo.lenis_tag(), page) == page


class TestCrawlability:
    """The actual property that was broken: no crawlable links between docs."""

    @pytest.mark.parametrize(
        "path",
        [p for p in PAGES if seo.KINDS[p.relative_to(DOCS).as_posix()] is not seo.Kind.LANDING],
        ids=[i for i in IDS if i != "index.html"],
    )
    def test_footer_links_to_every_other_docs_page(self, path: Path) -> None:
        text = _read(path)
        assert text.count(seo.FOOT_BEGIN) == 1, "missing the generated crawlable footer"
        foot = text.partition(seo.FOOT_BEGIN)[2].partition(seo.FOOT_END)[0]
        # Scope to the <nav>: the meta strip below it also carries a brand link
        # to "/" plus outbound GitHub/PyPI links, which are not nav entries.
        nav_html = foot.partition("<nav")[2].partition("</nav>")[0]
        hrefs = set(re.findall(r'<a href="(/[^"]*)"', nav_html))
        assert hrefs == set(seo.nav_lookup(NAV)), "footer links disagree with NAV_GROUPS"
        assert len(hrefs) >= 15, "a docs page must statically link most of the docs tree"
        assert '<a href="/">' in foot, "the footer should also link home"

    @pytest.mark.parametrize(
        "path",
        [p for p in PAGES if seo.KINDS[p.relative_to(DOCS).as_posix()] is not seo.Kind.LANDING],
        ids=[i for i in IDS if i != "index.html"],
    )
    def test_footer_is_outside_article_and_inside_main(self, path: Path) -> None:
        """Placement is load-bearing, not cosmetic.

        _buildDocsIndex() indexes `.docs-content article` for the client-side
        search; 17 link titles per page inside <article> would swamp every query.
        """
        text = _read(path)
        assert text.index("</article>") < text.index(seo.FOOT_BEGIN)
        assert text.index(seo.FOOT_END) < text.index("</main>")

    @pytest.mark.parametrize(
        "path",
        [p for p in PAGES if seo.KINDS[p.relative_to(DOCS).as_posix()] is not seo.Kind.LANDING],
        ids=[i for i in IDS if i != "index.html"],
    )
    def test_footer_does_not_depend_on_js(self, path: Path) -> None:
        """[data-reveal] starts at opacity:0 (site.css) — never on a crawlable nav."""
        foot = _read(path).partition(seo.FOOT_BEGIN)[2].partition(seo.FOOT_END)[0]
        assert "data-reveal" not in foot

    @pytest.mark.parametrize(
        "path",
        [p for p in PAGES if seo.KINDS[p.relative_to(DOCS).as_posix()] is seo.Kind.ARTICLE],
        ids=[i for i, p in zip(IDS, PAGES) if seo.KINDS[p.relative_to(DOCS).as_posix()] is seo.Kind.ARTICLE],
    )
    def test_current_page_is_marked_once(self, path: Path) -> None:
        foot = _read(path).partition(seo.FOOT_BEGIN)[2].partition(seo.FOOT_END)[0]
        marked = re.findall(r'<a href="([^"]+)" aria-current="page">', foot)
        assert marked == [seo.url_for(path)]


class TestRobotsAndSitemap:
    def test_robots_content(self) -> None:
        text = _read(DOCS / "robots.txt")
        assert f"Sitemap: {seo.SITE}/sitemap.xml" in text
        assert "User-agent: *" in text
        assert "Allow: /" in text
        assert "Disallow: /superpowers/" in text

    def test_sitemap_is_valid_xml(self) -> None:
        root = ET.fromstring(_read(DOCS / "sitemap.xml"))
        assert root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset"
        locs = [e.text for e in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        assert locs and all(u.startswith(f"{seo.SITE}/") for u in locs)
        assert len(locs) == len(set(locs)), "duplicate URLs in the sitemap"
        # No lastmod: CI checks out at fetch-depth 1, so a git-derived date
        # would make --check disagree with a local run.
        assert "lastmod" not in _read(DOCS / "sitemap.xml")

    def test_required_site_files_exist(self) -> None:
        for name in ("404.html", ".nojekyll", "robots.txt", "sitemap.xml", "CNAME"):
            assert (DOCS / name).exists(), f"docs/{name} is missing"

    def test_internal_design_docs_are_not_published(self) -> None:
        """They were readable at yeaboi.ai/superpowers/... for months."""
        assert not (DOCS / "superpowers").exists(), "internal design docs are back in the Pages root"
        assert not list(DOCS.glob("*.pdf")), "an orphan PDF is being served from the site root"

    def test_no_page_embeds_the_demo_gif(self) -> None:
        """Fine as a README asset; a Core Web Vitals hit on a real page."""
        for path in PAGES:
            assert "demo.gif" not in _read(path), f"{path.name} embeds the multi-MB demo GIF"


class TestUrlDerivation:
    """Pure unit tests of the script's own helpers — no filesystem."""

    @pytest.mark.parametrize(
        ("rel", "expected"),
        [
            ("index.html", "/"),
            ("404.html", "/404.html"),
            ("docs/index.html", "/docs/index.html"),
            ("docs/modes/retro.html", "/docs/modes/retro.html"),
        ],
    )
    def test_url_for_applies_the_pages_strip_rule(self, rel: str, expected: str) -> None:
        assert seo.url_for(DOCS / rel) == expected

    def test_only_the_site_root_index_collapses(self) -> None:
        """docs/docs/index.html must keep its filename.

        Every NAV_GROUPS path, the hardcoded navigateTo("/docs/index.html") and
        setDocsCurrent's string equality use the explicit form; canonicalising
        to /docs/ would disagree with every internal link on the site.
        """
        assert seo.url_for(DOCS / "docs" / "index.html") == "/docs/index.html"

    def test_attr_escaping(self) -> None:
        assert seo.attr("a & b") == "a &amp; b"
        assert "&quot;" in seo.attr('say "hi"')

    def test_json_ld_escapes_tag_starters(self) -> None:
        out = seo.json_ld({"x": "</script><img onerror=1>"})
        assert "<" not in out and ">" not in out
        assert json.loads(out)["x"] == "</script><img onerror=1>"

    def test_json_ld_escapes_line_separators(self) -> None:
        """U+2028/9 are legal JSON but illegal in JS string literals."""
        out = seo.json_ld({"x": "a b c"})
        assert " " not in out and " " not in out
        assert json.loads(out)["x"] == "a b c"

    def test_parse_nav_groups_handles_children_and_trailing_commas(self) -> None:
        js = """
        var NAV_GROUPS = [
          { label: "Start", entries: [
            { title: "Docs", path: "/docs/index.html" },
          ] },
          { label: "Modes", entries: [
            { title: "Overview", path: "/docs/modes/index.html", children: [
              { title: "Retro", path: "/docs/modes/retro.html" },
            ] },
          ] },
        ];
        """
        groups = seo.parse_nav_groups(js)
        assert [g["label"] for g in groups] == ["Start", "Modes"]
        lookup = seo.nav_lookup(groups)
        assert lookup["/docs/modes/retro.html"] == ("Modes", "/docs/modes/index.html", "Retro")
        assert lookup["/docs/index.html"][1] is None
