"""Team profile export — HTML, Markdown, and log reports for team analysis results.

Generates standalone reports from a TeamProfile, reusing the CSS from
html_exporter.py for visual consistency with plan exports.

Exports are sorted into per-project subdirectories under ~/.scrum-agent/exports/:
  ~/.scrum-agent/exports/{project_key}/team-profile-{timestamp}.html
  ~/.scrum-agent/exports/{project_key}/team-profile-{timestamp}.md

Analysis logs are written to ~/.scrum-agent/logs/:
  ~/.scrum-agent/logs/team-analysis-{project_key}-{timestamp}.log
"""

from __future__ import annotations

import html
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from yeaboi.analysis.ai_usage import _source_label
from yeaboi.team_profile import TeamProfile
from yeaboi.tools.team_learning import ANALYSIS_GLOSSARY, INSIGHT_CATEGORIES

logger = logging.getLogger(__name__)

# Display titles for the AI narrative sections (examples["narrative"]["sections"]),
# in the same order as the TUI overview cards.
_NARRATIVE_TITLES = (
    ("velocity", "Velocity & Sprints"),
    ("team", "Team Members"),
    ("estimation", "Estimation & Points"),
    ("workflow", "Workflow & DoD"),
    ("writing", "Writing Style"),
    ("trends", "Trends & Repos"),
    ("recommendations", "Recommendations"),
)

# Jargon definitions shown under the sprint table in both export formats.
_SPRINT_GLOSSARY_KEYS = ("churn", "delta", "spill")


def _project_export_dir(project_key: str, base_dir: Path | None = None) -> Path:
    """Return the per-project analysis export directory, creating it if needed."""
    if base_dir:
        out_dir = base_dir / project_key.lower()
    else:
        from yeaboi.paths import get_analysis_export_dir

        out_dir = get_analysis_export_dir(project_key)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _format_pct(val: float) -> str:
    """Format a percentage, dropping the decimal if it's .0."""
    return f"{val:.0f}%" if val == int(val) else f"{val:.1f}%"


def _e(text: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(text), quote=True)


def _pct_bar_html(pct: float, width_px: int = 120) -> str:
    """Render a thin percentage bar as inline HTML."""
    fill = min(int(pct), 100)
    color = "var(--ok)" if pct >= 80 else ("var(--warn)" if pct >= 50 else "var(--danger)")
    return (
        f'<span style="display:inline-flex;align-items:center;gap:6px;">'
        f'<span style="display:inline-block;width:{width_px}px;height:6px;'
        f'background:var(--line);border-radius:3px;overflow:hidden;">'
        f'<span style="display:block;width:{fill}%;height:100%;background:{color};'
        f'border-radius:3px;"></span></span>'
        f'<span style="font-size:0.8rem;color:var(--text-muted);">{_format_pct(pct)}</span></span>'
    )


def _small_pct(value: float) -> str:
    if 0 < value < 0.1:
        return "<0.1%"
    return f"{value:.1f}%".replace(".0%", "%")


def _footprint_value(ai_sig) -> str:
    """Footprint stat for export: a % — or a raw count when the sample is too
    small for a stable percentage (same rule as the TUI/CLI, Surface Parity)."""
    from yeaboi.analysis.ai_usage import footprint_small_sample

    scanned = getattr(ai_sig, "scanned_commits", 0) + getattr(ai_sig, "scanned_prs", 0)
    marked = getattr(ai_sig, "ai_commits", 0) + getattr(ai_sig, "ai_prs", 0)
    if footprint_small_sample(ai_sig):
        return f"{marked} of {scanned} AI-marked (small sample — % suppressed)"
    return _small_pct(ai_sig.footprint_pct)


def _doc_pages_value(dq_sig, dq_pages: int) -> str:
    """Pages-scanned stat for export, flagged when too few pages for a trend."""
    from yeaboi.analysis.doc_quality import doc_small_sample

    platforms = ", ".join(dq_sig.platforms_scanned) or "n/a"
    suffix = "; small sample — read as examples" if doc_small_sample(dq_sig) else ""
    return f"{dq_pages} ({platforms}{suffix})"


def _coverage_message(report: dict) -> str:
    status = str(report.get("status", "complete")).replace("_", " ")
    completed = int(report.get("completed", 0) or 0)
    eligible = int(report.get("eligible", 0) or 0)
    if status == "complete":
        return f"Complete — {completed:,} of {eligible:,} eligible items analysed"
    if status == "no data":
        return "No matching data was found in the selected scope and time window"
    return f"{status.title()} — {completed:,} of {eligible:,} eligible items analysed"


def _coverage_errors(report: dict) -> list[str]:
    errors = report.get("grouped_errors") or []
    return [
        (
            f"{item.get('provider', '')}: {int(item.get('count', 1) or 1):,} item(s) — "
            f"{item.get('detail', item.get('status', 'failed'))}"
        )
        for item in errors
        if isinstance(item, dict)
    ]


def _section(id_: str, title: str, content: str) -> str:
    """Wrap content in a <section> with id and h2."""
    return f'\n<section id="{id_}"><h2>{_e(title)}</h2>{content}</section>'


def _kv_table(rows: list[tuple[str, str]]) -> str:
    """Render label/value pairs as a two-column card table."""
    trs = "".join(
        f"<tr><td style='width:40%;color:var(--text-muted);'>{_e(lbl)}</td><td style='font-weight:500;'>{v}</td></tr>"
        for lbl, v in rows
    )
    return f'<div class="card" style="padding:0;overflow:hidden;"><table class="data-table">{trs}</table></div>'


def _insight_html(it: dict) -> str:
    """Render one coaching insight as an <li>, linking the cited example when present."""
    body = f"<strong>{_e(str(it.get('title', '')))}</strong> &mdash; {_e(str(it.get('detail', '')))}"
    if it.get("evidence"):
        body += f" <em>({_e(str(it['evidence']))})</em>"
    link = str(it.get("link", "") or "").strip()
    if link:
        body += f' <a href="{_e(link)}">↳ example</a>'
    return f"<li>{body}</li>"


def _insight_md(it: dict) -> str:
    """Render one coaching insight as a Markdown bullet, linking the cited example when present."""
    line = f"- **{it.get('title', '')}** — {it.get('detail', '')}"
    if it.get("evidence"):
        line += f" *({it['evidence']})*"
    link = str(it.get("link", "") or "").strip()
    if link:
        line += f" [↳ example]({link})"
    return line


def _action_html(action: dict) -> str:
    scope = ", ".join(str(value) for value in action.get("affected_scope", []))
    link = str(action.get("link", "") or "")
    title = _e(str(action.get("title", "")))
    title_html = f'<a href="{_e(link)}">{title}</a>' if link else title
    return (
        f"<li><strong>{_e(str(action.get('priority', '')).upper())}: {title_html}</strong>"
        f" — {_e(str(action.get('detail', '')))}"
        f"<br><em>Scope: {_e(scope)} · Owner: {_e(str(action.get('owner_role', '')))}"
        f" · Effort: {_e(str(action.get('effort', '')))}</em></li>"
    )


def _action_md(action: dict) -> str:
    title = str(action.get("title", ""))
    link = str(action.get("link", "") or "")
    if link:
        title = f"[{title}]({link})"
    scope = ", ".join(str(value) for value in action.get("affected_scope", []))
    return (
        f"- **{str(action.get('priority', '')).upper()}: {title}** — {action.get('detail', '')} "
        f"_(Scope: {scope}; Owner: {action.get('owner_role', '')}; Effort: {action.get('effort', '')}; "
        f"Done when: {action.get('completion_check', '')})_"
    )


def _ai_example_md(s: dict) -> str:
    """Render one AI-adoption sample as a Markdown bullet (link when available, else SHA)."""
    tool = "unlabelled AI" if s.get("tool") == "other_ai" else str(s.get("tool", ""))
    title = str(s.get("title", ""))
    url = str(s.get("url", "") or "")
    if url:
        return f"- [{tool}] [{title}]({url}) — {_source_label(str(s.get('source', '')))}"
    key = str(s.get("key", "") or "")
    return f"- [{tool}] {title}" + (f" — commit `{key}`" if key else "")


def _doc_example_md(s: dict) -> str:
    """Render one documentation sample as a Markdown bullet (linked page + scores)."""
    title = str(s.get("title", "Untitled"))
    url = str(s.get("url", "") or "")
    head = f"[{title}]({url})" if url else title
    meta = f"{s.get('platform', '')} · clarity {s.get('clarity', 0):.0f} · usefulness {s.get('usefulness', 0):.0f}"
    return f"- {head} ({meta})"


def _ai_example_html(s: dict) -> str:
    """Render one AI-adoption sample as an <li> (link when available, else SHA/key)."""
    tool = "unlabelled AI" if s.get("tool") == "other_ai" else str(s.get("tool", ""))
    title = _e(str(s.get("title", "")))
    label = f"[{_e(tool)}] {title}"
    url = str(s.get("url", "") or "")
    if url:
        return f'<li>{label} — <a href="{_e(url)}">{_e(_source_label(str(s.get("source", ""))))}</a></li>'
    key = str(s.get("key", "") or "")
    ref = f" — commit {_e(key)}" if key else ""
    return f"<li>{label}{ref}</li>"


def _doc_example_html(s: dict) -> str:
    """Render one documentation sample as an <li> (linked page title + scores)."""
    title = _e(str(s.get("title", "Untitled")))
    meta = (
        f"{_e(str(s.get('platform', '')))} · clarity {s.get('clarity', 0):.0f} "
        f"· usefulness {s.get('usefulness', 0):.0f}"
    )
    url = str(s.get("url", "") or "")
    head = f'<a href="{_e(url)}">{title}</a>' if url else title
    return f"<li>{head} <span style='color:var(--text-muted);'>({meta})</span></li>"


def _ceremony_rows(ceremony) -> list[tuple[str, str]]:
    """Cadence / trend key-value rows shared by the HTML and MD renderers."""
    rows: list[tuple[str, str]] = []
    if ceremony.retro_cadence:
        rows.append(("Retro cadence", ceremony.retro_cadence))
    if ceremony.standup_cadence:
        rows.append(("Standup cadence", ceremony.standup_cadence))
    if ceremony.confidence_trend:
        rows.append(("Standup confidence", ceremony.confidence_trend))
    if ceremony.action_items:
        rows.append(("Open retro action items", str(len(ceremony.action_items))))
    return rows


def _ceremony_html(ceremony) -> str:
    """Render the 'Ceremony Cadence & Trends' section content (HTML)."""
    parts = [_kv_table(_ceremony_rows(ceremony))]
    for title, themes in (
        ("What's been working", ceremony.went_well_themes),
        ("Recurring pain points", ceremony.didnt_go_well_themes),
    ):
        if themes:
            items = "".join(f"<li>{_e(t)} <span style='color:var(--text-muted);'>({n}×)</span></li>" for t, n in themes)
            parts.append(f'<div class="card"><strong>{_e(title)}</strong><ul>{items}</ul></div>')
    return "".join(parts)


def _ceremony_md(ceremony) -> list[str]:
    """Render the 'Ceremony Cadence & Trends' section (Markdown lines)."""
    lines = ["## Ceremony Cadence & Trends", ""]
    lines.extend(f"- **{lbl}:** {val}" for lbl, val in _ceremony_rows(ceremony))
    for title, themes in (
        ("What's been working", ceremony.went_well_themes),
        ("Recurring pain points", ceremony.didnt_go_well_themes),
    ):
        if themes:
            lines.extend(["", f"**{title}:**"])
            lines.extend(f"- {t} ({n}×)" for t, n in themes)
    lines.append("")
    return lines


