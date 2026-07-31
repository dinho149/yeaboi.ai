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

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from yeaboi.analysis.ai_usage import _source_label
from yeaboi.html_theme import export_page, safe_url
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
        from yeaboi.fs_policy import resolve_and_check
        from yeaboi.paths import _safe_key

        out_dir = resolve_and_check(base_dir, mode="write", context="analysis export dir") / _safe_key(
            project_key, "project"
        )
    else:
        from yeaboi.paths import get_analysis_export_dir

        out_dir = get_analysis_export_dir(project_key)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _format_pct(val: float) -> str:
    """Format a percentage, dropping the decimal if it's .0."""
    return f"{val:.0f}%" if val == int(val) else f"{val:.1f}%"


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


def _practice_cell_text(row: dict, prefix: str, min_sample: int) -> str:
    """One practices cell for export: n/a without data, a raw fraction under
    the sample floor (same rule as the TUI table, Surface Parity), else a %."""
    den = int(row.get(f"{prefix}_den", 0) or 0)
    if not den:
        return "n/a"
    num = int(row.get(f"{prefix}_num", 0) or 0)
    rate = row.get(f"{prefix}_rate")
    if den < min_sample or not isinstance(rate, (int, float)):
        return f"{num}/{den}"
    return f"{rate:.0f}%"


def _practice_rows(ai_blob) -> tuple[list[dict], dict | None, int, dict]:
    """Unpack blob["member_practices"] for export; empty members = old profile."""
    practices = ai_blob.get("member_practices") if isinstance(ai_blob, dict) else None
    if not isinstance(practices, dict) or not practices.get("members"):
        return [], None, 5, {}
    team = practices.get("team")
    return (
        list(practices["members"]),
        team if isinstance(team, dict) else None,
        int(practices.get("min_sample", 5) or 5),
        practices.get("file_data") or {},
    )


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


# ---------------------------------------------------------------------------
# Blocks — the payload vocabulary
#
# This report is not one shape. It is twenty-odd *generated* sections whose
# composition depends on which analyses were enabled and which sources
# answered, so it travels as a list of blocks and the bundle draws them. See
# the note on ``Block`` in ``frontend/src/export/boot.ts`` for why that is a
# better contract here than twenty named interfaces would be.
#
# Nothing below escapes anything, because nothing below builds markup.
# ---------------------------------------------------------------------------


def _tone_up(value: float, ok: float, warn: float) -> str:
    """Reading for a metric where higher is better — completion, accuracy."""
    return "ok" if value >= ok else ("warn" if value >= warn else "danger")


def _tone_down(value: float, ok: float, warn: float) -> str:
    """Reading for a metric where lower is better — spillover, cycle time."""
    return "ok" if value < ok else ("warn" if value < warn else "danger")


def _cell(
    text: str,
    *,
    tone: str = "",
    pct: float | None = None,
    href: str = "",
    note: str = "",
    person: bool = False,
) -> str | dict:
    """One table cell or key/value value — a bare string unless it carries more.

    ``tone`` is the *reading* of the number (ok / warn / danger), never a
    colour. It has to be decided here rather than in the bundle because the
    thresholds are domain facts that differ per column and per direction: 80%
    completion is good, 80% spillover is not.

    ``href`` runs through :func:`safe_url`, so a tracker URL of
    ``javascript:alert(1)`` arrives as plain text rather than a live link.
    """
    out: dict = {"t": text}
    if tone:
        out["tone"] = tone
    if pct is not None:
        out["pct"] = round(min(max(pct, 0.0), 100.0), 1)
    if url := safe_url(href):
        out["href"] = url
    if note:
        out["note"] = note
    if person:
        out["person"] = True
    return out if len(out) > 1 else text


def _pct_cell(pct: float, *, ok: float = 80, warn: float = 50) -> str | dict:
    """A coverage percentage — a number with a direction, so it carries a reading."""
    return _cell(_format_pct(pct), pct=pct, tone=_tone_up(pct, ok, warn))


def _share_cell(pct: float) -> str | dict:
    """A share of a mix — a bar, and deliberately no reading.

    A distribution has no good direction: 15% of tasks being QA is not a
    failure, and 55% being backend is not a warning. Both used to draw through
    the same threshold ramp as a completion rate, so the task-type table
    rendered its own composition in amber and red.
    """
    return _cell(_format_pct(pct), pct=pct)


def _kv(rows: Sequence[tuple[str, str | dict]], *, title: str = "") -> dict | None:
    """Label/value facts about one thing. ``None`` when there are no facts.

    Empty rather than blank, for the same reason ``NoticeBlock`` renders nothing
    for an empty list: a titled block with no rows under it reads as "we looked
    and found nothing", which is a stronger claim than any caller makes.
    ``_add`` drops the ``None``.
    """
    if not rows:
        return None
    block: dict = {"kind": "kv", "rows": [[label, value] for label, value in rows]}
    if title:
        block["title"] = title
    return block


def _table(
    headers: Sequence[str],
    rows: Sequence[Sequence[str | dict]],
    *,
    numeric: Sequence[int] = (),
    title: str = "",
) -> dict | None:
    """Rows that compare with each other. ``numeric`` columns right-align in mono.

    ``None`` for no rows — see :func:`_kv`.
    """
    if not rows:
        return None
    block: dict = {"kind": "table", "headers": list(headers), "rows": [list(row) for row in rows]}
    if numeric:
        block["numeric"] = list(numeric)
    if title:
        block["title"] = title
    return block


def _runs(runs: Sequence[Mapping]) -> list[dict]:
    """Normalise a ``Run[]``: hoist edge whitespace out of emphasised runs.

    ``**text **`` and ``_ text_`` are not emphasis in Markdown — a space against
    the delimiter cancels it outright — and :func:`_md_runs` renders the same
    lists these blocks carry. It is invisible until someone reads the ``.md``,
    and it recurs: the separator between a title and its detail is a natural
    thing to tack onto the emphasised half.

    So rather than asking every producer to remember, the three block builders
    that accept runs do it, once.
    """
    out: list[dict] = []
    for run in runs:
        text = str(run.get("s", ""))
        if not (run.get("strong") or run.get("em")) or text == text.strip():
            out.append(dict(run))
            continue
        if not text.strip():
            # Emphasising whitespace emphasises nothing; keep it as a plain gap
            # rather than splitting it into a lead and a trail of itself.
            out.append({"s": text})
            continue
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        if lead:
            out.append({"s": lead})
        out.append({**run, "s": text.strip()})
        if trail:
            out.append({"s": trail})
    return out


def _bullets(items: Sequence[Sequence[Mapping]], *, title: str = "", ordered: bool = False) -> dict | None:
    """A list of rich-text lines, each a ``Run[]``. ``None`` for no lines."""
    if not items:
        return None
    block: dict = {"kind": "bullets", "items": [_runs(runs) for runs in items]}
    if title:
        block["title"] = title
    if ordered:
        block["ordered"] = True
    return block


def _cards(cards: Sequence[Mapping], *, title: str = "") -> dict | None:
    """Titled groups of rich-text lines. ``None`` for no cards."""
    if not cards:
        return None
    block: dict = {
        "kind": "cards",
        "cards": [{**card, "items": [_runs(runs) for runs in card.get("items", ())]} for card in cards],
    }
    if title:
        block["title"] = title
    return block


def _note(text: str) -> dict:
    """A caveat about what the numbers can and cannot show."""
    return {"kind": "note", "text": text}


def _prose(text: str) -> dict:
    """Free text somebody — or some model — wrote."""
    return {"kind": "prose", "text": text}


def _callout(tone: str, title: str, text: str = "", items: Sequence[Sequence[Mapping]] = ()) -> dict:
    """A finding worth stopping on — a bottleneck, a recommendation, a warning."""
    block: dict = {"kind": "callout", "tone": tone, "title": title}
    if text:
        block["text"] = text
    if items:
        block["items"] = [_runs(runs) for runs in items]
    return block


def _bar(label: str, counts: Sequence[tuple[str, float]]) -> dict | None:
    """A counted breakdown. ``None`` when nothing positive is left to draw."""
    kept = [[str(name), n] for name, n in counts if n > 0]
    return {"kind": "bar", "label": label, "counts": kept} if kept else None


def _md_runs(runs: Sequence[Mapping]) -> str:
    """Render a ``Run[]`` as Markdown — the other consumer of the same structure."""
    out: list[str] = []
    for run in _runs(runs):
        text = str(run.get("s", ""))
        if run.get("href"):
            text = f"[{text}]({run['href']})"
        if run.get("em"):
            text = f"_{text}_"
        if run.get("strong"):
            text = f"**{text}**"
        out.append(text)
    return "".join(out)


def _insight_runs(it: dict) -> list[dict]:
    """One coaching insight as ``Run[]``: title, detail, evidence, cited example.

    **This is one of the pairs that collapsed.** ``_insight_html`` and
    ``_insight_md`` rendered the same four fields into two markups, so every
    future field had to be added to both, in two different escaping regimes.
    The runs *are* the fields; each renderer draws them.
    """
    runs: list[dict] = [
        {"s": str(it.get("title", "")), "strong": True},
        {"s": " — "},
        {"s": str(it.get("detail", ""))},
    ]
    # The separating space is its own run, outside the emphasis. Markdown does
    # not italicise `_ text_` — a leading space inside the delimiters cancels
    # the emphasis outright, which is invisible until someone reads the .md.
    if it.get("evidence"):
        runs += [{"s": " "}, {"s": f"({it['evidence']})", "em": True}]
    if link := safe_url(str(it.get("link", "") or "").strip()):
        runs += [{"s": " "}, {"s": "↳ example", "href": link}]
    return runs


