"""Unit tests for reporting/presentation — the self-contained slide deck."""

import json

from yeaboi.agent.state import DeliveredItem, DeliveryReport
from yeaboi.reporting import presentation
from yeaboi.reporting.style import DeckStyle


def _report():
    return DeliveryReport(
        period_label="Last month (~2 sprints)",
        period_start="2026-06-15",
        period_end="2026-07-13",
        project_name="Acme Portal",
        sprint_names=("Sprint 11", "Sprint 12"),
        headline="Two sprints of strong delivery.",
        executive_summary="We shipped SSO and cut checkout time.",
        themes=(("Security", ("SSO", "MFA")), ("Performance", ("Faster checkout",))),
        highlights=("SSO live", "2x faster checkout"),
        metrics=(("Items delivered", "12"),),
        delivered_items=(DeliveredItem(key="A-1", title="x", status="Done"),),
        emoji_theme=(("headline", "🚀"), ("themes", "🧩"), ("highlights", "⭐")),
    )


class TestBuildSlides:
    def test_slide_order_and_types(self):
        slides = presentation._build_slides(_report(), DeckStyle())
        types = [s["type"] for s in slides]
        assert types[0] == "title"
        assert types[-1] == "thanks"
        assert "summary" in types
        assert "metrics" in types
        assert types.count("list") == 3  # 2 themes + highlights

    def test_empty_report_still_has_title_and_thanks(self):
        slides = presentation._build_slides(DeliveryReport(period_label="Last sprint"), DeckStyle())
        types = [s["type"] for s in slides]
        assert types == ["title", "thanks"]

    def test_summary_slide_carries_sentence_points(self):
        report = _report()
        summary = (
            "Over the past month the team locked down access across the whole estate. "
            "Monitoring coverage expanded to every cloud account we operate in production. "
            "A housekeeping programme removed legacy accounts and tightened key permissions."
        )
        from dataclasses import replace

        slides = presentation._build_slides(replace(report, executive_summary=summary), DeckStyle())
        summary_slide = next(s for s in slides if s["type"] == "summary")
        assert "body" not in summary_slide
        assert len(summary_slide["points"]) == 3
        assert summary_slide["points"][0].endswith("estate.")

    def test_compact_cards_capped_at_four_bullets(self):
        from dataclasses import replace

        report = replace(_report(), themes=(("Big theme", tuple(f"outcome {i}" for i in range(8))),))
        slides = presentation._build_slides(report, DeckStyle(layout="compact", content_fit="tight"))
        cards_slide = next(s for s in slides if s["type"] == "cards")
        bullets = cards_slide["cards"][0][1]
        # Quarter-slide cards cap tighter than the default max_bullets=6.
        assert bullets == ["outcome 0", "outcome 1", "outcome 2", "outcome 3", "… and 4 more"]

    def test_compact_expand_keeps_every_bullet_via_cont_cards(self):
        from dataclasses import replace

        report = replace(_report(), themes=(("Big theme", tuple(f"outcome {i}" for i in range(8))),))
        slides = presentation._build_slides(report, DeckStyle(layout="compact"))  # default fit resolves to expand
        cards = [c for s in slides if s["type"] == "cards" for c in s["cards"]]
        assert [t for t, _b in cards] == ["Big theme", "Big theme (cont.)"]
        assert [b for _t, page in cards for b in page] == [f"outcome {i}" for i in range(8)]
        assert all("more" not in b for _t, page in cards for b in page)


