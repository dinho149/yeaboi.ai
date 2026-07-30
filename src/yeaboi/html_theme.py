"""Shared design system for every shareable/exported HTML page.

One stylesheet (``EXPORT_CSS``) + one page shell (``html_page``) used by all
static exporters (planning, standup, retro export, performance, reporting,
roadmap, analysis, anonymize) and the share gate page, so everything shared
over a tunnel reads as one product family with the retro live board.

The theme palettes are the retro board's (see ``retro/page.py``): midnight is
the default, with light/solarized/synthwave/forest selectable via a small
inline script persisted in localStorage. Pages stay fully self-contained —
no external requests, ever.

``EXPORT_CSS`` and the theme script are no longer written here: they are built
from ``frontend/src/design/tokens.css`` and ``frontend/src/export/main.ts`` into
``yeaboi/web/static/export.{css,js}`` (``make web``) and loaded at import. The
markup primitives below still emit the class names that stylesheet defines, so
the two move together — ``tests/unit/test_html_theme.py`` asserts the overlap.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Mapping, Sequence

from yeaboi.web.assets import read_asset, render_page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

# Token vocabulary (matches the retro live board):
#   --bg --panel --line --text --muted --accent --accent2 --card --ink
# plus semantic tokens for exporters:
#   --ok --warn --danger --info  and the priority ramp --critical --high --medium --low
#
# Source of truth: frontend/src/design/tokens.css. Read once at import from the
# committed bundle, so this stays a plain str for every existing caller.
EXPORT_CSS = read_asset("export.css")

# Theme switcher: the built `export` bundle (frontend/src/export/main.ts). A
# constant — no caller data is ever interpolated into it — which
# test_theme_script_is_constant asserts by rendering two structurally different
# pages and comparing the <script> byte for byte.
_THEME_SCRIPT = read_asset("export.js")


def escape(text: str, quote: bool = True) -> str:
    """HTML-escape a value (stringified first) — the one escape helper for every exporter.

    Exporters import this as ``_e`` instead of ``html.escape`` so there is a
    single definition with a single default (``quote=True``).
    """
    return html.escape(str(text), quote)


_e = escape


# Schemes allowed to reach an ``href``. Deliberately tiny: exports only ever
# link to a tracker (http/https) or a person (mailto).
_SAFE_URL_SCHEMES = frozenset({"http", "https", "mailto"})

# A scheme per RFC 3986: ALPHA *( ALPHA / DIGIT / "+" / "-" / "." ) ":"
_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")

# Per the URL spec browsers remove TAB / LF / CR from *anywhere* in a URL before
# parsing it, so ``java&#9;script:alert(1)`` reaches the parser as
# ``javascript:alert(1)``. Strip exactly those here, or the allowlist below is
# trivially bypassed. Interior spaces are deliberately NOT stripped — browsers
# keep them, so they cannot be used to smuggle a scheme past this check.
_URL_STRIP_RE = re.compile(r"[\t\n\r]")


def safe_url(url: str) -> str:
    """Return ``url`` if it is safe to place in an ``href``, else ``""``.

    Tracker URLs reach the exports from Jira / Azure DevOps / GitHub payloads and
    from a user-configured base URL, so they are attacker-influenced. **HTML
    escaping does not help here**: ``javascript:alert(1)`` contains no character
    ``html.escape`` touches, so it survives into the attribute intact and runs on
    click. This is why ``tests/unit/test_export_xss.py`` never caught it — its
    probe is markup-shaped, not scheme-shaped.

    A value with no scheme at all (``example.com/browse/KEY``, ``/browse/KEY``) is
    returned unchanged: with no scheme the browser resolves it relative to the
    document and it cannot execute. Protocol-relative ``//host`` is rejected —
    under ``file://`` it resolves to a bogus origin and it is never what an
    exporter meant.

    # See docs: "Guardrails" — output validation / escaping
    """
    if not url:
        return ""  # guard first: str(None) would yield the literal "None"
    # strip() removes the leading/trailing whitespace and C0 controls browsers
    # also ignore; the regex then removes TAB/LF/CR from the interior.
    cleaned = _URL_STRIP_RE.sub("", str(url).strip(" \t\n\r\v\f\x00\x7f"))
    if not cleaned:
        return ""
    if cleaned.startswith("//"):  # protocol-relative
        logger.warning("Dropped protocol-relative URL from export: %r", url)
        return ""
    match = _SCHEME_RE.match(cleaned)
    if match is None:
        return cleaned  # relative reference — inert
    if match.group(1).lower() in _SAFE_URL_SCHEMES:
        return cleaned
    logger.warning("Dropped unsafe URL scheme %r from export", match.group(1))
    return ""


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


def chip(label: str, kind: str = "") -> str:
    """A pill chip. ``kind`` is one of ok/warn/danger/info/accent/critical/high/medium/low/pts or '' for neutral."""
    cls = f"badge badge-{_e(kind)}" if kind else "badge badge-tag"
    return f'<span class="{cls}">{_e(label)}</span>'


def section(id_: str, title: str, content: str) -> str:
    """A titled page section. ``content`` is trusted pre-built HTML; id/title are escaped."""
    return f'<section id="{_e(id_)}"><h2>{_e(title)}</h2>{content}</section>'


def stat_tile(value: str, label: str) -> str:
    """A metric tile (big number + small caption) for use inside ``.stat-grid``."""
    return f'<div class="stat"><div class="num">{_e(value)}</div><div class="lbl">{_e(label)}</div></div>'


def _safe_css_var(color_var: str, default: str = "--accent") -> str:
    """Return ``color_var`` if it is a plain ``--token`` name, else ``default``.

    Chart helpers interpolate these into style/SVG attributes — the whitelist
    shape (dashes + alphanumerics only) makes injection impossible.
    """
    if not color_var.startswith("--") or not color_var[2:].replace("-", "").isalnum():
        return default
    return color_var


def stat_bar(pct: float, *, color_var: str = "--accent") -> str:
    """A horizontal progress bar filled to ``pct`` percent, colored by a CSS token name."""
    width = max(0, min(100, int(pct)))
    color_var = _safe_css_var(color_var)
    return (
        f'<div class="capacity-bar">'
        f'<div class="capacity-fill" style="width:{width}%;background:var({color_var})"></div></div>'
    )


def sparkline_svg(
    values: Sequence[float],
    *,
    vmin: float | None = None,
    vmax: float | None = None,
    color_var: str = "--accent",
    end_color_var: str = "--accent",
    start_label: str = "",
    end_label: str = "",
    title: str = "",
) -> str:
    """A theme-aware inline-SVG sparkline (line + soft area fill + end dot).

    Colors are CSS custom-property tokens so the chart recolors with the page
    theme and prints correctly — the reason this is hand-built SVG rather than
    a matplotlib PNG. Returns "" for fewer than two points (no trend to show).
    Path data is numbers-only; every text/color input is escaped/sanitized.
    """
    if len(values) < 2:
        return ""
    color_var = _safe_css_var(color_var)
    end_color_var = _safe_css_var(end_color_var, default=color_var)
    lo = min(values) if vmin is None else vmin
    hi = max(values) if vmax is None else vmax
    width, height, pad = 600.0, 48.0, 6.0
    span = hi - lo

    def _xy(i: int, v: float) -> tuple[float, float]:
        x = pad + (i / (len(values) - 1)) * (width - 2 * pad)
        frac = 0.5 if span <= 0 else (min(max(v, lo), hi) - lo) / span
        y = pad + (1 - frac) * (height - 2 * pad)
        return x, y

    pts = [_xy(i, v) for i, v in enumerate(values)]
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    baseline = height - pad
    area = (
        f"M {pts[0][0]:.1f},{pts[0][1]:.1f} "
        + " ".join(f"L {x:.1f},{y:.1f}" for x, y in pts[1:])
        + f" L {pts[-1][0]:.1f},{baseline:.1f} L {pts[0][0]:.1f},{baseline:.1f} Z"
    )
    end_x, end_y = pts[-1]
    labels = ""
    if start_label or end_label:
        labels = f'<div class="spark-labels"><span>{_e(start_label)}</span><span>{_e(end_label)}</span></div>'
    # vector-effect keeps the 2px stroke and dot ring crisp while
    # preserveAspectRatio="none" stretches the drawing to the container width.
    return (
        f'<div class="spark-wrap">'
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" preserveAspectRatio="none" role="img" '
        f'aria-label="{_e(title)}"><title>{_e(title)}</title>'
        f'<path d="{area}" fill="var({color_var})" fill-opacity="0.12"/>'
        f'<polyline points="{poly}" fill="none" stroke="var({color_var})" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
        f'<circle cx="{end_x:.1f}" cy="{end_y:.1f}" r="4" fill="var({end_color_var})" '
        f'stroke="var(--panel)" stroke-width="2" vector-effect="non-scaling-stroke"/>'
        f"</svg>{labels}</div>"
    )


def segment_bar(segments: Sequence[tuple[float, str]], *, title: str = "", width_pct: float = 100.0) -> str:
    """A segmented horizontal bar from ``(value, color_var)`` pairs (pure CSS).

    Segment widths are proportional to values; ``width_pct`` scales the whole
    track (how caller normalizes bars against a shared maximum). Returns ""
    when nothing positive remains to draw.
    """
    kept = [(v, _safe_css_var(cv)) for v, cv in segments if v > 0]
    total = sum(v for v, _ in kept)
    if total <= 0:
        return ""
    width_pct = max(0.0, min(100.0, width_pct))
    cells = "".join(f'<i style="flex:0 0 {v / total * 100:.1f}%;background:var({cv})"></i>' for v, cv in kept)
    return f'<div class="seg-track" role="img" aria-label="{_e(title)}" style="width:{width_pct:.1f}%">{cells}</div>'


def legend(items: Sequence[tuple[str, str]]) -> str:
    """A swatch legend row from ``(label, color_var)`` pairs. Empty items → ""."""
    if not items:
        return ""
    spans = "".join(
        f'<span><i style="background:var({_safe_css_var(cv)})"></i>{_e(label)}</span>' for label, cv in items
    )
    return f'<div class="legend">{spans}</div>'


# Small token palette for deterministic avatar colors — indexed by a stable
# name digest (NOT built-in hash(), which is salted per process).
_AVATAR_VARS = ("--accent", "--accent2", "--info", "--ok", "--warn", "--high")


def avatar(name: str) -> str:
    """A 26px initials circle for a member name, deterministically colored.

    Initials = first alphanumeric of the first and last whitespace tokens
    ("Alice Johnson" → "AJ", "alice" → "A", no alphanumerics → "?").
    """
    tokens = (name or "").split()
    picks = tokens[:1] if len(tokens) == 1 else [tokens[0], tokens[-1]] if tokens else []
    letters = []
    for token in picks:
        first_alnum = next((ch for ch in token if ch.isalnum()), "")
        if first_alnum:
            letters.append(first_alnum.upper())
    initials = "".join(letters) or "?"
    var = _AVATAR_VARS[sum(map(ord, name or "")) % len(_AVATAR_VARS)]
    return (
        f'<span class="avatar" style="background:color-mix(in srgb, var({var}) 22%, var(--panel));'
        f'color:var({var})">{_e(initials)}</span>'
    )


def notice_block(title: str, items: Sequence[str]) -> str:
    """The ⚠ notices panel — a titled warning card listing caveats. Empty items → ''."""
    if not items:
        return ""
    lis = "".join(f"<li>{_e(item)}</li>" for item in items)
    return f'<div class="notice"><div class="notice-title">⚠ {_e(title)}</div><ul>{lis}</ul></div>'


# ---------------------------------------------------------------------------
# Chart compositions — shared building blocks over the primitives above,
# extracted from the standup export so every mode renders visuals the same way.
# ---------------------------------------------------------------------------

# The [A-Z] lookahead avoids splitting on "e.g. " and similar abbreviations.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

# Fixed hue order for counted breakdowns; an overflow segment folds into a
# muted "other" instead of inventing new hues.
_CHART_VARS = ("--accent", "--accent2", "--info", "--ok", "--warn", "--high", "--medium")


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences (abbreviation-safe), dropping empties."""
    return [s.strip() for s in _SENTENCE_RE.split(text.strip()) if s.strip()]