def build_team_profile_html(
    profile: TeamProfile,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    ceremony=None,
    charts_dir: Path | None = None,
) -> str:
    """Build a self-contained HTML report matching the TUI results screen.

    ``ceremony`` is an optional CeremonyContext (agent/ceremony_history.py). When
    present and non-empty, a "Ceremony Cadence & Trends" section is added.
    """
    from yeaboi.html_theme import html_page

    ex = examples or {}
    sections: list[str] = []
    nav_links: list[tuple[str, str]] = []
    depth = str(ex.get("analysis_depth", "")).strip().lower()
    if depth in ("quick", "deep"):
        _depth_html = f"<p><strong>Analysis depth:</strong> {_e(depth.capitalize())}</p>"
    else:
        _depth_html = ""

    def _nav(id_: str, label: str) -> None:
        nav_links.append((id_, label))

    # ── Executive Summary (AI narrative, generated at analysis time) ─
    narrative = ex.get("narrative", {})
    if isinstance(narrative, dict) and narrative.get("executive_summary"):
        n_html = _depth_html + f"<p>{_e(str(narrative['executive_summary']))}</p>"
        n_sections = narrative.get("sections", {})
        if isinstance(n_sections, dict):
            n_items = "".join(
                f"<li><strong>{_e(title)}:</strong> <em>{_e(str(n_sections[nk]))}</em></li>"
                for nk, title in _NARRATIVE_TITLES
                if n_sections.get(nk)
            )
            if n_items:
                n_html += f"<ul>{n_items}</ul>"
        _nav("summary", "Summary")
        sections.append(_section("summary", "Executive Summary", n_html))

    # ── Team Insights (AI coaching, generated at analysis time) ─────
    insights = ex.get("insights", {})
    if isinstance(insights, dict) and any(insights.get(k) for k, _ in INSIGHT_CATEGORIES):
        i_parts: list[str] = []
        for ik, ilabel in INSIGHT_CATEGORIES:
            i_items = insights.get(ik)
            if not isinstance(i_items, list) or not i_items:
                continue
            i_lis = "".join(
                f"<li><strong>{_e(str(it.get('title', '')))}</strong> &mdash; {_e(str(it.get('detail', '')))}"
                + (f" <em>({_e(str(it['evidence']))})</em>" if it.get("evidence") else "")
                + "</li>"
                for it in i_items
                if isinstance(it, dict) and it.get("title")
            )
            if i_lis:
                i_parts.append(f'<div class="card"><strong>{_e(ilabel)}</strong><ul>{i_lis}</ul></div>')
        if i_parts:
            _nav("insights", "Insights")
            sections.append(_section("insights", "Team Insights", "".join(i_parts)))

    # ── AI Adoption (detectable AI-tool footprint — lower bound) ─────
    ai_sig = getattr(profile, "ai_adoption", None)
    ai_blob = ex.get("ai_adoption", {})
    code_features = set(ai_blob.get("enabled_features") or ("ai_footprint", "code_health"))
    ai_scanned = (getattr(ai_sig, "scanned_commits", 0) + getattr(ai_sig, "scanned_prs", 0)) if ai_sig else 0
    if "ai_footprint" in code_features and ai_sig and ai_scanned:
        disclaimer = (
            '<p class="muted"><em>Lower bound — only AI tools that leave a marker in commit '
            "messages or PR descriptions are counted. Inline IDE assist (Copilot ghost-text, "
            "Cursor Tab) leaves no trace, so real usage is at least this.</em></p>"
        )
        activity_coverage = ai_blob.get("activity_coverage", {}) if isinstance(ai_blob, dict) else {}
        a_rows = [
            ("Coverage", _e(_coverage_message(activity_coverage))),
            ("Detectable footprint", _e(_footprint_value(ai_sig))),
            ("Commits with AI marker", f"{ai_sig.ai_commits} of {ai_sig.scanned_commits}"),
        ]
        if ai_sig.scanned_prs:
            a_rows.append(("PRs with AI marker", f"{ai_sig.ai_prs} of {ai_sig.scanned_prs}"))
        if ai_sig.sources_scanned:
            a_rows.append(("Sources scanned", ", ".join(_source_label(s) for s in ai_sig.sources_scanned)))
        if isinstance(ai_blob, dict) and ai_blob.get("selected_users"):
            a_rows.append(("Selected users", ", ".join(str(u) for u in ai_blob["selected_users"])))
            a_rows.append(
                (
                    "Matched identities",
                    f"{len(ai_blob.get('matched_identities') or {})} of {len(ai_blob['selected_users'])}",
                )
            )
        if getattr(ai_sig, "repos_scanned", ()):
            a_rows.append(("Repositories scanned", str(len(ai_sig.repos_scanned))))
            repo_preview = ", ".join(ai_sig.repos_scanned[:5])
            if len(ai_sig.repos_scanned) > 5:
                repo_preview += f" (+{len(ai_sig.repos_scanned) - 5} more)"
            a_rows.append(("Repository scope", _e(repo_preview)))
        a_html = disclaimer + _kv_table(a_rows)
        if ai_sig.per_tool:
            tool_lis = "".join(
                f"<li>{_e('unlabelled AI' if t == 'other_ai' else t)}: {n}</li>" for t, n in ai_sig.per_tool
            )
            a_html += f"<h4>By tool</h4><ul>{tool_lis}</ul>"
        if getattr(ai_sig, "per_source", ()):
            src_lis = "".join(f"<li>{_e(_source_label(s))}: {n}</li>" for s, n in ai_sig.per_source)
            a_html += f"<h4>By source</h4><ul>{src_lis}</ul>"
        if ai_sig.per_activity:
            act_lis = "".join(f"<li>{_e(a)}: {n}</li>" for a, n in ai_sig.per_activity)
            a_html += f"<h4>By activity</h4><ul>{act_lis}</ul>"
        if ai_sig.per_author:
            auth_lis = "".join(f"<li>{_e(a)}: {n}</li>" for a, n in ai_sig.per_author[:8])
            a_html += f"<h4>By contributor</h4><ul>{auth_lis}</ul>"
        ai_coverage = ai_blob.get("coverage") if isinstance(ai_blob, dict) else None
        if ai_coverage:
            gap_lis = "".join(f"<li>{_e(g)}</li>" for g in ai_coverage)
            a_html += f"<h4>Not scanned</h4><ul>{gap_lis}</ul>"
        ai_samples = ai_blob.get("samples") if isinstance(ai_blob, dict) else None
        if ai_samples:
            ex_lis = "".join(_ai_example_html(s) for s in ai_samples)
            a_html += f"<h4>Examples</h4><ul>{ex_lis}</ul>"
        ai_insights = ai_blob.get("insights", {}) if isinstance(ai_blob, dict) else {}
        if isinstance(ai_insights, dict) and any(ai_insights.get(k) for k, _ in INSIGHT_CATEGORIES):
            for ik, ilabel in INSIGHT_CATEGORIES:
                i_items = ai_insights.get(ik)
                if not isinstance(i_items, list) or not i_items:
                    continue
                i_lis = "".join(_insight_html(it) for it in i_items if isinstance(it, dict) and it.get("title"))
                if i_lis:
                    a_html += f'<div class="card"><strong>{_e(ilabel)}</strong><ul>{i_lis}</ul></div>'
        _nav("ai-adoption", "AI Footprint")
        sections.append(_section("ai-adoption", "AI Footprint", a_html))

    file_health = ai_blob.get("repository_health", {}) if isinstance(ai_blob, dict) else {}
    if "code_health" in code_features and file_health:
        health_coverage = ai_blob.get("coverage_report", {})
        health_failed = health_coverage.get("status") in {"failed", "no_data"}
        h_rows = [("Coverage", _e(_coverage_message(health_coverage)))]
        if not health_failed:
            h_rows.extend(
                [
                    ("Changed files analysed", str(file_health.get("files_analysed", 0))),
                    ("Repositories touched", str(file_health.get("repositories_touched", 0))),
                    ("Findings", str(file_health.get("findings", 0))),
                ]
            )
        h_html = (
            '<p class="muted"><em>Scoped to files attributable to the selected users. '
            "Untouched repositories and unrelated contributors are not analysed.</em></p>" + _kv_table(h_rows)
        )
        errors = _coverage_errors(health_coverage)
        if errors:
            h_html += "<h4>Collection errors</h4><ul>" + "".join(f"<li>{_e(error)}</li>" for error in errors) + "</ul>"
        health_actions = ai_blob.get("action_plan", []) if isinstance(ai_blob, dict) else []
        if health_actions and not health_failed:
            h_html += (
                "<h4>Prioritized action plan</h4><ol>"
                + "".join(_action_html(action) for action in health_actions)
                + "</ol>"
            )
        _nav("code-health", "Code Health")
        sections.append(_section("code-health", "Code Health", h_html))

    # ── Documentation (Notion/Confluence clarity + usefulness) ────────────
    dq_sig = getattr(profile, "doc_quality", None)
    dq_blob = ex.get("doc_quality", {})
    dq_pages = getattr(dq_sig, "pages_scanned", 0) if dq_sig else 0
    if dq_sig and isinstance(dq_blob, dict):
        doc_coverage = dq_blob.get("coverage_report", {})
        doc_failed = doc_coverage.get("status") in {"failed", "no_data"}
        dq_disclaimer = (
            '<p class="muted"><em>Clarity is a readability score. Usefulness measures purpose, '
            "ownership, structure, and actionability. Explicit AI markers are a lower bound.</em></p>"
        )
        d_rows = [("Coverage", _e(_coverage_message(doc_coverage)))]
        if not doc_failed:
            d_split = f"{dq_sig.clear_pages} clear / {dq_sig.mixed_pages} mixed / {dq_sig.unclear_pages} unclear"
            d_rows.extend(
                [
                    ("Average clarity", f"{dq_sig.avg_clarity:.0f}/100"),
                    ("Average usefulness", f"{getattr(dq_sig, 'avg_usefulness', 0):.0f}/100"),
                    ("Pages scanned", _doc_pages_value(dq_sig, dq_pages)),
                    ("Clarity split", d_split),
                    (
                        "Owned / actionable",
                        f"{getattr(dq_sig, 'owned_pages', 0)} / {getattr(dq_sig, 'actionable_pages', 0)}",
                    ),
                    ("Explicit AI markers", f"{dq_sig.ai_marked_pages} page(s) (lower bound)"),
                ]
            )
        d_html = dq_disclaimer + _kv_table(d_rows)
        doc_errors = _coverage_errors(doc_coverage)
        if doc_errors:
            d_html += (
                "<h4>Collection errors</h4><ul>" + "".join(f"<li>{_e(error)}</li>" for error in doc_errors) + "</ul>"
            )
        if dq_sig.flagged_pages and not doc_failed:
            flag_lis = "".join(f"<li>{_e(title)}: {_e(reason)}</li>" for title, reason in dq_sig.flagged_pages)
            d_html += f"<h4>Flagged pages</h4><ul>{flag_lis}</ul>"
        dq_samples = dq_blob.get("samples") if isinstance(dq_blob, dict) else None
        if dq_samples and not doc_failed:
            ex_lis = "".join(_doc_example_html(s) for s in dq_samples)
            d_html += f"<h4>Examples</h4><ul>{ex_lis}</ul>"
        dq_insights = dq_blob.get("insights", {}) if isinstance(dq_blob, dict) else {}
        if not doc_failed and isinstance(dq_insights, dict) and any(dq_insights.get(k) for k, _ in INSIGHT_CATEGORIES):
            for ik, ilabel in INSIGHT_CATEGORIES:
                i_items = dq_insights.get(ik)
                if not isinstance(i_items, list) or not i_items:
                    continue
                i_lis = "".join(_insight_html(it) for it in i_items if isinstance(it, dict) and it.get("title"))
                if i_lis:
                    d_html += f'<div class="card"><strong>{_e(ilabel)}</strong><ul>{i_lis}</ul></div>'
        dq_actions = dq_blob.get("action_plan", []) if isinstance(dq_blob, dict) else []
        if dq_actions and not doc_failed:
            d_html += "<h4>Prioritized action plan</h4><ol>" + "".join(_action_html(a) for a in dq_actions) + "</ol>"
        _nav("documentation", "Documentation")
        sections.append(_section("documentation", "Documentation", d_html))

    # ── Team & Velocity ─────────────────────────────────────────────
    vel_rows: list[tuple[str, str]] = []
    team_sz = ex.get("team_size", 0)
    members = ex.get("team_members", [])
    per_dev = ex.get("per_dev_velocity", 0)

    if team_sz and isinstance(team_sz, int):
        mem_str = f"{team_sz} contributors"
        if members and isinstance(members, list):
            mem_str += f" ({', '.join(str(m) for m in members[:8])})"
        vel_rows.append(("Team size", mem_str))

    # Use sprint_details for accurate velocity if available
    sp_details = ex.get("sprint_details", [])
    if isinstance(sp_details, list) and sp_details:
        import math as _m

        sp_pts = [sd["points"] for sd in sp_details if isinstance(sd, dict) and sd.get("points", 0) > 0]
        vel = round(sum(sp_pts) / len(sp_pts), 1) if sp_pts else profile.velocity_avg
        std = (
            round(_m.sqrt(sum((x - sum(sp_pts) / len(sp_pts)) ** 2 for x in sp_pts) / len(sp_pts)), 1)
            if len(sp_pts) >= 2
            else profile.velocity_stddev
        )
    else:
        vel = profile.velocity_avg
        std = profile.velocity_stddev

    vel_rows.append(("Team velocity", f"{vel} pts/sprint"))
    _html_scope = ex.get("scope_changes", {})
    if isinstance(_html_scope, dict) and _html_scope.get("totals"):
        _hcv = _html_scope["totals"].get("avg_committed_velocity", 0.0)
        _hdv = _html_scope["totals"].get("avg_delivered_velocity", 0.0)
        if _hcv > 0:
            _hdp = round(_hdv / _hcv * 100)
            _hdc = "var(--ok)" if _hdp >= 85 else ("var(--warn)" if _hdp >= 70 else "var(--danger)")
            vel_rows.append(("Committed avg", f"{_hcv:g} pts/sprint"))
            vel_rows.append(
                (
                    "Delivered avg",
                    f'{_hdv:g} pts/sprint <span style="color:{_hdc};">({_hdp}% accuracy)</span>',
                )
            )
    _hv_cs = ex.get("contributor_stats", [])
    if isinstance(_hv_cs, list) and _hv_cs:
        _hv_vals = [c.get("per_sprint", 0) for c in _hv_cs if c.get("per_sprint", 0) > 0]
        if _hv_vals:
            _hv_avg = round(sum(_hv_vals) / len(_hv_vals), 1)
            vel_rows.append(("Per developer", f"{_hv_avg} pts/sprint"))
    elif per_dev and isinstance(per_dev, (int, float)) and per_dev > 0:
        vel_rows.append(("Per developer", f"{per_dev} pts/sprint"))
    if vel > 0:
        var_pct = std / vel * 100
        vel_rows.append(("Variance", f"&pm;{std} ({var_pct:.0f}%)"))
    if profile.sprint_completion_rate > 0:
        vel_rows.append(("Completion rate", _pct_bar_html(profile.sprint_completion_rate)))
    if profile.spillover.carried_over_pct > 0:
        vel_rows.append(("Spillover", f"{_format_pct(profile.spillover.carried_over_pct)} carried over"))

    # Velocity trend
    vt = ex.get("velocity_trend", {})
    if isinstance(vt, dict) and vt.get("trend") and vt["trend"] != "insufficient_data":
        trend = vt["trend"]
        slope = vt.get("slope", 0)
        first_v = vt.get("first_velocity", 0)
        last_v = vt.get("last_velocity", 0)
        icon = {"improving": "&#x2197;", "degrading": "&#x2198;"}.get(trend, "&#x2192;")
        color = {"improving": "var(--ok)", "degrading": "var(--danger)"}.get(trend, "var(--text-muted)")
        vel_rows.append(
            (
                "Trend",
                f'<span style="color:{color};font-weight:600;">{icon} {_e(trend.capitalize())}</span>'
                f" ({first_v} &rarr; {last_v}, {slope:+.1f}/sprint)",
            )
        )

    _nav("velocity", "Velocity")
    sections.append(_section("velocity", "Team & Velocity", _kv_table(vel_rows)))

    # ── Ceremony cadence & trends (Standup + Retro history) ─────────
    if ceremony is not None and not ceremony.is_empty:
        _nav("ceremonies", "Ceremonies")
        sections.append(_section("ceremonies", "Ceremony Cadence & Trends", _ceremony_html(ceremony)))

    # ── Recurring work ──────────────────────────────────────────────
    rec_count = ex.get("recurring_count", 0)
    del_count = ex.get("delivery_count", 0)
    rec_items = ex.get("recurring", [])
    if rec_count and isinstance(rec_count, int) and rec_count > 0:
        rec_html = (
            f'<p style="color:var(--text-muted);margin-bottom:0.5rem;">'
            f"{rec_count} recurring tickets excluded "
            f"({del_count} delivery stories analysed)</p>"
        )
        if rec_items and isinstance(rec_items, list):
            rec_lis = "".join(
                f"<li><code>{_e(r.get('issue_key', ''))}</code> {_e(r.get('summary', ''))}</li>"
                for r in rec_items[:5]
                if isinstance(r, dict)
            )
            rec_html += f'<ul style="color:var(--text-muted);font-size:0.85rem;">{rec_lis}</ul>'
        sections.append(f'\n<div class="card" style="border-left:3px solid var(--medium);">{rec_html}</div>')

    # ── Spillover Root Causes ───────────────────────────────────────
    spill_corr = ex.get("spillover_correlation", {})
    if isinstance(spill_corr, dict) and spill_corr:
        by_size = spill_corr.get("by_size", {})
        by_disc = spill_corr.get("by_discipline", {})
        by_tasks = spill_corr.get("by_task_count", {})
        has_spill = any(v > 0 for d in (by_size, by_disc, by_tasks) if isinstance(d, dict) for v in d.values())
        if has_spill:
            sc_rows: list[tuple[str, str]] = []
            if by_size:
                sorted_sizes = sorted(by_size.items(), key=lambda x: int(x[0]))
                parts = " &middot; ".join(f"{sz}pt={pct:.0f}%" for sz, pct in sorted_sizes)
                sc_rows.append(("By story size", parts))
            if by_disc:
                parts = " &middot; ".join(f"{d}={pct:.0f}%" for d, pct in sorted(by_disc.items()))
                sc_rows.append(("By discipline", parts))
            if by_tasks:
                parts = " &middot; ".join(f"{b}={pct:.0f}%" for b, pct in by_tasks.items())
                sc_rows.append(("By task count", parts))
            _nav("spillover", "Spillover")
            sections.append(_section("spillover", "Spillover Root Causes", _kv_table(sc_rows)))

    # ── Sprint Breakdown ────────────────────────────────────────────
    if sp_details and isinstance(sp_details, list) and len(sp_details) > 0:
        sp_hdr = "<tr><th>Sprint</th><th>Pts</th><th>Done</th><th>Rate</th><th></th></tr>"
        sp_rows_html = []
        for sd in sp_details:
            if not isinstance(sd, dict):
                continue
            name = _e(sd.get("name", "?"))
            pts = sd.get("points", 0)
            planned = sd.get("planned", 0)
            completed = sd.get("completed", 0)
            rate = sd.get("rate", 0)
            done = sd.get("done", False)
            has_shadow = sd.get("has_shadow", False)
            icon = "&#x2713;" if done else ("&#x25cb;" if has_shadow else "&#x2717;")
            icon_color = "var(--ok)" if done else ("var(--warn)" if has_shadow else "var(--danger)")
            rate_color = "var(--ok)" if rate >= 80 else ("var(--warn)" if rate >= 50 else "var(--danger)")
            sp_rows_html.append(
                f"<tr><td>{name}</td><td>{pts}</td><td>{completed}/{planned}</td>"
                f'<td style="color:{rate_color};font-weight:600;">{rate}%</td>'
                f'<td style="color:{icon_color};">{icon}</td></tr>'
            )
        if sp_rows_html:
            # Velocity chart (optional charts extra) — base64-embedded above
            # the table so the HTML stays self-contained/offline.
            from yeaboi.charts import velocity_chart
            from yeaboi.html_exporter import img_b64_tag

            chart_rows = [
                (str(sd.get("name", "?")), float(sd.get("planned", 0) or 0), float(sd.get("completed", 0) or 0))
                for sd in sp_details
                if isinstance(sd, dict)
            ]
            chart = velocity_chart(chart_rows, charts_dir / "velocity.png") if charts_dir is not None else None
            chart_html = img_b64_tag(chart, "Sprint velocity") if chart else ""
            sprint_content = chart_html + (
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<table class="data-table">{sp_hdr}{"".join(sp_rows_html)}</table></div>'
            )

            # Incomplete sprint analysis
            incomplete = [
                sd
                for sd in sp_details
                if isinstance(sd, dict)
                and (not sd.get("done", False) or sd.get("has_shadow", False))
                and sd.get("incomplete")
            ]
            if incomplete:
                sprint_content += (
                    '<h3 style="font-size:0.9rem;color:var(--text-muted);'
                    'margin-top:1rem;">Incomplete sprint analysis</h3>'
                )
                for sd in incomplete[:3]:
                    sname = _e(sd.get("name", "?"))
                    gap = sd.get("planned", 0) - sd.get("completed", 0)
                    has_sh = sd.get("has_shadow", False)
                    label_parts = []
                    if gap > 0:
                        label_parts.append(f"{gap} stories not completed")
                    if has_sh:
                        label_parts.append("shadow spillover")
                    sprint_content += (
                        f'<div class="card" style="border-left:3px solid var(--warn);margin:0.5rem 0;">'
                        f'<strong style="color:var(--warn);">{sname}</strong>'
                        f'<span style="color:var(--text-muted);margin-left:0.5rem;">{" + ".join(label_parts)}</span>'
                    )
                    for item in sd.get("incomplete", [])[:3]:
                        if not isinstance(item, dict):
                            continue
                        ek = _e(item.get("issue_key", ""))
                        sm = _e(item.get("summary", ""))
                        shadow = item.get("shadow", False)
                        pts_v = item.get("points", 0)
                        detail = " (re-created)" if shadow else (f" ({pts_v}pts)" if pts_v else "")
                        sprint_content += (
                            f'<div style="margin-left:1rem;font-size:0.85rem;color:var(--text-muted);">'
                            f"<code>{ek}</code> {sm}"
                            f'<span style="color:var(--warn);">{detail}</span></div>'
                        )
                    sprint_content += "</div>"

            # Append scope tracking into sprint breakdown section
            _sc_scope = ex.get("scope_changes", {})
            if isinstance(_sc_scope, dict) and _sc_scope.get("totals"):
                _sc_t = _sc_scope["totals"]
                _sc_a = _sc_t.get("added_mid_sprint", 0)
                _sc_r = _sc_t.get("re_estimated", 0)
                _sc_n = _sc_t.get("total_stories", 0)
                _sc_cv = _sc_t.get("avg_committed_velocity", 0.0)
                _sc_dv = _sc_t.get("avg_delivered_velocity", 0.0)
                if _sc_a > 0 or _sc_r > 0 or _sc_cv > 0:
                    sprint_content += '<hr style="border:none;border-top:1px solid var(--border);margin:1rem 0;">'
                    if _sc_cv > 0:
                        _dp = round(_sc_dv / _sc_cv * 100)
                        _dc = "var(--ok)" if _dp >= 85 else ("var(--warn)" if _dp >= 70 else "var(--danger)")
                        sprint_content += (
                            f"<p>Committed <strong>{_sc_cv:g}</strong> &rarr; "
                            f"Delivered <strong>{_sc_dv:g}</strong> pts/sprint avg "
                            f'<span style="color:{_dc};">({_dp}% accuracy)</span></p>'
                        )
                    if _sc_n > 0 and (_sc_a > 0 or _sc_r > 0):
                        sprint_content += (
                            f'<p style="font-size:0.85rem;">{_sc_a} added mid-sprint '
                            f"({_sc_a * 100 // _sc_n}%) &middot; "
                            f"{_sc_r} re-estimated ({_sc_r * 100 // _sc_n}%)</p>"
                        )
                    _sc_tls = _sc_scope.get("timelines", [])
                    _sc_we = [t for t in _sc_tls if hasattr(t, "change_events") and t.change_events]
                    for tl in _sc_we[-4:]:
                        _d = tl.scope_change_total
                        _p = round(_d / tl.committed_pts * 100) if tl.committed_pts else 0
                        _ds = f"+{_d:g}" if _d > 0 else f"{_d:g}"
                        _dcol = "var(--ok)" if _d == 0 else ("var(--warn)" if abs(_d) < 5 else "var(--danger)")
                        _ns = len(tl.daily_snapshots[0].stories_in_sprint) if tl.daily_snapshots else 0
                        _nf = len(tl.daily_snapshots[-1].stories_in_sprint) if tl.daily_snapshots else 0
                        sprint_content += (
                            f'<div style="margin:1rem 0 0.5rem 0;padding:0.5rem;'
                            f'border-left:3px solid {_dcol};background:rgba(255,255,255,0.02);">'
                            f"<strong>{_e(tl.sprint_name)}</strong> "
                            f'<span style="color:{_dcol};">{_ds} scope ({_p:+d}%)</span>'
                            f'<div style="font-size:0.85rem;color:var(--text-muted);margin:0.25rem 0;">'
                            f"committed {tl.committed_pts:g} pts ({_ns} stories)</div>"
                        )
                        for ev in tl.change_events[:5]:
                            ct = ev.change_type.replace("re_estimated_", "re-est ").replace("_", " ")
                            evd = f"+{ev.delta_pts:g}" if ev.delta_pts > 0 else f"{ev.delta_pts:g}"
                            evc = (
                                "var(--ok)"
                                if ev.delta_pts < 0
                                else ("var(--warn)" if abs(ev.delta_pts) <= 3 else "var(--danger)")
                            )
                            sprint_content += (
                                f'<div style="font-size:0.85rem;margin:0.1rem 0 0 1rem;">'
                                f'<span style="color:{evc};">{evd} pts</span> '
                                f"<code>{_e(ev.issue_key)}</code> {_e(ct)}"
                            )
                            if ev.summary:
                                sprint_content += (
                                    f' <span style="color:var(--text-muted);">{_e(ev.summary[:45])}</span>'
                                )
                            sprint_content += "</div>"
                        if len(tl.change_events) > 5:
                            sprint_content += (
                                f'<div style="font-size:0.8rem;margin-left:1rem;color:var(--text-muted);">'
                                f"... +{len(tl.change_events) - 5} more</div>"
                            )
                        sprint_content += (
                            f'<div style="font-size:0.85rem;color:var(--text-muted);margin:0.25rem 0;">'
                            f"final {tl.final_pts:g} pts ({_nf} stories) &middot; "
                            f"delivered {tl.delivered_pts:g} pts</div></div>"
                        )
                    _sc_chains = _sc_scope.get("carry_over_chains", [])
                    if _sc_chains:
                        sprint_content += (
                            f'<h3 style="font-size:0.85rem;color:var(--warn);margin-top:0.75rem;">'
                            f"{len(_sc_chains)} stories bounced across 3+ sprints</h3>"
                        )
                        for ch in _sc_chains[:5]:
                            if isinstance(ch, dict):
                                ek = _e(ch.get("issue_key", ""))
                                sps = " &rarr; ".join(_e(str(s)) for s in ch.get("sprints", []))
                                sprint_content += (
                                    f'<div style="margin:0.2rem 0 0 1rem;font-size:0.85rem;">'
                                    f"<code>{ek}</code> {sps}</div>"
                                )

            _nav("sprints", "Sprints")
            sprint_content += (
                '<p style="color:var(--text-muted);font-size:0.85rem;">'
                + " &middot; ".join(_e(ANALYSIS_GLOSSARY[g]) for g in _SPRINT_GLOSSARY_KEYS)
                + "</p>"
            )
            sections.append(_section("sprints", "Sprint Breakdown", sprint_content))

    # ── Team Members ───────────────────────────────────────────────
    _h_contrib = ex.get("contributor_stats", [])
    if isinstance(_h_contrib, list) and _h_contrib:
        # Interrupted work summary
        _h_total_rec = sum(c.get("recurring_pts", 0) for c in _h_contrib)
        _h_total_del = sum(c.get("delivery_pts", 0) for c in _h_contrib)
        tm_content = ""
        if _h_total_rec > 0:
            _h_tot = _h_total_rec + _h_total_del
            _h_rec_pct = round(_h_total_rec / _h_tot * 100) if _h_tot else 0
            tm_content += (
                f"<p>Interrupted work: <strong>{_h_total_rec:g} pts</strong> ({_h_rec_pct}% of total effort)</p>"
            )
        # Contributor table
        tm_content += (
            '<table class="data-table"><tr>'
            "<th>Name</th><th>Delivered</th><th>Stories</th>"
            "<th>Spill%</th><th>Cycle</th><th>Sprints</th><th>Focus</th><th>Pts/sprint</th>"
            "</tr>"
        )
        for cs in _h_contrib[:10]:
            sp_r = cs.get("spill_rate", 0)
            sp_col = "var(--ok)" if sp_r < 10 else ("var(--warn)" if sp_r < 25 else "var(--danger)")
            ct_v = cs.get("avg_cycle_time", 0)
            ct_s = f"{ct_v:.0f}d" if ct_v > 0 else "&mdash;"
            disc = cs.get("top_discipline", "fullstack")
            wt = cs.get("top_work_type", "")
            focus = f"{disc}/{wt.split('/')[0]}" if wt else disc
            ps = cs.get("per_sprint", 0)
            ps_col = "var(--ok)" if ps >= 3 else ("var(--warn)" if ps >= 1.5 else "var(--low)")
            sa = cs.get("sprints_active", 0)
            tm_content += (
                f"<tr><td>{_e(cs.get('name', ''))}</td>"
                f'<td style="text-align:right;">{cs.get("delivery_pts", 0)}</td>'
                f'<td style="text-align:right;">{cs.get("stories_completed", 0)}</td>'
                f'<td style="text-align:right;color:{sp_col};">{sp_r}%</td>'
                f'<td style="text-align:right;">{ct_s}</td>'
                f'<td style="text-align:right;">{sa}</td>'
                f"<td>{_e(focus[:18])}</td>"
                f'<td style="text-align:right;color:{ps_col};">{ps}</td></tr>'
            )
        tm_content += "</table>"
        # Insights
        if len(_h_contrib) >= 3 and _h_total_del > 0:
            top = _h_contrib[0]
            top_pct = round(top["delivery_pts"] / _h_total_del * 100)
            if top_pct >= 40:
                tm_content += (
                    f'<p style="color:var(--warn);">&#x26a0; {_e(top["name"])} carries {top_pct}% of delivery work</p>'
                )
        _nav("team-members", "Team")
        sections.append(_section("team-members", "Team Members", tm_content))

    # ── Shadow Spillover ────────────────────────────────────────────
    shadow = ex.get("shadow_spillover", [])
    if isinstance(shadow, list) and shadow:
        shadow_html = (
            f'<div class="card" style="border-left:3px solid var(--warn);">'
            f'<strong style="color:var(--warn);">&#x26a0; {len(shadow)} re-created stories detected</strong>'
            f'<p style="color:var(--text-muted);">Closed in one sprint but re-created in the next:</p>'
        )
        for sh in shadow[:5]:
            if not isinstance(sh, dict):
                continue
            ek = _e(sh.get("issue_key", ""))
            url = sh.get("issue_url", "")
            title = _e(sh.get("title", ""))
            from_sp = _e(sh.get("from_sprint", ""))
            to_sp = _e(sh.get("to_sprint", ""))
            key_html = f'<a href="{_e(url)}"><code>{ek}</code></a>' if url else f"<code>{ek}</code>"
            shadow_html += (
                f'<div style="margin:0.3rem 0 0 1rem;font-size:0.85rem;">'
                f"{key_html} {title}"
                f'<span style="color:var(--text-muted);margin-left:0.5rem;">{from_sp} &rarr; {to_sp}</span>'
                f"</div>"
            )
        shadow_html += "</div>"
        sections.append(f"\n{shadow_html}")

    # ── Discipline-Specific Calibration ─────────────────────────────
    disc_cal = ex.get("discipline_calibration", {})
    if isinstance(disc_cal, dict) and len(disc_cal) > 1:
        disc_content = ""
        for disc, entries in sorted(disc_cal.items()):
            if not isinstance(entries, list) or not entries:
                continue
            disc_hdr = "<tr><th>Points</th><th>Cycle time</th><th>Variance</th><th>Samples</th><th>Spillover</th></tr>"
            disc_rows = ""
            for e in entries:
                if not isinstance(e, dict):
                    continue
                pts = e.get("points", 0)
                avg_d = e.get("avg_cycle_days", 0)
                var = e.get("variance", 0)
                samples = e.get("samples", 0)
                sp = e.get("spill_pct", 0)
                var_html = f"&pm;{var:.0f}d" if var > 0 else "&mdash;"
                sp_color = "var(--ok)" if sp < 10 else ("var(--warn)" if sp < 25 else "var(--danger)")
                sp_html = f'<span style="color:{sp_color};">{sp:.0f}%</span>' if sp > 0 else "&mdash;"
                disc_rows += (
                    f"<tr><td>{pts}pt{'s' if pts != 1 else ''}</td>"
                    f"<td>{avg_d:.0f}d</td><td>{var_html}</td>"
                    f"<td>{samples}</td><td>{sp_html}</td></tr>"
                )
            disc_content += (
                f'<h3 style="font-size:0.9rem;margin-top:1rem;">{_e(disc)}</h3>'
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<table class="data-table">{disc_hdr}{disc_rows}</table></div>'
            )
        _nav("disc-cal", "Discipline Cal.")
        sections.append(_section("disc-cal", "Calibration by Discipline", disc_content))

    # ── Point Calibration ───────────────────────────────────────────
    cals = [c for c in profile.point_calibrations if c.sample_count > 0]
    _raw_conf = ex.get("confidence_levels", {})
    # JSON round-trip may stringify int keys — normalise to int keys
    conf_levels: dict[int, str] = {}
    if isinstance(_raw_conf, dict):
        for k, v in _raw_conf.items():
            try:
                conf_levels[int(k)] = str(v)
            except (ValueError, TypeError):
                pass
    if cals:
        cal_hdr = (
            "<tr><th>Points</th><th>Avg cycle time</th><th>Samples</th>"
            "<th>Tasks</th><th>Slip</th><th>Confidence</th></tr>"
        )
        cal_rows_html = []
        for c in cals:
            conf = conf_levels.get(c.point_value, "")
            conf_color = {"high": "var(--ok)", "medium": "var(--text-muted)", "low": "var(--warn)"}.get(conf, "")
            conf_html = f'<span style="color:{conf_color};font-weight:600;">{conf.upper()}</span>' if conf else ""
            cal_rows_html.append(
                f"<tr><td><strong>{c.point_value} pt{'s' if c.point_value != 1 else ''}</strong></td>"
                f"<td>{c.avg_cycle_time_days:.0f} days</td>"
                f"<td>{c.sample_count}</td>"
                f"<td>~{c.typical_task_count:.0f}</td>"
                f"<td>{_format_pct(c.overshoot_pct)}</td>"
                f"<td>{conf_html}</td></tr>"
            )
            if c.common_patterns:
                pats = ", ".join(_e(p) for p in c.common_patterns)
                cal_rows_html.append(
                    f'<tr><td colspan="6" style="color:var(--text-muted);font-size:0.8rem;'
                    f'padding-left:2rem;">Typical: {pats}</td></tr>'
                )
            # Issue key examples
            cal_examples = ex.get(f"calibration_{c.point_value}pt", [])
            for ce in cal_examples[:2]:
                if not isinstance(ce, dict):
                    continue
                ek = _e(ce.get("issue_key", ""))
                url = ce.get("issue_url", "")
                sm = _e(ce.get("summary", ""))
                detail = _e(ce.get("detail", ""))
                key_html = f'<a href="{_e(url)}"><code>{ek}</code></a>' if url else f"<code>{ek}</code>"
                cal_rows_html.append(
                    f'<tr><td colspan="6" style="font-size:0.8rem;padding-left:2rem;">'
                    f'{key_html} <span style="color:var(--text-muted);">{sm}</span>'
                    f"{f' <em>{detail}</em>' if detail else ''}</td></tr>"
                )
        cal_table = (
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<table class="data-table">{cal_hdr}{"".join(cal_rows_html)}</table></div>'
        )
        _nav("calibration", "Calibration")
        sections.append(_section("calibration", "What Each Point Value Means", cal_table))

    # ── Story Shapes ────────────────────────────────────────────────
    shapes = [s for s in profile.story_shapes if s.sample_count > 0]
    if shapes:
        sh_hdr = "<tr><th>Discipline</th><th>Avg pts</th><th>Avg ACs</th><th>Avg tasks</th><th>Samples</th></tr>"
        sh_rows = "".join(
            f"<tr><td><strong>{_e(s.discipline)}</strong></td><td>{s.avg_points}</td>"
            f"<td>{s.avg_ac_count}</td><td>{s.avg_task_count}</td><td>{s.sample_count}</td></tr>"
            for s in shapes
        )
        shape_table = (
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<table class="data-table">{sh_hdr}{sh_rows}</table></div>'
        )
        _nav("shapes", "Story Shapes")
        sections.append(_section("shapes", "Story Shape by Discipline", shape_table))

    # ── Task Decomposition ──────────────────────────────────────────
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict) and td.get("total_tasks", 0) > 0:
        td_rows: list[tuple[str, str]] = [
            ("Stories with tasks", f"{td['stories_with_tasks']} / {td['total_stories']}"),
            ("Total tasks", str(td["total_tasks"])),
            ("Avg tasks/story", str(td["avg_tasks_per_story"])),
            ("Task completion", _pct_bar_html(td["task_completion_rate"])),
        ]
        td_content = _kv_table(td_rows)

        type_dist = td.get("type_distribution", {})
        if type_dist:
            dist_rows = "".join(
                f"<tr><td>{_e(cat)}</td><td>{_pct_bar_html(pct)}</td></tr>" for cat, pct in type_dist.items()
            )
            td_content += (
                f'<div class="card" style="padding:0;overflow:hidden;margin-top:0.5rem;">'
                f'<table class="data-table">{dist_rows}</table></div>'
            )

        # Bottlenecks
        bottlenecks = td.get("bottlenecks", [])
        for cat, rate_val, count in bottlenecks:
            td_content += (
                f'<div class="card" style="border-left:3px solid var(--warn);margin-top:0.5rem;">'
                f'<strong style="color:var(--warn);">&#x26a0; {_e(str(cat))} bottleneck</strong>'
                f'<p style="color:var(--text-muted);">'
                f"Only {rate_val}% completion ({count} tasks)</p></div>"
            )

        # Common task patterns
        common_tasks = td.get("common_tasks", [])
        if common_tasks:
            ct_rows = "".join(
                f"<tr><td>{_e(str(title)[:45])}</td><td>&times;{cnt}</td></tr>" for title, cnt in common_tasks[:4]
            )
            td_content += (
                f'<h3 style="font-size:0.85rem;color:var(--text-muted);margin-top:0.75rem;">'
                f"Common task patterns</h3>"
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<table class="data-table">{ct_rows}</table></div>'
            )

        # Task assignees
        assignees = td.get("task_assignees", {})
        if assignees:
            ta_rows = "".join(
                f"<tr><td>{_e(str(name))}</td><td>{cnt} tasks</td></tr>" for name, cnt in list(assignees.items())[:5]
            )
            td_content += (
                f'<h3 style="font-size:0.85rem;color:var(--text-muted);margin-top:0.75rem;">'
                f"Task assignees</h3>"
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<table class="data-table">{ta_rows}</table></div>'
            )

        _nav("tasks", "Tasks")
        sections.append(_section("tasks", "Task Decomposition", td_content))

    # ── DoD Signals ─────────────────────────────────────────────────
    dod = profile.dod_signal
    dod_items_with_key: list[tuple[str, float, str]] = []
    if dod.stories_with_testing_mention_pct > 0:
        dod_items_with_key.append(("Testing mentioned", dod.stories_with_testing_mention_pct, "dod_testing"))
    if dod.stories_with_pr_link_pct > 0:
        dod_items_with_key.append(("PR linked before close", dod.stories_with_pr_link_pct, "dod_pr"))
    if dod.stories_with_review_mention_pct > 0:
        dod_items_with_key.append(("Code review mentioned", dod.stories_with_review_mention_pct, "dod_review"))
    if dod.stories_with_deploy_mention_pct > 0:
        dod_items_with_key.append(("Deploy mentioned", dod.stories_with_deploy_mention_pct, "dod_deploy"))

    if dod_items_with_key:
        dod_hdr = "<tr><th>Practice</th><th>Coverage</th><th>Example</th></tr>"
        dod_rows_html = ""
        for label, pct, ekey in dod_items_with_key:
            ex_items = ex.get(ekey, [])
            ex_html = ""
            if ex_items and isinstance(ex_items, list) and ex_items:
                e0 = ex_items[0]
                if isinstance(e0, dict):
                    ek = _e(e0.get("issue_key", ""))
                    eu = e0.get("issue_url", "")
                    sm = _e(e0.get("summary", "")[:30])
                    key_h = f'<a href="{_e(eu)}"><code>{ek}</code></a>' if eu else f"<code>{ek}</code>"
                    ex_html = f'{key_h} <span style="color:var(--text-muted);">{sm}</span>'
            dod_rows_html += (
                f"<tr><td>{_e(label)}</td><td>{_pct_bar_html(pct)}</td>"
                f'<td style="font-size:0.8rem;">{ex_html}</td></tr>'
            )
        if dod.common_checklist_items:
            items = ", ".join(_e(i) for i in dod.common_checklist_items[:6])
            dod_rows_html += (
                f'<tr><td>Common signals</td><td colspan="2" style="color:var(--text-muted);">{items}</td></tr>'
            )
        dod_table = (
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<table class="data-table">{dod_hdr}{dod_rows_html}</table></div>'
        )
        _nav("dod", "DoD")
        sections.append(_section("dod", "Definition of Done (inferred)", dod_table))

    # ── Proposed DoD ───────────────────────────────────────────────
    pdod = ex.get("proposed_dod", {})
    if isinstance(pdod, dict) and pdod.get("items"):
        pdod_summary = pdod.get("summary", "")
        pdod_health = pdod.get("health", "weak")
        h_col = (
            "var(--ok)"
            if pdod_health == "strong"
            else ("var(--warn)" if pdod_health == "moderate" else "var(--danger)")
        )
        pdod_html = f'<p style="color:{h_col};font-weight:bold;">{_e(pdod_summary)}</p>'
        pdod_html += (
            '<table class="data-table"><tr><th>Practice</th><th>Status</th><th>Evidence</th><th>Action</th></tr>'
        )
        _pst_icon = {"established": "&#x2713;", "emerging": "&#x25cb;", "missing": "&#x2717;"}
        _pst_col = {"established": "var(--ok)", "emerging": "var(--warn)", "missing": "var(--danger)"}
        for item in pdod["items"]:
            st = item.get("status", "missing")
            sig = item.get("signals", "no evidence")
            pdod_html += (
                f"<tr><td>{_e(item.get('practice', ''))}</td>"
                f'<td style="color:{_pst_col.get(st, "var(--low)")};">'
                f"{_pst_icon.get(st, '?')} {_e(st)}</td>"
                f'<td style="color:var(--text-muted);">{_e(sig)}</td>'
                f'<td style="color:var(--text-muted);font-size:0.85rem;">'
                f"{_e(item.get('recommendation', ''))}</td></tr>"
            )
        pdod_html += "</table>"
        dod_ordering = pdod.get("ordering", [])
        if len(dod_ordering) >= 2:
            pdod_html += (
                f'<p style="margin-top:0.5rem;color:var(--text-muted);">'
                f"Typical order: {' &rarr; '.join(_e(o) for o in dod_ordering)}</p>"
            )
        custom_steps = pdod.get("custom_steps", [])
        if custom_steps:
            parts = ", ".join(f"&ldquo;{_e(cs['title'])}&rdquo; ({cs['pct']}%)" for cs in custom_steps[:4])
            pdod_html += f'<p style="color:var(--text-muted);">Team-specific steps: {parts}</p>'
        _nav("proposed-dod", "Proposed DoD")
        sections.append(_section("proposed-dod", "Proposed Definition of Done", pdod_html))

    # ── Writing Patterns ────────────────────────────────────────────
    wp = profile.writing_patterns
    wp_rows: list[tuple[str, str]] = []
    if wp.uses_given_when_then:
        wp_rows.append(("AC format", "Given/When/Then &#x2713;"))
    if wp.median_ac_count > 0:
        wp_rows.append(("Median ACs/story", str(wp.median_ac_count)))
    if wp.median_task_count_per_story > 0:
        wp_rows.append(("Median tasks/story", str(wp.median_task_count_per_story)))
    if wp.subtask_label_distribution:
        parts = " &middot; ".join(f"{_e(lbl)} {int(pct * 100)}%" for lbl, pct in wp.subtask_label_distribution[:5])
        wp_rows.append(("Sub-task types", parts))
    if wp.common_personas:
        wp_rows.append(("Personas", _e(", ".join(wp.common_personas[:5]))))
    if wp_rows:
        _nav("patterns", "Patterns")
        sections.append(_section("patterns", "Writing Patterns", _kv_table(wp_rows)))

    # ── Repository Activity ─────────────────────────────────────────
    repos = ex.get("repositories", {})
    if isinstance(repos, dict) and repos.get("top_repos"):
        top = repos["top_repos"]
        avg_cts = repos.get("repo_avg_cycle_time", {})
        spill_repos_set = {r["repo"] for r in repos.get("spillover_repos", []) if isinstance(r, dict)}

        repo_hdr = "<tr><th>Repository</th><th>Stories</th><th>Share</th><th>Avg cycle</th></tr>"
        repo_rows_html = ""
        for r in top[:8]:
            if not isinstance(r, dict):
                continue
            rname = r.get("repo", "")
            cnt = r.get("stories", 0)
            pct = r.get("pct", 0)
            avg_ct = avg_cts.get(rname) if isinstance(avg_cts, dict) else None
            ct_html = f"{avg_ct:.0f}d" if avg_ct else "&mdash;"
            ct_color = "var(--warn)" if avg_ct and avg_ct > 15 else "var(--text-muted)"
            name_style = "color:var(--warn);font-weight:600;" if rname in spill_repos_set else ""
            repo_rows_html += (
                f'<tr><td style="{name_style}"><strong>{_e(rname)}</strong></td>'
                f"<td>{cnt}</td><td>{_pct_bar_html(pct, 80)}</td>"
                f'<td style="color:{ct_color};">{ct_html}</td></tr>'
            )

        repo_content = (
            f'<div class="card" style="padding:0;overflow:hidden;">'
            f'<table class="data-table">{repo_hdr}{repo_rows_html}</table></div>'
        )

        # Spillover-prone repos
        spill_repos = repos.get("spillover_repos", [])
        if spill_repos and isinstance(spill_repos, list):
            repo_content += (
                '<h3 style="font-size:0.85rem;color:var(--text-muted);margin-top:0.75rem;">'
                "Repos with highest spillover rate</h3>"
            )
            for sr in spill_repos[:3]:
                if not isinstance(sr, dict):
                    continue
                repo_content += (
                    f'<div style="margin:0.3rem 0 0 1rem;font-size:0.85rem;">'
                    f'<strong style="color:var(--warn);">{_e(sr.get("repo", ""))}</strong>'
                    f' <span style="color:var(--text-muted);">'
                    f"{sr.get('spill_rate', 0)}% spillover ({sr.get('spills', 0)} times)</span></div>"
                )

        # Repos by point value
        by_pts = repos.get("by_pts", {})
        if by_pts and isinstance(by_pts, dict):
            repo_content += (
                '<h3 style="font-size:0.85rem;color:var(--text-muted);margin-top:0.75rem;">Repos by story size</h3>'
            )
            for pts_key in sorted(by_pts.keys(), key=lambda x: int(x)):
                pt_repos = by_pts[pts_key]
                if not pt_repos:
                    continue
                repo_content += (
                    f'<div style="margin:0.2rem 0 0 1rem;font-size:0.85rem;">'
                    f"<strong>{pts_key}pt</strong>"
                    f' <span style="color:var(--text-muted);">'
                    f"{', '.join(_e(str(r)) for r in pt_repos[:3])}</span></div>"
                )

        _nav("repos", "Repos")
        sections.append(_section("repos", "Repository Activity", repo_content))

    # ── Ticket Naming & Organisation ──────────────────────────────────
    _h_naming = ex.get("naming_conventions", {})
    if isinstance(_h_naming, dict) and (
        _h_naming.get("title_prefixes")
        or _h_naming.get("label_distribution")
        or _h_naming.get("epic_examples")
        or _h_naming.get("template_sections")
    ):
        nm_rows: list[tuple[str, str]] = []
        _nm_prefixes = _h_naming.get("title_prefixes", [])
        if _nm_prefixes:
            nm_rows.append(("Title prefixes", " &middot; ".join(f"{p} {pct}%" for p, pct in _nm_prefixes[:5])))
        else:
            nm_rows.append(("Title prefixes", "none detected"))
        _nm_lbls = _h_naming.get("label_distribution", [])
        _nm_lpct = _h_naming.get("stories_with_labels_pct", 0)
        if _nm_lbls:
            nm_rows.append(
                (
                    "Labels",
                    f"{_nm_lpct}% labelled: " + " &middot; ".join(f"{lbl} {pct}%" for lbl, pct in _nm_lbls[:6]),
                )
            )
        _nm_style = _h_naming.get("epic_naming_style", "")
        _nm_epex = _h_naming.get("epic_examples", [])
        if _nm_style and _nm_epex:
            _nm_exs = ", ".join(f"&ldquo;{_e(e[:40])}&rdquo;" for e in _nm_epex[:3])
            nm_rows.append(("Epic naming", f"{_nm_style} &mdash; {_nm_exs}"))
        _nm_secs = _h_naming.get("template_sections", [])
        if _nm_secs:
            _nm_ss = " &rarr; ".join(f"&ldquo;{_e(s)}&rdquo;" for s, _ in _nm_secs[:5])
            nm_rows.append(("Description template", _nm_ss))
        _nav("naming", "Naming")
        sections.append(_section("naming", "Ticket Naming & Organisation", _kv_table(nm_rows)))

    # ── Story & Epic Structure ──────────────────────────────────────
    _h_struct = ex.get("story_structure", {})
    if isinstance(_h_struct, dict) and (_h_struct.get("subtask_ordering") or _h_struct.get("epic_completion")):
        st_rows: list[tuple[str, str]] = []
        _st_ord = _h_struct.get("subtask_ordering", [])
        if len(_st_ord) >= 2:
            st_rows.append(("Subtask sequence", " &rarr; ".join(_e(s) for s in _st_ord)))
        _st_skip = _h_struct.get("skipped_types", [])
        if _st_skip:
            st_rows.append(
                (
                    "Rarely created",
                    " &middot; ".join(f"{s['type']} ({s['present_pct']}%)" for s in _st_skip),
                )
            )
        _st_avg = _h_struct.get("avg_epic_completion", 0)
        if _st_avg > 0:
            st_rows.append(("Epic completion avg", f"{_st_avg}%"))
        _st_ling = _h_struct.get("lingering_epics", [])
        if _st_ling:
            for ep in _st_ling[:3]:
                st_rows.append(
                    (
                        _e(ep.get("epic_title", "?")),
                        f"{ep['completed']}/{ep['total']} done ({ep['rate']}%)",
                    )
                )
        _st_spread = _h_struct.get("epic_sprint_spread", [])
        if _st_spread:
            for ep in _st_spread[:3]:
                st_rows.append(
                    (
                        _e(ep.get("epic", "?")),
                        f"{ep['stories']} stories across {ep['sprints']} sprints",
                    )
                )
        if st_rows:
            _nav("structure", "Structure")
            sections.append(_section("structure", "Story & Epic Structure", _kv_table(st_rows)))

    # ── Acceptance Criteria Patterns ──────────────────────────────────
    ac_pat = ex.get("ac_patterns", {})
    if isinstance(ac_pat, dict) and ac_pat.get("stories_with_ac_pct") is not None:
        ac_pct = ac_pat.get("stories_with_ac_pct", 0)
        ac_rows: list[tuple[str, str]] = [("Stories with ACs", f"{ac_pct}%")]
        if ac_pct == 0:
            ac_rows.append(
                (
                    "",
                    "<em>No acceptance criteria detected. ACs help define done and reduce ambiguity.</em>",
                )
            )
        else:
            spec = ac_pat.get("specificity", {})
            ac_rows.extend(
                [
                    ("Median ACs/story", str(ac_pat.get("median_ac", 0))),
                    ("Specificity", f"{spec.get('label', '?')} ({spec.get('precise_pct', 0)}% precise)"),
                ]
            )
            themes = ac_pat.get("themes", {})
            _tex = ac_pat.get("theme_examples", {})
            if themes:
                _tp: list[str] = []
                for t, p in list(themes.items())[:5]:
                    _ex_d = _tex.get(t)
                    _ex_h = ""
                    if isinstance(_ex_d, dict) and _ex_d.get("issue_key"):
                        _ek = _e(_ex_d["issue_key"])
                        _eu = _ex_d.get("issue_url", "")
                        _sm = _e(_ex_d.get("summary", "")[:30])
                        _lk = f'<a href="{_e(_eu)}"><code>{_ek}</code></a>' if _eu else f"<code>{_ek}</code>"
                        _ex_h = f' {_lk} <span style="color:var(--text-muted);">{_sm}</span>'
                    _tp.append(f"<strong>{_e(t)}</strong> {p}%{_ex_h}")
                ac_rows.append(("Topics", "<br>".join(_tp)))
            by_disc = ac_pat.get("by_discipline", {})
            if len(by_disc) >= 2:
                parts = " &middot; ".join(f"{d} {v['avg_ac']:.0f} avg" for d, v in by_disc.items())
                ac_rows.append(("By discipline", parts))
            spill = ac_pat.get("spillover_correlation", {})
            low_s = spill.get("low_ac_spill_pct", 0)
            high_s = spill.get("high_ac_spill_pct", 0)
            if low_s > high_s + 5 and spill.get("low_ac_count", 0) >= 5:
                ac_rows.append(("Spillover impact", f"0-1 ACs: {low_s}% spill vs 3+ ACs: {high_s}% spill"))
        _nav("ac-patterns", "ACs")
        sections.append(_section("ac-patterns", "Acceptance Criteria Patterns", _kv_table(ac_rows)))

    # ── Epic Sizing ─────────────────────────────────────────────────
    epic = profile.epic_pattern
    if epic.sample_count > 0:
        lo, hi = epic.typical_story_count_range
        ep_rows: list[tuple[str, str]] = [
            ("Avg stories/epic", f"{epic.avg_stories_per_epic:.0f}"),
            ("Avg points/epic", f"{epic.avg_points_per_epic:.0f}"),
        ]
        if lo > 0 or hi > 0:
            ep_rows.append(("Story count range", f"{lo}&ndash;{hi}"))
        sections.append(_section("epics", "Epic Sizing", _kv_table(ep_rows)))

    # ── Point Descriptions (LLM-generated) ──────────────────────────
    pt_descs = ex.get("point_descriptions", {})
    if isinstance(pt_descs, dict) and pt_descs:
        pd_rows: list[str] = []
        for pts_key in sorted(pt_descs.keys(), key=lambda x: int(x) if x.isdigit() else 99):
            pd_rows.append(f"<tr><td><strong>{pts_key} pt</strong></td><td>{_e(pt_descs[pts_key])}</td></tr>")
        if pd_rows:
            pd_table = (
                f'<div class="card" style="padding:0;overflow:hidden;">'
                f'<table class="data-table"><tr><th>Points</th><th>What it means for this team</th></tr>'
                f"{''.join(pd_rows)}</table></div>"
            )
            _nav("point-descriptions", "Point Descriptions")
            sections.append(
                _section("point-descriptions", "What Each Point Value Means (LLM Interpretation)", pd_table)
            )

    # ── Estimation Accuracy ───────────────────────────────────────
    addl = ex.get("additional_patterns", {})
    est_bias = addl.get("estimation_bias", {}) if isinstance(addl, dict) else {}
    if isinstance(est_bias, dict) and est_bias.get("sample_size", 0) >= 5:
        eb_rows = [
            ("Accurate (at original estimate)", f"{est_bias.get('accurate_pct', 0):.0f}%"),
            ("Underestimated (points increased)", f"{est_bias.get('underestimated_pct', 0):.0f}%"),
            ("Overestimated (points decreased)", f"{est_bias.get('overestimated_pct', 0):.0f}%"),
        ]
        worst = est_bias.get("worst_overestimate_sizes", [])
        if worst:
            eb_rows.append(("Most overestimated sizes", ", ".join(f"{s}pt" for s in worst)))
        _nav("estimation", "Estimation")
        sections.append(_section("estimation", "Estimation Accuracy", _kv_table(eb_rows)))

    # ── Seasonal Patterns ─────────────────────────────────────────
    seasonal = addl.get("seasonal", {}) if isinstance(addl, dict) else {}
    if isinstance(seasonal, dict) and seasonal.get("monthly_avg"):
        monthly = seasonal["monthly_avg"]
        s_rows = [(m, f"{v:g} pts") for m, v in monthly.items()]
        low_m = seasonal.get("low_months", {})
        high_m = seasonal.get("high_months", {})
        for m, v in low_m.items():
            s_rows.append((f"\u2193 {m} (low)", f"{v:g} pts"))
        for m, v in high_m.items():
            s_rows.append((f"\u2191 {m} (high)", f"{v:g} pts"))
        _nav("seasonal", "Seasonal")
        sections.append(_section("seasonal", "Seasonal Patterns", _kv_table(s_rows)))

    # ── Workflow ──────────────────────────────────────────────────
    wf = ex.get("workflow_style", {})
    if isinstance(wf, dict) and wf.get("workflow"):
        wf_seq = " \u2192 ".join(wf["workflow"])
        wf_rows = [("Workflow", wf_seq)]
        wf_style_label = {"columns-as-dod": "Columns as DoD steps", "minimal": "Minimal workflow"}.get(
            wf.get("style", "minimal"), wf.get("style", "minimal")
        )
        wf_rows.append(("Style", wf_style_label))
        for col, rate in wf.get("dod_columns", {}).items():
            wf_rows.append((f"  {col} pass-through", f"{rate}%"))
        _nav("workflow", "Workflow")
        sections.append(_section("workflow", "Board Workflow", _kv_table(wf_rows)))

    # ── Recommendations (all 13 types, matching TUI) ────────────────
    recs: list[tuple[str, str]] = []
    if vel > 0:
        var_pct = std / vel * 100
        if var_pct > 35:
            recs.append(
                (
                    "High velocity variance",
                    f"Velocity swings &pm;{var_pct:.0f}% sprint-to-sprint. "
                    "Consider smaller stories or stricter sprint commitments.",
                )
            )
    if profile.sprint_completion_rate > 0 and profile.sprint_completion_rate < 60:
        recs.append(
            (
                "Low sprint completion",
                f"Only {profile.sprint_completion_rate:.0f}% of planned work completes. "
                "Right-size commitments to 80-90% of velocity.",
            )
        )
    if profile.spillover.carried_over_pct > 15:
        recs.append(
            (
                "Frequent spillover",
                f"{profile.spillover.carried_over_pct:.0f}% of stories carry over. "
                "Break large stories into smaller slices.",
            )
        )
    for c in cals:
        if c.point_value >= 8 and c.avg_cycle_time_days > 60:
            recs.append(
                (
                    f"{c.point_value}-point stories too large",
                    f"{c.point_value}-point stories take {c.avg_cycle_time_days:.0f}d on average. "
                    "Consider splitting into smaller pieces.",
                )
            )
            break
    dod = profile.dod_signal
    if 0 < dod.stories_with_testing_mention_pct < 15:
        recs.append(
            (
                "Testing rarely mentioned",
                f"Only {dod.stories_with_testing_mention_pct:.0f}% of stories mention testing. "
                "Add explicit test criteria to acceptance criteria.",
            )
        )
    if 0 < dod.stories_with_pr_link_pct < 20:
        recs.append(
            (
                "Low PR linkage",
                f"Only {dod.stories_with_pr_link_pct:.0f}% of stories reference a PR. "
                "Link PRs to tickets for traceability.",
            )
        )
    rec_count_val = ex.get("recurring_count", 0)
    del_count_val = ex.get("delivery_count", 0)
    if isinstance(rec_count_val, int) and isinstance(del_count_val, int):
        total = rec_count_val + del_count_val
        if total > 0 and rec_count_val / total > 0.3:
            recs.append(
                (
                    "High recurring overhead",
                    f"{rec_count_val} of {total} tickets ({rec_count_val / total * 100:.0f}%) "
                    "are recurring. Consider consolidating or timeboxing.",
                )
            )
    _html_cs = ex.get("contributor_stats", [])
    if isinstance(_html_cs, list) and _html_cs:
        _hcv = [c.get("per_sprint", 0) for c in _html_cs if c.get("per_sprint", 0) > 0]
        if _hcv:
            _hca = round(sum(_hcv) / len(_hcv), 1)
            if _hca < 3:
                recs.append(
                    (
                        "Low per-developer output",
                        f"Contributors average {_hca} pts/sprint. "
                        "Check for blockers, context-switching, or oversized stories.",
                    )
                )
    _repos = ex.get("repositories", {})
    if isinstance(_repos, dict):
        for sr in _repos.get("spillover_repos", []):
            if isinstance(sr, dict) and sr.get("spill_rate", 0) >= 40:
                recs.append(
                    (
                        f"{_e(sr['repo'])} has high spillover",
                        f"{sr['spill_rate']}% of stories touching {_e(sr['repo'])} don't complete the sprint.",
                    )
                )
    _shadow = ex.get("shadow_spillover", [])
    if isinstance(_shadow, list) and len(_shadow) >= 2:
        recs.append(
            (
                "Shadow spillover",
                f"{len(_shadow)} stories were closed then re-created in the next sprint. "
                "Consider keeping the original ticket open instead of cloning.",
            )
        )
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict):
        if td.get("task_completion_rate", 100) < 60:
            recs.append(
                (
                    "Low task completion",
                    f"Only {td['task_completion_rate']}% of sub-tasks are completed.",
                )
            )
        for cat, rate_val, count in td.get("bottlenecks", []):
            recs.append(
                (
                    f"{cat} bottleneck",
                    f"{cat} tasks have only {rate_val}% completion ({count} tasks).",
                )
            )
        sw = td.get("stories_with_tasks", 0)
        tot = td.get("total_stories", 0)
        if tot > 10 and sw > 0 and sw / tot < 0.3:
            recs.append(
                (
                    "Low task breakdown",
                    f"Only {sw} of {tot} stories ({sw / tot * 100:.0f}%) have sub-tasks.",
                )
            )

    # Scope change recommendations
    _sc = ex.get("scope_changes", {})
    if isinstance(_sc, dict) and _sc.get("totals"):
        _sct = _sc["totals"]
        _sct_n = _sct.get("total_stories", 0)
        _sct_cv = _sct.get("avg_committed_velocity", 0.0)
        _sct_dv = _sct.get("avg_delivered_velocity", 0.0)
        if _sct_cv > 0 and _sct_dv / _sct_cv < 0.7:
            _dp = round(_sct_dv / _sct_cv * 100)
            recs.append(
                (
                    "Low delivery accuracy",
                    f"Team delivers only {_dp}% of committed scope "
                    f"({_sct_dv} of {_sct_cv} pts avg). "
                    "Reduce sprint commitments to match actual capacity.",
                )
            )
        if _sct_n > 0:
            _sct_a = _sct.get("added_mid_sprint", 0)
            _sct_r = _sct.get("re_estimated", 0)
            if _sct_a / _sct_n > 0.15:
                recs.append(
                    (
                        "High mid-sprint scope additions",
                        f"{_sct_a} of {_sct_n} stories ({_sct_a / _sct_n * 100:.0f}%) "
                        "were added after the sprint started. "
                        "Protect sprint commitments by locking scope after planning.",
                    )
                )
            if _sct_r / _sct_n > 0.15:
                recs.append(
                    (
                        "Frequent re-estimation",
                        f"{_sct_r} of {_sct_n} stories ({_sct_r / _sct_n * 100:.0f}%) "
                        "had their points changed mid-sprint. "
                        "Improve estimation accuracy with team calibration sessions.",
                    )
                )
        _sc_sps = _sc.get("per_sprint", [])
        _hi_churn = [s for s in _sc_sps if s.get("scope_churn", 0) > 0.3]
        if len(_hi_churn) >= 2:
            _cn = ", ".join(s.get("name", "?") for s in _hi_churn[:3])
            recs.append(
                (
                    "High scope churn",
                    f"{len(_hi_churn)} sprints had &gt;30% scope churn ({_e(_cn)}). "
                    "Scope is volatile &mdash; enforce a sprint lock after planning.",
                )
            )
        _sc_ch = _sc.get("carry_over_chains", [])
        if len(_sc_ch) >= 3:
            recs.append(
                (
                    "Carry-over chains",
                    f"{len(_sc_ch)} stories bounced across 3+ sprints. "
                    "These are zombie stories &mdash; split or kill them.",
                )
            )

    _html_ac = ex.get("ac_patterns", {})
    if isinstance(_html_ac, dict) and _html_ac.get("recommendation"):
        recs.append(("Acceptance criteria gaps", _e(_html_ac["recommendation"])))

    _html_pdod = ex.get("proposed_dod", {})
    if isinstance(_html_pdod, dict) and _html_pdod.get("health") == "weak":
        _hm = [i["practice"] for i in _html_pdod.get("items", []) if i.get("status") == "missing"]
        recs.append(
            (
                "No consistent Definition of Done",
                f"No consistent DoD found. {_e(', '.join(_hm[:3]))} show no evidence. "
                "Create a team DoD checklist to improve quality.",
            )
        )
    elif isinstance(_html_pdod, dict) and _html_pdod.get("health") == "moderate":
        _he = [i["practice"] for i in _html_pdod.get("items", []) if i.get("status") == "emerging"]
        if _he:
            recs.append(
                (
                    "Create a formal Definition of Done",
                    f"{_e(', '.join(_he[:3]))} are practiced inconsistently. "
                    "Write a shared DoD checklist and enforce it on every story.",
                )
            )

    if recs:
        rec_html_items = "".join(
            f'<div class="card" style="border-left:3px solid var(--warn);margin-bottom:0.5rem;">'
            f'<strong style="color:var(--warn);">&#x26a0; {title}</strong>'
            f'<p style="color:var(--text-muted);margin-top:0.3rem;">{desc}</p></div>'
            for title, desc in recs
        )
        _nav("recommendations", "Recs")
        sections.append(_section("recommendations", "Recommendations", rec_html_items))

    # ── Assemble page ───────────────────────────────────────────────
    gen_ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    return html_page(
        title=f"Team Profile — {profile.project_key}",
        heading=f"Team Profile — {profile.source}/{profile.project_key}",
        meta=[
            f"{profile.sample_sprints} sprints analysed",
            f"{profile.sample_stories} stories",
            f"Generated {gen_ts}",
        ],
        badges=[", ".join(sprint_names)] if sprint_names else [],
        nav=nav_links,
        body="".join(sections),
        footer_note=f"Generated by yeaboi.ai • {datetime.now().strftime('%Y-%m-%d')}",
    )


