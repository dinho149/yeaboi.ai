"""Shared helpers for every exported and shared HTML page.

What is left here after the React migration is the part that is genuinely
server-side: escaping and URL safety for the Markdown twins, the normalisation
a trend series needs before it can be drawn, image embedding, and
:func:`export_page` — the one shell every static export renders through.

**The markup is gone.** This module used to carry a stylesheet constant and
around a dozen primitives that emitted class names by hand — ``chip``,
``stat_tile``, ``sparkline_svg``, ``avatar``, ``html_page`` — and every exporter
assembled its report by concatenating them, which is why
``tests/unit/test_export_xss.py`` exists. Those primitives now live in
``frontend/src/design/primitives/`` as components, the exporters pass a payload
of text and numbers instead of markup, and there is nothing on this side left to
get wrong. ``_safe_css_var``, which regex-checked a CSS custom-property name on
every chart call, went with them: the whitelist is a TypeScript union now.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from yeaboi.web.assets import render_page
from yeaboi.web.brand import build_chrome

logger = logging.getLogger(__name__)


def escape(text: str, quote: bool = True) -> str:
    """HTML-escape a value, stringifying it first.

    Every exporter used to import this as ``_e``; none do now, because none of
    them build markup any more. What is left are the two places
    :func:`export_page` interpolates a caller value into the shell — the mode
    attribute and the ``<noscript>`` filename — so it stays public rather than
    private only because deleting a name other trees may still import buys
    nothing.
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
# Prose and series shaping — what a payload needs done before it can be drawn.
# ---------------------------------------------------------------------------

# The [A-Z] lookahead avoids splitting on "e.g. " and similar abbreviations.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


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


def trend(
    rows: Sequence[Mapping],
    *,
    date_key: str,
    value_key: str,
    title: str,
    label: str,
    status_key: str = "",
    cutoff_date: str = "",
    current: tuple[str, float] | None = None,
    max_points: int = 14,
    floor: float | None = None,
    ceiling: float | None = None,
) -> dict | None:
    """Return the export bundle's trend-card payload, or ``None`` for no chart.

    The React counterpart of :func:`sparkline_card`: same normalization (via
    :func:`history_series`), no markup. ``None`` under two points, because one
    run is not a trend — and ``None`` rather than an omitted key, so the bundle
    can tell "the server decided there is no chart" from "the field is missing".

    ``floor``/``ceiling`` bound the drawn domain. They travel because they are
    facts about the *series*, not about the drawing: a confidence percentage
    cannot exceed 100, so padding the top past it would claim headroom that does
    not exist. A count has a floor of 0 and no ceiling, which is the default.
    """
    points = history_series(
        rows,
        date_key=date_key,
        value_key=value_key,
        status_key=status_key,
        cutoff_date=cutoff_date,
        current=current,
        max_points=max_points,
    )
    if len(points) < 2:
        return None
    out: dict = {
        "title": title,
        "label": f"{label} — last {len(points)} runs",
        "points": [[day, value] for day, value in points],
    }
    if floor is not None:
        out["floor"] = floor
    if ceiling is not None:
        out["ceiling"] = ceiling
    return out


# Mirrors the export-image cap in ``export_targets._MAX_IMAGE_BYTES`` — anything
# bigger bloats the self-contained page past usefulness.
_MAX_EMBED_BYTES = 5 * 1024 * 1024


def image_data_uri(path: str | Path) -> str:
    """Return a file as a ``data:`` URI, or "" if it cannot be embedded.

    Keeps an exported page self-contained and offline: screenshots and charts
    live under ``~/.yeaboi`` and get pruned, so a page that referenced them by
    path would quietly lose its images. Best-effort by design — a missing,
    oversized or unreadable file is a decoration the report can do without, not
    a reason to fail the export.
    """
    import base64
    import mimetypes

    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size > _MAX_EMBED_BYTES:
            logger.warning("Skipping image embed (missing or too large): %s", p)
            return ""
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"
    except Exception as exc:  # noqa: BLE001 — embedding is best-effort decoration
        logger.warning("Could not embed image %s: %s", p, exc)
        return ""


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------


def export_payload(
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
) -> dict[str, object]:
    """Return the ``{chrome, report}`` boot payload for one exported report.

    Split out of :func:`export_page` because the payload now has a second
    consumer. A file on disk is rendered once and never changes, so building it
    inside the page renderer was fine; an *editable* shared document re-derives
    the same payload on every change and pushes it down the long poll, and it has
    no HTML to build. The page renderer is now the thin one: this function owns
    the shape, ``export_page`` owns the document around it.

    Keeping one builder is what stops the two from drifting — an edited report
    and its exported file have to draw identically, and they now do so because
    they are literally the same dict.
    """
    # The chrome dict is built in web/brand.py, because the live boards and the
    # share gate now draw the same masthead from the same shape.
    chrome = build_chrome(
        mode=mode,
        title=title,
        wordmark=wordmark,
        subtitle=subtitle,
        facts=facts,
        badges=badges,
        nav=nav,
        footer=footer,
    )
    return {"chrome": chrome, "report": dict(report)}


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
    document_title: str = "",
    noscript: str = "",
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
        document_title: Overrides the ``<title>`` only, leaving the ``<h1>``
            alone. The two want different things: the heading says what kind of
            document this is ("Daily Standup"), while a browser tab has to
            distinguish it from the four others the reader has open, so it wants
            the date or the sprint too. Empty means "use ``title`` for both",
            which is what every file export does.
        noscript: Replacement text for the no-JavaScript note, for a page with
            no sibling Markdown file to point at. A shared editable document is
            the case: it is served, not written, so ``markdown_name`` would name
            a file nobody wrote. Wins over ``markdown_name`` when both are given.

    **On JavaScript.** These pages render client-side, so with scripting off
    they would be blank. That is a real regression from the string-templated
    version and it is answered rather than ignored: every exporter writes a
    Markdown file beside the HTML, that file is the primary artifact for
    several of them already, and ``markdown_name`` puts its name in front of
    anyone who lands on the page without a runtime.
    """
    data = export_payload(
        mode=mode,
        title=title,
        wordmark=wordmark,
        report=report,
        subtitle=subtitle,
        facts=facts,
        badges=badges,
        nav=nav,
        footer=footer,
    )

    body = ""
    if noscript:
        body = f'<noscript><p class="noscript">{_e(noscript)}</p></noscript>'
    elif markdown_name:
        body = (
            '<noscript><p class="noscript">This report is drawn in the browser. '
            f"With JavaScript off, the same content is in <code>{_e(markdown_name)}</code>, "
            "written beside this file.</p></noscript>"
        )

    return render_page(
        bundle="export",
        title=document_title or title,
        data=data,
        body=body,
        html_attrs=f'data-mode="{_e(mode)}"',
    )