def prose_bullets(text: str) -> list[str]:
    """Split prose into scannable bullet fragments: sentences, then "; " clauses.

    LLM summaries pack several facts into one long sentence, which reads as a
    wall of text when rendered as a single bullet.
    """
    fragments: list[str] = []
    for sentence in split_sentences(text):
        fragments.extend(part.strip(" ;") for part in sentence.split("; ") if part.strip(" ;"))
    return fragments


def counted_segment_bar(
    counts: Sequence[tuple[str, int]],
    *,
    palette: Sequence[str] = _CHART_VARS,
    title: str = "",
    overflow_label: str = "other",
    overflow_var: str = "--muted",
) -> str:
    """A sorted segmented bar + counted legend ("github 12") from (label, count) pairs.

    Zero/negative counts are dropped; more than ``len(palette) + 1`` labels fold
    the tail into a single muted overflow segment. Returns "" when nothing is
    positive.
    """
    pairs = [(str(label), int(n)) for label, n in counts if int(n) > 0]
    if not pairs:
        return ""
    pairs.sort(key=lambda pair: -pair[1])
    if len(pairs) > len(palette) + 1:
        head, tail = pairs[: len(palette)], pairs[len(palette) :]
        pairs = [*head, (overflow_label, sum(n for _, n in tail))]
    vars_ = [*palette, overflow_var][: len(pairs)]
    bar = segment_bar([(n, var) for (_, n), var in zip(pairs, vars_)], title=title)
    key = legend([(f"{label} {n}", var) for (label, n), var in zip(pairs, vars_)])
    return f"{bar}{key}"


