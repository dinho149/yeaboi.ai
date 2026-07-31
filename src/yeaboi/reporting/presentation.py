"""Build a self-contained HTML slide deck from a DeliveryReport.

This is the "presentation" output of Reporting mode: one offline .html file that
opens in any browser — over ``file://``, from an email attachment, off a USB
stick — and presents the delivered work to the business.

**What this module owns is the deck's *content*, not its appearance.** The
slides, the palettes and the style knobs are assembled here into one JSON boot
payload; the rendering is ``frontend/src/deck`` and ships as a committed bundle
that :func:`yeaboi.web.assets.render_page` inlines. That split is why this file
is a fifth of its former size: it used to carry a ``_CSS`` string, a ``_JS``
renderer, and two functions that *generated* CSS rules from Python values — the
last of those in the codebase. A user's custom palette became a
``[data-theme="…"] { … }`` block, and a chosen heading colour became three
selectors resolved against whichever palette the deck happened to open with,
which is why pressing "T" used to re-theme everything except that colour.

The design split the user chose stays exactly as it was: the *wording* — slide
copy, outcome themes, section emojis — comes from the LLM design pass in
engine.py (with a deterministic fallback), and this file only arranges it.

Two properties the deck must keep, both easy to break by accident:

* **Offline.** No external request of any kind, including fonts. Enforced
  statically by ``tests/unit/test_web_assets.py``.
* **Inert.** Ticket and outcome text arrives from a tracker, so it is data all
  the way to the DOM: JSON in a non-executing ``<script type="application/json">``,
  then React text children. Nothing is ever parsed as markup.

# See docs: "Reporting Mode" — presentation output
"""

from __future__ import annotations

import logging
from datetime import datetime

from yeaboi.agent.state import DeliveryReport
from yeaboi.reporting.layout import TIGHT_CARDS_PER_SLIDE, plan_list_slides, plan_outcome_slides
from yeaboi.reporting.style import FONT_PRESETS, FONT_SCALES, DeckStyle, cap_items, summary_points

logger = logging.getLogger(__name__)

# There used to be a `THEMES` tuple here naming the four built-in palettes. It
# was a second copy of `themes.BUILTIN_PALETTES`' keys and nothing outside this
# file read it — `all_palettes()` already returns them in cycle order, built-ins
# first, with the user's own appended.

# How many outcome-theme cards fit on one "compact" tight-fit slide (2×2 grid) —
# shared with the pptx renderer via layout.py.
_CARDS_PER_SLIDE = TIGHT_CARDS_PER_SLIDE

# The deck's two sections, carried in each content slide's mono eyebrow.
#
# They exist because the eyebrow has to say something the heading does not.
# Numbering the slides there would be the obvious filler and would say nothing
# — the counter at the bottom of the screen already does it. Naming the section
# is the information a stakeholder who looked up mid-deck actually wants, and it
# is the same two-act shape every delivery report has: here is the period, here
# is what came out of it.
_OVERVIEW = "Overview"
_DELIVERY = "Delivery"


def _emoji(report: DeliveryReport, slot: str, default: str = "") -> str:
    for s, e in report.emoji_theme:
        if s == slot and e:
            return e
    return default


def _period_line(report: DeliveryReport) -> str:
    """The one-line "when was this" that the title slide and the deck rail share."""
    dates = f"{report.period_start} to {report.period_end}".strip(" to")
    line = report.period_label + (f"  ·  {dates}" if dates else "")
    if report.sprint_names:
        line += f"  ·  {', '.join(report.sprint_names)}"
    return line


