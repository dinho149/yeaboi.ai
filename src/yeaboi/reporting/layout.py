"""Content-aware slide planning shared by the .pptx and HTML deck renderers.

pptx text frames neither clip nor shrink overflowing text, so the renderers must
budget content themselves from estimated wrapped-line counts. This module is the
single source of those estimates *and* of the "expand" packing plan: which theme
cards land on which Outcomes slide, with every bullet kept (long themes paginate
into "(cont.)" cards) and slides added as needed instead of trimming to
"… and N more". The planner returns content only — no geometry — and the pptx
renderer recomputes heights by calling the same functions the planner used, so
the two can never drift. The HTML deck consumes the same plan; its viewport fit
is best-effort (CSS ``clamp()`` sizes absorb small overshoots) but the pptx-side
budget is conservative enough to keep both surfaces comfortable.

Everything here is pure and deterministic: no I/O, no LLM.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from yeaboi.reporting.style import FONT_SCALES, DeckStyle

if TYPE_CHECKING:
    from yeaboi.agent.state import DeliveryReport

# 16:9 deck geometry, in inches (python-pptx defaults to 4:3).
SLIDE_W_IN = 13.333
SLIDE_H_IN = 7.5
MARGIN_IN = 0.9

# Text-fitting heuristics: single-spaced pptx text occupies ≈ 1.25 × font size
# per line; the average proportional-font glyph is ≈ 0.55 × font size wide.
LINE_SPACING = 1.25
AVG_CHAR_W = 0.55

# Compact-layout card geometry (mirrors the HTML deck's 2-column card grid).
CARD_GAP_IN = 0.3
CONTENT_TOP_IN = 2.0  # heading band above the card area
CONTENT_H_IN = SLIDE_H_IN - CONTENT_TOP_IN - 0.5  # breathing room below
CARD_MIN_H_IN = 1.1  # cosmetic floor, applied in packing AND render so budgets agree
MAX_CARDS_PER_SLIDE = 6  # 3 rows of 2 — content-sized cards earn a third row
CARD_TITLE_PT = 16
CARD_BULLET_PT = 12
CARD_TEXT_INSET_IN = 0.24  # 0.12" left + right text-frame margins
CARD_PAD_V_IN = 0.18  # 0.1" top + 0.08" bottom text-frame margins

# Full-slide bullet lists (detailed themes / Highlights): add_bullets geometry.
LIST_BULLET_PT = 18
LIST_TOP_IN = 2.0
LIST_H_IN = SLIDE_H_IN - LIST_TOP_IN - 0.6

_CARD_W_IN = (SLIDE_W_IN - 2 * MARGIN_IN - CARD_GAP_IN) / 2  # half-width card
TIGHT_CARDS_PER_SLIDE = 4  # the fixed 2×2 grid of content_fit="tight" (both renderers)


def est_lines(text: str, width_in: float, size_pt: float, scale: float) -> int:
    """Estimate how many lines ``text`` wraps to in a ``width_in``-wide frame."""
    chars_per_line = max(10, int(width_in * 72 / (AVG_CHAR_W * size_pt * scale)))
    return max(1, math.ceil(len(text) / chars_per_line))


def fit_bullets(
    items,
    *,
    width_in: float,
    height_in: float,
    size_pt: float,
    scale: float,
    max_items: int | None = None,
    space_after_pt: float = 10.0,
) -> list[str]:
    """Trim ``items`` so their estimated rendered height fits ``height_in`` inches.

    Applies both the user's ``max_items`` cap and the geometric budget, appending
    one "… and N more" marker (with a reserved line) when anything is trimmed.
    The first item always renders, even when the estimate says it overflows.
    """
    items = [str(item) for item in items]
    limit = len(items) if max_items is None else min(max_items, len(items))
    line_h = LINE_SPACING * size_pt * scale / 72
    para_h = space_after_pt / 72
    shown: list[str] = []
    used = 0.0
    for idx, item in enumerate(items[:limit]):
        est = est_lines(item, width_in, size_pt, scale) * line_h + para_h
        reserve = line_h if idx + 1 < len(items) else 0.0  # room for the marker line
        if shown and used + est + reserve > height_in:
            break
        used += est
        shown.append(item)
    if len(shown) < len(items):
        shown.append(f"… and {len(items) - len(shown)} more")
    return shown


def paginate_bullets(
    items,
    *,
    width_in: float,
    height_in: float,
    size_pt: float,
    scale: float,
    max_items: int | None = None,
    space_after_pt: float = 10.0,
) -> list[list[str]]:
    """Split ``items`` into successive pages instead of trimming — never drops.

    Each page holds at most ``max_items`` bullets (the user's per-card/slide page
    size) and fits the ``height_in`` budget by line estimate; every page has at
    least one item, so a single over-budget bullet still renders (top-anchored
    frames degrade to a few pixels of bottom overflow, not data loss).
    """
    items = [str(item) for item in items]
    line_h = LINE_SPACING * size_pt * scale / 72
    para_h = space_after_pt / 72
    pages: list[list[str]] = []
    page: list[str] = []
    used = 0.0
    for item in items:
        est = est_lines(item, width_in, size_pt, scale) * line_h + para_h
        full = max_items is not None and len(page) >= max_items
        if page and (full or used + est > height_in):
            pages.append(page)
            page = []
            used = 0.0
        page.append(item)
        used += est
    if page:
        pages.append(page)
    return pages


def card_height(title: str, bullets, *, width_in: float, scale: float) -> float:
    """Estimated rendered height of one outcome card — the exact add_card math."""
    inner_w = width_in - CARD_TEXT_INSET_IN
    title_h = est_lines(title, inner_w, CARD_TITLE_PT, scale) * (LINE_SPACING * CARD_TITLE_PT * scale / 72) + 6 / 72
    body_h = sum(
        est_lines(str(b), inner_w, CARD_BULLET_PT, scale) * (LINE_SPACING * CARD_BULLET_PT * scale / 72) + 4 / 72
        for b in bullets
    )
    return CARD_PAD_V_IN + title_h + body_h


@dataclass(frozen=True)
class CardPlan:
    """One rendered card: a (possibly continued) theme title and its bullets."""

    title: str
    bullets: tuple[str, ...]
    full_width: bool = False


@dataclass(frozen=True)
class SlidePlan:
    """One Outcomes slide's worth of cards, in reading order."""

    cards: tuple[CardPlan, ...]


