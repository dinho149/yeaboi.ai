"""TUI-specific artifact renderers for the pipeline review screen.

# See docs: "Architecture" — these renderers produce clean text-block layouts
# for the scrollable viewport, as opposed to the REPL formatters which use
# Rich Tables and Panels with borders.
"""

from __future__ import annotations

import re

import rich.box
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from yeaboi.ui.session._utils import _render_to_lines

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DOD_SHORT = ("AC Met", "Docs", "Testing", "Code Merged", "SDLC", "Sign-off", "Know. Sharing")


# ---------------------------------------------------------------------------
# Priority styling helper
# ---------------------------------------------------------------------------


def _priority_color(priority) -> str:
    """Return a Rich style string for the given Priority enum value."""
    from yeaboi.agent.state import Priority

    return {
        Priority.CRITICAL: "bold red",
        Priority.HIGH: "yellow",
        Priority.MEDIUM: "rgb(70,100,180)",
        Priority.LOW: "dim",
    }.get(priority, "")


def _task_label_color(label: str) -> str:
    """Return a Rich style string for a task label value.

    Matches the colour scheme in formatters.py for consistency across REPL and TUI.
    """
    return {
        "Code": "bold cyan",
        "Documentation": "bold magenta",
        "Infrastructure": "bold yellow",
        "Testing": "bold green",
    }.get(label, "dim")


# ---------------------------------------------------------------------------
# Project analysis renderer
# ---------------------------------------------------------------------------


