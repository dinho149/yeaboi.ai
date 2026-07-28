"""Build a self-contained HTML slide deck from a DeliveryReport.

This is the "presentation" output of Reporting mode: one offline .html file — inline
CSS + JS, no external dependencies — that opens in any browser and presents the
delivered work to the business. It follows the same embedded-asset pattern as
retro/page.py: big ``_CSS`` / ``_JS`` module strings with ``__PLACEHOLDER__`` markers
filled via ``str.replace`` + ``json.dumps``.

Design split (the "hybrid" the user chose): the *content* — slide wording, the
outcome themes, and the section emojis — is supplied by the LLM design pass in
engine.py (with a deterministic fallback). This file only *renders* it: layout,
theme palettes, keyboard navigation, progress. Ticket/outcome text is written to the
DOM via ``textContent`` (never innerHTML), so anything that came from a tracker is
inert — the same XSS defense the retro page uses.

This module is E501-exempt in pyproject.toml (embedded asset).

# See docs: "Reporting Mode" — presentation output
# See docs: "Retro Mode" — self-contained embedded HTML page pattern
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from yeaboi.agent.state import DeliveryReport
from yeaboi.reporting.layout import TIGHT_CARDS_PER_SLIDE, plan_list_slides, plan_outcome_slides
from yeaboi.reporting.style import FONT_PRESETS, FONT_SCALES, DeckStyle, cap_items, resolve_color, summary_points

logger = logging.getLogger(__name__)

# Theme names offered by the deck; the viewer cycles them with the "T" key.
THEMES = ("midnight", "aurora", "sunset", "mono")

# How many outcome-theme cards fit on one "compact" tight-fit slide (2×2 grid) —
# shared with the pptx renderer via layout.py.
_CARDS_PER_SLIDE = TIGHT_CARDS_PER_SLIDE


def _json_for_script(value) -> str:
    """JSON-encode ``value`` safely for embedding inside an inline ``<script>``.

    ``json.dumps`` does not escape ``<`` / ``>`` / ``&``, so an untrusted ticket
    title containing ``</script>`` would otherwise break out of the script element
    and execute as markup. Escaping these to their ``\\uXXXX`` forms keeps the JSON
    valid while making a ``</script>`` breakout impossible — the standard mitigation
    for JSON-in-HTML. Ticket text is still rendered client-side via ``textContent``,
    so this is defense in depth.
    """
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _emoji(report: DeliveryReport, slot: str, default: str = "") -> str:
    for s, e in report.emoji_theme:
        if s == slot and e:
            return e
    return default


def _build_slides(report: DeliveryReport, style: DeckStyle) -> list[dict]:
    """Turn a DeliveryReport into an ordered list of slide dicts for the JS renderer.

    ``style`` controls the composition: bullet caps, the compact (theme cards)
    layout, and which optional slides/footnotes appear. All text stays as plain
    strings — the client renders it via textContent, so untrusted ticket titles
    cannot inject markup.
    """
    slides: list[dict] = []
    dates = f"{report.period_start} to {report.period_end}".strip(" to")
    subtitle = report.period_label + (f"  ·  {dates}" if dates else "")
    if report.sprint_names:
        subtitle += f"  ·  {', '.join(report.sprint_names)}"

    slides.append(
        {
            "type": "title",
            "emoji": _emoji(report, "headline", "🚀"),
            "title": report.project_name or "Delivery Report",
            "subtitle": subtitle,
            "headline": report.headline,
        }
    )
    if report.executive_summary:
        slides.append(
            {
                "type": "summary",
                "emoji": _emoji(report, "summary", "📋"),
                "title": "Executive summary",
                # Sentence-level points, not one prose blob — far more readable
                # projected. The JS renderer draws one <p> per point.
                "points": summary_points(report.executive_summary),
            }
        )
    if report.metrics:
        metrics_slide = {
            "type": "metrics",
            "emoji": _emoji(report, "metrics", "📊"),
            "title": "By the numbers",
            "metrics": [[label, value] for label, value in report.metrics],
        }
        # One corroboration footnote from the supporting code/docs signals —
        # reference context only, phrased identically on every surface.
        if report.supporting_signals and style.include_signals:
            from yeaboi.reporting.context import signals_sentence

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
                        "title": "Outcomes" if len(chunks) == 1 else f"Outcomes ({idx}/{len(chunks)})",
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
                    "title": "Outcomes" if len(plan) == 1 else f"Outcomes ({idx}/{len(plan)})",
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
            for page_title, page_items in pages:
                slides.append(
                    {
                        "type": "list",
                        "emoji": _emoji(report, "themes", "🧩"),
                        "title": page_title,
                        "items": cap_items(page_items, style.max_bullets) if fit_mode == "tight" else list(page_items),
                    }
                )
    if report.highlights and style.include_highlights:
        if fit_mode == "tight":
            pages = [("Highlights", tuple(report.highlights))]
        else:
            pages = plan_list_slides("Highlights", report.highlights, scale=scale, max_bullets=style.max_bullets)
        for page_title, page_items in pages:
            slides.append(
                {
                    "type": "list",
                    "emoji": _emoji(report, "highlights", "⭐"),
                    "title": page_title,
                    "items": cap_items(page_items, style.max_bullets) if fit_mode == "tight" else list(page_items),
                }
            )
    if style.include_thanks:
        slides.append(
            {
                "type": "thanks",
                "emoji": _emoji(report, "thanks", "🙌"),
                "title": "Thank you",
                "subtitle": report.project_name or "",
            }
        )
    return slides


def _custom_theme_css(palettes: dict[str, dict[str, str]]) -> str:
    """CSS ``[data-theme]`` blocks for the user's custom palettes.

    Names/hexes are already slug- and #RRGGBB-validated by ``themes.load_custom_palettes``,
    so interpolating them into CSS is safe. ``card``/``border`` keep the shared overlay
    defaults the built-ins use.
    """
    blocks = []
    for name, p in palettes.items():
        blocks.append(
            f'[data-theme="{name}"] {{ --bg1:{p["bg1"]}; --bg2:{p["bg2"]}; --fg:{p["fg"]}; --muted:{p["muted"]}; '
            f"--accent:{p['accent']}; --accent2:{p['accent2']}; "
            f"--card:rgba(255,255,255,.05); --border:rgba(255,255,255,.10); }}"
        )
    return "\n".join(blocks)


def _style_css(style: DeckStyle, palette: dict[str, str]) -> str:
    """CSS overrides for the user's deck style — empty for the default style.

    Only deviations from the defaults are emitted, so a default-style deck keeps the
    exact historical stylesheet. Color values are palette hexes or #RRGGBB strings
    already validated by ``style_from_dict``/``resolve_color`` — safe to interpolate.
    """
    rules: list[str] = []
    title = resolve_color(style.title_color, palette, "")
    if title:
        rules.append(f".slide h1 {{ color: {title}; }}")
    heading = resolve_color(style.heading_color, palette, "")
    if heading:
        # h2 uses gradient text-clip — a flat two-stop gradient keeps the mechanics.
        rules.append(f"h2 {{ background: linear-gradient(90deg, {heading}, {heading}); }}")
        rules.append(f"ul.items li::before {{ color: {heading}; }}")
        rules.append(f".card h3 {{ color: {heading}; }}")
    if style.font_family != "modern":
        rules.append(f"body {{ font-family: {FONT_PRESETS[style.font_family]['css']}; }}")
    scale = FONT_SCALES.get(style.font_scale, 1.0)
    if scale != 1.0:
        # Re-declare the clamp() sizes with Python-scaled bounds (no root-em tricks).
        clamps = {
            ".emoji": (3, 9, 6),
            "h1": (2, 6, 4),
            "h2": (1.6, 4.5, 3),
            ".sub": (1, 2.4, 1.4),
            ".headline": (1.2, 3, 1.8),
            ".body": (1.1, 2.6, 1.6),
            "ul.items li": (1.05, 2.5, 1.5),
            ".metric .val": (2.4, 6, 4),
        }
        for selector, (lo, vw, hi) in clamps.items():
            rules.append(
                f"{selector} {{ font-size: clamp({lo * scale:.2f}rem, {vw * scale:.2f}vw, {hi * scale:.2f}rem); }}"
            )
    return "\n".join(rules)


def build_presentation_html(report: DeliveryReport, *, theme: str = "midnight", style: DeckStyle | None = None) -> str:
    """Return a self-contained HTML slide deck presenting the delivery report.

    The deck offers the built-in palettes plus any custom ones from
    reporting_themes.json — the viewer's T key cycles through all of them.
    ``style`` (see reporting/style.py) customizes colors, typography, layout and
    optional sections; None means the neutral defaults — this function never reads
    the prefs file itself, callers resolve persistence.
    """
    from yeaboi.reporting.themes import get_palette, load_custom_palettes

    style = style or DeckStyle()
    custom = load_custom_palettes()
    theme_names = list(THEMES) + sorted(custom)
    theme = theme if theme in theme_names else "midnight"
    slides = _build_slides(report, style)
    logger.info("reporting presentation: slide deck built — %d slide(s), theme=%s", len(slides), theme)
    # Footer badge: the yeaboi duck (inline data URI — the deck stays offline)
    # next to the generated-by line; a missing asset simply drops the image.
    from yeaboi.reporting.branding import duck_data_uri

    duck = duck_data_uri()
    duck_img = f'<img id="brandDuck" src="{duck}" alt="">' if duck else ""
    footer = f"{duck_img}Generated by yeaboi.ai · {datetime.now().strftime('%Y-%m-%d')}"
    js = (
        _JS.replace("__SLIDES__", _json_for_script(slides))
        .replace("__THEME__", _json_for_script(theme))
        .replace("__THEMES__", _json_for_script(theme_names))
        .replace("__STYLE__", _json_for_script({"slide_numbers": style.slide_numbers, "footer": style.footer_text}))
    )
    css = _CSS + _custom_theme_css(custom) + "\n" + _style_css(style, get_palette(theme))
    title = report.project_name or "Delivery Report"
    # Title bar text is our own (project name); still escape the couple of dynamic bits.
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="en" data-theme="{theme}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title} — Delivery Report</title>
<style>{css}</style>
</head>
<body>
<div id="deck" aria-live="polite"></div>
<div id="chrome">
  <div id="progress"><div id="bar"></div></div>
  <div id="controls">
    <button id="prev" aria-label="Previous slide">‹</button>
    <span id="counter">1 / 1</span>
    <button id="next" aria-label="Next slide">›</button>
    <button id="themeBtn" title="Cycle theme (T)">◑</button>
  </div>
  <div id="hint">← / → or Space to navigate · T for theme · F fullscreen</div>
</div>
<footer id="footer">{footer}</footer>
<script>{js}</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Embedded CSS — theme palettes + slide layout
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
:root, [data-theme="midnight"] { --bg1:#0d1117; --bg2:#161b2e; --fg:#e6edf3; --muted:#9aa4b2; --accent:#8c78e6; --accent2:#b8a6ff; --card:rgba(255,255,255,.05); --border:rgba(255,255,255,.10); }
[data-theme="aurora"] { --bg1:#04121a; --bg2:#0a2a2a; --fg:#e8fff6; --muted:#8fc9be; --accent:#28c2a0; --accent2:#6ff0d0; --card:rgba(255,255,255,.06); --border:rgba(255,255,255,.12); }
[data-theme="sunset"] { --bg1:#1a0d16; --bg2:#3a1424; --fg:#fff1e8; --muted:#d9a08f; --accent:#f0784e; --accent2:#ffb27a; --card:rgba(255,255,255,.06); --border:rgba(255,255,255,.12); }
[data-theme="mono"] { --bg1:#0b0b0c; --bg2:#1c1c1f; --fg:#f4f4f5; --muted:#a1a1aa; --accent:#d4d4d8; --accent2:#ffffff; --card:rgba(255,255,255,.05); --border:rgba(255,255,255,.10); }
html, body { height: 100%; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: radial-gradient(circle at 30% 20%, var(--bg2), var(--bg1) 70%); color: var(--fg); overflow: hidden; }
#deck { height: 100vh; display: flex; align-items: center; justify-content: center; padding: 6vh 8vw 12vh; }
.slide { width: 100%; max-width: 980px; animation: fade .45s ease; }
@keyframes fade { from { opacity: 0; transform: translateY(14px); } to { opacity: 1; transform: none; } }
.emoji { font-size: clamp(3rem, 9vw, 6rem); line-height: 1; margin-bottom: 1.2rem; }
h1 { font-size: clamp(2rem, 6vw, 4rem); font-weight: 800; letter-spacing: -.02em; line-height: 1.05; }
h2 { font-size: clamp(1.6rem, 4.5vw, 3rem); font-weight: 800; letter-spacing: -.02em; margin-bottom: 1.4rem; background: linear-gradient(90deg, var(--accent2), var(--accent)); -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent; }
.sub { color: var(--muted); font-size: clamp(1rem, 2.4vw, 1.4rem); margin-top: 1.2rem; }
.headline { font-size: clamp(1.2rem, 3vw, 1.8rem); margin-top: 1.8rem; font-weight: 600; color: var(--accent2); }
.body { font-size: clamp(1.1rem, 2.6vw, 1.6rem); line-height: 1.6; color: var(--fg); }
.body p { margin: 0 0 .9em; }
ul.items { list-style: none; display: flex; flex-direction: column; gap: .9rem; }
ul.items li { font-size: clamp(1.05rem, 2.5vw, 1.5rem); line-height: 1.4; padding-left: 2.2rem; position: relative; }
ul.items li::before { content: "▸"; color: var(--accent); position: absolute; left: 0; top: 0; }
.metrics { display: flex; flex-wrap: wrap; gap: 1.2rem; }
.metric { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 1.6rem 2rem; min-width: 160px; }
.metric .val { font-size: clamp(2.4rem, 6vw, 4rem); font-weight: 800; color: var(--accent2); }
.metric .lab { color: var(--muted); margin-top: .3rem; font-size: 1rem; }
.footnote { color: var(--muted); font-size: clamp(.85rem, 1.8vw, 1.05rem); margin-top: 1.6rem; font-style: italic; }
.cards { display: grid; grid-template-columns: repeat(2, 1fr); gap: 1.1rem; align-items: start; }
.cards.one { grid-template-columns: 1fr; }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 1.2rem 1.4rem; }
.card h3 { font-size: clamp(1rem, 2.2vw, 1.3rem); font-weight: 700; color: var(--accent2); margin-bottom: .7rem; }
.card ul.items { gap: .45rem; }
.card ul.items li { font-size: clamp(.85rem, 1.8vw, 1.05rem); padding-left: 1.5rem; }
#userFooter { position: fixed; bottom: 1rem; left: 1.2rem; color: var(--muted); font-size: .75rem; opacity: .7; }
#slideNo { position: fixed; bottom: 1rem; right: 1.2rem; color: var(--muted); font-size: .8rem; font-variant-numeric: tabular-nums; opacity: .7; }
.center { text-align: center; }
#chrome { position: fixed; left: 0; right: 0; bottom: 0; padding: .8rem 1.2rem 1rem; }
#progress { height: 4px; background: var(--border); border-radius: 4px; overflow: hidden; margin-bottom: .7rem; }
#bar { height: 100%; width: 0; background: linear-gradient(90deg, var(--accent), var(--accent2)); transition: width .35s ease; }
#controls { display: flex; align-items: center; justify-content: center; gap: 1rem; }
#controls button { background: var(--card); border: 1px solid var(--border); color: var(--fg); width: 40px; height: 40px; border-radius: 50%; font-size: 1.2rem; cursor: pointer; transition: background .2s, transform .1s; }
#controls button:hover { background: var(--border); }
#controls button:active { transform: scale(.92); }
#counter { color: var(--muted); font-variant-numeric: tabular-nums; min-width: 64px; text-align: center; }
#hint { text-align: center; color: var(--muted); font-size: .8rem; margin-top: .6rem; opacity: .7; }
#footer { position: fixed; top: 1rem; right: 1.2rem; color: var(--muted); font-size: .75rem; opacity: .6; display: flex; align-items: center; gap: .45rem; }
#brandDuck { height: 18px; width: 18px; image-rendering: pixelated; }
@media (max-width: 640px) { #hint { display: none; } .cards { grid-template-columns: 1fr; } }
"""