def plan_outcome_slides(themes, *, scale: float = 1.0, max_bullets: int = 6) -> list[SlidePlan]:
    """Pack outcome themes into card slides with every bullet kept (expand fit).

    Themes stay in order (the narrative order matters; no masonry reshuffling).
    A theme whose bullets exceed one half-width card paginates into "(cont.)"
    cards — ``max_bullets`` acts as a page size, not a truncation cap. Cards are
    then packed greedily into 2-column rows, each slide taking rows while the
    content-height budget holds; a slide left with a single card renders it
    full-width instead of leaving an empty column.
    """
    # Budget pages against the longer "(cont.)" title so continuation cards can
    # never overflow the height their bullets were paginated for.
    cards: list[CardPlan] = []
    for ttitle, outcomes in themes:
        cont_title = f"{ttitle} (cont.)"
        title_h = card_height(cont_title, (), width_in=_CARD_W_IN, scale=scale)
        pages = paginate_bullets(
            outcomes,
            width_in=_CARD_W_IN - CARD_TEXT_INSET_IN,
            height_in=CONTENT_H_IN - title_h,
            size_pt=CARD_BULLET_PT,
            scale=scale,
            max_items=max_bullets,
            space_after_pt=4.0,
        )
        for idx, page in enumerate(pages):
            cards.append(CardPlan(title=ttitle if idx == 0 else cont_title, bullets=tuple(page)))

    # Greedy row packing: rows of two, row height = the taller card, a new slide
    # whenever the next card would push past the content budget or the card cap.
    slides: list[list[CardPlan]] = []
    current: list[CardPlan] = []
    row_heights: list[float] = []
    for card in cards:
        # Same floor the renderer applies — pack with rendered heights so a slide
        # full of floored minimum-height cards can't overrun the budget.
        h = max(CARD_MIN_H_IN, card_height(card.title, card.bullets, width_in=_CARD_W_IN, scale=scale))
        if current:
            if len(current) % 2 == 1:  # extend the open row (may raise its height)
                total = sum(row_heights[:-1]) + max(row_heights[-1], h) + CARD_GAP_IN * (len(row_heights) - 1)
                fits = total <= CONTENT_H_IN and len(current) < MAX_CARDS_PER_SLIDE
                if fits:
                    current.append(card)
                    row_heights[-1] = max(row_heights[-1], h)
                    continue
            else:  # start a new row on this slide
                total = sum(row_heights) + h + CARD_GAP_IN * len(row_heights)
                if total <= CONTENT_H_IN and len(current) < MAX_CARDS_PER_SLIDE:
                    current.append(card)
                    row_heights.append(h)
                    continue
            slides.append(current)
        current = [card]
        row_heights = [h]
    if current:
        slides.append(current)

    return [
        SlidePlan(cards=tuple(replace(c, full_width=True) for c in group) if len(group) == 1 else tuple(group))
        for group in slides
    ]


def plan_list_slides(
    title: str, items, *, scale: float = 1.0, max_bullets: int = 6, size_pt: float = LIST_BULLET_PT
) -> list[tuple[str, tuple[str, ...]]]:
    """Paginate a full-slide bullet list (detailed theme / Highlights) — expand fit.

    Returns ``[(slide_title, bullets), …]``; a single fitting page keeps the plain
    title, overflow pages get "(i/n)" suffixes ("Highlights (2/2)").
    """
    pages = paginate_bullets(
        items,
        width_in=SLIDE_W_IN - 2 * MARGIN_IN,
        height_in=LIST_H_IN,
        size_pt=size_pt,
        scale=scale,
        max_items=max_bullets,
    )
    if len(pages) <= 1:
        return [(title, tuple(page)) for page in pages]
    return [(f"{title} ({idx}/{len(pages)})", tuple(page)) for idx, page in enumerate(pages, start=1)]


def count_fit_slides(report: DeliveryReport, style: DeckStyle) -> tuple[int, int]:
    """Slide counts for the sections ``content_fit`` affects: (tight, expand).

    The TUI export offer's single call: when the expand count exceeds the tight
    count, the difference is exactly the "extra slides" the user is offered.
    Honors layout, include_highlights, font scale, and max_bullets.
    """
    scale = FONT_SCALES.get(style.font_scale, 1.0)
    tight = 0
    expand = 0
    if report.themes:
        if style.layout == "compact":
            tight += math.ceil(len(report.themes) / TIGHT_CARDS_PER_SLIDE)
            expand += len(plan_outcome_slides(report.themes, scale=scale, max_bullets=style.max_bullets))
        else:
            tight += len(report.themes)
            expand += sum(
                len(plan_list_slides(t, outcomes, scale=scale, max_bullets=style.max_bullets))
                for t, outcomes in report.themes
            )
    if report.highlights and style.include_highlights:
        tight += 1
        expand += len(plan_list_slides("Highlights", report.highlights, scale=scale, max_bullets=style.max_bullets))
    return tight, expand