def history_series(
    rows: Sequence[Mapping],
    *,
    date_key: str,
    value_key: str,
    status_key: str = "",
    ok_statuses: Sequence[str] = ("success", "partial"),
    cutoff_date: str = "",
    current: tuple[str, float] | None = None,
    max_points: int = 14,
) -> list[tuple[str, float]]:
    """Normalize newest-first store history rows into (date, value), oldest → newest.

    Rows past ``cutoff_date`` are dropped (re-exporting an old run must not show
    its future), same-date reruns dedupe keeping the newest (input newest-first),
    ``status_key`` (when given) filters to ``ok_statuses``, and ``current``
    appends today's point when its date isn't already present.
    """
    points: list[tuple[str, float]] = []
    seen: set[str] = set()
    for row in rows:
        day = str(row.get(date_key, "") or "")
        value = row.get(value_key)
        if not day or value is None:
            continue
        if status_key and row.get(status_key) not in ok_statuses:
            continue
        if cutoff_date and day > cutoff_date:
            continue
        if day in seen:
            continue
        seen.add(day)
        points.append((day, float(value)))
    points.reverse()
    if current and current[0] and current[0] not in seen:
        points.append((current[0], float(current[1])))
    return points[-max_points:]


def sparkline_card(
    points: Sequence[tuple[str, float]],
    *,
    title: str,
    color_var: str = "--accent",
    end_color_var: str = "",
    pad: float = 8.0,
    floor: float = 0.0,
    ceiling: float | None = None,
    svg_title: str = "",
) -> str:
    """A titled ``.card`` wrapping a sparkline over (date, value) points, or "".

    The value domain is the data range padded by ``pad`` (clamped to
    ``floor``/``ceiling``) — a full fixed domain flattens series that move in a
    narrow band into an unreadable line. Under two points renders nothing.
    """
    if len(points) < 2:
        return ""
    values = [value for _, value in points]
    vmin = max(floor, min(values) - pad)
    vmax = min(ceiling, max(values) + pad) if ceiling is not None else max(values) + pad
    svg = sparkline_svg(
        values,
        vmin=vmin,
        vmax=vmax,
        color_var=color_var,
        end_color_var=end_color_var or color_var,
        start_label=points[0][0],
        end_label=points[-1][0],
        title=svg_title or f"{title} — last {len(points)} runs",
    )
    return f"<div class='card'><div class='card-title' style='margin-bottom:.3rem'>{_e(title)}</div>{svg}</div>"


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------


