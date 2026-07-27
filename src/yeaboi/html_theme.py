"""Shared design system for every shareable/exported HTML page.

One stylesheet (``EXPORT_CSS``) + one page shell (``html_page``) used by all
static exporters (planning, standup, retro export, performance, reporting,
roadmap, analysis, anonymize) and the share gate page, so everything shared
over a tunnel reads as one product family with the retro live board.

The theme palettes are the retro board's (see ``retro/page.py``): midnight is
the default, with light/solarized/synthwave/forest selectable via a small
inline script persisted in localStorage. Pages stay fully self-contained —
no external requests, ever.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

# Token vocabulary (matches the retro live board):
#   --bg --panel --line --text --muted --accent --accent2 --card --ink
# plus semantic tokens for exporters:
#   --ok --warn --danger --info  and the priority ramp --critical --high --medium --low
EXPORT_CSS = """
:root, [data-theme="midnight"] {
  --bg:#0d1117; --panel:#161b22; --line:#30363d; --text:#c9d1d9;
  --muted:#8b949e; --accent:#50bebe; --accent2:#a371f7; --card:#0d1117; --ink:#04211f;
  --ok:#3fb950; --warn:#d29922; --danger:#f85149; --info:#58a6ff;
  --critical:#f85149; --high:#f0883e; --medium:#58a6ff; --low:#8b949e;
}
[data-theme="light"] {
  --bg:#f6f8fa; --panel:#ffffff; --line:#d0d7de; --text:#1f2328;
  --muted:#656d76; --accent:#0969da; --accent2:#8250df; --card:#f6f8fa; --ink:#ffffff;
  --ok:#1a7f37; --warn:#9a6700; --danger:#cf222e; --info:#0969da;
  --critical:#cf222e; --high:#bc4c00; --medium:#0969da; --low:#57606a;
}
[data-theme="solarized"] {
  --bg:#002b36; --panel:#073642; --line:#0a4b59; --text:#eee8d5;
  --muted:#93a1a1; --accent:#2aa198; --accent2:#d33682; --card:#002b36; --ink:#002b36;
  --ok:#859900; --warn:#b58900; --danger:#dc322f; --info:#268bd2;
  --critical:#dc322f; --high:#cb4b16; --medium:#268bd2; --low:#93a1a1;
}
[data-theme="synthwave"] {
  --bg:#1a1033; --panel:#241847; --line:#3d2a6b; --text:#f5e6ff;
  --muted:#a48fd0; --accent:#ff5edb; --accent2:#36e0ff; --card:#150c29; --ink:#1a1033;
  --ok:#3ddc97; --warn:#ffd166; --danger:#ff6b81; --info:#36e0ff;
  --critical:#ff6b81; --high:#ff9e64; --medium:#36e0ff; --low:#a48fd0;
}
[data-theme="forest"] {
  --bg:#0c1a12; --panel:#12261b; --line:#1f3a2a; --text:#d7e8dc;
  --muted:#89a894; --accent:#4cc38a; --accent2:#d9c26a; --card:#0a160f; --ink:#04211a;
  --ok:#4cc38a; --warn:#d9c26a; --danger:#e5534b; --info:#6cb6ff;
  --critical:#e5534b; --high:#db9b4a; --medium:#6cb6ff; --low:#89a894;
}

/* Compat aliases — pre-existing exporter markup references the old token
   names inline (var(--surface) etc.); alias them so it all keeps working. */
