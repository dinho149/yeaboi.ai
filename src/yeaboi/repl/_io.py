"""Artifact rendering and file import/export helpers."""

import logging
from pathlib import Path

from langchain_core.messages import AIMessage
from rich.console import Console
from rich.panel import Panel

from yeaboi.agent.state import QuestionnaireState
from yeaboi.questionnaire_io import build_questionnaire_from_answers, parse_questionnaire_md
from yeaboi.repl._questionnaire import AI_LABEL
from yeaboi.repl._review import REVIEW_HINT
from yeaboi.repl._ui import _simulate_stream, stream_response

logger = logging.getLogger(__name__)


def _get_active_suggestion(graph_state: dict) -> str | None:
    """Return the suggested answer for the current question, if any.

    Only returns a suggestion when the questionnaire is active, not
    awaiting confirmation, and not in editing mode.

    Args:
        graph_state: The current graph state dict.

    Returns:
        The suggestion text, or None if no active suggestion.
    """
    qs = graph_state.get("questionnaire")
    if (
        isinstance(qs, QuestionnaireState)
        and not qs.completed
        and not qs.awaiting_confirmation
        and qs.editing_question is None
        and qs.current_question not in qs.probed_questions
    ):
        return qs.suggested_answers.get(qs.current_question)
    return None


def _render_context_source_panels(console: Console, result: dict) -> None:
    """Render dim panels showing the raw output from each successful context source.

    Shows the user exactly what data the repo scan, Confluence search, or SCRUM.md
    contributed to the analysis — so they can verify the LLM used real data, not
    hallucinated context.

    Each panel is rendered in dim style to visually separate tool output from the
    main analysis panel. Only sources with non-None data are shown.
    """
    source_panels = [
        ("repo_context", "Repository Scan"),
        ("confluence_context", "Confluence Docs"),
        ("user_context", "SCRUM.md"),
    ]
    for key, title in source_panels:
        content = result.get(key)
        if content:
            # Truncate very long output (e.g. large file trees) to keep the
            # terminal readable. Show first 40 lines with a note if truncated.
            lines = content.splitlines()
            if len(lines) > 40:
                truncated = "\n".join(lines[:40])
                truncated += f"\n\n[dim italic]... ({len(lines) - 40} more lines)[/dim italic]"
            else:
                truncated = content
            console.print()
            console.print(
                Panel(
                    f"[dim]{truncated}[/dim]",
                    title=f"[dim bold]{title}[/dim bold]",
                    border_style="dim",
                    padding=(0, 1),
                )
            )


def _render_resume_summary(console: Console, graph_state: dict) -> None:
    """Show a compact summary of existing artifacts when resuming between pipeline steps.

    Displays counts of epics, stories, tasks, and sprints so the user knows
    what has been generated so far before the next step runs.
    """
    parts: list[str] = []
    for key, label in (("features", "feature"), ("stories", "story"), ("tasks", "task"), ("sprints", "sprint")):
        items = graph_state.get(key, [])
        if items:
            n = len(items)
            # Pluralise: feature→features, story→stories, task→tasks, sprint→sprints
            plural = f"{label[:-1]}ies" if label.endswith("y") else f"{label}s"
            parts.append(f"{n} {label if n == 1 else plural}")
    if graph_state.get("project_analysis"):
        parts.insert(0, "analysis")
    if parts:
        console.print(f"[dim]Completed: {', '.join(parts)}[/dim]")