def _action_runs(action: dict) -> list[dict]:
    """One prioritised action as ``Run[]``.

    The HTML twin used to omit ``completion_check`` where the Markdown twin
    included it — the same action in two artifacts, and only one of them told
    you how you would know it was done. One producer, so it cannot recur.
    """
    title = str(action.get("title", ""))
    head: dict = {"s": f"{str(action.get('priority', '')).upper()}: {title}", "strong": True}
    if link := safe_url(str(action.get("link", "") or "")):
        head["href"] = link
    meta = [
        f"Scope: {', '.join(str(v) for v in action.get('affected_scope', []))}",
        f"Owner: {action.get('owner_role', '')}",
        f"Effort: {action.get('effort', '')}",
    ]
    if check := action.get("completion_check", ""):
        meta.append(f"Done when: {check}")
    return [
        head,
        {"s": " — "},
        {"s": str(action.get("detail", ""))},
        {"s": " "},
        {"s": f"({'; '.join(meta)})", "em": True},
    ]


def _ai_example_runs(s: dict) -> list[dict]:
    """One AI-adoption sample as ``Run[]``.

    **The last of the twins, and it had drifted the furthest.** The Markdown
    renderer linked the sample's *title*; the HTML one linked the source label
    and left the title dead. Same sample, two artifacts, and only one of them
    let you click through to the commit. This keeps the Markdown behaviour,
    which is the useful one.
    """
    tool = "unlabelled AI" if s.get("tool") == "other_ai" else str(s.get("tool", ""))
    # The separating space is its own run, outside the emphasis — same rule as
    # `_insight_runs`. A space inside the delimiters cancels the emphasis in
    # Markdown, and it is invisible until someone reads the .md.
    runs: list[dict] = [{"s": f"[{tool}]", "strong": True}, {"s": " "}]
    title = str(s.get("title", ""))
    if url := safe_url(str(s.get("url", "") or "")):
        runs.append({"s": title, "href": url})
        runs.append({"s": f" — {_source_label(str(s.get('source', '')))}"})
    else:
        runs.append({"s": title})
        if key := str(s.get("key", "") or ""):
            runs.append({"s": f" — commit {key}"})
    return runs


def _doc_example_runs(s: dict) -> list[dict]:
    """One documentation sample as ``Run[]`` — linked page title plus its scores."""
    title = str(s.get("title", "Untitled"))
    meta = f"{s.get('platform', '')} · clarity {s.get('clarity', 0):.0f} · usefulness {s.get('usefulness', 0):.0f}"
    head: dict = {"s": title}
    if url := safe_url(str(s.get("url", "") or "")):
        head["href"] = url
    return [head, {"s": " "}, {"s": f"({meta})", "em": True}]


def _insight_md(it: dict) -> str:
    """Render one coaching insight as a Markdown bullet."""
    return f"- {_md_runs(_insight_runs(it))}"


def _action_md(action: dict) -> str:
    """Render one prioritised action as a Markdown bullet."""
    return f"- {_md_runs(_action_runs(action))}"


def _ai_example_md(s: dict) -> str:
    """Render one AI-adoption sample as a Markdown bullet."""
    return f"- {_md_runs(_ai_example_runs(s))}"


def _doc_example_md(s: dict) -> str:
    """Render one documentation sample as a Markdown bullet."""
    return f"- {_md_runs(_doc_example_runs(s))}"


def _insight_cards(blob: Mapping, *, title: str = "") -> dict | None:
    """The coaching-insight cards for one analysis blob, or ``None`` for none.

    Three sections built this same list, and the top-level Team Insights one
    dropped the cited example — so an insight linked to its evidence in the AI
    and Documentation sections and not in the one that led the report.
    """
    cards: list[dict] = []
    for key, label in INSIGHT_CATEGORIES:
        items = blob.get(key)
        if not isinstance(items, list) or not items:
            continue
        runs = [_insight_runs(it) for it in items if isinstance(it, dict) and it.get("title")]
        if runs:
            cards.append({"title": label, "items": runs})
    return _cards(cards, title=title) if cards else None


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


