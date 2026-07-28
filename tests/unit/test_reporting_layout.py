"""Unit tests for reporting/layout — content-aware slide planning."""

from yeaboi.agent.state import DeliveryReport
from yeaboi.reporting import layout
from yeaboi.reporting.layout import (
    CONTENT_H_IN,
    MAX_CARDS_PER_SLIDE,
    CardPlan,
    card_height,
    count_fit_slides,
    est_lines,
    fit_bullets,
    paginate_bullets,
    plan_list_slides,
    plan_outcome_slides,
)
from yeaboi.reporting.style import DeckStyle

_LONG = (
    "Introduced a break-glass process for administrative access so elevated permissions "
    "are granted on demand and time-limited rather than permanently assigned."
)
_SHORT = "Shipped SSO."


class TestPaginateBullets:
    def test_single_fitting_page(self):
        pages = paginate_bullets([_SHORT, _SHORT], width_in=5.0, height_in=4.0, size_pt=12, scale=1.0)
        assert pages == [[_SHORT, _SHORT]]

    def test_never_drops_an_item(self):
        items = [f"{_LONG} #{i}" for i in range(12)]
        pages = paginate_bullets(items, width_in=5.0, height_in=2.0, size_pt=12, scale=1.0)
        assert [b for page in pages for b in page] == items
        assert len(pages) > 1

    def test_max_items_acts_as_page_size(self):
        pages = paginate_bullets([_SHORT] * 7, width_in=5.0, height_in=10.0, size_pt=12, scale=1.0, max_items=3)
        assert [len(p) for p in pages] == [3, 3, 1]

    def test_every_page_has_at_least_one_item(self):
        # A single bullet taller than the budget still lands on its own page.
        pages = paginate_bullets([_LONG * 4], width_in=3.0, height_in=0.3, size_pt=12, scale=1.0)
        assert pages == [[_LONG * 4]]

    def test_no_marker_lines_ever(self):
        pages = paginate_bullets([_LONG] * 8, width_in=5.0, height_in=1.5, size_pt=12, scale=1.0)
        assert all("more" not in b for page in pages for b in page)


class TestCardHeight:
    def test_monotonic_in_bullets(self):
        one = card_height("Theme", [_LONG], width_in=5.6, scale=1.0)
        three = card_height("Theme", [_LONG] * 3, width_in=5.6, scale=1.0)
        assert three > one > 0

    def test_monotonic_in_scale(self):
        small = card_height("Theme", [_LONG] * 2, width_in=5.6, scale=1.0)
        large = card_height("Theme", [_LONG] * 2, width_in=5.6, scale=1.15)
        assert large > small

    def test_wider_card_is_shorter(self):
        half = card_height("Theme", [_LONG] * 3, width_in=5.6, scale=1.0)
        full = card_height("Theme", [_LONG] * 3, width_in=11.5, scale=1.0)
        assert full < half