def export_team_profile_html(
    profile: TeamProfile,
    output_dir: Path | None = None,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    ceremony=None,
) -> Path:
    """Write the self-contained team-profile HTML report and return its path."""
    out_dir = _project_export_dir(profile.project_key, output_dir)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"team-profile-{ts}.html"
    page = build_team_profile_html(
        profile,
        examples=examples,
        sprint_names=sprint_names,
        ceremony=ceremony,
        charts_dir=out_dir,
    )
    out_path.write_text(page, encoding="utf-8")
    logger.info("Exported team profile HTML to %s", out_path)
    return out_path


def export_team_profile_md(
    profile: TeamProfile,
    output_dir: Path | None = None,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    ceremony=None,
) -> Path:
    """Generate a Markdown report matching the TUI results screen.

    ``ceremony`` is an optional CeremonyContext; when non-empty, a "Ceremony
    Cadence & Trends" section is appended after Team & Velocity.

    Returns the path to the generated file.
    """
    out_dir = _project_export_dir(profile.project_key, output_dir)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"team-profile-{ts}.md"
    md = build_team_profile_markdown(
        profile, examples=examples, sprint_names=sprint_names, ceremony=ceremony, charts_dir=out_dir
    )
    # Relink the chart (and any other images) relative to the export folder.
    from yeaboi.export_targets import localize_images

    md = localize_images(md, out_dir)
    out_path.write_text(md, encoding="utf-8")
    logger.info("Exported team profile Markdown to %s", out_path)
    return out_path


