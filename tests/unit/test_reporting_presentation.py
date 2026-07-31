"""Unit tests for reporting/presentation — the self-contained slide deck.

Two halves, matching what the module now is. :func:`_build_slides` and
:func:`deck_payload` are asserted on directly, because that is where every
decision lives; the HTML side is only the seam — one document, one bundle, one
JSON island — and the bundle's own deployment guards live in
``test_web_assets.py``. The deck's *behaviour* is tested in
``frontend/src/deck/*.test.tsx``.
"""

import json

from tests._pages import island
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


class TestSectionsAndPagination:
    """The eyebrow's two inputs: which act of the deck, and where in a run."""

    def test_every_content_slide_names_its_section(self):
        slides = presentation._build_slides(_report(), DeckStyle())
        sections = {s["type"]: s.get("section") for s in slides}
        assert sections == {
            "title": None,  # the bookends belong to neither act
            "summary": "Overview",
            "metrics": "Overview",
            "list": "Delivery",
            "thanks": None,
        }

    def test_a_section_is_never_just_the_heading_restated(self):
        """The whole reason the eyebrow carries the section and not a number.

        If a section label ever equals its own slide title, the eyebrow becomes
        the heading in smaller type and the device is pure decoration.
        """
        for slide in presentation._build_slides(_report(), DeckStyle()):
            assert slide.get("section") != slide.get("title")

    def test_pagination_is_a_pair_not_a_title_suffix(self):
        from dataclasses import replace

        report = replace(_report(), themes=(("Big", tuple(f"item {i}" for i in range(8))),), highlights=())
        slides = presentation._build_slides(report, DeckStyle(max_bullets=3))
        lists = [s for s in slides if s["type"] == "list"]
        # The title stays the theme's own name at every size; "(2/3)" belongs in
        # the eyebrow, not in the largest type on a projected slide.
        assert [s["title"] for s in lists] == ["Big", "Big", "Big"]
        assert [s["page"] for s in lists] == [[1, 3], [2, 3], [3, 3]]

    def test_unpaginated_runs_still_carry_a_single_page_pair(self):
        """`[1, 1]` rather than an absent key — the client hides it, and one
        shape for the field beats two."""
        slides = presentation._build_slides(_report(), DeckStyle())
        assert all(s["page"] == [1, 1] for s in slides if s["type"] == "list")


class TestDeckPayload:
    def test_carries_the_deck_and_its_context(self):
        data = presentation.deck_payload(_report())
        assert data["project"] == "Acme Portal"
        assert data["period"].startswith("Last month (~2 sprints)")
        assert "2026-06-15 to 2026-07-13" in data["period"]
        assert "Sprint 11, Sprint 12" in data["period"]
        assert data["theme"] == "midnight"
        assert data["slides"][0]["type"] == "title"

    def test_ships_every_palette_whole(self):
        """An exported deck has no server to look a theme name up from."""
        from yeaboi.reporting.themes import BUILTIN_PALETTES

        palettes = presentation.deck_payload(_report())["palettes"]
        assert list(palettes) == list(BUILTIN_PALETTES)
        assert palettes["aurora"]["accent"] == "#28c2a0"
        assert set(palettes["midnight"]) == {"bg1", "bg2", "fg", "muted", "accent", "accent2"}

    def test_invalid_theme_falls_back_to_midnight(self):
        assert presentation.deck_payload(_report(), theme="nonsense")["theme"] == "midnight"

    def test_style_colors_stay_unresolved(self):
        """Resolved client-side against whichever palette is showing.

        Baking them in was a live bug: pressing T re-themed everything except
        the heading colour the user had chosen, which stayed on the palette the
        deck happened to open with.
        """
        style = DeckStyle(heading_color="accent2", title_color="#ff0000")
        data = presentation.deck_payload(_report(), theme="aurora", style=style)
        assert data["style"]["headingColor"] == "accent2"
        assert data["style"]["titleColor"] == "#ff0000"

    def test_font_preset_and_scale_are_resolved(self):
        data = presentation.deck_payload(_report(), style=DeckStyle(font_family="classic", font_scale="large"))
        assert data["style"]["fontFamily"] == 'Georgia, "Times New Roman", Times, serif'
        assert data["style"]["fontScale"] == 1.15

    def test_a_hand_built_style_with_a_bogus_preset_still_exports(self):
        """DeckStyle is a plain dataclass — only ``style_from_dict`` validates.

        A KeyError here would take down a whole export over a typo in a field
        that only changes a font.
        """
        data = presentation.deck_payload(_report(), style=DeckStyle(font_family="comic"))
        assert data["style"]["fontFamily"] == "var(--font-sans)"

    def test_default_font_preset_names_the_design_token(self):
        """So a deck picks up Geist wherever the rest of the product does."""
        assert presentation.deck_payload(_report())["style"]["fontFamily"] == "var(--font-sans)"

    def test_footer_and_slide_numbers(self):
        data = presentation.deck_payload(_report(), style=DeckStyle(footer_text="ACME Corp", slide_numbers=True))
        assert data["style"]["footer"] == "ACME Corp"
        assert data["style"]["slideNumbers"] is True

    def test_payload_has_no_secrets(self):
        flat = json.dumps(presentation.deck_payload(_report())).lower()
        for forbidden in ("token", "secret", "password"):
            assert forbidden not in flat

    def test_thanks_title_stays_the_fixed_string_the_wordmark_needs(self):
        """The client sets it in the block-glyph face, which cannot wrap.

        Three cells per character, two rows, no line breaking — so this has to
        stay ours and stay short. The renderer falls back to a plain heading
        past 14 characters, but that fallback should never fire from here.
        """
        thanks = presentation._build_slides(_report(), DeckStyle())[-1]
        assert thanks["title"] == "Thank you"


