"""Self-contained HTML export for Scrum plan artifacts.

Generates a single-file HTML report (no external dependencies) from whatever
artifacts are available in graph_state — works at any pipeline checkpoint.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime
from pathlib import Path

from yeaboi.html_theme import EXPORT_CSS, html_page

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CSS — the shared design system (html_theme), embedded once in <head>
# ---------------------------------------------------------------------------

# Kept under the old name so existing `from yeaboi.html_exporter import _CSS`
# call sites keep working; the stylesheet itself now lives in html_theme.
_CSS = EXPORT_CSS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIORITY_BADGE = {
    "critical": "badge-critical",
    "high": "badge-high",
    "medium": "badge-medium",
    "low": "badge-low",
}

_DISCIPLINE_CLASS = {
    "fullstack": "disc-fullstack",
    "frontend": "disc-frontend",
    "backend": "disc-backend",
    "qa": "disc-qa",
    "devops": "disc-devops",
    "design": "disc-design",
}


def _e(text: str) -> str:
    """HTML-escape a string."""
    return html.escape(str(text), quote=True)


# Matches the export-image cap in export_targets._MAX_IMAGE_BYTES — anything
# bigger would bloat the self-contained HTML beyond usefulness.
_MAX_EMBED_BYTES = 5 * 1024 * 1024


def img_b64_tag(path: str | Path, alt: str = "") -> str:
    """Return an <img> tag with the file base64-embedded as a data: URI.

    Keeps exported HTML self-contained/offline (screenshots in ~/.yeaboi may be
    pruned later). Best-effort: missing/oversized/unreadable files return ''.
    """
    import base64
    import mimetypes

    p = Path(path)
    try:
        if not p.is_file() or p.stat().st_size > _MAX_EMBED_BYTES:
            logging.getLogger(__name__).warning("Skipping HTML image embed (missing or too large): %s", p)
            return ""
        mime = mimetypes.guess_type(p.name)[0] or "image/png"
        data = base64.b64encode(p.read_bytes()).decode("ascii")
        return f'<img src="data:{mime};base64,{data}" alt="{_e(alt)}" style="max-width:100%;border-radius:8px" />'
    except Exception as exc:  # noqa: BLE001 — embedding is best-effort decoration
        logging.getLogger(__name__).warning("Could not embed image %s: %s", p, exc)
        return ""


def _badge(label: str, extra_class: str = "badge-tag") -> str:
    return f'<span class="badge {_e(extra_class)}">{_e(label)}</span>'


def _priority_badge(priority: str) -> str:
    cls = _PRIORITY_BADGE.get(priority.lower(), "badge-tag")
    return _badge(priority.upper(), cls)


def _discipline_badge(discipline: str) -> str:
    cls = _DISCIPLINE_CLASS.get(discipline.lower(), "badge-tag")
    return _badge(discipline.capitalize(), cls)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_questionnaire_section(graph_state: dict) -> str:
    """Render questionnaire answers as an HTML section."""
    from yeaboi.prompts.intake import INTAKE_QUESTIONS

    qs = graph_state.get("questionnaire")
    if qs is None or not qs.answers:
        return ""

    rows: list[str] = []
    for q_num in sorted(qs.answers.keys()):
        q_text = INTAKE_QUESTIONS.get(q_num, f"Question {q_num}")
        answer = qs.answers[q_num]
        rows.append(f"<tr><td>Q{q_num}</td><td>{_e(q_text)}</td><td>{_e(str(answer))}</td></tr>")

    if not rows:
        return ""

    return f"""