def build_team_profile_markdown(
    profile: TeamProfile,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    ceremony=None,
    charts_dir: Path | None = None,
) -> str:
    """Build the team-profile Markdown report as a string.

    Extracted from ``export_team_profile_md`` so the same content can be
    published to Notion/Confluence (via export_targets) without touching disk.
    When ``charts_dir`` is set (and matplotlib is installed), a sprint-velocity
    chart PNG is rendered there and embedded above the Sprint Breakdown table.
    """
    ex = examples or {}
    gen_ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        f"# Team Profile — {profile.source}/{profile.project_key}",
        "",
        f"*{profile.sample_sprints} sprints · {profile.sample_stories} stories · Generated {gen_ts}*",
    ]
    depth = str(ex.get("analysis_depth", "")).strip().lower()
    if depth in ("quick", "deep"):
        lines.extend(["", f"**Analysis depth:** {depth.capitalize()}"])
    if sprint_names:
        lines.append(f"\nSprints: {', '.join(sprint_names)}")
    lines.append("")

    # ── Executive Summary (AI narrative, generated at analysis time) ─
    narrative = ex.get("narrative", {})
    if isinstance(narrative, dict) and narrative.get("executive_summary"):
        lines.extend(["## Executive Summary", "", str(narrative["executive_summary"]), ""])
        n_sections = narrative.get("sections", {})
        if isinstance(n_sections, dict):
            for nk, title in _NARRATIVE_TITLES:
                if n_sections.get(nk):
                    lines.append(f"- **{title}:** {n_sections[nk]}")
            lines.append("")

    # ── Team Insights (AI coaching, generated at analysis time) ─────
    insights = ex.get("insights", {})
    if isinstance(insights, dict) and any(insights.get(k) for k, _ in INSIGHT_CATEGORIES):
        lines.extend(["## Team Insights", ""])
        for ik, ilabel in INSIGHT_CATEGORIES:
            i_items = insights.get(ik)
            if not isinstance(i_items, list) or not i_items:
                continue
            lines.extend([f"### {ilabel}", ""])
            for it in i_items:
                if not isinstance(it, dict) or not it.get("title"):
                    continue
                i_line = f"- **{it.get('title', '')}** — {it.get('detail', '')}"
                if it.get("evidence"):
                    i_line += f" *({it['evidence']})*"
                lines.append(i_line)
            lines.append("")

    # ── AI Adoption (detectable AI-tool footprint — lower bound) ─────
    ai_sig = getattr(profile, "ai_adoption", None)
    ai_blob = ex.get("ai_adoption", {})
    code_features = set(ai_blob.get("enabled_features") or ("ai_footprint", "code_health"))
    ai_scanned = (getattr(ai_sig, "scanned_commits", 0) + getattr(ai_sig, "scanned_prs", 0)) if ai_sig else 0
    if "ai_footprint" in code_features and ai_sig and ai_scanned:
        lines.extend(["## AI Footprint", ""])
        lines.append(
            "> _Lower bound — only AI tools that leave a marker in commit messages or PR "
            "descriptions are counted. Inline IDE assist (Copilot ghost-text, Cursor Tab) "
            "leaves no trace, so real usage is at least this._"
        )
        lines.append("")
        activity_coverage = ai_blob.get("activity_coverage", {}) if isinstance(ai_blob, dict) else {}
        lines.append(f"- **Coverage:** {_coverage_message(activity_coverage)}")
        lines.append(f"- **Detectable footprint:** {_footprint_value(ai_sig).replace('<', '&lt;')}")
        lines.append(f"- **Commits with AI marker:** {ai_sig.ai_commits} of {ai_sig.scanned_commits}")
        if ai_sig.scanned_prs:
            lines.append(f"- **PRs with AI marker:** {ai_sig.ai_prs} of {ai_sig.scanned_prs}")
        if ai_sig.sources_scanned:
            lines.append(f"- **Sources scanned:** {', '.join(_source_label(s) for s in ai_sig.sources_scanned)}")
        if isinstance(ai_blob, dict) and ai_blob.get("selected_users"):
            lines.append(f"- **Selected users:** {', '.join(str(u) for u in ai_blob['selected_users'])}")
            lines.append(
                f"- **Matched identities:** {len(ai_blob.get('matched_identities') or {})} "
                f"of {len(ai_blob['selected_users'])}"
            )
        if getattr(ai_sig, "repos_scanned", ()):
            lines.append(f"- **Repositories scanned:** {len(ai_sig.repos_scanned)}")
            repo_preview = ", ".join(ai_sig.repos_scanned[:5])
            if len(ai_sig.repos_scanned) > 5:
                repo_preview += f" (+{len(ai_sig.repos_scanned) - 5} more)"
            lines.append(f"- **Repository scope:** {repo_preview}")
        if ai_sig.per_tool:
            tools = ", ".join(f"{'unlabelled AI' if t == 'other_ai' else t} ({n})" for t, n in ai_sig.per_tool)
            lines.append(f"- **By tool:** {tools}")
        if getattr(ai_sig, "per_source", ()):
            lines.append(f"- **By source:** {', '.join(f'{_source_label(s)} ({n})' for s, n in ai_sig.per_source)}")
        if ai_sig.per_activity:
            lines.append(f"- **By activity:** {', '.join(f'{a} ({n})' for a, n in ai_sig.per_activity)}")
        if ai_sig.per_author:
            lines.append(f"- **By contributor:** {', '.join(f'{a} ({n})' for a, n in ai_sig.per_author[:8])}")
        ai_coverage = ai_blob.get("coverage") if isinstance(ai_blob, dict) else None
        if ai_coverage:
            lines.append(f"- **Not scanned:** {'; '.join(ai_coverage)}")
        lines.append("")
        ai_samples = ai_blob.get("samples") if isinstance(ai_blob, dict) else None
        if ai_samples:
            lines.extend(["### Examples", ""])
            lines.extend(_ai_example_md(s) for s in ai_samples)
            lines.append("")
        ai_insights = ai_blob.get("insights", {}) if isinstance(ai_blob, dict) else {}
        if isinstance(ai_insights, dict) and any(ai_insights.get(k) for k, _ in INSIGHT_CATEGORIES):
            for ik, ilabel in INSIGHT_CATEGORIES:
                i_items = ai_insights.get(ik)
                if not isinstance(i_items, list) or not i_items:
                    continue
                lines.extend([f"### {ilabel}", ""])
                lines.extend(_insight_md(it) for it in i_items if isinstance(it, dict) and it.get("title"))
                lines.append("")
    file_health = ai_blob.get("repository_health", {}) if isinstance(ai_blob, dict) else {}
    if "code_health" in code_features and file_health:
        health_coverage = ai_blob.get("coverage_report", {})
        health_failed = health_coverage.get("status") in {"failed", "no_data"}
        lines.extend(["## Code Health", ""])
        lines.append(
            "> _Scoped to files attributable to the selected users. Untouched repositories "
            "and unrelated contributors are not analysed._"
        )
        lines.extend(["", f"- **Coverage:** {_coverage_message(health_coverage)}"])
        if not health_failed:
            lines.extend(
                [
                    f"- **Changed files analysed:** {file_health.get('files_analysed', 0)}",
                    f"- **Repositories touched:** {file_health.get('repositories_touched', 0)}",
                    f"- **Findings:** {file_health.get('findings', 0)}",
                ]
            )
        for error in _coverage_errors(health_coverage):
            lines.append(f"- **Collection error:** {error}")
        lines.append("")
        health_actions = ai_blob.get("action_plan", []) if isinstance(ai_blob, dict) else []
        if health_actions and not health_failed:
            lines.extend(["### Prioritized action plan", ""])
            lines.extend(_action_md(action) for action in health_actions)
            lines.append("")

    # ── Documentation (Notion/Confluence clarity + usefulness) ────────────
    dq_sig = getattr(profile, "doc_quality", None)
    dq_blob = ex.get("doc_quality", {})
    dq_pages = getattr(dq_sig, "pages_scanned", 0) if dq_sig else 0
    if dq_sig and isinstance(dq_blob, dict):
        doc_coverage = dq_blob.get("coverage_report", {})
        doc_failed = doc_coverage.get("status") in {"failed", "no_data"}
        lines.extend(["## Documentation", ""])
        lines.append(
            "> _Clarity is a readability score. Usefulness measures purpose, ownership, "
            "structure, and actionability. Explicit AI markers are a lower bound._"
        )
        lines.append("")
        lines.append(f"- **Coverage:** {_coverage_message(doc_coverage)}")
        if not doc_failed:
            dq_split = f"{dq_sig.clear_pages} clear / {dq_sig.mixed_pages} mixed / {dq_sig.unclear_pages} unclear"
            lines.append(f"- **Average clarity:** {dq_sig.avg_clarity:.0f}/100")
            lines.append(f"- **Average usefulness:** {getattr(dq_sig, 'avg_usefulness', 0):.0f}/100")
            lines.append(f"- **Pages scanned:** {_doc_pages_value(dq_sig, dq_pages)}")
            lines.append(f"- **Clarity split:** {dq_split}")
            lines.append(
                f"- **Owned / actionable pages:** {getattr(dq_sig, 'owned_pages', 0)} / "
                f"{getattr(dq_sig, 'actionable_pages', 0)}"
            )
            lines.append(f"- **Explicit AI markers:** {dq_sig.ai_marked_pages} page(s) (lower bound)")
        for error in _coverage_errors(doc_coverage):
            lines.append(f"- **Collection error:** {error}")
        if dq_sig.flagged_pages and not doc_failed:
            lines.append(f"- **Flagged:** {', '.join(f'{t} ({r})' for t, r in dq_sig.flagged_pages)}")
        lines.append("")
        dq_samples = dq_blob.get("samples") if isinstance(dq_blob, dict) else None
        if dq_samples and not doc_failed:
            lines.extend(["### Examples", ""])
            lines.extend(_doc_example_md(s) for s in dq_samples)
            lines.append("")
        dq_insights = dq_blob.get("insights", {}) if isinstance(dq_blob, dict) else {}
        if not doc_failed and isinstance(dq_insights, dict) and any(dq_insights.get(k) for k, _ in INSIGHT_CATEGORIES):
            for ik, ilabel in INSIGHT_CATEGORIES:
                i_items = dq_insights.get(ik)
                if not isinstance(i_items, list) or not i_items:
                    continue
                lines.extend([f"### {ilabel}", ""])
                lines.extend(_insight_md(it) for it in i_items if isinstance(it, dict) and it.get("title"))
                lines.append("")
        dq_actions = dq_blob.get("action_plan", []) if isinstance(dq_blob, dict) else []
        if dq_actions and not doc_failed:
            lines.extend(["### Prioritized action plan", ""])
            lines.extend(_action_md(action) for action in dq_actions)
            lines.append("")

    # ── Recurring work ──────────────────────────────────────────────
    rec_count = ex.get("recurring_count", 0)
    del_count = ex.get("delivery_count", 0)
    rec_items = ex.get("recurring", [])
    if rec_count and isinstance(rec_count, int) and rec_count > 0:
        lines.append(f"> {rec_count} recurring tickets excluded ({del_count} delivery stories analysed)")
        if rec_items and isinstance(rec_items, list):
            for r in rec_items[:5]:
                if isinstance(r, dict):
                    lines.append(f">   - `{r.get('issue_key', '')}` {r.get('summary', '')}")
        lines.append("")

    # ── Ceremony cadence & trends (Standup + Retro history) ─────────
    if ceremony is not None and not ceremony.is_empty:
        lines.extend(_ceremony_md(ceremony))

    # ── Team & Velocity ─────────────────────────────────────────────
    lines.extend(["## Team & Velocity", ""])
    team_sz = ex.get("team_size", 0)
    members = ex.get("team_members", [])
    per_dev = ex.get("per_dev_velocity", 0)
    if team_sz and isinstance(team_sz, int):
        mem = f" ({', '.join(str(m) for m in members[:8])})" if members else ""
        lines.append(f"- **Team size:** {team_sz} contributors{mem}")

    sp_details = ex.get("sprint_details", [])
    if isinstance(sp_details, list) and sp_details:
        import math as _m

        sp_pts = [sd["points"] for sd in sp_details if isinstance(sd, dict) and sd.get("points", 0) > 0]
        vel = round(sum(sp_pts) / len(sp_pts), 1) if sp_pts else profile.velocity_avg
        std = (
            round(_m.sqrt(sum((x - sum(sp_pts) / len(sp_pts)) ** 2 for x in sp_pts) / len(sp_pts)), 1)
            if len(sp_pts) >= 2
            else profile.velocity_stddev
        )
    else:
        vel = profile.velocity_avg
        std = profile.velocity_stddev

    lines.append(f"- **Velocity:** {vel} pts/sprint")
    _md_vsc = ex.get("scope_changes", {})
    if isinstance(_md_vsc, dict) and _md_vsc.get("totals"):
        _mcv = _md_vsc["totals"].get("avg_committed_velocity", 0.0)
        _mdv = _md_vsc["totals"].get("avg_delivered_velocity", 0.0)
        if _mcv > 0:
            _mdp = round(_mdv / _mcv * 100)
            lines.append(f"- **Committed avg:** {_mcv:g} pts/sprint")
            lines.append(f"- **Delivered avg:** {_mdv:g} pts/sprint ({_mdp}% accuracy)")
    _mv_cs = ex.get("contributor_stats", [])
    if isinstance(_mv_cs, list) and _mv_cs:
        _mv_vals = [c.get("per_sprint", 0) for c in _mv_cs if c.get("per_sprint", 0) > 0]
        if _mv_vals:
            _mv_avg = round(sum(_mv_vals) / len(_mv_vals), 1)
            lines.append(f"- **Per developer:** {_mv_avg} pts/sprint")
    elif per_dev and isinstance(per_dev, (int, float)):
        lines.append(f"- **Per developer:** {per_dev} pts/sprint")
    if vel > 0:
        lines.append(f"- **Variance:** ±{std} ({std / vel * 100:.0f}%)")
    if profile.sprint_completion_rate > 0:
        lines.append(f"- **Completion rate:** {_format_pct(profile.sprint_completion_rate)}")
    if profile.spillover.carried_over_pct > 0:
        lines.append(f"- **Spillover:** {_format_pct(profile.spillover.carried_over_pct)} carried over")

    # Velocity trend
    vt = ex.get("velocity_trend", {})
    if isinstance(vt, dict) and vt.get("trend") and vt["trend"] != "insufficient_data":
        trend = vt["trend"]
        slope = vt.get("slope", 0)
        first_v = vt.get("first_velocity", 0)
        last_v = vt.get("last_velocity", 0)
        lines.append(f"- **Trend:** {trend.capitalize()} ({first_v} → {last_v}, {slope:+.1f}/sprint)")
    lines.append("")

    # ── Spillover Root Causes ───────────────────────────────────────
    spill_corr = ex.get("spillover_correlation", {})
    if isinstance(spill_corr, dict) and spill_corr:
        by_size = spill_corr.get("by_size", {})
        by_disc = spill_corr.get("by_discipline", {})
        by_tasks = spill_corr.get("by_task_count", {})
        has_spill = any(v > 0 for d in (by_size, by_disc, by_tasks) if isinstance(d, dict) for v in d.values())
        if has_spill:
            lines.extend(["## Spillover Root Causes", ""])
            if by_size:
                parts = " · ".join(f"{sz}pt={pct:.0f}%" for sz, pct in sorted(by_size.items(), key=lambda x: int(x[0])))
                lines.append(f"- **By size:** {parts}")
            if by_disc:
                parts = " · ".join(f"{d}={pct:.0f}%" for d, pct in sorted(by_disc.items()))
                lines.append(f"- **By discipline:** {parts}")
            if by_tasks:
                parts = " · ".join(f"{b}={pct:.0f}%" for b, pct in by_tasks.items())
                lines.append(f"- **By task count:** {parts}")
            lines.append("")

    # ── Sprint Breakdown ────────────────────────────────────────────
    if sp_details and isinstance(sp_details, list) and sp_details:
        lines.extend(["## Sprint Breakdown", ""])
        if charts_dir is not None:
            # Velocity chart (optional charts extra) — embedded above the table
            # and carried through file/Notion/Confluence exports by the
            # ![alt](path) pipeline in export_targets.
            from yeaboi.charts import velocity_chart

            rows = [
                (str(sd.get("name", "?")), float(sd.get("planned", 0) or 0), float(sd.get("completed", 0) or 0))
                for sd in sp_details
                if isinstance(sd, dict)
            ]
            chart = velocity_chart(rows, charts_dir / "velocity.png")
            if chart is not None:
                lines.extend([f"![Sprint velocity]({chart})", ""])
        lines.extend(
            [
                "| Sprint | Pts | Done | Rate | |",
                "|--------|-----|------|------|-|",
            ]
        )
        for sd in sp_details:
            if not isinstance(sd, dict):
                continue
            name = sd.get("name", "?")
            pts = sd.get("points", 0)
            planned = sd.get("planned", 0)
            completed = sd.get("completed", 0)
            rate = sd.get("rate", 0)
            done = sd.get("done", False)
            has_shadow = sd.get("has_shadow", False)
            icon = "✓" if done else ("○" if has_shadow else "✗")
            lines.append(f"| {name} | {pts} | {completed}/{planned} | {rate}% | {icon} |")
        lines.append("")
        lines.append("*" + " · ".join(ANALYSIS_GLOSSARY[g] for g in _SPRINT_GLOSSARY_KEYS) + "*")
        lines.append("")

        # Incomplete sprint analysis
        incomplete = [
            sd
            for sd in sp_details
            if isinstance(sd, dict)
            and (not sd.get("done", False) or sd.get("has_shadow", False))
            and sd.get("incomplete")
        ]
        if incomplete:
            lines.extend(["### Incomplete sprint analysis", ""])
            for sd in incomplete[:3]:
                sname = sd.get("name", "?")
                gap = sd.get("planned", 0) - sd.get("completed", 0)
                has_sh = sd.get("has_shadow", False)
                parts = []
                if gap > 0:
                    parts.append(f"{gap} stories not completed")
                if has_sh:
                    parts.append("shadow spillover")
                lines.append(f"**{sname}** — {' + '.join(parts)}")
                for item in sd.get("incomplete", [])[:3]:
                    if not isinstance(item, dict):
                        continue
                    ek = item.get("issue_key", "")
                    sm = item.get("summary", "")
                    shadow = item.get("shadow", False)
                    pts_v = item.get("points", 0)
                    detail = " (re-created)" if shadow else (f" ({pts_v}pts)" if pts_v else "")
                    lines.append(f"  - `{ek}` {sm}{detail}")
                lines.append("")

    # ── Team Members ───────────────────────────────────────────────
    _md_contrib = ex.get("contributor_stats", [])
    if isinstance(_md_contrib, list) and _md_contrib:
        lines.extend(["## Team Members", ""])
        _md_trec = sum(c.get("recurring_pts", 0) for c in _md_contrib)
        _md_tdel = sum(c.get("delivery_pts", 0) for c in _md_contrib)
        if _md_trec > 0:
            _md_rpct = round(_md_trec / (_md_trec + _md_tdel) * 100) if (_md_trec + _md_tdel) else 0
            lines.append(f"Interrupted work: **{_md_trec:g} pts** ({_md_rpct}% of total effort)")
            lines.append("")
        lines.extend(
            [
                "| Name | Delivered | Stories | Spill% | Cycle | Sprints | Focus | Pts/sprint |",
                "|------|-----------|---------|--------|-------|---------|-------|------------|",
            ]
        )
        for cs in _md_contrib[:10]:
            ct_v = cs.get("avg_cycle_time", 0)
            ct_s = f"{ct_v:.0f}d" if ct_v > 0 else "\u2014"
            disc = cs.get("top_discipline", "fullstack")
            wt = cs.get("top_work_type", "")
            focus = f"{disc}/{wt.split('/')[0]}" if wt else disc
            lines.append(
                f"| {cs.get('name', '')} "
                f"| {cs.get('delivery_pts', 0)} "
                f"| {cs.get('stories_completed', 0)} "
                f"| {cs.get('spill_rate', 0)}% "
                f"| {ct_s} "
                f"| {cs.get('sprints_active', 0)} "
                f"| {focus[:18]} "
                f"| {cs.get('per_sprint', 0)} |"
            )
        if len(_md_contrib) >= 3 and _md_tdel > 0:
            top = _md_contrib[0]
            top_pct = round(top["delivery_pts"] / _md_tdel * 100)
            if top_pct >= 40:
                lines.append("")
                lines.append(f"> {top['name']} carries {top_pct}% of delivery work")
        lines.append("")

    # ── Shadow Spillover ────────────────────────────────────────────
    shadow = ex.get("shadow_spillover", [])
    if isinstance(shadow, list) and shadow:
        lines.extend(
            [
                f"## Shadow Spillover ({len(shadow)} re-created stories)",
                "",
                "Closed in one sprint but re-created in the next:",
                "",
            ]
        )
        for sh in shadow[:5]:
            if not isinstance(sh, dict):
                continue
            ek = sh.get("issue_key", "")
            title = sh.get("title", "")
            from_sp = sh.get("from_sprint", "")
            to_sp = sh.get("to_sprint", "")
            lines.append(f"- `{ek}` {title}")
            if from_sp or to_sp:
                lines.append(f"  - {from_sp} → {to_sp}")
        lines.append("")

    # ── Scope Analysis (appended to sprint section) ─────────────────
    _md_scope = ex.get("scope_changes", {})
    if isinstance(_md_scope, dict) and _md_scope.get("totals"):
        _md_t = _md_scope["totals"]
        _md_a = _md_t.get("added_mid_sprint", 0)
        _md_r = _md_t.get("re_estimated", 0)
        _md_n = _md_t.get("total_stories", 0)
        _md_cv = _md_t.get("avg_committed_velocity", 0.0)
        _md_dv = _md_t.get("avg_delivered_velocity", 0.0)
        if _md_a > 0 or _md_r > 0 or _md_cv > 0:
            lines.append("---")
            lines.append("")
            if _md_cv > 0:
                _md_dp = round(_md_dv / _md_cv * 100)
                lines.append(f"Committed **{_md_cv:g}** → Delivered **{_md_dv:g}** pts/sprint avg ({_md_dp}% accuracy)")
            if _md_n > 0 and (_md_a > 0 or _md_r > 0):
                lines.append(
                    f"- {_md_a} added mid-sprint ({_md_a * 100 // _md_n}%) "
                    f"· {_md_r} re-estimated ({_md_r * 100 // _md_n}%)"
                )
            lines.append("")
            _md_tls = _md_scope.get("timelines", [])
            _md_we = [t for t in _md_tls if hasattr(t, "change_events") and t.change_events]
            for tl in _md_we[-4:]:
                _d = tl.scope_change_total
                _p = round(_d / tl.committed_pts * 100) if tl.committed_pts else 0
                _ds = f"+{_d:g}" if _d > 0 else f"{_d:g}"
                _ns = len(tl.daily_snapshots[0].stories_in_sprint) if tl.daily_snapshots else 0
                _nf = len(tl.daily_snapshots[-1].stories_in_sprint) if tl.daily_snapshots else 0
                lines.append(f"### {tl.sprint_name} — {_ds} scope ({_p:+d}%)")
                lines.append("")
                lines.append(f"Committed {tl.committed_pts:g} pts ({_ns} stories)")
                lines.append("")
                for ev in tl.change_events[:5]:
                    ct = ev.change_type.replace("re_estimated_", "re-est ").replace("_", " ")
                    evd = f"+{ev.delta_pts:g}" if ev.delta_pts > 0 else f"{ev.delta_pts:g}"
                    sm = f" — {ev.summary}" if ev.summary else ""
                    lines.append(f"- {evd} pts `{ev.issue_key}` {ct}{sm}")
                if len(tl.change_events) > 5:
                    lines.append(f"- ... +{len(tl.change_events) - 5} more")
                lines.append("")
                lines.append(f"Final {tl.final_pts:g} pts ({_nf} stories) · Delivered {tl.delivered_pts:g} pts")
                lines.append("")
            _md_chains = _md_scope.get("carry_over_chains", [])
            if _md_chains:
                lines.append(f"**{len(_md_chains)} stories bounced across 3+ sprints:**")
                for ch in _md_chains[:5]:
                    if isinstance(ch, dict):
                        ek = ch.get("issue_key", "")
                        sps = " → ".join(str(s) for s in ch.get("sprints", []))
                        lines.append(f"- `{ek}` {sps}")
                lines.append("")

    # ── Discipline-Specific Calibration ─────────────────────────────
    disc_cal = ex.get("discipline_calibration", {})
    if isinstance(disc_cal, dict) and len(disc_cal) > 1:
        lines.extend(["## Calibration by Discipline", ""])
        for disc, entries in sorted(disc_cal.items()):
            if not isinstance(entries, list) or not entries:
                continue
            lines.append(f"### {disc}")
            lines.append("")
            lines.append("| Points | Cycle | Variance | Samples | Spillover |")
            lines.append("|--------|-------|----------|---------|-----------|")
            for e in entries:
                if not isinstance(e, dict):
                    continue
                pts = e.get("points", 0)
                avg_d = e.get("avg_cycle_days", 0)
                var = e.get("variance", 0)
                samples = e.get("samples", 0)
                sp = e.get("spill_pct", 0)
                var_str = f"±{var:.0f}d" if var > 0 else "—"
                sp_str = f"{sp:.0f}%" if sp > 0 else "—"
                lines.append(f"| {pts}pts | {avg_d:.0f}d | {var_str} | {samples} | {sp_str} |")
            lines.append("")

    # ── Point Calibration ───────────────────────────────────────────
    cals = [c for c in profile.point_calibrations if c.sample_count > 0]
    _md_raw_conf = ex.get("confidence_levels", {})
    _md_conf: dict[int, str] = {}
    if isinstance(_md_raw_conf, dict):
        for k, v in _md_raw_conf.items():
            try:
                _md_conf[int(k)] = str(v)
            except (ValueError, TypeError):
                pass
    if cals:
        lines.extend(
            [
                "## What Each Point Value Means",
                "",
                "| Points | Cycle time | Samples | Tasks | Slip | Confidence |",
                "|--------|-----------|---------|-------|------|------------|",
            ]
        )
        for c in cals:
            pts_label = f"{c.point_value}pt" if c.point_value == 1 else f"{c.point_value}pts"
            conf = _md_conf.get(c.point_value, "")
            conf_str = conf.upper() if conf == "high" else (conf if conf else "")
            lines.append(
                f"| {pts_label} | {c.avg_cycle_time_days:.0f}d | {c.sample_count} "
                f"| ~{c.typical_task_count:.0f} | {_format_pct(c.overshoot_pct)} | {conf_str} |"
            )
            if c.common_patterns:
                lines.append(f"  - Typical: {', '.join(c.common_patterns)}")
            # Issue key examples
            cal_examples = ex.get(f"calibration_{c.point_value}pt", [])
            for ce in cal_examples[:2]:
                if isinstance(ce, dict):
                    ek = ce.get("issue_key", "")
                    sm = ce.get("summary", "")
                    detail = ce.get("detail", "")
                    lines.append(f"  - `{ek}` {sm}{f' — {detail}' if detail else ''}")
        lines.append("")

    # ── Story Shapes ────────────────────────────────────────────────
    shapes = [s for s in profile.story_shapes if s.sample_count > 0]
    if shapes:
        lines.extend(
            [
                "## Story Shape by Discipline",
                "",
                "| Discipline | Avg pts | Avg ACs | Avg tasks | Samples |",
                "|-----------|---------|---------|-----------|---------|",
            ]
        )
        for s in shapes:
            lines.append(
                f"| {s.discipline} | {s.avg_points} | {s.avg_ac_count} | {s.avg_task_count} | {s.sample_count} |"
            )
        lines.append("")

    # ── Task Decomposition ──────────────────────────────────────────
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict) and td.get("total_tasks", 0) > 0:
        lines.extend(["## Task Decomposition", ""])
        lines.append(f"- **Stories with tasks:** {td['stories_with_tasks']} / {td['total_stories']}")
        lines.append(f"- **Total tasks:** {td['total_tasks']}")
        lines.append(f"- **Avg tasks/story:** {td['avg_tasks_per_story']}")
        lines.append(f"- **Task completion:** {_format_pct(td['task_completion_rate'])}")
        type_dist = td.get("type_distribution", {})
        if type_dist:
            lines.append("")
            for cat, pct in type_dist.items():
                lines.append(f"  - {cat}: {_format_pct(pct)}")

        bottlenecks = td.get("bottlenecks", [])
        for cat, rate_val, count in bottlenecks:
            lines.append(f"- **{cat} bottleneck:** only {rate_val}% completion ({count} tasks)")

        common_tasks = td.get("common_tasks", [])
        if common_tasks:
            lines.extend(["", "Common task patterns:"])
            for title, cnt in common_tasks[:4]:
                lines.append(f"  - {title} ×{cnt}")

        assignees = td.get("task_assignees", {})
        if assignees:
            lines.extend(["", "Task assignees:"])
            for name, cnt in list(assignees.items())[:5]:
                lines.append(f"  - {name}: {cnt} tasks")
        lines.append("")

    # ── DoD Signals ─────────────────────────────────────────────────
    dod = profile.dod_signal
    dod_items_keyed: list[tuple[str, float, str]] = []
    if dod.stories_with_testing_mention_pct > 0:
        dod_items_keyed.append(("Testing mentioned", dod.stories_with_testing_mention_pct, "dod_testing"))
    if dod.stories_with_pr_link_pct > 0:
        dod_items_keyed.append(("PR linked", dod.stories_with_pr_link_pct, "dod_pr"))
    if dod.stories_with_review_mention_pct > 0:
        dod_items_keyed.append(("Code review", dod.stories_with_review_mention_pct, "dod_review"))
    if dod.stories_with_deploy_mention_pct > 0:
        dod_items_keyed.append(("Deploy", dod.stories_with_deploy_mention_pct, "dod_deploy"))
    if dod_items_keyed:
        lines.extend(["## Definition of Done (inferred)", ""])
        for label, pct, ekey in dod_items_keyed:
            ex_items = ex.get(ekey, [])
            ex_str = ""
            if ex_items and isinstance(ex_items, list) and ex_items:
                e0 = ex_items[0]
                if isinstance(e0, dict):
                    ex_str = f" — e.g. `{e0.get('issue_key', '')}` {e0.get('summary', '')[:30]}"
            lines.append(f"- **{label}:** {_format_pct(pct)}{ex_str}")
        if dod.common_checklist_items:
            lines.append(f"- **Common signals:** {', '.join(dod.common_checklist_items[:6])}")
        lines.append("")

    # ── Proposed DoD ───────────────────────────────────────────────
    pdod = ex.get("proposed_dod", {})
    if isinstance(pdod, dict) and pdod.get("items"):
        lines.extend(["## Proposed Definition of Done", ""])
        pdod_summary = pdod.get("summary", "")
        if pdod_summary:
            lines.append(f"**{pdod_summary}**")
            lines.append("")
        lines.extend(
            [
                "| Practice | Status | Evidence | Action |",
                "|----------|--------|----------|--------|",
            ]
        )
        _md_st_icon = {"established": "\u2713", "emerging": "\u25cb", "missing": "\u2717"}
        for item in pdod["items"]:
            st = item.get("status", "missing")
            sig = item.get("signals", "no evidence")
            lines.append(
                f"| {item.get('practice', '')} "
                f"| {_md_st_icon.get(st, '?')} {st} "
                f"| {sig} "
                f"| {item.get('recommendation', '')} |"
            )
        dod_ordering = pdod.get("ordering", [])
        if len(dod_ordering) >= 2:
            lines.append(f"**Typical order:** {' → '.join(dod_ordering)}")
        custom_steps = pdod.get("custom_steps", [])
        if custom_steps:
            parts = ", ".join(f'"{cs["title"]}" ({cs["pct"]}%)' for cs in custom_steps[:4])
            lines.append(f"**Team-specific steps:** {parts}")
        lines.append("")

    # ── Writing Patterns ────────────────────────────────────────────
    wp = profile.writing_patterns
    wp_items: list[tuple[str, str]] = []
    if wp.uses_given_when_then:
        wp_items.append(("AC format", "Given/When/Then ✓"))
    if wp.median_ac_count > 0:
        wp_items.append(("Median ACs/story", str(wp.median_ac_count)))
    if wp.median_task_count_per_story > 0:
        wp_items.append(("Median tasks/story", str(wp.median_task_count_per_story)))
    if wp.subtask_label_distribution:
        parts = " · ".join(f"{lbl} {int(pct * 100)}%" for lbl, pct in wp.subtask_label_distribution[:5])
        wp_items.append(("Sub-task types", parts))
    if wp.common_personas:
        wp_items.append(("Personas", ", ".join(wp.common_personas[:5])))
    if wp_items:
        lines.extend(["## Writing Patterns", ""])
        for label, val in wp_items:
            lines.append(f"- **{label}:** {val}")
        lines.append("")

    # ── Repository Activity ─────────────────────────────────────────
    repos = ex.get("repositories", {})
    if isinstance(repos, dict) and repos.get("top_repos"):
        avg_cts = repos.get("repo_avg_cycle_time", {})
        lines.extend(
            [
                "## Repository Activity",
                "",
                "| Repository | Stories | Share | Avg cycle |",
                "|-----------|---------|-------|-----------|",
            ]
        )
        for r in repos["top_repos"][:8]:
            if isinstance(r, dict):
                rname = r.get("repo", "")
                avg_ct = avg_cts.get(rname) if isinstance(avg_cts, dict) else None
                ct_str = f"{avg_ct:.0f}d" if avg_ct else "—"
                lines.append(f"| {rname} | {r.get('stories', 0)} | {_format_pct(r.get('pct', 0))} | {ct_str} |")
        lines.append("")

        spill_repos = repos.get("spillover_repos", [])
        if spill_repos and isinstance(spill_repos, list):
            lines.append("**Spillover-prone repos:**")
            for sr in spill_repos[:3]:
                if isinstance(sr, dict):
                    lines.append(
                        f"- **{sr.get('repo', '')}** — "
                        f"{sr.get('spill_rate', 0)}% spillover ({sr.get('spills', 0)} times)"
                    )
            lines.append("")

        by_pts = repos.get("by_pts", {})
        if by_pts and isinstance(by_pts, dict):
            lines.append("**Repos by story size:**")
            for pts_key in sorted(by_pts.keys(), key=lambda x: int(x)):
                pt_repos = by_pts[pts_key]
                if pt_repos:
                    lines.append(f"- {pts_key}pt: {', '.join(str(r) for r in pt_repos[:3])}")
            lines.append("")

    # ── Ticket Naming & Organisation ──────────────────────────────────
    _md_naming = ex.get("naming_conventions", {})
    if isinstance(_md_naming, dict) and (
        _md_naming.get("title_prefixes")
        or _md_naming.get("label_distribution")
        or _md_naming.get("epic_examples")
        or _md_naming.get("template_sections")
    ):
        lines.extend(["## Ticket Naming & Organisation", ""])
        _mnp = _md_naming.get("title_prefixes", [])
        if _mnp:
            _pp_str = " \u00b7 ".join(f"{p} {pct}%" for p, pct in _mnp[:5])
            lines.append(f"- **Title prefixes:** {_pp_str}")
        else:
            lines.append("- **Title prefixes:** none detected")
        _mnl = _md_naming.get("label_distribution", [])
        _mnlp = _md_naming.get("stories_with_labels_pct", 0)
        if _mnl:
            _ll_str = " \u00b7 ".join(f"{lbl} {pct}%" for lbl, pct in _mnl[:6])
            lines.append(f"- **Labels:** {_mnlp}% labelled: {_ll_str}")
        _mns = _md_naming.get("epic_naming_style", "")
        _mnex = _md_naming.get("epic_examples", [])
        if _mns and _mnex:
            _ee_str = ", ".join(f'"{e[:40]}"' for e in _mnex[:3])
            lines.append(f"- **Epic naming:** {_mns} \u2014 {_ee_str}")
        _mnt = _md_naming.get("template_sections", [])
        if _mnt:
            _ss_str = " \u2192 ".join(f'"{s}"' for s, _ in _mnt[:5])
            lines.append(f"- **Description template:** {_ss_str}")
        lines.append("")

    # ── Story & Epic Structure ──────────────────────────────────────
    _md_struct = ex.get("story_structure", {})
    if isinstance(_md_struct, dict) and (_md_struct.get("subtask_ordering") or _md_struct.get("epic_completion")):
        lines.extend(["## Story & Epic Structure", ""])
        _mso = _md_struct.get("subtask_ordering", [])
        if len(_mso) >= 2:
            _mso_str = " \u2192 ".join(_mso)
            lines.append(f"- **Subtask sequence:** {_mso_str}")
        _msk = _md_struct.get("skipped_types", [])
        if _msk:
            _skp = " \u00b7 ".join(f"{s['type']} ({s['present_pct']}%)" for s in _msk)
            lines.append(f"- **Rarely created:** {_skp}")
        _msa = _md_struct.get("avg_epic_completion", 0)
        if _msa > 0:
            lines.append(f"- **Epic completion avg:** {_msa}%")
        _msl = _md_struct.get("lingering_epics", [])
        if _msl:
            lines.append("")
            for ep in _msl[:3]:
                lines.append(f"- {ep.get('epic_title', '?')} \u2014 {ep['completed']}/{ep['total']} ({ep['rate']}%)")
        _mss = _md_struct.get("epic_sprint_spread", [])
        if _mss:
            lines.append("")
            lines.append("**Multi-sprint epics:**")
            for ep in _mss[:3]:
                lines.append(f"- {ep.get('epic', '?')} \u2014 {ep['stories']} stories across {ep['sprints']} sprints")
        lines.append("")

    # ── Acceptance Criteria Patterns ──────────────────────────────────
    ac_pat = ex.get("ac_patterns", {})
    if isinstance(ac_pat, dict) and ac_pat.get("stories_with_ac_pct") is not None:
        ac_pct = ac_pat.get("stories_with_ac_pct", 0)
        lines.extend(["## Acceptance Criteria Patterns", ""])
        lines.append(f"- **Stories with ACs:** {ac_pct}%")
        if ac_pct == 0:
            lines.append("")
            lines.append(
                "> No acceptance criteria detected in any story. "
                "ACs help define what 'done' means and reduce ambiguity."
            )
        else:
            lines.append(f"- **Median ACs/story:** {ac_pat.get('median_ac', 0)}")
            spec = ac_pat.get("specificity", {})
            lines.append(
                f"- **Specificity:** {spec.get('label', '?')} "
                f"({spec.get('precise_pct', 0)}% precise, {spec.get('vague_pct', 0)}% vague)"
            )
            themes = ac_pat.get("themes", {})
            _md_tex = ac_pat.get("theme_examples", {})
            if themes:
                lines.append("")
                lines.append("**Topics:**")
                for t, p in list(themes.items())[:5]:
                    _md_ex = _md_tex.get(t)
                    ex_str = ""
                    if isinstance(_md_ex, dict) and _md_ex.get("issue_key"):
                        ex_str = f" — `{_md_ex['issue_key']}` {_md_ex.get('summary', '')[:30]}"
                    lines.append(f"- **{t}** {p}%{ex_str}")
            by_disc = ac_pat.get("by_discipline", {})
            if len(by_disc) >= 2:
                parts = " · ".join(f"{d} {v['avg_ac']:.0f} avg" for d, v in by_disc.items())
                lines.append(f"- **By discipline:** {parts}")
            spill = ac_pat.get("spillover_correlation", {})
            low_s = spill.get("low_ac_spill_pct", 0)
            high_s = spill.get("high_ac_spill_pct", 0)
            if low_s > high_s + 5 and spill.get("low_ac_count", 0) >= 5:
                lines.append(f"- **Spillover impact:** 0-1 ACs: {low_s}% spill vs 3+ ACs: {high_s}% spill")
            ac_rec = ac_pat.get("recommendation", "")
            if ac_rec:
                lines.append("")
                lines.append(f"> {ac_rec}")
            lines.append("")

    # ── Epic Sizing ─────────────────────────────────────────────────
    epic = profile.epic_pattern
    if epic.sample_count > 0:
        lines.extend(["## Epic Sizing", ""])
        lines.append(f"- **Avg stories/epic:** {epic.avg_stories_per_epic:.0f}")
        lines.append(f"- **Avg points/epic:** {epic.avg_points_per_epic:.0f}")
        lo, hi = epic.typical_story_count_range
        if lo > 0 or hi > 0:
            lines.append(f"- **Story count range:** {lo}–{hi}")
        lines.append("")

    # ── Point Descriptions (LLM-generated) ──────────────────────────
    pt_descs = ex.get("point_descriptions", {})
    if isinstance(pt_descs, dict) and pt_descs:
        lines.extend(["## What Each Point Value Means (LLM Interpretation)", ""])
        for pts_key in sorted(pt_descs.keys(), key=lambda x: int(x) if x.isdigit() else 99):
            lines.append(f"- **{pts_key} pt:** {pt_descs[pts_key]}")
        lines.append("")

    # ── Estimation Accuracy ───────────────────────────────────────
    addl_md = ex.get("additional_patterns", {})
    est_bias_md = addl_md.get("estimation_bias", {}) if isinstance(addl_md, dict) else {}
    if isinstance(est_bias_md, dict) and est_bias_md.get("sample_size", 0) >= 5:
        lines.extend(["## Estimation Accuracy", ""])
        lines.append(f"- **Accurate:** {est_bias_md.get('accurate_pct', 0):.0f}%")
        lines.append(f"- **Underestimated:** {est_bias_md.get('underestimated_pct', 0):.0f}%")
        lines.append(f"- **Overestimated:** {est_bias_md.get('overestimated_pct', 0):.0f}%")
        worst_md = est_bias_md.get("worst_overestimate_sizes", [])
        if worst_md:
            lines.append(f"- **Most overestimated:** {', '.join(f'{s}pt' for s in worst_md)}")
        lines.append("")

    # ── Seasonal Patterns ─────────────────────────────────────────
    seasonal_md = addl_md.get("seasonal", {}) if isinstance(addl_md, dict) else {}
    if isinstance(seasonal_md, dict) and seasonal_md.get("monthly_avg"):
        monthly_md = seasonal_md["monthly_avg"]
        lines.extend(["## Seasonal Patterns", ""])
        lines.append("| Month | Velocity |")
        lines.append("|-------|----------|")
        for m, v in monthly_md.items():
            lines.append(f"| {m} | {v:g} pts |")
        low_md = seasonal_md.get("low_months", {})
        high_md = seasonal_md.get("high_months", {})
        for m, v in low_md.items():
            lines.append(f"- ↓ **{m}:** {v:g} pts (below average)")
        for m, v in high_md.items():
            lines.append(f"- ↑ **{m}:** {v:g} pts (above average)")
        lines.append("")

    # ── Workflow ──────────────────────────────────────────────────
    wf_md = ex.get("workflow_style", {})
    if isinstance(wf_md, dict) and wf_md.get("workflow"):
        lines.extend(["## Board Workflow", ""])
        lines.append(f"**Sequence:** {' → '.join(wf_md['workflow'])}")
        wf_s = {"columns-as-dod": "Columns as DoD steps", "minimal": "Minimal workflow"}.get(
            wf_md.get("style", "minimal"), wf_md.get("style", "minimal")
        )
        lines.append(f"**Style:** {wf_s}")
        for col, rate in wf_md.get("dod_columns", {}).items():
            lines.append(f"- {col}: {rate}% pass-through")
        lines.append("")

    # ── Recommendations (all 13 types, matching TUI) ────────────────
    recs: list[tuple[str, str]] = []
    if vel > 0:
        var_pct = std / vel * 100
        if var_pct > 35:
            recs.append(("High velocity variance", f"Velocity swings ±{var_pct:.0f}%."))
    if profile.sprint_completion_rate > 0 and profile.sprint_completion_rate < 60:
        recs.append(("Low sprint completion", f"Only {profile.sprint_completion_rate:.0f}% completes."))
    if profile.spillover.carried_over_pct > 15:
        recs.append(("Frequent spillover", f"{profile.spillover.carried_over_pct:.0f}% carry over."))
    for c in cals:
        if c.point_value >= 8 and c.avg_cycle_time_days > 60:
            recs.append((f"{c.point_value}-pt stories too large", f"Take {c.avg_cycle_time_days:.0f}d avg."))
            break
    dod = profile.dod_signal
    if 0 < dod.stories_with_testing_mention_pct < 15:
        recs.append(("Testing rarely mentioned", f"Only {dod.stories_with_testing_mention_pct:.0f}%."))
    if 0 < dod.stories_with_pr_link_pct < 20:
        recs.append(("Low PR linkage", f"Only {dod.stories_with_pr_link_pct:.0f}%."))
    md_rec_count = ex.get("recurring_count", 0)
    md_del_count = ex.get("delivery_count", 0)
    if isinstance(md_rec_count, int) and isinstance(md_del_count, int):
        total = md_rec_count + md_del_count
        if total > 0 and md_rec_count / total > 0.3:
            recs.append(("High recurring overhead", f"{md_rec_count}/{total} are recurring."))
    _md_cs = ex.get("contributor_stats", [])
    if isinstance(_md_cs, list) and _md_cs:
        _mcv = [c.get("per_sprint", 0) for c in _md_cs if c.get("per_sprint", 0) > 0]
        if _mcv:
            _mca = round(sum(_mcv) / len(_mcv), 1)
            if _mca < 3:
                recs.append(("Low per-developer output", f"Contributors avg {_mca} pts/sprint."))
    _repos = ex.get("repositories", {})
    if isinstance(_repos, dict):
        for sr in _repos.get("spillover_repos", []):
            if isinstance(sr, dict) and sr.get("spill_rate", 0) >= 40:
                recs.append((f"{sr['repo']} high spillover", f"{sr['spill_rate']}% of stories spill."))
    _shadow = ex.get("shadow_spillover", [])
    if isinstance(_shadow, list) and len(_shadow) >= 2:
        recs.append(("Shadow spillover", f"{len(_shadow)} stories re-created across sprints."))
    td = ex.get("task_decomposition", {})
    if isinstance(td, dict):
        if td.get("task_completion_rate", 100) < 60:
            recs.append(("Low task completion", f"Only {td['task_completion_rate']}% of tasks done."))
        for cat, rate_val, count in td.get("bottlenecks", []):
            recs.append((f"{cat} bottleneck", f"Only {rate_val}% completion ({count} tasks)."))
        sw = td.get("stories_with_tasks", 0)
        tot = td.get("total_stories", 0)
        if tot > 10 and sw > 0 and sw / tot < 0.3:
            recs.append(("Low task breakdown", f"Only {sw}/{tot} stories have sub-tasks."))

    # Scope change recommendations
    _md_sc = ex.get("scope_changes", {})
    if isinstance(_md_sc, dict) and _md_sc.get("totals"):
        _md_sct = _md_sc["totals"]
        _md_n = _md_sct.get("total_stories", 0)
        _md_cv = _md_sct.get("avg_committed_velocity", 0.0)
        _md_dv = _md_sct.get("avg_delivered_velocity", 0.0)
        if _md_cv > 0 and _md_dv / _md_cv < 0.7:
            _dp = round(_md_dv / _md_cv * 100)
            recs.append(("Low delivery accuracy", f"Team delivers only {_dp}% of committed scope."))
        if _md_n > 0:
            _md_a = _md_sct.get("added_mid_sprint", 0)
            _md_r = _md_sct.get("re_estimated", 0)
            if _md_a / _md_n > 0.15:
                recs.append(
                    (
                        "High mid-sprint scope additions",
                        f"{_md_a}/{_md_n} stories ({_md_a / _md_n * 100:.0f}%) added after sprint start.",
                    )
                )
            if _md_r / _md_n > 0.15:
                recs.append(
                    (
                        "Frequent re-estimation",
                        f"{_md_r}/{_md_n} stories ({_md_r / _md_n * 100:.0f}%) re-estimated mid-sprint.",
                    )
                )
        _md_sps = _md_sc.get("per_sprint", [])
        _md_hc = [s for s in _md_sps if s.get("scope_churn", 0) > 0.3]
        if len(_md_hc) >= 2:
            _cn = ", ".join(s.get("name", "?") for s in _md_hc[:3])
            recs.append(("High scope churn", f"{len(_md_hc)} sprints had >30% churn ({_cn})."))
        _md_ch = _md_sc.get("carry_over_chains", [])
        if len(_md_ch) >= 3:
            recs.append(("Carry-over chains", f"{len(_md_ch)} stories bounced across 3+ sprints."))

    _md_ac = ex.get("ac_patterns", {})
    if isinstance(_md_ac, dict) and _md_ac.get("recommendation"):
        recs.append(("Acceptance criteria gaps", _md_ac["recommendation"]))

    _md_pdod = ex.get("proposed_dod", {})
    if isinstance(_md_pdod, dict) and _md_pdod.get("health") == "weak":
        _mm = [i["practice"] for i in _md_pdod.get("items", []) if i.get("status") == "missing"]
        recs.append(
            (
                "No consistent DoD",
                f"No consistent DoD found. {', '.join(_mm[:3])} show no evidence. Create a team DoD checklist.",
            )
        )
    elif isinstance(_md_pdod, dict) and _md_pdod.get("health") == "moderate":
        _me = [i["practice"] for i in _md_pdod.get("items", []) if i.get("status") == "emerging"]
        if _me:
            recs.append(
                (
                    "Create a formal DoD",
                    f"{', '.join(_me[:3])} are inconsistent. Write a shared DoD checklist.",
                )
            )

    if recs:
        lines.extend(["## Recommendations", ""])
        for title, desc in recs:
            lines.append(f"- **{title}:** {desc}")
        lines.append("")

    lines.extend(["---", "", "🤙 _Generated by [yeaboi.ai](https://yeaboi.ai)_", ""])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Analysis log — structured record of each analysis run