class TestBuildPresentationHtml:
    """The seam only — one document, one bundle, one island.

    The bundle's own constraints (no eval, no external URL, IIFE not ESM) are
    parametrized over every entry in ``test_web_assets.py``, so scanning the
    minified output again here would only re-implement it worse.
    """

    def test_is_one_self_contained_document(self):
        html = presentation.build_presentation_html(_report())
        assert html.startswith("<!DOCTYPE html>")
        assert "<style>" in html and "<script>" in html
        assert "<link" not in html
        assert '<script type="module"' not in html

    def test_mounts_into_root_and_points_noscript_at_the_report(self):
        html = presentation.build_presentation_html(_report())
        assert '<div id="root">' in html
        noscript = html[html.index("<noscript>") : html.index("</noscript>")]
        assert "HTML report" in noscript  # the sibling file the same export wrote

    def test_declares_the_reporting_accent_and_the_deck_palette(self):
        html = presentation.build_presentation_html(_report(), theme="aurora")
        assert 'data-mode="reporting"' in html
        # Not `data-theme`: that attribute already means one of the five *site*
        # palettes, and a deck theme named "midnight" is a different thing.
        assert 'data-deck-theme="aurora"' in html
        assert "data-theme=" not in html.split("<style>")[0]

    def test_title_names_the_project(self):
        html = presentation.build_presentation_html(_report())
        assert "<title>Acme Portal — Delivery Report</title>" in html

    def test_island_parses_and_holds_the_payload(self):
        boot = island(presentation.build_presentation_html(_report()))
        assert boot["slides"][0]["type"] == "title"
        assert boot["theme"] == "midnight"

    def test_untrusted_text_cannot_close_the_script_it_rides_in(self):
        r = DeliveryReport(
            period_label="Last sprint",
            headline="hi",
            themes=(("T", ("</script><img src=x onerror=alert(1)>",)),),
            emoji_theme=(("themes", "🧩"),),
        )
        html = presentation.build_presentation_html(r)
        assert "</script><img" not in html
        assert "\\u003c/script" in html
        listed = next(s for s in island(html)["slides"] if s["type"] == "list")
        assert listed["items"] == ["</script><img src=x onerror=alert(1)>"]

    def test_project_name_cannot_escape_the_title_element(self):
        html = presentation.build_presentation_html(
            DeliveryReport(period_label="x", project_name="</title><script>alert(1)</script>")
        )
        assert "</title><script>alert(1)" not in html
        assert "&lt;/title&gt;" in html