<section id="questionnaire">
  <h2>Intake Questionnaire</h2>
  <div class="card" style="padding:0;overflow:hidden;">
    <table class="q-table">
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</section>
"""


def _build_capacity_block(graph_state: dict, analysis) -> str:
    """Render capacity/velocity breakdown as an HTML block within the analysis section."""
    team_size = graph_state.get("team_size", 0)
    velocity = graph_state.get("velocity_per_sprint", 0)
    net_velocity = graph_state.get("net_velocity_per_sprint", 0)
    if not team_size or not velocity:
        return ""

    target_sprints = analysis.target_sprints if analysis else 0
    sprint_weeks = graph_state.get("sprint_length_weeks", 2)
    if not target_sprints:
        return ""

    bank_holidays = graph_state.get("capacity_bank_holiday_days", 0)
    planned_leave = graph_state.get("capacity_planned_leave_days", 0)
    unplanned_pct = graph_state.get("capacity_unplanned_leave_pct", 0)
    onboarding = graph_state.get("capacity_onboarding_engineer_sprints", 0)
    ktlo = graph_state.get("capacity_ktlo_engineers", 0)
    discovery_pct = graph_state.get("capacity_discovery_pct", 5)

    rows = [f"<li>Team: {team_size} engineer(s), {sprint_weeks}-week sprints &times; {target_sprints}</li>"]
    rows.append(f"<li>Gross velocity: {velocity} pts/sprint</li>")

    deductions: list[str] = []
    if bank_holidays > 0:
        deductions.append(f"bank holidays: {bank_holidays}d")
    if planned_leave > 0:
        deductions.append(f"planned leave: {planned_leave}d")
    if unplanned_pct > 0:
        deductions.append(f"unplanned: {unplanned_pct}%")
    if onboarding > 0:
        deductions.append(f"onboarding: {onboarding} eng-sprint(s)")
    if ktlo > 0:
        deductions.append(f"KTLO: {ktlo} eng")
    if discovery_pct > 0:
        deductions.append(f"discovery: {discovery_pct}%")

    if deductions:
        rows.append(f"<li>Deductions: {', '.join(deductions)}</li>")

    rows.append(f"<li><strong>Net velocity: {net_velocity} pts/sprint</strong></li>")

    return f"""
  <div class="analysis-section">
    <h3>Capacity</h3>
    <ul>{"".join(rows)}</ul>
  </div>"""


def _build_analysis_section(graph_state: dict) -> str:
    """Render ProjectAnalysis as an HTML section."""
    analysis = graph_state.get("project_analysis")
    if not analysis:
        return ""

    def _field_block(label: str, items: tuple) -> str:
        if not items:
            return ""
        lis = "".join(f'<li class="{"assumption-item" if label == "Assumptions" else ""}">{_e(i)}</li>' for i in items)
        return f'<div class="analysis-section"><h3>{_e(label)}</h3><ul>{lis}</ul></div>'

    blocks = [
        _field_block("Goals", analysis.goals),
        _field_block("End Users", analysis.end_users),
        _field_block("Tech Stack", analysis.tech_stack),
        _field_block("Integrations", getattr(analysis, "integrations", ())),
        _field_block("Constraints", analysis.constraints),
        _field_block("Risks", analysis.risks),
        _field_block("Out of Scope", analysis.out_of_scope),
        _field_block("Assumptions", analysis.assumptions),
    ]
    grid = f'<div class="analysis-grid">{"".join(b for b in blocks if b)}</div>'

    sprint_info = (
        f"{analysis.sprint_length_weeks}-week sprints &times; {analysis.target_sprints} sprints "
        f"= ~{analysis.sprint_length_weeks * analysis.target_sprints} weeks total"
    )

    meta_badges = "".join(
        [
            _badge(analysis.project_type.capitalize(), "badge-tag"),
            _badge(sprint_info, "badge-tag"),
        ]
    )

    capacity_html = _build_capacity_block(graph_state, analysis)

    return f"""
<section id="analysis">
  <h2>Project Analysis</h2>
  <div class="card">
    <div class="card-header">
      <div>
        <div class="card-title">{_e(analysis.project_name)}</div>
        <div class="card-desc">{_e(analysis.project_description)}</div>
      </div>
    </div>
    <div class="card-meta">{meta_badges}</div>
    <div class="card-desc" style="margin-top:0.75rem;"><strong>Target state:</strong> {_e(analysis.target_state)}</div>
  </div>
  {grid}
  {capacity_html}