def _ceremony_blocks(ceremony) -> list[dict]:
    """The 'Ceremony Cadence & Trends' section as blocks."""
    blocks: list[dict] = [_kv(_ceremony_rows(ceremony))]
    for title, themes in (
        ("What's been working", ceremony.went_well_themes),
        ("Recurring pain points", ceremony.didnt_go_well_themes),
    ):
        if themes:
            blocks.append(_bullets([[{"s": str(t)}, {"s": f" ({n}×)", "em": True}] for t, n in themes], title=title))
    return blocks


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
    markdown_name: str = "",
) -> str:
    """Build a self-contained team-profile report as a React page.

    Returns the page; the content itself travels as a ``profile`` payload of
    generated :ref:`blocks <Block>` and is drawn in the browser. Nothing here
    builds markup, which is why this function lost roughly a thousand lines and
    all of its escaping.

    ``ceremony`` is an optional CeremonyContext (agent/ceremony_history.py). When
    present and non-empty, a "Ceremony Cadence & Trends" section is added.
    """
    ex = examples or {}
    sections: list[dict] = []
    nav_links: list[tuple[str, str]] = []

    def _add(id_: str, title: str, blocks: Sequence[dict | None], *, nav: str = "") -> None:
        """Append a section, dropping blocks that decided they had nothing to draw."""
        kept = [block for block in blocks if block]
        if not kept:
            return
        sections.append({"id": id_, "title": title, "blocks": kept})
        if nav:
            nav_links.append((id_, nav))

    # What the analysis could not read, hoisted out of the three sections that
    # each used to carry their own "Collection errors" list. A reader had to
    # visit all three to learn what was missing from the numbers above; now it
    # is one list, before them, with the analysis named on every line.
    coverage: list[str] = []

    def _collect_coverage(label: str, report: Mapping) -> None:
        if str(report.get("status", "complete")) in {"failed", "no_data", "partial"}:
            coverage.append(f"{label} — {_coverage_message(report)}")
        coverage.extend(f"{label} — {error}" for error in _coverage_errors(report))

    # ── Executive Summary (AI narrative, generated at analysis time) ─
    narrative = ex.get("narrative", {})
    if isinstance(narrative, dict) and narrative.get("executive_summary"):
        blocks: list[dict | None] = []
        depth = str(ex.get("analysis_depth", "")).strip().lower()
        if depth in ("quick", "deep"):
            blocks.append(_kv([("Analysis depth", depth.capitalize())]))
        blocks.append(_prose(str(narrative["executive_summary"])))
        n_sections = narrative.get("sections", {})
        if isinstance(n_sections, dict):
            items = [
                [{"s": f"{title}: ", "strong": True}, {"s": str(n_sections[key]), "em": True}]
                for key, title in _NARRATIVE_TITLES
                if n_sections.get(key)
            ]
            if items:
                blocks.append(_bullets(items))
        _add("summary", "Executive Summary", blocks, nav="Summary")

    # ── Team Insights (AI coaching, generated at analysis time) ─────
    insights = ex.get("insights", {})
    if isinstance(insights, dict):
        _add("insights", "Team Insights", [_insight_cards(insights)], nav="Insights")

    # ── AI Adoption (detectable AI-tool footprint — lower bound) ─────
    ai_sig = getattr(profile, "ai_adoption", None)
    ai_blob = ex.get("ai_adoption", {})
    code_features = set(ai_blob.get("enabled_features") or ("ai_footprint", "code_health"))
    ai_scanned = (getattr(ai_sig, "scanned_commits", 0) + getattr(ai_sig, "scanned_prs", 0)) if ai_sig else 0
    if "ai_footprint" in code_features and ai_sig and ai_scanned:
        activity_coverage = ai_blob.get("activity_coverage", {}) if isinstance(ai_blob, dict) else {}
        _collect_coverage("AI usage", activity_coverage)
        a_rows: list[tuple[str, str | dict]] = [
            ("Coverage", _coverage_message(activity_coverage)),
            ("Detectable footprint", _footprint_value(ai_sig)),
            ("Commits with AI marker", f"{ai_sig.ai_commits} of {ai_sig.scanned_commits}"),
        ]
        if ai_sig.scanned_prs:
            a_rows.append(("PRs with AI marker", f"{ai_sig.ai_prs} of {ai_sig.scanned_prs}"))
        if ai_sig.sources_scanned:
            a_rows.append(("Sources scanned", ", ".join(_source_label(s) for s in ai_sig.sources_scanned)))
        if isinstance(ai_blob, dict) and ai_blob.get("selected_users"):
            selected = [str(u) for u in ai_blob["selected_users"]]
            a_rows.append(("Selected users", ", ".join(selected)))
            a_rows.append(("Matched identities", f"{len(ai_blob.get('matched_identities') or {})} of {len(selected)}"))
        if getattr(ai_sig, "repos_scanned", ()):
            a_rows.append(("Repositories scanned", str(len(ai_sig.repos_scanned))))
            preview = ", ".join(ai_sig.repos_scanned[:5])
            if len(ai_sig.repos_scanned) > 5:
                preview += f" (+{len(ai_sig.repos_scanned) - 5} more)"
            a_rows.append(("Repository scope", preview))

        blocks = [
            _note(
                "Lower bound — only AI tools that leave a marker in commit messages or PR "
                "descriptions are counted. Inline IDE assist (Copilot ghost-text, Cursor Tab) "
                "leaves no trace, so real usage is at least this."
            ),
            _kv(a_rows),
        ]

        p_members, p_team, p_min, p_file_data = _practice_rows(ai_blob)
        if p_members:
            blocks.append(
                _table(
                    ["Member", "Commits", "PRs", "Tests", "Docs", "Tickets", "Descriptions"],
                    [
                        [
                            _cell(str(row.get("member", "")), person=True),
                            str(row.get("commits", 0)),
                            str(row.get("prs", 0)),
                            _practice_cell_text(row, "tests", p_min),
                            _practice_cell_text(row, "docs", p_min),
                            _practice_cell_text(row, "ticket", p_min),
                            _practice_cell_text(row, "desc", p_min),
                        ]
                        for row in p_members + ([p_team] if p_team else [])
                    ],
                    numeric=(1, 2, 3, 4, 5, 6),
                    title="Engineering practices by member",
                )
            )
            if p_file_data.get("total") and p_file_data.get("with_file_data", 0) < p_file_data["total"]:
                blocks.append(
                    _note(
                        f"File-based columns (Tests, Docs) cover {p_file_data.get('with_file_data', 0)} "
                        f"of {p_file_data['total']} items with change metadata."
                    )
                )

        if ai_sig.per_tool:
            blocks.append(
                _bar("By tool", [("unlabelled AI" if t == "other_ai" else str(t), n) for t, n in ai_sig.per_tool])
            )
        if getattr(ai_sig, "per_source", ()):
            blocks.append(_bar("By source", [(_source_label(s), n) for s, n in ai_sig.per_source]))
        if ai_sig.per_activity:
            blocks.append(_bar("By activity", [(str(a), n) for a, n in ai_sig.per_activity]))
        if ai_sig.per_author:
            blocks.append(
                _table(
                    ["Contributor", "AI-marked"],
                    [[_cell(str(a), person=True), str(n)] for a, n in ai_sig.per_author[:8]],
                    numeric=(1,),
                    title="By contributor",
                )
            )
        ai_coverage = ai_blob.get("coverage") if isinstance(ai_blob, dict) else None
        if ai_coverage:
            blocks.append(_bullets([[{"s": str(gap)}] for gap in ai_coverage], title="Not scanned"))
        ai_samples = ai_blob.get("samples") if isinstance(ai_blob, dict) else None
        if ai_samples:
            blocks.append(_bullets([_ai_example_runs(s) for s in ai_samples], title="Examples"))
        ai_insights = ai_blob.get("insights", {}) if isinstance(ai_blob, dict) else {}
        if isinstance(ai_insights, dict):
            blocks.append(_insight_cards(ai_insights))
        _add("ai-adoption", "AI Usage", blocks, nav="AI Usage")

    # ── Code Health (findings in files the selected users touched) ───
    file_health = ai_blob.get("repository_health", {}) if isinstance(ai_blob, dict) else {}
    if "code_health" in code_features and file_health:
        health_coverage = ai_blob.get("coverage_report", {})
        health_failed = health_coverage.get("status") in {"failed", "no_data"}
        _collect_coverage("Code health", health_coverage)
        h_rows: list[tuple[str, str | dict]] = [("Coverage", _coverage_message(health_coverage))]
        if not health_failed:
            h_rows += [
                ("Changed files analysed", str(file_health.get("files_analysed", 0))),
                ("Repositories touched", str(file_health.get("repositories_touched", 0))),
                ("Findings", str(file_health.get("findings", 0))),
            ]
        blocks = [
            _note(
                "Scoped to files attributable to the selected users. Untouched repositories "
                "and unrelated contributors are not analysed."
            ),
            _kv(h_rows),
        ]
        health_actions = ai_blob.get("action_plan", []) if isinstance(ai_blob, dict) else []
        if health_actions and not health_failed:
            blocks.append(
                _bullets(
                    [_action_runs(action) for action in health_actions],
                    title="Prioritized action plan",
                    ordered=True,
                )
            )
        _add("code-health", "Code Health", blocks, nav="Code Health")

    # ── Documentation (Notion/Confluence clarity + usefulness) ───────
    dq_sig = getattr(profile, "doc_quality", None)
    dq_blob = ex.get("doc_quality", {})
    dq_pages = getattr(dq_sig, "pages_scanned", 0) if dq_sig else 0
    if dq_sig and isinstance(dq_blob, dict):
        doc_coverage = dq_blob.get("coverage_report", {})
        doc_failed = doc_coverage.get("status") in {"failed", "no_data"}
        _collect_coverage("Documentation", doc_coverage)
        d_rows: list[tuple[str, str | dict]] = [("Coverage", _coverage_message(doc_coverage))]
        if not doc_failed:
            d_rows += [
                ("Average clarity", f"{dq_sig.avg_clarity:.0f}/100"),
                ("Average usefulness", f"{getattr(dq_sig, 'avg_usefulness', 0):.0f}/100"),
                ("Pages scanned", _doc_pages_value(dq_sig, dq_pages)),
                (
                    "Clarity split",
                    f"{dq_sig.clear_pages} clear / {dq_sig.mixed_pages} mixed / {dq_sig.unclear_pages} unclear",
                ),
                (
                    "Owned / actionable",
                    f"{getattr(dq_sig, 'owned_pages', 0)} / {getattr(dq_sig, 'actionable_pages', 0)}",
                ),
                ("Explicit AI markers", f"{dq_sig.ai_marked_pages} page(s) (lower bound)"),
            ]
        blocks = [
            _note(
                "Clarity is a readability score. Usefulness measures purpose, ownership, "
                "structure, and actionability. Explicit AI markers are a lower bound."
            ),
            _kv(d_rows),
        ]
        if dq_sig.flagged_pages and not doc_failed:
            blocks.append(
                _bullets(
                    [
                        [{"s": str(title), "strong": True}, {"s": f" — {reason}"}]
                        for title, reason in dq_sig.flagged_pages
                    ],
                    title="Flagged pages",
                )
            )
        dq_samples = dq_blob.get("samples") if isinstance(dq_blob, dict) else None
        if dq_samples and not doc_failed:
            blocks.append(_bullets([_doc_example_runs(s) for s in dq_samples], title="Examples"))
        dq_insights = dq_blob.get("insights", {}) if isinstance(dq_blob, dict) else {}
        if not doc_failed and isinstance(dq_insights, dict):
            blocks.append(_insight_cards(dq_insights))
        dq_actions = dq_blob.get("action_plan", []) if isinstance(dq_blob, dict) else []
        if dq_actions and not doc_failed:
            blocks.append(
                _bullets(
                    [_action_runs(action) for action in dq_actions],
                    title="Prioritized action plan",
                    ordered=True,
                )
            )
        _add("documentation", "Documentation", blocks, nav="Documentation")

    # ── Team & Velocity ─────────────────────────────────────────────
    vel_rows: list[tuple[str, str | dict]] = []
    team_size = ex.get("team_size", 0)
    members = ex.get("team_members", [])
    per_dev = ex.get("per_dev_velocity", 0)

    if team_size and isinstance(team_size, int):
        member_note = f"{team_size} contributors"
        if members and isinstance(members, list):
            member_note += f" ({', '.join(str(m) for m in members[:8])})"
        vel_rows.append(("Team size", member_note))

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
    scope_blob = ex.get("scope_changes", {})
    if isinstance(scope_blob, dict) and scope_blob.get("totals"):
        committed = scope_blob["totals"].get("avg_committed_velocity", 0.0)
        delivered = scope_blob["totals"].get("avg_delivered_velocity", 0.0)
        if committed > 0:
            accuracy = round(delivered / committed * 100)
            vel_rows.append(("Committed avg", f"{committed:g} pts/sprint"))
            vel_rows.append(("Delivered avg", f"{delivered:g} pts/sprint"))
            # Its own row rather than a coloured parenthetical inside the last
            # one: the accuracy is the judged number, the averages are not.
            vel_rows.append(("Delivery accuracy", _cell(f"{accuracy}%", tone=_tone_up(accuracy, 85, 70))))
    contributors = ex.get("contributor_stats", [])
    if isinstance(contributors, list) and contributors:
        per_sprint = [c.get("per_sprint", 0) for c in contributors if c.get("per_sprint", 0) > 0]
        if per_sprint:
            vel_rows.append(("Per developer", f"{round(sum(per_sprint) / len(per_sprint), 1)} pts/sprint"))
    elif per_dev and isinstance(per_dev, (int, float)) and per_dev > 0:
        vel_rows.append(("Per developer", f"{per_dev} pts/sprint"))
    if vel > 0:
        vel_rows.append(("Variance", f"±{std} ({std / vel * 100:.0f}%)"))
    if profile.sprint_completion_rate > 0:
        vel_rows.append(("Completion rate", _pct_cell(profile.sprint_completion_rate)))
    if profile.spillover.carried_over_pct > 0:
        vel_rows.append(("Spillover", f"{_format_pct(profile.spillover.carried_over_pct)} carried over"))

    velocity_trend = ex.get("velocity_trend", {})
    if isinstance(velocity_trend, dict) and velocity_trend.get("trend") not in (None, "", "insufficient_data"):
        direction = str(velocity_trend["trend"])
        vel_rows.append(
            (
                "Trend",
                _cell(
                    f"{ {'improving': '↗', 'degrading': '↘'}.get(direction, '→') } {direction.capitalize()}",
                    tone={"improving": "ok", "degrading": "danger"}.get(direction, ""),
                    note=(
                        f"{velocity_trend.get('first_velocity', 0)} → {velocity_trend.get('last_velocity', 0)}, "
                        f"{velocity_trend.get('slope', 0):+.1f}/sprint"
                    ),
                ),
            )
        )

    _add("velocity", "Team & Velocity", [_kv(vel_rows)], nav="Velocity")

    # ── Ceremony cadence & trends (Standup + Retro history) ─────────
    if ceremony is not None and not ceremony.is_empty:
        _add("ceremonies", "Ceremony Cadence & Trends", _ceremony_blocks(ceremony), nav="Ceremonies")

    # ── Recurring work ──────────────────────────────────────────────
    recurring_count = ex.get("recurring_count", 0)
    delivery_count = ex.get("delivery_count", 0)
    recurring_items = ex.get("recurring", [])
    if isinstance(recurring_count, int) and recurring_count > 0:
        blocks = [_note(f"{recurring_count} recurring tickets excluded ({delivery_count} delivery stories analysed)")]
        if isinstance(recurring_items, list) and recurring_items:
            blocks.append(
                _bullets(
                    [
                        [{"s": str(r.get("issue_key", "")), "strong": True}, {"s": f" {r.get('summary', '')}"}]
                        for r in recurring_items[:5]
                        if isinstance(r, dict)
                    ]
                )
            )
        _add("recurring", "Recurring Work", blocks, nav="Recurring")

    # ── Spillover Root Causes ───────────────────────────────────────
    spill_corr = ex.get("spillover_correlation", {})
    if isinstance(spill_corr, dict) and spill_corr:
        by_size = spill_corr.get("by_size", {})
        by_discipline = spill_corr.get("by_discipline", {})
        by_tasks = spill_corr.get("by_task_count", {})
        buckets = (by_size, by_discipline, by_tasks)
        if any(v > 0 for d in buckets if isinstance(d, dict) for v in d.values()):
            sc_rows: list[tuple[str, str | dict]] = []
            if by_size:
                ordered = sorted(by_size.items(), key=lambda pair: int(pair[0]))
                sc_rows.append(("By story size", " · ".join(f"{size}pt={pct:.0f}%" for size, pct in ordered)))
            if by_discipline:
                pairs = sorted(by_discipline.items())
                sc_rows.append(("By discipline", " · ".join(f"{name}={pct:.0f}%" for name, pct in pairs)))
            if by_tasks:
                sc_rows.append(("By task count", " · ".join(f"{b}={pct:.0f}%" for b, pct in by_tasks.items())))
            _add("spillover", "Spillover Root Causes", [_kv(sc_rows)], nav="Spillover")

    # ── Sprint Breakdown ────────────────────────────────────────────
    if isinstance(sp_details, list) and sp_details:
        sprint_rows: list[list[str | dict]] = []
        for sd in sp_details:
            if not isinstance(sd, dict):
                continue
            rate = sd.get("rate", 0)
            if sd.get("done", False):
                status, tone = "Done", "ok"
            elif sd.get("has_shadow", False):
                status, tone = "Shadow", "warn"
            else:
                status, tone = "Missed", "danger"
            sprint_rows.append(
                [
                    str(sd.get("name", "?")),
                    str(sd.get("points", 0)),
                    f"{sd.get('completed', 0)}/{sd.get('planned', 0)}",
                    _cell(f"{rate}%", tone=_tone_up(rate, 80, 50)),
                    # A word, not a ✓/○/✗ glyph. The icon column was announced
                    # to a screen reader as a bare symbol, so the one column
                    # that said whether the sprint landed said nothing at all.
                    _cell(status, tone=tone),
                ]
            )

        if sprint_rows:
            blocks = []
            # Velocity chart (optional charts extra) — embedded as a data: URI
            # so the page stays self-contained and works offline.
            from yeaboi.charts import velocity_chart
            from yeaboi.html_theme import image_data_uri

            chart_rows = [
                (str(sd.get("name", "?")), float(sd.get("planned", 0) or 0), float(sd.get("completed", 0) or 0))
                for sd in sp_details
                if isinstance(sd, dict)
            ]
            chart = velocity_chart(chart_rows, charts_dir / "velocity.png") if charts_dir is not None else None
            if chart and (chart_uri := image_data_uri(chart)):
                blocks.append({"kind": "image", "src": chart_uri, "alt": "Sprint velocity"})

            blocks.append(
                _table(
                    ["Sprint", "Pts", "Done", "Rate", "Status"],
                    sprint_rows,
                    numeric=(1, 2, 3),
                )
            )

            incomplete = [
                sd
                for sd in sp_details
                if isinstance(sd, dict)
                and (not sd.get("done", False) or sd.get("has_shadow", False))
                and sd.get("incomplete")
            ]
            if incomplete:
                cards: list[dict] = []
                for sd in incomplete[:3]:
                    gap = sd.get("planned", 0) - sd.get("completed", 0)
                    label_parts = []
                    if gap > 0:
                        label_parts.append(f"{gap} stories not completed")
                    if sd.get("has_shadow", False):
                        label_parts.append("shadow spillover")
                    items = []
                    for item in sd.get("incomplete", [])[:3]:
                        if not isinstance(item, dict):
                            continue
                        points = item.get("points", 0)
                        detail = " (re-created)" if item.get("shadow", False) else (f" ({points}pts)" if points else "")
                        items.append(
                            [
                                {"s": str(item.get("issue_key", "")), "strong": True},
                                {"s": f" {item.get('summary', '')}"},
                                {"s": detail, "em": True},
                            ]
                        )
                    title = str(sd.get("name", "?"))
                    cards.append(
                        {"title": f"{title} — {' + '.join(label_parts)}" if label_parts else title, "items": items}
                    )
                blocks.append(_cards(cards, title="Incomplete sprint analysis"))

            if isinstance(scope_blob, dict) and scope_blob.get("totals"):
                totals = scope_blob["totals"]
                added = totals.get("added_mid_sprint", 0)
                re_estimated = totals.get("re_estimated", 0)
                total_stories = totals.get("total_stories", 0)
                committed = totals.get("avg_committed_velocity", 0.0)
                delivered = totals.get("avg_delivered_velocity", 0.0)
                if added > 0 or re_estimated > 0 or committed > 0:
                    scope_rows: list[tuple[str, str | dict]] = []
                    if committed > 0:
                        accuracy = round(delivered / committed * 100)
                        scope_rows.append(("Committed → delivered", f"{committed:g} → {delivered:g} pts/sprint avg"))
                        scope_rows.append(("Delivery accuracy", _cell(f"{accuracy}%", tone=_tone_up(accuracy, 85, 70))))
                    if total_stories > 0 and (added > 0 or re_estimated > 0):
                        scope_rows.append(("Added mid-sprint", f"{added} ({added * 100 // total_stories}%)"))
                        scope_rows.append(("Re-estimated", f"{re_estimated} ({re_estimated * 100 // total_stories}%)"))
                    if scope_rows:
                        blocks.append(_kv(scope_rows, title="Scope tracking"))

                    timelines = [t for t in scope_blob.get("timelines", []) if getattr(t, "change_events", None)]
                    for timeline in timelines[-4:]:
                        blocks.extend(_scope_timeline_blocks(timeline))

                    chains = scope_blob.get("carry_over_chains", [])
                    if chains:
                        blocks.append(
                            _bullets(
                                [
                                    [
                                        {"s": str(chain.get("issue_key", "")), "strong": True},
                                        {"s": " " + " → ".join(str(s) for s in chain.get("sprints", []))},
                                    ]
                                    for chain in chains[:5]
                                    if isinstance(chain, dict)
                                ],
                                title=f"{len(chains)} stories bounced across 3+ sprints",
                            )
                        )

            blocks.append(_note(" · ".join(ANALYSIS_GLOSSARY[key] for key in _SPRINT_GLOSSARY_KEYS)))
            _add("sprints", "Sprint Breakdown", blocks, nav="Sprints")

    # ── Team Members ───────────────────────────────────────────────
    if isinstance(contributors, list) and contributors:
        recurring_pts = sum(c.get("recurring_pts", 0) for c in contributors)
        delivery_pts = sum(c.get("delivery_pts", 0) for c in contributors)
        blocks = []
        if recurring_pts > 0:
            total = recurring_pts + delivery_pts
            share = round(recurring_pts / total * 100) if total else 0
            blocks.append(_kv([("Interrupted work", f"{recurring_pts:g} pts ({share}% of total effort)")]))

        member_rows: list[list[str | dict]] = []
        for cs in contributors[:10]:
            spill = cs.get("spill_rate", 0)
            cycle = cs.get("avg_cycle_time", 0)
            discipline = cs.get("top_discipline", "fullstack")
            work_type = cs.get("top_work_type", "")
            per_sprint_pts = cs.get("per_sprint", 0)
            member_rows.append(
                [
                    _cell(str(cs.get("name", "")), person=True),
                    str(cs.get("delivery_pts", 0)),
                    str(cs.get("stories_completed", 0)),
                    _cell(f"{spill}%", tone=_tone_down(spill, 10, 25)),
                    f"{cycle:.0f}d" if cycle > 0 else "—",
                    str(cs.get("sprints_active", 0)),
                    (f"{discipline}/{work_type.split('/')[0]}" if work_type else str(discipline))[:18],
                    _cell(
                        str(per_sprint_pts),
                        tone="ok" if per_sprint_pts >= 3 else ("warn" if per_sprint_pts >= 1.5 else "low"),
                    ),
                ]
            )
        blocks.append(
            _table(
                ["Name", "Delivered", "Stories", "Spill%", "Cycle", "Sprints", "Focus", "Pts/sprint"],
                member_rows,
                numeric=(1, 2, 3, 4, 5, 7),
            )
        )
        if len(contributors) >= 3 and delivery_pts > 0:
            top = contributors[0]
            top_share = round(top["delivery_pts"] / delivery_pts * 100)
            if top_share >= 40:
                blocks.append(_callout("warn", f"{top['name']} carries {top_share}% of delivery work"))
        _add("team-members", "Team Members", blocks, nav="Team")

    # ── Shadow Spillover ────────────────────────────────────────────
    shadow = ex.get("shadow_spillover", [])
    if isinstance(shadow, list) and shadow:
        items = []
        for sh in shadow[:5]:
            if not isinstance(sh, dict):
                continue
            key_run: dict = {"s": str(sh.get("issue_key", "")), "strong": True}
            if url := safe_url(str(sh.get("issue_url", "") or "")):
                key_run["href"] = url
            items.append(
                [
                    key_run,
                    {"s": f" {sh.get('title', '')}"},
                    {"s": f" {sh.get('from_sprint', '')} → {sh.get('to_sprint', '')}", "em": True},
                ]
            )
        _add(
            "shadow",
            "Shadow Spillover",
            [
                _callout(
                    "warn",
                    f"{len(shadow)} re-created stories detected",
                    "Closed in one sprint but re-created in the next:",
                    items,
                )
            ],
            nav="Shadow",
        )

    # ── Discipline-Specific Calibration ─────────────────────────────
    disc_cal = ex.get("discipline_calibration", {})
    if isinstance(disc_cal, dict) and len(disc_cal) > 1:
        blocks = []
        for discipline, entries in sorted(disc_cal.items()):
            if not isinstance(entries, list) or not entries:
                continue
            rows: list[list[str | dict]] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                points = entry.get("points", 0)
                variance = entry.get("variance", 0)
                spill = entry.get("spill_pct", 0)
                rows.append(
                    [
                        f"{points}pt{'s' if points != 1 else ''}",
                        f"{entry.get('avg_cycle_days', 0):.0f}d",
                        f"±{variance:.0f}d" if variance > 0 else "—",
                        str(entry.get("samples", 0)),
                        _cell(f"{spill:.0f}%", tone=_tone_down(spill, 10, 25)) if spill > 0 else "—",
                    ]
                )
            if rows:
                blocks.append(
                    _table(
                        ["Points", "Cycle time", "Variance", "Samples", "Spillover"],
                        rows,
                        numeric=(0, 1, 2, 3, 4),
                        title=str(discipline),
                    )
                )
        _add("disc-cal", "Calibration by Discipline", blocks, nav="Discipline Cal.")

    # ── Point Calibration ───────────────────────────────────────────
    cals = [c for c in profile.point_calibrations if c.sample_count > 0]
    # A JSON round-trip may stringify int keys — normalise back to int.
    conf_levels: dict[int, str] = {}
    raw_conf = ex.get("confidence_levels", {})
    if isinstance(raw_conf, dict):
        for key, value in raw_conf.items():
            try:
                conf_levels[int(key)] = str(value)
            except (ValueError, TypeError):
                pass
    if cals:
        cal_rows: list[list[str | dict]] = []
        cal_details: list[list[dict]] = []
        for c in cals:
            label = f"{c.point_value} pt{'s' if c.point_value != 1 else ''}"
            confidence = conf_levels.get(c.point_value, "")
            cal_rows.append(
                [
                    label,
                    f"{c.avg_cycle_time_days:.0f} days",
                    str(c.sample_count),
                    f"~{c.typical_task_count:.0f}",
                    _format_pct(c.overshoot_pct),
                    _cell(
                        confidence.upper(),
                        tone={"high": "ok", "medium": "muted", "low": "warn"}.get(confidence, ""),
                    )
                    if confidence
                    else "",
                ]
            )
            if c.common_patterns:
                cal_details.append(
                    [{"s": label, "strong": True}, {"s": f" — typically {', '.join(c.common_patterns)}"}]
                )
            # The examples used to ride as `colspan=6` sub-rows under their own
            # point value, which a table cannot express and a screen reader read
            # as a stray cell. One list under the table says the same thing.
            for example in ex.get(f"calibration_{c.point_value}pt", [])[:2]:
                if not isinstance(example, dict):
                    continue
                key_run = {"s": str(example.get("issue_key", ""))}
                if url := safe_url(str(example.get("issue_url", "") or "")):
                    key_run["href"] = url
                runs = [{"s": label, "strong": True}, {"s": " — "}, key_run]
                runs.append({"s": f" {example.get('summary', '')}"})
                if detail := str(example.get("detail", "")):
                    runs.append({"s": f" ({detail})", "em": True})
                cal_details.append(runs)

        blocks = [
            _table(
                ["Points", "Avg cycle time", "Samples", "Tasks", "Slip", "Confidence"],
                cal_rows,
                numeric=(0, 1, 2, 3, 4),
            )
        ]
        if cal_details:
            blocks.append(_bullets(cal_details, title="What these look like"))
        _add("calibration", "What Each Point Value Means", blocks, nav="Calibration")

    # ── Story Shapes ────────────────────────────────────────────────
    shapes = [s for s in profile.story_shapes if s.sample_count > 0]
    if shapes:
        _add(
            "shapes",
            "Story Shape by Discipline",
            [
                _table(
                    ["Discipline", "Avg pts", "Avg ACs", "Avg tasks", "Samples"],
                    [
                        [
                            str(s.discipline),
                            str(s.avg_points),
                            str(s.avg_ac_count),
                            str(s.avg_task_count),
                            str(s.sample_count),
                        ]
                        for s in shapes
                    ],
                    numeric=(1, 2, 3, 4),
                )
            ],
            nav="Story Shapes",
        )

    # ── Task Decomposition ──────────────────────────────────────────
    task_decomp = ex.get("task_decomposition", {})
    if isinstance(task_decomp, dict) and task_decomp.get("total_tasks", 0) > 0:
        blocks = [
            _kv(
                [
                    ("Stories with tasks", f"{task_decomp['stories_with_tasks']} / {task_decomp['total_stories']}"),
                    ("Total tasks", str(task_decomp["total_tasks"])),
                    ("Avg tasks/story", str(task_decomp["avg_tasks_per_story"])),
                    ("Task completion", _pct_cell(task_decomp["task_completion_rate"])),
                ]
            )
        ]
        type_dist = task_decomp.get("type_distribution", {})
        if type_dist:
            # The bar reads the mix at a glance; the table below keeps the
            # exact numbers. Both are built from the same dict.
            blocks.append(_bar("Task type distribution", [(cat, pct) for cat, pct in type_dist.items()]))
            blocks.append(
                _table(
                    ["Type", "Share"],
                    [[str(cat), _share_cell(pct)] for cat, pct in type_dist.items()],
                )
            )
        for category, rate, count in task_decomp.get("bottlenecks", []):
            blocks.append(_callout("warn", f"{category} bottleneck", f"Only {rate}% completion ({count} tasks)"))
        common_tasks = task_decomp.get("common_tasks", [])
        if common_tasks:
            blocks.append(
                _table(
                    ["Task", "Seen"],
                    [[str(title)[:45], f"×{count}"] for title, count in common_tasks[:4]],
                    numeric=(1,),
                    title="Common task patterns",
                )
            )
        assignees = task_decomp.get("task_assignees", {})
        if assignees:
            blocks.append(
                _table(
                    ["Assignee", "Tasks"],
                    [[_cell(str(name), person=True), str(count)] for name, count in list(assignees.items())[:5]],
                    numeric=(1,),
                    title="Task assignees",
                )
            )
        _add("tasks", "Task Decomposition", blocks, nav="Tasks")

    # ── DoD Signals ─────────────────────────────────────────────────
    dod = profile.dod_signal
    dod_practices: list[tuple[str, float, str]] = []
    if dod.stories_with_testing_mention_pct > 0:
        dod_practices.append(("Testing mentioned", dod.stories_with_testing_mention_pct, "dod_testing"))
    if dod.stories_with_pr_link_pct > 0:
        dod_practices.append(("PR linked before close", dod.stories_with_pr_link_pct, "dod_pr"))
    if dod.stories_with_review_mention_pct > 0:
        dod_practices.append(("Code review mentioned", dod.stories_with_review_mention_pct, "dod_review"))
    if dod.stories_with_deploy_mention_pct > 0:
        dod_practices.append(("Deploy mentioned", dod.stories_with_deploy_mention_pct, "dod_deploy"))

    if dod_practices:
        dod_rows: list[list[str | dict]] = []
        for label, pct, example_key in dod_practices:
            example: str | dict = ""
            candidates = ex.get(example_key, [])
            if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
                first = candidates[0]
                example = _cell(
                    str(first.get("issue_key", "")),
                    href=str(first.get("issue_url", "") or ""),
                    note=str(first.get("summary", ""))[:30],
                )
            dod_rows.append([label, _pct_cell(pct), example])
        blocks = [_table(["Practice", "Coverage", "Example"], dod_rows)]
        if dod.common_checklist_items:
            blocks.append(_note(f"Common signals: {', '.join(dod.common_checklist_items[:6])}"))
        _add("dod", "Definition of Done (inferred)", blocks, nav="DoD")

    # ── Proposed DoD ───────────────────────────────────────────────
    proposed = ex.get("proposed_dod", {})
    if isinstance(proposed, dict) and proposed.get("items"):
        health = proposed.get("health", "weak")
        blocks = [
            _callout(
                {"strong": "ok", "moderate": "warn"}.get(health, "danger"),
                str(proposed.get("summary", "")),
            ),
            _table(
                ["Practice", "Status", "Evidence", "Action"],
                [
                    [
                        str(item.get("practice", "")),
                        _cell(
                            str(item.get("status", "missing")),
                            tone={"established": "ok", "emerging": "warn", "missing": "danger"}.get(
                                str(item.get("status", "missing")), "low"
                            ),
                        ),
                        str(item.get("signals", "no evidence")),
                        str(item.get("recommendation", "")),
                    ]
                    for item in proposed["items"]
                ],
            ),
        ]
        ordering = proposed.get("ordering", [])
        if len(ordering) >= 2:
            blocks.append(_note(f"Typical order: {' → '.join(str(o) for o in ordering)}"))
        custom_steps = proposed.get("custom_steps", [])
        if custom_steps:
            steps = ", ".join(f"“{step['title']}” ({step['pct']}%)" for step in custom_steps[:4])
            blocks.append(_note(f"Team-specific steps: {steps}"))
        _add("proposed-dod", "Proposed Definition of Done", blocks, nav="Proposed DoD")

    # ── Writing Patterns ────────────────────────────────────────────
    wp = profile.writing_patterns
    wp_rows: list[tuple[str, str | dict]] = []
    if wp.uses_given_when_then:
        wp_rows.append(("AC format", "Given/When/Then ✓"))
    if wp.median_ac_count > 0:
        wp_rows.append(("Median ACs/story", str(wp.median_ac_count)))
    if wp.median_task_count_per_story > 0:
        wp_rows.append(("Median tasks/story", str(wp.median_task_count_per_story)))
    if wp.subtask_label_distribution:
        parts = " · ".join(f"{label} {int(pct * 100)}%" for label, pct in wp.subtask_label_distribution[:5])
        wp_rows.append(("Sub-task types", parts))
    if wp.common_personas:
        wp_rows.append(("Personas", ", ".join(wp.common_personas[:5])))
    if wp_rows:
        _add("patterns", "Writing Patterns", [_kv(wp_rows)], nav="Patterns")

    # ── Repository Activity ─────────────────────────────────────────
    repos = ex.get("repositories", {})
    if isinstance(repos, dict) and repos.get("top_repos"):
        top_repos = [r for r in repos["top_repos"][:8] if isinstance(r, dict)]
        avg_cycles = repos.get("repo_avg_cycle_time", {})
        spill_prone = {r["repo"] for r in repos.get("spillover_repos", []) if isinstance(r, dict)}

        repo_rows: list[list[str | dict]] = []
        for r in top_repos:
            name = str(r.get("repo", ""))
            cycle = avg_cycles.get(name) if isinstance(avg_cycles, dict) else None
            repo_rows.append(
                [
                    _cell(name, tone="warn" if name in spill_prone else ""),
                    str(r.get("stories", 0)),
                    _share_cell(r.get("pct", 0)),
                    _cell(f"{cycle:.0f}d", tone="warn")
                    if cycle and cycle > 15
                    else (f"{cycle:.0f}d" if cycle else "—"),
                ]
            )
        blocks = [
            _bar("Stories per repository", [(str(r.get("repo", "")), r.get("stories", 0)) for r in top_repos]),
            _table(["Repository", "Stories", "Share", "Avg cycle"], repo_rows, numeric=(1,)),
        ]

        spill_repos = repos.get("spillover_repos", [])
        if isinstance(spill_repos, list) and spill_repos:
            blocks.append(
                _bullets(
                    [
                        [
                            {"s": str(sr.get("repo", "")), "strong": True},
                            {"s": f" {sr.get('spill_rate', 0)}% spillover ({sr.get('spills', 0)} times)", "em": True},
                        ]
                        for sr in spill_repos[:3]
                        if isinstance(sr, dict)
                    ],
                    title="Repos with highest spillover rate",
                )
            )

        by_points = repos.get("by_pts", {})
        if isinstance(by_points, dict) and by_points:
            blocks.append(
                _bullets(
                    [
                        [
                            {"s": f"{points_key}pt", "strong": True},
                            {"s": " " + ", ".join(str(r) for r in by_points[points_key][:3])},
                        ]
                        for points_key in sorted(by_points, key=lambda k: int(k))
                        if by_points[points_key]
                    ],
                    title="Repos by story size",
                )
            )

        _add("repos", "Repository Activity", blocks, nav="Repos")

    # ── Ticket Naming & Organisation ────────────────────────────────
    naming = ex.get("naming_conventions", {})
    if isinstance(naming, dict) and (
        naming.get("title_prefixes")
        or naming.get("label_distribution")
        or naming.get("epic_examples")
        or naming.get("template_sections")
    ):
        prefixes = naming.get("title_prefixes", [])
        nm_rows: list[tuple[str, str | dict]] = [
            (
                "Title prefixes",
                " · ".join(f"{p} {pct}%" for p, pct in prefixes[:5]) if prefixes else "none detected",
            )
        ]
        labels = naming.get("label_distribution", [])
        if labels:
            share = naming.get("stories_with_labels_pct", 0)
            nm_rows.append(("Labels", f"{share}% labelled: " + " · ".join(f"{lbl} {pct}%" for lbl, pct in labels[:6])))
        style = naming.get("epic_naming_style", "")
        epic_examples = naming.get("epic_examples", [])
        if style and epic_examples:
            samples = ", ".join(f"“{str(e)[:40]}”" for e in epic_examples[:3])
            nm_rows.append(("Epic naming", f"{style} — {samples}"))
        template_sections = naming.get("template_sections", [])
        if template_sections:
            nm_rows.append(("Description template", " → ".join(f"“{s}”" for s, _ in template_sections[:5])))
        _add("naming", "Ticket Naming & Organisation", [_kv(nm_rows)], nav="Naming")

    # ── Story & Epic Structure ──────────────────────────────────────
    structure = ex.get("story_structure", {})
    if isinstance(structure, dict) and (structure.get("subtask_ordering") or structure.get("epic_completion")):
        st_rows: list[tuple[str, str | dict]] = []
        ordering = structure.get("subtask_ordering", [])
        if len(ordering) >= 2:
            st_rows.append(("Subtask sequence", " → ".join(str(s) for s in ordering)))
        skipped = structure.get("skipped_types", [])
        if skipped:
            st_rows.append(("Rarely created", " · ".join(f"{s['type']} ({s['present_pct']}%)" for s in skipped)))
        avg_completion = structure.get("avg_epic_completion", 0)
        if avg_completion > 0:
            st_rows.append(("Epic completion avg", f"{avg_completion}%"))
        for epic in structure.get("lingering_epics", [])[:3]:
            st_rows.append(
                (str(epic.get("epic_title", "?")), f"{epic['completed']}/{epic['total']} done ({epic['rate']}%)")
            )
        for epic in structure.get("epic_sprint_spread", [])[:3]:
            st_rows.append((str(epic.get("epic", "?")), f"{epic['stories']} stories across {epic['sprints']} sprints"))
        if st_rows:
            _add("structure", "Story & Epic Structure", [_kv(st_rows)], nav="Structure")

    # ── Acceptance Criteria Patterns ────────────────────────────────
    ac_patterns = ex.get("ac_patterns", {})
    if isinstance(ac_patterns, dict) and ac_patterns.get("stories_with_ac_pct") is not None:
        ac_pct = ac_patterns.get("stories_with_ac_pct", 0)
        ac_rows: list[tuple[str, str | dict]] = [("Stories with ACs", f"{ac_pct}%")]
        blocks = []
        if ac_pct == 0:
            blocks.append(_kv(ac_rows))
            blocks.append(_note("No acceptance criteria detected. ACs help define done and reduce ambiguity."))
        else:
            specificity = ac_patterns.get("specificity", {})
            ac_rows += [
                ("Median ACs/story", str(ac_patterns.get("median_ac", 0))),
                (
                    "Specificity",
                    f"{specificity.get('label', '?')} ({specificity.get('precise_pct', 0)}% precise)",
                ),
            ]
            by_discipline = ac_patterns.get("by_discipline", {})
            if len(by_discipline) >= 2:
                ac_rows.append(
                    (
                        "By discipline",
                        " · ".join(f"{d} {v['avg_ac']:.0f} avg" for d, v in by_discipline.items()),
                    )
                )
            spill = ac_patterns.get("spillover_correlation", {})
            low_ac = spill.get("low_ac_spill_pct", 0)
            high_ac = spill.get("high_ac_spill_pct", 0)
            if low_ac > high_ac + 5 and spill.get("low_ac_count", 0) >= 5:
                ac_rows.append(("Spillover impact", f"0-1 ACs: {low_ac}% spill vs 3+ ACs: {high_ac}% spill"))
            blocks.append(_kv(ac_rows))

            themes = ac_patterns.get("themes", {})
            theme_examples = ac_patterns.get("theme_examples", {})
            if themes:
                # Its own list rather than `<br>`-joined rich text crammed into
                # one key/value cell — each topic carries a linked example.
                items = []
                for theme, pct in list(themes.items())[:5]:
                    runs: list[dict] = [{"s": str(theme), "strong": True}, {"s": f" {pct}%"}]
                    example = theme_examples.get(theme)
                    if isinstance(example, dict) and example.get("issue_key"):
                        key_run = {"s": str(example["issue_key"])}
                        if url := safe_url(str(example.get("issue_url", "") or "")):
                            key_run["href"] = url
                        runs += [{"s": " "}, key_run, {"s": f" {str(example.get('summary', ''))[:30]}", "em": True}]
                    items.append(runs)
                blocks.append(_bullets(items, title="Topics"))
        _add("ac-patterns", "Acceptance Criteria Patterns", blocks, nav="ACs")

    # ── Epic Sizing ─────────────────────────────────────────────────
    epic = profile.epic_pattern
    if epic.sample_count > 0:
        low, high = epic.typical_story_count_range
        epic_rows: list[tuple[str, str | dict]] = [
            ("Avg stories/epic", f"{epic.avg_stories_per_epic:.0f}"),
            ("Avg points/epic", f"{epic.avg_points_per_epic:.0f}"),
        ]
        if low > 0 or high > 0:
            epic_rows.append(("Story count range", f"{low}–{high}"))
        _add("epics", "Epic Sizing", [_kv(epic_rows)])

    # ── Point Descriptions (LLM-generated) ──────────────────────────
    point_descriptions = ex.get("point_descriptions", {})
    if isinstance(point_descriptions, dict) and point_descriptions:
        _add(
            "point-descriptions",
            "What Each Point Value Means (LLM Interpretation)",
            [
                _table(
                    ["Points", "What it means for this team"],
                    [
                        [f"{key} pt", str(point_descriptions[key])]
                        for key in sorted(point_descriptions, key=lambda k: int(k) if k.isdigit() else 99)
                    ],
                )
            ],
            nav="Point Descriptions",
        )

    # ── Estimation Accuracy ─────────────────────────────────────────
    additional = ex.get("additional_patterns", {})
    estimation_bias = additional.get("estimation_bias", {}) if isinstance(additional, dict) else {}
    if isinstance(estimation_bias, dict) and estimation_bias.get("sample_size", 0) >= 5:
        eb_rows: list[tuple[str, str | dict]] = [
            ("Accurate (at original estimate)", f"{estimation_bias.get('accurate_pct', 0):.0f}%"),
            ("Underestimated (points increased)", f"{estimation_bias.get('underestimated_pct', 0):.0f}%"),
            ("Overestimated (points decreased)", f"{estimation_bias.get('overestimated_pct', 0):.0f}%"),
        ]
        worst = estimation_bias.get("worst_overestimate_sizes", [])
        if worst:
            eb_rows.append(("Most overestimated sizes", ", ".join(f"{s}pt" for s in worst)))
        _add("estimation", "Estimation Accuracy", [_kv(eb_rows)], nav="Estimation")

    # ── Seasonal Patterns ───────────────────────────────────────────
    seasonal = additional.get("seasonal", {}) if isinstance(additional, dict) else {}
    if isinstance(seasonal, dict) and seasonal.get("monthly_avg"):
        s_rows: list[tuple[str, str | dict]] = [(m, f"{v:g} pts") for m, v in seasonal["monthly_avg"].items()]
        for month, value in seasonal.get("low_months", {}).items():
            s_rows.append((f"↓ {month} (low)", _cell(f"{value:g} pts", tone="warn")))
        for month, value in seasonal.get("high_months", {}).items():
            s_rows.append((f"↑ {month} (high)", _cell(f"{value:g} pts", tone="ok")))
        _add("seasonal", "Seasonal Patterns", [_kv(s_rows)], nav="Seasonal")

    # ── Workflow ────────────────────────────────────────────────────
    workflow = ex.get("workflow_style", {})
    if isinstance(workflow, dict) and workflow.get("workflow"):
        wf_rows: list[tuple[str, str | dict]] = [
            ("Workflow", " → ".join(str(step) for step in workflow["workflow"])),
            (
                "Style",
                {"columns-as-dod": "Columns as DoD steps", "minimal": "Minimal workflow"}.get(
                    workflow.get("style", "minimal"), str(workflow.get("style", "minimal"))
                ),
            ),
        ]
        for column, rate in workflow.get("dod_columns", {}).items():
            wf_rows.append((f"{column} pass-through", f"{rate}%"))
        _add("workflow", "Board Workflow", [_kv(wf_rows)], nav="Workflow")

    # ── Recommendations (all 13 types, matching TUI) ────────────────
    recommendations = _recommendations(profile, ex, vel=vel, std=std, cals=cals)
    if recommendations:
        _add(
            "recommendations",
            "Recommendations",
            [_callout("warn", title, detail) for title, detail in recommendations],
            nav="Recs",
        )

    generated = datetime.now()
    return export_page(
        mode="analysis",
        title=f"Team Profile — {profile.project_key}",
        wordmark="team",
        subtitle=f"{profile.source}/{profile.project_key}",
        facts=[
            ("SPRINTS", str(profile.sample_sprints)),
            ("STORIES", str(profile.sample_stories)),
            ("GENERATED", generated.strftime("%Y-%m-%d %H:%M")),
        ],
        badges=[", ".join(sprint_names)] if sprint_names else [],
        nav=nav_links,
        footer=f"Generated by yeaboi.ai • {generated.strftime('%Y-%m-%d')}",
        markdown_name=markdown_name,
        report={"kind": "profile", "coverage": coverage, "sections": sections},
    )