:root, [data-theme] {
  --surface: var(--panel);
  --border: var(--line);
  --text-muted: var(--muted);
  --tag-bg: var(--card);
  --accent-dark: var(--accent);
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.5;
  font-size: 15px;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1, h2, h3 { color: var(--text); font-weight: 700; line-height: 1.25; }
h1 { font-size: 1.5rem; margin: 0 0 0.75rem; }
h2 { font-size: 1.15rem; margin: 0 0 0.9rem; }
h3 { font-size: 0.95rem; margin: 0 0 0.5rem; }
p { margin: 0 0 0.75rem; }
ul, ol { margin: 0 0 0.75rem; padding-left: 1.25rem; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85em; }

/* ── Header ─────────────────────────────────────────── */
.site-header {
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  padding: 1.6rem 3rem 1.4rem;
}
.site-header h1 {
  font-size: 1.45rem;
  margin: 0;
  color: var(--accent);
  letter-spacing: 0.01em;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.site-header .subtitle { color: var(--muted); font-size: 0.9rem; margin-top: 0.3rem; }
.site-header .meta {
  margin-top: 0.5rem;
  font-size: 0.82rem;
  color: var(--muted);
  display: flex;
  gap: 1.25rem;
  align-items: center;
  flex-wrap: wrap;
}
.site-header .badge { font-size: 0.75rem; }
.theme-btn {
  margin-left: auto;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 999px;
  color: var(--muted);
  font: inherit;
  font-size: 0.78rem;
  padding: 0.2rem 0.7rem;
  cursor: pointer;
}
.theme-btn:hover { border-color: var(--accent); color: var(--text); }

/* ── Nav ─────────────────────────────────────────────── */
.toc {
  background: var(--panel);
  border-bottom: 1px solid var(--line);
  padding: 0.55rem 3rem;
  display: flex;
  gap: 1.4rem;
  flex-wrap: wrap;
  font-size: 0.82rem;
  position: sticky;
  top: 0;
  z-index: 10;
}
.toc a { color: var(--muted); font-weight: 500; }
.toc a:hover { color: var(--accent); text-decoration: none; }

/* ── Layout ──────────────────────────────────────────── */
.container { max-width: 1100px; margin: 0 auto; padding: 2rem 3rem; }
section { margin-bottom: 3rem; }
section h2 {
  font-size: 1.15rem;
  color: var(--accent);
  letter-spacing: 0.02em;
  border-bottom: 1px solid var(--line);
  padding-bottom: 0.45rem;
  margin-bottom: 1.25rem;
}

/* ── Cards ───────────────────────────────────────────── */
.card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 1.1rem 1.35rem;
  margin-bottom: 1rem;
  transition: border-color 0.15s ease;
}
.card:hover { border-color: var(--accent); border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); }
.card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
  margin-bottom: 0.5rem;
}
.card-title { font-weight: 600; font-size: 0.95rem; }
.card-id { font-size: 0.75rem; color: var(--muted); font-family: ui-monospace, Menlo, monospace; margin-right: 0.5rem; }
.card-desc { font-size: 0.875rem; color: var(--muted); margin-top: 0.3rem; }
.card-meta { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.6rem; }

/* ── Badges / chips ──────────────────────────────────── */
.badge {
  display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px;
  font-size: 0.72rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em; white-space: nowrap;
  background: var(--card); color: var(--muted); border: 1px solid var(--line);
}
.badge-critical { color: var(--critical); background: color-mix(in srgb, var(--critical) 16%, transparent);
                  border-color: color-mix(in srgb, var(--critical) 40%, transparent); }
.badge-high     { color: var(--high); background: color-mix(in srgb, var(--high) 16%, transparent);
                  border-color: color-mix(in srgb, var(--high) 40%, transparent); }
.badge-medium   { color: var(--medium); background: color-mix(in srgb, var(--medium) 16%, transparent);
                  border-color: color-mix(in srgb, var(--medium) 40%, transparent); }
.badge-low      { color: var(--low); background: color-mix(in srgb, var(--low) 16%, transparent);
                  border-color: color-mix(in srgb, var(--low) 40%, transparent); }
.badge-tag      { background: var(--card); color: var(--muted); border: 1px solid var(--line); }
.badge-pts      { color: var(--ok); background: color-mix(in srgb, var(--ok) 14%, transparent);
                  border-color: color-mix(in srgb, var(--ok) 40%, transparent); }
.badge-ok       { color: var(--ok); background: color-mix(in srgb, var(--ok) 16%, transparent);
                  border-color: color-mix(in srgb, var(--ok) 40%, transparent); }
.badge-warn     { color: var(--warn); background: color-mix(in srgb, var(--warn) 16%, transparent);
                  border-color: color-mix(in srgb, var(--warn) 40%, transparent); }
.badge-danger   { color: var(--danger); background: color-mix(in srgb, var(--danger) 16%, transparent);
                  border-color: color-mix(in srgb, var(--danger) 40%, transparent); }
.badge-info     { color: var(--info); background: color-mix(in srgb, var(--info) 16%, transparent);
                  border-color: color-mix(in srgb, var(--info) 40%, transparent); }