</section>
"""


def _build_epic_section(graph_state: dict) -> str:
    """Render the project-level epic as an HTML section."""
    analysis = graph_state.get("project_analysis")
    if not analysis:
        return ""
    epic_key = graph_state.get("jira_epic_key", "") or graph_state.get("azdevops_epic_id", "")
    key_badge = f' <span class="badge badge-tag">{_e(epic_key)}</span>' if epic_key else ""
    return f"""
<section id="epic">
  <h2>Epic</h2>
  <div class="card">
    <div class="card-header">
      <span class="card-title">{_e(analysis.project_name)}</span>{key_badge}
    </div>
    <div class="card-desc">{_e(analysis.project_description)}</div>
    <div class="card-desc" style="margin-top:0.5rem;"><strong>Target state:</strong> {_e(analysis.target_state)}</div>
  </div>
</section>
"""


def _build_features_section(graph_state: dict) -> str:
    """Render feature list as an HTML section."""
    features = graph_state.get("features", [])
    if not features:
        return ""

    cards = []
    for feature in features:
        priority = feature.priority.value if hasattr(feature.priority, "value") else str(feature.priority)
        cards.append(f"""
  <div class="card">
    <div class="card-header">
      <div>
        <span class="card-id">{_e(feature.id)}</span>
        <span class="card-title">{_e(feature.title)}</span>
      </div>
      {_priority_badge(priority)}
    </div>
    <div class="card-desc">{_e(feature.description)}</div>
  </div>""")

    return f"""
<section id="features">
  <h2>Features</h2>
  {"".join(cards)}