class TestBuildPresentationHtml:
    def test_self_contained(self):
        import re

        html = presentation.build_presentation_html(_report(), theme="aurora")
        assert html.lstrip().startswith("<!DOCTYPE html>")
        assert 'data-theme="aurora"' in html
        assert "<style>" in html and "<script>" in html
        # No external resources (offline). The embedded branding data URI is
        # stripped first — 44KB of base64 contains arbitrary substrings ("cdn").
        stripped = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", html)
        assert "http://" not in stripped and "https://" not in stripped
        assert "cdn" not in stripped.lower()

    def test_untrusted_text_is_json_encoded_not_raw_markup(self):
        r = DeliveryReport(
            period_label="Last sprint",
            headline="hi",
            themes=(("T", ("<img src=x onerror=alert(1)>",)),),
            emoji_theme=(("themes", "🧩"),),
        )
        html = presentation.build_presentation_html(r)
        # The payload lives inside the JSON slide array, angle brackets escaped by json.dumps
        # (<), so it can never appear as a live tag in the document.
        assert "<img src=x onerror=alert(1)>" not in html
        assert "\\u003cimg" in html or "onerror" in html  # present, but encoded

    def test_invalid_theme_falls_back_to_midnight(self):
        html = presentation.build_presentation_html(_report(), theme="nonsense")
        assert 'data-theme="midnight"' in html

    def test_footer_badge_carries_duck_branding(self):
        html = presentation.build_presentation_html(_report())
        assert '<img id="brandDuck" src="data:image/png;base64,' in html
        assert "Generated by yeaboi.ai" in html
        assert "image-rendering: pixelated" in html  # pixel-art stays crisp

    def test_missing_duck_asset_drops_the_image_only(self, monkeypatch):
        monkeypatch.setattr("yeaboi.reporting.branding.duck_data_uri", lambda: None)
        html = presentation.build_presentation_html(_report())
        assert '<img id="brandDuck"' not in html  # (the CSS rule may remain)
        assert "Generated by yeaboi.ai" in html  # badge text survives

    def test_card_grid_css_sizes_cards_to_content(self):
        html = presentation.build_presentation_html(_report())
        assert "align-items: start;" in html  # cards size to content, no stretch
        assert ".cards.one { grid-template-columns: 1fr; }" in html
        assert "s.wide ? ' one' : ''" in html  # JS applies the lone-card class

    def test_summary_paragraph_css_and_js_fallback_present(self):
        html = presentation.build_presentation_html(_report())
        assert ".body p { margin: 0 0 .9em; }" in html
        # Old saved decks / artifacts with a "body" string still render.
        assert "s.points || (s.body ? [s.body] : [])" in html

    def test_slides_json_parses(self):
        html = presentation.build_presentation_html(_report())
        # Extract the injected SLIDES array and confirm it is valid JSON.
        marker = "const SLIDES = "
        start = html.index(marker) + len(marker)
        end = html.index(";\n", start)
        data = json.loads(html[start:end])
        assert isinstance(data, list) and data[0]["type"] == "title"


class TestCustomThemes:
    def _with_custom(self, monkeypatch):
        monkeypatch.setattr(
            "yeaboi.reporting.themes.load_custom_palettes",
            lambda: {
                "corporate": {
                    "bg1": "#101418",
                    "bg2": "#1c2733",
                    "fg": "#eef3f8",
                    "muted": "#93a3b4",
                    "accent": "#2f81f7",
                    "accent2": "#79b8ff",
                }
            },
        )

    def test_custom_theme_selectable_with_css_block(self, monkeypatch):
        self._with_custom(monkeypatch)
        html = presentation.build_presentation_html(_report(), theme="corporate")
        assert 'data-theme="corporate"' in html  # selected as the initial theme
        assert '[data-theme="corporate"] { --bg1:#101418;' in html  # injected CSS palette

    def test_custom_theme_in_t_key_cycle(self, monkeypatch):
        self._with_custom(monkeypatch)
        html = presentation.build_presentation_html(_report(), theme="midnight")
        marker = "const THEMES = "
        start = html.index(marker) + len(marker)
        end = html.index(";\n", start)
        names = json.loads(html[start:end])
        assert names == ["midnight", "aurora", "sunset", "mono", "corporate"]

    def test_unknown_theme_still_falls_back_to_midnight(self, monkeypatch):
        self._with_custom(monkeypatch)
        html = presentation.build_presentation_html(_report(), theme="nope")
        assert 'data-theme="midnight"' in html