def _scope_timeline_blocks(timeline) -> list[dict]:
    """One sprint's scope timeline: its totals, its day-by-day series, its events."""
    delta = timeline.scope_change_total
    pct = round(delta / timeline.committed_pts * 100) if timeline.committed_pts else 0
    tone = "ok" if delta == 0 else ("warn" if abs(delta) < 5 else "danger")
    snapshots = timeline.daily_snapshots
    first_count = len(snapshots[0].stories_in_sprint) if snapshots else 0
    last_count = len(snapshots[-1].stories_in_sprint) if snapshots else 0

    blocks: list[dict] = [
        _kv(
            [
                ("Scope change", _cell(f"{delta:+g} pts ({pct:+d}%)", tone=tone)),
                ("Committed", f"{timeline.committed_pts:g} pts ({first_count} stories)"),
                ("Final", f"{timeline.final_pts:g} pts ({last_count} stories)"),
                ("Delivered", f"{timeline.delivered_pts:g} pts"),
            ],
            title=str(timeline.sprint_name),
        )
    ]
    if len(snapshots) >= 2:
        blocks.append(
            {
                "kind": "trend",
                "trend": {
                    "title": f"{timeline.sprint_name}: scope per day",
                    "label": f"{timeline.sprint_name}: scope points per day",
                    "points": [[s.date, s.total_scope_pts] for s in snapshots],
                },
            }
        )
    if timeline.change_events:
        blocks.append(
            _table(
                ["Δ pts", "Issue", "Change", "Summary"],
                [
                    [
                        _cell(
                            f"{event.delta_pts:+g}",
                            tone="ok" if event.delta_pts < 0 else ("warn" if abs(event.delta_pts) <= 3 else "danger"),
                        ),
                        str(event.issue_key),
                        event.change_type.replace("re_estimated_", "re-est ").replace("_", " "),
                        str(event.summary or "")[:45],
                    ]
                    for event in timeline.change_events[:5]
                ],
                numeric=(0,),
            )
        )
        if len(timeline.change_events) > 5:
            blocks.append(
                _note(f"… and {len(timeline.change_events) - 5} more scope changes in {timeline.sprint_name}.")
            )
    return blocks