def export_page(
    *,
    mode: str,
    title: str,
    wordmark: str,
    report: Mapping[str, object],
    subtitle: str = "",
    facts: Sequence[tuple[str, str]] = (),
    badges: Sequence[str] = (),
    nav: Sequence[tuple[str, str]] = (),
    footer: str = "",
    markdown_name: str = "",
) -> str:
    """Render one exported report as a self-contained React page.

    This is the replacement for ``html_page``. The difference is not cosmetic:
    ``html_page`` took a ``body`` of **pre-built HTML**, which meant every
    exporter assembled markup by hand and carried the escaping discipline that
    goes with it — the reason ``tests/unit/test_export_xss.py`` exists at all.
    Here the exporter passes ``report``, a plain JSON-able mapping of text and
    numbers, and the bundle draws it. There is no markup on this side to get
    wrong.

    Args:
        mode: ``[data-mode]`` value driving ``--accent``. Not every export owns
            a distinct TUI accent (roadmap borrows planning's, anonymize the
            default), so this names the accent to wear, not the authoring mode.
        title: Document ``<title>`` and the page ``<h1>``.
        wordmark: The word set in the block-glyph face. Keep it short — the
            face is two rows tall and roughly three columns per letter.
        report: The report payload. Must carry a ``kind`` the bundle's
            ``Report`` switch knows; an unknown one throws in the browser
            rather than rendering blank.
        subtitle: Muted line under the title.
        facts: Header eyebrows as ``(label, value)``. Each should say something
            true about the run — a source, a date, a period.
        badges: Accent eyebrows with no value.
        nav: ``(section_id, label)`` contents links. Omit for a short report.
        footer: Footer line. Defaults to the standard credit.
        markdown_name: Filename of the sibling Markdown artifact. When given,
            the page carries a ``<noscript>`` note pointing at it — see below.

    **On JavaScript.** These pages render client-side, so with scripting off
    they would be blank. That is a real regression from the string-templated
    version and it is answered rather than ignored: every exporter writes a
    Markdown file beside the HTML, that file is the primary artifact for
    several of them already, and ``markdown_name`` puts its name in front of
    anyone who lands on the page without a runtime.
    """
    chrome: dict[str, object] = {
        "mode": mode,
        "frame": f"yeaboi — {mode}",
        "wordmark": wordmark,
        "title": title,
        "footer": footer or "Generated by yeaboi.ai",
    }
    if subtitle:
        chrome["subtitle"] = subtitle
    if facts:
        chrome["facts"] = [[label, value] for label, value in facts if value]
    if badges:
        chrome["badges"] = list(badges)
    if nav:
        chrome["nav"] = [[id_, label] for id_, label in nav]

    noscript = ""
    if markdown_name:
        noscript = (
            '<noscript><p class="noscript">This report is drawn in the browser. '
            f"With JavaScript off, the same content is in <code>{_e(markdown_name)}</code>, "
            "written beside this file.</p></noscript>"
        )

    return render_page(
        bundle="export",
        title=title,
        data={"chrome": chrome, "report": dict(report)},
        body=noscript,
        html_attrs=f'data-mode="{_e(mode)}"',
    )