def _render_tui_analysis(
    analysis,
    *,
    sprint_capacities: list[dict] | None = None,
    net_velocity: int | None = None,
    velocity_per_sprint: int | None = None,
    team_size: int | None = None,
    velocity_source: str | None = None,
    team_override_from: int | None = None,
    context_sources: list[dict] | None = None,
) -> Group:
    """Render the ProjectAnalysis as a styled TUI text block.

    Shows the project name as a header, key–value fields in a clean layout,
    bullet lists for goals/tech/etc, capacity analysis, and assumptions
    highlighted in yellow.
    """
    parts: list = []

    # Project name header
    parts.append(Text(f"Project Analysis: {analysis.project_name}", style="bold rgb(70,100,180)"))
    parts.append(Text(""))

    # Key-value fields
    def _kv(label: str, value: str, value_style: str = "white") -> Text:
        t = Text()
        t.append(f"{label}: ", style="bold rgb(140,140,160)")
        t.append(value, style=value_style)
        return t

    parts.append(_kv("Description", analysis.project_description))
    parts.append(_kv("Type", analysis.project_type))
    parts.append(_kv("Target State", analysis.target_state))
    sprint_info = f"{analysis.sprint_length_weeks}-week sprints × {analysis.target_sprints} sprints"
    parts.append(_kv("Sprint Planning", sprint_info))

    # Prompt quality rating — deterministic score from questionnaire tracking sets.
    # Grade colour: green for A/B, yellow for C, red for D.
    if getattr(analysis, "prompt_quality", None):
        pq = analysis.prompt_quality
        grade_colour = (
            "rgb(80,200,80)" if pq.grade in ("A", "B") else ("rgb(200,180,60)" if pq.grade == "C" else "rgb(200,60,60)")
        )
        total = pq.answered_count + pq.extracted_count + pq.defaulted_count + pq.skipped_count
        quality_header = Text()
        quality_header.append("Input Quality: ", style="bold rgb(140,140,160)")
        quality_header.append(f"{pq.grade} ({pq.score_pct}%)", style=f"bold {grade_colour}")
        parts.append(quality_header)

        breakdown = Text()
        breakdown.append(f"  {total} questions: ", style="rgb(140,140,140)")
        breakdown.append(f"{pq.answered_count} answered", style="rgb(80,200,80)")
        breakdown.append(" · ", style="dim")
        breakdown.append(f"{pq.extracted_count} extracted", style="cyan")
        breakdown.append(" · ", style="dim")
        breakdown.append(f"{pq.defaulted_count} defaults", style="rgb(200,180,60)")
        breakdown.append(" · ", style="dim")
        breakdown.append(f"{pq.skipped_count} skipped", style="dim")
        parts.append(breakdown)

        if pq.suggestions:
            parts.append(Text("  Suggestions:", style="bold rgb(140,140,160)"))
            for suggestion in pq.suggestions:
                sug_text = Text()
                sug_text.append("    – ", style="dim")
                sug_text.append(suggestion, style="rgb(160,160,160)")
                parts.append(sug_text)

    parts.append(Text(""))

    # Bullet list sections
    def _section(label: str, items: tuple, style: str = "rgb(160,160,160)") -> list:
        section_parts: list = []
        section_parts.append(Text(label, style="bold rgb(140,140,160)"))
        if not items:
            section_parts.append(Text("  (none)", style="dim"))
        else:
            for item in items:
                row = Text()
                row.append("  – ", style="dim")
                row.append(item, style=style)
                section_parts.append(row)
        return section_parts

    parts.extend(_section("Goals", analysis.goals))
    parts.append(Text(""))
    parts.extend(_section("End Users", analysis.end_users))
    parts.append(Text(""))
    parts.extend(_section("Tech Stack", analysis.tech_stack))
    parts.append(Text(""))
    parts.extend(_section("Integrations", analysis.integrations))
    parts.append(Text(""))
    parts.extend(_section("Constraints", analysis.constraints))
    parts.append(Text(""))
    parts.extend(_section("Risks", analysis.risks))
    parts.append(Text(""))
    parts.extend(_section("Out of Scope", analysis.out_of_scope))

    # Capacity analysis — shows velocity breakdown and per-sprint bank holiday impact.
    # Rendered between Out of Scope and Assumptions so the user sees planning capacity
    # before reviewing the assumptions that feed into it.
    if net_velocity is not None and velocity_per_sprint is not None:
        parts.append(Text(""))
        ts = team_size or 1
        source_label = {"jira": "from Jira", "estimated": "estimated", "manual": "manual"}.get(
            velocity_source or "", ""
        )
        parts.append(Text("Capacity", style="bold rgb(140,140,160)"))
        team_line = Text()
        team_line.append("  Team: ", style="dim")
        team_line.append(f"{ts} engineer(s)", style="white")
        if team_override_from is not None and team_override_from != ts:
            team_line.append(f" (expanded from {team_override_from} to fit scope)", style="dim yellow")
        team_line.append(" · Gross velocity: ", style="dim")
        team_line.append(f"{velocity_per_sprint} pts/sprint", style="white")
        if source_label:
            team_line.append(f" ({source_label})", style="dim")
        parts.append(team_line)

        has_per_sprint = sprint_capacities and any(
            sc.get("bank_holiday_days", 0) > 0 or sc.get("pto_days", 0) > 0 for sc in sprint_capacities
        )
        if has_per_sprint:
            parts.append(Text("  Per-sprint breakdown:", style="dim"))
            total_pts = 0
            for sc in sprint_capacities:
                idx = sc["sprint_index"] + 1
                nv = sc["net_velocity"]
                total_pts += nv
                row = Text()
                has_impact = sc.get("bank_holiday_names") or sc.get("pto_days", 0) > 0
                if has_impact:
                    row.append(f"    Sprint {idx}: ", style="dim")
                    row.append(f"{nv} pts", style="rgb(200,180,60)")
                    annotations = []
                    if sc["bank_holiday_names"]:
                        names = ", ".join(sc["bank_holiday_names"])
                        annotations.append(f"−{sc['bank_holiday_days']}d: {names}")
                    if sc.get("pto_days", 0) > 0:
                        pto_names = ", ".join(f"{e['person']} {e['days']}d" for e in sc.get("pto_entries", []))
                        annotations.append(f"PTO: {pto_names}")
                    row.append(f" ({'; '.join(annotations)})", style="dim yellow")
                else:
                    row.append(f"    Sprint {idx}: ", style="dim")
                    row.append(f"{nv} pts", style="rgb(80,200,80)")
                parts.append(row)
            total_line = Text()
            total_line.append("  Total: ", style="dim")
            total_line.append(f"{total_pts} pts", style="white")
            total_line.append(f" across {len(sprint_capacities)} sprints", style="dim")
            parts.append(total_line)

        net_line = Text()
        net_line.append("  Net velocity: ", style="dim")
        net_line.append(f"{net_velocity} pts/sprint", style="bold cyan")
        parts.append(net_line)

    # Assumptions — highlighted in yellow since they represent defaults/skipped answers
    if analysis.assumptions:
        parts.append(Text(""))
        assumptions_parts = _section("Assumptions", analysis.assumptions, style="yellow")
        # Wrap in a panel to visually separate from the rest
        parts.append(
            Panel(
                Group(*assumptions_parts),
                box=rich.box.ROUNDED,
                border_style="rgb(80,80,40)",
                padding=(1, 2),
            )
        )

    # Context sources — show which external data sources were used, skipped, or
    # failed during analysis so the user gets transparency about what fed the LLM.
    # Same info the REPL shows via _render_context_source_panels.
    if context_sources:
        parts.append(Text(""))
        parts.append(Text("Context Sources", style="bold rgb(140,140,160)"))
        for src in context_sources:
            src_line = Text("  ")
            if src["status"] == "success":
                src_line.append("✓ ", style="green")
                src_line.append(f"{src['name']}: ", style="dim")
                src_line.append(src["detail"], style="dim green")
            elif src["status"] == "error":
                src_line.append("✗ ", style="red")
                src_line.append(f"{src['name']}: ", style="dim")
                src_line.append(src["detail"], style="dim red")
            else:
                src_line.append("— ", style="dim")
                src_line.append(f"{src['name']}: {src['detail']}", style="dim")
            parts.append(src_line)

    # SCRUM.md contributions
    if analysis.scrum_md_contributions:
        parts.append(Text(""))
        contrib = Text()
        contrib.append("SCRUM.md enriched: ", style="dim cyan")
        contrib.append(" · ".join(analysis.scrum_md_contributions), style="cyan")
        parts.append(contrib)

    return Group(*parts)


# ---------------------------------------------------------------------------
# User stories renderer
# ---------------------------------------------------------------------------