# ---------------------------------------------------------------------------
# Embedded JS — renderer + navigation (SLIDES / THEME injected by str.replace)
# ---------------------------------------------------------------------------

_JS = """
const SLIDES = __SLIDES__;
const THEMES = __THEMES__;
const STYLE = __STYLE__;
let theme = __THEME__;
let i = 0;

const deck = document.getElementById('deck');
const bar = document.getElementById('bar');
const counter = document.getElementById('counter');

// Style chrome — both values are user preferences, rendered via textContent (inert).
let slideNo = null;
if (STYLE.slide_numbers) {
  slideNo = document.createElement('div');
  slideNo.id = 'slideNo';
  document.body.appendChild(slideNo);
}
if (STYLE.footer) {
  const uf = document.createElement('div');
  uf.id = 'userFooter';
  uf.textContent = STYLE.footer;
  document.body.appendChild(uf);
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;   // textContent → untrusted text is inert
  return n;
}

function render() {
  if (i < 0) i = 0;
  if (i > SLIDES.length - 1) i = SLIDES.length - 1;
  const s = SLIDES[i] || {};
  const slide = el('div', 'slide' + ((s.type === 'title' || s.type === 'thanks') ? ' center' : ''));
  if (s.emoji) slide.appendChild(el('div', 'emoji', s.emoji));

  if (s.type === 'title') {
    slide.appendChild(el('h1', null, s.title || 'Delivery Report'));
    if (s.subtitle) slide.appendChild(el('div', 'sub', s.subtitle));
    if (s.headline) slide.appendChild(el('div', 'headline', s.headline));
  } else if (s.type === 'thanks') {
    slide.appendChild(el('h1', null, s.title || 'Thank you'));
    if (s.subtitle) slide.appendChild(el('div', 'sub', s.subtitle));
  } else if (s.type === 'summary') {
    slide.appendChild(el('h2', null, s.title || ''));
    const body = el('div', 'body');
    (s.points || (s.body ? [s.body] : [])).forEach(p => body.appendChild(el('p', null, p)));
    slide.appendChild(body);
  } else if (s.type === 'metrics') {
    slide.appendChild(el('h2', null, s.title || ''));
    const wrap = el('div', 'metrics');
    (s.metrics || []).forEach(m => {
      const card = el('div', 'metric');
      card.appendChild(el('div', 'val', String(m[1])));
      card.appendChild(el('div', 'lab', String(m[0])));
      wrap.appendChild(card);
    });
    slide.appendChild(wrap);
    if (s.footnote) slide.appendChild(el('div', 'footnote', s.footnote));
  } else if (s.type === 'cards') {
    slide.appendChild(el('h2', null, s.title || ''));
    const grid = el('div', 'cards' + (s.wide ? ' one' : ''));
    (s.cards || []).forEach(c => {
      const card = el('div', 'card');
      card.appendChild(el('h3', null, String(c[0])));
      const ul = el('ul', 'items');
      (c[1] || []).forEach(it => ul.appendChild(el('li', null, it)));
      card.appendChild(ul);
      grid.appendChild(card);
    });
    slide.appendChild(grid);
  } else {  // list
    slide.appendChild(el('h2', null, s.title || ''));
    const ul = el('ul', 'items');
    (s.items || []).forEach(it => ul.appendChild(el('li', null, it)));
    slide.appendChild(ul);
  }

  deck.replaceChildren(slide);
  counter.textContent = (i + 1) + ' / ' + SLIDES.length;
  bar.style.width = (SLIDES.length > 1 ? (i / (SLIDES.length - 1)) * 100 : 100) + '%';
  if (slideNo) slideNo.textContent = String(i + 1);
}

function go(n) { i = Math.max(0, Math.min(SLIDES.length - 1, i + n)); render(); }
function cycleTheme() {
  const idx = (THEMES.indexOf(theme) + 1) % THEMES.length;
  theme = THEMES[idx];
  document.documentElement.setAttribute('data-theme', theme);
}

document.getElementById('prev').onclick = () => go(-1);
document.getElementById('next').onclick = () => go(1);
document.getElementById('themeBtn').onclick = cycleTheme;

document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight' || e.key === ' ' || e.key === 'PageDown') { e.preventDefault(); go(1); }
  else if (e.key === 'ArrowLeft' || e.key === 'PageUp') { e.preventDefault(); go(-1); }
  else if (e.key === 'Home') { i = 0; render(); }
  else if (e.key === 'End') { i = SLIDES.length - 1; render(); }
  else if (e.key === 't' || e.key === 'T') { cycleTheme(); }
  else if (e.key === 'f' || e.key === 'F') {
    if (document.fullscreenElement) document.exitFullscreen(); else document.documentElement.requestFullscreen();
  }
});

render();
"""