def _render_artifacts(console: Console, result: dict, *, compact: bool = False) -> None:
    """Render structured artifacts with Rich formatters instead of streaming markdown.

    # See docs: "Architecture" — REPL-side formatter layer
    #
    # When the graph produces structured artifacts (epics, stories, tasks, sprints,
    # project analysis, or the intake summary), this function renders them as Rich
    # Tables/Panels for a scannable, colour-coded display. The AI message content
    # (markdown) is still in the conversation history for the LLM — this just
    # changes what the user sees.

    Uses pending_review to pick the right formatter.
    Falls back to streaming if no formatter applies (should not happen when called correctly).

    Args:
        console: Rich Console for output.
        result: The graph result dict (post-invoke).
        compact: When True, pass compact=True to formatters to hide secondary columns.
    """
    # Lazy imports to avoid circular dependencies
    from yeaboi.formatters import (
        render_analysis_panel,
        render_features_table,
        render_sprint_plan,
        render_stories_table,
        render_tasks_table,
    )

    pending = result.get("pending_review")
    ai_msg = result["messages"][-1]
    logger.debug("_render_artifacts: pending=%s compact=%s", pending, compact)

    try:
        if pending == "project_intake" and result.get("questionnaire"):
            # Intake confirmation gate — render the summary as a Rich table
            # instead of streaming the raw markdown. The LLM message stays
            # in history; only the display is swapped out.
            from yeaboi.formatters import render_intake_summary

            qs_obj = result["questionnaire"]
            if isinstance(qs_obj, QuestionnaireState):
                console.print(render_intake_summary(qs_obj, compact=compact))
            else:
                stream_response(console, _simulate_stream(ai_msg.content))
        elif pending == "project_analyzer" and result.get("project_analysis"):
            console.print(render_analysis_panel(result["project_analysis"], compact=compact))
            # Show raw tool output for each successful context source so the
            # user can see exactly what data grounded the LLM's analysis.
            _render_context_source_panels(console, result)
            # Show context sources status — which external data sources were
            # used, skipped, or failed during analysis.
            sources = result.get("context_sources", [])
            if sources:
                console.print()
                for src in sources:
                    if src["status"] == "success":
                        console.print(f"  [green]✓[/green] [dim]{src['name']}: {src['detail']}[/dim]")
                    elif src["status"] == "error":
                        console.print(f"  [red]✗[/red] [dim]{src['name']}: {src['detail']}[/dim]")
                    else:
                        console.print(f"  [dim]— {src['name']}: {src['detail']}[/dim]")
        elif pending == "feature_generator" and result.get("features"):
            console.print(render_features_table(result["features"], compact=compact))
        elif pending == "story_writer" and result.get("stories"):
            console.print(render_stories_table(result["stories"], result.get("features", []), compact=compact))
        elif pending == "task_decomposer" and result.get("tasks"):
            console.print(
                render_tasks_table(
                    result["tasks"], result.get("stories", []), result.get("features", []), compact=compact
                )
            )
        elif pending == "sprint_planner" and result.get("sprints"):
            velocity = result.get("velocity_per_sprint", 10)
            console.print(
                render_sprint_plan(
                    result["sprints"], result.get("stories", []), result.get("features", []), velocity, compact=compact
                )
            )
        else:
            stream_response(console, _simulate_stream(ai_msg.content))
    except Exception:
        # Fallback — if Rich rendering fails (e.g. unexpected artifact shape),
        # stream the AI message as markdown so the user still sees output.
        logger.warning("Rich rendering failed for pending=%s, falling back to stream", pending)
        stream_response(console, _simulate_stream(ai_msg.content))


def _is_intake_phase(graph_state: dict) -> bool:
    """Check if the questionnaire is still in the intake phase (not yet completed)."""
    qs = graph_state.get("questionnaire")
    return isinstance(qs, QuestionnaireState) and not qs.completed


def _is_md_file_path(text: str) -> bool:
    """Check if input looks like a Markdown file path.

    Detects paths like `./file.md`, `/abs/path.md`, `~/file.md`, or bare
    `file.md`. Only returns True if the text ends with `.md` and has no
    spaces (to avoid false positives with normal conversation).
    """
    return text.endswith(".md") and " " not in text


def _import_questionnaire_file(console: Console, path: Path, graph_state: dict) -> dict:
    """Import a questionnaire file and update graph state with the summary.

    Parses the .md file, builds a QuestionnaireState, generates the intake
    summary, and injects it as an AIMessage into the conversation. Returns
    the updated graph_state.

    # See docs: "Project Intake Questionnaire" — offline workflow
    """
    from yeaboi.agent.nodes import _build_intake_summary

    parsed = parse_questionnaire_md(path)
    qs = build_questionnaire_from_answers(parsed)
    summary = _build_intake_summary(qs)
    ai_msg = AIMessage(content=summary)
    graph_state = {
        **graph_state,
        "questionnaire": qs,
        "messages": [*graph_state.get("messages", []), ai_msg],
        "pending_review": "project_intake",
    }
    console.print(f"[success]Loaded {len(parsed)} answers from {path}[/success]")
    # Render the Rich table summary instead of streaming raw markdown.
    # The markdown version stays in messages for the LLM.
    console.print(f"\n{AI_LABEL}")
    from yeaboi.formatters import render_intake_summary

    console.print(render_intake_summary(qs))
    console.print(f"\n{REVIEW_HINT}")
    return graph_state