class TestSupportingSignalsFootnote:
    def _signals_report(self):
        from dataclasses import replace

        from yeaboi.agent.state import SupportingSignal

        return replace(
            _report(),
            supporting_signals=(
                SupportingSignal(kind="pull_requests", source="github", count=24),
                SupportingSignal(kind="doc_updates", source="notion", count=5),
            ),
        )

    def test_metrics_slide_carries_corroboration_footnote(self):
        slides = presentation._build_slides(self._signals_report(), DeckStyle())
        metrics = next(s for s in slides if s["type"] == "metrics")
        assert metrics["footnote"] == "Corroborated by 24 merged PRs and 5 doc updates"

    def test_no_signals_no_footnote(self):
        slides = presentation._build_slides(_report(), DeckStyle())
        metrics = next(s for s in slides if s["type"] == "metrics")
        assert "footnote" not in metrics

    def test_footnote_flows_into_html(self):
        html = presentation.build_presentation_html(self._signals_report())
        assert "Corroborated by 24 merged PRs and 5 doc updates" in html
        assert "footnote" in html  # CSS class + renderer hook present

    def test_include_signals_false_drops_footnote(self):
        slides = presentation._build_slides(self._signals_report(), DeckStyle(include_signals=False))
        metrics = next(s for s in slides if s["type"] == "metrics")
        assert "footnote" not in metrics


class TestDeckStyleSlides:
    """DeckStyle-driven slide composition — layout, caps, toggles."""

    def _themes_report(self, n=5, bullets=1):
        from dataclasses import replace

        return replace(
            _report(), themes=tuple((f"Theme {i}", tuple(f"outcome {i}.{j}" for j in range(bullets))) for i in range(n))
        )

    def test_default_style_matches_no_style(self):
        assert presentation._build_slides(_report(), DeckStyle()) == presentation._build_slides(
            _report(), presentation.DeckStyle()
        )

    def test_compact_layout_chunks_themes_into_card_slides(self):
        slides = presentation._build_slides(self._themes_report(5), DeckStyle(layout="compact", content_fit="tight"))
        cards = [s for s in slides if s["type"] == "cards"]
        assert [s["title"] for s in cards] == ["Outcomes (1/2)", "Outcomes (2/2)"]
        assert [len(s["cards"]) for s in cards] == [4, 1]
        assert cards[0]["cards"][0][0] == "Theme 0"
        # No per-theme "list" slides remain — only the Highlights list survives.
        assert all(s["title"] == "Highlights" for s in slides if s["type"] == "list")

    def test_compact_expand_coalesces_short_themes_onto_one_slide(self):
        slides = presentation._build_slides(self._themes_report(5), DeckStyle(layout="compact"))
        cards = [s for s in slides if s["type"] == "cards"]
        # Content-sized packing: 5 one-line cards share a single plain-titled slide.
        assert [s["title"] for s in cards] == ["Outcomes"]
        assert [len(s["cards"]) for s in cards] == [5]
        assert "wide" not in cards[0]

    def test_compact_expand_lone_card_slide_is_wide(self):
        # Each theme fills most of one half-width card, so two pair on slide 1
        # and the third lands alone — the lone card must go full-width.
        long = (
            "Introduced a break-glass process for administrative access so elevated "
            "permissions are granted on demand and time-limited rather than permanently assigned."
        )
        from dataclasses import replace

        report = replace(_report(), themes=tuple((f"Huge {i}", tuple(long for _ in range(6))) for i in range(3)))
        slides = presentation._build_slides(report, DeckStyle(layout="compact"))
        cards = [s for s in slides if s["type"] == "cards"]
        lone = [s for s in cards if len(s["cards"]) == 1]
        assert lone and all(s.get("wide") is True for s in lone)

    def test_compact_single_chunk_plain_title(self):
        slides = presentation._build_slides(self._themes_report(3), DeckStyle(layout="compact", content_fit="tight"))
        cards = [s for s in slides if s["type"] == "cards"]
        assert [s["title"] for s in cards] == ["Outcomes"]

    def test_max_bullets_caps_theme_and_highlight_items(self):
        from dataclasses import replace

        report = replace(
            _report(),
            themes=(("Big", tuple(f"item {i}" for i in range(8))),),
            highlights=tuple(f"win {i}" for i in range(8)),
        )
        slides = presentation._build_slides(report, DeckStyle(max_bullets=3, content_fit="tight"))
        lists = [s for s in slides if s["type"] == "list"]
        for s in lists:
            assert len(s["items"]) == 4
            assert s["items"][-1] == "… and 5 more"

    def test_expand_paginates_lists_with_i_of_n_titles(self):
        from dataclasses import replace

        report = replace(
            _report(),
            themes=(("Big", tuple(f"item {i}" for i in range(8))),),
            highlights=tuple(f"win {i}" for i in range(8)),
        )
        slides = presentation._build_slides(report, DeckStyle(max_bullets=3))
        lists = [s for s in slides if s["type"] == "list"]
        assert [s["title"] for s in lists] == [
            "Big (1/3)",
            "Big (2/3)",
            "Big (3/3)",
            "Highlights (1/3)",
            "Highlights (2/3)",
            "Highlights (3/3)",
        ]
        assert [b for s in lists[:3] for b in s["items"]] == [f"item {i}" for i in range(8)]
        assert all("… and" not in b for s in lists for b in s["items"])

    def test_toggles_drop_highlights_and_thanks(self):
        slides = presentation._build_slides(_report(), DeckStyle(include_highlights=False, include_thanks=False))
        types = [s["type"] for s in slides]
        assert types[-1] != "thanks" and "thanks" not in types
        assert all(s.get("title") != "Highlights" for s in slides)