def _recommendations(
    profile: TeamProfile, ex: Mapping, *, vel: float, std: float, cals: Sequence
) -> list[tuple[str, str]]:
    """The 13 recommendation types, as ``(title, detail)`` pairs.

    Lifted out of the page builder because it is the one part of it that is
    pure analysis rather than presentation — it reads the profile and decides
    what is worth saying, and it does not care what draws the result.
    """
    recs: list[tuple[str, str]] = []
    if vel > 0 and std / vel * 100 > 35:
        recs.append(
            (
                "High velocity variance",
                f"Velocity swings ±{std / vel * 100:.0f}% sprint-to-sprint. "
                "Consider smaller stories or stricter sprint commitments.",
            )
        )
    if 0 < profile.sprint_completion_rate < 60:
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
    recurring_count = ex.get("recurring_count", 0)
    delivery_count = ex.get("delivery_count", 0)
    if isinstance(recurring_count, int) and isinstance(delivery_count, int):
        total = recurring_count + delivery_count
        if total > 0 and recurring_count / total > 0.3:
            recs.append(
                (
                    "High recurring overhead",
                    f"{recurring_count} of {total} tickets ({recurring_count / total * 100:.0f}%) "
                    "are recurring. Consider consolidating or timeboxing.",
                )
            )
    contributors = ex.get("contributor_stats", [])
    if isinstance(contributors, list) and contributors:
        per_sprint = [c.get("per_sprint", 0) for c in contributors if c.get("per_sprint", 0) > 0]
        if per_sprint:
            average = round(sum(per_sprint) / len(per_sprint), 1)
            if average < 3:
                recs.append(
                    (
                        "Low per-developer output",
                        f"Contributors average {average} pts/sprint. "
                        "Check for blockers, context-switching, or oversized stories.",
                    )
                )
    repos = ex.get("repositories", {})
    if isinstance(repos, dict):
        for sr in repos.get("spillover_repos", []):
            if isinstance(sr, dict) and sr.get("spill_rate", 0) >= 40:
                recs.append(
                    (
                        f"{sr['repo']} has high spillover",
                        f"{sr['spill_rate']}% of stories touching {sr['repo']} don't complete the sprint.",
                    )
                )
    shadow = ex.get("shadow_spillover", [])
    if isinstance(shadow, list) and len(shadow) >= 2:
        recs.append(
            (
                "Shadow spillover",
                f"{len(shadow)} stories were closed then re-created in the next sprint. "
                "Consider keeping the original ticket open instead of cloning.",
            )
        )
    task_decomp = ex.get("task_decomposition", {})
    if isinstance(task_decomp, dict):
        if task_decomp.get("task_completion_rate", 100) < 60:
            recs.append(
                ("Low task completion", f"Only {task_decomp['task_completion_rate']}% of sub-tasks are completed.")
            )
        for category, rate, count in task_decomp.get("bottlenecks", []):
            recs.append((f"{category} bottleneck", f"{category} tasks have only {rate}% completion ({count} tasks)."))
        with_tasks = task_decomp.get("stories_with_tasks", 0)
        total_stories = task_decomp.get("total_stories", 0)
        if total_stories > 10 and with_tasks > 0 and with_tasks / total_stories < 0.3:
            recs.append(
                (
                    "Low task breakdown",
                    f"Only {with_tasks} of {total_stories} stories "
                    f"({with_tasks / total_stories * 100:.0f}%) have sub-tasks.",
                )
            )

    scope = ex.get("scope_changes", {})
    if isinstance(scope, dict) and scope.get("totals"):
        totals = scope["totals"]
        total_stories = totals.get("total_stories", 0)
        committed = totals.get("avg_committed_velocity", 0.0)
        delivered = totals.get("avg_delivered_velocity", 0.0)
        if committed > 0 and delivered / committed < 0.7:
            recs.append(
                (
                    "Low delivery accuracy",
                    f"Team delivers only {round(delivered / committed * 100)}% of committed scope "
                    f"({delivered} of {committed} pts avg). "
                    "Reduce sprint commitments to match actual capacity.",
                )
            )
        if total_stories > 0:
            added = totals.get("added_mid_sprint", 0)
            re_estimated = totals.get("re_estimated", 0)
            if added / total_stories > 0.15:
                recs.append(
                    (
                        "High mid-sprint scope additions",
                        f"{added} of {total_stories} stories ({added / total_stories * 100:.0f}%) "
                        "were added after the sprint started. "
                        "Protect sprint commitments by locking scope after planning.",
                    )
                )
            if re_estimated / total_stories > 0.15:
                recs.append(
                    (
                        "Frequent re-estimation",
                        f"{re_estimated} of {total_stories} stories "
                        f"({re_estimated / total_stories * 100:.0f}%) had their points changed mid-sprint. "
                        "Improve estimation accuracy with team calibration sessions.",
                    )
                )
        churned = [s for s in scope.get("per_sprint", []) if s.get("scope_churn", 0) > 0.3]
        if len(churned) >= 2:
            names = ", ".join(s.get("name", "?") for s in churned[:3])
            recs.append(
                (
                    "High scope churn",
                    f"{len(churned)} sprints had >30% scope churn ({names}). "
                    "Scope is volatile — enforce a sprint lock after planning.",
                )
            )
        chains = scope.get("carry_over_chains", [])
        if len(chains) >= 3:
            recs.append(
                (
                    "Carry-over chains",
                    f"{len(chains)} stories bounced across 3+ sprints. These are zombie stories — split or kill them.",
                )
            )

    ac_patterns = ex.get("ac_patterns", {})
    if isinstance(ac_patterns, dict) and ac_patterns.get("recommendation"):
        recs.append(("Acceptance criteria gaps", str(ac_patterns["recommendation"])))

    proposed = ex.get("proposed_dod", {})
    if isinstance(proposed, dict) and proposed.get("health") == "weak":
        missing = [i["practice"] for i in proposed.get("items", []) if i.get("status") == "missing"]
        recs.append(
            (
                "No consistent Definition of Done",
                f"No consistent DoD found. {', '.join(missing[:3])} show no evidence. "
                "Create a team DoD checklist to improve quality.",
            )
        )
    elif isinstance(proposed, dict) and proposed.get("health") == "moderate":
        emerging = [i["practice"] for i in proposed.get("items", []) if i.get("status") == "emerging"]
        if emerging:
            recs.append(
                (
                    "Create a formal Definition of Done",
                    f"{', '.join(emerging[:3])} are practiced inconsistently. "
                    "Write a shared DoD checklist and enforce it on every story.",
                )
            )
    return recs