def _append_capacity_section(lines: list[str], graph_state: dict) -> None:
    """Append a capacity/velocity breakdown section to the markdown export.

    Recomputes the breakdown from state fields (team_size, velocity_per_sprint,
    capacity deductions) so the exported plan shows the same math that drove
    sprint planning decisions.
    """
    import logging

    _log = logging.getLogger(__name__)
    team_size = graph_state.get("team_size", 0)
    velocity = graph_state.get("velocity_per_sprint", 0)
    net_velocity = graph_state.get("net_velocity_per_sprint", 0)

    # Fallback: if analyzer hasn't run yet (export at intake review stage),
    # compute capacity from the questionnaire directly — same as the intake
    # summary does. This ensures early exports include PTO/capacity data.
    if (not team_size or not velocity) and graph_state.get("questionnaire"):
        from yeaboi.agent.nodes import _extract_capacity_deductions, _extract_team_and_velocity

        qs = graph_state["questionnaire"]
        tv = _extract_team_and_velocity(qs)
        team_size = team_size or tv.get("team_size", 0)
        velocity = velocity or tv.get("velocity_per_sprint", 0)

    _log.debug(
        "CAPACITY_EXPORT: team_size=%s velocity=%s net_velocity=%s",
        team_size,
        velocity,
        net_velocity,
    )
    if not team_size or not velocity:
        _log.debug("CAPACITY_EXPORT: skipping — team_size or velocity is 0/missing")
        return

    sprint_weeks = graph_state.get("sprint_length_weeks", 0)
    analysis = graph_state.get("project_analysis")
    target_sprints = analysis.target_sprints if analysis else 0

    # Fallback: derive sprint_weeks and target_sprints from questionnaire
    if graph_state.get("questionnaire"):
        from yeaboi.agent.nodes import _parse_first_int

        qs = graph_state["questionnaire"]
        if not sprint_weeks:
            sprint_weeks = _parse_first_int(qs.answers.get(8, "2 weeks")) or 2
        if not target_sprints:
            import re

            q10 = qs.answers.get(10, "")
            q10_nums = re.findall(r"\d+", q10)
            target_sprints = int(q10_nums[-1]) if q10_nums else 0
    if not target_sprints:
        return

    bank_holidays = graph_state.get("capacity_bank_holiday_days", 0)
    planned_leave = graph_state.get("capacity_planned_leave_days", 0)
    unplanned_pct = graph_state.get("capacity_unplanned_leave_pct", 0)
    onboarding = graph_state.get("capacity_onboarding_engineer_sprints", 0)
    ktlo = graph_state.get("capacity_ktlo_engineers", 0)
    discovery_pct = graph_state.get("capacity_discovery_pct", 5)

    # Fallback: extract capacity deductions from questionnaire
    if not any([bank_holidays, planned_leave, unplanned_pct, onboarding]) and graph_state.get("questionnaire"):
        from yeaboi.agent.nodes import _extract_capacity_deductions

        qs = graph_state["questionnaire"]
        cap = _extract_capacity_deductions(qs)
        bank_holidays = bank_holidays or cap.get("capacity_bank_holiday_days", 0)
        planned_leave = planned_leave or cap.get("capacity_planned_leave_days", 0)
        unplanned_pct = unplanned_pct or cap.get("capacity_unplanned_leave_pct", 0)
        onboarding = onboarding or cap.get("capacity_onboarding_engineer_sprints", 0)

    lines.append("## Capacity")
    lines.append("")
    lines.append(f"- **Team size:** {team_size} engineer(s)")
    lines.append(f"- **Sprint length:** {sprint_weeks} weeks")
    lines.append(f"- **Target sprints:** {target_sprints}")
    lines.append(f"- **Gross velocity:** {velocity} pts/sprint")

    deductions: list[str] = []
    leave_entries = graph_state.get("planned_leave_entries", [])
    if not leave_entries and graph_state.get("questionnaire"):
        leave_entries = list(graph_state["questionnaire"]._planned_leave_entries)
    if planned_leave > 0:
        if leave_entries:
            leave_detail = ", ".join(f"{e['person']} {e['working_days']}d" for e in leave_entries)
            deductions.append(f"Planned leave: {planned_leave} day(s) ({leave_detail})")
        else:
            deductions.append(f"Planned leave: {planned_leave} day(s)")
    if unplanned_pct > 0:
        deductions.append(f"Unplanned absence: {unplanned_pct}%")
    if onboarding > 0:
        deductions.append(f"Onboarding: {onboarding} engineer-sprint(s)")
    if ktlo > 0:
        deductions.append(f"KTLO/BAU: {ktlo} dedicated engineer(s)")
    if discovery_pct > 0:
        deductions.append(f"Discovery/design: {discovery_pct}%")

    if deductions:
        lines.append(f"- **Deductions:** {', '.join(deductions)}")

    # Per-sprint velocity breakdown — shows bank holiday impact per sprint
    sprint_caps = graph_state.get("sprint_capacities", [])
    starting_sprint = graph_state.get("starting_sprint_number", 0)
    sprint_label_start = starting_sprint if starting_sprint > 0 else 1
    if sprint_caps and any(sc.get("bank_holiday_days", 0) > 0 or sc.get("pto_days", 0) > 0 for sc in sprint_caps):
        lines.append("")
        lines.append("**Per-sprint velocity:**")
        lines.append("")
        for sc in sprint_caps:
            label = f"Sprint {sprint_label_start + sc['sprint_index']}"
            annotations = []
            names = sc.get("bank_holiday_names", [])
            if names:
                annotations.append(", ".join(names))
            if sc.get("pto_days", 0) > 0:
                pto_names = ", ".join(f"{e['person']} {e['days']}d" for e in sc.get("pto_entries", []))
                annotations.append(f"PTO: {pto_names}")
            if annotations:
                lines.append(f"- {label}: **{sc['net_velocity']} pts** — {'; '.join(annotations)}")
            else:
                lines.append(f"- {label}: **{sc['net_velocity']} pts**")
        lines.append("")
    else:
        if bank_holidays > 0:
            deductions.insert(0, f"Bank holidays: {bank_holidays} day(s)")
        lines.append(f"- **Net velocity:** {net_velocity} pts/sprint")
        lines.append("")