</section>
"""


def _build_stories_section(graph_state: dict) -> str:
    """Render user stories grouped by feature as an HTML section."""
    stories = graph_state.get("stories", [])
    if not stories:
        return ""

    features = graph_state.get("features", [])
    feature_titles = {e.id: e.title for e in features}

    # Group by feature
    by_feature: dict[str, list] = {}
    for story in stories:
        by_feature.setdefault(story.feature_id, []).append(story)

    sections: list[str] = []
    for feature_id, feature_stories in by_feature.items():
        feature_label = f"{feature_id}: {feature_titles.get(feature_id, feature_id)}"
        cards = []
        for story in feature_stories:
            priority = story.priority.value if hasattr(story.priority, "value") else str(story.priority)
            discipline = story.discipline.value if hasattr(story.discipline, "value") else str(story.discipline)
            pts = story.story_points.value if hasattr(story.story_points, "value") else int(story.story_points)

            ac_items = ""
            if story.acceptance_criteria:
                lis = "".join(
                    f"<li>"
                    f"<strong style='font-size:0.75rem;color:var(--text-muted);'>AC {i + 1}</strong> &mdash; "
                    f"<span class='ac-given'>Given</span> {_e(ac.given)} "
                    f"<span class='ac-when'>When</span> {_e(ac.when)} "
                    f"<span class='ac-then'>Then</span> {_e(ac.then)}"
                    f"</li>"
                    for i, ac in enumerate(story.acceptance_criteria)
                )
                ac_items = f'<ul class="ac-list">{lis}</ul>'

            rationale_html = ""
            if story.points_rationale:
                confidence = getattr(story, "points_confidence", "")
                conf_badge = ""
                if confidence:
                    conf_color = {"high": "var(--ok)", "medium": "var(--warn)", "low": "var(--danger)"}.get(
                        confidence.lower(), "var(--text-muted)"
                    )
                    conf_badge = (
                        f' <span style="font-size:0.75rem;color:{conf_color};">[{_e(confidence)} confidence]</span>'
                    )
                rationale_html = (
                    f'<div class="card-desc" style="margin-top:0.5rem;font-style:italic;">'
                    f"<strong>Points rationale:</strong> {_e(story.points_rationale)}{conf_badge}</div>"
                )

            # User story description ("As a X, I want to Y, so that Z")
            desc_html = (
                f'<div class="card-desc" style="margin-top:0.5rem;font-style:italic;color:var(--text-muted);">'
                f"{_e(story.text)}</div>"
            )

            # Definition of Done flags — use team-specific DoD when available
            from yeaboi.agent.state import resolve_dod_items

            dod_html = ""
            dod_items = resolve_dod_items(graph_state)
            dod_flags = story.dod_applicable
            if len(dod_flags) == len(dod_items):
                dod_items_html = "".join(
                    f'<li style="{"text-decoration:line-through;opacity:0.5;" if not applicable else ""}">'
                    f"{'✓' if applicable else '✗'} {_e(item)}</li>"
                    for item, applicable in zip(dod_items, dod_flags)
                )
                dod_html = (
                    f'<div style="margin-top:0.5rem;">'
                    f'<strong style="font-size:0.75rem;color:var(--text-muted);">Definition of Done:</strong>'
                    f'<ul class="ac-list" style="font-size:0.8rem;">{dod_items_html}</ul></div>'
                )

            cards.append(f"""
    <div class="card story-card {_e(priority)}">
      <div class="card-header">
        <div>
          <span class="card-id">{_e(story.id)}</span>
          <span class="card-title">{_e(story.title or story.text)}</span>
        </div>
        {_priority_badge(priority)}
      </div>
      {desc_html}
      <div class="card-meta">
        {_badge(f"{pts} pts", "badge-pts")}
        {_discipline_badge(discipline)}
      </div>
      {rationale_html}
      {ac_items}
      {dod_html}
    </div>""")

        label_style = (
            "font-size:0.8rem;font-weight:600;color:var(--text-muted);"
            "text-transform:uppercase;letter-spacing:0.05em;margin-bottom:0.5rem;"
        )
        sections.append(f"""
  <div style="margin-bottom:1.5rem;">
    <div style="{label_style}">{_e(feature_label)}</div>
    {"".join(cards)}
  </div>""")

    return f"""
<section id="stories">
  <h2>User Stories</h2>
  {"".join(sections)}
</section>
"""


def _build_tasks_section(graph_state: dict) -> str:
    """Render tasks grouped by story as an HTML section."""
    tasks = graph_state.get("tasks", [])
    if not tasks:
        return ""

    stories = graph_state.get("stories", [])
    story_text = {s.id: s.text for s in stories}

    # Group tasks by story
    by_story: dict[str, list] = {}
    for task in tasks:
        by_story.setdefault(task.story_id, []).append(task)

    rows: list[str] = []
    for story_id, story_tasks in by_story.items():
        label = story_text.get(story_id, story_id)
        hdr_style = (
            "background:var(--bg);font-weight:600;font-size:0.78rem;color:var(--text-muted);padding:0.4rem 0.75rem;"
        )
        rows.append(f"""
    <tr>
      <td colspan="4" style="{hdr_style}">
        {_e(story_id)} — {_e(label)}
      </td>
    </tr>""")
        for task in story_tasks:
            label_val = task.label.value if hasattr(task.label, "value") else str(task.label)
            desc_parts = [_e(task.description)]
            if task.test_plan:
                desc_parts.append(f"<br><strong>Test plan:</strong> {_e(task.test_plan)}")
            if task.ai_prompt:
                desc_parts.append(f"<br><strong>AI prompt:</strong> {_e(task.ai_prompt)}")
            rows.append(f"""
    <tr>
      <td class="mono">{_e(task.id)}</td>
      <td>{_badge(label_val, "badge-tag")}</td>
      <td style="font-weight:500;">{_e(task.title)}</td>
      <td style="color:var(--text-muted);">{"".join(desc_parts)}</td>
    </tr>""")

    return f"""