def _render_tui_stories(
    stories, features, *, selected_index: int | None = None, graph_state: dict | None = None
) -> Group:
    """Render user stories as text blocks grouped by feature — TUI-specific.

    Each story is a compact block: metadata header line, story text,
    acceptance criteria, and DoD — no tables or borders.

    selected_index: when not None, the story at this global index gets a white
    border to indicate it is currently selected; all other stories keep the
    default grey border.  The index is global across all feature groups (i.e.
    the first story in the second feature continues counting from the last story
    in the first feature).
    """

    from yeaboi.agent.state import DOD_ITEMS, resolve_dod_items, shorten_dod_items

    _graph_state = graph_state

    feature_titles = {e.id: e.title for e in features}

    # Group stories by feature_id
    grouped: dict[str, list] = {}
    for story in stories:
        grouped.setdefault(story.feature_id, []).append(story)

    parts: list = []
    global_idx = 0  # tracks position across all feature groups for selected_index
    for feature_id, feature_stories in grouped.items():
        feature_label = feature_titles.get(feature_id, feature_id)
        parts.append(Text(feature_label, style="bold rgb(70,100,180)"))

        for story in feature_stories:
            # Build story content inside a rounded box
            card_parts: list = []

            # Metadata header: ID · Pts · Priority · Discipline — in a rounded box
            header = Text()
            header.append(story.id, style="cyan")
            header.append("  ·  ", style="dim")
            header.append(f"{story.story_points} pts", style="dim")
            header.append("  ·  ", style="dim")
            header.append(str(story.priority.value), style=_priority_color(story.priority))
            header.append("  ·  ", style="dim")
            header.append(str(story.discipline.value), style="dim")
            header_box = Panel(
                header,
                box=rich.box.ROUNDED,
                border_style="white",
                padding=(0, 2),
                expand=False,
            )
            card_parts.append(header_box)
            card_parts.append(Text(""))

            # Story body — indented by 1 space via Padding so wrapped text aligns
            body_parts: list = []

            # Story text
            body_parts.append(Text(story.text))

            # Blank line before acceptance criteria
            body_parts.append(Text(""))

            # Acceptance criteria — values aligned to longest key ("Given" = 6 chars)
            _ac_w = 6  # len("Given ")
            _ac_count = len(story.acceptance_criteria)
            if _ac_count > 0:
                _ac_label = Text()
                _ac_label.append(f"Acceptance Criteria ({_ac_count})", style="bold rgb(140,140,160)")
                body_parts.append(_ac_label)
            for _ac_idx, ac in enumerate(story.acceptance_criteria, 1):
                if _ac_count > 1:
                    _ac_num = Text()
                    _ac_num.append(f"  AC {_ac_idx}", style="bold rgb(100,130,100)")
                    body_parts.append(_ac_num)
                ac_text = Text()
                ac_text.append(f"  {'Given':<{_ac_w}}", style="bold rgb(100,130,100)")
                ac_text.append(ac.given, style="rgb(140,140,140)")
                body_parts.append(ac_text)
                when_text = Text()
                when_text.append(f"  {'When':<{_ac_w}}", style="bold rgb(100,130,100)")
                when_text.append(ac.when, style="rgb(140,140,140)")
                body_parts.append(when_text)
                then_text = Text()
                then_text.append(f"  {'Then':<{_ac_w}}", style="bold rgb(100,130,100)")
                then_text.append(ac.then, style="rgb(140,140,140)")
                body_parts.append(then_text)
                if _ac_idx < _ac_count:
                    body_parts.append(Text(""))

            # DoD line — blank line above
            dod_flags = story.dod_applicable
            _dod_items = resolve_dod_items(_graph_state) if _graph_state else DOD_ITEMS
            _dod_short = shorten_dod_items(_dod_items)
            if len(dod_flags) >= len(_dod_items):
                body_parts.append(Text(""))
                dod = Text()
                dod.append("DoD: ", style="bold")
                for j, (short, applicable) in enumerate(zip(_dod_short, dod_flags)):
                    sep = "" if j == 0 else "  "
                    if applicable:
                        dod.append(f"{sep}✓ {short}", style="green")
                    else:
                        dod.append(f"{sep}✗ {short}", style="dim strike")
                body_parts.append(dod)

            # Points rationale + confidence — shows LLM's reasoning for the estimate
            if story.points_rationale or getattr(story, "points_confidence", ""):
                body_parts.append(Text(""))
                rationale = Text()
                conf = getattr(story, "points_confidence", "")
                if conf:
                    _conf_colors = {"high": "green", "medium": "yellow", "low": "red"}
                    rationale.append(f"[{conf}] ", style=f"bold {_conf_colors.get(conf, 'dim')}")
                if story.points_rationale:
                    rationale.append(story.points_rationale, style="dim italic")
                body_parts.append(rationale)

            card_parts.append(Padding(Group(*body_parts), (0, 0, 0, 1)))

            parts.append(Group(*card_parts, Text("")))
            # Narrow centered separator between stories (not after the last)
            if global_idx < len(stories) - 1:
                sep = Text("\u2500" * 36, style="rgb(40,40,50)", justify="center")
                parts.append(sep)
                parts.append(Text(""))
            global_idx += 1

    return Group(*parts)


# ---------------------------------------------------------------------------
# Epic renderer (project-level epic from ProjectAnalysis)
# ---------------------------------------------------------------------------