def _export_plan_markdown(graph_state: dict, path: Path | None = None) -> Path:
    """Export structured plan artifacts to a Markdown file.

    Builds a markdown document from the dataclass artifacts (ProjectAnalysis,
    Epics, Stories, Tasks, Sprints) — not from AIMessage content which includes
    intake chatter. Falls back gracefully when artifacts are missing.

    Args:
        graph_state: The final graph state dict containing all artifacts.
        path: Optional output path. Defaults to scrum-plan.md in cwd.

    Returns:
        The path the file was written to.
    """
    output_path = path or Path("scrum-plan.md")
    logger.debug("_export_plan_markdown: path=%s", output_path)
    from yeaboi.export_targets import localize_images

    # Pasted screenshots are copied next to the .md so the folder is portable.
    output_path.write_text(localize_images(build_plan_markdown(graph_state), output_path.parent))
    section_counts = {
        "features": len(graph_state.get("features", [])),
        "stories": len(graph_state.get("stories", [])),
        "tasks": len(graph_state.get("tasks", [])),
        "sprints": len(graph_state.get("sprints", [])),
    }
    logger.info("Exported markdown: path=%s sections=%s", output_path, section_counts)
    return output_path


def build_plan_markdown(graph_state: dict) -> str:
    """Build the sprint-plan Markdown document as a string.

    Extracted from ``_export_plan_markdown`` so the same content can be
    published to Notion/Confluence (via export_targets) without touching disk.
    """
    lines: list[str] = []

    # Analysis profile provenance
    profile_id = graph_state.get("analysis_profile_id", "")
    if profile_id:
        display_name = profile_id.split("-", 1)[1] if "-" in profile_id else profile_id
        source = profile_id.split("-", 1)[0] if "-" in profile_id else ""
        lines.append(f"> Calibrated with team analysis: **{display_name}** ({source})")
        lines.append("")

    # Project Analysis
    analysis = graph_state.get("project_analysis")
    if analysis:
        lines.append(f"# {analysis.project_name}")
        lines.append("")
        lines.append(f"**Description:** {analysis.project_description}")
        lines.append(f"**Type:** {analysis.project_type}")
        lines.append(f"**Target State:** {analysis.target_state}")
        lines.append(
            f"**Sprint Planning:** {analysis.sprint_length_weeks}-week sprints × {analysis.target_sprints} sprints"
        )
        for field_name in ("goals", "end_users", "tech_stack", "constraints", "risks", "out_of_scope"):
            items = getattr(analysis, field_name, ())
            if items:
                label = field_name.replace("_", " ").title()
                lines.append("")
                lines.append(f"## {label}")
                for item in items:
                    lines.append(f"- {item}")
        if analysis.assumptions:
            lines.append("")
            lines.append("## Assumptions")
            for a in analysis.assumptions:
                lines.append(f"- ⚠️ {a}")
        lines.append("")

    # Capacity breakdown — recompute from state fields so the user sees the
    # same deduction math that drove sprint planning.
    _append_capacity_section(lines, graph_state)

    # Epic
    analysis = graph_state.get("project_analysis")
    if analysis:
        epic_key = graph_state.get("jira_epic_key", "") or graph_state.get("azdevops_epic_id", "")
        key_suffix = f" ({epic_key})" if epic_key else ""
        lines.append("# Epic")
        lines.append("")
        lines.append(f"## {analysis.project_name}{key_suffix}")
        lines.append(f"{analysis.project_description}")
        lines.append(f"\n**Target state:** {analysis.target_state}")
        lines.append("")

    # Features
    features = graph_state.get("features", [])
    if features:
        lines.append("# Features")
        lines.append("")
        for feature in features:
            lines.append(f"## {feature.id}: {feature.title}")
            lines.append(
                f"**Priority:** {feature.priority.value if hasattr(feature.priority, 'value') else feature.priority}"
            )
            lines.append(f"{feature.description}")
            lines.append("")

    # Stories
    stories = graph_state.get("stories", [])
    if stories:
        lines.append("# User Stories")
        lines.append("")
        from yeaboi.agent.state import resolve_dod_items

        dod_items = resolve_dod_items(graph_state)
        for story in stories:
            lines.append(f"## {story.id}: {story.title or story.text}")
            lines.append(f"\n*{story.text}*\n")
            lines.append(
                f"**Feature:** {story.feature_id} | **Points:** {story.story_points} | "
                f"**Priority:** {story.priority.value if hasattr(story.priority, 'value') else story.priority} | "
                f"**Discipline:** {story.discipline.value if hasattr(story.discipline, 'value') else story.discipline}"
            )
            if story.points_rationale:
                confidence = getattr(story, "points_confidence", "")
                conf_tag = f" [{confidence} confidence]" if confidence else ""
                lines.append(f"\n> **Points rationale:** {story.points_rationale}{conf_tag}")
            if story.acceptance_criteria:
                lines.append("")
                lines.append("**Acceptance Criteria:**")
                for i, ac in enumerate(story.acceptance_criteria):
                    lines.append(f"\n**AC {i + 1}:**")
                    lines.append(f"- **Given** {ac.given}")
                    lines.append(f"  **When** {ac.when}")
                    lines.append(f"  **Then** {ac.then}")

            dod_flags = story.dod_applicable
            if len(dod_flags) == len(dod_items):
                lines.append("")
                lines.append("**Definition of Done:**")
                for item, applicable in zip(dod_items, dod_flags):
                    mark = "x" if applicable else " "
                    lines.append(f"- [{mark}] {item}")
            lines.append("")

    # Tasks
    tasks = graph_state.get("tasks", [])
    if tasks:
        lines.append("# Tasks")
        lines.append("")
        for task in tasks:
            label_val = task.label.value if hasattr(task.label, "value") else str(task.label)
            lines.append(f"### {task.id} [{label_val}]: {task.title}")
            lines.append(f"{task.description}")
            if task.test_plan:
                lines.append(f"\n**Test plan:** {task.test_plan}")
            if task.ai_prompt:
                lines.append(f"\n**AI prompt:** {task.ai_prompt}")
            lines.append("")

    # Sprints
    sprints = graph_state.get("sprints", [])
    if sprints:
        lines.append("# Sprint Plan")
        lines.append("")
        velocity = graph_state.get("velocity_per_sprint", 10)

        # At-a-glance overview table before the per-sprint detail sections —
        # renders as a native table on Notion/Confluence.
        from yeaboi.markdown_convert import md_table_cell as _cell

        def _sprint_used(sprint) -> int:
            return sum(
                (s.story_points.value if hasattr(s.story_points, "value") else int(s.story_points))
                for s in graph_state.get("stories", [])
                if s.id in sprint.story_ids
            )

        lines.append("| Sprint | Goal | Capacity | Used |")
        lines.append("|--------|------|----------|------|")
        for sprint in sprints:
            cap = sprint.capacity_points
            used = _sprint_used(sprint)
            lines.append(f"| **{_cell(sprint.name)}** | {_cell(sprint.goal)} | {cap} pts | {used} pts |")
        lines.append("")

        for sprint in sprints:
            lines.append(f"## {sprint.name}")
            lines.append(f"**Goal:** {sprint.goal}")
            cap = sprint.capacity_points
            deduction = ""
            if cap < velocity:
                deduction = f" _(reduced from {velocity} — bank holidays/deductions)_"
            lines.append(f"**Capacity:** {cap} pts{deduction}")
            used = sum(
                (s.story_points.value if hasattr(s.story_points, "value") else int(s.story_points))
                for s in graph_state.get("stories", [])
                if s.id in sprint.story_ids
            )
            fill_pct = min(int(used / cap * 100), 100) if cap else 0
            lines.append(f"**Used:** {used} / {cap} pts ({fill_pct}%)")
            lines.append("")
            for sid in sprint.story_ids:
                lines.append(f"- {sid}")
            lines.append("")

    # Attachments — screenshots pasted into the session (intake, review
    # feedback, chat). Only paths still on disk; the export pipeline embeds
    # them (Notion/Confluence upload, HTML base64, file copy).
    from pathlib import Path as _Path

    attachments: list[str] = []
    for key in ("pasted_images", "review_feedback_images", "chat_images"):
        for p in graph_state.get(key) or []:
            if p not in attachments and _Path(p).is_file():
                attachments.append(p)
    if attachments:
        lines.append("# Attachments")
        lines.append("")
        for i, p in enumerate(attachments, start=1):
            lines.append(f"![Screenshot {i}]({p})")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("🤙 _Generated by [yeaboi.ai](https://yeaboi.ai)_")
    lines.append("")

    return "\n".join(lines)


