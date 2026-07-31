"""Tests for what is left of ``html_theme`` after the React migration.

The module used to be a stylesheet plus a dozen markup primitives, and most of
this file tested those — that a chip escaped its label, that a sparkline
sanitised a CSS token name, that ``html_page`` produced a self-contained
document. All of it is gone: the primitives are components now, the escaping is
structural rather than per-call, and ``_safe_css_var``'s allowlist is a
TypeScript union that cannot be handed a bad value in the first place.

What remains is the genuinely server-side part, and it is what this file covers:
escaping and URL safety for the Markdown twins, the prose splitting the standup
summary needs, the series normalisation behind a trend card, and
:func:`export_page` — the one shell every static export renders through.
"""

import pytest

from yeaboi.html_theme import safe_url


class TestEscape:
    def test_escapes_markup_and_quotes(self):
        from yeaboi.html_theme import escape

        assert escape('<b>"x"</b>') == "&lt;b&gt;&quot;x&quot;&lt;/b&gt;"

    def test_stringifies_first(self):
        from yeaboi.html_theme import escape

        # Callers pass counts and floats; the one definition takes them.
        assert escape(42) == "42"


class TestProseBullets:
    def test_sentences_and_clauses_split(self):
        from yeaboi.html_theme import prose_bullets

        assert prose_bullets("A moved X. B; C remain in To Do.") == ["A moved X.", "B", "C remain in To Do."]

    def test_abbreviations_not_split(self):
        from yeaboi.html_theme import split_sentences

        assert split_sentences("Fixed e.g. the parser. Then shipped.") == ["Fixed e.g. the parser.", "Then shipped."]

    def test_empty(self):
        from yeaboi.html_theme import prose_bullets

        assert prose_bullets("") == []


class TestHistorySeries:
    def _rows(self):
        return [
            {"d": "2026-07-25", "v": 8, "s": "success"},
            {"d": "2026-07-25", "v": 3, "s": "success"},  # older same-day rerun — dropped
            {"d": "2026-07-24", "v": 6, "s": "failed"},
            {"d": "2026-07-23", "v": 5, "s": "success"},
        ]

    def test_oldest_first_dedupe_newest_wins(self):
        from yeaboi.html_theme import history_series

        pts = history_series(self._rows(), date_key="d", value_key="v")
        # No status filter → the failed row stays.
        assert pts == [("2026-07-23", 5.0), ("2026-07-24", 6.0), ("2026-07-25", 8.0)]

    def test_status_filter(self):
        from yeaboi.html_theme import history_series

        pts = history_series(self._rows(), date_key="d", value_key="v", status_key="s")
        assert pts == [("2026-07-23", 5.0), ("2026-07-25", 8.0)]

    def test_cutoff_and_current(self):
        from yeaboi.html_theme import history_series

        pts = history_series(
            self._rows(),
            date_key="d",
            value_key="v",
            cutoff_date="2026-07-24",
            current=("2026-07-26", 9),
        )
        assert pts == [("2026-07-23", 5.0), ("2026-07-24", 6.0), ("2026-07-26", 9.0)]

    def test_max_points_keeps_tail(self):
        from yeaboi.html_theme import history_series

        rows = [{"d": f"2026-07-{day:02d}", "v": day} for day in range(20, 0, -1)]
        pts = history_series(rows, date_key="d", value_key="v", max_points=5)
        assert len(pts) == 5
        assert pts[-1] == ("2026-07-20", 20.0)


class TestTrend:
    """``trend`` is the payload half of the sparkline card the exports draw.

    It is worth its own tests because it is the one place a *decision* is made
    on the server — under two points there is no chart, and ``None`` is how the
    bundle tells "the server decided" from "the field went missing".
    """

    def test_none_under_two_points(self):
        from yeaboi.html_theme import trend

        rows = [{"d": "2026-07-25", "v": 5}]
        assert trend(rows, date_key="d", value_key="v", title="Volume", label="Cards") is None
        assert trend([], date_key="d", value_key="v", title="Volume", label="Cards") is None

    def test_points_oldest_first_with_a_counted_label(self):
        from yeaboi.html_theme import trend

        rows = [{"d": "2026-07-25", "v": 9}, {"d": "2026-07-24", "v": 5}]
        out = trend(rows, date_key="d", value_key="v", title="Card volume", label="Cards")
        assert out == {
            "title": "Card volume",
            "label": "Cards — last 2 runs",
            "points": [["2026-07-24", 5.0], ["2026-07-25", 9.0]],
        }

    def test_bounds_travel_only_when_set(self):
        from yeaboi.html_theme import trend

        rows = [{"d": "2026-07-25", "v": 95}, {"d": "2026-07-24", "v": 99}]
        plain = trend(rows, date_key="d", value_key="v", title="t", label="l")
        bounded = trend(rows, date_key="d", value_key="v", title="t", label="l", floor=0, ceiling=100)
        assert "floor" not in plain and "ceiling" not in plain
        # A percentage cannot exceed 100, so the ceiling is a fact about the
        # series — padding past it would claim headroom that does not exist.
        assert bounded["floor"] == 0 and bounded["ceiling"] == 100