<section id="tasks">
  <h2>Tasks</h2>
  <div class="card" style="padding:0;overflow:hidden;">
    <table class="data-table">
      <thead><tr><th>ID</th><th>Label</th><th>Title</th><th>Description</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>
</section>
"""


def _build_sprints_section(graph_state: dict) -> str:
    """Render sprint plan as an HTML section."""
    sprints = graph_state.get("sprints", [])
    if not sprints:
        return ""

    velocity = graph_state.get("velocity_per_sprint", 10)
    stories = graph_state.get("stories", [])
    story_pts = {
        s.id: s.story_points.value if hasattr(s.story_points, "value") else int(s.story_points) for s in stories
    }

    cards: list[str] = []
    for sprint in sprints:
        used = sum(story_pts.get(sid, 0) for sid in sprint.story_ids)
        capacity = sprint.capacity_points
        fill_pct = min(int(used / capacity * 100), 100) if capacity else 0
        fill_color = "var(--danger)" if fill_pct > 100 else "var(--warn)" if fill_pct > 80 else "var(--accent)"

        # Show reduced capacity annotation when sprint has deductions
        deduction_note = ""
        if capacity < velocity:
            deduction_note = (
                f'<div style="font-size:0.75rem;color:var(--warn);margin-top:0.25rem;">'
                f"Reduced from {velocity} pts (bank holidays / deductions)</div>"
            )

        story_chips = "".join(f'<span class="badge badge-tag">{_e(sid)}</span>' for sid in sprint.story_ids)

        cards.append(f"""
  <div class="card sprint-card"{' style="border-color:var(--warn);"' if capacity < velocity else ""}>
    <div class="sprint-header">
      <span class="card-title">{_e(sprint.name)}</span>
      <span class="badge badge-tag">{used} / {capacity} pts</span>
    </div>
    <div class="sprint-goal">{_e(sprint.goal)}</div>
    <div class="capacity-bar"><div class="capacity-fill" style="width:{fill_pct}%;background:{fill_color}"></div></div>
    {deduction_note}
    <div class="sprint-stories">{story_chips}</div>
  </div>""")

    sprint_count = len(sprints)
    total_pts = sum(story_pts.get(sid, 0) for sprint in sprints for sid in sprint.story_ids)

    return f"""
<section id="sprints">
  <h2>Sprint Plan</h2>
  <p style="color:var(--text-muted);font-size:0.85rem;margin-bottom:1rem;">
    {sprint_count} sprint{"s" if sprint_count != 1 else ""} &bull;
    {total_pts} total story points &bull;
    {velocity} pts/sprint velocity
  </p>
  {"".join(cards)}