def _render_tui_epic(analysis, *, render_w: int = 80, examples: dict | None = None) -> Group:
    """Render the project-level epic for review.

    When examples dict is provided (from analysis profile), renders the epic
    using the team's naming convention and template sections. Otherwise
    falls back to a basic display of project name + description.
    """
    parts: list = []
    _ex = examples or {}
    wrap_w = max(40, render_w - 4)
    c_section = "bold rgb(220,180,60)"
    c_desc = "rgb(180,180,200)"
    c_muted = "rgb(140,140,160)"

    def _wrap(text: str, style: str, indent: str = "  ") -> None:
        words = text.split()
        buf = ""
        for word in words:
            if buf and len(buf) + len(word) + 1 > wrap_w:
                parts.append(Text(f"{indent}{buf}", style=style))
                buf = word
            else:
                buf = (buf + " " + word).strip()
        if buf:
            parts.append(Text(f"{indent}{buf}", style=style))

    # Check for team naming convention
    naming = _ex.get("naming_conventions", {})
    epic_style = naming.get("epic_naming_style", "") if isinstance(naming, dict) else ""
    template_sections = naming.get("template_sections", []) if isinstance(naming, dict) else []

    project_name = getattr(analysis, "project_name", "Untitled")
    desc = getattr(analysis, "project_description", "")

    # Header — use team naming convention if available
    hdr = Text()
    hdr.append("[E1]  ", style="bold cyan")
    if epic_style:
        hdr.append(f"({epic_style} naming)", style="dim")
        hdr.append("  ", style="dim")
    hdr.append(project_name, style="bold white")
    hdr.append("  \u00b7  ", style="dim")
    hdr.append("high", style="yellow")
    parts.append(hdr)
    parts.append(Text(""))

    # If team has template sections, render description using them
    if template_sections and desc:
        import re as _re

        # Try to parse section markers from description (LLM may use **Bold** or ## Heading)
        # First try **Section** markers
        section_re = _re.compile(r"\*\*([^*]+)\*\*\s*")
        section_parts = section_re.split(desc)

        # If no **bold** markers found, try ## heading markers
        if len(section_parts) <= 2:
            heading_re = _re.compile(r"#{1,3}\s+([^\n?]+\??)\s*")
            section_parts = heading_re.split(desc)

        if len(section_parts) > 2:
            # Description has sections — render them
            if section_parts[0].strip():
                _wrap(section_parts[0].strip(), c_desc)
                parts.append(Text(""))
            i = 1
            while i < len(section_parts) - 1:
                section_title = section_parts[i].strip().rstrip("?")
                section_body = section_parts[i + 1].strip() if i + 1 < len(section_parts) else ""
                parts.append(Text(f"  {section_title}", style=c_section))
                if section_body:
                    _wrap(section_body, c_desc)
                parts.append(Text(""))
                i += 2
        else:
            # No section markers — show template sections as guidance + raw description
            parts.append(Text("  Description", style=f"bold {c_muted}"))
            _wrap(desc, c_desc)
            parts.append(Text(""))
            if template_sections:
                parts.append(Text("  Team's template sections:", style="dim"))
                for sec_name, _ in template_sections[:5]:
                    parts.append(Text(f"    \u2022 {sec_name}", style="dim"))
                parts.append(Text(""))
    elif desc:
        # No template sections — basic description
        parts.append(Text("  Description", style=f"bold {c_muted}"))
        _wrap(desc, c_desc)
        parts.append(Text(""))

    # Target state
    target = getattr(analysis, "target_state", "")
    if target:
        row = Text()
        row.append("  Target State: ", style=f"bold {c_muted}")
        row.append(target, style=c_desc)
        parts.append(row)
        parts.append(Text(""))

    # Sprint planning summary
    sprint_weeks = getattr(analysis, "sprint_length_weeks", 0)
    target_sprints = getattr(analysis, "target_sprints", 0)
    if sprint_weeks or target_sprints:
        row = Text()
        row.append("Sprint Planning: ", style="bold rgb(140,140,160)")
        row.append(f"{sprint_weeks}-week sprints \u00d7 {target_sprints} sprints", style="rgb(180,180,200)")
        parts.append(row)
        parts.append(Text(""))

    # Team epic examples (if available from analysis)
    epic_examples = naming.get("epic_examples", []) if isinstance(naming, dict) else []
    if epic_examples:
        parts.append(Text("  Team's Epic Examples", style=f"bold {c_muted}"))
        for ex in epic_examples[:3]:
            parts.append(Text(f"    \u2022 {ex}", style="dim"))
        parts.append(Text(""))

    # Info note
    parts.append(Text(""))
    parts.append(
        Text(
            "This epic will be created in Jira/Azure DevOps during sync. All stories will be linked to it.",
            style="dim",
        )
    )

    return Group(*parts)


# ---------------------------------------------------------------------------
# Features renderer
# ---------------------------------------------------------------------------


def _render_tui_features(features, *, render_w: int = 80) -> Group:
    """Render features as styled text blocks — TUI-specific.

    Each feature has a header line (E1 · Title · priority) and description.
    All headers are padded to 75% of the render width so they line up.
    Separators between features.

    render_w: the available content width (passed from _render_pipeline_artifacts).
    """
    box_w = max(30, int(render_w * 0.75))

    parts: list = []
    for idx, feat in enumerate(features):
        card_parts: list = []

        # Header line: F1  ·  Title  ·  priority — fixed width
        header = Text(justify="left")
        header.append(feat.id, style="cyan")
        header.append("  ·  ", style="dim")
        header.append(feat.title, style="bold white")
        header.append("  ·  ", style="dim")
        header.append(str(feat.priority.value), style=_priority_color(feat.priority))
        # Pad to fixed width so all headers have consistent alignment
        plain_len = len(header.plain)
        if plain_len < box_w:
            header.append(" " * (box_w - plain_len))
        card_parts.append(header)
        card_parts.append(Text(""))

        # Description — flush with the header line above (no extra indent)
        card_parts.append(Text(feat.description, style="rgb(160,160,160)"))

        parts.append(Group(*card_parts, Text("")))

        # Separator between features (not after the last)
        if idx < len(features) - 1:
            sep = Text("\u2500" * 36, style="rgb(40,40,50)", justify="center")
            parts.append(sep)
            parts.append(Text(""))

    return Group(*parts)


# ---------------------------------------------------------------------------
# Tasks renderer
# ---------------------------------------------------------------------------