def _export_checkpoint(console: Console, graph_state: dict, stage: str = "complete") -> None:
    """Export current artifacts to both an HTML report and a Markdown file.

    Called from review checkpoints ([4] Export) and the intake confirmation
    gate ([4] Export). Writes both formats and prints the file paths.

    Args:
        console: Rich Console for output messages.
        graph_state: The current graph state dict containing all artifacts.
        stage: Pipeline stage label for the HTML header (e.g. "project_analyzer").
    """
    logger.info("_export_checkpoint: stage=%s", stage)
    from yeaboi.html_exporter import export_plan_html

    # Show which sections are included so the user knows export is cumulative
    # (exporting at any step always includes everything generated so far).
    section_labels = [
        ("questionnaire", "Questionnaire"),
        ("project_analysis", "Analysis"),
        ("features", "Features"),
        ("stories", "Stories"),
        ("tasks", "Tasks"),
        ("sprints", "Sprint plan"),
    ]
    included = [label for key, label in section_labels if graph_state.get(key)]
    if included:
        console.print(f"[hint]Includes: {', '.join(included)}[/hint]")

    html_path = export_plan_html(graph_state, stage=stage)
    md_path = _export_plan_markdown(graph_state)
    console.print(f"[success]HTML report  → {html_path}[/success]")
    console.print(f"[success]Markdown     → {md_path}[/success]")

    # Send anonymous telemetry if opted in (never blocks or errors)
    from yeaboi.telemetry import send_telemetry

    send_telemetry(graph_state)