</section>
"""


# ---------------------------------------------------------------------------
# Nav / header helpers
# ---------------------------------------------------------------------------


def _build_attachments_section(graph_state: dict) -> str:
    """Screenshots pasted into the session, base64-embedded so the file stays offline."""
    seen: list[str] = []
    for key in ("pasted_images", "review_feedback_images", "chat_images"):
        for p in graph_state.get(key) or []:
            if p not in seen:
                seen.append(p)
    tags = "".join(img_b64_tag(p, f"Screenshot {i}") for i, p in enumerate(seen, start=1))
    if not tags:
        return ""
    return f'<section id="attachments"><h2>Attachments</h2>{tags}</section>'


def _nav_links(graph_state: dict) -> list[tuple[str, str]]:
    """(section id, label) pairs for the sticky top-nav, one per available section."""
    links: list[tuple[str, str]] = []
    qs = graph_state.get("questionnaire")
    if qs and qs.answers:
        links.append(("questionnaire", "Questionnaire"))
    if graph_state.get("project_analysis"):
        links.append(("analysis", "Analysis"))
    if graph_state.get("features"):
        links.append(("features", "Features"))
    if graph_state.get("stories"):
        links.append(("stories", "Stories"))
    if graph_state.get("tasks"):
        links.append(("tasks", "Tasks"))
    if graph_state.get("sprints"):
        links.append(("sprints", "Sprint Plan"))
    return links


def _header_meta(graph_state: dict, stage_label: str) -> tuple[list[str], list[str]]:
    """(meta lines, stage badges) for the page header."""
    now = datetime.now().strftime("%B %d, %Y %H:%M")
    meta = [f"Exported: {now}", f"Stage: {stage_label}"]

    # Analysis profile provenance
    profile_id = graph_state.get("analysis_profile_id", "")
    if profile_id:
        display_name = profile_id.split("-", 1)[1] if "-" in profile_id else profile_id
        source = profile_id.split("-", 1)[0] if "-" in profile_id else ""
        meta.append(f"Calibrated with: {display_name}{f' ({source})' if source else ''}")

    badges = [
        label
        for key, label in [
            ("project_analysis", "Analysis"),
            ("features", "Features"),
            ("stories", "Stories"),
            ("tasks", "Tasks"),
            ("sprints", "Sprints"),
        ]
        if graph_state.get(key)
    ]
    return meta, badges


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

#: Human-readable label for each pipeline node
_STAGE_LABELS: dict[str, str] = {
    "project_analyzer": "Project Analysis",
    "feature_generator": "Features",
    "story_writer": "User Stories",
    "task_decomposer": "Tasks",
    "sprint_planner": "Sprint Plan",
    "questionnaire": "Questionnaire",
    "complete": "Complete Plan",
}


def build_export_html(graph_state: dict, stage: str = "complete") -> str:
    """Build a self-contained HTML report from available graph state artifacts.

    Works at any pipeline checkpoint — sections for missing artifacts are
    simply omitted. The ``stage`` parameter sets the label in the header.

    Args:
        graph_state: The current graph state dict (partial or complete).
        stage: The pipeline stage label (e.g. "project_analyzer").

    Returns:
        A complete self-contained HTML string.
    """
    stage_label = _STAGE_LABELS.get(stage, stage.replace("_", " ").title())
    analysis = graph_state.get("project_analysis")
    title = f"{analysis.project_name} — Scrum Plan" if analysis else "Scrum Plan"

    sections = [
        _build_questionnaire_section(graph_state),
        _build_analysis_section(graph_state),
        _build_epic_section(graph_state),
        _build_features_section(graph_state),
        _build_stories_section(graph_state),
        _build_tasks_section(graph_state),
        _build_sprints_section(graph_state),
        _build_attachments_section(graph_state),
    ]
    body_content = "".join(s for s in sections if s)
    if not body_content:
        body_content = '<p style="color:var(--text-muted)">No artifacts to export yet.</p>'

    meta, badges = _header_meta(graph_state, stage_label)
    return html_page(
        title=title,
        heading=analysis.project_name if analysis else "Scrum Plan",
        body=body_content,
        meta=meta,
        badges=badges,
        nav=_nav_links(graph_state),
        footer_note=f"Generated by yeaboi.ai • {datetime.now().strftime('%Y-%m-%d')}",
    )


def export_plan_html(graph_state: dict, stage: str = "complete", path: Path | None = None) -> Path:
    """Write the HTML report to disk and return the path.

    Args:
        graph_state: The current graph state dict.
        stage: Pipeline stage label for the header.
        path: Optional output path. Defaults to ``scrum-plan.html`` in cwd.

    Returns:
        The path the file was written to.
    """
    output_path = path or Path("scrum-plan.html")
    output_path.write_text(build_export_html(graph_state, stage=stage), encoding="utf-8")
    sections = sum(
        1
        for k in ("questionnaire", "project_analysis", "features", "stories", "tasks", "sprints")
        if graph_state.get(k)
    )
    logger.info("HTML exported to %s (%d section(s), stage=%s)", output_path, sections, stage)
    return output_path