def _render_tui_tasks(tasks, stories, features) -> Group:
    """Render tasks grouped by feature/story — TUI-specific.

    Each story group has a white-bordered header box (matching the user story
    card style: ID · pts · priority · discipline), with all tasks for that
    story listed underneath with indented descriptions.
    Separators appear between story groups.
    Feature titles are rendered as group headers (used for sticky pinning).
    """

    feature_titles = {e.id: e.title for e in features}
    story_map = {s.id: s for s in stories}

    # Group tasks by story_id
    tasks_by_story: dict[str, list] = {}
    for task in tasks:
        tasks_by_story.setdefault(task.story_id, []).append(task)

    # Group stories by feature_id
    stories_by_feature: dict[str, list[str]] = {}
    for story in stories:
        if story.id in tasks_by_story:
            stories_by_feature.setdefault(story.feature_id, []).append(story.id)

    parts: list = []
    # Count total story groups for separator logic
    total_groups = sum(len(sids) for sids in stories_by_feature.values())
    group_idx = 0

    for feature_id, story_ids in stories_by_feature.items():
        feature_label = feature_titles.get(feature_id, feature_id)
        parts.append(Text(feature_label, style="bold rgb(70,100,180)"))

        for story_id in story_ids:
            story = story_map.get(story_id)
            card_parts: list = []

            # Story header in white-bordered box (same as user story card)
            header = Text()
            header.append(story_id, style="cyan")
            if story:
                header.append("  ·  ", style="dim")
                header.append(f"{story.story_points} pts", style="dim")
                header.append("  ·  ", style="dim")
                header.append(str(story.priority.value), style=_priority_color(story.priority))
                header.append("  ·  ", style="dim")
                header.append(str(story.discipline.value), style="dim")
            header_box = Panel(
                header,
                box=rich.box.ROUNDED,
                border_style="white",
                padding=(0, 2),
                expand=False,
            )
            card_parts.append(header_box)
            card_parts.append(Text(""))

            # Tasks listed underneath with indentation
            body_parts: list = []
            for task in tasks_by_story.get(story_id, []):
                task_line = Text()
                task_line.append(task.id, style="cyan")
                # Show label badge in colour to visually distinguish task types.
                label_val = task.label.value if hasattr(task.label, "value") else str(task.label)
                label_style = _task_label_color(label_val)
                task_line.append(f"  [{label_val}]", style=label_style)
                task_line.append(f"  {task.title}", style="white")
                body_parts.append(task_line)

                if task.description:
                    body_parts.append(Text(task.description, style="rgb(140,140,140)"))

                if task.test_plan:
                    test_line = Text()
                    test_line.append("Test plan: ", style="bold rgb(100,200,100)")
                    test_line.append(task.test_plan, style="rgb(140,140,140)")
                    body_parts.append(test_line)

                if task.ai_prompt:
                    prompt_line = Text()
                    prompt_line.append("AI prompt: ", style="bold rgb(180,140,60)")
                    prompt_line.append(task.ai_prompt, style="rgb(120,120,120)")
                    body_parts.append(prompt_line)

                body_parts.append(Text(""))  # spacing between tasks

            # Remove trailing blank
            if body_parts and isinstance(body_parts[-1], Text) and not body_parts[-1].plain.strip():
                body_parts.pop()

            card_parts.append(Padding(Group(*body_parts), (0, 0, 0, 1)))

            parts.append(Group(*card_parts, Text("")))

            # Separator between story groups (not after the last)
            group_idx += 1
            if group_idx < total_groups:
                sep = Text("\u2500" * 36, style="rgb(40,40,50)", justify="center")
                parts.append(sep)
                parts.append(Text(""))

    return Group(*parts)


# ---------------------------------------------------------------------------
# Sprint plan renderer
# ---------------------------------------------------------------------------