def html_page(
    *,
    title: str,
    body: str,
    heading: str = "",
    subtitle: str = "",
    meta: Sequence[str] = (),
    badges: Sequence[str] = (),
    nav: Sequence[tuple[str, str]] = (),
    footer_note: str = "Generated by yeaboi.ai",
    theme_toggle: bool = True,
) -> str:
    """Build a complete self-contained themed HTML document.

    Everything except ``body`` is escaped here. ``body`` is trusted ONLY if
    every interpolated user/LLM string went through the escape helper
    (``escape``/``_e``) or a primitive (chip/section/stat_tile/stat_bar/
    notice_block) — enforced by tests/unit/test_export_xss.py.

    Args:
        title: Document ``<title>``.
        body: Trusted HTML placed inside the ``.container``.
        heading: Page ``<h1>``; defaults to ``title``.
        subtitle: Muted line under the heading.
        meta: Plain-text metadata snippets shown in the header row.
        badges: Labels rendered as accent chips in the header.
        nav: ``(section_id, label)`` pairs for the sticky table of contents.
        footer_note: Text of the page footer.
        theme_toggle: Include the theme-switcher script and button.
    """
    heading = heading or title

    badge_html = "".join(f'<span class="badge badge-accent">{_e(b)}</span>' for b in badges)
    toggle_btn = (
        '<button class="theme-btn" onclick="__yeaboiCycleTheme()" title="Switch theme">◐ theme</button>'
        if theme_toggle
        else ""
    )
    meta_spans = "".join(f"<span>{_e(m)}</span>" for m in meta)
    meta_row = ""
    if meta_spans or badge_html or toggle_btn:
        meta_row = f'<div class="meta">{meta_spans}{badge_html}{toggle_btn}</div>'
    subtitle_html = f'<div class="subtitle">{_e(subtitle)}</div>' if subtitle else ""

    nav_html = ""
    if nav:
        links = "".join(f'<a href="#{_e(id_)}">{_e(label)}</a>' for id_, label in nav)
        nav_html = f'<nav class="toc">{links}</nav>'

    script = f"<script>{_THEME_SCRIPT}</script>" if theme_toggle else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_e(title)}</title>
  <style>{EXPORT_CSS}</style>
</head>
<body>
{script}
<header class="site-header">
  <h1>{_e(heading)}</h1>
  {subtitle_html}
  {meta_row}
</header>
{nav_html}
<div class="container">
{body}
</div>
<footer class="site-footer">
  {_e(footer_note)}
</footer>
</body>
</html>"""