class TestDeckStyleHtml:
    """DeckStyle-driven CSS/JS output — overrides only when deviating from default."""

    def test_default_style_appends_no_override_css(self):
        html = presentation.build_presentation_html(_report())
        assert ".slide h1 { color:" not in html
        assert "Georgia" not in html
        assert '"slide_numbers": false, "footer": ""' in html

    def test_custom_hex_title_color(self):
        html = presentation.build_presentation_html(_report(), style=DeckStyle(title_color="#ff0000"))
        assert ".slide h1 { color: #ff0000; }" in html

    def test_role_heading_color_resolves_against_palette(self):
        from yeaboi.reporting.themes import get_palette

        html = presentation.build_presentation_html(_report(), theme="aurora", style=DeckStyle(heading_color="accent2"))
        accent2 = get_palette("aurora")["accent2"]
        assert f"h2 {{ background: linear-gradient(90deg, {accent2}, {accent2}); }}" in html

    def test_font_preset_css_stack(self):
        html = presentation.build_presentation_html(_report(), style=DeckStyle(font_family="classic"))
        assert 'body { font-family: Georgia, "Times New Roman", Times, serif; }' in html

    def test_font_scale_redeclares_clamp_sizes(self):
        html = presentation.build_presentation_html(_report(), style=DeckStyle(font_scale="large"))
        assert "h1 { font-size: clamp(2.30rem, 6.90vw, 4.60rem); }" in html

    def test_footer_and_slide_numbers_injected_via_style_json(self):
        html = presentation.build_presentation_html(
            _report(), style=DeckStyle(footer_text="ACME Corp", slide_numbers=True)
        )
        assert '"slide_numbers": true' in html
        assert '"footer": "ACME Corp"' in html

    def test_footer_text_is_json_escaped_not_raw_markup(self):
        html = presentation.build_presentation_html(
            _report(), style=DeckStyle(footer_text="</script><img onerror=alert(1)>")
        )
        assert "</script><img" not in html  # cannot break out of the script element

    def test_styled_deck_still_self_contained(self):
        import re

        html = presentation.build_presentation_html(
            _report(),
            style=DeckStyle(font_family="mono", layout="compact", slide_numbers=True, footer_text="f"),
        )
        stripped = re.sub(r"data:image/png;base64,[A-Za-z0-9+/=]+", "", html)
        assert "http://" not in stripped and "https://" not in stripped
        assert "cdn" not in stripped.lower()