.badge-accent   { color: var(--accent); background: color-mix(in srgb, var(--accent) 16%, transparent);
                  border-color: color-mix(in srgb, var(--accent) 40%, transparent); }

/* ── Discipline colours (hues readable on dark and light) ── */
.disc-fullstack { color: var(--accent2); border-color: transparent;
                  background: color-mix(in srgb, var(--accent2) 15%, transparent); }
.disc-frontend  { color: var(--info); border-color: transparent;
                  background: color-mix(in srgb, var(--info) 15%, transparent); }
.disc-backend   { color: var(--ok); border-color: transparent;
                  background: color-mix(in srgb, var(--ok) 15%, transparent); }
.disc-qa        { color: var(--warn); border-color: transparent;
                  background: color-mix(in srgb, var(--warn) 15%, transparent); }
.disc-devops    { color: var(--high); border-color: transparent;
                  background: color-mix(in srgb, var(--high) 15%, transparent); }
.disc-design    { color: var(--accent); border-color: transparent;
                  background: color-mix(in srgb, var(--accent) 15%, transparent); }

/* ── Tables ──────────────────────────────────────────── */
.data-table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
.data-table th {
  background: var(--card);
  text-align: left;
  padding: 0.5rem 0.75rem;
  font-weight: 600;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--muted);
  border-bottom: 1px solid var(--line);
}
.data-table td {
  padding: 0.6rem 0.75rem;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}
.data-table tr:last-child td { border-bottom: none; }
.data-table tr:hover td { background: color-mix(in srgb, var(--accent) 5%, transparent); }
.data-table .mono { font-family: ui-monospace, Menlo, monospace; font-size: 0.8rem; color: var(--muted); }

/* ── Story cards ─────────────────────────────────────── */
.story-card { border-left: 3px solid var(--accent); }
.story-card.critical { border-left-color: var(--critical); }
.story-card.high     { border-left-color: var(--high); }
.story-card.medium   { border-left-color: var(--medium); }
.story-card.low      { border-left-color: var(--low); }

/* ── Acceptance criteria ─────────────────────────────── */
.ac-list { list-style: none; margin-top: 0.6rem; padding-left: 0; }
.ac-list li { font-size: 0.82rem; padding: 0.2rem 0; color: var(--muted); }
.ac-list li + li { border-top: 1px dotted var(--line); padding-top: 0.3rem; }
.ac-given { color: var(--ok); font-weight: 600; }
.ac-when  { color: var(--warn); font-weight: 600; }
.ac-then  { color: var(--accent2); font-weight: 600; }

/* ── Sprint cards ────────────────────────────────────── */
.sprint-card { border-top: 3px solid var(--accent); }
.sprint-header { display: flex; justify-content: space-between; align-items: center; }
.sprint-goal { font-size: 0.875rem; color: var(--muted); margin: 0.5rem 0; }
.capacity-bar { height: 6px; background: var(--line); border-radius: 999px;
                margin: 0.5rem 0 0.75rem; overflow: hidden; }
.capacity-fill { height: 100%; background: var(--accent); border-radius: 999px; max-width: 100%; }
.sprint-stories { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }

/* ── Stat tiles (metric summaries) ───────────────────── */
.stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
             gap: 0.75rem; margin-bottom: 1rem; }
.stat {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 0.85rem 1rem;
}
.stat .num { font-size: 1.35rem; font-weight: 700; color: var(--accent); font-variant-numeric: tabular-nums; }
.stat .lbl { font-size: 0.75rem; color: var(--muted); text-transform: uppercase;
             letter-spacing: 0.05em; margin-top: 0.15rem; }

/* ── Notices ─────────────────────────────────────────── */
.notice {
  background: color-mix(in srgb, var(--warn) 8%, var(--panel));
  border: 1px solid color-mix(in srgb, var(--warn) 35%, transparent);
  border-radius: 10px;
  padding: 0.9rem 1.1rem;
  margin-bottom: 1rem;
}
.notice .notice-title { font-weight: 600; font-size: 0.85rem; color: var(--warn); margin-bottom: 0.4rem; }
.notice ul { margin: 0; padding-left: 1.1rem; font-size: 0.84rem; color: var(--muted); }