def _build_slides(report: DeliveryReport, style: DeckStyle) -> list[dict]:
    """Turn a DeliveryReport into an ordered list of slide dicts for the renderer.

    ``style`` controls the composition: bullet caps, the compact (theme cards)
    layout, and which optional slides/footnotes appear. All text stays as plain
    strings — the client renders it as text children, so untrusted ticket titles
    cannot inject markup.

    Pagination is carried as a ``page: [i, n]`` pair rather than baked into the
    title as "(1/3)". The planners in layout.py still return suffixed titles
    because the .pptx renderer wants them inline, but a projected slide reading
    "Security (2/3)" puts a piece of bookkeeping in the largest type on screen.
    Here it belongs in the eyebrow with the section name.
    """
    slides: list[dict] = []

    slides.append(
        {
            "type": "title",
            "emoji": _emoji(report, "headline", "🚀"),
            "title": report.project_name or "Delivery Report",
            "headline": report.headline,
        }
    )
    if report.executive_summary:
        slides.append(
            {
                "type": "summary",
                "emoji": _emoji(report, "summary", "📋"),
                "section": _OVERVIEW,
                "title": "Executive summary",
                # Sentence-level points, not one prose blob — far more readable
                # projected. The renderer draws one paragraph per point.
                "points": summary_points(report.executive_summary),
            }
        )
    if report.metrics:
        metrics_slide = {
            "type": "metrics",
            "emoji": _emoji(report, "metrics", "📊"),
            "section": _OVERVIEW,
            "title": "By the numbers",
            "metrics": [[label, value] for label, value in report.metrics],
        }
        # One corroboration footnote from the supporting code/docs signals —
        # reference context only, phrased identically on every surface.
        if report.supporting_signals and style.include_signals:
            from yeaboi.reporting.context import signals_sentence  # noqa: PLC0415 - optional, only when signals exist

            sentence = signals_sentence(report.supporting_signals)
            if sentence:
                metrics_slide["footnote"] = sentence
        slides.append(metrics_slide)
    # "ask" resolves to "expand" here: builders can never prompt, and adding
    # slides (never trimming) is the safe default — the TUI offers beforehand.
    fit_mode = style.content_fit if style.content_fit != "ask" else "expand"
    scale = FONT_SCALES.get(style.font_scale, 1.0)
    if style.layout == "compact" and report.themes:
        if fit_mode == "tight":
            # Fixed 2×2 grid; card bullets trim tighter than full-slide lists so
            # four dense cards still fit the non-scrolling viewport.
            chunks = [report.themes[i : i + _CARDS_PER_SLIDE] for i in range(0, len(report.themes), _CARDS_PER_SLIDE)]
            for idx, chunk in enumerate(chunks, start=1):
                slides.append(
                    {
                        "type": "cards",
                        "emoji": _emoji(report, "themes", "🧩"),
                        "section": _DELIVERY,
                        "title": "Outcomes",
                        "page": [idx, len(chunks)],
                        "cards": [
                            [ttitle, cap_items(outcomes, min(style.max_bullets, 4))] for ttitle, outcomes in chunk
                        ],
                    }
                )
        else:
            # Expand fit: the shared planner keeps every bullet and packs
            # content-sized cards — the same plan the .pptx renders, so both
            # surfaces show identical slide groupings.
            plan = plan_outcome_slides(report.themes, scale=scale, max_bullets=style.max_bullets)
            for idx, slide_plan in enumerate(plan, start=1):
                slide: dict = {
                    "type": "cards",
                    "emoji": _emoji(report, "themes", "🧩"),
                    "section": _DELIVERY,
                    "title": "Outcomes",
                    "page": [idx, len(plan)],
                    "cards": [[card.title, list(card.bullets)] for card in slide_plan.cards],
                }
                if len(slide_plan.cards) == 1 and slide_plan.cards[0].full_width:
                    slide["wide"] = True  # lone card spans the slide, no empty column
                slides.append(slide)
    else:
        for ttitle, outcomes in report.themes:
            if fit_mode == "tight":
                pages = [(ttitle, tuple(outcomes))]
            else:
                pages = plan_list_slides(ttitle, outcomes, scale=scale, max_bullets=style.max_bullets)
            for idx, (_suffixed, page_items) in enumerate(pages, start=1):
                slides.append(
                    {
                        "type": "list",
                        "emoji": _emoji(report, "themes", "🧩"),
                        "section": _DELIVERY,
                        "title": ttitle,
                        "page": [idx, len(pages)],
                        "items": cap_items(page_items, style.max_bullets) if fit_mode == "tight" else list(page_items),
                    }
                )
    if report.highlights and style.include_highlights:
        if fit_mode == "tight":
            pages = [("Highlights", tuple(report.highlights))]
        else:
            pages = plan_list_slides("Highlights", report.highlights, scale=scale, max_bullets=style.max_bullets)
        for idx, (_suffixed, page_items) in enumerate(pages, start=1):
            slides.append(
                {
                    "type": "list",
                    "emoji": _emoji(report, "highlights", "⭐"),
                    "section": _DELIVERY,
                    "title": "Highlights",
                    "page": [idx, len(pages)],
                    "items": cap_items(page_items, style.max_bullets) if fit_mode == "tight" else list(page_items),
                }
            )
    if style.include_thanks:
        slides.append(
            {
                "type": "thanks",
                "emoji": _emoji(report, "thanks", "🙌"),
                # Ours, not the LLM's, and the renderer sets it in the block-glyph
                # display face — so it has to stay a short fixed string.
                "title": "Thank you",
                "subtitle": report.project_name or "",
            }
        )
    return slides