# ---------------------------------------------------------------------------


def write_analysis_log(
    profile: TeamProfile,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    duration_secs: float = 0.0,
) -> Path:
    """Write a structured analysis log to ~/.scrum-agent/logs/.

    Each analysis run gets its own log file with full profile data, examples,
    and timing info. This provides an auditable history of every analysis run,
    sorted into the project's export directory for easy discovery.

    Returns the path to the generated log file.
    """
    import json

    from yeaboi.paths import get_analysis_log_dir

    log_dir = get_analysis_log_dir()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = log_dir / f"team-analysis-{profile.project_key.lower()}-{ts}.log"

    gen_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sections: list[str] = [
        f"Team Analysis Log — {profile.source}/{profile.project_key}",
        f"Generated: {gen_ts}",
        f"Duration: {duration_secs:.1f}s" if duration_secs > 0 else "",
        "",
        "=" * 60,
        "",
        f"Sprints analysed: {profile.sample_sprints}",
        f"Stories analysed: {profile.sample_stories}",
        f"Velocity avg:     {profile.velocity_avg} pts/sprint",
        f"Velocity stddev:  ±{profile.velocity_stddev}",
        f"Completion rate:  {_format_pct(profile.sprint_completion_rate)}",
        f"Estimation accuracy: {_format_pct(profile.estimation_accuracy_pct)}",
    ]

    if sprint_names:
        sections.extend(["", "Sprints:"])
        for name in sprint_names:
            sections.append(f"  - {name}")

    if profile.spillover.carried_over_pct > 0:
        sections.extend(
            [
                "",
                "Spillover:",
                f"  Carried over: {_format_pct(profile.spillover.carried_over_pct)}",
                f"  Avg spillover pts: {profile.spillover.avg_spillover_pts}",
            ]
        )
        if profile.spillover.most_common_spillover_reason:
            sections.append(f"  Common reason: {profile.spillover.most_common_spillover_reason}")

    if profile.point_calibrations:
        sections.extend(["", "Point Calibrations:"])
        for c in profile.point_calibrations:
            if c.sample_count == 0:
                continue
            sections.append(
                f"  {c.point_value}pt: {c.avg_cycle_time_days}d avg, "
                f"{c.sample_count} samples, {_format_pct(c.overshoot_pct)} slip, "
                f"~{c.typical_task_count} tasks"
            )
            if c.common_patterns:
                sections.append(f"       patterns: {', '.join(c.common_patterns)}")

    if profile.story_shapes:
        sections.extend(["", "Story Shapes:"])
        for s in profile.story_shapes:
            sections.append(
                f"  {s.discipline}: avg {s.avg_points}pts, "
                f"{s.avg_ac_count} ACs, {s.avg_task_count} tasks "
                f"({s.sample_count} samples)"
            )

    dod = profile.dod_signal
    if dod.stories_with_pr_link_pct > 0 or dod.stories_with_review_mention_pct > 0:
        sections.extend(["", "DoD Signals:"])
        if dod.stories_with_pr_link_pct > 0:
            sections.append(f"  PR linked:     {_format_pct(dod.stories_with_pr_link_pct)}")
        if dod.stories_with_review_mention_pct > 0:
            sections.append(f"  Code review:   {_format_pct(dod.stories_with_review_mention_pct)}")
        if dod.stories_with_testing_mention_pct > 0:
            sections.append(f"  Testing:       {_format_pct(dod.stories_with_testing_mention_pct)}")
        if dod.stories_with_deploy_mention_pct > 0:
            sections.append(f"  Deploy:        {_format_pct(dod.stories_with_deploy_mention_pct)}")
        if dod.common_checklist_items:
            sections.append(f"  Checklist:     {', '.join(dod.common_checklist_items)}")

    wp = profile.writing_patterns
    if wp.median_ac_count > 0 or wp.uses_given_when_then:
        sections.extend(["", "Writing Patterns:"])
        if wp.uses_given_when_then:
            sections.append("  AC format: Given/When/Then")
        if wp.median_ac_count > 0:
            sections.append(f"  Median ACs/story: {wp.median_ac_count}")
        if wp.median_task_count_per_story > 0:
            sections.append(f"  Median tasks/story: {wp.median_task_count_per_story}")
        if wp.common_personas:
            sections.append(f"  Personas: {', '.join(wp.common_personas)}")

    log_insights = examples.get("insights", {}) if examples else {}
    if isinstance(log_insights, dict) and any(log_insights.get(k) for k, _ in INSIGHT_CATEGORIES):
        sections.extend(["", "Team Insights:"])
        for ik, ilabel in INSIGHT_CATEGORIES:
            for it in log_insights.get(ik) or []:
                if isinstance(it, dict) and it.get("title"):
                    ev = f" ({it['evidence']})" if it.get("evidence") else ""
                    sections.append(f"  {ilabel.upper():<14s}{it['title']}{ev}")

    # AI-adoption footprint (lower bound — commit/PR markers only)
    ai_sig = getattr(profile, "ai_adoption", None)
    ai_blob = examples.get("ai_adoption", {}) if examples else {}
    code_features = set(ai_blob.get("enabled_features") or ("ai_footprint", "code_health"))
    ai_scanned = (getattr(ai_sig, "scanned_commits", 0) + getattr(ai_sig, "scanned_prs", 0)) if ai_sig else 0
    if "ai_footprint" in code_features and ai_sig and ai_scanned:
        sections.extend(["", "AI Footprint (lower bound — commit/PR markers only):"])
        sections.append(f"  Detectable footprint: {_footprint_value(ai_sig)}")
        sections.append(f"  Commits with AI marker: {ai_sig.ai_commits} of {ai_sig.scanned_commits}")
        if ai_sig.scanned_prs:
            sections.append(f"  PRs with AI marker: {ai_sig.ai_prs} of {ai_sig.scanned_prs}")
        if ai_sig.sources_scanned:
            sections.append(f"  Sources: {', '.join(_source_label(s) for s in ai_sig.sources_scanned)}")
        if isinstance(ai_blob, dict) and ai_blob.get("selected_users"):
            sections.append(f"  Selected users: {', '.join(str(u) for u in ai_blob['selected_users'])}")
            sections.append(
                f"  Matched identities: {len(ai_blob.get('matched_identities') or {})} "
                f"of {len(ai_blob['selected_users'])}"
            )
        for repo in getattr(ai_sig, "repos_scanned", ()):
            sections.append(f"  Scanned: {repo}")
        if ai_sig.per_tool:
            sections.append(
                "  By tool: "
                + ", ".join(f"{'unlabelled AI' if t == 'other_ai' else t}={n}" for t, n in ai_sig.per_tool)
            )
        if getattr(ai_sig, "per_source", ()):
            sections.append("  By source: " + ", ".join(f"{_source_label(s)}={n}" for s, n in ai_sig.per_source))
        ai_coverage = ai_blob.get("coverage") if isinstance(ai_blob, dict) else None
        if ai_coverage:
            sections.append(f"  Not scanned: {'; '.join(ai_coverage)}")
        ai_samples = ai_blob.get("samples") if isinstance(ai_blob, dict) else None
        if ai_samples:
            sections.append("  Examples:")
            for s in ai_samples:
                ref = s.get("url") or (f"commit {s.get('key')}" if s.get("key") else "")
                sections.append(f"    [{s.get('tool', '')}] {s.get('title', '')} {ref}".rstrip())
        ai_insights = ai_blob.get("insights", {}) if isinstance(ai_blob, dict) else {}
        if isinstance(ai_insights, dict) and any(ai_insights.get(k) for k, _ in INSIGHT_CATEGORIES):
            for ik, ilabel in INSIGHT_CATEGORIES:
                for it in ai_insights.get(ik) or []:
                    if isinstance(it, dict) and it.get("title"):
                        ev = f" ({it['evidence']})" if it.get("evidence") else ""
                        link = f" [{it['link']}]" if it.get("link") else ""
                        sections.append(f"  {ilabel.upper():<14s}{it['title']}{ev}{link}")

    file_health = ai_blob.get("repository_health", {}) if isinstance(ai_blob, dict) else {}
    if "code_health" in code_features and file_health:
        health_coverage = ai_blob.get("coverage_report", {})
        health_failed = health_coverage.get("status") in {"failed", "no_data"}
        sections.extend(["", "Code Health (selected-user changed files only):"])
        sections.append(f"  Coverage: {_coverage_message(health_coverage)}")
        if not health_failed:
            sections.append(f"  Changed files analysed: {file_health.get('files_analysed', 0)}")
            sections.append(f"  Repositories touched: {file_health.get('repositories_touched', 0)}")
            sections.append(f"  Findings: {file_health.get('findings', 0)}")
            for action in ai_blob.get("action_plan", []) if isinstance(ai_blob, dict) else []:
                sections.append(f"  {str(action.get('priority', 'medium')).upper():<10s}{action.get('title', '')}")
        for error in _coverage_errors(health_coverage):
            sections.append(f"  Collection error: {error}")

    # Documentation quality (clarity + usefulness + explicit-marker lower bound)
    dq_sig = getattr(profile, "doc_quality", None)
    dq_blob = examples.get("doc_quality", {}) if examples else {}
    dq_pages = getattr(dq_sig, "pages_scanned", 0) if dq_sig else 0
    if dq_sig and isinstance(dq_blob, dict):
        doc_coverage = dq_blob.get("coverage_report", {})
        doc_failed = doc_coverage.get("status") in {"failed", "no_data"}
        dq_split = f"{dq_sig.clear_pages} clear / {dq_sig.mixed_pages} mixed / {dq_sig.unclear_pages} unclear"
        sections.extend(["", "Documentation (clarity and usefulness; explicit markers are a lower bound):"])
        sections.append(f"  Coverage: {_coverage_message(doc_coverage)}")
        if not doc_failed:
            sections.append(f"  Average clarity: {dq_sig.avg_clarity:.0f}/100")
            sections.append(f"  Average usefulness: {getattr(dq_sig, 'avg_usefulness', 0):.0f}/100")
            sections.append(f"  Pages scanned: {_doc_pages_value(dq_sig, dq_pages)}")
            sections.append(f"  Clarity split: {dq_split}")
            sections.append(
                f"  Owned / actionable pages: {getattr(dq_sig, 'owned_pages', 0)} / "
                f"{getattr(dq_sig, 'actionable_pages', 0)}"
            )
            sections.append(f"  Explicit AI markers: {dq_sig.ai_marked_pages} page(s)")
        for error in _coverage_errors(doc_coverage):
            sections.append(f"  Collection error: {error}")
        dq_samples = dq_blob.get("samples") if isinstance(dq_blob, dict) else None
        if dq_samples and not doc_failed:
            sections.append("  Examples:")
            for s in dq_samples:
                ref = f" {s['url']}" if s.get("url") else ""
                sections.append(f"    {s.get('title', '')} ({s.get('platform', '')}){ref}".rstrip())
        dq_insights = dq_blob.get("insights", {}) if isinstance(dq_blob, dict) else {}
        if not doc_failed and isinstance(dq_insights, dict) and any(dq_insights.get(k) for k, _ in INSIGHT_CATEGORIES):
            for ik, ilabel in INSIGHT_CATEGORIES:
                for it in dq_insights.get(ik) or []:
                    if isinstance(it, dict) and it.get("title"):
                        ev = f" ({it['evidence']})" if it.get("evidence") else ""
                        link = f" [{it['link']}]" if it.get("link") else ""
                        sections.append(f"  {ilabel.upper():<14s}{it['title']}{ev}{link}")

    # Full profile JSON for machine-readable recovery
    sections.extend(["", "=" * 60, "", "Raw profile JSON:", ""])
    try:
        sections.append(json.dumps(asdict(profile), indent=2, ensure_ascii=False, default=str))
    except Exception:
        sections.append("(serialisation failed)")

    # Examples JSON if provided
    if examples:
        sections.extend(["", "=" * 60, "", "Examples JSON:", ""])
        try:
            sections.append(json.dumps(examples, indent=2, ensure_ascii=False, default=str))
        except Exception:
            sections.append("(serialisation failed)")

    log_path.write_text("\n".join(sections), encoding="utf-8")
    logger.info("Analysis log written to %s", log_path)
    return log_path