class TestImageDataUri:
    def test_embeds_a_real_file(self, tmp_path):
        from yeaboi.html_theme import image_data_uri

        png = tmp_path / "chart.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n")
        assert image_data_uri(png).startswith("data:image/png;base64,")

    def test_missing_file_is_a_decoration_the_report_does_without(self, tmp_path):
        from yeaboi.html_theme import image_data_uri

        assert image_data_uri(tmp_path / "gone.png") == ""

    def test_oversized_file_is_skipped(self, tmp_path, monkeypatch):
        from yeaboi import html_theme

        big = tmp_path / "big.png"
        big.write_bytes(b"x" * 64)
        monkeypatch.setattr(html_theme, "_MAX_EMBED_BYTES", 8)
        assert html_theme.image_data_uri(big) == ""


class TestExportPage:
    """The shell every static export renders through.

    ``report`` is a plain mapping of text and numbers, so the assertions here
    are about the *document*: that the payload lands in a non-executable island,
    that the mode reaches ``<html>`` where the accent can be right on the first
    paint, and that the page stays self-contained.
    """

    def _page(self, **over):
        from yeaboi.html_theme import export_page

        kwargs = {
            "mode": "analysis",
            "title": "Team Profile — X",
            "wordmark": "team",
            "report": {"kind": "profile", "coverage": [], "sections": []},
        }
        kwargs.update(over)
        return export_page(**kwargs)

    def test_payload_lands_in_the_island(self):
        from tests._pages import island

        boot = island(self._page(subtitle="jira/X", badges=["Sprint 1"]))
        assert boot["report"]["kind"] == "profile"
        assert boot["chrome"]["subtitle"] == "jira/X"
        assert boot["chrome"]["badges"] == ["Sprint 1"]
        assert boot["chrome"]["frame"] == "yeaboi — analysis"

    def test_mode_reaches_the_html_element(self):
        assert 'data-mode="analysis"' in self._page()

    def test_optional_chrome_is_absent_rather_than_empty(self):
        from tests._pages import island

        chrome = island(self._page())["chrome"]
        for key in ("subtitle", "facts", "badges", "nav"):
            assert key not in chrome, f"{key} should be omitted, not sent empty"

    def test_valueless_facts_are_dropped(self):
        from tests._pages import island

        chrome = island(self._page(facts=[("SPRINTS", "4"), ("STORIES", "")]))["chrome"]
        # A fact with no value is a label with nothing behind it.
        assert chrome["facts"] == [["SPRINTS", "4"]]

    def test_default_footer(self):
        from tests._pages import island

        assert island(self._page())["chrome"]["footer"] == "Generated by yeaboi.ai"

    def test_noscript_names_the_markdown_sibling(self):
        from tests._pages import markup

        # Against the markup, not the document: the inlined stylesheet has a
        # comment mentioning `<noscript>`, so a substring check over the whole
        # page reads the bundle and reports whatever it happens to contain.
        page = markup(self._page(markdown_name="team-profile-20260731-090000.md"))
        assert "<noscript>" in page
        assert "team-profile-20260731-090000.md" in page
        assert "<noscript>" not in markup(self._page())

    def test_noscript_filename_is_escaped(self):
        page = self._page(markdown_name='<img src=x onerror="alert(1)">.md')
        assert "<img src=x" not in page

    def test_self_contained(self):
        from tests._pages import assert_self_contained

        page = self._page()
        assert_self_contained(page)
        assert 'src="http' not in page


class TestSafeUrl:
    """safe_url() is the scheme allowlist for every exported href.

    HTML escaping does not neutralise a scheme — `javascript:alert(1)` contains
    no character html.escape() rewrites — so this is the only thing standing
    between an attacker-influenced tracker URL and a click-to-execute link.
    """

    @pytest.mark.parametrize(
        "url",
        [
            "https://jira.example.com/browse/ABC-1",
            "http://localhost:8080/x",
            "HTTPS://JIRA.EXAMPLE.COM/browse/ABC-1",  # scheme match is case-insensitive
            "mailto:someone@example.com",
        ],
    )
    def test_allows_safe_schemes(self, url):
        assert safe_url(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "JaVaScRiPt:alert(1)",
            "  javascript:alert(1)  ",  # leading/trailing whitespace is stripped first
            "java\tscript:alert(1)",  # browsers remove TAB from URLs before parsing
            "java\nscript:alert(1)",
            "java\rscript:alert(1)",
            "\x00javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
        ],
    )
    def test_rejects_dangerous_schemes(self, url):
        assert safe_url(url) == ""

    def test_rejects_protocol_relative(self):
        # Under file:// a protocol-relative URL resolves to a bogus origin, and
        # it is never what an exporter meant to emit.
        assert safe_url("//evil.example.com/x") == ""

    @pytest.mark.parametrize("url", ["example.com/browse/ABC-1", "/browse/ABC-1", "browse/ABC-1"])
    def test_allows_schemeless_relative(self, url):
        # No scheme means the browser resolves relative to the document — inert.
        # Kept so a Jira base URL configured without https:// still links.
        assert safe_url(url) == url

    @pytest.mark.parametrize("url", ["", "   ", None, "\t\n"])
    def test_empty_and_none(self, url):
        assert safe_url(url) == ""

    def test_interior_spaces_are_preserved(self):
        # Browsers do not strip interior spaces, so they cannot smuggle a scheme
        # past the check — and stripping them would corrupt legitimate URLs.
        assert safe_url("https://x.example/a b") == "https://x.example/a b"
        assert safe_url("java script:alert(1)") == "java script:alert(1)"  # no scheme → inert