def deck_payload(report: DeliveryReport, *, theme: str = "midnight", style: DeckStyle | None = None) -> dict:
    """Everything the deck bundle needs, as one JSON-serialisable dict.

    Split out of :func:`build_presentation_html` so the payload can be asserted
    on directly — the interesting decisions are all in here, and reading them
    back out of a rendered document means grepping a minified bundle.

    Two things are deliberately *not* resolved here:

    * **Style colours** stay as the user wrote them ("" | a palette role |
      ``#rrggbb``) and are resolved client-side against whichever palette is
      showing. Resolving them in Python would freeze them to the palette the
      deck opened with, which is a real bug in the old renderer: pressing "T"
      re-themed the whole deck except the heading colour the user had chosen.
    * **The palettes** are shipped whole rather than as a theme name. There is
      no server behind an exported deck, so there is nothing for a payload to
      disagree with — and custom palettes are user data from
      ``reporting_themes.json`` that no codegen could pin. This is what deleted
      the CSS-generating function: the palette is data now, all the way down.
    """
    from yeaboi.reporting.themes import all_palettes  # noqa: PLC0415 - reads user prefs; keep import lazy

    style = style or DeckStyle()
    palettes = all_palettes()
    theme = theme if theme in palettes else "midnight"
    return {
        "project": report.project_name or "Delivery Report",
        "period": _period_line(report),
        "generated": datetime.now().strftime("%Y-%m-%d"),
        "theme": theme,
        "palettes": palettes,
        "slides": _build_slides(report, style),
        "style": {
            "slideNumbers": style.slide_numbers,
            "footer": style.footer_text,
            "titleColor": style.title_color,
            "headingColor": style.heading_color,
            # .get, matching pptx_export: DeckStyle is a plain frozen dataclass,
            # so only style_from_dict validates the preset name. A caller that
            # constructs one by hand must not be able to KeyError an export.
            "fontFamily": FONT_PRESETS.get(style.font_family, FONT_PRESETS["modern"])["css"],
            "fontScale": FONT_SCALES.get(style.font_scale, 1.0),
        },
    }


# Shown before the bundle mounts, and to anyone with JavaScript disabled. A deck
# is keyboard-driven with one slide visible at a time, so there is nothing
# sensible to render statically — but the same export always writes a plain HTML
# report beside it, and that one is a document.
_NOSCRIPT = (
    "<noscript>This slide deck needs JavaScript. The same export wrote a plain "
    "HTML report next to this file — open that instead.</noscript>"
)


def build_presentation_html(report: DeliveryReport, *, theme: str = "midnight", style: DeckStyle | None = None) -> str:
    """Return a self-contained HTML slide deck presenting the delivery report.

    The deck offers the built-in palettes plus any custom ones from
    reporting_themes.json — the viewer's T key cycles through all of them.
    ``style`` (see reporting/style.py) customizes colors, typography, layout and
    optional sections; None means the neutral defaults — this function never reads
    the prefs file itself, callers resolve persistence.
    """
    from yeaboi.web.assets import render_page  # noqa: PLC0415 - avoids an import cycle via html_theme

    data = deck_payload(report, theme=theme, style=style)
    html = render_page(
        bundle="deck",
        title=f"{data['project']} — Delivery Report",
        data=data,
        # `data-mode` layers reporting's violet over the design tokens, which is
        # what the deck shows for the split second before the bundle applies the
        # chosen palette. `data-deck-theme` is a separate attribute on purpose:
        # `data-theme` already means "which of the five *site* palettes", and a
        # deck theme called "midnight" is not the same thing as a site theme
        # called "midnight". The value is a key of `all_palettes()`, which is
        # slug-validated in themes.py, so interpolating it here cannot inject.
        html_attrs=f'data-mode="reporting" data-deck-theme="{data["theme"]}"',
        body=_NOSCRIPT,
    )
    logger.info(
        "reporting presentation: slide deck built — %d slide(s), theme=%s, %d bytes",
        len(data["slides"]),
        data["theme"],
        len(html.encode("utf-8")),
    )
    return html