def _render_tui_sprint_plan(
    sprints,
    stories,
    features,
    velocity,
    *,
    sprint_capacities=None,
    team_override_from: int | None = None,
    team_size: int | None = None,
) -> Group:
    """Render sprint plan as text blocks — TUI-specific.

    Each sprint gets a white-bordered header box (name + capacity bar + points),
    the goal presented below with indentation, and stories listed underneath.
    Sprints impacted by bank holidays show a warning annotation with holiday names.
    Separators between sprints.
    """

    story_map = {s.id: s for s in stories}

    # Build per-sprint capacity lookup (0-based index → capacity dict).
    # Each dict has: sprint_index, bank_holiday_days, bank_holiday_names, net_velocity.
    # See docs: "Scrum Standards" — capacity planning
    cap_by_idx: dict[int, dict] = {}
    if sprint_capacities:
        for sc in sprint_capacities:
            cap_by_idx[sc["sprint_index"]] = sc

    total_points = sum(
        story_map[sid].story_points for sprint in sprints for sid in sprint.story_ids if sid in story_map
    )

    parts: list = []
    header = Text(f"{len(sprints)} sprint(s)  ·  Velocity: {velocity} pts  ·  Total: {total_points} pts")
    parts.append(header)
    if team_override_from is not None and team_size is not None and team_override_from != team_size:
        team_note = Text()
        team_note.append("  Team expanded from ", style="dim")
        team_note.append(f"{team_override_from}", style="white")
        team_note.append(" to ", style="dim")
        team_note.append(f"{team_size} engineer(s)", style="bold cyan")
        team_note.append(" to fit scope in target sprints", style="dim")
        parts.append(team_note)

    for idx, sprint in enumerate(sprints):
        sprint_points = sum(story_map[sid].story_points for sid in sprint.story_ids if sid in story_map)

        # Use per-sprint net velocity when available (bank holidays reduce capacity),
        # otherwise fall back to the flat velocity for all sprints.
        cap = cap_by_idx.get(idx)
        sprint_velocity = cap["net_velocity"] if cap else velocity
        has_holidays = bool(cap and (cap.get("bank_holiday_days", 0) > 0 or cap.get("pto_days", 0) > 0))

        ratio = sprint_points / sprint_velocity if sprint_velocity > 0 else 0
        card_parts: list = []

        # Sprint header in white-bordered box: name + capacity bar + points
        filled = min(int(ratio * 15), 15)
        empty = 15 - filled
        bar_style = "red" if ratio > 1.0 else ("yellow" if ratio > 0.8 else "green")
        header = Text()
        header.append(sprint.name, style="bold rgb(70,100,180)")
        header.append("  ")
        header.append("━" * filled, style=bar_style)
        header.append("─" * empty, style="dim")
        header.append(f"  {sprint_points}/{sprint_velocity} pts", style=bar_style)
        # Border style: amber for bank-holiday-impacted sprints
        border_style = "rgb(200,180,60)" if has_holidays else "white"
        header_box = Panel(
            header,
            box=rich.box.ROUNDED,
            border_style=border_style,
            padding=(0, 2),
            expand=False,
        )
        card_parts.append(header_box)

        # Bank holiday annotation — shown below the header for impacted sprints
        if has_holidays:
            names = ", ".join(cap.get("bank_holiday_names", []))
            holiday_text = Text()
            holiday_text.append("  ⚠ ", style="rgb(200,180,60)")
            holiday_text.append(
                f"−{cap['bank_holiday_days']}d capacity: {names}",
                style="dim rgb(200,180,60)",
            )
            card_parts.append(holiday_text)

        # PTO annotation — shown below bank holidays for sprints with planned leave
        pto_days = cap.get("pto_days", 0) if cap else 0
        if pto_days > 0:
            pto_entries = cap.get("pto_entries", [])
            pto_names = ", ".join(f"{e['person']} {e['days']}d" for e in pto_entries)
            pto_text = Text()
            pto_text.append("  📋 ", style="rgb(100,180,220)")
            pto_text.append(f"PTO: {pto_names}", style="dim rgb(100,180,220)")
            card_parts.append(pto_text)

        card_parts.append(Text(""))

        # Goal — indented, with label styled distinctly
        body_parts: list = []
        goal_text = Text()
        goal_text.append("Goal: ", style="bold white")
        goal_text.append(sprint.goal, style="rgb(140,140,140)")
        body_parts.append(goal_text)

        # Stories in this sprint
        body_parts.append(Text(""))
        for sid in sprint.story_ids:
            story = story_map.get(sid)
            if story:
                row = Text()
                row.append(sid, style="cyan")
                row.append(f"  {story.story_points} pts", style="dim")
                row.append("  ", style="dim")
                row.append(str(story.priority.value), style=_priority_color(story.priority))
                row.append(f"  {story.title or story.goal}", style="dim")
                body_parts.append(row)

        card_parts.append(Padding(Group(*body_parts), (0, 0, 0, 1)))

        parts.append(Group(*card_parts, Text("")))

        # Separator between sprints (not after the last)
        if idx < len(sprints) - 1:
            sep = Text("\u2500" * 36, style="rgb(40,40,50)", justify="center")
            parts.append(sep)
            parts.append(Text(""))

    return Group(*parts)


# ---------------------------------------------------------------------------
# Border stripping helper
# ---------------------------------------------------------------------------


def _strip_borders(renderable):
    """Strip borders from Rich renderables for cleaner TUI display.

    Panels are unwrapped to just their content. Tables get their edge
    and box removed. Groups are recursively processed.
    """
    if isinstance(renderable, Panel):
        # Show title as a blue heading, then the body without the box
        parts = []
        if renderable.title:
            # renderable.title is a Text object — strip markup and restyle
            title_plain = renderable.title.plain if isinstance(renderable.title, Text) else str(renderable.title)
            # Remove Rich markup tags like [bold], [/bold]
            title_clean = re.sub(r"\[/?[a-z_ ]+\]", "", title_plain)
            parts.append(Text(title_clean, style="rgb(70,100,180)", justify="left"))
            parts.append(Text(""))
        parts.append(_strip_borders(renderable.renderable))
        return Group(*parts)
    if isinstance(renderable, Table):
        renderable.show_edge = False
        renderable.show_header = False
        renderable.box = None
        renderable.title = None
        renderable.caption = None
        return renderable
    if isinstance(renderable, Group):
        renderable._renderables = [_strip_borders(r) for r in renderable._renderables]
        return renderable
    return renderable


# ---------------------------------------------------------------------------
# Calibration banner (shown when analysis profile is active)
# ---------------------------------------------------------------------------

_cached_profile = None
_cached_profile_id = ""
_cached_examples = None