class TestPlanOutcomeSlides:
    def _titles_and_bullets(self, plan):
        return [(c.title, list(c.bullets)) for s in plan for c in s.cards]

    def test_order_preserved_and_nothing_dropped(self):
        themes = [(f"Theme {i}", tuple(f"{_LONG} {i}.{j}" for j in range(4))) for i in range(5)]
        plan = plan_outcome_slides(themes)
        flat = [b for s in plan for c in s.cards for b in c.bullets]
        assert flat == [o for _t, outcomes in themes for o in outcomes]
        base_titles = [c.title.removesuffix(" (cont.)") for s in plan for c in s.cards]
        assert base_titles == sorted(base_titles, key=lambda t: int(t.split()[-1]))

    def test_short_themes_coalesce_onto_one_slide(self):
        themes = [(f"T{i}", (_SHORT, _SHORT)) for i in range(5)]
        plan = plan_outcome_slides(themes)
        assert len(plan) == 1
        assert len(plan[0].cards) == 5

    def test_dense_theme_paginates_into_cont_cards(self):
        themes = [("Big theme", tuple(f"{_LONG} #{i}" for i in range(14)))]
        plan = plan_outcome_slides(themes, max_bullets=4)
        cards = self._titles_and_bullets(plan)
        assert cards[0][0] == "Big theme"
        assert all(t == "Big theme (cont.)" for t, _b in cards[1:])
        assert sum(len(b) for _t, b in cards) == 14
        assert all(len(b) <= 4 for _t, b in cards)

    def test_card_cap_per_slide(self):
        themes = [(f"T{i}", (_SHORT,)) for i in range(MAX_CARDS_PER_SLIDE + 3)]
        plan = plan_outcome_slides(themes)
        assert all(len(s.cards) <= MAX_CARDS_PER_SLIDE for s in plan)
        assert len(plan) == 2

    def test_every_slide_fits_the_content_budget(self):
        themes = [(f"Theme {i}", tuple(f"{_LONG} {i}.{j}" for j in range(6))) for i in range(6)]
        plan = plan_outcome_slides(themes, scale=1.15)
        for s in plan:
            heights = [card_height(c.title, c.bullets, width_in=layout._CARD_W_IN, scale=1.15) for c in s.cards]
            rows = [heights[i : i + 2] for i in range(0, len(heights), 2)]
            total = sum(max(r) for r in rows) + layout.CARD_GAP_IN * (len(rows) - 1)
            assert total <= CONTENT_H_IN + 1e-9

    def test_lone_card_slide_is_full_width(self):
        # Two tall cards fill the first slide's row; the third lands alone.
        themes = [(f"Huge {i}", tuple(f"{_LONG} #{j}" for j in range(6))) for i in range(3)]
        plan = plan_outcome_slides(themes)
        lone = [s for s in plan if len(s.cards) == 1]
        assert lone and all(s.cards[0].full_width for s in lone)
        multi = [s for s in plan if len(s.cards) > 1]
        assert all(not c.full_width for s in multi for c in s.cards)


class TestPlanListSlides:
    def test_fitting_list_keeps_plain_title(self):
        assert plan_list_slides("Highlights", [_SHORT, _SHORT]) == [("Highlights", (_SHORT, _SHORT))]

    def test_overflow_paginates_with_i_of_n_titles(self):
        items = [f"{_LONG} #{i}" for i in range(9)]
        slides = plan_list_slides("Big", items, max_bullets=3)
        assert [t for t, _b in slides] == ["Big (1/3)", "Big (2/3)", "Big (3/3)"]
        assert [b for _t, page in slides for b in page] == items

    def test_empty_items_produce_no_slides(self):
        assert plan_list_slides("Highlights", []) == []


class TestCountFitSlides:
    def _report(self, n_themes=5, bullets=6, highlights=3):
        return DeliveryReport(
            period_label="Last month",
            themes=tuple((f"Theme {i}", tuple(f"{_LONG} {i}.{j}" for j in range(bullets))) for i in range(n_themes)),
            highlights=tuple(f"{_LONG} h{i}" for i in range(highlights)),
        )

    def test_compact_counts(self):
        tight, expand = count_fit_slides(self._report(), DeckStyle(layout="compact"))
        # tight: ceil(5/4)=2 card slides + 1 highlights; expand must cover everything.
        assert tight == 3
        assert expand > tight

    def test_detailed_counts(self):
        tight, expand = count_fit_slides(self._report(n_themes=2, bullets=2, highlights=2), DeckStyle())
        assert tight == 3  # 2 theme slides + highlights
        assert expand == 3  # everything already fits — no extra slides to offer

    def test_include_highlights_false_excludes_them(self):
        with_h = count_fit_slides(self._report(), DeckStyle(layout="compact"))
        without = count_fit_slides(self._report(), DeckStyle(layout="compact", include_highlights=False))
        assert without[0] == with_h[0] - 1

    def test_larger_font_scale_needs_more_expand_slides(self):
        base = count_fit_slides(self._report(), DeckStyle(layout="compact"))
        large = count_fit_slides(self._report(), DeckStyle(layout="compact", font_scale="large"))
        assert large[1] >= base[1]
        assert large[0] == base[0]  # tight count is geometry-free

    def test_empty_report_is_zero_zero(self):
        assert count_fit_slides(DeliveryReport(period_label="x"), DeckStyle(layout="compact")) == (0, 0)


class TestFitBullets:
    def test_fit_bullets_still_trims_with_marker(self):
        fitted = fit_bullets([_LONG] * 8, width_in=5.0, height_in=1.5, size_pt=12, scale=1.0)
        assert fitted[-1].startswith("… and ")
        assert len(fitted) < 9

    def test_est_lines_floor(self):
        assert est_lines("", 5.0, 12, 1.0) == 1
        assert isinstance(CardPlan("t", ("b",)), CardPlan)