def export_team_profile_html(
    profile: TeamProfile,
    output_dir: Path | None = None,
    *,
    examples: dict | None = None,
    sprint_names: list[str] | None = None,
    ceremony=None,
    markdown_name: str = "",
) -> Path:
    """Write the self-contained team-profile HTML report and return its path.

    ``markdown_name`` names the sibling Markdown file, which the page points at
    for anyone who opens it with scripting off. It is a parameter rather than
    something derived here because the two artifacts are written by separate
    calls with separate timestamps — guessing the name would produce a link to
    a file that is one second wrong.
    """
    out_dir = _project_export_dir(profile.project_key, output_dir)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"team-profile-{ts}.html"
    page = build_team_profile_html(
        profile,
        examples=examples,
        sprint_names=sprint_names,
        ceremony=ceremony,
        charts_dir=out_dir,
        markdown_name=markdown_name,
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
        lines.extend(["## AI Usage", ""])
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
        p_members, p_team, p_min, p_file_data = _practice_rows(ai_blob)
        if p_members:
            lines.extend(["### Engineering practices by member", ""])
            lines.append("| Member | Commits | PRs | Tests | Docs | Tickets | Descriptions |")
            lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
            for row in p_members + ([p_team] if p_team else []):
                lines.append(
                    f"| {row.get('member', '')} | {row.get('commits', 0)} | {row.get('prs', 0)} | "
                    f"{_practice_cell_text(row, 'tests', p_min)} | {_practice_cell_text(row, 'docs', p_min)} | "
                    f"{_practice_cell_text(row, 'ticket', p_min)} | {_practice_cell_text(row, 'desc', p_min)} |"
                )
            lines.append("")
            if p_file_data.get("total") and p_file_data.get("with_file_data", 0) < p_file_data["total"]:
                lines.append(
                    f"_File-based columns (Tests, Docs) cover {p_file_data.get('with_file_data', 0)} "
                    f"of {p_file_data['total']} items with change metadata._"
                )
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
        sections.extend(["", "AI Usage (footprint is a lower bound — commit/PR markers only):"])
        _p_members, p_team, p_min, _p_file_data = _practice_rows(ai_blob)
        if p_team:
            sections.append(
                "  Practices (team): "
                f"tests {_practice_cell_text(p_team, 'tests', p_min)} · "
                f"docs {_practice_cell_text(p_team, 'docs', p_min)} · "
                f"tickets {_practice_cell_text(p_team, 'ticket', p_min)} · "
                f"descriptions {_practice_cell_text(p_team, 'desc', p_min)}"
            )
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
        log_repos = list(getattr(ai_sig, "repos_scanned", ()) or ())
        for repo in log_repos[:5]:
            sections.append(f"  Scanned: {repo}")
        if len(log_repos) > 5:
            sections.append(f"  Scanned: +{len(log_repos) - 5} more repositories")
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
            sections.append(f"  Examples ({len(ai_samples)}):")
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