def _render_calibration_banner(profile_id: str, width: int = 80, stage: str = "") -> Panel | None:
    """Render a stage-specific calibration banner from the selected analysis profile.

    Shows different data depending on the pipeline stage:
    - project_analyzer: velocity, completion rate, team size
    - feature_generator: epic sizing, naming convention
    - story_writer: point definitions, AC patterns, story shapes, DoD
    - task_decomposer: task patterns, common task types
    - sprint_planner: velocity, spillover, capacity, completion rate

    Caches the loaded profile to avoid repeated DB reads on every frame.
    """
    global _cached_profile, _cached_profile_id, _cached_examples  # noqa: PLW0603

    if profile_id != _cached_profile_id:
        try:
            from yeaboi.agent.nodes import _load_profile_by_id

            _cached_profile, _cached_examples = _load_profile_by_id(profile_id)
            _cached_profile_id = profile_id
        except Exception:
            return None

    p = _cached_profile
    if p is None:
        return None

    _ex = _cached_examples or {}
    c_label = "rgb(100,180,100)"
    c_value = "bold white"
    c_muted = "rgb(120,120,140)"

    display_name = profile_id.split("-", 1)[1] if "-" in profile_id else profile_id
    source = getattr(p, "source", "?")

    def _dot() -> tuple[str, str]:
        return "  \u00b7  ", "dim"

    lines: list[Text] = []

    if stage == "project_analyzer":
        # Analysis phase: velocity, completion, team, sprints
        row = Text("  ", justify="left")
        vel = getattr(p, "velocity_avg", 0.0)
        if vel > 0:
            row.append(f"Velocity: {vel:.0f} pts/sprint", style=c_value)
            row.append(*_dot())
        comp = getattr(p, "sprint_completion_rate", 0.0)
        if comp > 0:
            row.append(f"Completion: {comp:.0f}%", style=c_value)
            row.append(*_dot())
        contrib = _ex.get("contributor_stats", {})
        if isinstance(contrib, dict) and contrib:
            row.append(f"Team: {len(contrib)}", style=c_value)
            row.append(*_dot())
        row.append(f"{getattr(p, 'sample_sprints', 0)} sprints", style=c_muted)
        lines.append(row)

    elif stage == "feature_generator":
        # Epic phase: naming convention, epic sizing, template
        row = Text("  ", justify="left")
        naming = _ex.get("naming_conventions", {})
        if isinstance(naming, dict):
            ns = naming.get("epic_naming_style", "")
            if ns:
                row.append(f"Epic naming: {ns}", style=c_value)
                row.append(*_dot())
            examples = naming.get("epic_examples", [])
            if examples:
                row.append(f'e.g. "{examples[0][:40]}"', style=c_muted)
        lines.append(row)
        ep = getattr(p, "epic_pattern", None)
        if ep and getattr(ep, "sample_count", 0) > 0:
            row2 = Text("  ", justify="left")
            row2.append(f"Avg {ep.avg_stories_per_epic:.0f} stories/epic", style=c_value)
            row2.append(*_dot())
            row2.append(f"{ep.avg_points_per_epic:.0f} pts/epic", style=c_value)
            lines.append(row2)

    elif stage == "story_writer":
        # Story phase: point definitions, AC patterns, disciplines, DoD
        pt_descs = _ex.get("point_descriptions", {})
        cals = getattr(p, "point_calibrations", ())
        if pt_descs and isinstance(pt_descs, dict):
            for pts_key in sorted(pt_descs.keys(), key=lambda x: int(x) if x.isdigit() else 99)[:3]:
                row = Text("  ", justify="left")
                row.append(f"{pts_key}pt: ", style=c_value)
                row.append(pt_descs[pts_key][:60], style=c_muted)
                lines.append(row)
        elif cals:
            for c in cals[:3]:
                if c.sample_count > 0:
                    row = Text("  ", justify="left")
                    row.append(f"{c.point_value}pt: ", style=c_value)
                    row.append(f"{c.avg_cycle_time_days:.0f}d cycle, ~{c.typical_task_count:.0f} tasks", style=c_muted)
                    lines.append(row)
        # AC + DoD
        wp = getattr(p, "writing_patterns", None)
        dod = getattr(p, "dod_signal", None)
        row_extra = Text("  ", justify="left")
        if wp and getattr(wp, "median_ac_count", 0) > 0:
            row_extra.append(f"Avg {wp.median_ac_count:.0f} ACs/story", style=c_value)
            row_extra.append(*_dot())
        if wp and getattr(wp, "uses_given_when_then", False):
            row_extra.append("Given/When/Then", style=c_value)
            row_extra.append(*_dot())
        if dod:
            dod_items = []
            if getattr(dod, "stories_with_review_mention_pct", 0) > 30:
                dod_items.append("review")
            if getattr(dod, "stories_with_testing_mention_pct", 0) > 30:
                dod_items.append("testing")
            if getattr(dod, "stories_with_deploy_mention_pct", 0) > 30:
                dod_items.append("deploy")
            if dod_items:
                row_extra.append(f"DoD: {', '.join(dod_items)}", style=c_value)
        if row_extra.plain.strip():
            lines.append(row_extra)

    elif stage == "task_decomposer":
        # Task phase: task patterns, type distribution, avg tasks/story
        td = _ex.get("task_decomposition", {})
        if isinstance(td, dict):
            row = Text("  ", justify="left")
            avg = td.get("avg_tasks_per_story", 0)
            if avg:
                row.append(f"Avg {avg:.1f} tasks/story", style=c_value)
                row.append(*_dot())
            dist = td.get("type_distribution", {})
            if dist:
                top = sorted(dist.items(), key=lambda x: -x[1])[:3]
                row.append(", ".join(f"{t} {v}%" for t, v in top), style=c_muted)
            lines.append(row)
            common = td.get("common_tasks", [])
            if common:
                row2 = Text("  ", justify="left")
                row2.append("Common: ", style=c_value)
                names = [t[0] if isinstance(t, (list, tuple)) else str(t) for t in common[:3]]
                row2.append(", ".join(names), style=c_muted)
                lines.append(row2)

    elif stage == "sprint_planner":
        # Sprint phase: velocity, spillover, capacity
        row = Text("  ", justify="left")
        vel = getattr(p, "velocity_avg", 0.0)
        if vel > 0:
            row.append(f"Velocity: {vel:.0f} pts/sprint", style=c_value)
            row.append(*_dot())
        comp = getattr(p, "sprint_completion_rate", 0.0)
        if comp > 0:
            row.append(f"Completion: {comp:.0f}%", style=c_value)
            row.append(*_dot())
        spill = getattr(p, "spillover", None)
        if spill and getattr(spill, "carried_over_pct", 0) > 0:
            row.append(f"Spillover: {spill.carried_over_pct:.0f}%", style=c_value)
        lines.append(row)
        # Scope changes
        scope = _ex.get("scope_changes", {})
        if isinstance(scope, dict):
            totals = scope.get("totals", {})
            churn = totals.get("avg_scope_churn_pct", 0)
            if churn > 0:
                row2 = Text("  ", justify="left")
                row2.append(f"Scope churn: {churn:.0f}%", style=c_value)
                row2.append(*_dot())
                delivered = totals.get("avg_delivered_velocity", 0)
                committed = totals.get("avg_committed_velocity", 0)
                if delivered and committed:
                    row2.append(f"Committed {committed:.0f} → Delivered {delivered:.0f}", style=c_muted)
                lines.append(row2)

    else:
        # Fallback: generic overview
        row = Text("  ", justify="left")
        vel = getattr(p, "velocity_avg", 0.0)
        if vel > 0:
            row.append(f"Velocity: {vel:.0f} pts/sprint", style=c_value)
            row.append(*_dot())
        row.append(f"{getattr(p, 'sample_sprints', 0)} sprints analysed", style=c_muted)
        lines.append(row)

    if not lines:
        return None

    content = Group(*lines)
    banner_w = min(width - 4, 72)

    return Panel(
        content,
        title=f"Team Analysis: {display_name} ({source})",
        title_align="left",
        border_style=c_label,
        box=rich.box.ROUNDED,
        width=banner_w,
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Pipeline artifact rendering (main entry point)
# ---------------------------------------------------------------------------


def _render_pipeline_artifacts(
    console: Console, graph_state: dict, *, selected_story: int | None = None
) -> tuple[list[str], list[tuple[int, str]]]:
    """Render pipeline artifacts to plain text lines for the scrollable viewport.

    Returns (content_lines, sticky_headers) where sticky_headers is a list of
    (line_index, styled_ansi_text) pairs identifying group headers that should
    stay pinned at the top of the viewport when scrolled past.

    selected_story: when the pending_review stage is story_writer, this index
    highlights the selected story with a white border.
    """
    from langchain_core.messages import AIMessage

    pending = graph_state.get("pending_review")
    w, _ = console.size
    render_w = max(40, w - 20)
    sticky_headers: list[tuple[int, str]] = []

    try:
        if pending == "project_analyzer" and graph_state.get("project_analysis"):
            renderable = _render_tui_analysis(
                graph_state["project_analysis"],
                sprint_capacities=graph_state.get("sprint_capacities"),
                net_velocity=graph_state.get("net_velocity_per_sprint"),
                velocity_per_sprint=graph_state.get("velocity_per_sprint"),
                team_size=graph_state.get("team_size"),
                velocity_source=graph_state.get("velocity_source"),
                context_sources=graph_state.get("context_sources"),
            )
        elif pending == "feature_generator" and graph_state.get("features"):
            renderable = _render_tui_features(graph_state["features"], render_w=render_w)
        elif pending == "story_writer" and graph_state.get("stories"):
            renderable = _render_tui_stories(
                graph_state["stories"],
                graph_state.get("features", []),
                selected_index=selected_story,
                graph_state=graph_state,
            )
        elif pending == "task_decomposer" and graph_state.get("tasks"):
            renderable = _render_tui_tasks(
                graph_state["tasks"], graph_state.get("stories", []), graph_state.get("features", [])
            )
        elif pending == "sprint_planner" and graph_state.get("sprints"):
            velocity = graph_state.get("velocity_per_sprint", 10)
            _team_override = graph_state.get("_capacity_team_override", 0)
            _orig_team = graph_state.get("team_size", 1)
            renderable = _render_tui_sprint_plan(
                graph_state["sprints"],
                graph_state.get("stories", []),
                graph_state.get("features", []),
                velocity,
                sprint_capacities=graph_state.get("sprint_capacities"),
                team_override_from=_orig_team if _team_override > 0 else None,
                team_size=_team_override if _team_override > 0 else None,
            )
        else:
            # Fallback: show the last AI message
            msgs = graph_state.get("messages", [])
            if msgs and isinstance(msgs[-1], AIMessage):
                return msgs[-1].content.splitlines(), []
            return ["(no content)"], []
    except Exception:
        import logging as _log

        _log.getLogger(__name__).exception("Pipeline artifact rendering failed for stage=%s", pending)
        msgs = graph_state.get("messages", [])
        if msgs and isinstance(msgs[-1], AIMessage):
            return msgs[-1].content.splitlines(), []
        return ["(rendering error)"], []

    # Prepend calibration banner when an analysis profile is active
    # Skip for feature_generator — features are just labels, no calibration needed
    _profile_id = graph_state.get("analysis_profile_id", "")
    if _profile_id and pending != "feature_generator":
        banner = _render_calibration_banner(_profile_id, render_w, stage=pending or "")
        if banner:
            renderable = Group(banner, Text(""), renderable)

    lines = _render_to_lines(console, renderable, render_w)

    # Build sticky header index for sections with group headers.
    # Stories/tasks: feature title lines are pinned.
    # Sprint plan: the summary line ("N sprint(s) · Velocity: ...") is pinned.
    if pending in ("story_writer", "task_decomposer"):
        features = graph_state.get("features", [])
        feature_titles = {e.title for e in features}
        for i, line in enumerate(lines):
            plain = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if plain in feature_titles:
                sticky_headers.append((i, line))
    elif pending == "sprint_planner" and lines:
        # First non-empty line is the sprint summary — pin it
        for i, line in enumerate(lines):
            plain = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if plain:
                sticky_headers.append((i, line))
                break

    return lines, sticky_headers