class TestNoGeneratedStylesheetSurvives:
    """The deck was the last place Python wrote CSS. It must stay gone.

    Both markers below are load-bearing rather than incidental: `_style_css`
    emitted bare rules like ``.slide h1 { color: … }`` into the document, and
    `_custom_theme_css` emitted a ``[data-theme="name"] { --bg1: … }`` block per
    user palette. Either reappearing means a value that should be data has been
    turned back into a stylesheet.
    """

    def test_no_hand_written_rules_are_appended(self):
        html = presentation.build_presentation_html(
            _report(),
            theme="sunset",
            style=DeckStyle(title_color="#ff0000", heading_color="accent2", font_scale="large"),
        )
        assert ".slide h1 { color:" not in html
        assert "--bg1:" not in html

    def test_the_generators_are_deleted_not_merely_unused(self):
        assert not hasattr(presentation, "_style_css")
        assert not hasattr(presentation, "_custom_theme_css")
        assert not hasattr(presentation, "_CSS")
        assert not hasattr(presentation, "_JS")


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

    def test_custom_palette_ships_in_the_payload(self, monkeypatch):
        self._with_custom(monkeypatch)
        data = presentation.deck_payload(_report(), theme="corporate")
        assert data["theme"] == "corporate"
        assert data["palettes"]["corporate"]["bg1"] == "#101418"

    def test_custom_palette_joins_the_t_key_cycle_last(self, monkeypatch):
        self._with_custom(monkeypatch)
        data = presentation.deck_payload(_report(), theme="midnight")
        assert list(data["palettes"]) == ["midnight", "aurora", "sunset", "mono", "corporate"]

    def test_unknown_theme_still_falls_back_to_midnight(self, monkeypatch):
        self._with_custom(monkeypatch)
        assert presentation.deck_payload(_report(), theme="nope")["theme"] == "midnight"


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

    def test_footnote_flows_into_theisland(self):
        boot = island(presentation.build_presentation_html(self._signals_report()))
        metrics = next(s for s in boot["slides"] if s["type"] == "metrics")
        assert metrics["footnote"] == "Corroborated by 24 merged PRs and 5 doc updates"

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
        assert [s["title"] for s in cards] == ["Outcomes", "Outcomes"]
        assert [s["page"] for s in cards] == [[1, 2], [2, 2]]
        assert [len(s["cards"]) for s in cards] == [4, 1]
        assert cards[0]["cards"][0][0] == "Theme 0"
        # No per-theme "list" slides remain — only the Highlights list survives.
        assert all(s["title"] == "Highlights" for s in slides if s["type"] == "list")

    def test_compact_expand_coalesces_short_themes_onto_one_slide(self):
        slides = presentation._build_slides(self._themes_report(5), DeckStyle(layout="compact"))
        cards = [s for s in slides if s["type"] == "cards"]
        # Content-sized packing: 5 one-line cards share a single slide.
        assert [s["page"] for s in cards] == [[1, 1]]
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

    def test_expand_paginates_lists_without_dropping_anything(self):
        from dataclasses import replace

        report = replace(
            _report(),
            themes=(("Big", tuple(f"item {i}" for i in range(8))),),
            highlights=tuple(f"win {i}" for i in range(8)),
        )
        slides = presentation._build_slides(report, DeckStyle(max_bullets=3))
        lists = [s for s in slides if s["type"] == "list"]
        assert [(s["title"], s["page"]) for s in lists] == [
            ("Big", [1, 3]),
            ("Big", [2, 3]),
            ("Big", [3, 3]),
            ("Highlights", [1, 3]),
            ("Highlights", [2, 3]),
            ("Highlights", [3, 3]),
        ]
        assert [b for s in lists[:3] for b in s["items"]] == [f"item {i}" for i in range(8)]
        assert all("… and" not in b for s in lists for b in s["items"])

    def test_toggles_drop_highlights_and_thanks(self):
        slides = presentation._build_slides(_report(), DeckStyle(include_highlights=False, include_thanks=False))
        types = [s["type"] for s in slides]
        assert types[-1] != "thanks" and "thanks" not in types
        assert all(s.get("title") != "Highlights" for s in slides)