/* ── Analysis grid ───────────────────────────────────── */
.analysis-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }
.analysis-section h3 {
  font-size: 0.78rem; text-transform: uppercase;
  letter-spacing: 0.05em; color: var(--muted); margin-bottom: 0.4rem;
}
.analysis-section ul { list-style: none; padding-left: 0; }
.analysis-section ul li { font-size: 0.875rem; padding: 0.15rem 0; }
.analysis-section ul li::before { content: "\\2022 "; color: var(--accent); }
.assumption-item::before { content: "\\26A0 " !important; color: var(--warn) !important; }

/* ── Questionnaire ───────────────────────────────────── */
.q-table { width: 100%; border-collapse: collapse; font-size: 0.84rem; }
.q-table td { padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--line); vertical-align: top; }
.q-table td:first-child { width: 2.5rem; font-weight: 600; color: var(--accent);
                          font-family: ui-monospace, Menlo, monospace; }
.q-table td:nth-child(2) { width: 40%; color: var(--muted); }
.q-table td:nth-child(3) { font-weight: 500; }
.q-table tr:last-child td { border-bottom: none; }

/* ── Footer ──────────────────────────────────────────── */
.site-footer {
  text-align: center;
  font-size: 0.78rem;
  color: var(--muted);
  padding: 2rem;
  border-top: 1px solid var(--line);
  margin-top: 2rem;
}

/* ── Responsive ──────────────────────────────────────── */
@media (max-width: 640px) {
  .site-header, .toc, .container { padding-left: 1rem; padding-right: 1rem; }
  .analysis-grid { grid-template-columns: 1fr; }
}

/* ── Print: always light, hide interactive chrome ────── */
@media print {
  :root, [data-theme] {
    --bg:#ffffff; --panel:#ffffff; --line:#d0d7de; --text:#1f2328;
    --muted:#656d76; --accent:#0969da; --accent2:#8250df; --card:#f6f8fa; --ink:#ffffff;
    --ok:#1a7f37; --warn:#9a6700; --danger:#cf222e; --info:#0969da;
    --critical:#cf222e; --high:#bc4c00; --medium:#0969da; --low:#57606a;
  }
  .theme-btn { display: none; }
  .toc { position: static; }
}
"""

# Theme switcher: a constant script (no caller data ever interpolated) that
# restores the saved theme (else follows prefers-color-scheme) and cycles
# palettes from the header button. Safe no-op when localStorage is unavailable.
_THEME_SCRIPT = """
(function () {
  var KEY = "yeaboi-export-theme";
  var THEMES = ["midnight", "light", "solarized", "synthwave", "forest"];
  var theme = null;
  try { theme = localStorage.getItem(KEY); } catch (e) {}
  if (THEMES.indexOf(theme) < 0) {
    var prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    theme = prefersLight ? "light" : "midnight";
  }
  document.documentElement.setAttribute("data-theme", theme);
  window.__yeaboiCycleTheme = function () {
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    document.documentElement.setAttribute("data-theme", theme);
    try { localStorage.setItem(KEY, theme); } catch (e) {}
  };
})();
"""


def escape(text: str, quote: bool = True) -> str:
    """HTML-escape a value (stringified first) — the one escape helper for every exporter.

    Exporters import this as ``_e`` instead of ``html.escape`` so there is a
    single definition with a single default (``quote=True``).
    """
    return html.escape(str(text), quote)


_e = escape


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


def stat_bar(pct: float, *, color_var: str = "--accent") -> str:
    """A horizontal progress bar filled to ``pct`` percent, colored by a CSS token name."""
    width = max(0, min(100, int(pct)))
    if not color_var.startswith("--") or not color_var[2:].replace("-", "").isalnum():
        color_var = "--accent"
    return (
        f'<div class="capacity-bar">'
        f'<div class="capacity-fill" style="width:{width}%;background:var({color_var})"></div></div>'
    )


def notice_block(title: str, items: Sequence[str]) -> str:
    """The ⚠ notices panel — a titled warning card listing caveats. Empty items → ''."""
    if not items:
        return ""
    lis = "".join(f"<li>{_e(item)}</li>" for item in items)
    return f'<div class="notice"><div class="notice-title">⚠ {_e(title)}</div><ul>{lis}</ul></div>'


# ---------------------------------------------------------------------------
# Page shell
# ---------------------------------------------------------------------------


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
